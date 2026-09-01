# -*- coding: utf-8 -*-
"""Las tres reglas de juicio del bloque C, SIN pegarle a la API.

`_consultar_al_modelo` se reemplaza por un doble que devuelve un veredicto armado a mano. Lo que
hay que probar no es que el modelo sepa decidir -eso no es determinístico y no se testea así- sino
todo lo que rodea a esa decisión: qué candidatos se le mandan, qué se hace con lo que contesta, y
sobre todo QUÉ SE DESCARTA de lo que contesta.
"""

from odoo.tests import tagged

from .test_pm_reglas import PmCommon


@tagged("post_install", "-at_install")
class JuicioCommon(PmCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tipo = cls.env.ref("mail.mail_activity_data_todo")
        cls.modelo_tarea = cls.env["ir.model"]._get("project.task")
        cls.modelo_proyecto = cls.env["ir.model"]._get("project.project")

    def _actividad(self, registro, resumen, usuario=None, nota=""):
        modelo = self.modelo_tarea if registro._name == "project.task" else self.modelo_proyecto
        return self.env["mail.activity"].create({
            "res_model_id": modelo.id,
            "res_id": registro.id,
            "activity_type_id": self.tipo.id,
            "summary": resumen,
            "note": nota,
            "user_id": (usuario or self.ana).id,
        })

    def _con_veredicto(self, veredicto):
        """Reemplaza la consulta al modelo y registra los payloads que se le mandaron."""
        payloads = []
        motor = type(self.env["primate.sagui.pm.engine"])

        def doble(self_motor, payload):
            payloads.append(payload)
            return veredicto

        self.patch(motor, "_consultar_al_modelo", doble)
        return payloads


class TestActividadesDuplicadas(JuicioCommon):
    def test_muestra_las_dos_lado_a_lado_y_no_propone_nada(self):
        """Cerrar una actividad en Odoo la BORRA: por eso es pregunta y no propuesta."""
        proyecto = self._proyecto("Con duplicadas", self.tecnica)
        una = self._actividad(proyecto, "Mandar presupuesto")
        otra = self._actividad(proyecto, "Enviar cotización al cliente")
        self._con_veredicto({"duplicados": [
            {"ids": [una.id, otra.id], "queda": una.id, "por_que": "mismo envío al mismo cliente"}]})

        hallazgos = self._hallazgos("actividades_duplicadas")

        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["tipo"], "pregunta")
        self.assertFalse(hallazgos[0]["valores"], "no propone borrar nada")
        detalle = hallazgos[0]["detalle"]
        self.assertIn("Mandar presupuesto", detalle)
        self.assertIn("Enviar cotización al cliente", detalle)
        self.assertIn("★ [%s]" % una.id, detalle.replace("★ ", "★ "))
        self.assertIn("mismo envío al mismo cliente", detalle)

    def test_sin_duplicados_no_hay_hallazgo(self):
        proyecto = self._proyecto("Sin duplicadas", self.tecnica)
        self._actividad(proyecto, "Llamar a Juan")
        self._actividad(proyecto, "Llamar a Juana")
        self._con_veredicto({"duplicados": [], "vinculos": [], "agrupables": []})

        self.assertFalse(self._hallazgos("actividades_duplicadas"))


class TestActividadEsTarea(JuicioCommon):
    def test_propone_colgarla_de_la_tarea(self):
        proyecto = self._proyecto("Con tarea", self.tecnica)
        tarea = self._tarea(proyecto, "Migrar la localización a 19.0")
        actividad = self._actividad(proyecto, "Hacer la migración de la localización")
        self._con_veredicto({"vinculos": [
            {"actividad_id": actividad.id, "tarea_id": tarea.id, "por_que": "es el mismo trabajo"}]})

        hallazgos = self._hallazgos("actividad_es_tarea")

        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["tipo"], "propuesta")
        self.assertEqual(hallazgos[0]["operacion"], "write")
        self.assertEqual(hallazgos[0]["valores"],
                         {"res_model_id": self.modelo_tarea.id, "res_id": tarea.id})

    def test_la_propuesta_es_aplicable_de_verdad(self):
        """res_model es un related de solo lectura: si se escribiera ese campo, no pasaría nada."""
        proyecto = self._proyecto("Aplicable", self.tecnica)
        tarea = self._tarea(proyecto, "La tarea")
        actividad = self._actividad(proyecto, "La actividad")
        self._con_veredicto({"vinculos": [
            {"actividad_id": actividad.id, "tarea_id": tarea.id, "por_que": "x"}]})

        valores = self._hallazgos("actividad_es_tarea")[0]["valores"]
        actividad.write(valores)

        self.assertEqual(actividad.res_model, "project.task")
        self.assertEqual(actividad.res_id, tarea.id)

    def test_no_propone_mover_algo_que_ya_esta_en_su_tarea(self):
        proyecto = self._proyecto("Ya vinculada", self.tecnica)
        tarea = self._tarea(proyecto, "La tarea")
        actividad = self._actividad(tarea, "Ya cuelga de la tarea")
        self._con_veredicto({"vinculos": [
            {"actividad_id": actividad.id, "tarea_id": tarea.id, "por_que": "x"}]})

        self.assertFalse(self._hallazgos("actividad_es_tarea"))


