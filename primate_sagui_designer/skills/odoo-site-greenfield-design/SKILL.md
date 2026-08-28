---
name: odoo-site-greenfield-design
description: >
  Use this skill WHENEVER a website, landing page, homepage or theme must be designed inside
  Odoo WITHOUT a design reference to copy — the user gives a company, a rubro, a brief, a logo
  or nothing at all and expects a site that "looks professional", "looks pro", "not generic",
  "like an agency did it". Also use it when an existing Odoo site is judged ugly, standard,
  templated or badly assembled and must be redesigned. Do NOT use it when there IS a PDF /
  mockup / screenshot to reproduce — that is the job of odoo-site-from-design. Both skills
  share the Odoo constraints and the QA/verifier step; this one adds the art-direction stage
  that the reproduction skill takes from the reference.
---

# Odoo site — greenfield design (art direction first)

## Why sites come out ugly, and what this skill fixes
Generic output is not a model problem; it is a process problem. A site looks templated when the
agent goes straight from "make a website for X" to HTML. Every ugly result shares the same causes:

1. **No art direction.** Nobody chose a concept, a palette with roles, or type with personality,
   so the model reaches for its statistical default (centered hero, three cards, a gradient).
2. **Colors chosen by position, not role.** An accent ends up as page background; text sits on a
   surface it was never paired with; Odoo's `o_cc` combinations stay at their defaults.
3. **One-shot generation.** A single big HTML blob drifts section to section; CSS selectors cancel
   each other and margins between blocks break ("mal ensamblado").
4. **No eyes.** The agent reads its own HTML and believes it. Without a screenshot and an
   independent critique, nothing gets fixed.

This skill imposes the process that makes design tools like Claude Design look good:
**brief → plan → self-critique of the plan → build section by section → screenshot → cross-model
critique → deterministic fix**. Never skip a stage. Never write markup before the plan is approved.

## Hard rules inherited from Odoo (non-negotiable)
Read `odoo-site-from-design` §"Hard architecture constraints" and apply all 8. In short:
`website.page` on `website.layout`; styling as a scoped `ir.asset` in `web.assets_frontend`
(NEVER `make_scss_customization`); everything namespaced under one wrapper; no injected
`<script>`; real `href` targets; real logo/images; header/footer styled *deliberately* through the
real frontend selectors (they live outside the wrapper); demo nav/footer content replaced.
Theme module when replacing the whole identity, scoped page for a one-off landing.

---

## Stage 0 — Pin the brief (2–5 lines, written down)
If the brief is vague, decide these yourself and state them; do not ask unless truly blocked:
- **Subject:** one concrete thing (not "a company"): *a family-run dental clinic in Ciudad de la
  Costa*, *an agro-inputs distributor selling to 200-ha farms*.
- **Audience** and **the page's single job** (book a visit, request a quote, download a catalog).
- **Available real material:** logo, photos, colors already in use, existing copy. Real material
  always beats invented material.
- **Reference sites given as URLs**, if any — see below. A URL is *not* a design to reproduce.
- **Tone words (3):** e.g. *sobrio, técnico, cercano*.

The subject's own world — its materials, tools, vocabulary, colors of its physical environment —
is where distinctive choices come from. A vineyard, a dental clinic and a logistics firm must not
share a palette.

### When the brief names a reference URL

A URL is the **fourth kind of material**, next to *PDF/mockup*, *partial (logo, colors)* and
*nothing*. It does **not** route to `odoo-site-from-design`: there is no design to reproduce
here, only a way of composing to learn from.

1. Capture it live with `tools/shoot.py` at **1440 and 375**, the same tool as everywhere else.
2. From the capture read **only these six things**, and write them into the plan as observations:
   hero kind · section order and rhythm · density · image kind · motion budget · nav/footer shape.
3. Pick the closest register file and **note the deviations** — where the reference does something
   its register does not.
4. Save the captures in the `sagui.design.run` as evidence, like any other reference.

