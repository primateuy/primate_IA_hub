# -*- coding: utf-8 -*-
# MODO b) GESTIÓN CONTINUA: la misma auditoría, desatendida y sobre lo que cambió.
#
# Tres diferencias con la receta, y ninguna es el playbook:
#   1. El alcance temporal: sólo lo que se movió desde la corrida anterior.
#   2. El ruteo: sin humano presente, las propuestas van a la bandeja desatendida
#      (sagui.automation.proposal), que es la que ya existe para eso.
#   3. El cierre: aviso agrupado POR PERSONA, notas de gestión y verificación.
#
# UN AVISO POR PERSONA, NO UNO POR ÍTEM. Una automatización diaria que manda un mensaje por
# hallazgo deja de leerse en tres días, y a partir de ahí la bandeja crece sola sin que nadie la
# mire. El agrupador es `destinatario_id`, que el motor calcula por hallazgo.
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

MAX_LINEAS_POR_AVISO = 15


class SaguiAutomationPm(models.Model):
    _inherit = "sagui.automation"

    def _build_run_ctx(self, dry_run=False):
        """La corrida continua mira sólo lo que cambió desde la anterior.

        La primera vez `last_run` está vacío y audita todo, que es lo correcto: hay que partir de
        una foto completa antes de poder mirar diferencias.
        """
        ctx = super()._build_run_ctx(dry_run=dry_run)
        ctx["pm_desde"] = self.last_run or False
        return ctx


class SaguiExecutableTaskPmContinuo(models.AbstractModel):
    _inherit = "sagui.executable.task"

    def _pm_cierre(self, env, run, ctx):
        """Lo que pasa al terminar una auditoría. Devuelve un resumen para el informe."""
        cierre = {}
        if self._is_unattended():
            cierre["avisos"] = self._pm_avisar_por_persona(run)
        cierre["notas"] = env["primate.sagui.pm.note"].generar(run)
        # La verificación va SIEMPRE al cierre: es el estado en que quedó la base, y da igual si
        # lo cambió esta corrida o alguien a mano.
        verificacion = run.verificar()
        cierre["verificacion"] = {"verdict": verificacion.get("verdict"),
                                  "infra": verificacion.get("infra", False)}
        return cierre

    def _pm_avisar_por_persona(self, run):
        """Un mensaje por persona con TODOS sus hallazgos. Devuelve cuántos avisos salieron."""
        por_persona = {}
        for hallazgo in run.finding_ids:
            if hallazgo.tipo == "nota" or not hallazgo.destinatario_id:
                continue
            por_persona.setdefault(hallazgo.destinatario_id, []).append(hallazgo)

        enviados = 0
        for persona, hallazgos in por_persona.items():
            self._notify(persona, self._pm_texto_aviso(run, persona, hallazgos))
            enviados += 1
        # Los hallazgos sin destinatario no se pierden en silencio: van al dueño de la tarea.
        huerfanos = run.finding_ids.filtered(
            lambda f: f.tipo != "nota" and not f.destinatario_id)
        if huerfanos:
            self._notify(self.user_id, _(
                "%(n)s hallazgos sin responsable identificable (nadie asignado en el registro, ni "
                "en el proyecto, ni en el área). Están en la corrida «%(r)s».") % {
                    "n": len(huerfanos), "r": run.name})
            enviados += 1
        return enviados

    def _pm_texto_aviso(self, run, persona, hallazgos):
        propuestas = [h for h in hallazgos if h.tipo == "propuesta"]
        preguntas = [h for h in hallazgos if h.tipo == "pregunta"]
        lineas = [_("Revisión de proyectos — %(n)s cosas para vos.") % {"n": len(hallazgos)}]
        if propuestas:
            lineas.append("")
            lineas.append(_("Para aprobar o rechazar (%s):") % len(propuestas))
            for hallazgo in propuestas[:MAX_LINEAS_POR_AVISO]:
                lineas.append("  → %s" % (hallazgo.resumen or "").strip())
        if preguntas:
            lineas.append("")
            lineas.append(_("Para que decidas vos (%s):") % len(preguntas))
            for hallazgo in preguntas[:MAX_LINEAS_POR_AVISO]:
                lineas.append("  ? %s" % (hallazgo.resumen or "").strip())
        restantes = len(hallazgos) - min(len(propuestas), MAX_LINEAS_POR_AVISO) \
            - min(len(preguntas), MAX_LINEAS_POR_AVISO)
        if restantes > 0:
            lineas.append("")
            lineas.append(_("(y %s más en la corrida «%s»)") % (restantes, run.name))
        return "\n".join(lineas)


class PrimateDashboardExtension(models.AbstractModel):
    """Contrato con el dashboard ejecutivo, por LOOKUP EN RUNTIME y no por herencia.

    Por qué no es un override de `get_dashboard_data`, que era lo que decía el diseño: no funciona.
    El dashboard define ese método SIN llamar a super() -es la definición base- y carga DESPUÉS que
    este módulo (91/92 contra 80/92: depende de sale_timesheet, que va tarde en el grafo). Su
    definición queda más arriba en el MRO y tapa cualquier herencia que venga de un módulo que
    cargue antes. Y el orden lo decide el grafo de dependencias, no algo que se pueda forzar desde
    acá sin hacer del dashboard una dependencia dura -que es justo lo que no queremos-.

    Entonces el dashboard busca este modelo por nombre al final de su RPC, exactamente como ya hace
    con `planning.slot`: `if "primate.project.dashboard.extension" in self.env`. Un lookup en
    runtime no depende del orden de carga. Si este módulo no está, no pasa nada.
    """
    _name = "primate.project.dashboard.extension"
    _description = "Extensión del payload del dashboard ejecutivo"

    @api.model
    def extend(self, payload, options=None):
        """Agrega la nota de gestión a cada fila de proyecto y a cada tarjeta de área."""
        proyectos = {
            p.id: p for p in self.env["project.project"].browse(
                [fila["id"] for fila in payload.get("projects") or []]).exists()
        }
        for fila in payload.get("projects") or []:
            proyecto = proyectos.get(fila["id"])
            fila["pm_note"] = (proyecto.pm_note or None) if proyecto else None
            fila["pm_note_date"] = (
                fields.Datetime.to_string(proyecto.pm_note_date)
                if proyecto and proyecto.pm_note_date else None)
        areas = {a.code: a for a in self.env["primate.area"].search([])}
        for tarjeta in payload.get("areas") or []:
            area = areas.get(tarjeta.get("area"))
            tarjeta["pm_note"] = (area.pm_note or None) if area else None
            tarjeta["pm_note_date"] = (
                fields.Datetime.to_string(area.pm_note_date)
                if area and area.pm_note_date else None)
        return payload
