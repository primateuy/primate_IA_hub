# -*- coding: utf-8 -*-
# Motor del generador (enfoque RESTYLE del sitio real): NO crea una landing huérfana a pantalla
# completa. Crea una website.page que USA website.layout (header/footer + barra de edición del
# admin) y la setea como HOMEPAGE del sitio de la compañía, restyleando el sitio para que se
# parezca al diseño, manteniéndolo FUNCIONAL (nav anda, el backend sigue en /odoo).
#
# Estilo: NUNCA make_scss_customization (edita variables del tema y un valor malo rompe la
# compilación de TODA la instancia). En su lugar agrega un ir.asset (CSS PLANO) a
# web.assets_frontend, namespaceado bajo .brandsite y SCOPEADO al website objetivo (ir.asset
# tiene website_id). Un .css plano falla aislado: no puede tumbar el tema.
#
# [VERIFICADO v19] ir.asset.website_id (addons/website/models/ir_asset.py); un path que no es glob
# se resuelve como attachment-por-url (ir_asset._get_paths); website.layout + homepage_url.
import logging
import re

from odoo import api, fields, models, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

_SCRIPT_RE = re.compile(r"<script\b.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_ON_ATTR_RE = re.compile(r"\son[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*```(?:html|css)?\s*|\s*```\s*$", re.IGNORECASE)
# @import en el CSS: lo SACAMOS del asset. Las URLs css2 de Google Fonts traen ';' (wght@300;400)
# y el pipeline de Odoo, al mover los @import al tope del bundle, corta la URL en el primer ';' ->
# @import malformado -> el navegador no parsea el stylesheet y NADA se estiliza. Las fuentes se
# cargan con un <link> en el HTML (donde el ';' no rompe nada). El regex consume el url(...)/string
# COMPLETO (con sus ';' internos) y recién después el ';' final — si no, deja basura que rompe la
# primera regla.
_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\([^)]*\)|"[^"]*"|'[^']*')[^;]*;""", re.IGNORECASE)
WRAPPER = "brandsite"  # namespace único de todo el CSS de marca


class Website(models.Model):
    _inherit = "website"

    # Homepage anterior, para poder revertir si una generación sale mal (no se borra nada).
    primate_prev_homepage_url = fields.Char("Homepage previa (Sagui)", copy=False)


class WebsitePage(models.Model):
    _inherit = "website.page"

    # Cuerpo HTML (las secciones) que va dentro de website.layout > #wrap > .brandsite.
    primate_landing_html = fields.Html(
        string="Cuerpo brandsite (Sagui)", sanitize=False, copy=False)


