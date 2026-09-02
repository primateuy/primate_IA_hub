# -*- coding: utf-8 -*-
"""Modo b) -gestión continua- y modo c) -nota de gestión-.

Sin pegarle a la API: la redacción de la nota y el juicio del bloque C se reemplazan por dobles.
Lo que se prueba es el ruteo a la bandeja desatendida, el agrupado del aviso POR PERSONA, y que la
regla "prohibido inventar" de la nota sea mecánica y no una promesa del prompt.
"""

import json

from odoo import fields
from odoo.tests import tagged

from .test_pm_reglas import PmCommon


@tagged("post_install", "-at_install")
class ContinuoCommon(PmCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.automatizacion = cls.env.ref("primate_sagui_pm.automation_gestion_continua")

    def _sin_redaccion(self, texto="Nota de prueba."):
        """Doble de la redacción: registra los contextos que se le mandaron al modelo."""
        contextos = []
        nota = type(self.env["primate.sagui.pm.note"])

        def doble(self_n, contexto):
            contextos.append(contexto)
            return texto

        self.patch(nota, "_redactar", doble)
        return contextos

    def _sin_juicio(self):
        motor = type(self.env["primate.sagui.pm.engine"])
        self.patch(motor, "_consultar_al_modelo",
                   lambda s, payload: {"duplicados": [], "vinculos": [], "agrupables": []})

    def _correr_desatendida(self, proyecto):
        ctx = self.automatizacion._build_run_ctx()
        self.automatizacion._pm_auditar(
            self.env, {"alcance": "proyecto", "project_id": proyecto.id}, ctx)
        return ctx["pm_run"]

    def _escenario(self, nombre="Continuo"):
        proyecto = self._proyecto(nombre, self.tecnica, user_id=self.ana.id)
        proyecto.task_ids.unlink()
        self.env["project.task.type"].create(
            {"name": "Nueva", "sequence": 1, "project_ids": [(6, 0, [proyecto.id])]})
        tarea = self._tarea(proyecto, "Sin etapa", user_ids=[(6, 0, [self.ana.id])])
        tarea.stage_id = False
        return proyecto, tarea


class TestRuteoDesatendido(ContinuoCommon):
    def test_las_propuestas_van_a_la_bandeja_desatendida(self):
        """Sin humano presente NO se usa pending.write: se usa la bandeja que ya existe."""
        self._sin_juicio(); self._sin_redaccion()
        proyecto, _tarea = self._escenario()

        run = self._correr_desatendida(proyecto)

        self.assertEqual(run.origen, "automatizacion")
        self.assertEqual(run.automation_id, self.automatizacion)
        propuestas = self.env["sagui.automation.proposal"].search(
            [("automation_id", "=", self.automatizacion.id), ("pm_run_id", "=", run.id)])
        self.assertTrue(propuestas)
        self.assertFalse(run.finding_ids.mapped("pending_write_id"),
                         "no se crearon confirmaciones interactivas")

    def test_la_propuesta_desatendida_tambien_queda_marcada_para_el_bloque(self):
        self._sin_juicio(); self._sin_redaccion()
        proyecto, _tarea = self._escenario()

        run = self._correr_desatendida(proyecto)

        propuesta = self.env["sagui.automation.proposal"].search(
            [("pm_run_id", "=", run.id), ("pm_rule_key", "=", "tarea_sin_etapa")], limit=1)
        self.assertTrue(propuesta)
        self.assertEqual(propuesta.pm_project_id, proyecto)
        self.assertEqual(propuesta.pm_area_id, self.tecnica)

    def test_nada_se_auto_aplica(self):
        self._sin_juicio(); self._sin_redaccion()
        proyecto, tarea = self._escenario()

        self._correr_desatendida(proyecto)

        self.assertFalse(tarea.stage_id, "modo propose: no se escribió nada")
        self.assertEqual(self.automatizacion.mode, "propose")
        self.assertFalse(self.automatizacion.allowed_models)

    def test_la_automatizacion_viene_apagada(self):
        """No se prende con la instalación: se prende cuando la receta ya se rodó."""
        self.assertFalse(self.automatizacion.active)

    def test_mira_solo_lo_que_cambio_desde_la_corrida_anterior(self):
        self.automatizacion.last_run = fields.Datetime.now()
        ctx = self.automatizacion._build_run_ctx()
        self.assertEqual(ctx["pm_desde"], self.automatizacion.last_run)

    def test_la_primera_corrida_audita_todo(self):
        """Sin corrida anterior hay que partir de una foto completa."""
        self.automatizacion.last_run = False
        self.assertFalse(self.automatizacion._build_run_ctx()["pm_desde"])


class TestAvisoPorPersona(ContinuoCommon):
    def test_un_aviso_por_persona_y_no_uno_por_item(self):
        self._sin_juicio(); self._sin_redaccion()
        proyecto = self._proyecto("Varios hallazgos", self.tecnica, user_id=self.ana.id)
        proyecto.task_ids.unlink()
        self.env["project.task.type"].create(
            {"name": "Nueva", "sequence": 1, "project_ids": [(6, 0, [proyecto.id])]})
        de_ana = self._tarea(proyecto, "Una de Ana", user_ids=[(6, 0, [self.ana.id])])
        otra_de_ana = self._tarea(proyecto, "Otra de Ana", user_ids=[(6, 0, [self.ana.id])])
        de_beto = self._tarea(proyecto, "Una de Beto", user_ids=[(6, 0, [self.beto.id])])
        (de_ana | otra_de_ana | de_beto).write({"stage_id": False})

        avisados = []
        self.patch(type(self.automatizacion), "_notify",
                   lambda s, owner, text: avisados.append((owner, text)))

        run = self._correr_desatendida(proyecto)

        self.assertEqual(len(run.finding_ids.filtered(lambda f: f.regla == "tarea_sin_etapa")), 3)
        self.assertEqual(len(avisados), 2, "dos personas, dos avisos: no uno por hallazgo")
        destinatarios = {u for u, _t in avisados}
        self.assertEqual(destinatarios, {self.ana, self.beto})
        texto_de_ana = [t for u, t in avisados if u == self.ana][0]
        self.assertIn("Una de Ana", texto_de_ana)
        self.assertIn("Otra de Ana", texto_de_ana)
        self.assertNotIn("Una de Beto", texto_de_ana, "a cada uno lo suyo")

    def test_el_destinatario_cae_al_responsable_del_proyecto_y_despues_al_del_area(self):
        self.tecnica.user_id = self.beto
        sin_nadie = self._proyecto("Sin responsable", self.tecnica)
        sin_nadie.user_id = False
        sin_nadie.task_ids.unlink()
        tarea = self._tarea(sin_nadie, "De nadie")
        tarea.user_ids = [(5, 0, 0)]

        engine = self.env["primate.sagui.pm.engine"]
        self.assertEqual(engine._destinatario(tarea, sin_nadie, self.tecnica), self.beto,
                         "sin responsable de tarea ni de proyecto, manda el del área")

        sin_nadie.user_id = self.ana
        self.assertEqual(engine._destinatario(tarea, sin_nadie, self.tecnica), self.ana,
                         "el del proyecto tiene prioridad sobre el del área")

    def test_los_hallazgos_sin_destinatario_no_se_pierden(self):
        """Van al dueño de la automatización, con el conteo. Callarlos sería peor."""
        self._sin_juicio(); self._sin_redaccion()
        self.tecnica.user_id = False
        proyecto = self._proyecto("Huérfano", self.tecnica)
        proyecto.user_id = False
        proyecto.task_ids.unlink()
        self.env["project.task.type"].create(
            {"name": "Nueva", "sequence": 1, "project_ids": [(6, 0, [proyecto.id])]})
        tarea = self._tarea(proyecto, "De nadie")
        tarea.write({"stage_id": False, "user_ids": [(5, 0, 0)]})

        avisados = []
        self.patch(type(self.automatizacion), "_notify",
                   lambda s, owner, text: avisados.append((owner, text)))

        self._correr_desatendida(proyecto)

        self.assertEqual(len(avisados), 1)
        self.assertEqual(avisados[0][0], self.automatizacion.user_id)
        self.assertIn("sin responsable identificable", avisados[0][1])


class TestNotaDeGestion(ContinuoCommon):
    def test_lo_que_falta_llega_al_modelo_como_null_y_no_ausente(self):
        """La regla "prohibido inventar" es MECÁNICA: el dato faltante no desaparece del
        contexto -que es lo que invita a rellenarlo-, entra explícitamente vacío."""
        self._sin_juicio()
        contextos = self._sin_redaccion()
        proyecto = self._proyecto("Sin nada", self.tecnica, partner_id=False,
                                  date_start=False, date=False)
        proyecto.user_id = False
        proyecto.task_ids.unlink()
        tarea = self._tarea(proyecto, "Algo", user_ids=[(6, 0, [self.ana.id])])
        tarea.stage_id = False

        self._correr_desatendida(proyecto)

        del_proyecto = [c for c in contextos if c.get("proyecto") == proyecto.display_name][0]
        for clave in ("cliente", "responsable", "fecha_inicio", "fecha_fin"):
            self.assertIn(clave, del_proyecto, "la clave tiene que estar, aunque el dato no")
            self.assertIsNone(del_proyecto[clave], "y tiene que estar en null, no omitida")

    def test_la_nota_se_guarda_en_el_proyecto_y_en_el_area(self):
        self._sin_juicio()
        self._sin_redaccion("El proyecto viene bien; falta cargar el cliente.")
        proyecto, _tarea = self._escenario()

        run = self._correr_desatendida(proyecto)

        self.assertEqual(proyecto.pm_note, "El proyecto viene bien; falta cargar el cliente.")
        self.assertTrue(proyecto.pm_note_date)
        self.assertEqual(self.tecnica.pm_note, "El proyecto viene bien; falta cargar el cliente.")
        self.assertIn("Notas de gestión regeneradas", run.informe)

    def test_si_no_se_puede_redactar_no_se_pisa_la_nota_anterior(self):
        """Una nota vieja CON SU FECHA es más útil que ninguna: se ve que está vieja."""
        self._sin_juicio()
        proyecto, _tarea = self._escenario()
        proyecto.write({"pm_note": "Nota anterior", "pm_note_date": fields.Datetime.now()})
        self._sin_redaccion("")   # el modelo no contestó

        self._correr_desatendida(proyecto)

        self.assertEqual(proyecto.pm_note, "Nota anterior")

    def test_sin_dashboard_las_metricas_van_en_null_y_no_rompen(self):
        """El dashboard es opcional en runtime: sin él la nota dice que no tiene esos datos."""
        self._sin_juicio()
        contextos = self._sin_redaccion()
        proyecto, _tarea = self._escenario()
        nota = type(self.env["primate.sagui.pm.note"])
        self.patch(nota, "_metricas", lambda s, p: None)

        self._correr_desatendida(proyecto)

        del_proyecto = [c for c in contextos if c.get("proyecto") == proyecto.display_name][0]
        self.assertIn("metricas_del_dashboard", del_proyecto)
        self.assertIsNone(del_proyecto["metricas_del_dashboard"])


class TestContratoConElDashboard(ContinuoCommon):
    """El contrato es un LOOKUP EN RUNTIME, no una herencia.

    Herencia no funciona: el dashboard carga después que este módulo (91/92 contra 80/92, porque
    depende de sale_timesheet) y define `get_dashboard_data` sin llamar a super(), así que tapa
    cualquier override que venga de un módulo que cargue antes.
    """

    def test_la_extension_agrega_la_nota_a_un_payload_cualquiera(self):
        """Se prueba el contrato solo, sin depender de que el dashboard esté instalado."""
        proyecto = self._proyecto("Con nota", self.tecnica)
        proyecto.write({"pm_note": "Va bien, falta el plan.",
                        "pm_note_date": fields.Datetime.now()})
        self.tecnica.write({"pm_note": "El área está al día.",
                            "pm_note_date": fields.Datetime.now()})
        payload = {"projects": [{"id": proyecto.id}], "areas": [{"area": "technical"}]}

        salida = self.env["primate.project.dashboard.extension"].extend(payload)

        self.assertEqual(salida["projects"][0]["pm_note"], "Va bien, falta el plan.")
        self.assertTrue(salida["projects"][0]["pm_note_date"])
        self.assertEqual(salida["areas"][0]["pm_note"], "El área está al día.")

    def test_la_extension_pone_null_donde_no_hay_nota(self):
        proyecto = self._proyecto("Sin nota", self.tecnica)
        payload = {"projects": [{"id": proyecto.id}], "areas": [{"area": "technical"}]}

        salida = self.env["primate.project.dashboard.extension"].extend(payload)

        self.assertIsNone(salida["projects"][0]["pm_note"])
        self.assertIsNone(salida["projects"][0]["pm_note_date"])

    def test_la_extension_no_se_cae_con_un_proyecto_borrado(self):
        proyecto = self._proyecto("Efímero", self.tecnica)
        payload = {"projects": [{"id": proyecto.id}], "areas": []}
        proyecto.unlink()

        salida = self.env["primate.project.dashboard.extension"].extend(payload)

        self.assertIsNone(salida["projects"][0]["pm_note"])

    def test_la_nota_viaja_en_el_payload_del_dashboard(self):
        Project = self.env["project.project"]
        if not hasattr(Project, "_primate_health_metrics"):
            self.skipTest("el dashboard ejecutivo no está instalado en esta base")
        proyecto = self._proyecto("Con nota", self.tecnica, user_id=self.env.user.id)
        proyecto.write({"pm_note": "Va bien, falta el plan.",
                        "pm_note_date": fields.Datetime.now()})
        self.tecnica.write({"pm_note": "El área está al día.",
                            "pm_note_date": fields.Datetime.now()})

        datos = Project.get_dashboard_data({"period": "all"})

        fila = [f for f in datos["projects"] if f["id"] == proyecto.id]
        self.assertTrue(fila, "el proyecto tiene que estar en el payload")
        self.assertEqual(fila[0]["pm_note"], "Va bien, falta el plan.")
        self.assertTrue(fila[0]["pm_note_date"])
        tarjeta = [a for a in datos["areas"] if a["area"] == "technical"][0]
        self.assertEqual(tarjeta["pm_note"], "El área está al día.")

    def test_un_proyecto_sin_nota_viaja_con_null_y_no_con_texto_inventado(self):
        Project = self.env["project.project"]
        if not hasattr(Project, "_primate_health_metrics"):
            self.skipTest("el dashboard ejecutivo no está instalado en esta base")
        proyecto = self._proyecto("Sin nota", self.tecnica, user_id=self.env.user.id)

        datos = Project.get_dashboard_data({"period": "all"})

        fila = [f for f in datos["projects"] if f["id"] == proyecto.id][0]
        self.assertIsNone(fila["pm_note"])
        self.assertIsNone(fila["pm_note_date"])
