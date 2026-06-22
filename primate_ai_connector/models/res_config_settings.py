# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    primate_ai_api_key = fields.Char(
        string="API key de Claude",
        config_parameter="primate_ai.api_key",
    )
    primate_ai_model = fields.Char(
        string="Modelo (análisis)",
        config_parameter="primate_ai.model",
        default="claude-sonnet-4-6",
        help="Modelo para análisis/documentación. Recomendado: claude-sonnet-4-6.",
    )
    primate_ai_model_fast = fields.Char(
        string="Modelo rápido (consultas simples)",
        config_parameter="primate_ai.model_fast",
        default="claude-haiku-4-5-20251001",
        help="Modelo barato para consultas simples (tiering). Ej: claude-haiku-4-5-20251001. "
             "Sagui lo usa para preguntas cortas; con /analiza forzás el modelo de análisis.",
    )
