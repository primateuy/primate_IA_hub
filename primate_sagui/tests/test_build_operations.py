"""_build_operations() es la ÚNICA fuente de qué se construye en background.

El bug que motivó estos tests: la decisión de diferir al cron miraba una tupla escrita a mano
mientras el cron miraba _build_operations(). Una operación agregada por otro módulo quedaba en
tierra de nadie —se ejecutaba dentro del request del chat— y el usuario veía dos síntomas:
demora enorme y ningún aviso, ni al terminar ni al fallar.

Estos tests no prueban que el código ande: prueban QUE NO VUELVA A HABER DOS LISTAS.
"""
import inspect
import re

from odoo.tests.common import TransactionCase


class TestBuildOperations(TransactionCase):

    def _assistant(self):
        return self.env["primate.sagui.assistant"]

    def test_every_build_operation_is_declared_in_one_place(self):
        """Toda operación de build conocida sale de _build_operations()."""
        ops = self._assistant()._build_operations()

        self.assertIn("website", ops)
        self.assertIn("website_greenfield", ops)
        # Sin duplicados: dos módulos que agregan la misma clave sería un enganche mal hecho.
        self.assertEqual(len(ops), len(set(ops)), "hay operaciones repetidas: %s" % (ops,))

    def test_no_source_file_hardcodes_the_build_operation_list(self):
        """NADIE vuelve a escribir la lista a mano.

        Se leen los fuentes de los métodos que deciden sobre operaciones de build y se
        comprueba que ninguno enumere las claves en literal. Si alguien agrega
        `if op in ("website", "website_greenfield")` en otro lado, este test se pone rojo con
        el nombre del método, que es exactamente el aviso que faltó la primera vez.
        """
        sospechosos = []
        modelo = type(self._assistant())
        for nombre, metodo in inspect.getmembers(modelo, predicate=inspect.isfunction):
            try:
                fuente = inspect.getsource(metodo)
            except (OSError, TypeError):
                continue
            if nombre == "_build_operations":
                continue
            # Dos o más claves de build enumeradas juntas en literal.
            if re.search(r'["\']website["\']\s*,\s*["\']website_greenfield["\']', fuente):
                sospechosos.append(nombre)
        self.assertFalse(
            sospechosos,
            "estos métodos enumeran las operaciones a mano en vez de usar "
            "_build_operations(): %s" % ", ".join(sorted(sospechosos)))

    def test_the_deferral_and_the_cron_ask_the_same_question(self):
        """La condición que difiere y la búsqueda del cron tienen que mirar lo mismo.

        Es la comparación que nadie hizo: cada una era correcta por separado.
        """
        modelo = type(self._assistant())
        difiere = inspect.getsource(modelo._handle_confirmation)
        cron = inspect.getsource(modelo._claim_pending_builds)

        self.assertIn("_build_operations()", difiere,
                      "la decisión de diferir al cron no usa _build_operations()")
        self.assertIn("_build_operations()", cron,
                      "la búsqueda del cron no usa _build_operations()")

    def test_the_status_tool_sees_every_build_operation(self):
        """Preguntar «¿cómo va mi sitio?» tiene que ver TODOS los flujos.

        También estaba hardcodeado: un build del diseñador era invisible al consultar estado.
        """
        modelo = type(self._assistant())
        if not hasattr(modelo, "_tool_estado_construccion"):
            self.skipTest("el tool de estado no está instalado")
        fuente = inspect.getsource(modelo._tool_estado_construccion)

        self.assertIn("_build_operations()", fuente)
