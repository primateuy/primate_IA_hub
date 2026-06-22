# -*- coding: utf-8 -*-
from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    # Whitelist de modelos donde Sagui PUEDE proponer escrituras (create/write).
    # Lista separada por comas/espacios de nombres técnicos, ej. "res.partner, crm.lead".
    # Vacía (por defecto) = escritura deshabilitada: las tools de escritura ni se exponen.
    primate_sagui_write_whitelist = fields.Char(
        string="Modelos con escritura habilitada (Sagui)",
        config_parameter="primate_sagui.write_whitelist",
        help="Lista separada por comas de modelos técnicos donde Sagui puede proponer "
             "creación/modificación de registros (siempre con confirmación del usuario). "
             "Dejar vacío para deshabilitar toda escritura.",
    )
