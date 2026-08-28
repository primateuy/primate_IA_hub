# -*- coding: utf-8 -*-
# VERIFICADOR VISUAL de un sitio generado. Es el primer caso concreto del Check/Verify genérico
# (sagui.verification): produce EVIDENCIA (capturas reales de la página publicada) y la contrasta
# contra la rúbrica, devolviendo hallazgos accionables.
#
# Dos fuentes de hallazgos, y el reporte SIEMPRE dice cuál es cuál:
#   • dom     → determinísticos, calculados en Python desde las sondas del navegador. Cubren los
#               ítems que un PNG no puede probar de forma confiable. En Odoo 19 la barra de edición
#               se sirve con `d-none` y recién `redirect.js` la muestra, así que D4 se verifica
#               MIRANDO EL DOM, no la captura; la captura logueada queda como evidencia de apoyo.
#   • review  → los que encuentra el revisor independiente mirando las capturas (A, B, C).
#
# Playwright corre por subprocess (tools/shoot.py), nunca dentro del worker: es asyncio + un
# navegador entero. Lo dispara el cron de build, que ya tiene su propia transacción y sus commits.
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile

from odoo import api, models, _
from odoo.tools import file_path

_logger = logging.getLogger(__name__)

# Cuánto puede moverse una costura entre secciones sin que sea un quiebre de ritmo. Un rem
# redondeado a px no es un error de diseño.
SPACING_TOLERANCE_PX = 2

SHOOT_TIMEOUT = 300
DEFAULT_BREAKPOINTS = (375, 768, 1440)