class TestActividadesSinTarea(JuicioCommon):
    def test_propone_crear_la_tarea_que_las_agrupa(self):
        proyecto = self._proyecto("Sin tarea", self.tecnica)
        una = self._actividad(proyecto, "Pedir accesos al servidor")
        otra = self._actividad(proyecto, "Configurar el backup")
        self._con_veredicto({"agrupables": [{
            "actividad_ids": [una.id, otra.id],
            "nombre_tarea": "Preparar el ambiente de producción",
            "por_que": "los dos son pasos del mismo alta"}]})

        hallazgos = self._hallazgos("actividades_sin_tarea")

        self.assertEqual(len(hallazgos), 1)
        self.assertEqual(hallazgos[0]["tipo"], "propuesta")
        self.assertEqual(hallazgos[0]["riesgo"], "alto", "crear registros nunca va a modo auto")
        self.assertEqual(hallazgos[0]["operacion"], "create")
        self.assertEqual(hallazgos[0]["valores"]["name"], "Preparar el ambiente de producción")
        self.assertEqual(hallazgos[0]["valores"]["project_id"], proyecto.id)
        self.assertEqual(hallazgos[0]["valores"]["area_id"], self.tecnica.id)
        self.assertEqual(hallazgos[0]["valores"]["user_ids"], [(6, 0, [self.ana.id])])

    def test_no_asigna_responsable_si_las_actividades_son_de_varios(self):
        proyecto = self._proyecto("De varios", self.tecnica)
        una = self._actividad(proyecto, "Paso uno", usuario=self.ana)
        otra = self._actividad(proyecto, "Paso dos", usuario=self.beto)
        self._con_veredicto({"agrupables": [{
            "actividad_ids": [una.id, otra.id], "nombre_tarea": "Hacer el trabajo", "por_que": "x"}]})

        valores = self._hallazgos("actividades_sin_tarea")[0]["valores"]

        self.assertNotIn("user_ids", valores, "con dos responsables no elige uno")

    def test_un_grupo_de_una_sola_actividad_no_es_un_grupo(self):
        proyecto = self._proyecto("Una sola", self.tecnica)
        una = self._actividad(proyecto, "Paso único")
        self._con_veredicto({"agrupables": [
            {"actividad_ids": [una.id], "nombre_tarea": "Tarea de una", "por_que": "x"}]})

        self.assertFalse(self._hallazgos("actividades_sin_tarea"))


class TestValidacionDelVeredicto(JuicioCommon):
    """La parte que no se puede saltear: el payload lleva texto escrito por usuarios y de la
    respuesta salen propuestas de ESCRITURA."""

    def test_descarta_ids_que_no_se_mandaron(self):
        """Una actividad de OTRO proyecto no se toca aunque el modelo la nombre."""
        proyecto = self._proyecto("El del alcance", self.tecnica)
        ajeno = self._proyecto("Otro proyecto", self.funcional)
        mia = self._actividad(proyecto, "Mía")
        de_otro = self._actividad(ajeno, "Ajena")
        self._con_veredicto({"duplicados": [
            {"ids": [mia.id, de_otro.id], "queda": mia.id, "por_que": "x"}]})

        hallazgos = self._hallazgos("actividades_duplicadas",
                                    alcance="proyecto", project_id=proyecto.id)

        self.assertFalse(hallazgos, "el grupo queda con un solo id válido y se descarta entero")

    def test_descarta_un_grupo_cuyo_queda_no_esta_en_ids(self):
        proyecto = self._proyecto("Incoherente", self.tecnica)
        una = self._actividad(proyecto, "Una")
        otra = self._actividad(proyecto, "Otra")
        self._con_veredicto({"duplicados": [
            {"ids": [una.id, otra.id], "queda": 999999, "por_que": "x"}]})

        self.assertFalse(self._hallazgos("actividades_duplicadas"))

    def test_descarta_un_vinculo_a_una_tarea_ajena(self):
        proyecto = self._proyecto("El del alcance", self.tecnica)
        ajeno = self._proyecto("Otro", self.funcional)
        tarea_ajena = self._tarea(ajeno, "Tarea de otro proyecto")
        actividad = self._actividad(proyecto, "Mía")
        self._con_veredicto({"vinculos": [
            {"actividad_id": actividad.id, "tarea_id": tarea_ajena.id, "por_que": "x"}]})

        hallazgos = self._hallazgos("actividad_es_tarea",
                                    alcance="proyecto", project_id=proyecto.id)

        self.assertFalse(hallazgos, "no se propone mover una actividad a una tarea de otro proyecto")

    def test_descarta_un_grupo_sin_nombre_de_tarea(self):
        proyecto = self._proyecto("Sin nombre", self.tecnica)
        una = self._actividad(proyecto, "Una")
        otra = self._actividad(proyecto, "Otra")
        self._con_veredicto({"agrupables": [
            {"actividad_ids": [una.id, otra.id], "nombre_tarea": "   ", "por_que": "x"}]})

        self.assertFalse(self._hallazgos("actividades_sin_tarea"))

    def test_el_payload_solo_lleva_lo_del_proyecto(self):
        proyecto = self._proyecto("El del alcance", self.tecnica)
        ajeno = self._proyecto("Otro", self.funcional)
        mia = self._actividad(proyecto, "Mía")
        tambien_mia = self._actividad(proyecto, "También mía")
        de_otro = self._actividad(ajeno, "Ajena")
        payloads = self._con_veredicto({"duplicados": [], "vinculos": [], "agrupables": []})

        self._hallazgos("actividades_duplicadas", alcance="proyecto", project_id=proyecto.id)

        self.assertEqual(len(payloads), 1)
        ids = {a["id"] for a in payloads[0]["actividades"]}
        self.assertEqual(ids, {mia.id, tambien_mia.id})
        self.assertNotIn(de_otro.id, ids)


