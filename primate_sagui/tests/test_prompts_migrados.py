# -*- coding: utf-8 -*-
"""Los seis prompts que salieron de Python a .md versionados.

Lo que hay que probar de esta migración no es que el texto esté: es que siga siendo CONDICIONAL.
Migrar estos fragmentos a un rol -que era la lectura fácil del diseño- los habría vuelto
incondicionales, y un usuario sin permisos de escritura habría terminado con instrucciones de
importación en su prompt sin que nada fallara.

Por eso cada caso se prueba en las dos direcciones: con la condición en True el texto está, con la
condición en False NO está. Más la tercera cosa, que es la que degrada en silencio: que cada
`source_path` resuelva a contenido real. Una ruta mal escrita hace que `content_of` devuelva "" y
el asistente quede sin esas instrucciones, sin un solo error en el log.
"""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

# clave de la skill -> texto que tiene que aparecer si la condición se cumple
CLAVES = {
    "website-from-pdf": "SITIO WEB DESDE UN PDF",
    "website-greenfield": "SITIO WEB PREMIUM DESDE LA IDENTIDAD",
    "spreadsheet-import": "IMPORTACIÓN DE PLANILLAS",
    "mcp-connectors": "CONECTORES (MCP)",
}
CLAVES_DISENADOR = {
    "web-designer-routing": "DISEÑO WEB",
    "documents-reference": "REFERENCIA DESDE DOCUMENTOS",
}


@tagged("post_install", "-at_install")
class TestSkillsResuelven(TransactionCase):
    def test_cada_skill_resuelve_a_contenido_real(self):
        """Una ruta mal escrita degrada EN SILENCIO: content_of devuelve "" y no falla nada."""
        Skill = self.env["sagui.skill"]
        for clave, marca in CLAVES.items():
            skill = Skill.search([("key", "=", clave)], limit=1)
            self.assertTrue(skill, "falta el registro de la skill «%s»" % clave)
            contenido = skill.content()
            self.assertTrue(contenido.strip(), "«%s» resuelve a vacío: ¿la ruta existe?" % clave)
            self.assertIn(marca, contenido)

    def test_ninguna_skill_de_chat_cuelga_de_un_rol(self):
        """Una skill listada en un rol se inyecta SIEMPRE. Estas son condicionales, así que
        colgarlas de un rol es exactamente el bug que esta migración evita."""
        for clave in CLAVES:
            skill = self.env["sagui.skill"].search([("key", "=", clave)], limit=1)
            roles = self.env["sagui.role"].search([("skill_ids", "in", skill.ids)])
            self.assertFalse(roles, "«%s» cuelga de %s y se inyectaría siempre"
                             % (clave, roles.mapped("key")))

    def test_el_rol_del_asistente_de_chat_existe_y_tiene_prompt(self):
        """El prompt base salió de Python: si el rol falta, _system_prompt tiene que FALLAR, no
        devolver un prompt mutilado."""
        rol = self.env["sagui.role"].get("chat_assistant")
        self.assertTrue((rol.system_prompt or "").strip())
        self.assertIn(rol.system_prompt.strip()[:40], self.env["primate.sagui.assistant"]._system_prompt())

    def test_el_rol_del_chat_no_lista_skills(self):
        """`role.prompt()` envuelve cada skill con un encabezado "===== SKILL: ... =====", lo que
        cambiaría el texto que hoy recibe el modelo."""
        self.assertFalse(self.env["sagui.role"].get("chat_assistant").skill_ids)


@tagged("post_install", "-at_install")
class TestCondicionEnPython(TransactionCase):
    """Los dos lados de cada condición. El texto va al .md; la condición se queda en Python."""

    def _prompt(self, assistant=None):
        return (assistant or self.env["primate.sagui.assistant"])._system_prompt()

    def test_importacion_solo_con_whitelist_de_escritura(self):
        Assistant = type(self.env["primate.sagui.assistant"])
        marca = CLAVES["spreadsheet-import"]

        self.patch(Assistant, "_write_whitelist", lambda s: {"product.template"})
        self.assertIn(marca, self._prompt(), "con whitelist, el texto tiene que estar")

        self.patch(Assistant, "_write_whitelist", lambda s: set())
        self.assertNotIn(marca, self._prompt(),
                         "SIN whitelist NO puede estar: es el bug que evita la migración")

    def test_conectores_solo_con_conectores_configurados(self):
        Assistant = type(self.env["primate.sagui.assistant"])
        marca = CLAVES["mcp-connectors"]

        self.patch(Assistant, "_connector_tool_specs",
                   lambda s: [{"name": "mcp__1__x", "description": "", "input_schema": {}}])
        self.assertIn(marca, self._prompt())

        self.patch(Assistant, "_connector_tool_specs", lambda s: [])
        self.assertNotIn(marca, self._prompt())

    def test_los_incondicionales_estan_siempre(self):
        prompt = self._prompt()
        for clave in ("website-from-pdf", "website-greenfield"):
            self.assertIn(CLAVES[clave], prompt)

    def test_si_falta_la_skill_el_prompt_no_revienta(self):
        """`content_of` devuelve "" para una clave inexistente: una skill faltante degrada el
        prompt, no rompe el chat. Es deliberado, y por eso hace falta el test de que las rutas
        resuelven: si no, esta degradación sería invisible."""
        Skill = type(self.env["sagui.skill"])
        self.patch(Skill, "content_of", lambda s, key: "")
        prompt = self._prompt()
        self.assertTrue(prompt.strip(), "el prompt base sigue estando")
        for marca in CLAVES.values():
            self.assertNotIn(marca, prompt)
