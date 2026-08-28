"""Todo `self._algo(...)` del rol tiene que existir en el modelo.

Este test nace de un bug real: un reemplazo por rango de texto se llevó puesta la definición de
`_parse_section` y dejó la llamada en pie. El módulo instalaba, el registry cargaba, los tests
pasaban — y la generación moría con AttributeError recién al construir un sitio, que es lo caro
y lo lento de probar.

Un método que no existe no es un error de diseño: es un typo que cuesta una corrida entera de
API. Se detecta leyendo el código, sin llamar a nadie.
"""
import ast

from odoo.tests.common import TransactionCase
from odoo.tools import file_path

# Cada archivo se chequea contra EL MODELO QUE DEFINE, no contra uno cualquiera: el verificador
# es un modelo aparte y sus métodos no viven en el asistente.
MODULOS = {
    "primate_sagui_designer/models/sagui_designer.py": "primate.sagui.assistant",
    "primate_sagui_designer/models/sagui_documents.py": "primate.sagui.assistant",
    "primate_sagui_designer/models/sagui_verifier_web.py": "sagui.verifier.web",
}


class TestSelfCallsResolve(TransactionCase):

    def _llamadas_a_self(self, ruta):
        """Nombres de método invocados como self.<algo>(…) en un archivo."""
        arbol = ast.parse(open(file_path(ruta), encoding="utf-8").read())
        nombres = set()
        for nodo in ast.walk(arbol):
            if (isinstance(nodo, ast.Call)
                    and isinstance(nodo.func, ast.Attribute)
                    and isinstance(nodo.func.value, ast.Name)
                    and nodo.func.value.id == "self"):
                nombres.add(nodo.func.attr)
        return nombres

    def test_every_self_call_in_the_role_exists(self):
        """Ningún método del rol se llama a sí mismo al vacío."""
        faltantes = {}
        for ruta, nombre_modelo in MODULOS.items():
            modelo = self.env[nombre_modelo]
            for nombre in self._llamadas_a_self(ruta):
                if not hasattr(modelo, nombre):
                    faltantes.setdefault(ruta, []).append(nombre)
        self.assertFalse(
            faltantes,
            "hay llamadas a métodos inexistentes (la generación moriría recién al construir): %s"
            % faltantes)

    def test_the_verifier_methods_exist(self):
        """El verificador visual es un modelo aparte: se chequea contra el suyo."""
        verificador = self.env["sagui.verifier.web"]
        for nombre in ("verify", "_shoot", "_attach", "_dom_findings", "_collect_evidence"):
            self.assertTrue(hasattr(verificador, nombre),
                            "sagui.verifier.web perdió %s" % nombre)
