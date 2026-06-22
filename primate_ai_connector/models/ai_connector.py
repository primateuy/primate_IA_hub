# -*- coding: utf-8 -*-
# Conector base con la API de Anthropic (Claude). REUTILIZABLE por todo el ecosistema de IA.
# Implementación de referencia: la llamada y el loop de tool-use ya están resueltos.
import json
import logging

import requests

from odoo import api, models, _
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-4-6"


class PrimateAIConnector(models.AbstractModel):
    _name = "primate.ai.connector"
    _description = "Conector base con la API de Claude (Anthropic)"

    # --- configuración ---
    @api.model
    def _get_config(self):
        # sudo SOLO para leer config interna, nunca para datos de negocio
        icp = self.env["ir.config_parameter"].sudo()
        key = icp.get_param("primate_ai.api_key")
        model = icp.get_param("primate_ai.model") or DEFAULT_MODEL
        if not key:
            raise UserError(_(
                "Falta configurar la API key de Claude. Ajustes → primate_ai.api_key."
            ))
        return key, model

    # --- config para la UI de Sagui (Ajustes embebido en el shell web) ---
    # Devuelve SOLO datos no secretos (la API key NUNCA se devuelve: sólo si está o no
    # configurada). Editar requiere permisos de administración (igual que Ajustes de Odoo).
    @api.model
    def get_sagui_settings(self):
        icp = self.env["ir.config_parameter"].sudo()
        return {
            "model": icp.get_param("primate_ai.model") or "",
            "model_fast": icp.get_param("primate_ai.model_fast") or "",
            "write_whitelist": icp.get_param("primate_sagui.write_whitelist") or "",
            "api_key_set": bool(icp.get_param("primate_ai.api_key")),
            "can_edit": self.env.user.has_group("base.group_system"),
        }

    @api.model
    def set_sagui_settings(self, vals):
        """Guarda la config desde el shell. Sólo administradores (como Ajustes de Odoo). La API
        key se actualiza SÓLO si viene un valor no vacío (campo write-only: no se expone el actual)."""
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Necesitás permisos de administración para cambiar la configuración."))
        icp = self.env["ir.config_parameter"].sudo()
        vals = vals or {}
        if "model" in vals:
            icp.set_param("primate_ai.model", (vals.get("model") or "").strip())
        if "model_fast" in vals:
            icp.set_param("primate_ai.model_fast", (vals.get("model_fast") or "").strip())
        if "write_whitelist" in vals:
            icp.set_param("primate_sagui.write_whitelist", (vals.get("write_whitelist") or "").strip())
        new_key = (vals.get("api_key") or "").strip()
        if new_key:  # write-only: sólo si el usuario tipeó una key nueva
            icp.set_param("primate_ai.api_key", new_key)
        return self.get_sagui_settings()

    # --- una sola request ---
    @api.model
    def call(self, messages, system=None, tools=None, max_tokens=2048, model=None, cache=True,
             timeout=60):
        """Hace una request a la Messages API y devuelve el dict de respuesta crudo.

        :param timeout: segundos de timeout HTTP. Subilo para generaciones largas (ej. una
                        landing HTML completa puede tardar > 60s).

        :param messages: lista de mensajes [{role, content}] (content puede ser str o
                          lista de bloques, según el formato de la API).
        :param system: prompt de sistema (str) opcional.
        :param tools: lista de tool specs (JSON-schema) opcional.
        :param cache: si True, aplica prompt caching marcando el system y la última tool
                      con cache_control ephemeral. Así el prefijo estático (system + tools)
                      se reusa barato (~10% del precio de input) en lugar de reenviarse a
                      precio lleno en cada vuelta del loop. Es GA (no requiere header beta);
                      si el prefijo es chico (<~1024 tokens) simplemente no cachea.
        """
        key, default_model = self._get_config()
        payload = {
            "model": model or default_model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = (
                [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
                if cache else system
            )
        if tools:
            if cache:
                # Copia + marca la última tool: cachea todo el bloque de tools (render
                # order: tools -> system -> messages).
                tools = [dict(t) for t in tools]
                tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
            payload["tools"] = tools

        try:
            resp = requests.post(
                API_URL,
                headers={
                    "content-type": "application/json",
                    "x-api-key": key,
                    "anthropic-version": ANTHROPIC_VERSION,
                },
                data=json.dumps(payload),
                timeout=timeout,
            )
        except requests.RequestException as e:
            raise UserError(_("Error de red llamando a Claude: %s") % e)

        try:
            data = resp.json()
        except ValueError:
            raise UserError(_("Respuesta no válida de la API de Claude."))

        if resp.status_code != 200 or data.get("type") == "error":
            msg = (data.get("error") or {}).get("message") or resp.text[:300]
            raise UserError(_("Error de la API de Claude: %s") % msg)

        self._log_usage(data.get("usage") or {}, payload["model"])
        return data

    # --- caching del historial del loop ---
    @api.model
    def _apply_history_cache(self, msgs):
        """Marca SOLO el último bloque del historial con cache_control (limpiando los
        viejos). Así el historial acumulado del loop de tool-use se cachea y no se
        reenvía a precio lleno en cada iteración (que es el mayor costo oculto)."""
        cleaned = []
        for m in msgs:
            c = m.get("content")
            if isinstance(c, list):
                c = [{k: v for k, v in b.items() if k != "cache_control"} for b in c]
            cleaned.append({**m, "content": c})
        if cleaned:
            last = dict(cleaned[-1])
            c = last.get("content")
            if isinstance(c, str):
                c = [{"type": "text", "text": c}]
            if isinstance(c, list) and c:
                c = [dict(b) for b in c]
                c[-1] = {**c[-1], "cache_control": {"type": "ephemeral"}}
                last["content"] = c
                cleaned[-1] = last
        return cleaned

    # --- loop de tool-use multi-paso ---
    @api.model
    def run_conversation(self, messages, system, tool_specs, tool_runner,
                         max_iterations=6, model=None, cache=True):
        """Ejecuta la conversación resolviendo tool_use hasta la respuesta final.

        :param tool_runner: callable(nombre_tool: str, args: dict) -> str (resultado).
                            DEBE ejecutar las operaciones con los permisos del usuario
                            que pregunta (ver primate_sagui: self.env(user=user.id)).
        :param cache: aplica prompt caching del prefijo estático y del historial del loop.
        :return: (texto_final: str, messages: list)  -- messages incluye toda la traza.
        """
        msgs = list(messages)
        for _i in range(max_iterations):
            call_msgs = self._apply_history_cache(msgs) if cache else msgs
            data = self.call(call_msgs, system=system, tools=tool_specs,
                             model=model, cache=cache)
            content = data.get("content", [])
            msgs.append({"role": "assistant", "content": content})

            tool_uses = [b for b in content if b.get("type") == "tool_use"]
            if not tool_uses:
                text = "".join(
                    b.get("text", "") for b in content if b.get("type") == "text"
                )
                return text, msgs

            results = []
            for tu in tool_uses:
                try:
                    # Savepoint por tool: si una query falla (Claude pudo mandar un campo
                    # o dominio inválido), Postgres aborta la transacción. El savepoint
                    # la revierte SOLO a este punto, así el resto del loop, el log de uso
                    # y el commit final siguen sanos (si no: InFailedSqlTransaction).
                    with self.env.cr.savepoint():
                        out = tool_runner(tu.get("name"), tu.get("input") or {})
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": out if isinstance(out, str) else json.dumps(out, default=str),
                    })
                except Exception as e:  # noqa: BLE001 - se reporta al modelo, no rompe
                    _logger.warning("Tool %s falló: %s", tu.get("name"), e)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "is_error": True,
                        "content": _("La herramienta falló: %s") % e,
                    })
            msgs.append({"role": "user", "content": results})

        return _("Se alcanzó el límite de pasos sin una respuesta final."), msgs

    # --- loop de tool-use en streaming (SSE) ---
    @api.model
    def stream_conversation(self, messages, system, tool_specs, tool_runner,
                            model=None, cache=True, max_iterations=6, max_tokens=2048):
        """Generador que resuelve el loop de tool-use con stream=True y produce eventos:

          {"type": "text",  "text": "<delta>"}                    # texto parcial
          {"type": "tool",  "name": "...", "phase": "start|done"} # estado de una tool
          {"type": "error", "message": "..."}
          {"type": "done",  "usage": {...}}

        El controller los serializa a SSE. tool_runner DEBE ejecutar las operaciones con
        los permisos del usuario (ver primate_sagui: self.env(user=user.id)). Reusa el
        prompt caching del prefijo estático y del historial (`_apply_history_cache`).

        IMPORTANTE: es un generador perezoso. El que lo consume (el controller) debe
        hacerlo dentro de un cursor/env vivo, porque las tools tocan el ORM.
        """
        key, default_model = self._get_config()
        used_model = model or default_model
        msgs = list(messages)

        for _i in range(max_iterations):
            call_msgs = self._apply_history_cache(msgs) if cache else msgs
            payload = {
                "model": used_model,
                "max_tokens": max_tokens,
                "messages": call_msgs,
                "stream": True,
            }
            if system:
                payload["system"] = (
                    [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
                    if cache else system
                )
            if tool_specs:
                tools = [dict(t) for t in tool_specs]
                if cache:
                    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
                payload["tools"] = tools

            assistant_blocks = []   # se va reconstruyendo el turno del assistant
            cur = None              # bloque en construcción
            stop_reason = None
            # Usage acumulado del turno. En streaming el input y los tokens de caché llegan
            # en 'message_start'; el output (acumulado) en 'message_delta'. Hay que juntar
            # ambos para que el costo y el log de caché sean exactos (el scaffold solo
            # miraba el delta y perdía input/caché).
            usage = {}

            try:
                resp = requests.post(
                    API_URL,
                    headers={
                        "content-type": "application/json",
                        "x-api-key": key,
                        "anthropic-version": ANTHROPIC_VERSION,
                    },
                    data=json.dumps(payload),
                    stream=True,
                    timeout=120,
                )
            except requests.RequestException as e:
                yield {"type": "error", "message": _("Error de red llamando a Claude: %s") % e}
                return

            if resp.status_code != 200:
                # En error la API no streamea: el cuerpo trae el detalle.
                detail = resp.text[:300]
                try:
                    detail = (resp.json().get("error") or {}).get("message") or detail
                except ValueError:
                    pass
                yield {"type": "error", "message": _("Error de la API de Claude: %s") % detail}
                return

            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                etype = ev.get("type")

                if etype == "message_start":
                    # input_tokens + cache_creation/read_input_tokens (output todavía ~1).
                    usage.update((ev.get("message") or {}).get("usage") or {})

                elif etype == "content_block_start":
                    cur = dict(ev.get("content_block") or {})
                    if cur.get("type") == "tool_use":
                        cur["_input_json"] = ""
                        yield {"type": "tool", "name": cur.get("name"), "phase": "start"}

                elif etype == "content_block_delta":
                    delta = ev.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if cur is not None:
                            cur["text"] = (cur.get("text") or "") + text
                        yield {"type": "text", "text": text}
                    elif delta.get("type") == "input_json_delta":
                        cur["_input_json"] = cur.get("_input_json", "") + delta.get("partial_json", "")

                elif etype == "content_block_stop":
                    if cur is not None:
                        if cur.get("type") == "tool_use":
                            raw = cur.pop("_input_json", "") or "{}"
                            try:
                                cur["input"] = json.loads(raw)
                            except ValueError:
                                cur["input"] = {}
                        else:
                            cur.pop("_input_json", None)
                        assistant_blocks.append(cur)
                        cur = None

                elif etype == "message_delta":
                    stop_reason = (ev.get("delta") or {}).get("stop_reason") or stop_reason
                    # El output_tokens final llega acá: merge sobre el input/caché del start.
                    usage.update(ev.get("usage") or {})

                elif etype == "message_stop":
                    break

            # Registrar el uso del turno (input + caché + output).
            self._log_usage(usage, used_model)
            msgs.append({"role": "assistant", "content": assistant_blocks})

            tool_uses = [b for b in assistant_blocks if b.get("type") == "tool_use"]
            if stop_reason != "tool_use" or not tool_uses:
                yield {"type": "done", "usage": usage}
                return

            results = []
            for tu in tool_uses:
                yield {"type": "tool", "name": tu.get("name"), "phase": "done"}
                try:
                    # Savepoint por tool: si una query falla (Claude pudo mandar un campo
                    # o dominio inválido), Postgres aborta la transacción. El savepoint
                    # la revierte SOLO a este punto, así el resto del loop, el log de uso
                    # y el commit final siguen sanos (si no: InFailedSqlTransaction).
                    with self.env.cr.savepoint():
                        out = tool_runner(tu.get("name"), tu.get("input") or {})
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "content": out if isinstance(out, str) else json.dumps(out, default=str),
                    })
                except Exception as e:  # noqa: BLE001 - se reporta al modelo, no rompe
                    _logger.warning("Tool %s falló (stream): %s", tu.get("name"), e)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": tu["id"],
                        "is_error": True,
                        "content": _("La herramienta falló: %s") % e,
                    })
            msgs.append({"role": "user", "content": results})

        yield {"type": "done", "usage": {}}

    # --- log de uso (sudo permitido: es infraestructura, no datos de negocio) ---
    @api.model
    def _log_usage(self, usage, model):
        try:
            # Savepoint defensivo: si el create fallara, no debe envenenar la transacción
            # del request (que después commitea el stream).
            with self.env.cr.savepoint():
                vals = {
                    "model_name": model,
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                    "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
                    "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                    "user_id": self.env.uid,
                }
                # Etiquetar con la ACCIÓN de negocio si el caller la abrió (contexto).
                action_id = self.env.context.get("ai_action_id")
                if action_id:
                    vals["action_id"] = action_id
                self.env["primate.ai.usage.log"].sudo().create(vals)
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo registrar el uso de IA", exc_info=True)