class TestDegradacionYCosto(JuicioCommon):
    def test_sin_modelo_avisa_UNA_vez_y_las_otras_reglas_siguen(self):
        """Sin API key o con el proveedor caído: las diez reglas determinísticas ya corrieron."""
        proyecto = self._proyecto("Sin conector", self.tecnica, partner_id=False)
        self._actividad(proyecto, "Una")
        self._actividad(proyecto, "Otra")
        self._con_veredicto(None)

        _ctx, hallazgos = self.engine.auditar()

        avisos = [h for h in hallazgos if "no se evaluaron" in (h["resumen"] or "")]
        self.assertEqual(len(avisos), 1, "una sola nota, no una por regla")
        self.assertEqual(avisos[0]["tipo"], "nota")
        self.assertTrue([h for h in hallazgos if h["regla"] == "proyecto_sin_cliente"],
                        "las reglas determinísticas igual corrieron")

    def test_no_se_llama_al_modelo_si_no_hay_nada_que_juzgar(self):
        """Un proyecto con una sola actividad y sin tareas no tiene duplicados posibles ni nada
        con qué comparar. Preguntarle igual al modelo es pagar por una respuesta conocida."""
        proyecto = self._proyecto("Nada que juzgar", self.tecnica)
        proyecto.task_ids.unlink()
        self._actividad(proyecto, "Una sola")
        payloads = self._con_veredicto({"duplicados": [], "vinculos": [], "agrupables": []})

        self.engine.auditar(alcance="proyecto", project_id=proyecto.id)

        self.assertFalse(payloads, "no se gasta una llamada en un proyecto sin material")

    def test_una_sola_llamada_al_modelo_por_proyecto(self):
        """Las tres reglas comparten el veredicto: tres llamadas costarían el triple por lo mismo."""
        proyecto = self._proyecto("Uno solo", self.tecnica)
        self._actividad(proyecto, "Una")
        self._actividad(proyecto, "Otra")
        payloads = self._con_veredicto({"duplicados": [], "vinculos": [], "agrupables": []})

        self.engine.auditar(alcance="proyecto", project_id=proyecto.id)

        self.assertEqual(len(payloads), 1, "una llamada por proyecto, no una por regla")

    def test_las_reglas_de_juicio_salen_en_el_bloque_C(self):
        claves = [r["clave"] for r in self.engine._reglas()]
        bloques = [r["bloque"] for r in self.engine._reglas()]
        self.assertEqual(bloques, sorted(bloques), "el informe sale A, B, C, D")
        for clave in ("actividades_duplicadas", "actividad_es_tarea", "actividades_sin_tarea"):
            self.assertIn(clave, claves)


class TestParseoDelVeredicto(JuicioCommon):
    def test_acepta_json_envuelto_en_backticks(self):
        crudo = '```json\n{"duplicados": [], "vinculos": [], "agrupables": []}\n```'
        self.assertEqual(self.engine._parsear_veredicto(crudo),
                         {"duplicados": [], "vinculos": [], "agrupables": []})

    def test_un_veredicto_ilegible_es_como_no_haber_contestado(self):
        self.assertIsNone(self.engine._parsear_veredicto("perdón, no puedo ayudarte con eso"))
        self.assertIsNone(self.engine._parsear_veredicto("{roto"))
        self.assertIsNone(self.engine._parsear_veredicto(""))
