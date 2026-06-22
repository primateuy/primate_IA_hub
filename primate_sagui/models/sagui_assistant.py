# -*- coding: utf-8 -*-
# Sagui: tools de lectura + orquestación del mensaje.
# Verificado contra Discuss v19 (addons/mail, addons/mail_bot): el armado del
# historial usa message_ids del canal y el render postea como el partner del bot.
import base64
import json
import logging
import re
import secrets

from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

# Prompt de sistema: instrucción interna para Claude, no es texto de interfaz, así que
# NO va con _() (además, llamar _() a nivel de módulo dispara un warning porque todavía
# no hay idioma cargado). El stack de PrimateUY es en español.
SYSTEM_PROMPT = (
    "Sos Sagui, el asistente interno de Odoo de PrimateUY. Respondés en español, claro y "
    "conciso. Podés redactar documentación, analizar casos y revisar datos del sistema. "
    "Cuando necesites datos, USÁ las herramientas (buscar/agrupar/describir) antes de afirmar "
    "algo: nunca inventes valores. Si no tenés acceso a un dato (por permisos), decilo. "
    "Trabajás SOLO con los datos que el usuario actual puede ver."
)

MAX_LIMIT = 100
# Cantidad de mensajes recientes del canal que se mandan como contexto (acota tokens).
HISTORY_LIMIT = 20
# Fase 3 (escritura): tope de registros por modificación y vida de una propuesta.
MAX_WRITE_IDS = 50
PENDING_TTL_MIN = 60
# Comando determinístico de confirmación/cancelación (lo parsea NUESTRO código, no la IA).
CONFIRM_RE = re.compile(r"^\s*(confirmar|cancelar)(?:\s+([0-9a-f]{6}))?\s*$", re.IGNORECASE)

# Fase 4 (entrada multimodal): adjuntos de imagen/PDF que mandamos a la API de Claude.
# Formatos verificados contra la doc de Anthropic (base64, sin beta header).
SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
SUPPORTED_DOC_TYPES = {"application/pdf"}
MAX_MEDIA_BYTES = 10 * 1024 * 1024  # tope por archivo (la API admite hasta 32MB de request)
MAX_MEDIA_BLOCKS = 5                 # tope de adjuntos enviados al modelo por turno

# Planillas para importación (no se mandan como binario al modelo: se le pasa el
# attachment_id por texto para que use las tools de importación). Ver sagui_import.py.
SPREADSHEET_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",                                           # .xls
    "application/vnd.oasis.opendocument.spreadsheet",                     # .ods
    "text/csv",                                                           # .csv
}

# Tiering de modelo: pistas de que la consulta es "pesada" (análisis/documentación) ->
# usar el modelo de análisis (Sonnet) en vez del rápido (Haiku).
HEAVY_HINTS = ("analiz", "document", "compar", "explic", "redact", "informe",
               "resum", "por que", "porque", "razon", "evalua", "factura")


