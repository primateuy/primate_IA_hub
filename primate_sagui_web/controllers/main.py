# -*- coding: utf-8 -*-
# Endpoint que recibe el mensaje del usuario y devuelve la respuesta en streaming (SSE).
#
# [VERIFICADO v19] Streaming HTTP: odoo.http.Response con un generador como cuerpo y
# direct_passthrough=True. Headers text/event-stream + X-Accel-Buffering:no (que nginx no buffee).
#
# CURSOR/ENV PROPIO (clave): el cursor del request se cierra al RETORNAR el controller, pero el
# generador se consume DESPUÉS. Abrimos un cursor/env nuevo DENTRO del generador con el uid del
# usuario (patrón addons/mail/models/mail_mail.py: Registry().cursor() + api.Environment, commitea
# al salir del with). Las tools Y la persistencia corren CON LOS PERMISOS DEL USUARIO (sin sudo).
#
# CONVERSACIONES PERSISTIDAS: si llega conversation_id, el historial se carga DESDE la conversación
# (sagui.message), no del cliente, y el turno (user + assistant) se PERSISTE dentro de este cursor.
# Sin conversation_id → modo efímero (el panel del systray), usando el history del cliente.
#
# LECTURA + ESCRITURA: expone read-tools y write-tools (crear/modificar) con el flujo de
# confirmación de Discuss ('confirmar <token>'), respaldado en el canal DM del usuario.
import json
import logging

from odoo import api, fields, http
from odoo.http import request, Response
from odoo.modules.registry import Registry
from odoo.tools import html2plaintext

from odoo.addons.primate_sagui.models.sagui_assistant import CONFIRM_RE

_logger = logging.getLogger(__name__)

WEB_TOOLS = ("buscar_registros", "agrupar_registros", "describir_modelo",
             "crear_registro", "modificar_registros")
_OP_LABEL = {"create": "crear", "write": "modificar", "import": "importar"}


class SaguiWebController(http.Controller):

    @http.route("/sagui/ask", type="http", auth="user", methods=["POST"], csrf=False)
    def ask(self, **kw):
        """Recibe {message, conversation_id?, history?} y devuelve eventos SSE del stream."""
        try:
            data = json.loads(request.httprequest.data or "{}")
        except ValueError:
            data = {}
        message = (data.get("message") or "").strip()
        history = data.get("history") or []            # solo modo efímero (sin conversation_id)
        conversation_id = data.get("conversation_id")  # modo persistido

        dbname = request.env.cr.dbname
        uid = request.env.uid
        context = dict(request.env.context)

        def sse(event):
            return ("data: %s\n\n" % json.dumps(event, ensure_ascii=False)).encode("utf-8")

        def event_stream():
            if not message:
                yield sse({"type": "error", "message": "Mensaje vacío."})
                yield sse({"type": "done", "usage": {}})
                return

            with Registry(dbname).cursor() as cr:
                env = api.Environment(cr, uid, context)
                assistant = env["primate.sagui.assistant"]
                connector = env["primate.ai.connector"]
                user = env.user

                bot = env.ref("primate_sagui.partner_sagui_bot", raise_if_not_found=False)
                channel = env["discuss.channel"]._get_or_create_chat([bot.id]) if bot else None

                # Conversación persistida (search → la record rule la filtra al dueño).
                conv = None
                if conversation_id:
                    conv = env["sagui.conversation"].search([("id", "=", int(conversation_id))])
                Msg = env["sagui.message"]

                def persist(role, body):
                    if conv and (body or "").strip():
                        Msg.create({"conversation_id": conv.id, "role": role, "body": body})

                # 1) CONFIRMACIÓN/CANCELACIÓN determinística (no pasa por el LLM).
                if channel and CONFIRM_RE.match(message):
                    last_id = max(channel.message_ids.ids or [0])
                    try:
                        assistant._handle_confirmation(channel, user, message)
                    except Exception as e:  # noqa: BLE001
                        yield sse({"type": "error", "message": str(e)})
                        yield sse({"type": "done", "usage": {}})
                        return
                    new_bot = channel.message_ids.filtered(
                        lambda m: m.id > last_id and m.author_id.id == bot.id
                    ).sorted("id")
                    txt = "\n\n".join(html2plaintext(m.body or "") for m in new_bot) or "Listo."
                    yield sse({"type": "text", "text": txt})
                    if conv:
                        persist("user", message)
                        persist("assistant", txt)
                        conv.write({"last_activity": fields.Datetime.now()})
                    yield sse({"type": "done", "usage": {}})
                    return

                # 2) Conversación normal. Historial DESDE la conversación si está persistida.
                if conv:
                    hist = [{"role": m.role, "content": m.body or ""}
                            for m in conv.message_ids.sorted("id")]
                else:
                    hist = list(history)
                messages = hist + [{"role": "user", "content": message}]
                model = assistant._pick_model(message)
                # read+write de datos + tools de conectores (mcp__*, read-only en FASE 1).
                tool_specs = [t for t in assistant._tool_specs()
                              if t["name"] in WEB_TOOLS or t["name"].startswith("mcp__")]
                proposals = []
                full = ""

                # Acción de negocio para costeo/drill-down: este turno = una conversación.
                first_line = (message or "").strip().splitlines()
                label = (conv.name if conv and conv.name and conv.name != "Nueva conversación"
                         else (first_line[0][:60] if first_line else "Conversación"))
                action = env["primate.ai.action"]._open(
                    "conversacion", label,
                    ref=("sagui.conversation,%s" % conv.id) if conv else False)
                connector = connector.with_context(ai_action_id=action.id)

                try:
                    for ev in connector.stream_conversation(
                        messages,
                        system=assistant._system_prompt(),
                        tool_specs=tool_specs,
                        tool_runner=lambda n, a: assistant._run_tool(n, a, user, channel, proposals),
                        model=model,
                        cache=True,
                    ):
                        if ev.get("type") == "text":
                            full += ev.get("text") or ""
                        yield sse(ev)
                except Exception as e:  # noqa: BLE001 - se reporta al cliente, no rompe
                    _logger.exception("Sagui web: error en el stream")
                    yield sse({"type": "error", "message": str(e)})

                # Preview determinístico de cada propuesta de escritura (fuente: los datos guardados).
                Pending = env["primate.sagui.pending.write"].sudo()
                for pid in proposals:
                    p = Pending.browse(pid)
                    if p.exists() and p.state == "pending":
                        op = _OP_LABEL.get(p.operation, "modificar")
                        preview = ("\n\n📝 Confirmación requerida — Sagui quiere %s datos:\n%s\n\n"
                                   "Respondé **confirmar %s** para aplicar, o **cancelar %s**."
                                   % (op, p.summary or "", p.token, p.token))
                        full += preview
                        yield sse({"type": "text", "text": preview})

                # Persistir el turno + actualizar actividad/título (en este cursor; commitea al salir).
                if conv:
                    persist("user", message)
                    persist("assistant", full)
                    vals = {"last_activity": fields.Datetime.now()}
                    if conv.name == "Nueva conversación":
                        title = (message.strip().splitlines() or [""])[0][:60].strip()
                        vals["name"] = title or "Conversación"
                    conv.write(vals)

                yield sse({"type": "done", "usage": {}})

        headers = [
            ("Content-Type", "text/event-stream; charset=utf-8"),
            ("Cache-Control", "no-cache"),
            ("X-Accel-Buffering", "no"),
            ("Connection", "keep-alive"),
        ]
        return Response(event_stream(), headers=headers, direct_passthrough=True)
