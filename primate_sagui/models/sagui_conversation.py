# -*- coding: utf-8 -*-
# Persistencia de las conversaciones del chat WEB de Sagui (sección Conversaciones de
# primate_sagui_web). Es SEPARADO del bot de Discuss (que usa discuss.channel): acá cada usuario
# tiene sus propias conversaciones, aisladas por record rule (nunca ve las de otro).
from odoo import api, fields, models


class SaguiConversation(models.Model):
    _name = "sagui.conversation"
    _description = "Conversación de Sagui (web)"
    _order = "last_activity desc, id desc"

    name = fields.Char(string="Título", required=True, default="Nueva conversación")
    user_id = fields.Many2one(
        "res.users", string="Dueño", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.uid,
    )
    last_activity = fields.Datetime(string="Última actividad", default=fields.Datetime.now, index=True)
    active = fields.Boolean(default=True)
    message_ids = fields.One2many("sagui.message", "conversation_id", string="Mensajes")

    # ---- API para el front (RPC). Las record rules garantizan el aislamiento por usuario. ----
    @api.model
    def list_conversations(self):
        """Mis conversaciones (la record rule filtra a las del usuario actual)."""
        return [{
            "id": c.id,
            "name": c.name,
            "last_activity": fields.Datetime.to_string(c.last_activity) if c.last_activity else False,
        } for c in self.search([])]

    @api.model
    def create_conversation(self, name=None):
        c = self.create({"name": (name or "").strip() or "Nueva conversación"})
        return {"id": c.id, "name": c.name,
                "last_activity": fields.Datetime.to_string(c.last_activity) if c.last_activity else False}

    @api.model
    def delete_conversation(self, conversation_id):
        # search (no browse) para que la record rule filtre: si no es del usuario, no la encuentra.
        conv = self.search([("id", "=", int(conversation_id))])
        if conv:
            conv.unlink()
        return True

    @api.model
    def get_conversation_messages(self, conversation_id):
        conv = self.search([("id", "=", int(conversation_id))])
        if not conv:
            return []
        return [{"role": m.role, "body": m.body or ""} for m in conv.message_ids.sorted("id")]


class SaguiMessage(models.Model):
    _name = "sagui.message"
    _description = "Mensaje de una conversación de Sagui"
    _order = "id"

    conversation_id = fields.Many2one(
        "sagui.conversation", string="Conversación", required=True, index=True, ondelete="cascade")
    role = fields.Selection(
        [("user", "Usuario"), ("assistant", "Sagui")], string="Rol", required=True)
    body = fields.Text(string="Texto")
    input_tokens = fields.Integer(string="Tokens entrada")
    output_tokens = fields.Integer(string="Tokens salida")
    cost = fields.Float(string="Costo", digits=(12, 6))
