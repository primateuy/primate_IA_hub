# -*- coding: utf-8 -*-
# Registro de una corrida de auditoría y sus hallazgos.
#
# Existe para una cosa concreta: DECIDIR QUÉ REGLAS PUEDEN PASAR A MODO 'auto'. La decisión no se
# toma por intuición sino por la tasa de propuestas aprobadas SIN CAMBIOS de cada regla, corrida
# tras corrida. Por eso el hallazgo guarda su regla y su propuesta, y no sólo un texto.
from odoo import api, fields, models, _


class SaguiPmRun(models.Model):
    _name = "sagui.pm.run"
    _description = "Corrida del Gestor de Proyectos"
    _order = "id desc"

    name = fields.Char(string="Referencia", compute="_compute_name", store=True)
    origen = fields.Selection(
        [("receta", "Receta"), ("automatizacion", "Automatización")],
        string="Origen", required=True, default="receta", index=True)
    recipe_id = fields.Many2one("sagui.recipe", string="Receta", ondelete="set null", index=True)
    user_id = fields.Many2one(
        "res.users", string="Ejecutó", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.uid)
    date = fields.Datetime(string="Fecha", default=fields.Datetime.now, required=True, index=True)

    alcance = fields.Selection(
        [("todo", "Todo"), ("area", "Un área"), ("proyecto", "Un proyecto")],
        string="Alcance", required=True, default="todo")
    area_id = fields.Many2one("primate.area", string="Área", ondelete="set null", index=True)
    project_id = fields.Many2one("project.project", string="Proyecto", ondelete="set null", index=True)
    dias = fields.Integer(string="Días sin movimiento")
    desde = fields.Datetime(
        string="Cambios desde",
        help="Vacío = auditoría completa. Con fecha, sólo lo que cambió desde la corrida anterior.")

    finding_ids = fields.One2many("sagui.pm.finding", "run_id", string="Hallazgos")
    informe = fields.Text(string="Informe")

    finding_count = fields.Integer(string="Hallazgos", compute="_compute_counts", store=True)
    propuesta_count = fields.Integer(string="Propuestas", compute="_compute_counts", store=True)
    pregunta_count = fields.Integer(string="Preguntas", compute="_compute_counts", store=True)
    nota_count = fields.Integer(string="Notas", compute="_compute_counts", store=True)
    aprobadas_count = fields.Integer(string="Aprobadas", compute="_compute_resultado")
    rechazadas_count = fields.Integer(string="Rechazadas", compute="_compute_resultado")
    pendientes_count = fields.Integer(string="Pendientes", compute="_compute_resultado")

    @api.depends("date", "alcance", "area_id", "project_id")
    def _compute_name(self):
        for run in self:
            if run.alcance == "area":
                donde = run.area_id.display_name or _("un área")
            elif run.alcance == "proyecto":
                donde = run.project_id.display_name or _("un proyecto")
            else:
                donde = _("todo")
            run.name = _("Auditoría %(d)s — %(w)s") % {
                "d": fields.Datetime.to_string(run.date or fields.Datetime.now())[:16], "w": donde}

    @api.depends("finding_ids", "finding_ids.tipo")
    def _compute_counts(self):
        for run in self:
            tipos = run.finding_ids.mapped("tipo")
            run.finding_count = len(tipos)
            run.propuesta_count = tipos.count("propuesta")
            run.pregunta_count = tipos.count("pregunta")
            run.nota_count = tipos.count("nota")

    @api.depends("finding_ids.pending_write_id.state")
    def _compute_resultado(self):
        """Aprobadas / rechazadas / pendientes salen del ESTADO REAL de la propuesta, no de un
        contador propio: un contador propio se desincroniza en cuanto alguien aprueba desde otro
        lado, y este número es el que decide qué regla pasa a 'auto'.

        El `depends` NO es decorativo: sin él el compute se calcula una vez, queda cacheado y
        sigue devolviendo el valor viejo después de aprobar. Lo agarró el test que lee el contador
        antes y después de aprobar un bloque.
        """
        for run in self:
            estados = run.finding_ids.mapped("pending_write_id.state")
            run.aprobadas_count = estados.count("done")
            run.rechazadas_count = estados.count("cancelled") + estados.count("expired")
            run.pendientes_count = estados.count("pending") + estados.count("processing")

    # ------------------------------------------------------------------ aprobación por bloque
    def aprobar_bloque(self, regla=None, project_id=None):
        """Aprueba de una sola vez todas las propuestas de una regla y/o de un proyecto.

        Es la razón de ser de los campos pm_*. Una bandeja de 60 propuestas que sólo se aprueban
        de a una no se aprueba: se ignora. Pero el bloque NO es un atajo al gate — cada propuesta
        se aplica por el mismo `web_confirm_pending` de siempre, con los permisos del usuario,
        su revalidación y su registro en el audit log. Lo único que cambia es cuántos clics cuesta.

        Devuelve {aplicadas, fallidas, errores}.
        """
        self.ensure_one()
        hallazgos = self.finding_ids.filtered(lambda f: f.pending_write_id.state == "pending")
        if regla:
            hallazgos = hallazgos.filtered(lambda f: f.regla == regla)
        if project_id:
            hallazgos = hallazgos.filtered(lambda f: f.project_id.id == int(project_id))
        aplicadas, errores = 0, []
        Recipe = self.env["sagui.recipe"]
        for hallazgo in hallazgos:
            resultado = Recipe.web_confirm_pending(hallazgo.pending_write_id.token)
            if resultado.get("ok"):
                aplicadas += 1
            else:
                errores.append("%s: %s" % (hallazgo.res_name, resultado.get("error")))
        return {"aplicadas": aplicadas, "fallidas": len(errores), "errores": errores}

    def rechazar_bloque(self, regla=None, project_id=None):
        """La contraparte: descartar todo un bloque sin aplicar nada."""
        self.ensure_one()
        hallazgos = self.finding_ids.filtered(lambda f: f.pending_write_id.state == "pending")
        if regla:
            hallazgos = hallazgos.filtered(lambda f: f.regla == regla)
        if project_id:
            hallazgos = hallazgos.filtered(lambda f: f.project_id.id == int(project_id))
        hallazgos.mapped("pending_write_id").sudo().write({"state": "cancelled"})
        return {"rechazadas": len(hallazgos)}

    def por_regla(self):
        """{clave de regla: {tipo: n}} — el conteo por regla del informe y de la promoción a auto."""
        self.ensure_one()
        salida = {}
        for hallazgo in self.finding_ids:
            fila = salida.setdefault(hallazgo.regla, {"propuesta": 0, "pregunta": 0, "nota": 0})
            fila[hallazgo.tipo] = fila.get(hallazgo.tipo, 0) + 1
        return salida


