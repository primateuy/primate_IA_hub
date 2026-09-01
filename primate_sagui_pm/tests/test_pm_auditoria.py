# -*- coding: utf-8 -*-
"""La auditoría de punta a punta, SIN llamar al modelo.

Se invoca `_pm_auditar` directamente con el ctx de la receta: lo que hay que probar es el gate y
la aprobación por bloque, no que el modelo sepa llamar a una tool. Un test que pegue contra la
API sería lento, caro y no determinístico, y no probaría nada que no pruebe éste.
"""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from .test_pm_reglas import PmCommon


# Hereda de PmCommon, que ya es post_install (ver la nota allá): igual se marca explícito para
# que no dependa de la herencia si alguien reordena las clases.
@tagged("post_install", "-at_install")
class TestAuditoriaPuntaAPunta(PmCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.receta = cls.env.ref("primate_sagui_pm.recipe_auditoria_proyectos")

    def _correr(self, **args):
        ctx = self.receta._build_run_ctx()
        ctx["review_items"] = []
        args.setdefault("alcance", "todo")
        informe = self.receta._pm_auditar(self.env, args, ctx)
        return ctx["pm_run"], informe

    def _armar_escenario(self):
        """Dos proyectos de áreas distintas, cada uno con una tarea sin etapa."""
        self.tecnico = self._proyecto("Proyecto técnico", self.tecnica)
        self.func = self._proyecto("Proyecto funcional", self.funcional)
        for proyecto in (self.tecnico, self.func):
            self.env["project.task.type"].create(
                {"name": "Nueva", "sequence": 1, "project_ids": [(6, 0, [proyecto.id])]})
        self.t_tecnica = self._tarea(self.tecnico, "Sin etapa técnica")
        self.t_funcional = self._tarea(self.func, "Sin etapa funcional")
        (self.t_tecnica | self.t_funcional).write({"stage_id": False})

    # ------------------------------------------------------------------ el gate
    def test_no_escribe_nada_hasta_que_alguien_apruebe(self):
        self._armar_escenario()
        etapa_antes = self.t_tecnica.stage_id

        run, informe = self._correr()

        self.assertTrue(run.propuesta_count, "tiene que haber propuestas")
        self.assertEqual(self.t_tecnica.stage_id, etapa_antes, "no se escribió nada")
        pendientes = run.finding_ids.mapped("pending_write_id")
        self.assertTrue(pendientes)
        self.assertEqual(set(pendientes.mapped("state")), {"pending"})
        self.assertIn("AUDITORÍA", informe)

    def test_cada_propuesta_queda_marcada_con_su_regla_y_su_proyecto(self):
        """Los campos pm_* son lo que hace posible aprobar por bloque."""
        self._armar_escenario()
        run, _informe = self._correr()

        propuesta = run.finding_ids.filtered(
            lambda f: f.regla == "tarea_sin_etapa" and f.res_id == self.t_tecnica.id
        ).pending_write_id
        self.assertTrue(propuesta)
        self.assertEqual(propuesta.pm_run_id, run)
        self.assertEqual(propuesta.pm_rule_key, "tarea_sin_etapa")
        self.assertEqual(propuesta.pm_project_id, self.tecnico)
        self.assertEqual(propuesta.pm_area_id, self.tecnica)

    # ------------------------------------------------------------------ bloques
    def test_aprobar_un_bloque_aplica_solo_ese_bloque(self):
        self._armar_escenario()
        proyecto_sin_area = self._proyecto("Sin área")
        self._tarea(proyecto_sin_area, "Marca el área", area_id=self.tecnica.id)

        run, _informe = self._correr()
        self.assertTrue(run.finding_ids.filtered(lambda f: f.regla == "proyecto_sin_area"))

        resultado = run.aprobar_bloque(regla="tarea_sin_etapa")

        self.assertTrue(resultado["aplicadas"])
        self.assertFalse(resultado["fallidas"], resultado["errores"])
        self.assertTrue(self.t_tecnica.stage_id, "la regla aprobada se aplicó")
        self.assertFalse(proyecto_sin_area.area_id, "la regla NO aprobada no se tocó")
        otras = run.finding_ids.filtered(lambda f: f.regla == "proyecto_sin_area")
        self.assertEqual(set(otras.mapped("pending_write_id.state")), {"pending"})

    def test_aprobar_por_proyecto_no_toca_los_otros_proyectos(self):
        self._armar_escenario()
        run, _informe = self._correr()

        run.aprobar_bloque(project_id=self.tecnico.id)

        self.assertTrue(self.t_tecnica.stage_id)
        self.assertFalse(self.t_funcional.stage_id, "el otro proyecto quedó intacto")

    def test_lo_aplicado_queda_en_el_audit_log(self):
        self._armar_escenario()
        run, _informe = self._correr()
        antes = self.env["sagui.task.action.log"].search_count([("recipe_id", "=", self.receta.id)])

        run.aprobar_bloque(regla="tarea_sin_etapa")

        despues = self.env["sagui.task.action.log"].search_count([("recipe_id", "=", self.receta.id)])
        self.assertGreater(despues, antes, "cada escritura aplicada deja su registro")

    def test_rechazar_un_bloque_no_aplica_nada(self):
        self._armar_escenario()
        run, _informe = self._correr()

        run.rechazar_bloque(regla="tarea_sin_etapa")

        self.assertFalse(self.t_tecnica.stage_id)
        estados = run.finding_ids.filtered(
            lambda f: f.regla == "tarea_sin_etapa").mapped("pending_write_id.state")
        self.assertEqual(set(estados), {"cancelled"})

    # ------------------------------------------------------------------ alcance
    def test_con_alcance_de_area_ninguna_propuesta_es_de_otra_area(self):
        """Criterio de aceptación del encargo, verificado sobre las propuestas reales."""
        self._armar_escenario()

        run, _informe = self._correr(alcance="area", area_code="technical")

        proyectos = run.finding_ids.mapped("project_id")
        self.assertIn(self.tecnico, proyectos)
        self.assertNotIn(self.func, proyectos)
        areas = run.finding_ids.mapped("pending_write_id.pm_area_id")
        self.assertNotIn(self.funcional, areas)

    def test_la_corrida_cuenta_por_regla(self):
        self._armar_escenario()
        run, _informe = self._correr()
        por_regla = run.por_regla()
        self.assertIn("tarea_sin_etapa", por_regla)
        self.assertEqual(
            por_regla["tarea_sin_etapa"]["propuesta"],
            len(run.finding_ids.filtered(lambda f: f.regla == "tarea_sin_etapa")))

    def test_el_resultado_sale_del_estado_real_de_las_propuestas(self):
        self._armar_escenario()
        run, _informe = self._correr()
        self.assertEqual(run.aprobadas_count, 0)

        run.aprobar_bloque(regla="tarea_sin_etapa")

        self.assertEqual(run.aprobadas_count,
                         len(run.finding_ids.filtered(lambda f: f.regla == "tarea_sin_etapa")))
        self.assertEqual(run.rechazadas_count, 0)


@tagged("post_install", "-at_install")
class TestRolYReceta(TransactionCase):
    def test_el_rol_carga_su_playbook_desde_el_archivo(self):
        """Si la ruta del .md está mal, la skill devuelve "" y el prompt degrada EN SILENCIO."""
        rol = self.env["sagui.role"].get("project_manager")
        skill = self.env.ref("primate_sagui_pm.skill_project_management")
        contenido = skill.content()
        self.assertTrue(contenido.strip(), "la skill tiene que resolver a contenido real")
        self.assertIn("tarea_area_desalineada", contenido)
        self.assertIn(contenido[:200], rol.prompt())

    def test_la_receta_arranca_en_modo_propose_y_sin_scope_de_auto(self):
        receta = self.env.ref("primate_sagui_pm.recipe_auditoria_proyectos")
        self.assertEqual(receta.mode, "propose")
        self.assertFalse(receta.allowed_models, "scope vacío = nada se auto-aplica")
        self.assertFalse(receta.allow_unlink)

    def test_la_tool_de_auditoria_solo_se_ofrece_en_modo_escritura(self):
        receta = self.env.ref("primate_sagui_pm.recipe_auditoria_proyectos")
        nombres = [s["name"] for s in receta._task_specs(self.env, write_mode=True)]
        self.assertIn("auditar_proyectos", nombres)
        nombres = [s["name"] for s in receta._task_specs(self.env, write_mode=False)]
        self.assertNotIn("auditar_proyectos", nombres)
