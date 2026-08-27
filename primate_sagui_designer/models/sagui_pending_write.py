# -*- coding: utf-8 -*-
# El rol diseñador registra su propuesta como una escritura pendiente más, para reusar tal cual el
# gate humano que ya existe: nada se construye ni se publica sin un "confirmar" explícito.
from odoo import fields, models


class SaguiPendingWrite(models.Model):
    _inherit = "primate.sagui.pending.write"

    operation = fields.Selection(
        selection_add=[("website_designer", "Diseñar sitio (rol diseñador)")],
        ondelete={"website_designer": "cascade"},
    )