**What must never cross from a reference URL into the plan: palette, typefaces and copy.**
Reading a hex value off a screenshot is the most tempting thing in this whole skill and the one
that produces a site that belongs to somebody else. If a generated site carries a reference's
color, font or wording, that is a **C1 finding** and the page is not "listo".

The point of capturing a reference is to answer *"how is this page put together?"* — never
*"what does it look like?"*

## Stage 1 — Design plan (text only; NO HTML/CSS yet)
Produce this compact plan. Every later decision derives from it.

```
REGISTER     <key> — how the page is COMPOSED, chosen from `_shared/registers/`:
             tech-minimal | tech-warm | tech-editorial
             + ONE line of justification tied to THIS subject and audience
CONCEPT      one sentence: what this page feels like and why that fits the subject
PALETTE      4–6 named hex values WITH ROLES:
             bg (page) / surface / text / text-muted / accent / accent-contrast
             + which Odoo color-combination (o_cc1..o_cc5) maps to which role
TYPE         display face (characterful, used with restraint) — weight, case, tracking
             body face (neutral, readable) — sizes for body / small / caption
             optional utility face for data or labels
             explicit scale: --fs-display, --fs-h2, --fs-h3, --fs-body, --fs-small
             (Google Fonts, loaded as theme/asset — never assume a font is installed)
RHYTHM       ordered section list with light/dark alternation, e.g.
             hero(dark) → services(light) → proof(surface) → CTA(accent) → footer(dark)
LAYOUT       one-line description + ASCII wireframe per section (desktop and mobile note)
SIGNATURE    the ONE element this site will be remembered by, tied to the subject
             (a typographic device, a color block, an image treatment, a real motion moment)
MOTION       where motion serves (one orchestrated moment beats scattered effects) or "none"
COPY VOICE   register, verbs, what the CTA literally says
```

**REGISTER is not optional and it is not a mood.** A register fixes the *composition* — hero
kind, section rhythm, density, image kind, motion budget, nav/footer shape — and supplies
**no colors, no typefaces and no signature**; those still come from the subject. Read the chosen
file in full before writing the rest of the plan: every field below has to be consistent with it.

Default mapping when the brief only says "profesional / moderno / pro":

| Subject | Register |
|---|---|
| software, SaaS, agencies, consulting, fintech — anything selling a digital product | `tech-minimal` |
| …but with real content and ideas to publish (research, cases, docs, a blog that matters) | `tech-editorial` |
| services with people at the center: health, education, local business, studios, food | `tech-warm` |

You may depart from the default — the subject knows better than the table — but **say so in the
plan**, in the same justification line. "Modern and professional" is not a register: it is the
request that makes you choose one.

**If the register is not decided, nothing gets generated.** A plan without `REGISTER` is
incomplete and Stage 3 does not start.

Guidance for the plan:
- **Type carries the personality.** Pair display and body deliberately; do not use the same
  pairing you would reach for on any other project. Set numbers, not adjectives.
- **The hero is a thesis.** Open with the most characteristic thing in the subject's world. The
  "big number + small label + supporting stats + gradient" hero is the template answer.
- **Structure is information.** Numbering (01/02/03), eyebrows, dividers must encode something
  true about the content. A numbered list is only right if the content is a real sequence.
- **Match complexity to the concept.** Minimal directions need precision in spacing and type;
  maximal ones need elaborate execution. Elegance = executing the chosen vision well.
- **Copy is design material.** Plain verbs, sentence case, specific over clever. Buttons say
  what happens ("Pedir turno", not "Enviar"). No filler paragraphs, no "Bienvenidos a nuestra web".

## Stage 2 — Critique the plan BEFORE building
Ask, per item of the plan: *"Would I have produced this for any similar brief?"*
Run a mental control: imagine the same prompt for a competitor in the same rubro and see if you
land on the same palette/hero/layout. Anything that matches the generic answer gets revised.
Write down what you changed and why (one line each). Only then proceed.

Then check the plan **against its own register**: does the hero, the rhythm, the density and the
footer actually match the file you chose, or did you write a `tech-editorial` header on top of a
three-card `tech-minimal` page? A register executed halfway reads worse than no register — and
each file ends with a *"What ruins this register"* section that is precisely the list of ways to
get it wrong.

