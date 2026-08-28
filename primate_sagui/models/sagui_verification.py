# -*- coding: utf-8 -*-
# VERIFICACIÓN de post-condiciones: el "Check/Verify" GENÉRICO de Sagui.
#
# Un generador (el rol que sea) produce algo; este modelo lo evalúa contra post-condiciones
# explícitas, con EVIDENCIA (capturas, salidas, adjuntos) y un revisor INDEPENDIENTE, y devuelve
# HALLAZGOS ESTRUCTURADOS — no un texto de opinión. Es la pieza que después reusa el loop
# autónomo de Automatizaciones: la interfaz es {post-condiciones + evidencia → hallazgos}.
#
# Reglas duras del contrato:
#   • La rúbrica llega como CONTENIDO (texto) o como sagui.skill de tipo checklist.
#     NUNCA como ruta de archivo: este modelo es genérico y no puede saber en qué addon vive el
#     rol que lo usa (ver _resolve_rubric, que rechaza explícitamente algo con pinta de ruta).
#   • El revisor ve SOLO evidencia + contexto declarado + rúbrica. Nunca el código/HTML generado
#     ni el hilo de la conversación: si ve la fuente, deja de ser un par de ojos independiente.
#   • Un hallazgo sin ubicación y sin arreglo concreto se descarta al parsear. "Mejorá el diseño"
#     no es un hallazgo.
import base64
import json
import logging
import re
import struct

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MAX_EVIDENCE = 8
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
REVIEW_TIMEOUT = 240
REVIEW_MAX_TOKENS = 4096

# Contrato de SALIDA del revisor. La rúbrica dice QUÉ mirar; esto dice CÓMO devolverlo, para que
# los hallazgos se puedan aplicar de forma determinística en vez de leerse a mano.
OUTPUT_CONTRACT = """
Devolvé EXCLUSIVAMENTE un objeto JSON (sin ``` ni texto alrededor) con esta forma:

{"verdict": "pass" | "fail",
 "blocks": {"A": "pass|fail", "B": "pass|fail", "C": "pass|fail", "D": "pass|fail"},
 "findings": [
   {"severity": "FAIL" | "WARN",
    "rubric": "<id del ítem de rúbrica, ej. B1>",
    "section": "<sección o elemento concreto>",
    "breakpoint": <número de px, o null si aplica a todos>,
    "seen": "<qué está mal, concreto y observable en la evidencia>",
    "fix": "<el cambio MÍNIMO que lo corrige; nombrá el token o el elemento>",
    "fix_kind": "token" | "regen",
    "token": "<nombre de la custom property si fix_kind=token, ej. --fs-h2; si no, null>",
    "token_value": "<el VALOR nuevo exacto, ej. '2.4rem' o '#1f6f64'; si no, null>"}
 ]}

fix_kind = "token" para todo lo que sea tamaño, espaciado, color o peso: son ediciones del bloque
de tokens. fix_kind = "regen" SÓLO para fallas de layout o estructura que exijan regenerar la
sección. Ante la duda, "token".

Cuando pongas fix_kind "token", dame SIEMPRE `token` y `token_value` con el valor nuevo exacto y
listo para escribir en el CSS: se aplica de forma automática y literal, sin que nadie lo
interprete. Si no podés dar un valor concreto, no uses fix_kind "token".

No aceptes ni emitas hallazgos vagos: cada uno tiene que apuntar a un elemento concreto y traer un
cambio aplicable. Si no podés nombrar el elemento, no es un hallazgo. Si todo pasa, devolvé
"findings": [] con verdict "pass".
"""

REVIEWER_SYSTEM = """Sos un revisor de diseño adversarial. NO construiste esto: lo estás auditando.
Recibís evidencia (capturas), el plan que el generador debía seguir y una rúbrica. Tu trabajo es
encontrar dónde el resultado NO cumple la rúbrica, con precisión quirúrgica y sin cortesía.

No tenés acceso al código fuente y no lo necesitás: juzgá lo que se VE en la evidencia. Si algo no
se puede determinar desde la evidencia, no lo afirmes — omitilo.
"""


