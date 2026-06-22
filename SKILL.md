---
name: odoo-site-from-design
description: >
  Use this skill WHENEVER you generate or reproduce a website from a design reference
  (PDF, image, screenshot, mockup) inside Odoo, or restyle an Odoo website to match a
  brand/design — even if the user just says "make the site look like this PDF" or
  "replace the company's website design". Covers: extracting a design system from the
  reference, producing faithful SCOPED HTML/CSS, and integrating it as a REAL, functional
  Odoo website (working navigation, reachable backend, real images and logo) instead of an
  orphan full-screen page. Always consult this before writing the generation prompt, the
  page template, or the styling/asset code for a design-to-site feature.
---

# Odoo site from a design reference

## Goal and non-goals
- **Goal:** the company's actual Odoo website looks like the design AND stays a functional
  site — navigation works, the backend is reachable, buttons go somewhere, images are real.
- **Faithful, not a clone.** Reproduce palette, type feel, layout order, spacing rhythm and
  real imagery. Exact bespoke typography, hand-drawn elements and one-off collages are
  approximated or omitted; the user finishes art direction by hand. Say this; don't oversell.
- **Never an orphan page.** A full-screen custom HTML page with no layout = dead buttons, no
  nav, no way back to the backend. Always integrate into the website framework (below).
- **Reproduction ≠ generation.** If a generic design-system *generator* skill is also installed
  (one that recommends palette/style/fonts from the product's industry), do NOT run it to
  "design" this site. A reproduction's design system is the reference itself; a recommender will
  impose its own palette/style and *reduce* fidelity. Use such generators for greenfield only —
  borrow only their universal UX/quality checklists, never their palette/style/stack choices.
  In particular, ignore any default to a utility-CSS framework (e.g. Tailwind): inside Odoo,
  styling stays a scoped plain-CSS asset (see constraint 2), not a global framework + build step.

## Output mode: theme module vs one-off page
Two ways to deliver — pick by intent. Pass 1 (extract the design system) feeds BOTH identically.
- **Theme module — preferred when replacing the whole site's identity.** Codify the design
  SYSTEM as a proper Odoo theme: palette + color-combinations, typography (load matched fonts as
  theme assets), header/footer templates, button/snippet styling. SCSS lives in the theme's
  `primary_variables.scss` etc., **compiled at install** — robust, consistent everywhere, and
  native snippets become on-brand and editable in the builder. This is the *structural* fix for
  header/footer/palette/contrast: they're defined once, correctly, instead of patched per page.
  Caveat: a theme reproduces the design *language* faithfully; it does NOT auto-compose the
  bespoke page layout — the homepage is still assembled on top (native snippets styled by the
  theme, or a custom page). Write valid SCSS; a broken value breaks the theme's assets (scoped,
  not the whole instance) — but never reach for runtime `make_scss_customization` for this.
- **Scoped page — fine for a one-off landing.** A single `website.page` + a namespaced CSS asset
  (constraints below). Faster and more pixel-faithful for one bespoke page, but it does NOT
  restyle the rest of the site and isn't reusable.

**Composition via a snippet schema (most editable, best for a reusable generator).** When the
homepage is composed with native snippets on a theme, first have the interpreter emit a reviewable
*schema*: an ordered list mapping each reference section to the closest native snippet (`s_banner`,
`s_text_image`, `s_features`, `s_image_gallery`, `s_call_to_action`, `s_text_block`…) with its
copy, chosen extracted image, and a color-combination (`o_cc1..o_cc5`) for the light/dark rhythm.
Confirm the schema, then instantiate. Separating interpret (PDF→schema) from build (schema→page)
is what catches mapping errors early. Fidelity is bounded by the snippet vocabulary (not
pixel-perfect), but the result is fully builder-editable and maintainable — the right trade for a
generator that any client will own and edit.

**Make the mode configurable (best for a reusable generator).** Expose the body composition as a
per-generation switch: `fiel` = scoped custom HTML for maximum layout fidelity (edited by code);
`editable` = native snippets from the schema for full builder editability. Both modes share the
SAME Pass 1 extraction AND the SAME generated theme — the theme owns header/footer/palette/
typography/menus/logo in either mode, so those are never re-fought. Only the homepage *body*
renderer differs. This turns the fidelity-vs-editability tension into a user choice instead of an
architecture bet.

## Hard architecture constraints (Odoo)
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

## Method: extract a design system FIRST, then render, then critique
Mirror the discipline of a real design pass. Do passes 1–3 every time.

### Pass 1 — Extract the design system (write it down as tokens before any HTML)
- **Palette WITH ROLES, not positional.** Decide which color is the page background, which is
  surface, which is text, which is the accent. The most common failure is using an *accent*
  (e.g. a tan) as the full-page background. If the reference reads black/editorial, the
  background is dark or cream and text is its high-contrast pair — derive roles from how the
  reference actually looks, not from array order.
- **Type — define an EXPLICIT scale, don't eyeball it.** Set exact `font-family`, size,
  weight, line-height, letter-spacing and case for each role (display, section heading, eyebrow,
  body, list item, caption) and bind every element to it via CSS custom properties. "Make it
  look similar" drifts section to section; numbers don't. Name the matched web fonts and load
  them via Google Fonts: poster-style condensed display → a tall condensed grotesque
  (e.g. Anton / Archivo Narrow ExtraBold); body/nav → a neutral grotesque (e.g. Archivo, Inter);
  handwritten asides → a script face (e.g. Caveat). Type is most of the personality — getting
  the family, weight and scale right does more for fidelity than any other single change.
