# -*- coding: utf-8 -*-
# Hook: cuando el usuario le escribe al bot Sagui en un canal, dispara la respuesta.
#
# [VERIFICADO v19] Punto de extensión: addons/mail_bot/models/discuss_channel.py
# engancha la lógica de OdooBot en `_message_post_after_hook(self, message, msg_vals)`
# (no en `message_post`). Modelamos a Sagui sobre ese patrón: misma guardia
# anti-recursión que `mail.bot._apply_logic` (comparar el author del mensaje contra
# el partner del bot) y misma forma de responder (channel.sudo().message_post con
# author_id del bot). La membresía se consulta vía channel_member_ids.partner_id,
# que es el campo real del modelo en v19.
import logging

from odoo import models

_logger = logging.getLogger(__name__)


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    def _message_post_after_hook(self, message, msg_vals):
        # Primero la lógica estándar (incluye el hook de OdooBot vía super()).
        res = super()._message_post_after_hook(message, msg_vals)
        try:
            self._sagui_maybe_reply(message, msg_vals)
        except Exception:  # noqa: BLE001 - nunca romper el message_post por culpa del bot
            _logger.exception("Sagui: error al evaluar/disparar la respuesta entrante")
        return res

    def _sagui_maybe_reply(self, message, msg_vals):
        """Decide si Sagui debe responder a este mensaje y, si corresponde, lo hace.

        Reglas (espejadas de cómo responde OdooBot):
        - El bot debe ser miembro del canal.
        - El autor NO puede ser el propio bot (guardia anti-recursión).
        - Solo mensajes tipo 'comment' (no notificaciones del sistema).
        - Responde en chats directos (DM) o cuando se lo menciona en otros canales.
        """
        self.ensure_one()

        # Fase 2: la nota de contexto sembrada por el botón "Consultar a Sagui" se
        # postea con este flag para NO disparar una respuesta automática (es solo
        # contexto; Sagui responde recién cuando el usuario pregunta).
        if self.env.context.get("sagui_skip_reply"):
            return

        bot_partner = self.env.ref(
            "primate_sagui.partner_sagui_bot", raise_if_not_found=False
        )
        if not bot_partner:
            return

        # msg_vals trae los valores crudos del message_post; author_id puede venir ahí
        # o en el propio message. Igual que mail.bot, priorizamos msg_vals.
        author_partner_id = msg_vals.get("author_id") or message.author_id.id
        # Guardia anti-recursión: si el que postea es el bot, no responder.
        if not author_partner_id or author_partner_id == bot_partner.id:
            return

        # Solo comentarios de usuario.
        message_type = msg_vals.get("message_type") or message.message_type
        if message_type != "comment":
            return

        # El bot tiene que ser miembro del canal (DM o canal donde se lo invitó).
        member_partner_ids = self.channel_member_ids.partner_id.ids
        if bot_partner.id not in member_partner_ids:
            return

        # Responder en DM siempre; en canales grupales/públicos solo si se lo mencionó.
        mentioned_ids = msg_vals.get("partner_ids") or message.partner_ids.ids or []
        if self.channel_type != "chat" and bot_partner.id not in mentioned_ids:
            return

        # Resolver el usuario humano autor (para ejecutar las tools con SUS permisos).
        # sudo solo para resolver el mapping partner->user (infraestructura, no negocio).
        author_user = self.env["res.users"].sudo().search(
            [("partner_id", "=", author_partner_id)], limit=1
        )
        if not author_user or author_user.id == self.env.ref("base.user_root").id:
            return

        # Síncrono: la llamada a Claude tarda unos segundos y bloquea el message_post.
        # Trade-off documentado (SPEC T3): no dependemos de queue_job en v1; si el
        # ecosistema lo trae, se puede diferir con with_delay para no bloquear el POST.
        self.env["primate.sagui.assistant"].process_user_message(
            self, author_user, message
        )
