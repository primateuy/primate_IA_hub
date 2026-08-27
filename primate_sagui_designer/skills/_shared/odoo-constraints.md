# Odoo — las 8 restricciones de arquitectura (checklist compartido)

Checklist común a `odoo-site-from-design` y `odoo-site-greenfield-design`. No son
recomendaciones: un sitio que incumple cualquiera de estas no se entrega. Vive en `_shared/`
porque las dos skills lo necesitan idéntico — duplicarlo es cómo se desincronizan.

1. **Serve inside the website layout.** The generated homepage is a `website.page` that uses
   the standard `website.layout` (header + footer + the logged-in admin edit bar). This keeps
   navigation working and the backend reachable. The Odoo backend is ALWAYS at `/odoo`
   (or `/web`) regardless of the front-end — never lock the user out of it.
2. **Style via a scoped frontend CSS asset — NEVER `make_scss_customization`.**
   `make_scss_customization` edits the theme's SCSS *variables*; a single bad value breaks the
   compilation of the whole instance's assets ("css error occured, using an old style…").
   Instead add an `ir.asset` (a plain `.css`/`.scss` file) to `web.assets_frontend`. A plain
   CSS file fails in isolation; it cannot take down the theme. Scope it to the target website
   if multi-website.
3. **Namespace all custom CSS** under a single wrapper (e.g. `.brandsite { … }`) so nothing
   leaks to other pages or the backend. No global element selectors (`body`, `h1` bare).
4. **No injected `<script>`** in generated markup. Behaviour comes from real links/anchors.
5. **Real targets for every button/link.** In-page anchors (`#servicos`), real pages
   (`/contactus`), `mailto:`, `tel:`, `https://wa.me/…`. Never leave `href="#"` dead.
6. **Real assets.** Place the EXTRACTED images by their `ir.attachment` URL; set the extracted
   logo as `website.logo`. Never leave snippet/theme stock placeholders ("Your Logo", library
   stock photos).
7. **Restyle the header/footer DELIBERATELY — they live OUTSIDE the body wrapper.** The body
   content is namespaced under `.brandsite`, but Odoo's header/footer are part of
   `website.layout`, *outside* that wrapper, so the namespaced CSS never reaches them. If you
   only style `.brandsite`, the header keeps its default look and frequently ends up unreadable
   (classic symptom: light text on a light bar — invisible). Style the real frontend header/
   footer selectors explicitly (e.g. `header#top` and the top menu) in the website-scoped asset,
   with deliberate contrast that matches the design. [VERIFY exact header selectors against the
   installed website addon.] A functional, on-brand header beats a pixel-perfect dead one.
8. **Replace the demo header content with the design's own nav.** Swap Odoo's default menu items
   (Home, Contact Us), the demo phone and the demo CTA for the reference's nav items as real
   `website.menu` records pointing at the in-page anchors, plus a real CTA. Do NOT also render a
   second copy of the nav inside the body — one functional nav, living in the header. **Same for
   the footer:** replace Odoo's demo footer (Useful Links, the "We are a team…" blurb,
   `info@yourcompany.example.com`, the +1 555 phone, the demo social icons) with the design's
   own footer content, styled on-brand. A leftover demo footer is the most common tell.

## Cómo se verifica cada una
La 1, la 6, la 7 y la 8 dejan rastro observable y las comprueba el verificador visual
(`references/verifier-rubric.md`, bloque D) — parte por DOM y parte por captura. Las demás son
contrato de construcción: se cumplen al generar, y romperlas no siempre se ve en una captura.
