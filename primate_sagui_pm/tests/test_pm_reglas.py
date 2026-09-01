# -*- coding: utf-8 -*-
"""Las reglas del playbook, una por una.

Lo que se prueba de cada regla no es sólo que encuentre lo que tiene que encontrar, sino que NO
proponga cuando no puede deducir el valor. Esa es la promesa central del rol -"prohibido
inventar"- y es la que se rompe sola con el tiempo si nadie la mira.
"""

from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


# post_install, y NO es cosmético. Odoo corre los tests de cada módulo apenas lo carga, y este
# módulo no depende de sale_timesheet: en `at_install` el ORM todavía NO conoce
# `project.project.billing_type`, pero la constraint NOT NULL de ese campo YA existe en Postgres
# si sale_timesheet está instalado en la base. Resultado: el INSERT sale sin la columna y revienta
# con un not-null que no tiene nada que ver con lo que se está probando. Con post_install el
# registry está completo y el campo se puede pasar.
@tagged("post_install", "-at_install")
class PmCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tecnica = cls.env.ref("primate_project_area.area_technical")
        cls.funcional = cls.env.ref("primate_project_area.area_functional")
        cls.engine = cls.env["primate.sagui.pm.engine"]
        cls.partner = cls.env["res.partner"].create({"name": "Cliente PM"})
        # Con project.group_project_user, que es lo que tiene de verdad alguien que trabaja en
        # tareas: sin ese grupo ni siquiera pueden crear la tarea del escenario.
        grupos = [
            cls.env.ref("base.group_user").id,
            cls.env.ref("project.group_project_user").id,
        ]
        cls.ana = cls.env["res.users"].create({
            "name": "Ana", "login": "pm_ana", "email": "ana@test.com",
            "group_ids": [(6, 0, grupos)]})
        cls.beto = cls.env["res.users"].create({
            "name": "Beto", "login": "pm_beto", "email": "beto@test.com",
            "group_ids": [(6, 0, grupos)]})

    def _proyecto(self, nombre, area=None, **values):
        Project = self.env["project.project"]
        valores = {"name": nombre, "partner_id": self.partner.id,
                   "date_start": fields.Date.today(),
                   "date": fields.Date.today() + timedelta(days=30)}
        # sale_timesheet agrega `billing_type` como compute STORED y required cuyo compute NO
        # asigna nada salvo que el valor sea 'manually'. Si `allow_billable` llega sin valor
        # explícito, el recompute se dispara, deja el campo sin asignar y el INSERT revienta con
        # un not-null de Postgres -sin que el campo aparezca siquiera en el INSERT-. Es una arista
        # de sale_timesheet, no de este módulo; se sortea igual que en los tests del dashboard, y
        # sólo si el campo existe (este módulo no depende de sale_timesheet).
        # `billing_type` (sale_timesheet) es required y stored. Ver la nota de post_install
        # arriba: acá el ORM sí lo conoce, y su compute no asigna nada salvo que el valor sea
        # 'manually', así que hay que pasarlo. Se hace sólo si el campo existe porque este módulo
        # NO depende de sale_timesheet y tiene que instalar sin él.
        if "billing_type" in Project._fields:
            valores["billing_type"] = "not_billable"
        if area:
            valores["area_id"] = area.id
        valores.update(values)
        return Project.create(valores)

    def _tarea(self, proyecto, nombre="Tarea", usuario=None, **values):
        """`usuario` fija el create_uid, que es una de las señales de "quién anduvo acá".

        Sin esto la tarea la crea el superusuario, que el motor EXCLUYE a propósito de los
        candidatos a responsable: proponer a OdooBot como responsable sería peor que no proponer.
        """
        Task = self.env["project.task"]
        if usuario:
            Task = Task.with_user(usuario)
        return Task.create(
            {"name": nombre, "project_id": proyecto.id if proyecto else False, **values})

    def _hallazgos(self, regla, **kwargs):
        ctx = self.engine._resolver_alcance(**kwargs)
        return getattr(self.engine, self.engine._regla(regla)["buscar"])(ctx)

    def _de(self, hallazgos, registro):
        return [h for h in hallazgos if h["res_id"] == registro.id
                and h["modelo"] == registro._name]