class SaguiAssistant(models.AbstractModel):
    _name = "primate.sagui.assistant"
    _description = "Lógica del asistente Sagui"

    # ---------- whitelist de escritura + prompt dinámico (Fase 3) ----------
    @api.model
    def _write_whitelist(self):
        """Conjunto de modelos donde Sagui tiene permitido PROPONER escrituras.

        Configurable en Ajustes (ir.config_parameter primate_sagui.write_whitelist).
        sudo solo para leer config interna; vacío = escritura deshabilitada.
        """
        raw = self.env["ir.config_parameter"].sudo().get_param(
            "primate_sagui.write_whitelist"
        ) or ""
        return {m.strip() for m in re.split(r"[,;\s]+", raw) if m.strip()}

    @api.model
    def _system_prompt(self):
        prompt = SYSTEM_PROMPT
        whitelist = self._write_whitelist()
        if whitelist:
            prompt += (
                "\n\nESCRITURA DE DATOS (con confirmación OBLIGATORIA del usuario): además de "
                "leer, podés PROPONER crear o modificar registros con crear_registro y "
                "modificar_registros, SOLO en estos modelos: %s. Estas herramientas NO aplican "
                "el cambio: registran una propuesta con un token. Cuando las uses, explicá en "
                "una frase qué se va a crear/cambiar y pedile al usuario que responda "
                "exactamente 'confirmar <token>' para aplicar o 'cancelar <token>' para "
                "descartar. NUNCA afirmes que un cambio ya se aplicó: se aplica recién cuando "
                "el usuario confirma." % ", ".join(sorted(whitelist))
            )
        if "account.move" in whitelist:
            prompt += (
                "\n\nFACTURAS DESDE DOCUMENTO: si el usuario adjunta una imagen o PDF de una "
                "factura, leela y extraé los datos clave (proveedor/cliente, fecha, y cada línea "
                "con producto, cantidad, precio unitario e impuestos). VERIFICÁ cada dato contra "
                "Odoo con buscar_registros ANTES de usarlo: buscá el partner por nombre/RUT/CUIT y "
                "cada producto por nombre o código (default_code). Si algo no existe (un producto, "
                "el cliente), NO lo inventes: preguntale al usuario a qué registro existente "
                "mapearlo, o si quiere que lo cree (en ese caso proponé su creación con "
                "crear_registro, que también se confirma). Determiná el tipo: 'in_invoice' "
                "(factura de proveedor que recibimos) u 'out_invoice' (factura a un cliente); si no "
                "es claro por el documento, preguntá. Cuando tengas TODOS los ids resueltos, "
                "proponé la factura con crear_registro sobre account.move incluyendo move_type, "
                "partner_id, invoice_date y invoice_line_ids como comandos, por ejemplo "
                "[[0,0,{\"product_id\": ID, \"quantity\": N, \"price_unit\": P}]]. Dejá la factura "
                "en BORRADOR: no la confirmes ni la valides; el usuario la revisa y la valida desde "
                "Odoo."
            )
        return prompt

    # ---------- tiering de modelo (Sonnet análisis / Haiku rápido) ----------
    @api.model
    def _pick_model(self, text, has_media=False):
        """Devuelve el string de modelo a usar, o None para el default (Sonnet) del config.

        Heurística simple + override explícito (/rapido, /analiza). Con imágenes/PDF usa
        siempre el modelo de análisis (mejor para visión/extracción de facturas). Para algo
        más fino se podría pre-clasificar con una llamada barata a Haiku.
        """
        icp = self.env["ir.config_parameter"].sudo()
        fast = icp.get_param("primate_ai.model_fast")
        t = (text or "").strip().lower()
        if t.startswith("/rapido") or t.startswith("/r "):
            return fast or None
        if t.startswith("/analiza") or t.startswith("/sonnet"):
            return None  # default (Sonnet)
        if has_media:
            return None  # visión/factura -> modelo de análisis
        # Consulta corta y sin pistas de análisis -> modelo rápido.
        if fast and len(t) < 80 and not any(w in t for w in HEAVY_HINTS):
            return fast
        return None

    # ---------- tool specs (JSON-schema para la Messages API) ----------
    @api.model
    def _tool_specs(self):
        specs = [
            {
                "name": "buscar_registros",
                "description": "Busca registros en un modelo de Odoo y devuelve los campos "
                               "pedidos. Respeta los permisos del usuario.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string", "description": "Modelo técnico, ej. 'res.partner'"},
                        "domain": {"type": "array", "description": "Dominio Odoo, ej. [['active','=',true]]"},
                        "fields": {"type": "array", "items": {"type": "string"}},
                        "limit": {"type": "integer"},
                        "order": {"type": "string"},
                    },
                    "required": ["model"],
                },
            },
            {
                "name": "agrupar_registros",
                "description": "Agrupa/agrega registros para KPIs y análisis (equivalente a "
                               "read_group). Devuelve el conteo de cada grupo y las agregaciones "
                               "pedidas.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string"},
                        "domain": {"type": "array"},
                        "fields": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Medidas a agregar. Podés pasar solo el nombre del "
                                           "campo numérico (se suma por defecto, ej. 'amount_total') "
                                           "o especificar la función con 'campo:func' "
                                           "(ej. 'amount_total:avg', 'id:count'). El conteo por "
                                           "grupo se incluye siempre.",
                        },
                        "groupby": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Campos por los que agrupar. Para fechas se admite "
                                           "granularidad, ej. 'date:month'.",
                        },
                    },
                    "required": ["model", "fields", "groupby"],
                },
            },
            {
                "name": "describir_modelo",
                "description": "Devuelve los campos de un modelo (nombre, tipo, etiqueta, "
                               "relación) para saber qué consultar.",
                "input_schema": {
                    "type": "object",
                    "properties": {"model": {"type": "string"}},
                    "required": ["model"],
                },
            },
        ]
        # Tools de escritura SOLO si hay whitelist configurada. Aún así no aplican nada:
        # registran una propuesta que el usuario debe confirmar en el chat.
        if self._write_whitelist():
            specs += [
                {
                    "name": "crear_registro",
                    "description": "PROPONE crear un registro nuevo en un modelo permitido. NO "
                                   "lo crea: registra una propuesta que el usuario debe "
                                   "confirmar en el chat.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string", "description": "Modelo técnico"},
                            "values": {
                                "type": "object",
                                "description": "Mapa campo->valor. Para relacionales (many2one) "
                                               "usá el id; fechas en texto ISO.",
                            },
                        },
                        "required": ["model", "values"],
                    },
                },
                {
                    "name": "modificar_registros",
                    "description": "PROPONE modificar registros existentes (por sus ids) en un "
                                   "modelo permitido. NO los modifica: registra una propuesta a "
                                   "confirmar por el usuario. Identificá los ids con "
                                   "buscar_registros antes de usar esta herramienta.",
                    "input_schema": {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string"},
                            "ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "IDs exactos a modificar (máx %s)." % MAX_WRITE_IDS,
                            },
                            "values": {"type": "object", "description": "Mapa campo->valor."},
                        },
                        "required": ["model", "ids", "values"],
                    },
                },
            ]
        return specs

    # ---------- ejecución de tools CON LOS PERMISOS DEL USUARIO ----------
    @api.model
    def _run_tool(self, name, args, user, channel=None, proposals=None):
        # CRÍTICO: env del usuario que pregunta -> ACLs y record rules aplican solos.
        # Nunca sudo() para datos de negocio.
        env = self.env(user=user.id)

        # Tools de escritura: NO ejecutan, solo PROPONEN (confirmación posterior).
        if name in ("crear_registro", "modificar_registros"):
            return self._propose_write(name, args, user, channel, proposals)

        # Guard común: si la tool opera sobre un modelo, que EXISTA (módulo instalado). Sin esto,
        # env[model] tira KeyError crudo y el LLM lo confunde con "no tenés permisos".
        if name in ("buscar_registros", "agrupar_registros", "describir_modelo"):
            model_arg = args.get("model")
            if model_arg not in self.env:
                return _(
                    "El modelo '%(m)s' no existe o su módulo no está instalado en este Odoo (no es "
                    "un problema de permisos). Para datos de productos/inventario hace falta instalar "
                    "Inventario o Ventas; para facturas, Contabilidad. Decíselo al usuario; no afirmes "
                    "que es por permisos."
                ) % {"m": model_arg}

        if name == "buscar_registros":
            model = args["model"]
            domain = args.get("domain") or []
            limit = min(int(args.get("limit") or 20), MAX_LIMIT)
            recs = env[model].search(domain, limit=limit, order=args.get("order"))
            fields_ = args.get("fields") or []
            return json.dumps(recs.read(fields_), default=str, ensure_ascii=False)

        if name == "agrupar_registros":
            model = args["model"]
            Model = env[model]
            domain = args.get("domain") or []
            fields_ = args["fields"]
            groupby = args["groupby"]
            # v19: read_group está deprecado a favor de formatted_read_group (definido en
            # addons/web). El nuevo método exige las agregaciones como 'campo:func', a
            # diferencia del viejo que sumaba por defecto. Normalizamos: un campo suelto
            # se suma, y siempre pedimos '__count' para el tamaño de cada grupo.
            if hasattr(Model, "formatted_read_group"):
                aggregates = [f if ":" in f else "%s:sum" % f for f in fields_]
                if "__count" not in aggregates:
                    aggregates.append("__count")
                res = Model.formatted_read_group(domain, groupby, aggregates)
            else:
                res = Model.read_group(domain, fields_, groupby, lazy=False)
            return json.dumps(res, default=str, ensure_ascii=False)

        if name == "describir_modelo":
            model = args["model"]
            fg = env[model].fields_get(
                attributes=["string", "type", "relation", "help"]
            )
            return json.dumps(fg, default=str, ensure_ascii=False)

        return _("Herramienta desconocida: %s") % name

    # ---------- orquestación de un mensaje del usuario ----------
    @api.model
    def process_user_message(self, channel, author, message):
        """Procesa un mensaje del usuario y postea la respuesta de Sagui en el canal.

        :param channel: discuss.channel
        :param author: res.users que escribió (NO el bot)
        :param message: mail.message recién posteado por el usuario
        """
        # 1) ¿Es un comando determinístico de confirmación/cancelación de una escritura?
        #    Lo maneja NUESTRO código (no la IA): así Claude no puede auto-confirmar.
        body_text = html2plaintext(message.body or "").strip() if message else ""
        if self._handle_confirmation(channel, author, body_text):
            return

        connector = self.env["primate.ai.connector"]
        messages = self._build_history(channel)
        if not messages:
            return
        # Tiering: media SOLO del mensaje actual (tiering predecible por mensaje). La
        # imagen igual viaja en el historial para contexto; y "factura" está en las pistas
        # pesadas, así que los follow-ups de factura por texto siguen yendo a Sonnet.
        current_media = bool(message) and any(
            (att.mimetype or "").split(";")[0].strip().lower()
            in (SUPPORTED_IMAGE_TYPES | SUPPORTED_DOC_TYPES)
            for att in message.attachment_ids
        )
        model = self._pick_model(body_text, has_media=current_media)
        proposals = []  # ids de pending.write creados en este turno
        # Acción de negocio para el costeo/drill-down: este turno = una conversación.
        first_line = (body_text or "").strip().splitlines()
        action = self.env["primate.ai.action"]._open(
            "conversacion",
            (first_line[0][:60] if first_line else (channel.name or "Conversación")),
            ref="discuss.channel,%s" % channel.id,
        )
        connector = connector.with_context(ai_action_id=action.id)
        try:
            text, _trace = connector.run_conversation(
                messages,
                system=self._system_prompt(),
                tool_specs=self._tool_specs(),
                tool_runner=lambda n, a: self._run_tool(n, a, author, channel, proposals),
                model=model,
                cache=True,
            )
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui falló procesando el mensaje")
            text = _("Tuve un problema procesando eso: %s") % e

        self._post_bot_reply(channel, text)

        # Preview determinístico de cada propuesta de escritura (fuente de verdad: lo
        # generamos desde los datos guardados, no de la paráfrasis de la IA).
        if proposals:
            pendings = self.env["primate.sagui.pending.write"].sudo().browse(proposals)
            for pending in pendings:
                if pending.exists() and pending.state == "pending":
                    self._post_write_preview(channel, pending)

    # ---------- Fase 3: escritura con confirmación en el chat ----------
    @api.model
    def _propose_write(self, name, args, user, channel, proposals):
        """Registra una operación de escritura PENDIENTE (no la ejecuta).

        Valida whitelist + permisos del usuario y guarda un primate.sagui.pending.write.
        Devuelve a la IA un texto indicando que falta la confirmación del usuario.
        """
        if not channel:
            return _("No puedo proponer escrituras fuera de un canal de chat.")
        whitelist = self._write_whitelist()
        model = args.get("model")
        if not model or model not in whitelist:
            return _(
                "No tengo permitido escribir en el modelo '%(model)s'. Habilitados: %(wl)s."
            ) % {"model": model or "?", "wl": ", ".join(sorted(whitelist)) or _("(ninguno)")}

        values = args.get("values") or {}
        if not isinstance(values, dict) or not values:
            return _("Faltan los valores a escribir.")

        env_user = self.env(user=user.id)  # permisos del usuario que pregunta
        if name == "crear_registro":
            try:
                env_user[model].check_access("create")
            except Exception as e:  # noqa: BLE001
                return _("No tenés permiso para crear en %(model)s: %(err)s") % {"model": model, "err": e}
            operation, res_ids = "create", None
            summary = self._summary_create(env_user, model, values)
        else:  # modificar_registros
            ids = [int(i) for i in (args.get("ids") or [])][:MAX_WRITE_IDS]
            if not ids:
                return _("Indicá los ids de los registros a modificar (buscalos primero).")
            recs = env_user[model].browse(ids).exists()
            if not recs:
                return _("No encontré registros con esos ids (o no podés verlos).")
            try:
                recs.check_access("write")
            except Exception as e:  # noqa: BLE001
                return _("No tenés permiso para modificar esos %(model)s: %(err)s") % {"model": model, "err": e}
            operation, res_ids = "write", recs.ids
            summary = self._summary_write(recs, values)

        token = secrets.token_hex(3)
        pending = self.env["primate.sagui.pending.write"].sudo().create({
            "token": token,
            "channel_id": channel.id,
            "user_id": user.id,
            "operation": operation,
            "model_name": model,
            "res_ids": json.dumps(res_ids) if res_ids is not None else False,
            "values_json": json.dumps(values, default=str, ensure_ascii=False),
            "summary": summary,
            "state": "pending",
        })
        if proposals is not None:
            proposals.append(pending.id)
        return _(
            "PROPUESTA REGISTRADA (token %(token)s). NO está aplicada. Pedile al usuario que "
            "responda 'confirmar %(token)s' para aplicar o 'cancelar %(token)s' para descartar. "
            "Resumen: %(summary)s"
        ) % {"token": token, "summary": summary}

    @api.model
    def _summary_create(self, env_user, model, values):
        # Resumen amigable para facturas (el genérico volcaría comandos anidados ilegibles).
        if model == "account.move":
            return self._summary_invoice(env_user, values)
        label = env_user[model]._description or model
        parts = ", ".join("%s = %s" % (k, v) for k, v in values.items())
        return _("Crear un %(label)s (%(model)s) con: %(vals)s") % {
            "label": label, "model": model, "vals": parts}

    @api.model
    def _summary_invoice(self, env_user, values):
        """Preview legible de una factura propuesta (account.move)."""
        type_label = {
            "in_invoice": "Factura de proveedor",
            "out_invoice": "Factura de cliente",
            "in_refund": "Nota de crédito (proveedor)",
            "out_refund": "Nota de crédito (cliente)",
        }.get(values.get("move_type"), values.get("move_type") or "Movimiento")

        partner = "(sin partner)"
        if values.get("partner_id"):
            try:
                partner = env_user["res.partner"].browse(int(values["partner_id"])).display_name
            except Exception:  # noqa: BLE001
                partner = "id %s" % values["partner_id"]

        line_strs, subtotal = [], 0.0
        for cmd in values.get("invoice_line_ids") or []:
            # Comando esperado: [0, 0, {vals}] (Claude lo manda como listas en JSON).
            if not (isinstance(cmd, (list, tuple)) and len(cmd) == 3 and isinstance(cmd[2], dict)):
                continue
            lv = cmd[2]
            prod = lv.get("name") or "(línea)"
            if lv.get("product_id"):
                try:
                    prod = env_user["product.product"].browse(int(lv["product_id"])).display_name
                except Exception:  # noqa: BLE001
                    prod = "producto id %s" % lv["product_id"]
            qty = lv.get("quantity", 1)
            price = lv.get("price_unit", 0)
            try:
                subtotal += float(qty) * float(price)
            except Exception:  # noqa: BLE001
                pass
            line_strs.append("%s — %s x %s" % (prod, qty, price))

        return _(
            "%(type)s para %(partner)s (fecha %(date)s)\nLíneas:\n  - %(lines)s\n"
            "Subtotal sin impuestos: %(total)s — queda en BORRADOR para que la valides."
        ) % {
            "type": type_label,
            "partner": partner,
            "date": values.get("invoice_date") or "-",
            "lines": "\n  - ".join(line_strs) or "(sin líneas)",
            "total": round(subtotal, 2),
        }

    @api.model
    def _summary_write(self, recs, values):
        names = recs[:10].mapped("display_name")
        extra = "" if len(recs) <= 10 else _(" y %s más") % (len(recs) - 10)
        parts = ", ".join("%s = %s" % (k, v) for k, v in values.items())
        return _(
            "Modificar %(n)s registro(s) [%(names)s%(extra)s] de %(model)s con: %(vals)s"
        ) % {"n": len(recs), "names": ", ".join(names), "extra": extra,
             "model": recs._name, "vals": parts}

    @api.model
    def _handle_confirmation(self, channel, author, body_text):
        """Detecta 'confirmar <token>' / 'cancelar <token>' de forma determinística.

        Devuelve True si lo manejó (y entonces NO se llama a la IA). La ejecución real
        de la escritura sale SIEMPRE de acá, nunca de una decisión del modelo.
        """
        m = CONFIRM_RE.match(body_text or "")
        if not m:
            return False
        action = m.group(1).lower()
        token = (m.group(2) or "").lower()
        Pending = self.env["primate.sagui.pending.write"].sudo()
        base_dom = [
            ("channel_id", "=", channel.id),
            ("user_id", "=", author.id),
            ("state", "=", "pending"),
        ]
        if token:
            pending = Pending.search(base_dom + [("token", "=", token)], limit=1)
        else:
            candidates = Pending.search(base_dom, order="create_date desc")
            if len(candidates) == 1:
                pending = candidates
            elif not candidates:
                pending = Pending.browse()
            else:
                self._post_bot_reply(channel, _(
                    "Hay varias operaciones pendientes. Indicá el token: %s"
                ) % ", ".join(candidates.mapped("token")))
                return True

        if not pending:
            # ¿Ya se está construyendo (sitio diferido a cron)? Avisar en vez de confundir/re-disparar.
            proc_dom = [("channel_id", "=", channel.id), ("user_id", "=", author.id),
                        ("state", "=", "processing")]
            if token:
                proc_dom += [("token", "=", token)]
            proc = Pending.search(proc_dom, limit=1)
            if proc:
                self._post_bot_reply(channel, _(
                    "Ese sitio (%s) ya lo estoy construyendo — tarda 1-2 minutos, no hace falta "
                    "reintentar; te aviso acá cuando esté. ⏳"
                ) % proc.token)
                return True
            self._post_bot_reply(channel, _("No hay ninguna operación pendiente con ese token."))
            return True

        if action == "cancelar":
            pending.write({"state": "cancelled"})
            self._post_bot_reply(channel, _(
                "Operación %s cancelada. No se modificó nada."
            ) % pending.token)
            return True

        # Sitios web: la construcción llama al LLM (~1-2 min). NO se corre inline: bloquearía el
        # message_post → el front de Discuss corta con "Failed to post message" y, si el request se
        # aborta, su transacción hace ROLLBACK → al reintentar se RE-genera y se queman tokens otra
        # vez. La diferimos a un cron: corre en su propia transacción, commitea solo (idempotente) y
        # postea el resultado cuando termina. El resto de operaciones (create/write/import) son
        # rápidas y siguen inline.
        if pending.operation in ("website", "website_greenfield"):
            pending.write({"state": "processing"})
            self._post_bot_reply(channel, _(
                "¡Dale! Estoy construyendo el sitio (%s). Tarda un par de minutos (2-3) porque lo "
                "genero con todo el detalle premium — seguí usando el chat, te aviso acá apenas esté "
                "listo. No hace falta reintentar. ⏳"
            ) % pending.token)
            cron = self.env.ref("primate_sagui.cron_build_sites", raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()
            return True

        self._execute_pending(channel, author, pending)
        return True

    @api.model
    def _cron_process_pending_builds(self):
        """Construye los sitios confirmados FUERA del request del chat (lo dispara
        _handle_confirmation con cron._trigger()). Cada build en su propia transacción/commit: si
        uno falla no afecta a los demás, y los ya hechos (state!='processing') no se re-corren —
        así un reintento del usuario no vuelve a quemar tokens."""
        Pending = self.env["primate.sagui.pending.write"].sudo()
        pendings = Pending.search([
            ("operation", "in", ("website", "website_greenfield")),
            ("state", "=", "processing"),
        ], order="create_date", limit=5)
        MAX_ATTEMPTS = 2
        for pending in pendings:
            channel, author = pending.channel_id, pending.user_id
            if not channel or not author:
                pending.write({"state": "error", "result_info": "sin canal/usuario"})
                self.env.cr.commit()
                continue
            # Tope de reintentos: si un build se mató por timeout (rollback → sigue 'processing'),
            # NO re-generar infinitamente quemando tokens. Tras MAX_ATTEMPTS, error claro.
            if pending.build_attempts >= MAX_ATTEMPTS:
                pending.write({"state": "error",
                               "result_info": "excedió reintentos (probable timeout de build)"})
                self._post_bot_reply(channel, _(
                    "❌ El sitio (%s) tardó demasiado y lo corté para no seguir gastando tokens. "
                    "Reintentá con menos secciones o un brief más corto, o avisame.") % pending.token)
                self.env.cr.commit()
                continue
            # Incrementar intentos y COMMIT ANTES de construir: si un kill por timeout aborta el
            # build, el contador queda persistido (no se pierde en el rollback) y no se reintenta sin fin.
            pending.write({"build_attempts": pending.build_attempts + 1})
            self.env.cr.commit()
            try:
                self._execute_pending(channel, author, pending)  # setea done/error + postea
            except Exception as e:  # noqa: BLE001
                _logger.exception("Sagui: cron build falló (%s)", pending.token)
                pending.write({"state": "error", "result_info": str(e)[:200]})
                self._post_bot_reply(channel, _(
                    "❌ No pude construir el sitio (%(t)s): %(e)s") % {"t": pending.token, "e": e})
            # Persistir cada build aunque el siguiente falle (y para no re-correrlo nunca).
            self.env.cr.commit()

    @api.model
    def _execute_pending(self, channel, author, pending):
        """Ejecuta una escritura confirmada CON LOS PERMISOS DEL USUARIO (nunca sudo)."""
        # Expiración.
        if (fields.Datetime.now() - pending.create_date).total_seconds() > PENDING_TTL_MIN * 60:
            pending.write({"state": "expired"})
            self._post_bot_reply(channel, _("La operación %s expiró. Volvé a pedirla.") % pending.token)
            return

        # Acción EXTERNA (conector): se ejecuta llamando la tool del MCP server, no por el ORM.
        if pending.operation == "external":
            ok, info = self.env["sagui.connector"]._execute_external_pending(pending)
            pending.write({"state": "done" if ok else "error", "result_info": (info or "")[:200]})
            self._post_bot_reply(channel, ("✅ " if ok else "❌ ") + (info or ""))
            return

        model = pending.model_name
        # Re-validar whitelist (pudo cambiar la config entre propuesta y confirmación).
        if model not in self._write_whitelist():
            pending.write({"state": "error", "result_info": "modelo fuera de whitelist"})
            self._post_bot_reply(channel, _(
                "Ya no está habilitada la escritura en %s. No se aplicó nada."
            ) % model)
            return

        env_user = self.env(user=author.id)  # CRÍTICO: ejecutar como el usuario
        try:
            values = json.loads(pending.values_json or "{}")
            if pending.operation == "create":
                env_user[model].check_access("create")
                rec = env_user[model].create(values)
                pending.write({"state": "done", "result_info": "creado id %s" % rec.id})
                msg = _("✅ Listo: creé **%(name)s** (%(model)s, id %(id)s).") % {
                    "name": rec.display_name, "model": model, "id": rec.id}
            else:
                ids = json.loads(pending.res_ids or "[]")
                recs = env_user[model].browse(ids).exists()
                if not recs:
                    raise UserError(_("Los registros ya no existen o no podés verlos."))
                recs.check_access("write")
                recs.write(values)
                pending.write({"state": "done", "result_info": "modificados %s" % len(recs)})
                msg = _("✅ Listo: modifiqué %(n)s registro(s) de %(model)s.") % {
                    "n": len(recs), "model": model}
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui: falló la ejecución de la escritura %s", pending.token)
            pending.write({"state": "error", "result_info": str(e)[:200]})
            msg = _("❌ No pude aplicar la operación %(token)s: %(err)s") % {
                "token": pending.token, "err": e}
        self._post_bot_reply(channel, msg)

    @api.model
    def _post_write_preview(self, channel, pending):
        """Postea (como el bot) el preview determinístico de una escritura propuesta."""
        op = {"create": _("crear"), "write": _("modificar"),
              "import": _("importar")}.get(pending.operation, _("modificar"))
        # Render del resumen respetando saltos de línea (las facturas son multilínea).
        summary_html = Markup("<br/>").join(
            escape(line) for line in (pending.summary or "").split("\n")
        )
        body = Markup(
            "<p>📝 <b>Confirmación requerida</b> — Sagui quiere <b>%(op)s</b> datos:</p>"
            "<blockquote>%(summary)s</blockquote>"
            "<p>Para aplicar respondé <b>confirmar %(token)s</b>; "
            "para descartar, <b>cancelar %(token)s</b>.</p>"
        ) % {"op": op, "summary": summary_html, "token": pending.token}
        bot_partner = self.env.ref(
            "primate_sagui.partner_sagui_bot", raise_if_not_found=False
        )
        kwargs = {"body": body, "message_type": "comment", "subtype_xmlid": "mail.mt_comment"}
        if bot_partner:
            kwargs["author_id"] = bot_partner.id
        channel.sudo().message_post(**kwargs)

    # ---------- Fase 2: "Consultar a Sagui" desde un formulario ----------
    @api.model
    def action_consultar_registro(self, res_model, res_id):
        """Abre el DM con Sagui sembrando el contexto del registro actual.

        Llamado desde el botón del control panel (cualquier formulario). Se ejecuta
        como el usuario que clickea, así que respeta sus permisos: si no puede ver el
        registro, Odoo levanta AccessError y no se siembra nada.

        Devuelve la acción cliente que enfoca el canal en Discuss (verificado contra
        v19: mail.action_discuss con active_id = 'discuss.channel_<id>').
        """
        if not res_model or not res_id:
            raise UserError(_("No hay un registro válido para consultar."))

        # Leer el registro CON LOS PERMISOS DEL USUARIO (nunca sudo para negocio).
        record = self.env[res_model].browse(int(res_id))
        record.check_access("read")  # AccessError si no puede; MissingError si no existe
        display_name = record.display_name

        bot_partner = self.env.ref(
            "primate_sagui.partner_sagui_bot", raise_if_not_found=False
        )
        if not bot_partner:
            raise UserError(_("El usuario Sagui no está configurado."))

        # DM (chat) entre el usuario actual y Sagui. _get_or_create_chat es el helper
        # real de discuss.channel en v19.
        channel = self.env["discuss.channel"]._get_or_create_chat([bot_partner.id])

        # Nota de contexto: se postea como el usuario, pero con sagui_skip_reply para
        # que el hook NO dispare una respuesta automática (sería responder a la nada).
        # Queda en el historial del canal, así cuando el usuario pregunte, Claude ya
        # sabe de qué registro se trata (modelo + id) y puede usar buscar_registros.
        body = Markup(
            "<p>📌 <b>Contexto:</b> consulta sobre el registro <b>%(name)s</b> "
            "del modelo <code>%(model)s</code> (id %(id)s).<br/>"
            "Preguntame lo que necesites sobre este registro.</p>"
        ) % {"name": display_name or "", "model": res_model, "id": res_id}

        channel.with_context(sagui_skip_reply=True).message_post(
            body=body,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
            author_id=self.env.user.partner_id.id,
        )

        return {
            "type": "ir.actions.client",
            "tag": "mail.action_discuss",
            "params": {"active_id": "discuss.channel_%s" % channel.id},
        }

    # ---------- helpers verificados contra Discuss v19 ----------
    @api.model
    def _build_history(self, channel):
        """Mapea los últimos mensajes del canal a [{role, content}] para la API.

        - message_ids viene ordenado por 'id desc' (más nuevo primero): tomamos los
          últimos HISTORY_LIMIT y los damos vuelta a orden cronológico.
        - Solo comentarios de usuario (no notificaciones).
        - autor == bot Sagui -> 'assistant'; cualquier otro -> 'user'.
        - Adjuntos de imagen/PDF en mensajes del usuario se mandan como bloques
          multimodales (Fase 4); van ANTES del texto (best practice de la API).
        - Se limpia el HTML del body a texto plano. Se colapsan roles consecutivos y
          se garantiza que el primer turno sea 'user'.
        - content se manda como string si el turno es solo texto (compat), o como lista
          de bloques si trae imágenes/PDF.
        """
        bot_partner = self.env.ref(
            "primate_sagui.partner_sagui_bot", raise_if_not_found=False
        )
        bot_partner_id = bot_partner.id if bot_partner else False

        recent = channel.message_ids.filtered(
            lambda m: m.message_type == "comment"
        )[:HISTORY_LIMIT]
        # Solo el mensaje MÁS NUEVO manda el binario pesado (imágenes/PDF); los mensajes anteriores
        # van únicamente con la NOTA de texto (que ya lleva el attachment_id). Re-enviar varios MB de
        # adjuntos en CADA turno hacía el chat lentísimo y degradaba el tool-use (el modelo dejaba de
        # invocar tools y "narraba"). Sagui retiene el id por la nota y sigue pudiendo operar con él.
        newest_id = recent[:1].id

        turns = []          # [{"role":..., "blocks":[...]}]
        media_count = 0
        for msg in reversed(recent):
            role = "assistant" if msg.author_id.id == bot_partner_id else "user"
            blocks = []
            # Adjuntos solo de mensajes del usuario (el bot no manda archivos).
            if role == "user":
                for att in msg.attachment_ids:
                    mimetype = (att.mimetype or "").split(";")[0].strip().lower()
                    # Planillas: no se manda el binario; se le pasa el id al LLM por texto.
                    if mimetype in SPREADSHEET_TYPES:
                        blocks.append({"type": "text", "text": (
                            "[Planilla adjunta: «%s» — attachment_id=%s, tipo=%s. Si el usuario "
                            "quiere importarla, usá analizar_importacion con ese attachment_id.]"
                            % (att.name or "archivo", att.id, mimetype))})
                        continue
                    # PDF: la NOTA con el id va SIEMPRE (habilita analizar_sitio / facturas, sin
                    # importar el tamaño: analizar_sitio extrae por su cuenta). El contenido como
                    # document block solo si entra en el límite (para leer facturas directamente).
                    if mimetype == "application/pdf":
                        if msg.id == newest_id and media_count < MAX_MEDIA_BLOCKS:
                            block = self._attachment_to_block(att)
                            if block:
                                blocks.append(block)
                                media_count += 1
                        blocks.append({"type": "text", "text": (
                            "[PDF adjunto: «%s» — attachment_id=%s. Para generar un SITIO WEB a "
                            "partir de este diseño usá analizar_sitio con ese attachment_id; para "
                            "leer una factura usá su contenido directamente.]"
                            % (att.name or "archivo", att.id))})
                        continue
                    # Imágenes.
                    if msg.id == newest_id and media_count < MAX_MEDIA_BLOCKS:
                        block = self._attachment_to_block(att)
                        if block:
                            blocks.append(block)   # media antes del texto
                            media_count += 1
                    # NOTA con el id va SIEMPRE (aunque se haya saltado el binario por el cap):
                    # habilita greenfield (logo/identidad → sitio premium) y que el modelo
                    # referencie el adjunto por id. No importar como factura desde acá.
                    blocks.append({"type": "text", "text": (
                        "[Imagen adjunta: «%s» — attachment_id=%s, tipo=%s. Si es un LOGO/identidad "
                        "de marca y el usuario quiere un SITIO web premium desde cero, usá "
                        "disenar_sitio_marca con ese attachment_id.]"
                        % (att.name or "archivo", att.id, mimetype))})
            text = html2plaintext(msg.body or "").strip()
            if text:
                blocks.append({"type": "text", "text": text})
            if not blocks:
                continue
            # Colapsar mensajes consecutivos del mismo rol en un solo turno.
            if turns and turns[-1]["role"] == role:
                turns[-1]["blocks"].extend(blocks)
            else:
                turns.append({"role": role, "blocks": blocks})

        # La Messages API exige que el primer mensaje sea de rol 'user'.
        while turns and turns[0]["role"] != "user":
            turns.pop(0)

        messages = []
        for t in turns:
            blocks = t["blocks"]
            if len(blocks) == 1 and blocks[0]["type"] == "text":
                messages.append({"role": t["role"], "content": blocks[0]["text"]})
            else:
                messages.append({"role": t["role"], "content": blocks})
        return messages

    @api.model
    def _attachment_to_block(self, att):
        """Convierte un ir.attachment de imagen/PDF en un bloque de contenido de la API.

        Se lee con los permisos del usuario (self.env ya es el autor en el hook). Devuelve
        None si el tipo no es soportado, está vacío o supera el tope de tamaño.
        """
        mimetype = (att.mimetype or "").split(";")[0].strip().lower()
        if mimetype not in SUPPORTED_IMAGE_TYPES and mimetype not in SUPPORTED_DOC_TYPES:
            return None
        try:
            raw = att.raw
        except Exception:  # noqa: BLE001 - sin acceso/contenido -> se omite
            return None
        if not raw:
            return None
        if len(raw) > MAX_MEDIA_BYTES:
            _logger.info(
                "Sagui: adjunto '%s' omitido por tamaño (%s bytes)", att.name, len(raw)
            )
            return None
        data_b64 = base64.b64encode(raw).decode("ascii")
        kind = "image" if mimetype in SUPPORTED_IMAGE_TYPES else "document"
        return {
            "type": kind,
            "source": {"type": "base64", "media_type": mimetype, "data": data_b64},
        }

    @api.model
    def _post_bot_reply(self, channel, text):
        """Postea la respuesta como el bot Sagui.

        sudo está permitido acá: es infraestructura (postear como el bot), no una
        consulta de negocio. El subtype/silent espejan cómo responde OdooBot.
        """
        bot_partner = self.env.ref(
            "primate_sagui.partner_sagui_bot", raise_if_not_found=False
        )
        body = self._markdown_to_html(text)
        kwargs = {
            "body": body,
            "message_type": "comment",
            "subtype_xmlid": "mail.mt_comment",
        }
        if bot_partner:
            kwargs["author_id"] = bot_partner.id
        channel.sudo().message_post(**kwargs)

    @api.model
    def _markdown_to_html(self, text):
        """Convierte el markdown básico de Claude a HTML seguro.

        No dependemos de una librería de markdown (no está garantizada en Odoo):
        escapamos todo el texto (anti-inyección) y luego aplicamos un set mínimo de
        reglas sobre el texto ya escapado. El resultado es Markup, así que
        message_post no lo vuelve a escapar.
        """
        if not text:
            return Markup("")
        # 1) Escapar: a partir de acá trabajamos sobre HTML seguro.
        safe = str(escape(text))
        # 2) Reglas mínimas de markdown sobre el texto escapado.
        #    Negrita **...**, itálica *...*, código `...`.
        safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
        safe = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", safe)
        safe = re.sub(r"`(.+?)`", r"<code>\1</code>", safe)
        # 3) Saltos de línea -> <br/>.
        safe = safe.replace("\n", "<br/>")
        return Markup(safe)
