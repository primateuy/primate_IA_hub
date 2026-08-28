# Design registers — how a site is COMPOSED (never what colors it uses)

A register is a way of composing a page: hero type, section rhythm, density, image kind, motion
budget, footer shape. It is chosen in Stage 1 of `odoo-site-greenfield-design` and recorded in
the plan as `REGISTER: <key>`. The subject still decides palette, typefaces and signature.

## Rules
1. A register never supplies colors, fonts or a signature. If a register file names a site,
   that site is a *composition* reference only. Copying its palette or its brand gradient is a
   vetoed default (see `anti-patterns.md`).
2. Choosing a register is a stated decision in the plan, with one line of justification tied to
   the subject and audience. "Modern and professional" is not a register; it is the request that
   triggers choosing one.
3. Default mapping when the brief only says "profesional / moderno / pro":
   - software, SaaS, agencies, consulting, fintech, anything selling a digital product →
     `tech-minimal` (or `tech-editorial` if the brand has strong content/ideas to publish)
   - services with people at the center (health, education, local business, studios, food) →
     `tech-warm`
   - the user names a reference URL → extract the register FROM that capture (see below), then
     pick the closest file here and note deviations.
4. Reference URLs: capture them live with Playwright at 1440 and 375 (same `shoot.py`), and read
   from the capture only these things: hero kind, section order and rhythm, density, image kind,
   motion budget, nav/footer shape. Write them into the plan as observations. Never carry palette,
   type or copy from a reference URL into the plan.
5. Execute the register with precision. A minimal register done sloppily (uneven spacing, mixed
   weights) looks cheaper than no register at all. Spacing scale and type scale are non-negotiable.

## Files
- `tech-minimal.md` — precision minimal: one statement, one action, whitespace, product as image
- `tech-warm.md` — human and direct: character type, own illustration/photo, opinionated copy
- `tech-editorial.md` — editorial: strong light/dark rhythm, large display, content-led sections
