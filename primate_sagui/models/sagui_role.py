# -*- coding: utf-8 -*-
# ROL de Sagui: un perfil de trabajo = prompt propio + skills cargadas + tools habilitadas +
# verificador asociado. Es DATO (XML), no código: agregar un rol nuevo no requiere tocar Python,
# y ajustar el prompt o cambiar la rúbrica de su verificador se hace desde la UI.
#
# El rol NO ejecuta: lo consume el mixin que implementa el flujo (ej. primate.sagui.designer),
# que le pide el prompt y la lista de tools. Así la política vive en datos y la mecánica en código.
from odoo import api, fields, models, _
from odoo.exceptions import UserError

# Proveedores del REVISOR. La revisión cruzada quiere un modelo distinto al generador; hoy sólo
# está implementado Anthropic (en request AISLADA: ve evidencia + plan + rúbrica, nunca el HTML
# generado ni el hilo). Agregar otro proveedor = un adaptador en sagui.verification y una opción acá.
REVIEWER_PROVIDERS = [
    ("anthropic", "Anthropic (Claude)"),
]


class SaguiRole(models.Model):
    _name = "sagui.role"
    _description = "Rol de Sagui (prompt + skills + tools + verificador)"
    _order = "sequence, name"

    name = fields.Char(string="Nombre", required=True)
    key = fields.Char(
        string="Clave", required=True, index=True, copy=False,
        help="Identificador estable con el que el código pide el rol. Ej: 'web_designer'.")
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activo", default=True)
    description = fields.Text(string="Descripción")

    system_prompt = fields.Text(
        string="Prompt del rol", required=True,
        help="Se antepone a las skills. Definí acá QUIÉN es y qué decide; el CÓMO va en las skills.")
    skill_ids = fields.Many2many(
        "sagui.skill", "sagui_role_skill_rel", "role_id", "skill_id", string="Skills",
        help="Se cargan en runtime y se inyectan en el prompt, en orden.")
    tool_names = fields.Char(
        string="Tools habilitadas",
        help="Nombres separados por coma. Vacío = todas las del asistente.")

    # --- verificador asociado (post-condiciones) ---
    verifier_active = fields.Boolean(string="Verificar al terminar", default=True)
    verifier_rubric_id = fields.Many2one(
        "sagui.skill", string="Rúbrica del verificador",
        domain=[("kind", "=", "checklist")], ondelete="restrict",
        help="Se le pasa al verificador como CONTENIDO, nunca como ruta de archivo.")
    reviewer_provider = fields.Selection(
        REVIEWER_PROVIDERS, string="Proveedor del revisor", default="anthropic", required=True)
    reviewer_model = fields.Char(
        string="Modelo del revisor",
        help="Vacío = el modelo de análisis por defecto. Poné uno DISTINTO al del generador.")
    verifier_max_loops = fields.Integer(
        string="Vueltas máximas", default=3,
        help="Tope de ciclos generar → verificar → corregir antes de reportarle al humano.")

    _key_uniq = models.Constraint("UNIQUE (key)", "Ya existe un rol con esa clave.")

    # ------------------------------------------------------------------ API
    @api.model
    def get(self, key, required=True):
        """Devuelve el rol por clave."""
        role = self.search([("key", "=", key)], limit=1)
        if not role and required:
            raise UserError(_("No existe el rol de Sagui «%s».") % key)
        return role

    def prompt(self, extra=None):
        """System prompt completo del rol: su prompt + las skills cargadas + un extra opcional."""
        self.ensure_one()
        parts = [(self.system_prompt or "").strip()]
        skills = self.env["sagui.skill"].render(self.skill_ids.mapped("key"))
        if skills:
            parts.append(
                "Las siguientes SKILLS son tu procedimiento obligatorio. Seguilas al pie; "
                "donde una skill contradiga tu intuición, manda la skill.\n\n" + skills)
        if extra:
            parts.append(extra.strip())
        return "\n\n".join(p for p in parts if p)

    def skill_content(self, key):
        """Contenido de una skill del rol (o de cualquiera, por clave)."""
        self.ensure_one()
        skill = self.skill_ids.filtered(lambda s: s.key == key)
        return skill[:1].content() if skill else self.env["sagui.skill"].content_of(key)

    def allowed_tools(self, specs):
        """Filtra una lista de tool specs por las tools habilitadas del rol."""
        self.ensure_one()
        names = [n.strip() for n in (self.tool_names or "").split(",") if n.strip()]
        if not names:
            return specs
        return [s for s in specs if s.get("name") in names]

    def rubric_text(self):
        """Contenido de la rúbrica del verificador. "" si el rol no tiene una."""
        self.ensure_one()
        return self.verifier_rubric_id.content() if self.verifier_rubric_id else ""

    @api.constrains("verifier_active", "verifier_rubric_id")
    def _check_verifier(self):
        for rec in self:
            if rec.verifier_active and not rec.verifier_rubric_id:
                raise UserError(_(
                    "El rol «%s» verifica al terminar, así que necesita una rúbrica "
                    "(una sagui.skill de tipo checklist).") % rec.name)
