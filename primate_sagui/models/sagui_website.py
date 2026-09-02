# -*- coding: utf-8 -*-
# INTERPRETAR un PDF/imagen de diseño a un SCHEMA revisable (sistema de diseño + secciones + copy
# [anotaciones filtradas] + imágenes + snippet/o_cc/layout por sección). En AMBOS modos se aplica el
# TEMA de marca, que es dueño de header/footer/paleta/tipografía/menús/logo (no se re-pelean nunca).
# El CUERPO de la home es CONFIGURABLE por generación vía `render_mode` en generar_sitio:
#   • 'fiel'     → cuerpo = HTML/CSS a medida scopeado (.brandsite), máxima fidelidad al PDF, los
#                  colores salen del tema (clases o_cc + vars --o-color-N). Editable por código.
#   • 'editable' → cuerpo = snippets NATIVOS desde el schema, estilados por el tema. 100% editable
#                  en el builder; fidelidad acotada a lo que dan los snippets.
# Default 'fiel'. Sigue el skill `odoo-site-from-design`. Confirmación antes de construir.
import base64
import json
import logging
import re

from odoo import fields, models, _

_logger = logging.getLogger(__name__)

MAX_PDF_PAGES = 8
RENDER_MAX_PX = 2200
MAX_EMBEDDED_IMAGES = 12
MIN_IMG_SIDE = 80
MIN_IMG_BYTES = 2048
MAX_OUTPUT_TOKENS = 8192

# Catálogo de snippets nativos soportados (debe coincidir con los builders de primate.website.builder).
#   s_banner         → hero: eyebrow + titular grande + bajada + botón + imagen.
#   s_text_image     → texto a un lado + imagen al otro (titular, párrafos, botón).
#   s_features       → grilla de 2-4 features (ícono FontAwesome + título + texto), con intro.
#   s_image_gallery  → carrusel/galería de imágenes (portfolio).
#   s_call_to_action → banda CTA: titular + bajada + botón (suele ir en o_cc oscuro).
#   s_text_block     → bloque de 1-2 párrafos de texto (sobre/manifiesto), sin imagen.
SNIPPETS = ("s_banner", "s_text_image", "s_features", "s_image_gallery",
            "s_call_to_action", "s_text_block")
COLOR_COMBOS = ("o_cc1", "o_cc2", "o_cc3", "o_cc4", "o_cc5")

