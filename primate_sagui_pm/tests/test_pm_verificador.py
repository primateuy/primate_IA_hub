# -*- coding: utf-8 -*-
"""El verificador de post-condiciones: el segundo caso del Check/Verify.

Lo que se prueba de cada post-condición son los dos lados -detecta la violación y NO reprueba
cuando está todo bien-, más las dos cosas que lo distinguen del verificador visual: que no gasta
un solo token, y que un fallo de la CONSULTA no se reporta como fallo de gestión.

El caso que pedía el encargo tiene su test propio: una tarea sin responsable creada A MANO después
de la corrida también reprueba. La post-condición es sobre el estado de la base, no sobre lo que
hizo Sagui.
"""

import json
from datetime import timedelta

from odoo import fields
from odoo.tests import tagged

from .test_pm_reglas import PmCommon


@tagged("post_install", "-at_install")
class VerificadorCommon(PmCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tipo_actividad = cls.env.ref("mail.mail_activity_data_todo")
        cls.modelo_proyecto = cls.env["ir.model"]._get("project.project")

    def _corrida(self, **valores):
        base = {"origen": "receta", "alcance": "todo", "dias": 7}
        base.update(valores)
        return self.env["sagui.pm.run"].create(base)

    def _sana(self, proyecto, nombre="Sana"):
        """Tarea que cumple todas las post-condiciones."""
        etapa = self.env["project.task.type"].search(
            [("project_ids", "in", proyecto.id)], limit=1)
        if not etapa:
            etapa = self.env["project.task.type"].create(
                {"name": "Nueva", "sequence": 1, "project_ids": [(6, 0, [proyecto.id])]})
        return self._tarea(proyecto, nombre, stage_id=etapa.id,
                           user_ids=[(6, 0, [self.ana.id])])

    def _checks(self, resultado):
        """{id de post-condición: pass/fail} desde los bloques del veredicto."""
        return resultado.get("blocks") or {}


class TestPostCondiciones(VerificadorCommon):
    def test_todo_en_orden_da_pass(self):
        proyecto = self._proyecto("Impecable", self.tecnica)
        proyecto.task_ids.unlink()
        self._sana(proyecto)
        run = self._corrida(alcance="proyecto", project_id=proyecto.id)

        resultado = run.verificar()

        self.assertEqual(resultado["verdict"], "pass", resultado.get("findings"))
        self.assertEqual(set(self._checks(resultado).values()), {"pass"})

    def test_C1_detecta_una_tarea_sin_responsable(self):
        proyecto = self._proyecto("Sin responsable", self.tecnica)
        proyecto.task_ids.unlink()
        tarea = self._sana(proyecto)
        tarea.user_ids = [(5, 0, 0)]
        run = self._corrida(alcance="proyecto", project_id=proyecto.id)

        resultado = run.verificar()

        self.assertEqual(resultado["verdict"], "fail")
        self.assertEqual(self._checks(resultado)["C1"], "fail")
        hallazgo = [f for f in resultado["findings"] if f["rubric"] == "C1"][0]
        self.assertIn(str(tarea.id), hallazgo["seen"])
        self.assertTrue(hallazgo["fix"])

    def test_C2_detecta_una_tarea_sin_etapa(self):
        proyecto = self._proyecto("Sin etapa", self.tecnica)
        proyecto.task_ids.unlink()
        tarea = self._sana(proyecto)
        tarea.stage_id = False
        run = self._corrida(alcance="proyecto", project_id=proyecto.id)

        self.assertEqual(self._checks(run.verificar())["C2"], "fail")

    def test_C3_detecta_una_tarea_en_un_proyecto_sin_area(self):
        proyecto = self._proyecto("Sin área")
        proyecto.task_ids.unlink()
        self._sana(proyecto)
        run = self._corrida(alcance="proyecto", project_id=proyecto.id)

        self.assertEqual(self._checks(run.verificar())["C3"], "fail")

    def test_C4_detecta_una_actividad_vencida_sin_nota(self):
        proyecto = self._proyecto("Con vencida", self.tecnica)
        proyecto.task_ids.unlink()
        self._sana(proyecto)
        self.env["mail.activity"].create({
            "res_model_id": self.modelo_proyecto.id, "res_id": proyecto.id,
            "activity_type_id": self.tipo_actividad.id, "summary": "Vencida y muda",
            "user_id": self.ana.id,
            "date_deadline": fields.Date.today() - timedelta(days=30)})
        run = self._corrida(alcance="proyecto", project_id=proyecto.id)

        self.assertEqual(self._checks(run.verificar())["C4"], "fail")

    def test_C4_acepta_una_actividad_vencida_CON_nota(self):
        """Alguien decidió y lo dejó escrito: eso no es un problema de higiene. Reprueba el
        silencio, no el vencimiento."""
        proyecto = self._proyecto("Con vencida explicada", self.tecnica)
        proyecto.task_ids.unlink()
        self._sana(proyecto)
        self.env["mail.activity"].create({
            "res_model_id": self.modelo_proyecto.id, "res_id": proyecto.id,
            "activity_type_id": self.tipo_actividad.id, "summary": "Vencida pero explicada",
            "note": "<p>Frenada hasta que el cliente responda el mail del 3/7.</p>",
            "user_id": self.ana.id,
            "date_deadline": fields.Date.today() - timedelta(days=30)})
        run = self._corrida(alcance="proyecto", project_id=proyecto.id)

        self.assertEqual(self._checks(run.verificar())["C4"], "pass")

    def test_el_alcance_acota_lo_que_se_verifica(self):
        """Una tarea rota en OTRO proyecto no reprueba la corrida de éste."""
        mio = self._proyecto("El mío", self.tecnica)
        ajeno = self._proyecto("El otro", self.funcional)
        mio.task_ids.unlink()
        ajeno.task_ids.unlink()
        self._sana(mio)
        rota = self._sana(ajeno)
        rota.user_ids = [(5, 0, 0)]
        run = self._corrida(alcance="proyecto", project_id=mio.id)

        self.assertEqual(run.verificar()["verdict"], "pass")


class TestDetectaCambiosPosteriores(VerificadorCommon):
    def test_una_tarea_sin_responsable_creada_A_MANO_despues_de_la_corrida_reprueba(self):
        """EL caso del encargo. La post-condición es sobre el estado de la base, no sobre lo que
        hizo Sagui: por eso lo agarra."""
        proyecto = self._proyecto("Limpio al principio", self.tecnica)
        proyecto.task_ids.unlink()
        self._sana(proyecto)
        run = self._corrida(alcance="proyecto", project_id=proyecto.id)
        self.assertEqual(run.verificar()["verdict"], "pass", "arranca en orden")

        # Alguien crea una tarea a mano, después de la corrida.
        a_mano = self._sana(proyecto, "Creada a mano")
        a_mano.user_ids = [(5, 0, 0)]

        resultado = run.verificar()

        self.assertEqual(resultado["verdict"], "fail")
        hallazgo = [f for f in resultado["findings"] if f["rubric"] == "C1"][0]
        self.assertIn(str(a_mano.id), hallazgo["seen"])


class TestInfraNoEsGestion(VerificadorCommon):
    def test_una_consulta_que_revienta_da_veredicto_de_infra(self):
        """Reportar "gestión reprobada" por un dominio roto manda a arreglar datos que están
        bien. Misma regla que la captura degradada del verificador visual."""
        proyecto = self._proyecto("Cualquiera", self.tecnica)
        run = self._corrida(alcance="proyecto", project_id=proyecto.id)
        verificador = type(self.env["primate.sagui.pm.verifier"])

        def explota(self_v, run_, ctx):
            raise ValueError("campo 'foo' inexistente")

        self.patch(verificador, "_violan_c1", explota)

        resultado = run.verificar()

        self.assertEqual(resultado["verdict"], "error")
        self.assertTrue(resultado["infra"], "es infra, no gestión reprobada")
        self.assertFalse(resultado["findings"])

    def test_no_gasta_un_solo_token(self):
        proyecto = self._proyecto("Sin tokens", self.tecnica)
        proyecto.task_ids.unlink()
        self._sana(proyecto)
        run = self._corrida(alcance="proyecto", project_id=proyecto.id)
        llamadas = []
        self.patch(type(self.env["primate.ai.connector"]), "call",
                   lambda s, *a, **k: llamadas.append(1) or {})

        run.verificar()

        self.assertFalse(llamadas, "las post-condiciones son consultas, no una opinión")


class TestVerificaTrasAplicar(VerificadorCommon):
    def test_aprobar_un_bloque_corre_la_verificacion(self):
        """Aplicar sin verificar deja el estado sin comprobar, que es lo que el Check/Verify
        existe para evitar."""
        receta = self.env.ref("primate_sagui_pm.recipe_auditoria_proyectos")
        proyecto = self._proyecto("Para auditar", self.tecnica)
        proyecto.task_ids.unlink()
        self.env["project.task.type"].create(
            {"name": "Nueva", "sequence": 1, "project_ids": [(6, 0, [proyecto.id])]})
        tarea = self._tarea(proyecto, "Sin etapa", user_ids=[(6, 0, [self.ana.id])])
        tarea.stage_id = False

        ctx = receta._build_run_ctx()
        ctx["review_items"] = []
        receta._pm_auditar(self.env, {"alcance": "proyecto", "project_id": proyecto.id}, ctx)
        run = ctx["pm_run"]

        resultado = run.aprobar_bloque(regla="tarea_sin_etapa")

        self.assertTrue(resultado["aplicadas"])
        self.assertIn("verificacion", resultado, "aprobar un bloque verifica")
        self.assertTrue(run.verification_id, "la corrida guarda su verificación")
        # La etapa que se acaba de aplicar ya no puede violar C2.
        bloques = json.loads(run.verification_id.blocks_json or "{}")
        self.assertEqual(bloques.get("C2"), "pass")
        self.assertTrue(tarea.stage_id, "y la tarea quedó efectivamente con etapa")

    def test_rechazar_un_bloque_no_dispara_verificacion(self):
        """No se aplicó nada: no hay estado nuevo que comprobar ni razón para gastar consultas."""
        receta = self.env.ref("primate_sagui_pm.recipe_auditoria_proyectos")
        proyecto = self._proyecto("Para rechazar", self.tecnica)
        proyecto.task_ids.unlink()
        self.env["project.task.type"].create(
            {"name": "Nueva", "sequence": 1, "project_ids": [(6, 0, [proyecto.id])]})
        tarea = self._tarea(proyecto, "Sin etapa", user_ids=[(6, 0, [self.ana.id])])
        tarea.stage_id = False
        ctx = receta._build_run_ctx()
        ctx["review_items"] = []
        receta._pm_auditar(self.env, {"alcance": "proyecto", "project_id": proyecto.id}, ctx)
        run = ctx["pm_run"]

        run.rechazar_bloque(regla="tarea_sin_etapa")

        self.assertFalse(run.verification_id)


class TestRubrica(VerificadorCommon):
    def test_la_rubrica_resuelve_a_contenido_real(self):
        """Una ruta mal escrita degradaría en silencio: la skill devolvería "" y el verificador
        correría sin rúbrica."""
        skill = self.env.ref("primate_sagui_pm.skill_pm_verifier_rubric")
        contenido = skill.content()
        self.assertTrue(contenido.strip())
        self.assertEqual(skill.kind, "checklist")
        for ident in ("C1", "C2", "C3", "C4", "C5"):
            self.assertIn("**%s**" % ident, contenido)

    def test_el_rol_declara_el_revisor_deterministico(self):
        rol = self.env["sagui.role"].get("project_manager")
        self.assertTrue(rol.verifier_active)
        self.assertEqual(rol.reviewer_provider, "rules")
        self.assertEqual(rol.verifier_rubric_id.key, "pm-verifier-rubric")
