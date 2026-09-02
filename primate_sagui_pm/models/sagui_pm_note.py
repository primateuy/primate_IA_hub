# -*- coding: utf-8 -*-
# NOTA DE GESTIÓN: el párrafo que un dashboard no puede dar.
#
# El dashboard dice QUÉ pasa (semáforo, avance, alertas). La nota dice QUÉ SIGNIFICA y QUÉ FALTA
# DECIDIR. Es lo único de este rol donde el modelo redacta libremente, y por eso es donde más
# fácil se inventa algo.
#
# "PROHIBIDO INVENTAR" ACÁ ES MECÁNICO, NO UNA INSTRUCCIÓN.
# El contexto se arma con `_dato()`: cada métrica entra SIEMPRE, y si no está disponible entra
# como None con su etiqueta. El modelo nunca recibe un contexto donde el dato faltante esté
# ausente -que es lo que lo invita a rellenarlo-, sino uno donde está explícitamente vacío. Y el
# system prompt le exige nombrarlo. Un dato que falta es información: "este proyecto no tiene
# plan" es exactamente lo que leadership necesita leer.
import json
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)

NOTE_MAX_TOKENS = 700
NOTE_TIMEOUT = 90

SYSTEM_NOTA = """\
Sos el Gestor de Proyectos de Sagui escribiendo la nota de gestión que leen la dirección y el
responsable del área. UN párrafo, 4 a 6 oraciones, en español rioplatense.

Cubrí, en este orden y sólo si hay material: en qué estado está, qué riesgo es el que importa,
qué decisión está esperando a una persona, y qué hizo Sagui en esta corrida.

REGLA DURA: en el contexto, un dato en `null` significa QUE NO LO TENÉS. No lo estimes, no lo
infieras de otro, no lo omitas: DECÍ QUE FALTA, con su nombre. "No tiene plan cargado, así que no
hay desvío que medir" es una oración correcta y útil. Inventar un porcentaje es la peor cosa que
podés hacer acá, porque nadie va a poder distinguirlo de uno real.

No repitas los números tal cual: interpretalos. No saludes, no cierres con una despedida, no uses
viñetas ni títulos. Devolvé SOLO el párrafo.
"""


