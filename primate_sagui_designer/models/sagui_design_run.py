# -*- coding: utf-8 -*-
# REGISTRO DE UNA GENERACIÓN DE SITIO: la bitácora de auditoría que pide la política de Sagui.
# Guarda QUÉ se generó, CON QUÉ TOKENS, contra qué referencia, qué dijo el revisor y QUÉ QUEDÓ
# ABIERTO. También guarda la homepage anterior, para que revertir sea un botón y no una
# arqueología.
import json

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SaguiDesignRun(models.Model):
    _name = "sagui.design.run"
    _description = "Generación de sitio de Sagui (auditoría: plan, tokens, evidencia, hallazgos)"
    _order = "id desc"

    name = fields.Char(string="Referencia", required=True, default=lambda s: _("Generación"))
    role_id = fields.Many2one("sagui.role", string="Rol", ondelete="restrict")
    user_id = fields.Many2one("res.users", string="Pedido por", ondelete="set null",
                              default=lambda s: s.env.user)
    state = fields.Selection(
        [("draft", "Borrador"), ("proposed", "Propuesta"), ("building", "Construyendo"),
         ("verifying", "Verificando"), ("done", "Listo"), ("error", "Error"),
         ("reverted", "Revertida")],
        string="Estado", default="draft", required=True, index=True)

    # --- de dónde salió el diseño ---
    source = fields.Selection(
        [("reference", "Con referencia (PDF/mockup)"),
         ("greenfield", "Sin referencia (greenfield)"),
         ("partial", "Referencia parcial (logo/colores)")],
        string="Origen", required=True, default="greenfield",
        help="Lo decide el enrutado del rol según haya o no material de referencia.")
    brief = fields.Text(string="Brief")
    render_mode = fields.Selection(
        [("fiel", "Fiel (HTML/CSS a medida)"), ("editable", "Editable (snippets nativos)")],
        string="Modo de cuerpo", default="fiel")

    reference_attachment_ids = fields.Many2many(
        "ir.attachment", "sagui_design_run_ref_rel", "run_id", "attachment_id",
        string="Referencias")
    asset_attachment_ids = fields.Many2many(
        "ir.attachment", "sagui_design_run_asset_rel", "run_id", "attachment_id",
        string="Assets (logo, fotos)")
    documents_folder = fields.Char(string="Carpeta de Documentos")

    # --- sitios de referencia por URL (cuarto tipo de material) ---
    # NO SON REFERENCIAS A REPRODUCIR. De su captura sale sólo COMPOSICIÓN -hero, ritmo,
    # densidad, tipo de imagen, movimiento, forma de nav/footer-; paleta, tipografía y copy no
    # cruzan nunca. Por eso viven en su propio campo y no en reference_attachment_ids, que es lo
    # que el flujo "con referencia" reproduce.
    reference_urls = fields.Text(
        string="Sitios de referencia (URLs)",
        help="Una por línea. Se capturan a 1440 y 375 y se leen SOLO como composición.")
    reference_capture_ids = fields.Many2many(
        "ir.attachment", "sagui_design_run_urlshot_rel", "run_id", "attachment_id",
        string="Capturas de referencia",
        help="Evidencia de qué se miró: quedan guardadas con el run.")
    reference_capture_count = fields.Integer(
        string="Capturas", compute="_compute_reference_capture_count")

    @api.depends("reference_capture_ids")
    def _compute_reference_capture_count(self):
        for rec in self:
            rec.reference_capture_count = len(rec.reference_capture_ids)

    # --- el plan y su autocrítica (etapas 0 a 2 de la skill) ---
    plan_json = fields.Text(string="Plan de diseño (JSON)")
    critique = fields.Text(string="Autocrítica del plan")
    tokens_json = fields.Text(string="Tokens aplicados (JSON)")
    schema_json = fields.Text(string="Schema de secciones (JSON)")

    # --- resultado ---
    website_id = fields.Many2one("website", string="Sitio", ondelete="set null")
    page_id = fields.Many2one("website.page", string="Página", ondelete="set null")
    page_url = fields.Char(string="URL")
    prev_homepage_url = fields.Char(
        string="Homepage anterior", copy=False,
        help="Se guarda antes de publicar para que revertir sea reversible de verdad.")

    # --- verificación ---
    verification_ids = fields.Many2many(
        "sagui.verification", "sagui_design_run_verification_rel", "run_id", "verification_id",
        string="Verificaciones")
    loops_done = fields.Integer(string="Vueltas de verificación", default=0)
    open_findings_json = fields.Text(string="Hallazgos abiertos (JSON)")
    open_finding_count = fields.Integer(string="Abiertos", compute="_compute_open", store=True)
    verdict = fields.Selection(
        [("pass", "Pasa"), ("fail", "Con pendientes"), ("error", "Error")],
        string="Veredicto final", index=True)
    report = fields.Text(string="Reporte al humano")

    @api.depends("open_findings_json")
    def _compute_open(self):
        for rec in self:
            rec.open_finding_count = len(rec.open_findings())

    def open_findings(self):
        self.ensure_one()
        try:
            return json.loads(self.open_findings_json or "[]")
        except (ValueError, TypeError):
            return []

    # ------------------------------------------------------------------ acciones
    def action_revert_homepage(self):
        """Restaura la homepage anterior. La generación queda registrada como revertida."""
        self.ensure_one()
        if not self.prev_homepage_url:
            raise UserError(_("Esta generación no guardó una homepage anterior."))
        restored = self.env["primate.website.builder"].revert_homepage(website=self.website_id)
        if not restored:
            raise UserError(_("No pude restaurar la homepage anterior."))
        self.state = "reverted"
        return True

    def action_view_evidence(self):
        """Abre las capturas de todas las verificaciones de esta generación."""
        self.ensure_one()
        attachments = self.verification_ids.mapped("evidence_ids")
        return {
            "type": "ir.actions.act_window",
            "name": _("Evidencia de la verificación"),
            "res_model": "ir.attachment",
            "view_mode": "kanban,list,form",
            "domain": [("id", "in", attachments.ids)],
        }

    # ------------------------------------------------------------------ reporte
    def build_report(self):
        """Texto para el humano. Dice explícitamente CÓMO se verificó cada hallazgo abierto.

        Distinguir 'lo vio el revisor en una captura' de 'se comprobó por DOM' no es un detalle
        de estilo: son dos niveles de evidencia distintos y el humano tiene que poder saber cuál
        respalda cada cosa.
        """
        self.ensure_one()
        lineas = []
        vueltas = self.loops_done
        lineas.append(_("Verificación: %(n)s vuelta(s), veredicto %(v)s.") % {
            "n": vueltas, "v": dict(self._fields["verdict"].selection).get(self.verdict, "—")})

        capturas = len(self.verification_ids.mapped("evidence_ids"))
        lineas.append(_("Evidencia adjunta: %s captura(s) de la página real.") % capturas)

        # ¿Se pudo verificar D4 con una sesión logueada?
        d4 = [f for f in self.open_findings() if f.get("rubric") == "D4"]
        no_verificado = [f for f in d4 if f.get("method") == "no verificado"]
        if no_verificado:
            lineas.append(_(
                "⚠️ D4 (barra de edición) NO se pudo verificar: %s.")
                % no_verificado[0].get("seen"))
        elif any(f.get("method") == "DOM" for f in d4) or self.verdict:
            lineas.append(_(
                "D4 (barra de edición) se verificó POR DOM, no por captura: en Odoo 19 la barra "
                "nace con d-none y sólo la muestra redirect.js, así que mirarla en un PNG no "
                "prueba nada. La captura logueada queda igual como evidencia."))

        abiertos = self.open_findings()
        if not abiertos:
            lineas.append(_("No quedaron hallazgos abiertos."))
        else:
            lineas.append(_("Quedaron %s hallazgo(s) abierto(s):") % len(abiertos))
            for f in abiertos:
                origen = {"dom": _("comprobado por DOM"),
                          "review": _("visto por el revisor en la captura")}.get(
                              f.get("source"), _("origen no declarado"))
                lineas.append("  • [%(sev)s %(rub)s] %(sec)s%(bp)s — %(seen)s (%(origen)s). "
                              "Sugerido: %(fix)s" % {
                                  "sev": f.get("severity"), "rub": f.get("rubric") or "?",
                                  "sec": f.get("section"),
                                  "bp": (" @%spx" % f["breakpoint"]) if f.get("breakpoint") else "",
                                  "seen": f.get("seen"), "origen": origen, "fix": f.get("fix")})
        self.report = "\n".join(lineas)
        return self.report
