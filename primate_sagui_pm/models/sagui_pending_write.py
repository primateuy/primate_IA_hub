# -*- coding: utf-8 -*-
# Marca de procedencia en las DOS bandejas que ya existen, para poder aprobar POR BLOQUE.
#
# No se crea una tercera bandeja: `primate.sagui.pending.write` (receta, con humano presente) y
# `sagui.automation.proposal` (automatización, desatendida) ya resuelven el gate y la auditoría.
# Lo único que faltaba era saber DE QUÉ regla y DE QUÉ proyecto viene cada propuesta, que es lo
# que permite "aprobar todas las tareas sin etapa de este proyecto" de una sola vez en vez de ir
# ítem por ítem -que es como una bandeja de 60 propuestas termina sin mirarse-.
from odoo import fields, models


class SaguiPendingWrite(models.Model):
    _inherit = "primate.sagui.pending.write"

    pm_run_id = fields.Many2one("sagui.pm.run", string="Corrida del gestor",
                                ondelete="set null", index=True)
    pm_rule_key = fields.Char(string="Regla del gestor", index=True)
    pm_project_id = fields.Many2one("project.project", string="Proyecto (gestor)",
                                    ondelete="set null", index=True)
    pm_area_id = fields.Many2one("primate.area", string="Área (gestor)",
                                 ondelete="set null", index=True)


class SaguiAutomationProposal(models.Model):
    _inherit = "sagui.automation.proposal"

    pm_run_id = fields.Many2one("sagui.pm.run", string="Corrida del gestor",
                                ondelete="set null", index=True)
    pm_rule_key = fields.Char(string="Regla del gestor", index=True)
    pm_project_id = fields.Many2one("project.project", string="Proyecto (gestor)",
                                    ondelete="set null", index=True)
    pm_area_id = fields.Many2one("primate.area", string="Área (gestor)",
                                 ondelete="set null", index=True)