**Vetoed defaults** (allowed only if the brief explicitly asks for them):
- warm cream bg (~#F4F1EA) + high-contrast serif display + terracotta/clay accent (~#D97757)
- near-black bg + one acid-green or vermilion accent
- broadsheet look: hairline rules, zero radius, dense newspaper columns
- purple/pink or blue-violet gradients anywhere
- centered hero + 3 icon cards + "Nuestros servicios" grid as the whole page
- emoji as icons; library stock photos with no relation to the subject
- decorative 01/02/03 numbering on non-sequential content
- generic testimonial carousel with invented names
Full list with reasons: `_shared/anti-patterns.md` (shared with `odoo-site-from-design`).

## Stage 3 — Build, section by section
- Emit the **section schema** first (section → mode: native snippet with `o_cc` / scoped HTML;
  copy; image; background-vs-inline flag). Confirm it with the user (Sagui shows it as a proposal).
- Generate **one section at a time**, each derived from the plan tokens; then assemble. A single
  giant generation drifts.
- Tokens as CSS custom properties on the wrapper (`--c-bg`, `--c-accent`, `--fs-*`, `--space-*`).
  Every element binds to a token; no hard-coded hex or px outside the token block.
- **Selector discipline:** one wrapper, class-based selectors of uniform specificity, spacing
  owned by the section (`.brandsite .sec { padding-block: var(--space-xl) }`) — never mix
  element selectors and class selectors for the same property; that is what breaks the seams
  between blocks.
- Header/footer: style the real frontend selectors from the plan's palette (contrast checked);
  create real `website.menu` records; replace demo footer content.
- Icons: SVG (Lucide/Heroicons). Images: real ones, sized and cropped for the slot; text-over-photo
  sections use the image as full-bleed background with a contrast layer.
- Responsive at 375/768/1024/1440; visible focus; `prefers-reduced-motion` respected;
  hover transitions 150–300 ms; `cursor: pointer` on clickables.

## Stage 4 — Screenshot and cross-model critique (the step that actually fixes ugliness)
1. Render the real Odoo page (Playwright) at 375, 768 and 1440 px, full page.
2. Send the screenshots + the design plan to a **reviewer model from a different provider than
   the generator** (Sagui's cross-review rule) with `references/verifier-rubric.md` as the rubric.
3. The reviewer returns a list of concrete failures, each with: rubric item, location (section,
   breakpoint), and the smallest change that fixes it. "Improve the design" is not an accepted
   finding.
4. Apply fixes. Anything about size, spacing, color or weight is a **deterministic edit of the
   token block**, not a regeneration. Regenerate a section only for layout/structure failures.
5. Re-screenshot, re-review. Stop when the rubric passes or after 3 loops (then report what is
   left and why).

## Stage 5 — Know the stop point
Deliver a strong, on-brand, functional draft with the right structure, real copy, real images and
a coherent palette/type system. Final art direction nuance is the human's finishing pass on the
tokens. Say so; do not oversell.

## Deliverable checklist (all must be true before "listo")
- [ ] Stage 0–2 written and included in the proposal (subject, plan, critique changes)
- [ ] `REGISTER` decided and justified in one line; the built page matches that register
- [ ] If a reference URL was given: captures saved as evidence, composition observations in the
      plan, and **no palette / typeface / copy** carried over from it
- [ ] Palette roles explicit; `o_cc` mapped; contrast ≥ 4.5:1 verified per section
- [ ] Display + body faces loaded; explicit `--fs-*` scale applied everywhere
- [ ] Sections follow the plan's rhythm; a single clear signature element
- [ ] Section schema confirmed; sections generated individually; tokens only
- [ ] Odoo constraints (8) satisfied; `/odoo` reachable; admin edit bar present
- [ ] Header/footer on-brand and readable; demo content gone; nav not duplicated
- [ ] Screenshots at 3 breakpoints reviewed by a different model; findings resolved or reported
- [ ] No vetoed default present; no invented testimonials/stats
