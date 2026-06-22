# -*- coding: utf-8 -*-
# GREENFIELD (sin PDF): Sagui genera un sitio premium DESDE CERO a partir de (1) la IDENTIDAD de
# marca adjunta (logo) y (2) un brief de negocio. Flujo:
#   disenar_sitio_marca → extrae la paleta del logo + corre el MOTOR de diseño in-module
#     (primate.sagui.design) sembrado con la identidad → DESIGN SYSTEM + plan de secciones →
#     PREVIEW para revisar (paleta, tipografía, secciones, anti-patterns, checklist).
#   generar_sitio_marca → registra una PROPUESTA con token (no construye nada).
#   confirmar <token> → genera el cuerpo 'fiel' (HTML/CSS scopeado .brandsite, copy premium escrito
#     por Sagui guiado por el design system) y lo arma con build_brandsite: TEMA de marca +
#     paleta de la identidad recoloreada SCOPEADA al website, dentro de website.layout, con
#     menús/footer/logo; el backend sigue en /odoo. SIN make_scss_customization ni assets globales.
#
# La inteligencia de UI/UX está BAKEADA build-time (data/design_kb + design_engine): no se gasta
# una llamada a Claude para "recomendar"; la llamada al modelo es sólo para REDACTAR/COMPONER el
# cuerpo siguiendo el sistema ya decidido.
import base64
import json
import logging
import re

from odoo import models, _

_logger = logging.getLogger(__name__)

# Generar un sitio premium completo (HTML de varias secciones + CSS) necesita presupuesto holgado.
# 8192 truncaba el HTML (el CSS se comía el budget). Sonnet 4-6 soporta salida larga.
MAX_OUTPUT_TOKENS = 32000

# ── Parte 2: principios de TASTE/CRAFT destilados (en mis palabras; no texto verbatim de nadie).
# Es el SYSTEM de la llamada de GENERACIÓN: lo que separa "lindo" de "premium".
GREENFIELD_BODY_PROMPT = (
    "Sos un diseñador de producto y dev front senior con criterio editorial. Tu trabajo: COMPONER y "
    "REDACTAR el cuerpo de una landing PREMIUM desde cero, siguiendo AL PIE el DESIGN SYSTEM que te "
    "paso (paleta, tipografía, patrón de secciones, efectos, anti-patterns) y el BRIEF del negocio. "
    "Escribí el copy vos (titulares y textos reales, específicos del negocio, en el idioma del "
    "brief) — nada de lorem ipsum ni 'placeholder'.\n"
    "\n"
    "PRINCIPIOS DE CRAFT (lo que hace que se sienta premium, no sólo correcto):\n"
    "• JERARQUÍA: una sola idea dominante por sección. Un único H1 en el hero. Escala tipográfica "
    "amplia y deliberada (hero gigante con clamp(), cuerpo tranquilo ~16-18px, line-height generoso "
    "1.5-1.7). El contraste de tamaño guía el ojo; si todo grita, nada destaca.\n"
    "• ESPACIADO: el lujo es el espacio en blanco. Ritmo por escala (múltiplos consistentes, p.ej. "
    "8/16/24/48/96px), aire vertical grande entre secciones (pt/pb amplios), nunca apretado. El "
    "espacio agrupa y separa mejor que las líneas.\n"
    "• RESTRICCIÓN: pocos colores, usados con intención. La paleta del design system manda; el "
    "acento es ACENTO (CTAs, detalles), no relleno. Cero gradientes/sombras gratuitos. Si dudás, "
    "quitá.\n"
    "• MICRO-INTERACCIONES: transiciones suaves y RÁPIDAS (150-250ms, easing natural) en hover/focus; "
    "movimiento con propósito (un lift sutil, un fade), nunca decorativo ni que desplace el layout. "
    "Respetá prefers-reduced-motion.\n"
    "• DETALLE/ALINEACIÓN: alineación óptica, grilla coherente, radios y grosores consistentes, "
    "estados hover/focus/active reales. Los detalles invisibles son los que se sienten.\n"
    "• CONTENIDO: copy concreto y con voz (beneficio antes que feature), CTAs accionables ('Agendá "
    "una visita', no 'Enviar'). Imágenes sólo si aportan; si no hay, componé con tipografía y color.\n"
    "\n"
    "COLORES/FUENTES = del DESIGN SYSTEM, vía variables CSS que YA están definidas en .brandsite "
    "(NO las redefinas, NO hardcodees hex de paleta): --p (primary), --a (accent/CTA), --bg "
    "(background), --fg (foreground/texto), --muted, --border, --on-p (texto sobre primary), --on-a "
    "(texto sobre accent). Para fondos de sección usá esas vars; el contraste de texto se ajusta "
    "solo. Las fuentes (heading/body) las pone el tema: NO declares font-family.\n"
    "TU CSS controla SÓLO el LAYOUT (grid/flex, spacing por escala, tamaños con clamp(), object-fit), "
    "TODO bajo `.brandsite .<id-seccion>` (cero selectores globales, cero @import). Cada <section> "
    "lleva su `id` del plan y la MISMA clase (`class=\"<id>\"`). Poné el background de la sección con "
    "las vars (ej. `background:var(--bg)` o `var(--p)` para una banda de marca).\n"
    "CRAFT TÉCNICO OBLIGATORIO: íconos SVG inline (estilo Heroicons/Lucide), NUNCA emojis; "
    "cursor:pointer en clickeables; :hover/:focus con transición 150-250ms; responsive real "
    "(@media 375/768/1024/1440, sin overflow horizontal); foco visible; jerarquía con un solo H1.\n"
    "Links reales (#ancla a otra sección, /contactus, mailto:, https://wa.me/…); nunca href vacío.\n"
    "SALIDA EXACTA (sin ``` ni texto fuera). PRIMERO el HTML COMPLETO con TODAS las secciones del "
    "plan, DESPUÉS el CSS (conciso, sin reglas redundantes):\n"
    "===HTML===\n(<section id=\"..\" class=\"..\">…</section> por CADA sección del plan, en orden)\n"
    "===CSS===\n(reglas .brandsite .<id> …)\n"
)


