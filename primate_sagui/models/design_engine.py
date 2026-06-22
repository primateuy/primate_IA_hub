# -*- coding: utf-8 -*-
# Motor de diseño in-module: dado un rubro/seed (y, opcional, la paleta extraída de la identidad
# de marca) devuelve un DESIGN SYSTEM (paleta + tipografía + patrón de secciones + efectos +
# anti-patterns + checklist). Es la inteligencia de UI/UX "bakeada" build-time: NO hay llamada a
# Claude para recomendar; todo sale de las tablas bundleadas en data/design_kb/.
#
# Lógica REESCRITA (adaptada al ORM) a partir de UI/UX Pro Max Skill (MIT, Next Level Builder):
# BM25 sobre los CSV + agregación multi-dominio (product -> reasoning -> style/color/landing/
# typography). Ver data/design_kb/ATTRIBUTION.md y LICENSE.
import csv
import json
import logging
import os
import re
from collections import defaultdict
from math import log

from odoo import api, models

_logger = logging.getLogger(__name__)

# data/design_kb/ relativo a este archivo (models/).
_KB_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "design_kb")

# Qué columnas de cada CSV se indexan (búsqueda) y cuáles se devuelven (salida). Igual que el
# CSV_CONFIG del skill original, recortado a los dominios que usa el generador greenfield.
CSV_CONFIG = {
    "product": {
        "file": "products.csv",
        "search": ["Product Type", "Keywords", "Primary Style Recommendation", "Key Considerations"],
        "out": ["Product Type", "Keywords", "Primary Style Recommendation", "Secondary Styles",
                "Landing Page Pattern", "Dashboard Style (if applicable)", "Color Palette Focus"],
    },
    "style": {
        "file": "styles.csv",
        "search": ["Style Category", "Keywords", "Best For", "Type", "AI Prompt Keywords"],
        "out": ["Style Category", "Type", "Keywords", "Primary Colors", "Effects & Animation",
                "Best For", "Light Mode ✓", "Dark Mode ✓", "Performance", "Accessibility",
                "AI Prompt Keywords", "Implementation Checklist", "Design System Variables"],
    },
    "color": {
        "file": "colors.csv",
        "search": ["Product Type", "Notes"],
        "out": ["Product Type", "Primary", "On Primary", "Secondary", "On Secondary", "Accent",
                "On Accent", "Background", "Foreground", "Card", "Card Foreground", "Muted",
                "Muted Foreground", "Border", "Destructive", "On Destructive", "Ring", "Notes"],
    },
    "landing": {
        "file": "landing.csv",
        "search": ["Pattern Name", "Keywords", "Conversion Optimization", "Section Order"],
        "out": ["Pattern Name", "Keywords", "Section Order", "Primary CTA Placement",
                "Color Strategy", "Recommended Effects", "Conversion Optimization"],
    },
    "typography": {
        "file": "typography.csv",
        "search": ["Font Pairing Name", "Category", "Mood/Style Keywords", "Best For",
                   "Heading Font", "Body Font"],
        "out": ["Font Pairing Name", "Category", "Heading Font", "Body Font",
                "Mood/Style Keywords", "Best For", "Google Fonts URL", "CSS Import", "Notes"],
    },
    "ux": {
        "file": "ux-guidelines.csv",
        "search": ["Category", "Issue", "Description", "Platform"],
        "out": ["Category", "Issue", "Description", "Do", "Don't", "Severity"],
    },
}
REASONING_FILE = "ui-reasoning.csv"

# Cuántos resultados pedir por dominio al armar el design system (igual que el skill).
_SEARCH_N = {"product": 1, "style": 3, "color": 2, "landing": 2, "typography": 2}

# Cache de proceso: los CSV son estáticos (shippeados con el módulo); cargarlos/indexarlos una vez.
_CSV_CACHE = {}     # file -> list[dict]
_BM25_CACHE = {}    # (file, tuple(search_cols)) -> (BM25, data)