class PrimateWebsiteBuilder(models.AbstractModel):
    _name = "primate.website.builder"
    _description = "Restylea el sitio de la compañía a partir de un diseño"

    @api.model
    def build_brandsite(self, body_html, css, name, schema=None, tokens=None,
                        logo_url=None, lang=None, theme=None, website=None,
                        palette=None, fonts=None):
        """Modo 'fiel': homepage con CUERPO a medida (HTML/CSS scopeado .brandsite), máxima fidelidad
        al diseño. El TEMA de marca es dueño de header/footer/menús/logo (se aplica acá).

        Por defecto (flujo PDF) los colores salen del TEMA (clases o_cc) y no se inyecta paleta. En
        GREENFIELD se pasa `palette`/`fonts` (la identidad de la marca): se RECOLOREA el tema SÓLO
        para este website vía CSS PLANO scopeado (vars de marca en .brandsite + restyle de
        header/footer + fuentes por <link>), sin make_scss_customization ni assets globales. El
        cuerpo usa las vars de marca (--p/--a/--bg/--fg…) o clases o_cc según el flujo.

        :param body_html: HTML de las secciones (va dentro de .brandsite).
        :param css: CSS de LAYOUT namespaceado bajo .brandsite.
        :param schema: dict del diseño (para nav/footer/logo). `tokens` se acepta por compat.
        :param theme: módulo de tema a aplicar (ej. 'theme_design_pipa').
        :param palette: dict de roles de color de la marca (greenfield); None → colores del tema.
        :param fonts: dict {display,body:{family,google}} de la marca (greenfield).
        :return: {page_id, url, website_id, website_name, name, prev_homepage_url, is_homepage}
        """
        if not body_html or not body_html.strip():
            raise UserError(_("El contenido generado está vacío."))
        website = website or self._company_website()
        if not website:
            raise UserError(_("No hay un sitio web configurado."))
        meta = schema or tokens or {}
        
        # Tema de marca: aplica estructura/menús/header/footer (mismo que en modo editable).
        if theme:
            self._apply_theme(website, theme)

        body = self._sanitize(body_html)
        font_links = ""

        # GREENFIELD: recoloreo el tema con la identidad SÓLO para este website (CSS plano scopeado
        # a .brandsite + restyle de header/footer + fuentes de marca).
        if palette:
            short = self._brand_short_palette(palette)
            css = "\n".join([
                css or "",
                self._brand_palette_css(palette, fonts),
                self._header_footer_css({"palette": self._brand_hf_palette(palette), "fonts": fonts}),
                self._contrast_finishing(css or "", short),
            ])
            font_links = self._font_links({"fonts": fonts or {}})

        # El CSS se EMBEBE inline (<style>) en el cuerpo de la página, NO vía ir.asset: en v19 un
        # ir.asset cuyo `path` apunta a un attachment-por-URL NO se inlinea en el bundle
        # web.assets_frontend (se sirve suelto pero el bundle lo ignora) → el sitio salía sin estilo.
        # El campo es Html(sanitize=False) y se renderiza RAW por t-out, así que el <style> (y el
        # <link> de fuentes) llegan tal cual. El CSS está namespaceado (.brandsite + header#top/
        # footer#bottom), así que sólo afecta a esta home. Sin @import (las fuentes van por <link>).
        final_css = _IMPORT_RE.sub("", css or "").strip()
        style_tag = ("<style>%s</style>" % final_css) if final_css else ""
        body = font_links + style_tag + body

        WebsiteCtx = self.env["website"].with_context(website_id=website.id)
        slug = self.env["ir.http"]._slugify(name or "inicio", max_length=80, path=True) or "inicio"
        url = WebsiteCtx.get_unique_path("/" + slug)
        key = self._unique_view_key(slug)

        # Página dentro de website.layout (header/footer del tema + barra admin). El cuerpo se emite
        # RAW desde el campo (no hace falta que sea XML-válido) dentro de .brandsite.
        arch = (
            '<t t-name="%s">'
            '<t t-call="website.layout">'
            '<div id="wrap" class="oe_structure">'
            '<div class="%s"><t t-out="main_object.primate_landing_html"/></div>'
            '</div>'
            '</t></t>'
        ) % (key, WRAPPER)
        view = self.env["ir.ui.view"].create({
            "name": name or "Inicio", "type": "qweb", "key": key,
            "arch": arch, "website_id": website.id,
        })
        page = self.env["website.page"].create({
            "name": name or "Inicio", "url": url, "view_id": view.id,
            "website_id": website.id, "is_published": True,
            "website_indexed": True, "primate_landing_html": body,
        })

        # (El CSS ya quedó embebido inline en `body`; no se usa ir.asset — ver nota arriba.)

        # Contenido de marca (lo pone el build, el tema lo estiliza): logo, nav y footer del diseño.
        if logo_url:
            self._set_logo(website, logo_url)
        self._build_nav(website, meta.get("nav") or [])
        self._replace_footer(website, meta.get("footer") or {}, meta.get("nav") or [])

        # Setear como HOMEPAGE, guardando la anterior recuperable (solo la primera vez).
        prev = website.homepage_url or "/"
        if not website.primate_prev_homepage_url:
            website.primate_prev_homepage_url = prev
        website.homepage_url = url
        # El controller que sirve '/' lee homepage_url de un ormcache (website._get_cached_values);
        # invalidarlo para que la raíz sirva la home nueva sin reiniciar el server.
        self.env.registry.clear_all_caches()

        return {
            "page_id": page.id, "url": url, "website_id": website.id,
            "website_name": website.name, "name": name,
            "prev_homepage_url": website.primate_prev_homepage_url, "is_homepage": True,
        }

    # ======================================================================
    #  build_snippet_site: SCHEMA de snippets nativos -> website.page BORRADOR (editable)
    # ======================================================================
    @api.model
    def build_snippet_site(self, schema, name, logo_url=None, lang=None,
                           theme=None, website=None):
        """Construye una página BORRADOR instanciando SNIPPETS NATIVOS de Odoo a partir del schema
        (snippet + copy + imagen + o_cc por sección). El estilo (paleta/fuentes) lo aporta el TEMA
        de marca aplicado al sitio — NO se inyecta CSS inline. La página queda editable en el editor.

        :param schema: dict {title, language, logo, nav, footer, sections:[{id, snippet, o_cc, copy,
                       image, images}]} producido por analizar_sitio.
        :param theme: nombre técnico del módulo de tema a aplicar (ej. 'theme_design_pipa').
        :return: {page_id, url, website_id, website_name, name, prev_homepage_url, is_homepage}
        """
        sections = (schema or {}).get("sections") or []
        if not sections:
            raise UserError(_("El schema no tiene secciones para construir."))
        website = website or self._company_website()
        if not website:
            raise UserError(_("No hay un sitio web configurado."))

        # 1) Aplicar el tema de marca (paleta + fuentes salen de su SCSS, compilado al instalar).
        if theme:
            self._apply_theme(website, theme)

        # 2) Instanciar cada snippet (HTML XML-válido, con clases nativas + o_cc + data-snippet).
        body = "".join(self._render_snippet(sec) for sec in sections)
        if not body.strip():
            raise UserError(_("No pude instanciar ningún snippet del schema."))

        WebsiteCtx = self.env["website"].with_context(website_id=website.id)
        slug = self.env["ir.http"]._slugify(name or "inicio", max_length=80, path=True) or "inicio"
        url = WebsiteCtx.get_unique_path("/" + slug)
        key = self._unique_view_key(slug)

        # Página dentro de website.layout. Los snippets van DIRECTO en el #wrap.oe_structure (zona
        # editable) -> el editor los reconoce y se pueden mover/editar/borrar como snippets reales.
        arch = (
            '<t t-name="%s">'
            '<t t-call="website.layout">'
            '<div id="wrap" class="oe_structure">%s</div>'
            '</t></t>'
        ) % (key, body)
        view = self.env["ir.ui.view"].create({
            "name": name or "Inicio", "type": "qweb", "key": key,
            "arch": arch, "website_id": website.id,
        })
        page = self.env["website.page"].create({
            "name": name or "Inicio", "url": url, "view_id": view.id,
            "website_id": website.id, "is_published": True,  # publicada: visible al toque (editable igual)
            "website_indexed": True,
        })

        # 3) Logo, nav y footer de marca (reutiliza el flujo del restyle).
        if logo_url:
            self._set_logo(website, logo_url)
        self._build_nav(website, (schema or {}).get("nav") or [])
        self._replace_footer(website, (schema or {}).get("footer") or {}, (schema or {}).get("nav") or [])

        # 4) Homepage, guardando la anterior recuperable (solo la primera vez).
        prev = website.homepage_url or "/"
        if not website.primate_prev_homepage_url:
            website.primate_prev_homepage_url = prev
        website.homepage_url = url

        return {
            "page_id": page.id, "url": url, "website_id": website.id,
            "website_name": website.name, "name": name,
            "prev_homepage_url": website.primate_prev_homepage_url, "is_homepage": True,
        }

    # ---------- aplicar tema de marca ----------
    @api.model
    def _apply_theme(self, website, theme_name):
        """Setea el tema de marca como theme_id del sitio (necesario: ir_asset descarta los assets
        de los temas que no sean website.theme_id). Idempotente. Regenera el bundle frontend."""
        mod = self.env["ir.module.module"].sudo().search([("name", "=", theme_name)], limit=1)
        if not mod or mod.state != "installed":
            _logger.warning("Tema %s no instalado; sigo sin aplicarlo", theme_name)
            return
        if website.theme_id == mod:
            return
        website.with_context(apply_new_theme=True, website_id=website.id).theme_id = mod
        try:
            mod.with_context(apply_new_theme=True, website_id=website.id)._theme_load(website)
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo correr _theme_load de %s", theme_name, exc_info=True)
        # Forzar regeneración del bundle (los assets del tema recién ahora se incluyen).
        self.env["ir.attachment"].sudo().search([
            ("url", "=like", "/web/assets/%assets_frontend%")]).unlink()
        self.env.registry.clear_all_caches()

    # ---------- website objetivo ----------
    @api.model
    def _company_website(self):
        company = self.env.company
        w = self.env["website"].search([("company_id", "=", company.id)], limit=1)
        return w or self.env["website"].get_current_website()

    # ---------- asset CSS scopeado (idempotente por website) ----------
    @api.model
    def _install_brandsite_asset(self, website, css):
        """Crea/actualiza el attachment CSS + el ir.asset (web.assets_frontend, website_id).
        Idempotente: re-generar reemplaza el CSS de ese sitio (no apila)."""
        url = "/primate_website_generator/brandsite_w%s.css" % website.id
        att = self.env["ir.attachment"].sudo().search([("url", "=", url)], limit=1)
        vals = {
            "name": "brandsite_w%s.css" % website.id, "url": url,
            "mimetype": "text/css", "raw": (css or "").encode("utf-8"),
            "public": True, "type": "binary",
        }
        if att:
            att.write(vals)
        else:
            att = self.env["ir.attachment"].sudo().create(vals)
        asset = self.env["ir.asset"].sudo().search([
            ("name", "=", "primate_brandsite_w%s" % website.id)], limit=1)
        asset_vals = {
            "name": "primate_brandsite_w%s" % website.id,
            "bundle": "web.assets_frontend", "directive": "append",
            "path": url, "website_id": website.id, "active": True,
        }
        if asset:
            asset.write(asset_vals)
        else:
            self.env["ir.asset"].sudo().create(asset_vals)
        return att

    # ---------- finishing de contraste ----------
    @staticmethod
    def _norm_hex(c):
        if not c or not isinstance(c, str):
            return None
        m = re.fullmatch(r"#?([0-9a-fA-F]{6})", c.strip()) or re.fullmatch(r"#?([0-9a-fA-F]{3})", c.strip())
        if not m:
            return None
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return "#" + h.lower()

    def _luminance(self, h):
        h = (h or "#000000").lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return 0.299 * r + 0.587 * g + 0.114 * b

    @api.model
    def _contrast_finishing(self, css, palette):
        """Finishing determinístico de CONTRASTE (skill: ≥4.5:1). El modelo a veces asigna mal el
        rol 'text' (lo deja igual al fondo) → texto invisible. Redefine --text (global y POR
        SECCIÓN) según la luminancia del fondo; como los hijos usan var(--text), heredan el color
        correcto sin tocar los acentos."""
        cands = [palette.get(k) for k in ("light", "surface", "background", "dark", "text")]
        normed = [self._norm_hex(c) for c in cands if self._norm_hex(c)]
        lights = [c for c in normed if self._luminance(c) >= 150]
        darks = [c for c in normed if self._luminance(c) < 150]
        light_text = max(lights, key=self._luminance) if lights else "#f5f0e8"
        dark_text = min(darks, key=self._luminance) if darks else "#161616"

        def text_for(bg_hex):
            return light_text if self._luminance(bg_hex) < 140 else dark_text

        # Emitimos --text (para hijos que usan var(--text)) Y color directo (para los que heredan).
        def rule(sel, c):
            return "%s{--text:%s;color:%s;}" % (sel, c, c)

        out = []
        bg0 = self._norm_hex(palette.get("background"))
        if bg0:
            out.append(rule(".brandsite", text_for(bg0)))
        for m in re.finditer(r"\.brandsite\s+\.([a-z0-9_-]+)\s*\{([^}]*)\}", css or "", re.I):
            sid, body = m.group(1), m.group(2)
            bgm = re.search(r"background(?:-color)?\s*:\s*([^;]+)", body, re.I)
            if not bgm:
                continue
            raw = bgm.group(1).strip()
            if "url(" in raw.lower():
                out.append(rule(".brandsite .%s" % sid, light_text))
                continue
            vm = re.search(r"var\(\s*--([a-z0-9_-]+)", raw, re.I)
            hexm = re.search(r"#[0-9a-fA-F]{3,6}", raw)
            color = self._norm_hex(palette.get(vm.group(1))) if vm else (self._norm_hex(hexm.group(0)) if hexm else None)
            if color:
                out.append(rule(".brandsite .%s" % sid, text_for(color)))
        return ("/* finishing: contraste por seccion (>=4.5:1) */\n" + "\n".join(out) + "\n") if out else ""

    # ---------- greenfield: recoloreo de marca scopeado ----------
    @api.model
    def _brand_short_palette(self, palette):
        """Paleta con claves CORTAS (p/a/bg/fg/…) para que _contrast_finishing resuelva las vars
        del cuerpo greenfield (var(--bg), var(--p), …) + las claves que usa para elegir light/dark."""
        p = palette or {}
        return {
            "p": p.get("primary"), "a": p.get("accent"), "bg": p.get("background"),
            "fg": p.get("foreground"), "muted": p.get("muted"), "border": p.get("border"),
            # claves que _contrast_finishing usa para decidir el color de texto por luminancia:
            "background": p.get("background"), "light": p.get("background"),
            "dark": p.get("foreground"), "text": p.get("foreground"), "surface": p.get("muted"),
        }

    @api.model
    def _brand_hf_palette(self, palette):
        """Mapea la paleta de marca a las claves que espera _header_footer_css (dark/light/accent)."""
        p = palette or {}
        return {
            "dark": p.get("foreground"), "light": p.get("background"), "accent": p.get("accent"),
            "background": p.get("background"), "text": p.get("foreground"), "surface": p.get("muted"),
        }

    @api.model
    def _brand_palette_css(self, palette, fonts):
        """CSS PLANO scopeado a .brandsite: define las vars de marca + tipografía + botones/links
        on-brand. Recolorea el tema SÓLO para este website (el ir.asset tiene website_id). Sin
        @import (las fuentes van por <link>)."""
        p = palette or {}
        def v(role, default):
            return self._norm_hex(p.get(role)) or default
        primary = v("primary", "#2563eb")
        accent = v("accent", primary)
        on_p = v("on_primary", "#ffffff")
        on_a = v("on_accent", "#ffffff")
        bg = v("background", "#ffffff")
        fg = v("foreground", "#161616")
        muted = v("muted", "#f1f1f1")
        border = v("border", "#e2e2e2")
        disp = ((fonts or {}).get("display") or {}).get("family") or "inherit"
        body_f = ((fonts or {}).get("body") or {}).get("family") or "inherit"
        return (
            "/* paleta de marca (greenfield), scopeada a este website */\n"
            ".%(w)s{--p:%(p)s;--on-p:%(onp)s;--a:%(a)s;--on-a:%(ona)s;--bg:%(bg)s;--fg:%(fg)s;"
            "--muted:%(mut)s;--border:%(bor)s;background:var(--bg);color:var(--fg);"
            "font-family:%(body)s;}\n"
            ".%(w)s h1,.%(w)s h2,.%(w)s h3,.%(w)s h4,.%(w)s h5,.%(w)s h6{font-family:%(disp)s;}\n"
            ".%(w)s a{color:var(--p);}\n"
            ".%(w)s .btn-primary{background-color:var(--a);border-color:var(--a);color:var(--on-a);"
            "transition:all 200ms ease;}\n"
            ".%(w)s .btn-primary:hover{filter:brightness(.94);transform:translateY(-1px);}\n"
            ".%(w)s .btn-secondary{background:transparent;color:var(--p);border:2px solid var(--p);"
            "transition:all 200ms ease;}\n"
            ".%(w)s .btn-secondary:hover{background:var(--p);color:var(--on-p);}\n"
        ) % {"w": WRAPPER, "p": primary, "onp": on_p, "a": accent, "ona": on_a, "bg": bg,
             "fg": fg, "mut": muted, "bor": border, "disp": disp, "body": body_f}

    @api.model
    def _header_footer_css(self, tokens):
        """Restyle DELIBERADO del header/footer REAL de Odoo (viven FUERA de .brandsite, así que
        el CSS namespaceado no los toca y quedan con el default ilegible). Targetea los selectores
        frontend reales (header#top / footer#bottom) con contraste explícito que matchee el diseño.
        [VERIFICADO v19] header = <header id="top">, navbar .navbar.o_cc.navbar-light (usa
        --bs-navbar-* vars), menú .top_menu .nav-link; footer = <footer id="bottom" .o_footer>."""
        pal = (tokens or {}).get("palette") or {}
        fonts = (tokens or {}).get("fonts") or {}

        def _c(*keys, default=""):
            for k in keys:
                v = pal.get(k)
                if v:
                    return v
            return default

        # Contraste deliberado oscuro+crema (matchea el feel editorial): header/footer con el color
        # OSCURO del diseño y el texto claro de alto contraste.
        hbg = _c("dark", "surface", "background", default="#10131c")
        htext = _c("light", "text", default="#f0ece4")
        accent = _c("accent", default="#c8b89a")
        fbg = _c("dark", "surface", "background", default="#0d0d0d")
        ftext = _c("light", "text", default="#f5f2ed")
        display = (fonts.get("display") or {}).get("family") or "inherit"
        body_family = (fonts.get("body") or {}).get("family") or "inherit"

        return (
            "/* Brand header/footer REAL (scopeado a este website por ir.asset, con contraste) */\n"
            "header#top, header#top .navbar {background-color:%(hbg)s !important;}\n"
            "header#top .navbar {--bs-navbar-color:%(htext)s; --bs-navbar-hover-color:%(acc)s;"
            " --bs-navbar-active-color:%(acc)s; --bs-navbar-brand-color:%(htext)s;"
            " --bs-navbar-brand-hover-color:%(acc)s;}\n"
            "header#top .nav-link, header#top .navbar-brand, header#top .top_menu .nav-link,"
            " header#top .navbar-nav .nav-link {color:%(htext)s !important; font-family:%(disp)s;"
            " text-transform:uppercase; letter-spacing:0.06em;}\n"
            "header#top .nav-link:hover, header#top .nav-link.active {color:%(acc)s !important;}\n"
            "header#top a[href^=\"tel:\"], header#top .btn_cta {display:none !important;}\n"  # ocultar teléfono + CTA demo
            "footer#bottom, footer#bottom .o_footer {background-color:%(fbg)s !important;"
            " color:%(ftext)s !important;}\n"
            "footer#bottom, footer#bottom a, footer#bottom .nav-link, footer#bottom .o_footer,"
            " footer#bottom h1, footer#bottom h2, footer#bottom h3, footer#bottom h4,"
            " footer#bottom h5, footer#bottom h6, footer#bottom p, footer#bottom li"
            " {color:%(ftext)s !important; --bs-body-color:%(ftext)s; --bs-heading-color:%(ftext)s;}\n"
            "footer#bottom .brandfooter h3 {font-family:%(disp)s; text-transform:uppercase;}\n"
            "footer#bottom a:hover {color:%(acc)s !important;}\n"
            ".%(w)s {font-family:%(body)s;}\n"
        ) % {"hbg": hbg, "htext": htext, "acc": accent, "fbg": fbg,
             "ftext": ftext, "disp": display, "body": body_family, "w": WRAPPER}

    @api.model
    def _font_links(self, tokens):
        """<link rel=stylesheet> de Google Fonts desde los tokens (en el HTML, no en el CSS:
        así el ';' de las URLs css2 no rompe el bundle)."""
        fonts = (tokens or {}).get("fonts") or {}
        urls, links = [], ""
        for f in (fonts.get("display"), fonts.get("body"), fonts.get("script")):
            g = (f or {}).get("google")
            if g and g.startswith("http") and g not in urls:
                urls.append(g)
                links += '<link rel="stylesheet" href="%s"/>' % g.replace('"', "%22")
        return links

    # ---------- nav del diseño ----------
    @api.model
    def _build_nav(self, website, nav_items):
        """Reemplaza los menús del top del sitio por los del diseño, apuntando a las anclas de
        sección de la home (#id). Limpia los demo. Idempotente (re-correr no duplica)."""
        Menu = self.env["website.menu"]
        Menu.search([
            ("website_id", "=", website.id),
            ("parent_id", "=", website.menu_id.id),
        ]).unlink()
        for i, item in enumerate(nav_items):
            if isinstance(item, str):
                label, target = item, ""
            else:
                label = item.get("label") or item.get("title")
                target = item.get("target") or item.get("href") or ""
            if not label:
                continue
            target = (target or "").strip()
            if target.startswith("#"):
                url = "/" + target            # ancla de sección de la home
            elif target.startswith(("/", "http", "mailto:", "tel:")):
                url = target                  # destino real
            else:
                url = "/#" + (self.env["ir.http"]._slugify(target or label) or "")
            Menu.create({
                "name": label, "url": url,
                "parent_id": website.menu_id.id, "website_id": website.id,
                "sequence": 10 + i,
            })

    # ---------- footer del diseño ----------
    @api.model
    def _replace_footer(self, website, footer, nav_items):
        """Reemplaza el CONTENIDO del footer demo de Odoo por el de la marca (COW por website).
        Escribe en el oe_structure #footer del view website.footer_custom. El color/contraste lo
        pone el CSS de header/footer (footer#bottom)."""
        from markupsafe import escape
        fview = self.env.ref("website.footer_custom", raise_if_not_found=False)
        if not fview:
            return
        brand = escape((footer.get("brand") or website.name or "").strip())
        # Filtrá anotaciones del diseñador (notas de fuente, lorem, placeholders) — no son contenido.
        lines = "".join("<p class='mb-1'>%s</p>" % escape(l)
                        for l in (footer.get("lines") or []) if l and not self._is_annotation(l))
        nav_links = "".join(
            "<li><a href='%s' class='text-reset text-decoration-none'>%s</a></li>" % (
                self._anchor_url(it), escape(self._nav_label(it)))
            for it in nav_items if self._nav_label(it))
        contact = footer.get("contact") or {}
        cl = []
        if contact.get("email"):
            cl.append("<li><a href='mailto:%s' class='text-reset text-decoration-none'>%s</a></li>"
                      % (escape(contact["email"]), escape(contact["email"])))
        if contact.get("whatsapp"):
            wa = re.sub(r"[^0-9]", "", str(contact["whatsapp"]))
            cl.append("<li><a href='https://wa.me/%s' class='text-reset text-decoration-none'>WhatsApp</a></li>" % wa)
        if contact.get("instagram"):
            ig = str(contact["instagram"]).lstrip("@")
            cl.append("<li><a href='https://instagram.com/%s' class='text-reset text-decoration-none'>Instagram</a></li>" % escape(ig))
        if not cl:  # el PDF no traía contacto -> link real a la página de contacto (no inventamos datos)
            cl.append("<li><a href='/contactus' class='text-reset text-decoration-none'>Fale conosco</a></li>")
        contact_links = "".join(cl)

        html = (
            "<div id='footer' class='oe_structure oe_structure_solo text-break brandfooter'>"
            "<section class='s_text_block pt40 pb24'><div class='container'><div class='row g-4'>"
            "<div class='col-lg-6'><h3 class='mb-2'>%(brand)s</h3>%(lines)s</div>"
            "<div class='col-lg-3'><h6 class='text-uppercase opacity-75 mb-2'>Menu</h6>"
            "<ul class='list-unstyled m-0'>%(nav)s</ul></div>"
            "<div class='col-lg-3'><h6 class='text-uppercase opacity-75 mb-2'>Contato</h6>"
            "<ul class='list-unstyled m-0'>%(contact)s</ul></div>"
            "</div></div></section></div>"
        ) % {"brand": brand, "lines": lines, "nav": nav_links, "contact": contact_links}
        try:
            fview_w = fview.with_context(website_id=website.id)
            # RESET ANTES DE ESCRIBIR. Sin esto el guardado se apila sobre el footer de la
            # generación anterior y el sitio termina mostrando las líneas de dos o tres marcas
            # distintas juntas (visto: el footer de una clínica con el texto de una herrería y de
            # un estudio contable). El footer es del WEBSITE, así que cada generación tiene que
            # dejarlo como si fuera la primera.
            try:
                fview_w.reset_arch(mode="hard")
            except Exception:  # noqa: BLE001 - vista sin arch original que resetear
                _logger.info("Footer sin arch original para resetear; se sobrescribe igual")
            fview_w.save(value=html, xpath="//div[@id='footer']")
        except Exception:  # noqa: BLE001 - si falla, el footer queda con el restyle pero contenido demo
            _logger.warning("No se pudo reemplazar el contenido del footer", exc_info=True)
        # Copyright de la marca (reemplaza el 'Company name' demo, que vive en la vista
        # website.footer_copyright_company_name, no en footer_custom).
        year = fields.Date.context_today(self).year
        cview = self.env.ref("website.footer_copyright_company_name", raise_if_not_found=False)
        if cview:
            span = ("<span class='o_footer_copyright_name me-2 small'>© %s %s</span>"
                    % (year, brand))
            try:
                cview.with_context(website_id=website.id).save(
                    value=span, xpath="//span[hasclass('o_footer_copyright_name')]")
            except Exception:  # noqa: BLE001
                _logger.warning("No se pudo reemplazar el copyright del footer", exc_info=True)

    @api.model
    def _is_annotation(self, text):
        """¿Es una anotación del diseñador/placeholder y no copy del sitio? (nota de fuente, lorem,
        'Your Logo', etc.)."""
        t = (text or "").strip().lower()
        if not t:
            return True
        pats = ("algo con esta letra", "esta letra", "esta fuente", "essa fonte", "esta tipografia",
                "lorem ipsum", "your logo", "tu logo", "seu logo", "placeholder", "sample text",
                "texto de ejemplo", "texto exemplo", "fonte:", "fuente:", "font:")
        if any(p in t for p in pats):
            return True
        # Un hueco entre corchetes es el modelo diciendo "acá va un dato que no tengo":
        # «Tel: [número real]», «[correo real]», «[dirección]». Publicarlo es peor que omitirlo.
        return bool(re.search(r"\[[^\]]{2,40}\]", t))

    @api.model
    def _nav_label(self, it):
        return it if isinstance(it, str) else (it.get("label") or it.get("title") or "")

    @api.model
    def _anchor_url(self, it):
        t = "" if isinstance(it, str) else (it.get("target") or it.get("href") or "")
        t = (t or "").strip()
        if t.startswith("#"):
            return "/" + t
        if t.startswith(("/", "http", "mailto:", "tel:")):
            return t
        return "/#" + (self.env["ir.http"]._slugify(t or self._nav_label(it)) or "")

    # ---------- logo ----------
    @api.model
    def _set_logo(self, website, logo_url):
        m = re.match(r"/web/image/(\d+)", logo_url or "")
        if not m:
            return
        att = self.env["ir.attachment"].browse(int(m.group(1)))
        try:
            if att.exists() and att.datas:
                website.logo = att.datas
        except Exception:  # noqa: BLE001
            _logger.warning("No se pudo setear el logo del sitio", exc_info=True)

    # ---------- revertir ----------
    @api.model
    def revert_homepage(self, website=None):
        """Restaura la homepage anterior (si una generación no gustó)."""
        website = website or self._company_website()
        if website.primate_prev_homepage_url:
            website.homepage_url = website.primate_prev_homepage_url
            return website.primate_prev_homepage_url
        return None

    # ---------- helpers ----------
    @api.model
    def _sanitize(self, html):
        html = _FENCE_RE.sub("", html.strip())
        html = _SCRIPT_RE.sub("", html)
        html = _ON_ATTR_RE.sub("", html)
        return html

    @api.model
    def _unique_view_key(self, slug):
        base = "primate_website_generator.b_" + re.sub(r"[^a-z0-9_]", "_", slug)
        key, i = base, 1
        View = self.env["ir.ui.view"].sudo()
        while View.search_count([("key", "=", key)]):
            i += 1
            key = "%s_%s" % (base, i)
        return key

    # ======================================================================
    #  Builders de snippets nativos (emiten HTML XML-válido para el arch del view)
    # ======================================================================
    @api.model
    def _render_snippet(self, sec):
        """Despacha una sección del schema al builder de su snippet. XML-válido y editable."""
        builders = {
            "s_banner": self._snip_banner,
            "s_text_image": self._snip_text_image,
            "s_features": self._snip_features,
            "s_image_gallery": self._snip_image_gallery,
            "s_call_to_action": self._snip_call_to_action,
            "s_text_block": self._snip_text_block,
        }
        fn = builders.get(sec.get("snippet")) or self._snip_text_block
        try:
            return fn(sec)
        except Exception:  # noqa: BLE001 - una sección que falla no aborta toda la página
            _logger.warning("Falló el render del snippet %s", sec.get("snippet"), exc_info=True)
            return ""

    # ---------- helpers de markup ----------
    @staticmethod
    def _esc(text):
        from markupsafe import escape
        return str(escape((text or "").strip()))

    def _sid(self, sec):
        return re.sub(r"[^a-z0-9_-]", "", (sec.get("id") or "sec").lower()) or "sec"

    def _cc(self, sec, default="o_cc1"):
        cc = (sec.get("o_cc") or "").strip()
        return cc if re.fullmatch(r"o_cc[1-5]", cc) else default

    def _img(self, url):
        """URL de imagen extraída → <img> nativo editable. Devuelve '' si no hay imagen válida."""
        if not url or not re.match(r"/web/image/\d+", str(url)):
            return ""
        return ('<img src="%s" class="img img-fluid mx-auto rounded" alt=""/>'
                % self._esc(url))

    def _btn(self, button, cls="btn btn-primary"):
        if not button or not button.get("label"):
            return ""
        href = button.get("href") or "#"
        if not re.match(r"^(#|/|https?:|mailto:|tel:)", str(href)):
            href = "#"
        return '<p><a href="%s" class="%s">%s</a></p>' % (
            self._esc(href), cls, self._esc(button.get("label")))

    def _paras(self, body):
        items = body if isinstance(body, list) else ([body] if body else [])
        return "".join("<p>%s</p>" % self._esc(p) for p in items if p and str(p).strip())

    def _list(self, items):
        """Lista de ítems (servicios, listados del PDF) como <ul> con peso de marca."""
        items = items if isinstance(items, list) else ([items] if items else [])
        lis = "".join('<li class="mb-1">%s</li>' % self._esc(i) for i in items if i and str(i).strip())
        return ('<ul class="list-unstyled fw-bold text-uppercase">%s</ul>' % lis) if lis else ""

    # ---------- snippets ----------
    def _snip_banner(self, sec):
        c = sec.get("copy") or {}
        sid, cc = self._sid(sec), self._cc(sec, "o_cc1")
        eyebrow = ('<p class="o_small text-uppercase mb-2" style="letter-spacing:.12em">%s</p>'
                   % self._esc(c.get("eyebrow"))) if c.get("eyebrow") else ""
        head = '<h1 class="display-2">%s</h1>' % self._esc(c.get("headline") or sec.get("label") or "")
        sub = '<p class="lead">%s</p>' % self._esc(c.get("subheadline")) if c.get("subheadline") else ""
        btn = self._btn(c.get("button"), "btn btn-lg btn-primary")
        img = self._img(sec.get("image"))
        img_col = '<div class="col-lg-6 pt16 pb16">%s</div>' % img if img else ""
        text_w = "col-lg-6" if img else "col-lg-10"
        return (
            '<section id="%s" class="s_banner o_cc %s pt96 pb96" data-snippet="s_banner" data-name="Banner">'
            '<div class="container"><div class="row align-items-center">'
            '<div class="%s pt16 pb16">%s%s%s%s</div>%s'
            '</div></div></section>'
        ) % (sid, cc, text_w, eyebrow, head, sub, btn, img_col)

    def _snip_text_image(self, sec):
        c = sec.get("copy") or {}
        sid, cc = self._sid(sec), self._cc(sec, "o_cc1")
        head = '<h2 class="h3-fs">%s</h2>' % self._esc(c.get("headline") or sec.get("label") or "")
        body = self._paras(c.get("body")) + self._list(c.get("list"))
        btn = self._btn(c.get("button"), "btn btn-secondary")
        img = self._img(sec.get("image"))
        if img:
            return (
                '<section id="%s" class="s_text_image o_cc %s pt80 pb80" data-snippet="s_text_image" data-name="Text - Image">'
                '<div class="container"><div class="row align-items-center">'
                '<div class="col-lg-6 pt16 pb16">%s%s%s</div>'
                '<div class="col-lg-6 pt16 pb16">%s</div>'
                '</div></div></section>'
            ) % (sid, cc, head, body, btn, img)
        # sin imagen → degradar a bloque de texto
        return (
            '<section id="%s" class="s_text_block o_cc %s pt64 pb64" data-snippet="s_text_block" data-name="Text">'
            '<div class="container s_allow_columns">%s%s%s</div></section>'
        ) % (sid, cc, head, body, btn)

    def _snip_features(self, sec):
        c = sec.get("copy") or {}
        sid, cc = self._sid(sec), self._cc(sec, "o_cc1")
        feats = [f for f in (c.get("features") or []) if (f.get("title") or f.get("text"))]
        if not feats:  # sin features → bloque de texto
            return self._snip_text_block(sec)
        intro = ""
        if c.get("headline"):
            intro += '<h2 class="h3-fs">%s</h2>' % self._esc(c["headline"])
        if c.get("subheadline"):
            intro += '<p class="lead">%s</p>' % self._esc(c["subheadline"])
        col = max(3, 12 // min(max(len(feats), 1), 4))  # 2→6, 3→4, 4→3 columnas
        cards = ""
        for f in feats[:4]:
            icon = re.sub(r"[^a-z0-9-]", "", (f.get("icon") or "star-o").lower().replace("fa-", "")) or "star-o"
            cards += (
                '<div class="col-lg-%s">'
                '<i class="s_features_icon fa fa-%s mb-3 rounded bg-o-color-3" role="img"/>'
                '<div class="overflow-hidden"><h3 class="h5-fs">%s</h3><p>%s</p></div>'
                '</div>'
            ) % (col, self._esc(icon), self._esc(f.get("title")), self._esc(f.get("text")))
        return (
            '<section id="%s" class="s_features o_cc %s pt64 pb64" data-snippet="s_features" data-name="Features">'
            '<div class="container">%s<div class="row">%s</div></div></section>'
        ) % (sid, cc, intro, cards)

    def _snip_image_gallery(self, sec):
        sid, cc = self._sid(sec), self._cc(sec, "o_cc1")
        imgs = [u for u in (sec.get("images") or []) if re.match(r"/web/image/\d+", str(u))]
        if not imgs and sec.get("image"):
            imgs = [sec["image"]]
        if not imgs:  # sin imágenes → no tiene sentido la galería
            return self._snip_text_block(sec)
        cid = "slideshow_%s" % sid
        items, dots = "", ""
        for i, u in enumerate(imgs):
            active = " active" if i == 0 else ""
            items += (
                '<div class="carousel-item%s">'
                '<img class="img img-fluid d-block mh-100 mw-100 mx-auto rounded object-fit-cover" '
                'src="%s" data-name="Image" data-index="%s" alt=""/></div>'
            ) % (active, self._esc(u), i)
            dots += (
                '<button type="button" data-bs-target="#%s" data-bs-slide-to="%s" '
                'style="background-image: url(%s)" class="%s" aria-label="Slide %s"/>'
            ) % (cid, i, self._esc(u), "active" if i == 0 else "", i + 1)
        return (
            '<section id="%s" class="s_image_gallery o_cc %s o_slideshow pt24 pb24 '
            's_image_gallery_controllers_outside s_image_gallery_controllers_outside_arrows_right '
            's_image_gallery_indicators_dots" data-vcss="002" data-columns="3" '
            'data-snippet="s_image_gallery" data-name="Image Gallery">'
            '<div class="o_container_small overflow-hidden">'
            '<div id="%s" class="carousel carousel-dark slide" data-bs-ride="false" data-bs-interval="0">'
            '<div class="carousel-inner">%s</div>'
            '<div class="o_carousel_controllers">'
            '<button class="carousel-control-prev o_not_editable" data-bs-target="#%s" data-bs-slide="prev" aria-label="Previous" title="Previous">'
            '<span class="carousel-control-prev-icon" aria-hidden="true"/></button>'
            '<div class="carousel-indicators">%s</div>'
            '<button class="carousel-control-next o_not_editable" data-bs-target="#%s" data-bs-slide="next" aria-label="Next" title="Next">'
            '<span class="carousel-control-next-icon" aria-hidden="true"/></button>'
            '</div></div></div></section>'
        ) % (sid, cc, cid, items, cid, dots, cid)

    def _snip_call_to_action(self, sec):
        c = sec.get("copy") or {}
        sid, cc = self._sid(sec), self._cc(sec, "o_cc5")
        head = '<h2 class="h3-fs">%s</h2>' % self._esc(c.get("headline") or sec.get("label") or "")
        sub = '<p class="lead">%s</p>' % self._esc(c.get("subheadline")) if c.get("subheadline") else ""
        btn = self._btn(c.get("button"), "btn btn-primary btn-lg")
        right = ('<div class="col-lg-3"><p class="text-lg-end">%s</p></div>' % btn) if btn else ""
        left = "col-lg-9" if btn else "col-lg-12"
        return (
            '<section id="%s" class="s_call_to_action o_cc %s pt64 pb64" data-snippet="s_call_to_action" data-name="Call to Action">'
            '<div class="container"><div class="row align-items-center">'
            '<div class="%s">%s%s</div>%s'
            '</div></div></section>'
        ) % (sid, cc, left, head, sub, right)

    def _snip_text_block(self, sec):
        c = sec.get("copy") or {}
        sid, cc = self._sid(sec), self._cc(sec, "o_cc1")
        head = '<h2 class="h3-fs">%s</h2>' % self._esc(c["headline"]) if c.get("headline") else ""
        body = self._paras(c.get("body")) + self._list(c.get("list"))
        if not body:
            body = '<p>%s</p>' % self._esc(c.get("subheadline") or sec.get("label") or "")
        return (
            '<section id="%s" class="s_text_block o_cc %s pt40 pb40" data-snippet="s_text_block" data-name="Text">'
            '<div class="container s_allow_columns">%s%s</div></section>'
        ) % (sid, cc, head, body)
