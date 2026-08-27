# -*- coding: utf-8 -*-
# SKILL de Sagui: un archivo .md versionado en un addon (o texto inline) que se carga en RUNTIME
# y se inyecta en el system prompt de un rol. La gracia es tener UNA sola fuente de verdad: el
# mismo SKILL.md que lee Claude Code en el repo es el que guía a Sagui en producción, sin
# duplicarlo hardcodeado en prompts de Python.
#
# Tres tipos:
#   skill      → el procedimiento completo (ej. odoo-site-greenfield-design/SKILL.md)
#   reference  → material de apoyo que la skill cita (ej. anti-patterns.md)
#   checklist  → rúbrica evaluable; es lo que se le pasa a un verificador como CONTENIDO
#                (sagui.verification NUNCA recibe una ruta de archivo: ver sagui_verification.py)
import logging
import os

from odoo import api, fields, models, tools, _
from odoo.tools import file_open
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MAX_SKILL_BYTES = 512 * 1024


class SaguiSkill(models.Model):
    _name = "sagui.skill"
    _description = "Skill de Sagui (instrucciones .md cargadas en runtime)"
    _order = "kind, sequence, name"

    name = fields.Char(string="Nombre", required=True, translate=False)
    key = fields.Char(
        string="Clave", required=True, index=True, copy=False,
        help="Identificador estable con el que la piden los roles y el verificador. "
             "Ej: 'odoo-site-greenfield-design', 'verifier-rubric'.")
    kind = fields.Selection(
        [("skill", "Skill"), ("reference", "Referencia"), ("checklist", "Checklist / rúbrica")],
        string="Tipo", required=True, default="skill", index=True)
    sequence = fields.Integer(string="Secuencia", default=10)
    active = fields.Boolean(string="Activa", default=True)

    # Origen del contenido: archivo del addon (preferido, versionado) o texto inline (override).
    source_path = fields.Char(
        string="Ruta en el addon", copy=False,
        help="Ruta relativa al addons_path, ej. "
             "'primate_sagui_designer/skills/odoo-site-greenfield-design/SKILL.md'.")
    body = fields.Text(
        string="Contenido (override)", copy=False,
        help="Si tiene texto, MANDA sobre el archivo. Sirve para ajustar una skill en caliente "
             "sin tocar el addon; vaciarlo vuelve al archivo versionado.")
    body_preview = fields.Text(string="Contenido efectivo", compute="_compute_body_preview")
    char_count = fields.Integer(string="Caracteres", compute="_compute_body_preview")

    _sql_constraints = [
        ("key_uniq", "unique(key)", "Ya existe una skill con esa clave."),
    ]

    # ------------------------------------------------------------------ contenido
    @api.model
    @tools.ormcache("path")
    def _read_addon_file(self, path):
        """Lee un .md del addons_path. Cacheado por ruta (los archivos no cambian en caliente).

        Devuelve "" si no existe: una skill faltante degrada el prompt, no rompe la generación.
        """
        if not path:
            return ""
        try:
            with file_open(path, mode="rb") as fh:
                raw = fh.read(MAX_SKILL_BYTES + 1)
        except (FileNotFoundError, ValueError, OSError) as e:
            _logger.warning("Sagui: no pude leer la skill %s (%s)", path, e)
            return ""
        if len(raw) > MAX_SKILL_BYTES:
            _logger.warning("Sagui: skill %s excede %s bytes; se trunca", path, MAX_SKILL_BYTES)
            raw = raw[:MAX_SKILL_BYTES]
        return raw.decode("utf-8", errors="replace")

    def content(self):
        """Texto efectivo de la skill: el override inline si hay, si no el archivo del addon."""
        self.ensure_one()
        if (self.body or "").strip():
            return self.body
        return self._read_addon_file(self.source_path or "")

    @api.model
    def content_of(self, key):
        """Contenido de una skill por clave. "" si no existe (no revienta un prompt por eso)."""
        skill = self.search([("key", "=", key)], limit=1)
        return skill.content() if skill else ""

    @api.model
    def render(self, keys, header=True):
        """Concatena varias skills en un bloque listo para inyectar en un system prompt."""
        parts = []
        for key in keys or []:
            skill = self.search([("key", "=", key)], limit=1)
            if not skill:
                _logger.warning("Sagui: skill '%s' no encontrada; sigo sin ella", key)
                continue
            text = skill.content()
            if not text.strip():
                continue
            if header:
                parts.append("===== SKILL: %s (%s) =====\n%s" % (skill.key, skill.kind, text))
            else:
                parts.append(text)
        return "\n\n".join(parts)

    # ------------------------------------------------------------------ compute / cache
    @api.depends("body", "source_path")
    def _compute_body_preview(self):
        for rec in self:
            text = rec.content() if rec.id else (rec.body or "")
            rec.body_preview = text[:4000]
            rec.char_count = len(text)

    @api.model
    def clear_file_cache(self):
        """Invalida el cache de archivos (útil tras editar un .md sin reiniciar el server)."""
        self.env.registry.clear_cache()
        return True

    @api.constrains("source_path", "body")
    def _check_source(self):
        for rec in self:
            if not (rec.source_path or "").strip() and not (rec.body or "").strip():
                raise UserError(_("La skill «%s» necesita una ruta en el addon o contenido inline.")
                                % (rec.name or rec.key))
            path = (rec.source_path or "").strip()
            if path and (os.path.isabs(path) or ".." in path.split("/")):
                raise UserError(_("La ruta de la skill debe ser relativa al addons_path, sin '..'."))