class SaguiVerification(models.Model):
    _name = "sagui.verification"
    _description = "Verificación de post-condiciones (Check/Verify genérico de Sagui)"
    _order = "id desc"

    name = fields.Char(string="Referencia", default="Verificación")
    # Qué se verificó (genérico: un sitio, un job, lo que sea).
    res_model = fields.Char(string="Modelo del objeto", index=True)
    res_id = fields.Integer(string="ID del objeto", index=True)
    target_json = fields.Text(string="Objetivo (JSON)")
    loop_no = fields.Integer(string="Vuelta", default=0)

    # Rúbrica: contenido efectivo + de dónde salió (trazabilidad, no origen de lectura).
    rubric_text = fields.Text(string="Rúbrica (contenido)", required=True)
    rubric_skill_id = fields.Many2one("sagui.skill", string="Rúbrica (skill)", ondelete="set null")

    reviewer_provider = fields.Char(string="Proveedor del revisor", default="anthropic")
    reviewer_model = fields.Char(string="Modelo del revisor")

    evidence_ids = fields.Many2many(
        "ir.attachment", "sagui_verification_evidence_rel", "verification_id", "attachment_id",
        string="Evidencia")
    evidence_labels = fields.Char(string="Etiquetas de la evidencia")
    # QUÉ VIO REALMENTE EL REVISOR. Sin esto, una captura degradada -escalada por el
    # postproceso de imágenes, recortada por altura, o tomada a un ancho que no es el del
    # breakpoint- entra a la revisión sin que nadie lo note, y el revisor opina de espaciado
    # y alineación mirando otra cosa.
    evidence_dims_json = fields.Text(string="Dimensiones de la evidencia (JSON)")

    findings_json = fields.Text(string="Hallazgos (JSON)")
    blocks_json = fields.Text(string="Bloques de la rúbrica (JSON)")
    finding_count = fields.Integer(string="Hallazgos", compute="_compute_counts", store=True)
    fail_count = fields.Integer(string="FAIL", compute="_compute_counts", store=True)
    verdict = fields.Selection(
        [("pass", "Pasa"), ("fail", "No pasa"), ("error", "Error")],
        string="Veredicto", index=True)
    notes = fields.Text(string="Notas")
    raw_response = fields.Text(string="Respuesta cruda del revisor")

    @api.depends("findings_json")
    def _compute_counts(self):
        for rec in self:
            findings = rec.findings() or []
            rec.finding_count = len(findings)
            rec.fail_count = len([f for f in findings if (f.get("severity") or "").upper() == "FAIL"])

    def findings(self):
        self.ensure_one()
        try:
            return json.loads(self.findings_json or "[]")
        except (ValueError, TypeError):
            return []

    # ==================================================================
    #  API pública
    # ==================================================================
    @api.model
    def run(self, target=None, rubric=None, rubric_key=None, context=None, evidence=None,
            collector=None, reviewer=None, loop_no=0, res_model=None, res_id=None, name=None):
        """Corre una verificación y devuelve {verdict, findings, verification_id, evidence_ids}.

        :param target:    dict con lo que identifica al objeto verificado (se guarda tal cual).
        :param rubric:    CONTENIDO de la rúbrica (texto). Excluyente con rubric_key.
        :param rubric_key: clave de una sagui.skill de tipo checklist.
        :param context:   dict de contexto declarado que el revisor SÍ puede ver
                          (ej. {'plan': ..., 'tokens': ...}). Nunca metas acá el código generado.
        :param evidence:  lista de {'label': str, 'attachment_id': int} ya recolectada.
        :param collector: dict {'model': ..., 'method': ...} que recolecta la evidencia a partir
                          del target. Se usa cuando la verificación tiene que producirla ella misma
                          (ej. sacar capturas con Playwright).
        :param reviewer:  dict {'provider': ..., 'model': ...}.
        """
        rubric_text, skill = self._resolve_rubric(rubric, rubric_key)
        reviewer = reviewer or {}
        provider = reviewer.get("provider") or "anthropic"
        rec = self.sudo().create({
            "name": name or _("Verificación"),
            "res_model": res_model, "res_id": res_id or 0,
            "target_json": json.dumps(target or {}, ensure_ascii=False, default=str),
            "loop_no": loop_no,
            "rubric_text": rubric_text,
            "rubric_skill_id": skill.id if skill else False,
            "reviewer_provider": provider,
            "reviewer_model": reviewer.get("model") or "",
        })

        try:
            items = self._collect(collector, target, evidence)
            if not items:
                raise UserError(_("No hay evidencia para verificar: sin capturas no hay revisión."))
            dims = self._measure_evidence(items)
            rec.write({
                "evidence_ids": [(6, 0, [i["attachment_id"] for i in items])],
                "evidence_labels": ", ".join(i.get("label") or "" for i in items),
                "evidence_dims_json": json.dumps(dims, ensure_ascii=False),
            })
            # CON EVIDENCIA DEGRADADA EL REVISOR NO CORRE. Gastar una llamada para que opine
            # sobre una imagen que no es la página es peor que no verificar: devuelve hallazgos
            # con pinta de ciertos. Se corta con un veredicto de INFRA, que no es un diseño
            # reprobado y así hay que leerlo.
            degradadas = [d for d in dims if d.get("degraded")]
            if degradadas:
                detalle = "; ".join(d["detail"] for d in degradadas)
                rec.write({"verdict": "error",
                           "notes": _("Evidencia degradada: %s") % detalle})
                return {"verdict": "error", "error": _(
                    "No verifiqué: la evidencia no sirve para revisar (%s). Es un problema de "
                    "captura, NO un defecto del diseño.") % detalle,
                    "findings": [], "verification_id": rec.id,
                    "evidence_ids": rec.evidence_ids.ids, "infra": True}
            raw = self._review(provider, reviewer.get("model"), rec.rubric_text, context, items)
            parsed = self._parse(raw)
            rec.write({
                "raw_response": raw[:20000],
                "findings_json": json.dumps(parsed["findings"], ensure_ascii=False),
                "blocks_json": json.dumps(parsed["blocks"], ensure_ascii=False),
                "verdict": parsed["verdict"],
                "notes": parsed.get("notes") or False,
            })
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui: falló la verificación %s", rec.id)
            rec.write({"verdict": "error", "notes": str(e)[:500]})
            return {"verdict": "error", "error": str(e), "findings": [],
                    "verification_id": rec.id, "evidence_ids": rec.evidence_ids.ids}

        return {
            "verdict": rec.verdict,
            "findings": rec.findings(),
            "blocks": json.loads(rec.blocks_json or "{}"),
            "verification_id": rec.id,
            "evidence_ids": rec.evidence_ids.ids,
        }

    def evidence_dims(self):
        """Dimensiones medidas de la evidencia de esta verificación (lista de dicts)."""
        self.ensure_one()
        try:
            return json.loads(self.evidence_dims_json or "[]")
        except ValueError:
            return []

    @api.model
    def _measure_evidence(self, items):
        """Ancho y alto REALES de cada captura, y si sirve para revisar.

        El PNG se lee de su cabecera IHDR: son 24 bytes y no hace falta una librería de
        imágenes para saber cuánto mide.

        Una captura está degradada cuando su ancho no coincide con el breakpoint que dice su
        etiqueta. Eso pasó de verdad: Odoo redimensiona las imágenes al guardarlas como adjunto
        (`base.image_autoresize_max_px`), y una captura de 1440x15106 entraba como 183x1920.
        En la lista de adjuntos se veía perfecta.

        Returns:
            list: [{"attachment_id", "label", "width", "height", "expected_width",
                    "degraded", "truncated", "detail"}]
        """
        salida = []
        for item in items or []:
            att = self.env["ir.attachment"].sudo().browse(item.get("attachment_id")).exists()
            ancho = alto = 0
            if att:
                try:
                    crudo = att.raw or b""
                    if crudo[:8] == b"\x89PNG\r\n\x1a\n":
                        ancho, alto = struct.unpack(">II", crudo[16:24])
                except Exception:  # noqa: BLE001
                    ancho = alto = 0
            etiqueta = item.get("label") or ""
            esperado = 0
            encontrado = re.search(r"(\d{3,4})\s*(?:px|w)\b", etiqueta)
            if encontrado:
                esperado = int(encontrado.group(1))
            degradada, detalle = False, ""
            if not ancho:
                degradada = True
                detalle = _("«%s»: no pude leer las dimensiones de la captura") % etiqueta
            elif esperado and ancho != esperado:
                degradada = True
                detalle = _(
                    "«%(label)s» mide %(w)sx%(h)s px y debería medir %(e)s de ancho: la imagen "
                    "se guardó escalada y no se puede revisar espaciado ni alineación sobre eso"
                ) % {"label": etiqueta, "w": ancho, "h": alto, "e": esperado}
            salida.append({
                "attachment_id": item.get("attachment_id"), "label": etiqueta,
                "width": ancho, "height": alto, "expected_width": esperado,
                "degraded": degradada, "truncated": bool(item.get("truncated")),
                "page_height": item.get("page_height") or 0, "detail": detalle,
            })
        return salida

    # ==================================================================
    #  Rúbrica: contenido o skill; NUNCA una ruta
    # ==================================================================
    @api.model
    def _resolve_rubric(self, rubric, rubric_key):
        skill = None
        if rubric_key:
            skill = self.env["sagui.skill"].search([("key", "=", rubric_key)], limit=1)
            if not skill:
                raise UserError(_("No existe la skill de rúbrica «%s».") % rubric_key)
            if skill.kind != "checklist":
                raise UserError(_(
                    "La skill «%s» no es una rúbrica (tipo checklist).") % rubric_key)
            text = skill.content()
        else:
            text = rubric or ""

        text = (text or "").strip()
        if not text:
            raise UserError(_("La verificación necesita una rúbrica con contenido."))
        # Guarda explícita: si llega algo con pinta de ruta de archivo, es un error de llamada.
        # Este modelo es genérico y no resuelve rutas de addons ajenos.
        if "\n" not in text and (text.endswith((".md", ".txt", ".json")) or text.startswith("/")):
            raise UserError(_(
                "La rúbrica se pasa como CONTENIDO o como sagui.skill de tipo checklist, "
                "no como ruta de archivo (recibí: %s)." % text[:120]))
        return text, skill

    # ==================================================================
    #  Evidencia
    # ==================================================================
    @api.model
    def _collect(self, collector, target, evidence):
        """Normaliza la evidencia: la ya provista, o la que produzca el collector declarado."""
        items = list(evidence or [])
        if collector:
            model, method = collector.get("model"), collector.get("method")
            if not model or model not in self.env:
                raise UserError(_("Collector de evidencia inválido: %s") % model)
            produced = getattr(self.env[model].sudo(), method)(target) or []
            items += list(produced)

        clean = []
        for item in items[:MAX_EVIDENCE]:
            att_id = item.get("attachment_id")
            att = self.env["ir.attachment"].sudo().browse(att_id).exists() if att_id else None
            if not att:
                continue
            clean.append({"label": item.get("label") or att.name or "", "attachment_id": att.id})
        return clean

    # ==================================================================
    #  Revisor (adaptadores por proveedor)
    # ==================================================================
    @api.model
    def _review(self, provider, model, rubric_text, context, items):
        blocks = self._build_blocks(rubric_text, context, items)
        handler = getattr(self, "_review_%s" % provider, None)
        if not handler:
            raise UserError(_(
                "Proveedor de revisión no soportado: %s. Implementá _review_%s en "
                "sagui.verification.") % (provider, provider))
        return handler(model, blocks)

    @api.model
    def _build_blocks(self, rubric_text, context, items):
        """Arma el contenido del mensaje al revisor: evidencia + contexto declarado + rúbrica.

        Deliberadamente NO incluye el HTML/CSS generado: el revisor juzga lo que se ve.
        """
        blocks = []
        for item in items:
            att = self.env["ir.attachment"].sudo().browse(item["attachment_id"])
            raw = att.raw or b""
            if not raw or len(raw) > MAX_EVIDENCE_BYTES:
                _logger.info("Sagui: evidencia '%s' omitida (%s bytes)", item.get("label"), len(raw))
                continue
            mimetype = (att.mimetype or "image/png").split(";")[0].strip()
            blocks.append({"type": "text", "text": "Evidencia: %s" % (item.get("label") or att.name)})
            blocks.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mimetype,
                           "data": base64.b64encode(raw).decode("ascii")},
            })
        if context:
            blocks.append({"type": "text", "text": "PLAN QUE DEBÍA SEGUIR:\n%s"
                           % json.dumps(context, ensure_ascii=False, indent=2, default=str)})
        blocks.append({"type": "text", "text": "RÚBRICA:\n%s" % rubric_text})
        blocks.append({"type": "text", "text": OUTPUT_CONTRACT})
        return blocks

    @api.model
    def _review_anthropic(self, model, blocks):
        """Revisión con Claude en request AISLADA (sin historial, sin tools, sin el HTML)."""
        icp = self.env["ir.config_parameter"].sudo()
        model = model or icp.get_param("primate_sagui.reviewer_model") or None
        data = self.env["primate.ai.connector"].call(
            [{"role": "user", "content": blocks}],
            system=REVIEWER_SYSTEM, model=model, max_tokens=REVIEW_MAX_TOKENS,
            timeout=REVIEW_TIMEOUT, cache=False,
        )
        return "".join(b.get("text", "") for b in (data.get("content") or [])
                       if b.get("type") == "text")

    # ==================================================================
    #  Parseo de hallazgos
    # ==================================================================
    @api.model
    def _parse(self, raw):
        payload = self._safe_json(raw) or {}
        findings = []
        for item in payload.get("findings") or []:
            if not isinstance(item, dict):
                continue
            seen = (item.get("seen") or "").strip()
            fix = (item.get("fix") or "").strip()
            section = (item.get("section") or "").strip()
            # Un hallazgo sin ubicación ni arreglo concreto no es accionable: se descarta.
            if not seen or not fix or not section:
                _logger.info("Sagui: hallazgo descartado por vago: %s", item)
                continue
            fix_kind = (item.get("fix_kind") or "token").lower()
            findings.append({
                "severity": (item.get("severity") or "FAIL").upper(),
                "rubric": (item.get("rubric") or "").strip(),
                "section": section,
                "breakpoint": item.get("breakpoint"),
                "seen": seen,
                "fix": fix,
                "fix_kind": fix_kind if fix_kind in ("token", "regen") else "token",
                "token": (item.get("token") or "").strip() or None,
                "token_value": (item.get("token_value") or "").strip() or None,
            })
        blocks = payload.get("blocks") or {}
        verdict = (payload.get("verdict") or "").lower()
        if verdict not in ("pass", "fail"):
            # Sin veredicto explícito: manda la evidencia. Un FAIL cualquiera no pasa.
            verdict = "fail" if any(f["severity"] == "FAIL" for f in findings) else "pass"
        return {"verdict": verdict, "findings": findings, "blocks": blocks,
                "notes": payload.get("notes")}

    @api.model
    def _safe_json(self, raw):
        text = (raw or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = text.split("```")[1] if "```" in text[3:] else text[3:]
            text = text.split("\n", 1)[-1] if text.lower().startswith("json") else text
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except (ValueError, TypeError):
            _logger.warning("Sagui: no pude parsear la respuesta del revisor")
            return None