class TestReglasTarea(PmCommon):
    def test_sin_etapa_propone_la_primera_del_proyecto(self):
        proyecto = self._proyecto("Con etapas", self.tecnica)
        etapa = self.env["project.task.type"].create(
            {"name": "Nueva", "sequence": 1, "project_ids": [(6, 0, [proyecto.id])]})
        self.env["project.task.type"].create(
            {"name": "Después", "sequence": 9, "project_ids": [(6, 0, [proyecto.id])]})
        tarea = self._tarea(proyecto)
        tarea.stage_id = False

        hallazgos = self._de(self._hallazgos("tarea_sin_etapa"), tarea)

        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["tipo"], "propuesta")
        self.assertEqual(hallazgos[0]["valores"], {"stage_id": etapa.id})

    def test_sin_etapa_no_propone_si_el_proyecto_no_tiene_etapas(self):
        """El hallazgo sería del proyecto, no de cada tarea: no se propone un stage_id inventado."""
        proyecto = self._proyecto("Sin etapas", self.tecnica)
        proyecto.type_ids = [(5, 0, 0)]
        tarea = self._tarea(proyecto)
        tarea.stage_id = False
        self.assertFalse(self._de(self._hallazgos("tarea_sin_etapa"), tarea))

    def test_sin_responsable_propone_cuando_hay_un_solo_candidato(self):
        proyecto = self._proyecto("Responsable", self.tecnica)
        tarea = self._tarea(proyecto, "De Ana", usuario=self.ana)
        tarea.user_ids = [(5, 0, 0)]

        hallazgos = self._de(self._hallazgos("tarea_sin_responsable"), tarea)

        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["tipo"], "propuesta")
        self.assertEqual(hallazgos[0]["valores"], {"user_ids": [(6, 0, [self.ana.id])]})

    def test_sin_responsable_pregunta_cuando_hay_varios_candidatos(self):
        """Elegir "el más probable" entre dos personas es la propuesta plausible y falsa."""
        proyecto = self._proyecto("Ambigua", self.tecnica)
        tarea = self._tarea(proyecto, "De varios", usuario=self.ana)
        tarea.user_ids = [(5, 0, 0)]
        tarea.with_user(self.beto).message_post(body="Yo también anduve acá")

        hallazgos = self._de(self._hallazgos("tarea_sin_responsable"), tarea)

        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["tipo"], "pregunta")
        self.assertFalse(hallazgos[0]["valores"])

    def test_sin_responsable_no_propone_al_superusuario(self):
        """Una tarea creada por OdooBot no tiene candidato: proponerlo sería peor que no proponer."""
        proyecto = self._proyecto("De nadie", self.tecnica)
        tarea = self._tarea(proyecto, "Huérfana")
        tarea.user_ids = [(5, 0, 0)]

        hallazgos = self._de(self._hallazgos("tarea_sin_responsable"), tarea)

        self.assertEqual(hallazgos[0]["tipo"], "pregunta")

    def test_area_desalineada_pregunta_y_nunca_propone_alinear(self):
        """La regla estructural: es el único mecanismo que detecta el desalineado, y no lo arregla
        solo porque una tarea de otra área dentro del proyecto puede ser correcta."""
        proyecto = self._proyecto("Técnico", self.tecnica)
        tarea = self._tarea(proyecto, "Administrativa de verdad")
        tarea.area_id = self.funcional

        hallazgos = self._de(self._hallazgos("tarea_area_desalineada"), tarea)

        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["tipo"], "pregunta")
        self.assertFalse(hallazgos[0]["valores"], "nunca propone alinear al proyecto")
        self.assertIn(self.tecnica.display_name, hallazgos[0]["detalle"])
        self.assertIn(self.funcional.display_name, hallazgos[0]["detalle"])

    def test_area_desalineada_no_marca_la_tarea_que_heredo(self):
        proyecto = self._proyecto("Técnico", self.tecnica)
        tarea = self._tarea(proyecto, "Heredada")
        self.assertEqual(tarea.area_id, self.tecnica)
        self.assertFalse(self._de(self._hallazgos("tarea_area_desalineada"), tarea))

    def test_area_desalineada_detecta_el_proyecto_que_cambio_de_area(self):
        """Sin re-cascada, cambiar el área del proyecto deja las tareas atrás. Esta es la única
        red que lo agarra."""
        proyecto = self._proyecto("Se mudó", self.tecnica)
        tarea = self._tarea(proyecto, "Quedó atrás")
        proyecto.area_id = self.funcional

        self.assertEqual(len(self._de(self._hallazgos("tarea_area_desalineada"), tarea)), 1)


class TestReglaSinMovimiento(PmCommon):
    def test_sin_movimiento_mira_las_tres_señales_juntas(self):
        proyecto = self._proyecto("Frenado", self.tecnica)
        vieja = self._tarea(proyecto, "Vieja")
        self.env.cr.execute(
            "UPDATE project_task SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(days=60), vieja.id))
        self.env.cr.execute(
            "UPDATE mail_message SET date = %s WHERE model = 'project.task' AND res_id = %s",
            (fields.Datetime.now() - timedelta(days=60), vieja.id))
        self.env.invalidate_all()

        hallazgos = self._de(self._hallazgos("tarea_sin_movimiento", dias=7), vieja)

        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["tipo"], "pregunta")

    def test_una_tarea_recien_creada_no_esta_frenada(self):
        proyecto = self._proyecto("Nuevo", self.tecnica)
        tarea = self._tarea(proyecto, "Recién nacida")
        self.assertFalse(self._de(self._hallazgos("tarea_sin_movimiento", dias=7), tarea))