- **Spacing & grid:** a spacing scale and the column/grid logic.
- **Section list & rhythm:** the ordered sections and how they alternate (e.g. dark hero →
  light about → dark CTA). Rhythm is what makes it feel designed.
- **Signature:** the single most characteristic element of the reference. Reproduce that well;
  keep everything else quiet.
- **Map copy to its source section.** Read where each text block sits in the reference and bind
  it there. Never let leftover lines (a handwritten aside, a tagline) float into the hero just
  because they were extracted — misplaced real copy reads as if it were invented.
- **Ignore designer annotations & placeholders — they are not site copy.** Design references
  routinely contain the author's own notes: font specs ("algo con esta letra", "use this type"),
  lorem ipsum, "Your Logo", crop marks, scribbles, comments in a different language from the site.
  These are instructions to a human, not content. Never render them. If a line looks like a note
  about the design rather than a message to the visitor, drop it.
- **Background vs inline images.** Flag sections that are *text over a photo* (e.g. a CTA band):
  that image is a full-bleed section **background** with the text overlaid, not a gallery
  thumbnail. Carry a per-section `background_image` for these.
- **Per-section layout.** Capture each section's specific layout (a heavy vertical list beside a
  photo, a 4-up gallery, a centered band over an image) and reproduce it — don't substitute a
  generic stacked block.

### Pass 2 — Render
- Semantic HTML; CSS custom properties for the tokens from Pass 1; everything under the
  namespace wrapper. Responsive down to mobile; visible keyboard focus; honor
  `prefers-reduced-motion`. Interpolate the real copy in the reference's language.

### Pass 3 — Self-critique against the reference (fix before returning)
Run the checklist; if any item fails, fix it, don't ship it.

## Fidelity & function checklist
- [ ] Dominant color matches the reference (an accent did NOT become the page background)
- [ ] Display type weight/case/scale matches the reference's feel
- [ ] Sections are in the reference's order with comparable rhythm/spacing
- [ ] Extracted images are placed (no stock placeholders); logo is the real one
- [ ] Every button/link has a real target; nav scrolls/links correctly
- [ ] CSS is namespaced; no global selectors; no `make_scss_customization`
- [ ] Page uses the website layout; admin edit bar present; `/odoo` reachable
- [ ] Responsive, keyboard-focusable, reduced-motion respected
- [ ] Header/footer styled deliberately and READABLE (not light-on-light); real frontend
      selectors, not just `.brandsite`
- [ ] Design's nav items created as real menus (demo Home/Contact Us/phone/CTA removed); nav not
      duplicated in the body
- [ ] Text-over-image sections use the photo as a full-bleed background
- [ ] Every piece of copy sits in its source section (nothing real left floating out of place)
- [ ] Explicit type scale applied (matched fonts; sizes/weights consistent across sections)
- [ ] Footer replaced with the design's content (no leftover Odoo demo footer)

## Craft checklist (universal polish — enforce on every build)
These generalize the pre-delivery checks a good UI skill runs; they're stack-agnostic and safe
inside Odoo.
- [ ] Icons are SVG (Heroicons/Lucide), never emojis-as-icons
- [ ] `cursor-pointer` on every clickable element; hover states with 150–300ms transitions
- [ ] Text contrast ≥ 4.5:1 against its actual background (re-check after any dark/light section)
- [ ] Visible keyboard focus on all interactive elements
- [ ] `prefers-reduced-motion` respected for every animation
- [ ] Layout verified at 375 / 768 / 1024 / 1440 px (no overflow, no broken stacking)
- [ ] No AI-default tells (unmotivated purple/pink gradients, generic stock that isn't the
      reference's own imagery)

## When wiring this into a PDF/image → site tool
- The Pass 1–3 method above IS the system prompt for the multimodal generation call. Send the
  rendered page image(s) + extracted text + the extracted image URLs; ask for the tokens first,
  then the namespaced HTML/CSS, then a critique-and-fix.
- Show a PREVIEW (detected sections + the token system) and require confirmation before
  publishing/replacing the homepage.
- Keep the prior homepage recoverable (don't hard-delete) so a bad generation is reversible.
- **Generate section by section, not one blob.** A single giant HTML generation drifts on
  per-section layout and image placement. Generate each section focused on its own crop of the
  reference, with its mapped image, then assemble. This raises layout fidelity far more than
  re-rolling the whole page.
- **Output an explicit image→section map** in the preview (which extracted photo goes with which
  section, from the reference's adjacency) so a wrong photo is caught before publishing.
- **Know the stop point.** The pipeline's job is a strong, on-brand, functional draft with the
  right structure, copy, images and palette. Exact type sizes, spacing and bespoke per-section
  nuance are a DETERMINISTIC finishing pass on the scoped CSS tokens — fast and reliable — not
  something to chase by re-rolling the generation. Re-generating to fix a font size is the slow,
  unreliable path; editing the `--fs-*` custom property is the fast one.