# Una sola llamada de visión: PDF → SCHEMA de snippets nativos. El estilo (paleta/tipografía) NO
# va acá: lo aporta el TEMA de marca. Acá sólo: estructura (qué snippet), copy real, imagen y o_cc.
SCHEMA_PROMPT = (
    "Sos director de arte + arquitecto de contenido de Odoo. A partir del RENDER de las páginas "
    "(imágenes) + el texto + la lista de URLs de imágenes EXTRAÍDAS del PDF, traducí el diseño a un "
    "SCHEMA de SNIPPETS NATIVOS de Odoo 19. Conservá el idioma del diseño. NO generes HTML ni CSS.\n"
    "IMPORTANTE: NO elijas paleta ni fuentes (las pone el tema de marca). Tu trabajo es la "
    "ESTRUCTURA: por cada sección del PDF, elegí el snippet nativo MÁS PARECIDO y volcá su copy "
    "REAL (tal cual, en su idioma), su imagen y su color-combination.\n"
    "SNIPPETS disponibles (elegí el que MEJOR reproduce el LAYOUT real de esa sección):\n"
    " • s_banner: HERO. Primera sección con marca/titular grande. Campos: eyebrow, headline, "
    "subheadline, button{label,href}, image.\n"
    " • s_text_image: texto a un lado + imagen al otro. Campos: headline, body[párrafos], "
    "list[ítems] (si el texto es una LISTA), button(opcional), image.\n"
    " • s_features: grilla de servicios/beneficios CON ÍCONOS. Campos: headline, subheadline, "
    "features[{icon(nombre FontAwesome SIN 'fa-', ej 'paper-plane-o','bullseye','camera'),title,text}] (2-4).\n"
    " • s_image_gallery: galería/portfolio de imágenes. Campos: headline(opcional), images[urls].\n"
    " • s_call_to_action: banda CTA de cierre. Campos: headline, subheadline, button{label,href}.\n"
    " • s_text_block: bloque de texto/lista (sobre/manifiesto/listado), sin imagen. Campos: "
    "headline(opcional), body[párrafos], list[ítems].\n"
    "LISTAS (importante para fidelidad): si una sección es un LISTADO de servicios/ítems (varias "
    "líneas cortas, ej. 'identidade visual / logotipos / branding / …' o 'impressões A5 / flyer / "
    "folder / …'), volcá cada ítem en `list[]` — NO los juntes en un párrafo. Elegí s_features SÓLO "
    "si el PDF muestra un ÍCONO por ítem; si es una lista de texto al lado de una foto, usá "
    "s_text_image con `list`; si es sólo lista, s_text_block con `list`. SIEMPRE poné un `headline`.\n"
    "COLOR-COMBINATIONS — REGLA DE FIDELIDAD: mirá el FONDO REAL de cada sección en el PDF y "
    "matcheálo. Convención del tema: o_cc1=blanco, o_cc2=crema claro, o_cc3=OSCURO, o_cc4=acento "
    "(naranja), o_cc5=OSCURO. Entonces: fondo blanco→o_cc1, fondo claro/crema→o_cc2, fondo "
    "oscuro/negro→o_cc3 o o_cc5. Usá o_cc4 (naranja) SÓLO si el fondo de esa sección es LITERALMENTE "
    "el color de acento en el PDF — NO uses naranja para una banda que en el PDF es negra (ej. el "
    "cierre 'QUE COMUNICA SUA MARCA' suele ser OSCURO → o_cc5, no o_cc4). No inventes el ritmo: copialo.\n"
    "IMÁGENES: elegí de las URLs extraídas la que está físicamente en/junto a esa sección (por "
    "ADYACENCIA en el PDF). Para s_image_gallery juntá varias. Si no hay imagen, null.\n"
    "FILTRÁ las ANOTACIONES del diseñador (textos tipo 'aquí va el logo', 'fondo #hex', medidas, "
    "'placeholder', notas de revisión, 'algo con esta letra'): NO son copy del sitio, descartalas.\n"
    "BOTONES: href real cuando se infiera (#ancla, /contactus, mailto:, https://wa.me/…); si no, '#'.\n"
    "LAYOUT: en `layout` describí el LAYOUT visual concreto de la sección (ej. 'lista vertical en "
    "negrita a la derecha de una foto a la izquierda', 'titular gigante centrado sobre banda "
    "oscura') — lo usa el modo de máxima fidelidad para reproducir la composición exacta.\n"
    "NAV (label + ancla #id de la sección). FOOTER (marca, líneas tipo 'Brand Manager / Assessoria "
    "de Comunicação', contacto real: email/whatsapp/instagram). LOGO: cuál URL extraída es el logo.\n"
    "SALIDA: EXCLUSIVAMENTE un JSON (sin ```), formato:\n"
    '{"title":str,"language":str,"logo":url|null,'
    '"nav":[{"label":str,"target":"#id"}],'
    '"footer":{"brand":str,"lines":[str],"contact":{"email":str|null,"whatsapp":str|null,"instagram":str|null}},'
    '"sections":[{"id":str,"label":str,"layout":str,'
    '"snippet":"s_banner|s_text_image|s_features|s_image_gallery|s_call_to_action|s_text_block",'
    '"o_cc":"o_cc1|o_cc2|o_cc3|o_cc4|o_cc5",'
    '"copy":{"eyebrow":str|null,"headline":str|null,"subheadline":str|null,"body":[str],"list":[str],'
    '"button":{"label":str,"href":str}|null,'
    '"features":[{"icon":str,"title":str,"text":str}]},'
    '"image":url|null,"images":[url]}]}'
)

