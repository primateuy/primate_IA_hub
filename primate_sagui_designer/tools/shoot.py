#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shoot.py — Capturador de evidencia visual para el verificador de Sagui.

Corre FUERA del worker de Odoo (subprocess): Playwright es asyncio + un navegador entero, y no
tiene por qué vivir dentro de un request ni de una transacción. Lo dispara
primate_sagui_designer/models/sagui_verifier_web.py desde el cron de build.

Saca capturas full-page a varios anchos, en sesión anónima y en sesión logueada, y además corre
SONDAS DE DOM. Las sondas no son un adorno: en Odoo 19 la barra de edición
(`.o_frontend_to_backend_nav`) se sirve con `d-none` y recién `redirect.js` le pone `d-flex` en
DOMContentLoaded cuando el usuario no es público. Mirarla en un PNG es frágil; preguntarle al DOM
si está y si quedó visible es determinístico. Lo mismo con el overflow horizontal.

Uso:
    python shoot.py --base-url http://localhost:8069 --path / --out-dir /tmp/shots \\
                    --breakpoints 375,768,1440 --login admin --password admin

Salida: un JSON por stdout (ver SALIDA abajo). Todo error se reporta en ese JSON, no por excepción:
el verificador tiene que poder decir "no pude capturar X" en vez de romperse.
"""
import argparse
import json
import os
import sys
import time

# Se le da tiempo al frontend a montar los assets (el bundle puede recompilar en la 1ª visita).
NAV_TIMEOUT_MS = 45000
SETTLE_MS = 1200

# Sondas de DOM: cada una devuelve algo verificable contra la rúbrica.
PROBE_JS = r"""
() => {
  const txt = (el) => (el ? (el.textContent || '').trim().replace(/\s+/g, ' ') : '');
  const header = document.querySelector('header') || document.querySelector('#top');
  const footer = document.querySelector('footer') || document.querySelector('#bottom');
  const navEl = document.querySelector('.o_frontend_to_backend_nav');
  const navLinks = Array.from(
    (header || document).querySelectorAll('#top_menu a, .navbar-nav a, header nav a')
  ).map((a) => (a.textContent || '').trim()).filter((t) => t.length);
  const logo = document.querySelector('header img, #top img, .navbar-brand img');
  const footerText = txt(footer);
  const demoTells = [
    'info@yourcompany', '+1 555', 'yourcompany.example.com',
    'Useful Links', 'We are a team of passionate people',
  ].filter((t) => footerText.toLowerCase().includes(t.toLowerCase()));
  return {
    title: document.title || '',
    // D4 — barra de edición: presente en el DOM Y efectivamente visible (sin d-none).
    edit_bar_present: !!navEl,
    // OJO: no usar offsetParent para esto. La barra es `position: fixed`, y para un elemento
    // fixed offsetParent devuelve null aunque esté perfectamente visible → falso negativo.
    edit_bar_visible: (() => {
      if (!navEl) { return false; }
      const cs = getComputedStyle(navEl);
      const r = navEl.getBoundingClientRect();
      return cs.display !== 'none' && cs.visibility !== 'hidden'
             && parseFloat(cs.opacity || '1') > 0 && r.width > 0 && r.height > 0;
    })(),
    // D1 — el header trae la nav del diseño, no la demo.
    has_header: !!header,
    nav_items: navLinks.slice(0, 20),
    nav_demo_tells: navLinks.filter((t) => ['Home', 'Contact Us', 'Shop', 'Inicio y contacto']
                                            .includes(t)),
    // D2 — footer propio, sin restos de la demo de Odoo.
    has_footer: !!footer,
    footer_demo_tells: demoTells,
    footer_excerpt: footerText.slice(0, 400),
    // D3 — logo real.
    logo_src: logo ? (logo.getAttribute('src') || '') : '',
    // B6 — overflow horizontal al ancho actual.
    scroll_width: document.documentElement.scrollWidth,
    client_width: document.documentElement.clientWidth,
    horizontal_overflow:
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    // Señal de que la página está dentro de website.layout (constraint 1).
    in_website_layout: !!(header && footer),
    section_ids: Array.from(document.querySelectorAll('main section[id], #wrap section[id]'))
                      .map((s) => s.id).slice(0, 30),
  };
}
"""


def _login(context, base_url, login, password):
    """Login real por el form de /web/login. Devuelve (ok, detalle)."""
    page = context.new_page()
    try:
        page.goto(base_url.rstrip("/") + "/web/login", timeout=NAV_TIMEOUT_MS,
                  wait_until="domcontentloaded")
        # El selector se acota al FORM de login: en una instancia con website, /web/login trae
        # además el header del sitio, y un `button[type=submit]` suelto matchea el botón de
        # búsqueda del header antes que el de login.
        form = page.locator("form.oe_login_form")
        form.locator("input[name='login']").fill(login)
        form.locator("input[name='password']").fill(password)
        form.locator("button[type='submit']").first.click()
        page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        # Si seguimos en /web/login, las credenciales no sirvieron.
        if "/web/login" in page.url:
            alert = page.query_selector(".alert-danger")
            return False, (alert.inner_text().strip() if alert else "credenciales rechazadas")
        return True, page.url
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    finally:
        page.close()


def _capture(context, url, width, out_path, label):
    """Una captura full-page a un ancho + las sondas de DOM de esa vista."""
    page = context.new_page()
    try:
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="networkidle")
        page.wait_for_timeout(SETTLE_MS)
        page.screenshot(path=out_path, full_page=True)
        probes = page.evaluate(PROBE_JS)
        return {"label": label, "width": width, "path": out_path, "probes": probes, "error": None}
    except Exception as e:  # noqa: BLE001
        return {"label": label, "width": width, "path": None, "probes": {}, "error": str(e)}
    finally:
        page.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Capturas + sondas de DOM para el verificador de Sagui.")
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--path", default="/")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--breakpoints", default="375,768,1440")
    ap.add_argument("--login", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("--admin-width", type=int, default=1440,
                    help="Ancho de la única captura logueada (evidencia de la barra de edición).")
    args = ap.parse_args(argv)

    result = {"ok": False, "shots": [], "errors": [], "admin_session": None}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        result["errors"].append("playwright no está instalado en este intérprete: %s" % e)
        print(json.dumps(result))
        return 2

    os.makedirs(args.out_dir, exist_ok=True)
    url = args.base_url.rstrip("/") + (args.path if args.path.startswith("/") else "/" + args.path)
    widths = [int(w) for w in args.breakpoints.split(",") if w.strip().isdigit()]
    stamp = str(int(time.time()))

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(args=["--force-color-profile=srgb"])
        except Exception as e:  # noqa: BLE001
            result["errors"].append("no pude lanzar Chromium: %s" % e)
            print(json.dumps(result))
            return 2

        # --- sesión anónima: así lo ve un visitante ---
        anon = browser.new_context(ignore_https_errors=True)
        for w in widths:
            out = os.path.join(args.out_dir, "shot_%s_%s_anon.png" % (stamp, w))
            result["shots"].append(_capture(anon, url, w, out, "%spx anónimo" % w))
        anon.close()

        # --- sesión logueada: sólo para evidenciar la barra de edición (D4) ---
        if args.login and args.password:
            admin = browser.new_context(ignore_https_errors=True)
            ok, detail = _login(admin, args.base_url, args.login, args.password)
            result["admin_session"] = {"ok": ok, "detail": detail}
            if ok:
                out = os.path.join(args.out_dir, "shot_%s_%s_admin.png" % (stamp, args.admin_width))
                result["shots"].append(
                    _capture(admin, url, args.admin_width, out,
                             "%spx logueado" % args.admin_width))
            else:
                result["errors"].append("no pude iniciar sesión: %s" % detail)
            admin.close()
        else:
            result["admin_session"] = {"ok": False, "detail": "sin credenciales"}

        browser.close()

    result["ok"] = any(s.get("path") for s in result["shots"])
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