class SaguiPmNote(models.AbstractModel):
    _name = "primate.sagui.pm.note"
    _description = "Nota de gestión del Gestor de Proyectos"

    # ==================================================================
    #  Contexto: SÓLO datos leídos, y los que faltan van como None
    # ==================================================================
    @api.model
    def _dato(self, contexto, etiqueta, valor):
        """Mete la métrica SIEMPRE, con su etiqueta. Ausente y vacío no son lo mismo: un dato que
        no está tiene que llegarle al modelo como null, no desaparecer del diccionario."""
        contexto[etiqueta] = valor
        return contexto

    @api.model
    def _contexto_proyecto(self, run, proyecto):
        hallazgos = run.finding_ids.filtered(lambda f: f.project_id == proyecto)
        contexto = {}
        self._dato(contexto, "proyecto", proyecto.display_name)
        self._dato(contexto, "area", proyecto.area_id.display_name or None)
        self._dato(contexto, "responsable", proyecto.user_id.display_name or None)
        self._dato(contexto, "cliente", proyecto.partner_id.display_name or None)
        self._dato(contexto, "fecha_inicio", str(proyecto.date_start or "") or None)
        self._dato(contexto, "fecha_fin", str(proyecto.date or "") or None)
        self._dato(contexto, "tareas_abiertas", self._tareas_abiertas(proyecto))
        self._dato(contexto, "hallazgos_de_esta_corrida", self._resumen_hallazgos(hallazgos))
        # Métricas del dashboard: SÓLO si está instalado. Sin él van en null, y el modelo lo dice.
        self._dato(contexto, "metricas_del_dashboard", self._metricas(proyecto))
        return contexto

    @api.model
    def _tareas_abiertas(self, proyecto):
        from .sagui_pm_engine import ESTADOS_CERRADOS
        return self.env["project.task"].search_count([
            ("project_id", "=", proyecto.id), ("state", "not in", list(ESTADOS_CERRADOS))])

    @api.model
    def _resumen_hallazgos(self, hallazgos):
        """Qué encontró la corrida, agrupado por regla. Vacío = None, no {}."""
        if not hallazgos:
            return None
        salida = {}
        for hallazgo in hallazgos:
            fila = salida.setdefault(hallazgo.regla, {"propuesta": 0, "pregunta": 0, "nota": 0})
            fila[hallazgo.tipo] += 1
        return salida

    @api.model
    def _metricas(self, proyecto):
        """Semáforo y avance del dashboard ejecutivo. None si el módulo no está instalado.

        Se detecta en RUNTIME, nunca en el manifest: este módulo tiene que correr sin el dashboard,
        y sin él la nota simplemente dice que no tiene esos datos.
        """
        Project = self.env["project.project"]
        if not hasattr(Project, "_primate_health_metrics"):
            return None
        try:
            from odoo.addons.primate_project_dashboard.models import dashboard_params
            params = dashboard_params.get_params(self.env)
            valores = proyecto._primate_health_metrics(
                params, fields.Date.context_today(self)).get(proyecto.id) or {}
        except Exception as e:  # noqa: BLE001
            _logger.warning("Sagui PM: no pude leer las métricas del dashboard (%s)", e)
            return None
        return {
            "semaforo": valores.get("health_state"),
            "avance_real": valores.get("progress_real"),
            "avance_planificado": valores.get("progress_planned"),
            "tiene_plan": valores.get("has_plan"),
            "dias_de_desvio": valores.get("deviation_days"),
        }

    @api.model
    def _contexto_area(self, run, area):
        """El agregado del área, para leadership. Mismo criterio: lo que falta va en null."""
        proyectos = self.env["project.project"].search(
            [("area_id", "=", area.id), ("active", "=", True)])
        hallazgos = run.finding_ids.filtered(lambda f: f.area_id == area)
        contexto = {}
        self._dato(contexto, "area", area.display_name)
        self._dato(contexto, "responsable_del_area", area.user_id.display_name or None)
        self._dato(contexto, "proyectos_activos", len(proyectos) or None)
        self._dato(contexto, "hallazgos_de_esta_corrida", self._resumen_hallazgos(hallazgos))
        self._dato(contexto, "proyectos_sin_plan", [
            p.display_name for p in proyectos if not (p.date_start and p.date)] or None)
        semaforos = {}
        for proyecto in proyectos:
            metricas = self._metricas(proyecto)
            if metricas and metricas.get("semaforo"):
                semaforos[metricas["semaforo"]] = semaforos.get(metricas["semaforo"], 0) + 1
        self._dato(contexto, "semaforos_del_dashboard", semaforos or None)
        return contexto

    # ==================================================================
    #  Generación
    # ==================================================================
    @api.model
    def _redactar(self, contexto):
        """Una request aislada. Devuelve el párrafo, o "" si no se pudo (y entonces no se pisa
        la nota anterior: una nota vieja con su fecha es más útil que ninguna)."""
        try:
            respuesta = self.env["primate.ai.connector"].call(
                [{"role": "user", "content": json.dumps(contexto, ensure_ascii=False,
                                                        default=str, indent=2)}],
                system=SYSTEM_NOTA, max_tokens=NOTE_MAX_TOKENS, timeout=NOTE_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            _logger.warning("Sagui PM: no pude redactar la nota de gestión (%s)", e)
            return ""
        texto = ""
        for bloque in (respuesta or {}).get("content") or []:
            if bloque.get("type") == "text":
                texto += bloque.get("text") or ""
        return texto.strip()

    @api.model
    def generar(self, run):
        """Regenera las notas de la corrida. Devuelve {proyectos: n, areas: n}.

        Sólo se regeneran los proyectos CON hallazgos en esta corrida: un proyecto del que no hay
        nada nuevo que decir no necesita un párrafo nuevo, y su nota conserva su fecha, que es lo
        que deja ver que está vieja. Las áreas del alcance se regeneran siempre: el agregado
        cambia aunque un proyecto puntual no.
        """
        proyectos = run.finding_ids.mapped("project_id").filtered(lambda p: p.active)
        areas = run.finding_ids.mapped("area_id") or run.area_id
        hechos = {"proyectos": 0, "areas": 0}
        ahora = fields.Datetime.now()
        for proyecto in proyectos:
            texto = self._redactar(self._contexto_proyecto(run, proyecto))
            if texto:
                proyecto.write({"pm_note": texto, "pm_note_date": ahora})
                hechos["proyectos"] += 1
        for area in areas:
            texto = self._redactar(self._contexto_area(run, area))
            if texto:
                area.write({"pm_note": texto, "pm_note_date": ahora})
                hechos["areas"] += 1
        return hechos


class ProjectProjectNote(models.Model):
    _inherit = "project.project"

    pm_note = fields.Text(
        string="Nota de gestión", copy=False, readonly=True,
        help="Párrafo regenerado por el Gestor de Proyectos de Sagui en cada corrida.")
    pm_note_date = fields.Datetime(string="Nota actualizada", copy=False, readonly=True)


class PrimateAreaNote(models.Model):
    _inherit = "primate.area"

    pm_note = fields.Text(
        string="Nota de gestión", copy=False, readonly=True,
        help="Párrafo agregado del área, regenerado por el Gestor de Proyectos de Sagui.")
    pm_note_date = fields.Datetime(string="Nota actualizada", copy=False, readonly=True)