class SaguiPmFinding(models.Model):
    _name = "sagui.pm.finding"
    _description = "Hallazgo del Gestor de Proyectos"
    _order = "run_id desc, bloque, regla, id"

    run_id = fields.Many2one(
        "sagui.pm.run", string="Corrida", required=True, index=True, ondelete="cascade")
    regla = fields.Char(string="Regla", required=True, index=True)
    bloque = fields.Char(string="Bloque")
    titulo = fields.Char(string="Título de la regla")
    riesgo = fields.Selection(
        [("bajo", "Bajo"), ("medio", "Medio"), ("alto", "Alto"), ("nota", "Nota")],
        string="Riesgo", index=True,
        help="Sólo las reglas de riesgo BAJO son candidatas a pasar a modo automático, y sólo con "
             "evidencia de corridas aprobadas sin cambios.")
    tipo = fields.Selection(
        [("propuesta", "Propuesta"), ("pregunta", "Pregunta"), ("nota", "Nota")],
        string="Tipo", required=True, index=True,
        help="propuesta: hay un valor concreto y aplicable, va al gate. "
             "pregunta: hay un problema pero ningún valor deducible; NO se escribe nada. "
             "nota: hecho de gestión, no de higiene.")

    model_name = fields.Char(string="Modelo")
    res_id = fields.Integer(string="ID del registro")
    res_name = fields.Char(string="Registro")
    project_id = fields.Many2one("project.project", string="Proyecto", ondelete="set null", index=True)
    area_id = fields.Many2one("primate.area", string="Área", ondelete="set null", index=True)

    resumen = fields.Text(string="Resumen")
    detalle = fields.Text(string="Detalle")
    pending_write_id = fields.Many2one(
        "primate.sagui.pending.write", string="Propuesta", ondelete="set null", index=True)
    state = fields.Selection(related="pending_write_id.state", string="Estado de la propuesta")

    def action_abrir_registro(self):
        self.ensure_one()
        if not self.model_name or not self.res_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": self.model_name,
            "res_id": self.res_id,
            "views": [(False, "form")],
            "view_mode": "form",
            "target": "current",
        }