class SaguiVerifierWeb(models.AbstractModel):
    _name = "sagui.verifier.web"
    _description = "Verificador visual de sitios generados por Sagui"

    # ==================================================================
    #  Entrada principal
    # ==================================================================
    @api.model
    def verify(self, target, rubric_key=None, rubric=None, context=None, reviewer=None,
               loop_no=0, res_model=None, res_id=None):
        """Captura la página real, la revisa y devuelve hallazgos ordenados por severidad.

        :param target: {'url': '/', 'website_id': int, 'design_run_id': int (opcional)}
        :return: dict de sagui.verification.run + 'dom_findings' y 'evidence_labels'.
        """
        target = dict(target or {})
        shots = self._shoot(target)
        if not shots.get("ok"):
            return {"verdict": "error", "findings": [],
                    "error": "; ".join(shots.get("errors") or [_("no pude capturar la página")]),
                    "dom_findings": [], "evidence_ids": []}

        evidence = self._attach(shots, target)
        dom_findings = self._dom_findings(
            shots, register=((context or {}).get("register") or {}).get("key"))

        result = self.env["sagui.verification"].run(
            target=target,
            rubric=rubric, rubric_key=rubric_key,
            context=context,
            evidence=evidence,
            reviewer=reviewer,
            loop_no=loop_no,
            res_model=res_model or "sagui.design.run",
            res_id=res_id or target.get("design_run_id") or 0,
            name=_("Verificación visual %s") % (target.get("url") or "/"),
        )

        # Los hallazgos de DOM se suman a los del revisor, marcados por origen para que el
        # reporte al humano pueda decir explícitamente cómo se verificó cada cosa.
        merged = [dict(f, source="review") for f in (result.get("findings") or [])]
        merged += dom_findings
        # Un fallo de INFRAESTRUCTURA del revisor (sin crédito, timeout, proveedor caído) no es
        # un fallo de diseño: se propaga como 'error' para que el loop corte y el humano se entere
        # de que la revisión no ocurrió, en vez de leer un "no pasa" que nadie dictaminó.
        if result.get("verdict") == "error":
            verdict = "error"
        elif any(f.get("severity") == "FAIL" for f in merged):
            verdict = "fail"
        else:
            verdict = result.get("verdict") or "pass"

        verification = self.env["sagui.verification"].sudo().browse(result.get("verification_id"))
        if verification.exists():
            verification.write({
                "findings_json": json.dumps(merged, ensure_ascii=False),
                "verdict": verdict,
            })

        result.update({
            "verdict": verdict,
            "findings": merged,
            "dom_findings": dom_findings,
            "admin_session": shots.get("admin_session") or {},
            "evidence_labels": [s.get("label") for s in shots.get("shots") or []],
        })
        return result

    # ==================================================================
    #  Evidencia: subprocess a Playwright
    # ==================================================================
    @api.model
    def _collect_evidence(self, target):
        """Collector para sagui.verification.run(collector=...). Devuelve [{label, attachment_id}]."""
        shots = self._shoot(dict(target or {}))
        if not shots.get("ok"):
            return []
        return self._attach(shots, target or {})

    @api.model
    def _shoot(self, target):
        """Corre tools/shoot.py y devuelve su JSON. Nunca levanta: los errores van en el dict."""
        icp = self.env["ir.config_parameter"].sudo()
        # El capturador tiene que pegarle a ESTA instancia. `web.base.url` suele apuntar al
        # dominio público (odoo.sh, un proxy), que desde el server puede no resolver o servir otra
        # cosa; por eso hay un override explícito.
        base_url = (target.get("base_url")
                    or icp.get_param("primate_sagui.shot_base_url")
                    or icp.get_param("web.base.url") or "").rstrip("/")
        if not base_url:
            return {"ok": False, "shots": [], "errors": [_(
                "no sé a qué URL pegarle: configurá primate_sagui.shot_base_url o web.base.url")]}

        try:
            script = file_path("primate_sagui_designer/tools/shoot.py")
        except (FileNotFoundError, ValueError) as e:
            return {"ok": False, "errors": [_("no encuentro shoot.py: %s") % e], "shots": []}

        breakpoints = target.get("breakpoints") or DEFAULT_BREAKPOINTS
        out_dir = tempfile.mkdtemp(prefix="sagui-shots-")
        cmd = [
            sys.executable, script,
            "--base-url", base_url,
            "--path", target.get("url") or "/",
            "--out-dir", out_dir,
            "--breakpoints", ",".join(str(b) for b in breakpoints),
        ]
        # Credenciales del usuario técnico para la captura logueada (evidencia de D4). Sin
        # credenciales la verificación sigue: D4 se resuelve igual por DOM, y se dice.
        login = icp.get_param("primate_sagui.shot_login") or ""
        password = icp.get_param("primate_sagui.shot_password") or ""
        # Un sitio AJENO no se loguea: nuestras credenciales no son suyas, y el intento sólo
        # gasta tiempo y deja un login fallido en el log de otro.
        if target.get("external"):
            login = password = ""
        if login and password:
            cmd += ["--login", login, "--password", password]

        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=SHOOT_TIMEOUT, text=True)
        except subprocess.TimeoutExpired:
            shutil.rmtree(out_dir, ignore_errors=True)
            return {"ok": False, "shots": [],
                    "errors": [_("la captura excedió %ss") % SHOOT_TIMEOUT]}
        except OSError as e:
            shutil.rmtree(out_dir, ignore_errors=True)
            return {"ok": False, "shots": [], "errors": [_("no pude lanzar el capturador: %s") % e]}

        try:
            data = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except (ValueError, IndexError):
            _logger.warning("Sagui: salida ininteligible del capturador: %s", (proc.stderr or "")[:500])
            shutil.rmtree(out_dir, ignore_errors=True)
            return {"ok": False, "shots": [],
                    "errors": [_("el capturador no devolvió JSON (%s)") % (proc.stderr or "")[:200]]}
        data["_out_dir"] = out_dir
        return data

    @api.model
    def _attach(self, shots, target):
        """Guarda los PNG como ir.attachment ligados al registro de la generación."""
        items = []
        res_id = target.get("design_run_id") or 0
        for shot in shots.get("shots") or []:
            path = shot.get("path")
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            # image_no_postprocess ES OBLIGATORIO ACÁ. Odoo achica toda imagen que entra como
            # adjunto al máximo de `base.image_autoresize_max_px` (1920 por defecto, del lado
            # LARGO). Una captura full-page de 1440x5000 entraba como 553x1920: el revisor
            # opinaba sobre espaciado, alineación y contraste mirando una tira ilegible, y la
            # captura tenía buena pinta en la lista de adjuntos.
            att = self.env["ir.attachment"].sudo().with_context(
                image_no_postprocess=True).create({
                    "name": "verificacion-%s.png" % (shot.get("label") or "").replace(" ", "-"),
                    "raw": raw,
                    "mimetype": "image/png",
                    "res_model": "sagui.design.run" if res_id else False,
                    "res_id": res_id,
                })
            items.append({
                "label": shot.get("label") or att.name, "attachment_id": att.id,
                # Viaja el recorte: una captura cortada a la altura máxima NO muestra el final
                # de la página, y sin decirlo el revisor reporta "falta el footer" sobre un
                # footer que existe y quedó fuera del encuadre.
                "truncated": bool(shot.get("truncated")),
                "page_height": shot.get("page_height") or 0,
            })
        shutil.rmtree(shots.get("_out_dir") or "", ignore_errors=True)
        return items

    # ==================================================================
    #  Hallazgos determinísticos desde las sondas de DOM
    # ==================================================================
    @api.model
    def _dom_findings(self, shots, register=None):
        """Ítems de la rúbrica que se verifican mirando el DOM, no la captura.

        Cada hallazgo lleva source='dom' y method='DOM' para que el reporte al humano pueda
        decirlo explícitamente en vez de dar a entender que se vio en una imagen.

        :param register: clave del registro elegido, si el plan fijó uno. Hay ítems que sólo
            aplican a un registro: el C6 de tech-minimal es el espaciado uniforme.
        """
        findings = []
        by_label = {s.get("label"): (s.get("probes") or {}) for s in shots.get("shots") or []}
        admin = next((p for label, p in by_label.items() if "logueado" in (label or "")), None)
        anon = next((p for label, p in by_label.items() if "anónimo" in (label or "")), None) or {}

        def add(rubric, section, seen, fix, severity="FAIL", breakpoint_=None, fix_kind="regen"):
            findings.append({
                "severity": severity, "rubric": rubric, "section": section,
                "breakpoint": breakpoint_, "seen": seen, "fix": fix,
                "fix_kind": fix_kind, "token": None, "source": "dom", "method": "DOM",
            })

        # --- D4: barra de edición. Se resuelve por DOM porque en v19 el markup nace con d-none
        #     y sólo redirect.js la muestra cuando el usuario no es público.
        session = shots.get("admin_session") or {}
        if not session.get("ok"):
            findings.append({
                "severity": "WARN", "rubric": "D4", "section": "layout",
                "breakpoint": None,
                "seen": _("No hubo sesión logueada (%s), así que D4 no se pudo verificar.")
                        % (session.get("detail") or "sin credenciales"),
                "fix": _("Configurá primate_sagui.shot_login / shot_password con un usuario "
                         "interno para poder verificar la barra de edición."),
                "fix_kind": "regen", "token": None, "source": "dom", "method": "no verificado",
            })
        elif admin is not None:
            if not admin.get("edit_bar_present"):
                add("D4", "layout",
                    _("La página logueada no trae .o_frontend_to_backend_nav: no está usando "
                      "website.layout."),
                    _("Servir la home como website.page sobre website.layout."))
            elif not admin.get("edit_bar_visible"):
                add("D4", "layout",
                    _("La barra de edición está en el DOM pero quedó oculta (d-none): algo "
                      "impide que redirect.js la muestre."),
                    _("Revisar que no haya JS inyectado ni CSS que pise .o_frontend_to_backend_nav."),
                    severity="WARN")

        # --- D1/D2/D3: nav, footer y logo, contra los restos de la demo de Odoo.
        if anon:
            if not anon.get("in_website_layout"):
                add("D1", "layout",
                    _("La página no tiene header ni footer: es una página huérfana."),
                    _("Integrarla en website.layout."))
            demo_nav = anon.get("nav_demo_tells") or []
            if demo_nav:
                add("D1", "header",
                    _("El header todavía muestra ítems de la demo de Odoo: %s") % ", ".join(demo_nav),
                    _("Reemplazar los website.menu por los del diseño."))
            demo_footer = anon.get("footer_demo_tells") or []
            if demo_footer:
                add("D2", "footer",
                    _("El footer conserva contenido de la demo: %s") % ", ".join(demo_footer),
                    _("Reemplazar el footer por el del diseño."))
            logo = (anon.get("logo_src") or "").strip()
            if not logo:
                add("D3", "header", _("No hay logo en el header."),
                    _("Setear website.logo con el logo real."))
            elif "web/image/website/" in logo and "logo" not in logo.lower():
                add("D3", "header",
                    _("El logo parece el placeholder por defecto (%s).") % logo,
                    _("Subir el logo real del cliente y asignarlo a website.logo."),
                    severity="WARN")

        # --- B6: overflow horizontal por breakpoint (medido, no estimado).
        for label, probes in by_label.items():
            if probes.get("horizontal_overflow"):
                width = probes.get("client_width")
                add("B6", "página",
                    _("Hay scroll horizontal: el contenido mide %(s)spx en un viewport de %(c)spx.")
                    % {"s": probes.get("scroll_width"), "c": width},
                    _("Encontrar el bloque que se pasa de ancho y acotarlo con max-width:100%."),
                    breakpoint_=width, fix_kind="regen")

        # --- C6 de tech-minimal: el espaciado entre secciones top-level tiene que usar SIEMPRE
        #     el mismo paso de la escala. Es determinístico y se mide, así que no se le gasta
        #     una llamada al revisor — y sobre todo, se dice CUÁL sección rompe, que es lo que
        #     un "el espaciado es irregular" nunca dice.
        if register == "tech-minimal":
            findings.extend(self._spacing_findings(anon or {}))

        return findings

    @api.model
    def _spacing_findings(self, probes):
        """Costuras entre secciones que no usan el mismo paso de espaciado.

        Se compara contra la costura MÁS FRECUENTE, no contra un valor fijo: la escala la
        eligió el plan y puede ser cualquiera. Lo que el registro exige es que sea una sola.

        Args:
            probes: sondas de una captura (la anónima, que es la que ve el visitante).

        Returns:
            list: hallazgos C6, uno por costura fuera del paso dominante.
        """
        gaps = probes.get("section_gaps") or []
        if len(gaps) < 3:
            # Con dos costuras no hay "paso dominante" que valga: cualquiera de las dos podría
            # ser la buena, y llamar rota a una sería tirar una moneda.
            return []
        seams = [int(g.get("seam") or 0) for g in gaps]
        # LA FRECUENCIA SE CUENTA CON LA MISMA TOLERANCIA QUE DESPUÉS SE PERDONA. Contando
        # valores exactos, 192/193/191 —el mismo paso con el redondeo de un rem— parecen tres
        # pasos distintos, y el chequeo reportaría "no hay ritmo" en una página impecable.
        frecuencias = {v: sum(1 for o in seams if abs(o - v) <= SPACING_TOLERANCE_PX)
                       for v in seams}
        dominante = max(frecuencias, key=lambda v: (frecuencias[v], -abs(v)))
        # SI NINGÚN VALOR SE REPITE, NO HAY PASO DEL QUE APARTARSE. Elegir uno igual —el más
        # chico, el primero— y llamar rotas a las demás es tirar una moneda y después
        # justificarla. Se dice lo que realmente pasa: no hay ritmo, y ahí están los números.
        if frecuencias[dominante] < 2:
            return [{
                "severity": "WARN", "rubric": "C6", "section": "layout", "breakpoint": None,
                "seen": _(
                    "Ninguna costura entre secciones se repite (%s px): el registro "
                    "tech-minimal pide un solo paso de escala y acá no hay ninguno."
                ) % ", ".join(str(v) for v in seams),
                "fix": _(
                    "Definí el paso una sola vez en `.brandsite .sec` y sacá el padding "
                    "vertical propio de todas las secciones."),
                "fix_kind": "regen", "token": None, "source": "dom", "method": "DOM",
            }]
        fuera = [g for g in gaps
                 if abs(int(g.get("seam") or 0) - dominante) > SPACING_TOLERANCE_PX]
        if not fuera:
            return []
        hallazgos = []
        for g in fuera[:6]:
            destino = g.get("to") or g.get("from") or "?"
            hallazgos.append({
                "severity": "WARN", "rubric": "C6", "section": destino, "breakpoint": None,
                "seen": _(
                    "La costura entre «%(de)s» y «%(a)s» deja %(valor)spx de aire y el resto "
                    "de la página usa %(paso)spx. El registro tech-minimal pide un solo paso "
                    "de escala entre secciones top-level."
                ) % {"de": g.get("from") or "?", "a": g.get("to") or "?",
                     "valor": int(g.get("seam") or 0), "paso": dominante},
                "fix": _(
                    "Sacá el padding vertical propio de la sección «%s» y dejá que lo ponga "
                    "`.brandsite .sec`, que es el único lugar donde vive el paso."
                ) % destino,
                "fix_kind": "regen", "token": None, "source": "dom", "method": "DOM",
            })
        return hallazgos