# Modo 'fiel': UNA llamada genera el CUERPO custom (HTML semántico + CSS de layout scopeado a
# .brandsite) reproduciendo el PDF. Los COLORES salen del TEMA (clases o_cc + vars --o-color-N); el
# CSS custom controla SOLO layout/espaciado/escala. Sin header/footer/nav/fuentes/@import.
FIEL_BODY_PROMPT = (
    "Sos dev front senior. Reproducí FIELMENTE el cuerpo de esta landing a partir del RENDER del PDF "
    "(imágenes) + el SCHEMA de secciones que te paso (id, layout, copy, imagen, o_cc). Conservá el "
    "idioma. El contenido va DENTRO de un contenedor `.brandsite` (NO generes <html>/<head>/<header>/"
    "<footer>/<nav>/<style>/<script>; SIN @import).\n"
    "COLORES Y FUENTES = del TEMA, no los inventes: poné en cada `<section>` la clase de su "
    "color-combination del schema (`class=\"o_cc o_ccN ...\"`) y usá las vars del tema cuando "
    "necesites un color (`var(--o-color-1)` acento, `--o-color-2/3/5` etc.) y las clases de botón del "
    "tema (`btn btn-primary`, `btn btn-secondary`). NO declares `font-family` (heredá del tema). NO "
    "hardcodees hex de paleta.\n"
    "TU CSS controla SOLO el LAYOUT (grid/flex, espaciado, tamaños, escala tipográfica con clamp(), "
    "object-fit de imágenes), TODO bajo `.brandsite .<id-seccion>` (cero selectores globales). "
    "Imágenes: usá las URLs del schema (`/web/image/<id>`), `max-width:100%;height:auto` u object-fit. "
    "Reproducí el layout de cada sección según su `layout`. Links reales (#ancla, /contactus, "
    "mailto:, wa.me); nunca href='#vacío'.\n"
    "CRAFT: íconos SVG inline (Heroicons/Lucide), NUNCA emojis; clickeables con cursor:pointer + "
    ":hover transition 150–300ms; responsive (@media 375/768/1024/1440, sin overflow); foco visible; "
    "prefers-reduced-motion. NADA de gradientes porque sí ni stock genérico.\n"
    "SALIDA EXACTA (sin ``` ni texto fuera):\n===CSS===\n(reglas .brandsite .<id> …)\n===HTML===\n"
    "(<section id=\"..\" class=\"o_cc o_ccN ..\">…</section> por cada sección, en orden)\n"
)

_FENCE_RE = re.compile(r"^\s*```(?:json|html|css)?\s*|\s*```\s*$", re.IGNORECASE)
_BLOCK_RE = re.compile(r"^===\s*(CSS|HTML)\s*===\s*$", re.IGNORECASE | re.MULTILINE)
RENDER_MODES = ("fiel", "editable")


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    # Stash de lo generado por analizar_sitio (lo sirve generar_sitio al confirmar).
    primate_landing_html = fields.Text("Contenido brandsite (Sagui)", copy=False)
    primate_landing_css = fields.Text("CSS brandsite (Sagui)", copy=False)
    primate_landing_title = fields.Char("Título (Sagui)", copy=False)
    primate_landing_meta = fields.Text("Tokens/meta (Sagui)", copy=False)