class TestReglasProyecto(PmCommon):
    def test_sin_area_propone_la_mayoritaria_de_sus_tareas(self):
        proyecto = self._proyecto("Sin área")
        for i in range(3):
            self._tarea(proyecto, "T%s" % i, area_id=self.tecnica.id)
        self._tarea(proyecto, "Otra", area_id=self.funcional.id)

        hallazgos = self._de(self._hallazgos("proyecto_sin_area"), proyecto)

        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["tipo"], "propuesta")
        self.assertEqual(hallazgos[0]["valores"], {"area_id": self.tecnica.id})

    def test_sin_area_pregunta_si_las_tareas_empatan(self):
        """La misma regla que usó la migración: el empate no se desempata inventando."""
        proyecto = self._proyecto("Empate")
        self._tarea(proyecto, "A", area_id=self.tecnica.id)
        self._tarea(proyecto, "B", area_id=self.funcional.id)

        hallazgos = self._de(self._hallazgos("proyecto_sin_area"), proyecto)

        self.assertEqual(hallazgos[0]["tipo"], "pregunta")
        self.assertFalse(hallazgos[0]["valores"])

    def test_sin_area_pregunta_si_no_hay_tareas(self):
        proyecto = self._proyecto("Vacío")
        hallazgos = self._de(self._hallazgos("proyecto_sin_area"), proyecto)
        self.assertEqual(hallazgos[0]["tipo"], "pregunta")

    def test_sin_cliente_nunca_lo_deduce(self):
        proyecto = self._proyecto("Implementación Acme", self.tecnica, partner_id=False)
        hallazgos = self._de(self._hallazgos("proyecto_sin_cliente"), proyecto)
        self.assertEqual(hallazgos[0]["tipo"], "pregunta")
        self.assertFalse(hallazgos[0]["valores"], "no infiere el cliente del nombre")

    def test_sin_plan_dice_que_le_falta(self):
        proyecto = self._proyecto("Sin fechas", self.tecnica, date_start=False, date=False)
        hallazgos = self._de(self._hallazgos("proyecto_sin_plan"), proyecto)
        self.assertEqual(hallazgos[0]["tipo"], "pregunta")
        self.assertIn("fecha", hallazgos[0]["detalle"])


class TestAlcance(PmCommon):
    def test_el_alcance_por_area_no_toca_otras_areas(self):
        """El criterio de aceptación: con alcance = Técnica, ninguna propuesta de otra área."""
        tecnico = self._proyecto("Proyecto técnico", self.tecnica)
        funcional = self._proyecto("Proyecto funcional", self.funcional)
        etapa_t = self.env["project.task.type"].create(
            {"name": "E", "sequence": 1, "project_ids": [(6, 0, [tecnico.id])]})
        self.env["project.task.type"].create(
            {"name": "E", "sequence": 1, "project_ids": [(6, 0, [funcional.id])]})
        del etapa_t
        de_tecnica = self._tarea(tecnico, "T")
        de_funcional = self._tarea(funcional, "F")
        (de_tecnica | de_funcional).write({"stage_id": False})

        hallazgos = self._hallazgos("tarea_sin_etapa", alcance="area", area_code="technical")

        self.assertTrue(self._de(hallazgos, de_tecnica))
        self.assertFalse(self._de(hallazgos, de_funcional))

    def test_una_regla_caida_no_se_lleva_puesta_la_auditoria(self):
        """Si una regla revienta, se reporta como nota y las demás siguen. Silenciarla daría un
        informe con 0 hallazgos, que se lee como "está todo bien"."""
        self._proyecto("Cualquiera", self.tecnica)
        engine = self.engine

        def explota(ctx):
            raise RuntimeError("kaboom")

        original = type(engine)._buscar_proyecto_sin_cliente
        try:
            type(engine)._buscar_proyecto_sin_cliente = lambda self, ctx: explota(ctx)
            _ctx, hallazgos = engine.auditar()
        finally:
            type(engine)._buscar_proyecto_sin_cliente = original

        caidas = [h for h in hallazgos if h.get("error")]
        self.assertEqual(len(caidas), 1)
        self.assertIn("kaboom", caidas[0]["resumen"])
        self.assertTrue([h for h in hallazgos if h["regla"] != "proyecto_sin_cliente"],
                        "las demás reglas igual corrieron")
