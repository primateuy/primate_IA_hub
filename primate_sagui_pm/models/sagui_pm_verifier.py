# -*- coding: utf-8 -*-
# VERIFICADOR del Gestor de Proyectos: el segundo caso concreto del Check/Verify genérico.
#
# El primero fue el verificador visual del diseñador: capturas + un revisor independiente. Éste es
# el opuesto y por eso es el que hizo genérico al modelo: post-condiciones de DATOS, evidencia por
# consulta ORM, y revisor DETERMINÍSTICO. Cero tokens.
#
# Por qué determinístico y no un modelo leyendo el resultado: "¿hay tareas abiertas sin
# responsable?" tiene respuesta exacta. Un revisor que opina sobre esa respuesta sólo agrega la
# posibilidad de que diga que no cuando la respuesta es que sí. La independencia del revisor -que
# es lo que aporta en el caso visual- acá no compra nada: la consulta ya es independiente de quien
# hizo el cambio.
#
# VERIFICA EL ESTADO, NO LO QUE HIZO SAGUI. Una tarea sin responsable creada a mano dos minutos
# después de la corrida también reprueba. Es a propósito: la post-condición es sobre la base, no
# sobre la ejecución.
import json
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

RUBRICA_KEY = "pm-verifier-rubric"
MAX_IDS_EN_EVIDENCIA = 20


class SaguiPmVerifier(models.AbstractModel):
    _name = "primate.sagui.pm.verifier"
    _description = "Verificador de post-condiciones del Gestor de Proyectos"

    # ==================================================================
    #  Post-condiciones
    # ==================================================================
    @api.model
    def _postcondiciones(self, run):
        """Las cinco post-condiciones de la rúbrica, cada una como una consulta.

        Cada entrada trae su `buscar`, que devuelve el recordset que la VIOLA (vacío = se cumple).
        """
        return [
            {"id": "C1", "titulo": _("Ninguna tarea abierta sin responsable"),
             "buscar": "_violan_c1",
             "fix": _("Asignar responsable, o cerrar las que ya no aplican.")},
            {"id": "C2", "titulo": _("Ninguna tarea abierta sin etapa"),
             "buscar": "_violan_c2",
             "fix": _("Poner la etapa que corresponda; si el proyecto no tiene etapas, "
                      "configurarlas.")},
            {"id": "C3", "titulo": _("Toda tarea abierta está en un proyecto con área"),
             "buscar": "_violan_c3",
             "fix": _("Cargarle el área al proyecto.")},
            {"id": "C4", "titulo": _("Ninguna actividad vencida sin explicación"),
             "buscar": "_violan_c4",
             "fix": _("Cerrarla, reprogramarla, o dejar escrito en la nota por qué sigue "
                      "vencida.")},
            {"id": "C5", "titulo": _("Ninguna propuesta aplicada fuera del alcance"),
             "buscar": "_violan_c5",
             "fix": _("Revisar la corrida: una propuesta tocó algo fuera del alcance declarado.")},
        ]

    @api.model
    def _contexto(self, run):
        """Rearma el alcance de la corrida para consultar exactamente lo mismo que se auditó."""
        return self.env["primate.sagui.pm.engine"]._resolver_alcance(
            alcance=run.alcance,
            area_code=run.area_id.code if run.area_id else None,
            project_id=run.project_id.id if run.project_id else None,
            dias=run.dias or None,
        )

    @api.model
    def _tareas_abiertas(self, ctx):
        from .sagui_pm_engine import ESTADOS_CERRADOS
        return self.env["project.task"].search([
            ("project_id", "in", ctx["proyectos"].ids),
            ("state", "not in", list(ESTADOS_CERRADOS)),
        ])

    @api.model
    def _violan_c1(self, run, ctx):
        return self._tareas_abiertas(ctx).filtered(lambda t: not t.user_ids)

    @api.model
    def _violan_c2(self, run, ctx):
        return self._tareas_abiertas(ctx).filtered(lambda t: not t.stage_id)

    @api.model
    def _violan_c3(self, run, ctx):
        return self._tareas_abiertas(ctx).filtered(lambda t: not t.project_id.area_id)

    @api.model
    def _violan_c4(self, run, ctx):
        """Vencida hace más de N días Y sin nota.

        Una actividad vencida con una nota que dice por qué no es un problema de higiene: alguien
        decidió y lo dejó escrito. Lo que reprueba es el silencio.
        """
        from datetime import timedelta
        corte = ctx["hoy"] - timedelta(days=ctx["dias"])
        engine = self.env["primate.sagui.pm.engine"]
        vencidas = self.env["mail.activity"].search([
            ("date_deadline", "<", corte),
            ("res_model", "in", ["project.task", "project.project"]),
        ])
        salida = self.env["mail.activity"]
        for actividad in vencidas:
            proyecto = engine._proyecto_de(engine._registro_de(actividad))
            if proyecto not in ctx["proyectos"]:
                continue
            if not engine._texto_plano(actividad.note).strip():
                salida |= actividad
        return salida

    @api.model
    def _violan_c5(self, run, ctx):
        """Propuestas de ESTA corrida que se aplicaron sobre un proyecto fuera del alcance.

        Es la promesa más fuerte del rol -"con alcance = Técnica, ninguna propuesta toca proyectos
        de otra área"- y la única post-condición que no habla del estado de los datos sino de lo
        que hizo la corrida. Si falla, no hay dato sucio que arreglar: hay un bug de alcance.
        """
        aplicadas = run.finding_ids.filtered(
            lambda f: f.pending_write_id and f.pending_write_id.state == "done")
        return aplicadas.filtered(
            lambda f: f.project_id and f.project_id not in ctx["proyectos"])

    # ==================================================================
    #  Evidencia: una consulta por post-condición, sin capturas
    # ==================================================================
    @api.model
    def _collect_evidence(self, target):
        """Recolector de `sagui.verification`. Devuelve evidencia TEXTUAL con su `check`."""
        run = self.env["sagui.pm.run"].browse((target or {}).get("run_id")).exists()
        if not run:
            return []
        try:
            ctx = self._contexto(run)
        except Exception as e:  # noqa: BLE001
            # Sin alcance no hay nada que consultar: TODAS las post-condiciones caen como infra.
            _logger.exception("Sagui PM: no pude rearmar el alcance de la corrida %s", run.id)
            return [{"label": pc["titulo"], "text": "",
                     "check": dict(pc, ok=False, error=str(e)[:200], count=0, ids=[])}
                    for pc in self._postcondiciones(run)]

        evidencia = []
        for pc in self._postcondiciones(run):
            evidencia.append(self._evaluar(run, ctx, pc))
        return evidencia

    @api.model
    def _evaluar(self, run, ctx, pc):
        """Corre una post-condición. Un fallo de la CONSULTA se declara como error, no como fallo
        de gestión: reportar 'gestión reprobada' por un dominio roto manda a arreglar datos que
        están bien."""
        try:
            violan = getattr(self, pc["buscar"])(run, ctx)
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui PM: falló la post-condición %s", pc["id"])
            return {"label": "%s — %s" % (pc["id"], pc["titulo"]), "text": "",
                    "check": {"id": pc["id"], "titulo": pc["titulo"], "ok": False,
                              "count": 0, "ids": [], "detalle": "", "fix": pc["fix"],
                              "error": str(e)[:200]}}
        ids = violan.ids[:MAX_IDS_EN_EVIDENCIA]
        ok = not violan
        detalle = "" if ok else _(
            "%(n)s %(que)s no cumplen: %(ids)s%(mas)s") % {
                "n": len(violan), "que": violan._description or violan._name,
                "ids": ids, "mas": _(" (y %s más)") % (len(violan) - len(ids))
                       if len(violan) > len(ids) else ""}
        texto = _("Consulta: %(t)s\nAlcance: %(a)s (%(p)s proyectos)\nResultado: "
                  "%(n)s incumplimientos") % {
            "t": pc["titulo"], "a": ctx["alcance"], "p": len(ctx["proyectos"]), "n": len(violan)}
        return {"label": "%s — %s" % (pc["id"], pc["titulo"]), "text": texto,
                "check": {"id": pc["id"], "titulo": pc["titulo"], "ok": ok,
                          "count": len(violan), "ids": ids, "detalle": detalle,
                          "fix": pc["fix"], "error": ""}}

    # ==================================================================
    #  API
    # ==================================================================
    @api.model
    def verificar(self, run):
        """Corre la verificación de una corrida y devuelve el resultado de sagui.verification."""
        rol = self.env["sagui.role"].get("project_manager", required=False)
        return self.env["sagui.verification"].run(
            target={"run_id": run.id},
            rubric_key=(rol.verifier_rubric_id.key if rol and rol.verifier_rubric_id
                        else RUBRICA_KEY),
            reviewer={"provider": "rules"},
            collector={"model": self._name, "method": "_collect_evidence"},
            res_model="sagui.pm.run", res_id=run.id,
            name=_("Post-condiciones de %s") % run.name,
        )