class SaguiGreenfield(models.AbstractModel):
    _inherit = "primate.sagui.assistant"

    # ---------- tool specs ----------
    def _tool_specs(self):
        specs = super()._tool_specs()
        specs += [
            {
                "name": "disenar_sitio_marca",
                "description": "GREENFIELD (sin PDF): diseña un sitio premium DESDE CERO a partir del "
                               "LOGO/identidad adjunto + un brief. Extrae la paleta del logo y corre "
                               "el motor de diseño interno (bakeado) para devolver un DESIGN SYSTEM "
                               "(paleta, tipografía, patrón de secciones, efectos, anti-patterns, "
                               "checklist) + plan de secciones, como PREVIEW para revisar. Además "
                               "REGISTRA la propuesta de construcción (NO construye aún: el usuario "
                               "confirma después con «confirmar»). Es el ÚNICO paso: NO hay otra tool "
                               "que llamar para proponer. Usalo con el attachment_id del LOGO.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "attachment_id": {"type": "integer", "description": "ir.attachment del logo/identidad."},
                        "brief": {"type": "string", "description": "Brief del negocio en el idioma del "
                                  "usuario: qué es, a quién, tono, qué secciones/contenido quiere."},
                        "seed_en": {"type": "string", "description": "IMPORTANTE: industria + estilo en "
                                    "INGLÉS y en términos de diseño (ej. 'luxury architecture studio, "
                                    "minimal, premium editorial'); la base de conocimiento está en "
                                    "inglés y el match mejora mucho. Derivalo del brief."},
                        "name": {"type": "string", "description": "Nombre/título del sitio (opcional)."},
                        "sections": {"type": "array", "items": {"type": "string"},
                            "description": "Si el usuario nombra las secciones que quiere (ej. "
                            "['Hero','Sobre','Servicios','Proyectos','Contacto']), pasalas acá en su "
                            "idioma; mandan sobre el patrón del motor. Omitir para usar el patrón "
                            "recomendado por el rubro."},
                        "company_name": {"type": "string", "description": "Si el usuario quiere una "
                            "COMPAÑÍA NUEVA para este sitio, su nombre (ej. 'Primate'). Al construir "
                            "se crea la res.company y un website propio de esa compañía, sin tocar el "
                            "sitio/compañía actuales. Omitir para usar la compañía/sitio actual."},
                    },
                    "required": ["attachment_id", "brief"],
                },
            },
            {
                "name": "estado_construccion",
                "description": "Devuelve el estado REAL de las construcciones de sitio recientes del "
                               "usuario en este chat (processing=en proceso, done=listo, error). "
                               "USALO SIEMPRE que el usuario pregunte si su sitio ya está listo, en "
                               "lugar de adivinar o afirmar que está hecho.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]
        return specs

    def _system_prompt(self):
        prompt = super()._system_prompt()
        prompt += (
            "\n\nSITIO WEB PREMIUM DESDE LA IDENTIDAD (greenfield, sin PDF): cuando el usuario adjunta "
            "su LOGO/identidad y quiere un sitio nuevo a partir de un brief, el flujo es de UN SOLO "
            "PASO de tu lado:\n"
            "1) Llamá `disenar_sitio_marca` UNA vez, con: attachment_id del LOGO (una IMAGEN png/jpg; "
            "si además adjuntan PDFs o ilustraciones vectoriales o fuentes, IGNORALOS para esto y usá "
            "el LOGO — NO uses analizar_sitio acá), brief, seed_en (industria+estilo EN INGLÉS, "
            "derivado del brief: la KB de diseño está en inglés), sections (si el usuario las nombró) y "
            "company_name (si quiere una compañía nueva). Esa tool diseña Y registra la propuesta de "
            "construcción en el mismo paso.\n"
            "2) Mostrá el DESIGN SYSTEM (paleta/tipografía/secciones/anti-patterns) UNA sola vez y "
            "terminá diciéndole al usuario que, si le gusta, responda exactamente «confirmar». NO "
            "llames ninguna otra tool. NO vuelvas a diseñar salvo que pida cambios.\n"
            "REGLAS DURAS:\n"
            "• TOKENS: NUNCA escribas un token en tu mensaje (no lo sabés con certeza y lo inventás, "
            "rompiendo el flujo). El sistema le muestra al usuario el token exacto en un mensaje "
            "aparte; vos sólo decís «respondé confirmar».\n"
            "• NO afirmes que el sitio se construyó, ni que se creó la compañía, ni inventes errores "
            "de permisos: la construcción es ASÍNCRONA (2-3 min) y el SISTEMA postea solo el resultado "
            "real al terminar. Si el usuario pregunta si está listo, llamá `estado_construccion` y "
            "respondé según eso (processing → 'sigue en proceso, te aviso solo'; done → listo con su "
            "URL; error → contás el error). No inventes URLs ni éxitos.\n"
            "• Si una compañía nueva se crea, avisás que para verla hay que cambiar de sitio en la app "
            "Website. Distinguí: PDF de diseño → analizar_sitio; logo + brief desde cero → "
            "disenar_sitio_marca."
        )
        return prompt

    # ---------- dispatch ----------
    def _run_tool(self, name, args, user, channel=None, proposals=None):
        if name == "disenar_sitio_marca":
            return self._tool_disenar_marca(args, user, channel, proposals)
        if name == "generar_sitio_marca":  # compat: si el modelo igual la llama, propone
            return self._propose_greenfield(args, user, channel, proposals, name=args.get("name"))
        if name == "estado_construccion":
            return self._tool_estado_construccion(channel, user)
        return super()._run_tool(name, args, user, channel=channel, proposals=proposals)

    def _tool_estado_construccion(self, channel, user):
        if not channel:
            return _("No hay chat para consultar el estado.")
        recs = self.env["primate.sagui.pending.write"].sudo().search([
            ("channel_id", "=", channel.id), ("user_id", "=", user.id),
            ("operation", "in", ("website", "website_greenfield")),
        ], order="create_date desc", limit=5)
        estados = {"pending": "esperando confirmación", "processing": "EN PROCESO (todavía no está)",
                   "done": "LISTO", "error": "falló", "cancelled": "cancelado", "expired": "expirado"}
        return json.dumps([{"token": r.token, "estado": estados.get(r.state, r.state),
                            "detalle": r.result_info or ""} for r in recs], ensure_ascii=False)

    def _execute_pending(self, channel, author, pending):
        if pending.operation == "website_greenfield":
            return self._execute_greenfield(channel, author, pending)
        return super()._execute_pending(channel, author, pending)

    # ======================================================================
    #  Tool: disenar_sitio_marca → identidad + brief → DESIGN SYSTEM (preview)
    # ======================================================================
    def _tool_disenar_marca(self, args, user, channel=None, proposals=None):
        env = self.env(user=user.id)
        att = self._get_design_attachment(env, args.get("attachment_id"))
        if isinstance(att, str):
            return att
        brief = (args.get("brief") or "").strip()
        seed = (args.get("seed_en") or "").strip() or brief
        if not seed:
            return _("Necesito un brief (qué es el negocio) para diseñar el sitio.")

        brand_palette = self._extract_brand_palette(att)
        logo_url = "/web/image/%s" % att.id
        design = self.env["primate.sagui.design"].recommend(seed, brand_palette=brand_palette)
        plan = self._section_plan(design, brief, requested=args.get("sections"))

        company_name = (args.get("company_name") or "").strip() or None
        title = (args.get("name") or "").strip() or company_name \
            or design.get("category") or (att.name or "Inicio").rsplit(".", 1)[0]
        stash = {
            "_greenfield": True, "design_system": design, "brief": brief, "seed": seed,
            "plan": plan, "title": title, "logo_url": logo_url, "company_name": company_name,
        }
        att.sudo().write({
            "primate_landing_title": title,
            "primate_landing_meta": json.dumps(stash, ensure_ascii=False),
            "primate_landing_html": False, "primate_landing_css": False,
        })

        preview = self.env["primate.sagui.design"].preview_text(design)
        plan_txt = "\n".join("  %s. %s" % (i + 1, s["label"]) for i, s in enumerate(plan))
        swatches = brand_palette.get("colors") if brand_palette else None
        # Registrar YA la propuesta de construcción (MISMO paso): el modelo llama disenar de forma
        # confiable pero NO siempre una segunda tool — así no hace falta. Queda 1 propuesta activa y
        # el usuario sólo dice «confirmar».
        propuesta = None
        if channel is not None:
            propuesta = self._propose_greenfield({"attachment_id": att.id, "name": title},
                                                 user, channel, proposals)
        return json.dumps({
            "preview_markdown": preview,
            "plan_secciones": plan_txt,
            "paleta_extraida_del_logo": swatches or "no detectada (uso paleta del rubro)",
            "propuesta_registrada": bool(propuesta),
            "nota": "Mostrá el design system (paleta/tipografía/secciones/anti-patterns) UNA sola vez. "
                    "YA quedó registrada la propuesta de construcción: NO llames ninguna otra tool. "
                    "Terminá diciéndole al usuario que, si le gusta, responda exactamente «confirmar» "
                    "para construir (NO escribas ningún token; el sistema lo muestra). Si quiere "
                    "cambios, que los diga y recién ahí volvés a diseñar.",
        }, ensure_ascii=False)

    # ---------- plan de secciones a partir del patrón del design system + brief ----------
    def _section_plan(self, design, brief, requested=None):
        # Si el usuario nombró las secciones, mandan; si no, se usa el patrón del motor.
        if requested and isinstance(requested, list):
            labels = [str(s).strip() for s in requested if str(s).strip()]
        else:
            raw = (design.get("pattern") or {}).get("sections") or "Hero > Features > CTA"
            # El patrón viene como "1. Hero ..., 2. Value prop, ..." o "Hero > Features > CTA".
            parts = re.split(r"\s*(?:\d+\.\s*|>|,|→)\s*", raw)
            labels = [p.strip() for p in parts if p.strip()]
        if not labels:
            labels = ["Hero", "Features", "CTA"]
        plan = []
        for i, lab in enumerate(labels[:7]):
            sid = re.sub(r"[^a-z0-9]+", "-", lab.lower()).strip("-") or ("sec-%s" % i)
            plan.append({"id": ("s-%s-%s" % (i, sid[:24])).strip("-"), "label": lab})
        return plan

    # ======================================================================
    #  Tool: generar_sitio_marca → PROPONE (no construye)
    # ======================================================================
    def _propose_greenfield(self, args, user, channel, proposals, name=None):
        if not channel:
            return _("No puedo proponer fuera de un canal de chat.")
        env = self.env(user=user.id)
        att = self._get_design_attachment(env, args.get("attachment_id"))
        if isinstance(att, str):
            return att
        stash = self._safe_json(att.sudo().primate_landing_meta) or {}
        if not stash.get("_greenfield") or not stash.get("design_system"):
            return _("Todavía no diseñé ese sitio. Usá disenar_sitio_marca primero.")
        name = (name or "").strip() or stash.get("title")
        summary = self._summary_greenfield(stash, name)
        # Dejar UNA sola propuesta activa: cancelar las anteriores de este chat/usuario, así el
        # usuario puede confirmar con sólo "confirmar" (sin token) y no hay ambigüedad ni tokens viejos.
        self.env["primate.sagui.pending.write"].sudo().search([
            ("channel_id", "=", channel.id), ("user_id", "=", user.id),
            ("state", "=", "pending"),
        ]).write({"state": "cancelled"})
        token = __import__("secrets").token_hex(3)
        pending = self.env["primate.sagui.pending.write"].sudo().create({
            "token": token, "channel_id": channel.id, "user_id": user.id,
            "operation": "website_greenfield", "model_name": "website.page",
            "values_json": json.dumps({"attachment_id": att.id, "name": name}, ensure_ascii=False),
            "summary": summary, "state": "pending",
        })
        if proposals is not None:
            proposals.append(pending.id)
        # IMPORTANTE para el modelo: NO repitas ningún token en tu respuesta (no lo conocés con
        # certeza). El sistema le muestra al usuario el token exacto en un mensaje aparte. Decile
        # simplemente que responda «confirmar» para construir.
        return _(
            "PROPUESTA REGISTRADA para construir «%(name)s» desde la identidad (queda 1 sola "
            "propuesta activa). NO se construyó nada todavía. En tu respuesta NO escribas ningún "
            "token: decile al usuario que responda exactamente «confirmar» para construir (o "
            "«cancelar»). El build corre en background ~2-3 min y el sistema avisa solo al terminar. "
            "Resumen: %(summary)s"
        ) % {"name": name, "summary": summary}

    # ======================================================================
    #  Ejecución real (al confirmar): genera el cuerpo y construye
    # ======================================================================
    def _execute_greenfield(self, channel, author, pending):
        env = self.env(user=author.id)
        try:
            data = json.loads(pending.values_json or "{}")
            att = self._get_design_attachment(env, data.get("attachment_id"))
            if isinstance(att, str):
                raise ValueError(att)
            stash = self._safe_json(att.sudo().primate_landing_meta) or {}
            design = stash.get("design_system") or {}
            if not design:
                raise ValueError(_("perdí el diseño; volvé a correr disenar_sitio_marca"))
            title = (data.get("name") or "").strip() or stash.get("title") or "Inicio"
            plan = stash.get("plan") or []
            logo_url = stash.get("logo_url")

            # FASE 1 — generación con el LLM (lo CARO). Se REUSA si ya se generó (un reintento NO
            # vuelve a gastar tokens) y se PERSISTE + COMMIT antes de tocar la DB del sitio: si algo
            # posterior se revierte, la generación queda guardada y no se desperdicia.
            body = att.sudo().primate_landing_html or ""
            css = att.sudo().primate_landing_css or ""
            if not body:
                # Acción para costeo/drill-down: la generación del sitio (lo CARO).
                action = env["primate.ai.action"]._open(
                    "generar_sitio", title or "Sitio", ref="ir.attachment,%s" % att.id)
                env = env(context={**env.context, "ai_action_id": action.id})
                body, css = self._render_greenfield_body(env, att, design, stash.get("brief", ""), plan)
                if not body:
                    raise ValueError(_("no pude generar el cuerpo del sitio; reintentá"))
                att.sudo().write({"primate_landing_html": body, "primate_landing_css": css})
                self.env.cr.commit()

            # FASE 2 — build del sitio (compañía/website/página). NO toca discuss_channel, así que NO
            # choca con la actividad del usuario en el chat. Commit para PERSISTIR el sitio aunque el
            # aviso (fase 3) falle por concurrencia. (Era el bug: el aviso chocaba con el chat en uso
            # y revertía TODO el build + desperdiciaba los tokens.)
            schema = {
                "nav": [{"label": s["label"], "target": "#" + s["id"]} for s in plan[1:6]],
                "footer": {"brand": title, "lines": [stash.get("brief", "")[:120]], "contact": {}},
                "logo": logo_url,
            }
            fonts = self._fonts_from_design(design)
            company = target_website = None
            if stash.get("company_name"):
                company, target_website = self._ensure_company_website(env, stash["company_name"])
            result = env["primate.website.builder"].build_brandsite(
                body, css, title, schema=schema, logo_url=logo_url, lang=None,
                theme="theme_design_pipa", palette=design.get("colors"), fonts=fonts,
                website=target_website)
            pending.write({"state": "done", "result_info": "greenfield page_id=%s url=%s company=%s" % (
                result.get("page_id"), result.get("url"), company.id if company else "-")})
            self.env.cr.commit()

            # FASE 3 — aviso en el chat: best-effort, transacción corta y con reintentos (postear en un
            # canal en uso puede chocar por serialización, pero el sitio YA está guardado: el aviso no
            # puede tumbar el build). Si igual falla, el usuario lo ve con estado_construccion (done).
            if company:
                extra = _("Creé la compañía «%(co)s» y su sitio «%(site)s» (website propio). Para "
                          "verlo, en la app Website cambiá al sitio «%(site)s» (selector arriba a la "
                          "derecha) y entrá a %(url)s. No toqué el sitio ni la compañía anteriores.") % {
                    "co": company.name, "site": result.get("website_name"), "url": result.get("url")}
            else:
                extra = _("La homepage anterior quedó guardada (%(prev)s): si no te gusta, pedime "
                          "revertir.") % {"prev": result.get("prev_homepage_url") or "/"}
            self._safe_post(channel, _(
                "✅ Listo: generé «%(site)s» en %(url)s desde tu identidad — sistema de diseño "
                "%(cat)s, paleta de marca aplicada, tema recoloreado (header/footer/menús/logo); el "
                "backend sigue en /odoo. %(extra)s"
            ) % {"site": result.get("website_name"), "url": result.get("url"),
                 "cat": design.get("category", "-"), "extra": extra})
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui greenfield: falló la construcción %s", pending.token)
            self.env.cr.rollback()
            try:
                pending.write({"state": "error", "result_info": str(e)[:200]})
                self.env.cr.commit()
            except Exception:  # noqa: BLE001
                self.env.cr.rollback()
            self._safe_post(channel, _("❌ No pude construir el sitio (%(token)s): %(err)s") % {
                "token": pending.token, "err": e})

    def _safe_post(self, channel, text, tries=4):
        """Postea en el chat tolerando conflictos de serialización (el canal puede estar en uso por el
        usuario). El sitio ya está commiteado, así que el aviso es best-effort: reintenta y, si no lo
        logra, sólo loguea (el usuario igual lo ve preguntando el estado)."""
        import time
        for _i in range(tries):
            try:
                self._post_bot_reply(channel, text)
                self.env.cr.commit()
                return True
            except Exception:  # noqa: BLE001
                self.env.cr.rollback()
                try:
                    time.sleep(0.7)
                except Exception:  # noqa: BLE001
                    pass
        _logger.warning("Sagui: no pude postear el aviso final; el sitio igual quedó construido.")
        return False

    # ---------- compañía nueva + su website (opcional) ----------
    def _ensure_company_website(self, env, company_name):
        """Crea (o reutiliza por nombre) una res.company y un website PROPIO de esa compañía.
        Se ejecuta como el usuario (necesita permisos de administración). Idempotente por nombre.
        Devuelve (company, website)."""
        Company = env["res.company"]
        company = Company.search([("name", "=", company_name)], limit=1)
        if not company:
            company = Company.create({"name": company_name})
        # Sin esto, la compañía nueva NO aparece en el selector del usuario (ni su website): hay que
        # sumarla a sus compañías permitidas. sudo: tocar res.users es infraestructura del build.
        user = env.user
        if company not in user.company_ids:
            user.sudo().write({"company_ids": [(4, company.id)]})
        Website = env["website"]
        website = Website.search([("company_id", "=", company.id)], limit=1)
        if not website:
            website = Website.create({"name": company_name, "company_id": company.id})
        return company, website

    # ---------- generación del cuerpo (LLM compone+redacta siguiendo el design system) ----------
    def _render_greenfield_body(self, env, att, design, brief, plan):
        content = []
        # Logo como contexto visual (si es raster) para matchear el vibe de marca.
        img_b64, media = self._logo_image_b64(att)
        if img_b64:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": media, "data": img_b64}})
        compact = {
            "style": design.get("style", {}).get("name"),
            "style_keywords": design.get("style", {}).get("keywords"),
            "palette_roles": design.get("colors"),
            "typography": design.get("typography"),
            "key_effects": design.get("key_effects"),
            "anti_patterns": design.get("anti_patterns"),
            "checklist": design.get("checklist"),
            "ux_rules": design.get("ux_rules"),
            "pattern": design.get("pattern"),
            "secciones_plan": plan,
        }
        content.append({"type": "text", "text":
            "BRIEF del negocio (idioma del copy):\n%s\n\nDESIGN SYSTEM (seguilo al pie):\n%s\n\n"
            "Generá el cuerpo premium (===CSS=== y ===HTML===), una <section> por cada ítem de "
            "secciones_plan (usando su `id`/clase), con copy real del negocio." % (
                brief, json.dumps(compact, ensure_ascii=False))})
        try:
            d = env["primate.ai.connector"].call(
                [{"role": "user", "content": content}],
                system=GREENFIELD_BODY_PROMPT, max_tokens=MAX_OUTPUT_TOKENS, timeout=300)
        except Exception as e:  # noqa: BLE001
            _logger.warning("Sagui greenfield: falló el render del cuerpo: %s", e)
            return "", ""
        blocks = self._parse_blocks("".join(b.get("text", "") for b in (d.get("content") or [])
                                            if b.get("type") == "text"))
        return blocks.get("html", "").strip(), blocks.get("css", "").strip()

    # ======================================================================
    #  Identidad: extracción de paleta del logo (PIL) + imagen para contexto
    # ======================================================================
    def _extract_brand_palette(self, att):
        """Extrae la paleta de marca del logo. Raster (PNG/JPG/WEBP) → colores dominantes vía PIL;
        SVG → hex de los fills. Devuelve {primary, accent, colors:[...]} o {} si no se puede."""
        try:
            raw = att.raw
        except Exception:  # noqa: BLE001
            return {}
        name = (att.name or "").lower()
        mimetype = (att.mimetype or "").lower()
        if "svg" in mimetype or name.endswith(".svg"):
            return self._palette_from_svg(raw)
        return self._palette_from_raster(raw)

    def _palette_from_raster(self, raw):
        try:
            import io
            from PIL import Image
        except Exception:  # noqa: BLE001
            return {}
        try:
            im = Image.open(io.BytesIO(raw))
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im).convert("RGB")
            else:
                im = im.convert("RGB")
            im.thumbnail((200, 200))
            q = im.quantize(colors=12, method=Image.MEDIANCUT)
            pal = q.getpalette() or []
            counts = sorted(q.getcolors() or [], reverse=True)  # [(count, idx), ...]
            ranked = []
            for cnt, idx in counts:
                r, g, b = pal[idx * 3], pal[idx * 3 + 1], pal[idx * 3 + 2]
                ranked.append((cnt, (r, g, b)))
            return self._rank_palette(ranked)
        except Exception:  # noqa: BLE001
            _logger.warning("No pude extraer paleta del logo (raster)", exc_info=True)
            return {}

    @staticmethod
    def _palette_from_svg(raw):
        try:
            txt = raw.decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return {}
        hexes = re.findall(r"#[0-9a-fA-F]{6}", txt)
        seen, colors = set(), []
        for h in hexes:
            h = h.lower()
            if h not in seen:
                seen.add(h)
                colors.append(h)
        colors = [c for c in colors if c not in ("#ffffff", "#000000")] or colors
        if not colors:
            return {}
        out = {"colors": colors[:6], "primary": colors[0]}
        if len(colors) > 1:
            out["accent"] = colors[1]
        return out

    def _rank_palette(self, ranked):
        """De [(count,(r,g,b))...] elige primary/accent: el más DOMINANTE no-neutro como primary y
        el más SATURADO distinto como accent. Filtra casi-blancos/negros (suelen ser fondo)."""
        def sat(rgb):
            r, g, b = rgb
            mx, mn = max(rgb), min(rgb)
            return 0 if mx == 0 else (mx - mn) / mx
        def lum(rgb):
            r, g, b = rgb
            return 0.299 * r + 0.587 * g + 0.114 * b
        def hexv(rgb):
            return "#%02x%02x%02x" % rgb
        colored = [(cnt, rgb) for cnt, rgb in ranked
                   if not (lum(rgb) > 242 or lum(rgb) < 14) and sat(rgb) > 0.12]
        all_hex = [hexv(rgb) for _cnt, rgb in ranked]
        if not colored:  # logo monocromo/neutro → tomamos el tono más oscuro como primary
            darks = sorted(ranked, key=lambda x: lum(x[1]))
            if not darks:
                return {}
            return {"colors": all_hex[:6], "primary": hexv(darks[0][1])}
        primary = max(colored, key=lambda x: x[0])[1]          # más dominante
        # accent = el color "pop": vivacidad = saturación × luminosidad (evita que un oscuro muy
        # saturado, ej. un navy, le gane al dorado/brillante que es el verdadero acento).
        accent_cands = sorted(colored, key=lambda x: sat(x[1]) * lum(x[1]), reverse=True)
        accent = next((rgb for _c, rgb in accent_cands if rgb != primary), primary)
        out = {"colors": all_hex[:6], "primary": hexv(primary)}
        if accent != primary:
            out["accent"] = hexv(accent)
        return out

    def _logo_image_b64(self, att):
        """Logo raster → (base64, media_type) para mandar como contexto visual, REDIMENSIONADO
        (los logos suelen ser de varios MB y la API limita el tamaño de imagen). SVG → (None, None)."""
        name = (att.name or "").lower()
        mimetype = (att.mimetype or "").split(";")[0].strip().lower()
        if "svg" in mimetype or name.endswith(".svg"):
            return None, None
        try:
            raw = att.raw
        except Exception:  # noqa: BLE001
            return None, None
        if not raw:
            return None, None
        try:
            import io
            from PIL import Image
            im = Image.open(io.BytesIO(raw))
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
                im = Image.alpha_composite(bg, im).convert("RGB")
            else:
                im = im.convert("RGB")
            im.thumbnail((768, 768))
            out = io.BytesIO()
            im.save(out, "PNG")
            return base64.b64encode(out.getvalue()).decode("ascii"), "image/png"
        except Exception:  # noqa: BLE001
            # Fallback: mandar crudo sólo si es chico; si no, sin imagen (igual hay paleta+brief).
            if len(raw) <= 3_000_000:
                media = mimetype if mimetype.startswith("image/") else "image/png"
                return base64.b64encode(raw).decode("ascii"), media
            return None, None

    # ---------- fuentes del design system → estructura que entiende el builder ----------
    def _fonts_from_design(self, design):
        t = design.get("typography") or {}
        google = self._css2_url(t.get("css_import") or "") or (t.get("google_fonts_url") or "")
        heading = t.get("heading") or "Inter"
        body = t.get("body") or "Inter"
        return {
            "display": {"family": "'%s', sans-serif" % heading, "google": google},
            "body": {"family": "'%s', sans-serif" % body, "google": google},
        }

    @staticmethod
    def _css2_url(css_import):
        """Saca la URL css2 de un '@import url('https://fonts.googleapis.com/css2?...');'."""
        m = re.search(r"https://fonts\.googleapis\.com/css2\?[^'\")]+", css_import or "")
        return m.group(0) if m else ""

    # ---------- resumen para la propuesta ----------
    def _summary_greenfield(self, stash, name):
        design = stash.get("design_system") or {}
        c = design.get("colors") or {}
        t = design.get("typography") or {}
        plan = stash.get("plan") or []
        secs = ", ".join(s["label"] for s in plan)
        return "\n".join([
            _("Construir «%(name)s» como homepage, generado DESDE CERO con la identidad de marca.") % {"name": name},
            _("Rubro/sistema: %(cat)s · estilo %(style)s") % {
                "cat": design.get("category", "-"), "style": (design.get("style") or {}).get("name", "-")},
            _("Paleta: primary %(p)s / accent %(a)s · tipografía %(h)s / %(b)s") % {
                "p": c.get("primary", "-"), "a": c.get("accent", "-"),
                "h": t.get("heading", "-"), "b": t.get("body", "-")},
            _("Secciones: %s") % (secs or "-"),
            (_("Se CREA la compañía «%(co)s» y un website propio para ella (no toca la compañía/"
               "sitio actuales).") % {"co": stash["company_name"]} if stash.get("company_name")
             else _("Se usa la compañía/sitio actual; la homepage anterior se guarda para revertir.")),
            _("Tema recoloreado con tu paleta (header/footer/menús/logo); backend en /odoo."),
        ])
