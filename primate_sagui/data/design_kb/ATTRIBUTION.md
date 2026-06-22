# Base de conocimiento de diseño — atribución

Las tablas CSV de esta carpeta (`products`, `styles`, `colors`, `landing`, `typography`,
`ux-guidelines`, `ui-reasoning`) provienen del proyecto **UI/UX Pro Max Skill**:

- Repositorio: https://github.com/nextlevelbuilder/ui-ux-pro-max-skill
- Autor: Next Level Builder
- Licencia: **MIT** (ver `LICENSE` en esta misma carpeta)
- Commit de referencia: `b7e3af80f6e3` (2026-04-03)

Se bundlean tal cual (subconjunto de `src/ui-ux-pro-max/data/`) bajo los términos de la
licencia MIT, que permite uso y redistribución conservando el aviso de copyright (incluido en
`LICENSE`). La lógica de recomendación (BM25 + generador de design system) fue **reescrita** en
`primate_sagui/models/design_engine.py` adaptándola al ORM de Odoo; el crédito del diseño de los
datos y del algoritmo original es de Next Level Builder.