class SaguiPmRunVerification(models.Model):
    _inherit = "sagui.pm.run"

    verification_id = fields.Many2one(
        "sagui.verification", string="Última verificación", ondelete="set null", copy=False)
    verificacion_estado = fields.Selection(
        related="verification_id.verdict", string="Post-condiciones")

    def verificar(self):
        """Corre las post-condiciones y guarda el resultado en la corrida."""
        self.ensure_one()
        resultado = self.env["primate.sagui.pm.verifier"].verificar(self)
        if resultado.get("verification_id"):
            self.verification_id = resultado["verification_id"]
        return resultado

    def action_verificar(self):
        self.verificar()
        return {
            "type": "ir.actions.act_window",
            "res_model": "sagui.verification",
            "res_id": self.verification_id.id,
            "views": [(False, "form")],
            "view_mode": "form",
            "target": "new",
        }

    def aprobar_bloque(self, regla=None, project_id=None):
        """Aplica el bloque y VERIFICA. Aplicar sin verificar deja el estado sin comprobar, que es
        justo lo que el Check/Verify existe para evitar."""
        resultado = super().aprobar_bloque(regla=regla, project_id=project_id)
        if resultado.get("aplicadas"):
            verificacion = self.verificar()
            resultado["verificacion"] = {
                "verdict": verificacion.get("verdict"),
                "infra": verificacion.get("infra", False),
                "findings": verificacion.get("findings") or [],
            }
        return resultado