class SaguiWebsite(models.AbstractModel):
    _inherit = "primate.sagui.assistant"

    # ---------- tool specs ----------
    def _tool_specs(self):
        specs = super()._tool_specs()
        specs += [
            {
                "name": "analizar_sitio",
                "description": "Lee un PDF/imagen de diseño adjunto y lo INTERPRETA a un SCHEMA de "
                               "snippets NATIVOS de Odoo: por cada sección elige el snippet más "
                               "parecido (s_banner, s_text_image, s_features, s_image_gallery, "
                               "s_call_to_action, s_text_block) + su copy + qué imagen le toca + un "
                               "color (o_cc1..o_cc5), conservando el idioma. Devuelve un PREVIEW del "
                               "schema para revisar ANTES de construir. Usalo con el attachment_id.",
                "input_schema": {
                    "type": "object",
                    "properties": {"attachment_id": {"type": "integer"}},
                    "required": ["attachment_id"],
                },
            },
            {
                "name": "generar_sitio",
                "description": "PROPONE CONSTRUIR el sitio a partir del schema ya interpretado (por "
                               "analizar_sitio). En AMBOS modos aplica el TEMA de marca (header/footer/"
                               "paleta/tipografía/menús/logo). El parámetro render_mode elige el CUERPO "
                               "de la home: 'fiel' = HTML/CSS a medida, máxima fidelidad al PDF (editás "
                               "por código); 'editable' = snippets nativos, 100% editables en el "
                               "builder (fidelidad acotada a snippets). Default 'fiel'. NO aplica al "
                               "toque: registra una propuesta con token; el usuario confirma para "
                               "construir. Preguntale al usuario qué modo prefiere si no lo dijo.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "attachment_id": {"type": "integer"},
                        "render_mode": {
                            "type": "string", "enum": ["fiel", "editable"],
                            "description": "Modo de cuerpo: 'fiel' (HTML/CSS a medida) o 'editable' "
                                           "(snippets nativos). Default 'fiel'.",
                        },
                        "name": {
                            "type": "string",
                            "description": "Nombre/título del sitio y base de su URL. Opcional: si no "
                                           "se pasa, se usa el título del diseño. Pasalo cuando el "
                                           "usuario pide 'otro nombre' o uno específico.",
                        },
                    },
                    "required": ["attachment_id"],
                },
            },
        ]
        return specs

    def _system_prompt(self):
        prompt = super()._system_prompt()
        prompt += "\n\n" + self.env["sagui.skill"].content_of("website-from-pdf")
        return prompt

    # ---------- dispatch ----------
    def _run_tool(self, name, args, user, channel=None, proposals=None):
        if name == "analizar_sitio":
            return self._tool_analizar_sitio(args, user)
        if name == "generar_sitio":
            return self._propose_site(args, user, channel, proposals,
                                      render_mode=args.get("render_mode"),
                                      name=args.get("name"))
        return super()._run_tool(name, args, user, channel=channel, proposals=proposals)

    def _execute_pending(self, channel, author, pending):
        if pending.operation == "website":
            return self._execute_site(channel, author, pending)
        return super()._execute_pending(channel, author, pending)

    # ======================================================================
    #  Tool: analizar_sitio -> INTERPRETA el PDF a un SCHEMA de snippets nativos (visión)
    # ======================================================================
    def _tool_analizar_sitio(self, args, user):
        env = self.env(user=user.id)
        att = self._get_design_attachment(env, args.get("attachment_id"))
        if isinstance(att, str):
            return att
        try:
            pages, images = self._pdf_extract(att.raw, att.mimetype or "")
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui: falló la extracción del PDF")
            return _("No pude leer el PDF/imagen: %s") % e
        if not pages:
            return _("No pude extraer contenido del archivo.")

        image_urls = self._save_site_images(env, att, images)
        # Acción para costeo/drill-down: interpretar el PDF/diseño (1 llamada de visión).
        # env (del usuario) → el costo se atribuye al usuario y hereda el contexto de la acción.
        action = env["primate.ai.action"]._open(
            "analizar_sitio", att.name or "Analizar diseño", ref="ir.attachment,%s" % att.id)
        connector = env["primate.ai.connector"].with_context(ai_action_id=action.id)

        # Referencia para la visión: render de cada página + su texto + las URLs extraídas.
        ref = []
        for i, pg in enumerate(pages):
            ref.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": pg["png_b64"]}})
            if pg["text"]:
                ref.append({"type": "text", "text": "Texto pág %s:\n%s" % (i + 1, pg["text"])})
        if image_urls:
            ref.append({"type": "text", "text": "Imágenes extraídas (URLs):\n" + "\n".join(image_urls)})

        # UNA llamada: PDF -> SCHEMA de snippets nativos (estructura + copy + imagen + o_cc).
        try:
            data = connector.call([{"role": "user", "content": ref + [
                {"type": "text", "text": "Traducí el diseño al SCHEMA de snippets nativos."}]}],
                system=SCHEMA_PROMPT, max_tokens=MAX_OUTPUT_TOKENS, timeout=240)
        except Exception as e:  # noqa: BLE001
            return _("La visión falló interpretando el diseño: %s") % e
        schema = self._safe_json("".join(b.get("text", "") for b in (data.get("content") or [])
                                         if b.get("type") == "text"))
        if not schema or not schema.get("sections"):
            return _("No pude interpretar el diseño. Probá con otro PDF.")

        schema = self._normalize_schema(schema, image_urls)
        title = schema.get("title") or (att.name or "Inicio").rsplit(".", 1)[0]
        schema["title"] = title
        att.sudo().write({
            "primate_landing_title": title,
            "primate_landing_meta": json.dumps(schema, ensure_ascii=False),
            # Limpiar restos del enfoque viejo (HTML/CSS inline) por si el adjunto se reusa.
            "primate_landing_html": False, "primate_landing_css": False,
        })

        preview = {
            "titulo": title, "idioma": schema.get("language"),
            "tema": "theme_design_pipa (paleta/fuentes de marca)",
            "secciones": [{
                "seccion": s.get("label") or s.get("id"),
                "snippet": s.get("snippet"), "color": s.get("o_cc"),
                "titular": (s.get("copy") or {}).get("headline") or "",
                "imagen": s.get("image") or (s.get("images") or [None])[0],
            } for s in schema["sections"]],
            "nav": [n.get("label") for n in (schema.get("nav") or [])],
            "footer": (schema.get("footer") or {}).get("brand"),
            "logo": schema.get("logo"),
            "imagenes_extraidas": len(image_urls),
        }
        return json.dumps({"preview": preview, "listo": True,
                           "modos": {"fiel": "HTML/CSS a medida, máxima fidelidad al PDF (se edita "
                                             "por código)", "editable": "snippets nativos, 100% "
                                             "editable en el builder (fidelidad acotada a snippets)"},
                           "nota": "Schema listo. Mostrale al usuario las secciones (snippet/color/"
                                   "imagen) y PREGUNTALE el modo de salida (fiel | editable, default "
                                   "fiel). Después llamá generar_sitio con attachment_id y "
                                   "render_mode (con confirmación). En ambos modos se aplica el tema "
                                   "de marca."}, default=str, ensure_ascii=False)

    # ---------- normalización del schema (valida snippet/o_cc, mapea imágenes, slugea ids) ----------
    def _normalize_schema(self, schema, image_urls):
        pool = list(image_urls or [])
        valid_pool = set(pool)
        clean = []
        for sec in schema.get("sections") or []:
            snip = (sec.get("snippet") or "").strip()
            if snip not in SNIPPETS:
                snip = "s_text_block"  # fallback seguro
            sec["snippet"] = snip
            cc = (sec.get("o_cc") or "").strip()
            sec["o_cc"] = cc if cc in COLOR_COMBOS else "o_cc1"
            sec["layout"] = (sec.get("layout") or "").strip()  # usado por el modo 'fiel'
            sec["id"] = self._slug_id(sec.get("id") or sec.get("label") or snip)
            # Imagen única: validar que sea de las extraídas; si no, descartar.
            img = sec.get("image")
            sec["image"] = img if img in valid_pool else None
            # Galería: filtrar a las extraídas reales.
            imgs = [u for u in (sec.get("images") or []) if u in valid_pool]
            if snip == "s_image_gallery" and not imgs and sec["image"]:
                imgs = [sec["image"]]
            sec["images"] = imgs
            copy = sec.get("copy") or {}
            if not isinstance(copy.get("body"), list):
                copy["body"] = [copy["body"]] if copy.get("body") else []
            if not isinstance(copy.get("list"), list):
                copy["list"] = [copy["list"]] if copy.get("list") else []
            if not isinstance(copy.get("features"), list):
                copy["features"] = []
            sec["copy"] = copy
            clean.append(sec)
        schema["sections"] = clean
        if schema.get("logo") not in valid_pool:
            schema["logo"] = None
        return schema

    def _slug_id(self, s):
        s = re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-")
        return s or "sec"

    # ======================================================================
    #  Tool: generar_sitio -> PROPONE (no aplica)
    # ======================================================================
    def _propose_site(self, args, user, channel, proposals, render_mode=None, name=None):
        if not channel:
            return _("No puedo proponer fuera de un canal de chat.")
        env_user = self.env(user=user.id)
        att = self._get_design_attachment(env_user, args.get("attachment_id"))
        if isinstance(att, str):
            return att
        if not att.primate_landing_meta:
            return _("Todavía no interpreté ese diseño. Usá analizar_sitio primero.")
        mode = (render_mode or "fiel").strip().lower()
        if mode not in RENDER_MODES:
            mode = "fiel"
        name = (name or "").strip() or None
        summary = self._summary_site(att, mode, name)
        token = __import__("secrets").token_hex(3)
        pending = self.env["primate.sagui.pending.write"].sudo().create({
            "token": token, "channel_id": channel.id, "user_id": user.id,
            "operation": "website", "model_name": "website.page",
            "values_json": json.dumps({"attachment_id": att.id, "render_mode": mode, "name": name},
                                      ensure_ascii=False),
            "summary": summary, "state": "pending",
        })
        if proposals is not None:
            proposals.append(pending.id)
        cuerpo = ("HTML/CSS a medida (máxima fidelidad al PDF)" if mode == "fiel"
                  else "snippets nativos (100% editable en el builder)")
        return _(
            "PROPUESTA REGISTRADA (token %(token)s) — modo %(mode)s: cuerpo = %(cuerpo)s; en ambos "
            "modos se aplica el tema de marca. NO se construyó nada todavía (la homepage anterior "
            "queda guardada para revertir). Pedile al usuario 'confirmar %(token)s' para construir o "
            "'cancelar %(token)s'. Resumen: %(summary)s"
        ) % {"token": token, "mode": mode, "cuerpo": cuerpo, "summary": summary}

    # ======================================================================
    #  Ejecución real (al confirmar)
    # ======================================================================
    def _execute_site(self, channel, author, pending):
        env_user = self.env(user=author.id)
        try:
            data = json.loads(pending.values_json or "{}")
            mode = (data.get("render_mode") or "fiel").strip().lower()
            if mode not in RENDER_MODES:
                mode = "fiel"
            att = self._get_design_attachment(env_user, data.get("attachment_id"))
            if isinstance(att, str):
                raise ValueError(att)
            schema = self._safe_json(att.sudo().primate_landing_meta) or {}
            if not schema.get("sections"):
                raise ValueError(_("no encuentro el schema interpretado; volvé a analizar el PDF"))
            title = (data.get("name") or "").strip() or att.sudo().primate_landing_title \
                or schema.get("title") or (att.name or "Inicio")
            logo = schema.get("logo") or self._first_pool_image(env_user, data.get("attachment_id"))
            builder = env_user["primate.website.builder"]

            if mode == "editable":
                # Cuerpo = snippets nativos desde el schema (100% editable en el builder).
                result = builder.build_snippet_site(
                    schema, title, logo_url=logo, lang=schema.get("language"),
                    theme="theme_design_pipa")
                cuerpo = _("snippets nativos (100% editable en el editor de Odoo)")
            else:
                # Cuerpo = HTML/CSS a medida scopeado (.brandsite), máxima fidelidad al PDF.
                # Acción para costeo/drill-down: la generación del sitio (lo CARO).
                action = env_user["primate.ai.action"]._open(
                    "generar_sitio", title or "Sitio", ref="ir.attachment,%s" % att.id)
                env_user = env_user(context={**env_user.context, "ai_action_id": action.id})
                body, css = self._render_fiel_body(env_user, att, schema)
                if not body:
                    raise ValueError(_("no pude renderizar el cuerpo fiel; probá el modo editable"))
                result = builder.build_brandsite(
                    body, css, title, schema=schema, logo_url=logo,
                    lang=schema.get("language"), theme="theme_design_pipa")
                cuerpo = _("HTML/CSS a medida (máxima fidelidad al PDF, se edita por código)")

            pending.write({"state": "done", "result_info": "mode=%s page_id=%s url=%s" % (
                mode, result.get("page_id"), result.get("url"))})
            self._post_bot_reply(channel, _(
                "✅ Listo: armé «%(site)s» en %(url)s — modo %(mode)s, cuerpo = %(cuerpo)s; con el "
                "tema de marca aplicado (header/footer/paleta/tipografía/menús/logo). El backend "
                "sigue en /odoo. La homepage anterior quedó guardada (%(prev)s): si no te gusta, "
                "pedime revertir."
            ) % {"site": result.get("website_name"), "url": result.get("url"), "mode": mode,
                 "cuerpo": cuerpo, "prev": result.get("prev_homepage_url") or "/"})
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui: falló la construcción del sitio %s", pending.token)
            pending.write({"state": "error", "result_info": str(e)[:200]})
            self._post_bot_reply(channel, _("❌ No pude construir el sitio (%(token)s): %(err)s") % {
                "token": pending.token, "err": e})

    # ---------- modo 'fiel': render del cuerpo custom (HTML/CSS) con una llamada de visión ----------
    def _render_fiel_body(self, env, att, schema):
        """Genera el cuerpo a medida (HTML semántico + CSS de layout scopeado a .brandsite) fiel al
        PDF. Colores/fuentes del tema (clases o_cc + vars); el CSS custom controla sólo el layout."""
        try:
            pages, _imgs = self._pdf_extract(att.raw, att.mimetype or "")
        except Exception:  # noqa: BLE001
            pages = []
        ref = []
        for i, pg in enumerate(pages[:MAX_PDF_PAGES]):
            ref.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": pg["png_b64"]}})
            if pg.get("text"):
                ref.append({"type": "text", "text": "Texto pág %s:\n%s" % (i + 1, pg["text"])})
        # Schema compacto para guiar el layout (id, layout, o_cc, copy, imagen) sin ruido.
        compact = [{"id": s["id"], "o_cc": s.get("o_cc"), "layout": s.get("layout"),
                    "copy": s.get("copy"), "image": s.get("image"),
                    "images": s.get("images")} for s in schema.get("sections") or []]
        ref.append({"type": "text", "text": "SCHEMA de secciones (orden, layout, copy, imágenes):\n"
                    + json.dumps(compact, ensure_ascii=False)})
        try:
            # env (no self.env): hereda el contexto de la acción y atribuye el costo al usuario.
            d = env["primate.ai.connector"].call(
                [{"role": "user", "content": ref + [
                    {"type": "text", "text": "Generá el cuerpo fiel (===CSS=== y ===HTML===)."}]}],
                system=FIEL_BODY_PROMPT, max_tokens=MAX_OUTPUT_TOKENS, timeout=300)
        except Exception as e:  # noqa: BLE001
            _logger.warning("Sagui: falló el render fiel del cuerpo: %s", e)
            return "", ""
        blocks = self._parse_blocks("".join(b.get("text", "") for b in (d.get("content") or [])
                                            if b.get("type") == "text"))
        return blocks.get("html", "").strip(), blocks.get("css", "").strip()

    # ======================================================================
    #  Helpers
    # ======================================================================
    def _parse_blocks(self, text):
        """Parte la salida del modelo por los marcadores ===CSS=== / ===HTML===."""
        text = _FENCE_RE.sub("", (text or "").strip())
        out, parts = {}, _BLOCK_RE.split(text)
        for i in range(1, len(parts) - 1, 2):
            out[parts[i].strip().lower()] = parts[i + 1].strip()
        return out

    def _safe_json(self, raw):
        if not raw:
            return None
        raw = _FENCE_RE.sub("", raw.strip())
        try:
            return json.loads(raw)
        except ValueError:
            s, e = raw.find("{"), raw.rfind("}")
            if s != -1 and e > s:
                try:
                    return json.loads(raw[s:e + 1])
                except ValueError:
                    return None
        return None

    def _first_pool_image(self, env, attachment_id):
        pool = self._pool_for_attachment(env, attachment_id)
        return pool[0] if pool else None

    def _pool_for_attachment(self, env, attachment_id):
        if not attachment_id:
            return []
        att = env["ir.attachment"].browse(int(attachment_id))
        if not att.exists():
            return []
        base = (att.name or "design").rsplit(".", 1)[0]
        imgs = env["ir.attachment"].search([("name", "=like", base + "_img_%")], order="name")
        return ["/web/image/%s" % a.id for a in imgs]

    def _get_design_attachment(self, env, attachment_id):
        try:
            att = env["ir.attachment"].browse(int(attachment_id or 0))
            att.check_access("read")
        except Exception:  # noqa: BLE001
            return _("No encontré el adjunto %s o no podés verlo.") % attachment_id
        if not att.exists():
            return _("El adjunto %s ya no existe.") % attachment_id
        mimetype = (att.mimetype or "").split(";")[0].strip().lower()
        ext = (att.name or "").lower()
        ok = mimetype == "application/pdf" or mimetype.startswith("image/") \
            or ext.endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp"))
        if not ok:
            return _("El adjunto '%s' no es un PDF ni una imagen de diseño.") % (att.name or "?")
        return att

    def _pdf_extract(self, raw, mimetype):
        import fitz  # PyMuPDF

        pages, images = [], []
        is_pdf = (mimetype or "").split(";")[0].strip().lower() == "application/pdf"
        if not is_pdf:
            pix = fitz.Pixmap(raw)
            if pix.alpha or pix.n > 3:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            png = pix.tobytes("png")
            pages.append({"text": "", "png_b64": base64.b64encode(png).decode("ascii")})
            images.append({"ext": "png", "bytes": png})
            return pages, images

        doc = fitz.open(stream=raw, filetype="pdf")
        seen_xrefs = set()
        for pno, page in enumerate(doc):
            if pno >= MAX_PDF_PAGES:
                break
            text = (page.get_text("text") or "").strip()
            png = self._render_page(page, fitz)
            pages.append({"text": text[:6000], "png_b64": base64.b64encode(png).decode("ascii")})
            for info in page.get_images(full=True):
                xref = info[0]
                if xref in seen_xrefs or len(images) >= MAX_EMBEDDED_IMAGES:
                    continue
                seen_xrefs.add(xref)
                try:
                    ext_img = doc.extract_image(xref)
                except Exception:  # noqa: BLE001
                    continue
                w, h = ext_img.get("width", 0), ext_img.get("height", 0)
                blob = ext_img.get("image") or b""
                if w < MIN_IMG_SIDE or h < MIN_IMG_SIDE or len(blob) < MIN_IMG_BYTES:
                    continue
                images.append({"ext": ext_img.get("ext", "png"), "bytes": blob})
        return pages, images

    def _render_page(self, page, fitz):
        rect = page.rect
        longest = max(rect.width, rect.height) or 1
        zoom = min(RENDER_MAX_PX / longest, 200 / 72.0)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png")

    def _save_site_images(self, env, att, images):
        urls, Att = [], env["ir.attachment"]
        for i, im in enumerate(images):
            try:
                rec = Att.create({
                    "name": "%s_img_%s.%s" % ((att.name or "design").rsplit(".", 1)[0], i, im["ext"]),
                    "raw": im["bytes"],
                    "mimetype": "image/%s" % ("jpeg" if im["ext"] in ("jpg", "jpeg") else im["ext"]),
                    "public": True, "res_model": "ir.ui.view", "res_id": 0,
                })
                urls.append("/web/image/%s" % rec.id)
            except Exception:  # noqa: BLE001
                _logger.warning("No pude guardar una imagen extraída del PDF", exc_info=True)
        return urls

    def _summary_site(self, att, mode="fiel", name=None):
        schema = self._safe_json(att.primate_landing_meta) or {}
        secs = ["%s [%s/%s]" % (s.get("label") or s.get("id"), s.get("snippet"), s.get("o_cc"))
                for s in (schema.get("sections") or [])]
        cuerpo = ("HTML/CSS a medida (máxima fidelidad al PDF, se edita por código)" if mode == "fiel"
                  else "snippets nativos (100% editable en el builder)")
        titulo = name or att.primate_landing_title or schema.get("title") or "-"
        return "\n".join([
            _("Construir «%(name)s» como homepage del sitio. MODO %(mode)s: cuerpo = %(cuerpo)s. En "
              "ambos modos el TEMA de marca pone header/footer/paleta/tipografía/menús/logo; el "
              "backend sigue en /odoo.") % {"name": titulo, "mode": mode, "cuerpo": cuerpo},
            _("Título: %(t)s · idioma: %(l)s") % {"t": titulo, "l": schema.get("language") or "-"},
            _("Secciones (sección [snippet/color]): %s") % "; ".join(secs) if secs else _("Secciones: -"),
            _("La homepage anterior se guarda para poder revertir."),
        ])
