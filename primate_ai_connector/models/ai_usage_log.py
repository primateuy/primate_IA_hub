# -*- coding: utf-8 -*-
from odoo import api, fields, models

# Tarifas (input, output, cache_write_5m, cache_read) en USD por millón de tokens.
# La escritura de caché cuesta ~1.25x el input; la lectura ~0.1x. Ajustar si cambian.
RATES = {
    "claude-sonnet-4-6": (3.0, 15.0, 3.75, 0.30),
    "claude-haiku-4-5-20251001": (1.0, 5.0, 1.25, 0.10),
}
DEFAULT_RATE = (3.0, 15.0, 3.75, 0.30)


class PrimateAIUsageLog(models.Model):
    _name = "primate.ai.usage.log"
    _description = "Registro de uso de la API de IA"
    _order = "create_date desc"

    model_name = fields.Char(string="Modelo", index=True)
    input_tokens = fields.Integer(string="Tokens entrada")
    output_tokens = fields.Integer(string="Tokens salida")
    cache_write_tokens = fields.Integer(string="Tokens escritura caché")
    cache_read_tokens = fields.Integer(string="Tokens lectura caché")
    user_id = fields.Many2one("res.users", string="Usuario", index=True)
    # Acción de negocio que agrupa esta llamada (conversación, generar sitio, etc.).
    # Se estampa desde el contexto (ai_action_id) en _log_usage. ondelete set null:
    # borrar la acción no debe perder el registro de uso/costo.
    action_id = fields.Many2one(
        "primate.ai.action", string="Acción", index=True, ondelete="set null")
    action_type = fields.Selection(
        related="action_id.action_type", string="Tipo de acción", store=True, index=True)
    cost = fields.Float(
        string="Costo estimado (USD)", compute="_compute_cost", store=True, digits=(12, 5)
    )

    @api.depends(
        "model_name", "input_tokens", "output_tokens",
        "cache_write_tokens", "cache_read_tokens",
    )
    def _compute_cost(self):
        for rec in self:
            pin, pout, pcw, pcr = RATES.get(rec.model_name, DEFAULT_RATE)
            rec.cost = (
                (rec.input_tokens / 1e6) * pin
                + (rec.output_tokens / 1e6) * pout
                + (rec.cache_write_tokens / 1e6) * pcw
                + (rec.cache_read_tokens / 1e6) * pcr
            )