# ============ BM25 (portado de core.py del skill, MIT) ============
class _BM25:
    def __init__(self, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.corpus, self.doc_lengths, self.idf = [], [], {}
        self.avgdl, self.N = 0, 0

    @staticmethod
    def _tok(text):
        text = re.sub(r"[^\w\s]", " ", str(text).lower())
        return [w for w in text.split() if len(w) > 2]

    def fit(self, documents):
        self.corpus = [self._tok(d) for d in documents]
        self.N = len(self.corpus)
        if not self.N:
            return
        self.doc_lengths = [len(d) for d in self.corpus]
        self.avgdl = sum(self.doc_lengths) / self.N
        doc_freqs = defaultdict(int)
        for doc in self.corpus:
            for word in set(doc):
                doc_freqs[word] += 1
        for word, freq in doc_freqs.items():
            self.idf[word] = log((self.N - freq + 0.5) / (freq + 0.5) + 1)

    def score(self, query):
        q = self._tok(query)
        scores = []
        for idx, doc in enumerate(self.corpus):
            s, dl = 0.0, self.doc_lengths[idx]
            tf = defaultdict(int)
            for w in doc:
                tf[w] += 1
            for tok in q:
                if tok in self.idf:
                    f = tf[tok]
                    s += self.idf[tok] * (f * (self.k1 + 1)) / (
                        f + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            scores.append((idx, s))
        return sorted(scores, key=lambda x: x[1], reverse=True)


def _load_csv(filename):
    if filename not in _CSV_CACHE:
        path = os.path.join(_KB_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            _CSV_CACHE[filename] = list(csv.DictReader(f))
    return _CSV_CACHE[filename]


def _bm25_for(filename, search_cols):
    ckey = (filename, tuple(search_cols))
    if ckey not in _BM25_CACHE:
        data = _load_csv(filename)
        docs = [" ".join(str(row.get(c, "")) for c in search_cols) for row in data]
        bm = _BM25()
        bm.fit(docs)
        _BM25_CACHE[ckey] = (bm, data)
    return _BM25_CACHE[ckey]


def _search(domain, query, max_results):
    cfg = CSV_CONFIG[domain]
    bm, data = _bm25_for(cfg["file"], cfg["search"])
    ranked = bm.score(query)
    out = []
    for idx, sc in ranked[:max_results]:
        if sc > 0:
            row = data[idx]
            out.append({c: row.get(c, "") for c in cfg["out"] if c in row})
    return out


class SaguiDesignEngine(models.AbstractModel):
    _name = "primate.sagui.design"
    _description = "Motor de recomendación de design system (UI/UX bakeado)"

    # ---------------------------------------------------------------- API pública
    @api.model
    def recommend(self, seed, brand_palette=None):
        """Dado un `seed` (rubro + brief: ej. 'estudio de arquitectura minimalista, premium') y,
        opcional, la `brand_palette` extraída del logo, devuelve un DESIGN SYSTEM (dict).

        Si hay brand_palette, sus colores de marca (primary/accent) MANDAN sobre la recomendación
        (la identidad es la verdad); el resto de roles (background/foreground/muted/border/…) se
        completan desde la tabla de colores del rubro para tener una paleta coherente y accesible.
        """
        seed = (seed or "").strip() or "landing page"

        # 1) rubro -> categoría de producto
        product = (_search("product", seed, 1) or [{}])[0]
        category = product.get("Product Type") or "General"

        # 2) reglas de razonamiento para esa categoría (estilo/mood/efectos/anti-patterns)
        reasoning = self._reasoning_for(category)
        style_priority = reasoning.get("style_priority", [])

        # 3) búsqueda multi-dominio (estilo sembrado con la prioridad del reasoning)
        style_q = ("%s %s" % (seed, " ".join(style_priority[:2]))) if style_priority else seed
        styles = _search("style", style_q, _SEARCH_N["style"])
        colors = _search("color", seed, _SEARCH_N["color"])
        landings = _search("landing", seed, _SEARCH_N["landing"])
        typos = _search("typography", seed, _SEARCH_N["typography"])

        best_style = self._best_style(styles, style_priority)
        rec_color = colors[0] if colors else {}
        best_typo = typos[0] if typos else {}
        best_landing = landings[0] if landings else {}

        palette = self._merge_palette(rec_color, brand_palette)
        effects = best_style.get("Effects & Animation", "") or reasoning.get("key_effects", "")

        ds = {
            "seed": seed,
            "category": category,
            "pattern": {
                "name": best_landing.get("Pattern Name", reasoning.get("pattern", "Hero + Features + CTA")),
                "sections": best_landing.get("Section Order", "Hero > Features > CTA"),
                "cta_placement": best_landing.get("Primary CTA Placement", "Above fold"),
                "color_strategy": best_landing.get("Color Strategy", ""),
                "conversion": best_landing.get("Conversion Optimization", ""),
                "recommended_effects": best_landing.get("Recommended Effects", ""),
            },
            "style": {
                "name": best_style.get("Style Category", "Minimalism & Swiss Style"),
                "type": best_style.get("Type", "General"),
                "keywords": best_style.get("Keywords", ""),
                "best_for": best_style.get("Best For", ""),
                "effects": best_style.get("Effects & Animation", ""),
                "performance": best_style.get("Performance", ""),
                "accessibility": best_style.get("Accessibility", ""),
                "light_mode": best_style.get("Light Mode ✓", ""),
                "dark_mode": best_style.get("Dark Mode ✓", ""),
            },
            "colors": palette,
            "typography": {
                "pairing": best_typo.get("Font Pairing Name", ""),
                "heading": best_typo.get("Heading Font", "Inter"),
                "body": best_typo.get("Body Font", "Inter"),
                "mood": best_typo.get("Mood/Style Keywords", reasoning.get("typography_mood", "")),
                "best_for": best_typo.get("Best For", ""),
                "google_fonts_url": best_typo.get("Google Fonts URL", ""),
                "css_import": best_typo.get("CSS Import", ""),
            },
            "key_effects": effects,
            "anti_patterns": self._anti_patterns(reasoning),
            "checklist": self._checklist(),
            # Reglas UX universales (no dependen del rubro): se consultan con términos de calidad,
            # no con el seed de negocio, para traer guías reales (navegación/foco/contraste/etc.).
            "ux_rules": [
                {"category": r.get("Category", ""), "do": r.get("Do", ""), "dont": r.get("Don't", "")}
                for r in _search("ux", "navigation accessibility hover focus contrast responsive "
                                       "animation touch keyboard scroll", 5)
            ],
            "brand_seeded": bool(brand_palette),
        }
        return ds

    # ---------------------------------------------------------------- helpers internos
    @api.model
    def _reasoning_for(self, category):
        data = _load_csv(REASONING_FILE)
        cat = (category or "").lower()
        rule = {}
        for r in data:
            if r.get("UI_Category", "").lower() == cat:
                rule = r
                break
        if not rule:  # match parcial
            for r in data:
                uc = r.get("UI_Category", "").lower()
                if uc and (uc in cat or cat in uc):
                    rule = r
                    break
        if not rule:
            return {"pattern": "Hero + Features + CTA",
                    "style_priority": ["Minimalism", "Flat Design"],
                    "typography_mood": "Clean", "key_effects": "Subtle hover transitions",
                    "anti_patterns": "", "severity": "MEDIUM"}
        try:
            decision = json.loads(rule.get("Decision_Rules", "{}"))
        except ValueError:
            decision = {}
        return {
            "pattern": rule.get("Recommended_Pattern", ""),
            "style_priority": [s.strip() for s in rule.get("Style_Priority", "").split("+") if s.strip()],
            "color_mood": rule.get("Color_Mood", ""),
            "typography_mood": rule.get("Typography_Mood", ""),
            "key_effects": rule.get("Key_Effects", ""),
            "anti_patterns": rule.get("Anti_Patterns", ""),
            "decision_rules": decision,
            "severity": rule.get("Severity", "MEDIUM"),
        }

    @staticmethod
    def _best_style(styles, priority):
        if not styles:
            return {}
        if not priority:
            return styles[0]
        for p in priority:  # match exacto por nombre de estilo
            pl = p.lower().strip()
            for s in styles:
                name = s.get("Style Category", "").lower()
                if pl and (pl in name or name in pl):
                    return s
        scored = []  # score por keyword
        for s in styles:
            sc, blob = 0, str(s).lower()
            for kw in priority:
                k = kw.lower().strip()
                if k and k in s.get("Style Category", "").lower():
                    sc += 10
                elif k and k in s.get("Keywords", "").lower():
                    sc += 3
                elif k and k in blob:
                    sc += 1
            scored.append((sc, s))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1] if scored and scored[0][0] > 0 else styles[0]

    @staticmethod
    def _merge_palette(rec_color, brand_palette):
        """Paleta final por rol. La recomendación del rubro da la base completa (con contraste
        pensado); si hay identidad de marca, su primary/accent (y secondary si vino) MANDAN."""
        pal = {
            "primary": rec_color.get("Primary", "#2563EB"),
            "on_primary": rec_color.get("On Primary", "#FFFFFF"),
            "secondary": rec_color.get("Secondary", "#3B82F6"),
            "accent": rec_color.get("Accent", "#EA580C"),
            "on_accent": rec_color.get("On Accent", "#FFFFFF"),
            "background": rec_color.get("Background", "#F8FAFC"),
            "foreground": rec_color.get("Foreground", "#1E293B"),
            "muted": rec_color.get("Muted", "#E9EFF8"),
            "muted_foreground": rec_color.get("Muted Foreground", "#64748B"),
            "border": rec_color.get("Border", "#E2E8F0"),
            "destructive": rec_color.get("Destructive", "#DC2626"),
            "notes": rec_color.get("Notes", ""),
        }
        bp = brand_palette or {}
        for role in ("primary", "secondary", "accent", "background", "foreground"):
            v = bp.get(role)
            if v:
                pal[role] = v
        if bp.get("colors"):  # lista cruda de hexes extraídos del logo (para referencia/preview)
            pal["brand_swatches"] = bp["colors"]
        return pal

    @staticmethod
    def _anti_patterns(reasoning):
        """Anti-patterns del rubro + los universales de craft (los que separan premium de chapucero)."""
        base = [a.strip() for a in (reasoning.get("anti_patterns") or "").split("+") if a.strip()]
        universal = [
            "Emojis como íconos (usar SVG: Heroicons/Lucide)",
            "Sin cursor:pointer en elementos clickeables",
            "Cambios de estado instantáneos (siempre transición 150-300ms)",
            "Texto de bajo contraste (<4.5:1)",
            "Foco invisible para navegación por teclado",
            "Hovers que desplazan el layout (scale que mueve a los vecinos)",
            "Gradientes/sombras porque sí; stock genérico sin curaduría",
        ]
        return base + universal

    @staticmethod
    def _checklist():
        return [
            "Sin emojis como íconos (SVG consistente: Heroicons/Lucide)",
            "cursor:pointer en todo lo clickeable",
            "Hover con transición suave (150-300ms)",
            "Contraste de texto >= 4.5:1 (modo claro)",
            "Foco visible para teclado",
            "prefers-reduced-motion respetado",
            "Responsive: 375 / 768 / 1024 / 1440px, sin scroll horizontal",
            "Jerarquía tipográfica clara (un solo H1, escala consistente)",
            "Ritmo de spacing por escala (no valores arbitrarios)",
        ]

    # ---------------------------------------------------------------- preview legible
    @api.model
    def preview_text(self, ds):
        """Preview en markdown del design system para mostrar en el chat ANTES de construir."""
        c = ds.get("colors", {})
        t = ds.get("typography", {})
        p = ds.get("pattern", {})
        st = ds.get("style", {})
        lines = []
        lines.append("**Sistema de diseño propuesto** (rubro: %s)" % ds.get("category", "-"))
        if ds.get("brand_seeded"):
            lines.append("_Sembrado con la paleta de tu identidad (primary/accent de marca)._")
        lines.append("")
        lines.append("**Estilo:** %s — %s" % (st.get("name", "-"), st.get("keywords", "")[:120]))
        lines.append("**Tipografía:** %s / %s%s" % (
            t.get("heading", "-"), t.get("body", "-"),
            (" (%s)" % t.get("pairing")) if t.get("pairing") else ""))
        lines.append("**Paleta:**")
        for role in ("primary", "accent", "secondary", "background", "foreground", "muted", "border"):
            if c.get(role):
                lines.append("- %s: `%s`" % (role, c[role]))
        lines.append("**Patrón de secciones:** %s" % p.get("sections", "-"))
        if p.get("conversion"):
            lines.append("**Conversión:** %s" % p["conversion"][:160])
        if ds.get("key_effects"):
            lines.append("**Micro-interacciones:** %s" % ds["key_effects"][:160])
        if ds.get("anti_patterns"):
            lines.append("**Evitar:** " + "; ".join(ds["anti_patterns"][:4]))
        lines.append("**Checklist de calidad:** %s ítems" % len(ds.get("checklist", [])))
        return "\n".join(lines)
