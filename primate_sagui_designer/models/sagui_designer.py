# -*- coding: utf-8 -*-
# ROL "DISEÑADOR WEB ODOO".
#
# El problema que resuelve: los sitios salían genéricos porque el proceso iba de "hacé un sitio"
# directo a HTML. Acá el proceso es el de la skill y no se saltea: brief → plan escrito →
# autocrítica del plan → propuesta al humano → generación SECCIÓN POR SECCIÓN → capturas →
# revisión independiente → corrección determinística de tokens.
#
# ENRUTADO por presencia de referencia:
#   hay PDF/mockup            → odoo-site-from-design  (el sistema de diseño sale del reference)
#   no hay / "hacelo lindo"   → odoo-site-greenfield-design (etapas 0-2 antes de una línea de HTML)
#   parcial (logo, colores)   → greenfield, con ese material como MATERIAL REAL FIJO del plan
#
# Lo que este rol NO hace, a propósito: no llama a primate.sagui.design.recommend(). Ese motor
# elige paleta y tipografía por rubro desde CSVs, que es justo lo que hace que dos briefs del mismo
# rubro salgan idénticos y que la paleta no tenga nada que ver con el mundo del sujeto. La paleta
# la decide el plan, derivada del sujeto.
import json
import logging
import os
import re
import secrets
import shutil

from odoo import api, models, _

_logger = logging.getLogger(__name__)

# Registros de composición disponibles. La lista vive acá para VALIDAR; el contenido de cada uno
# vive en skills/_shared/registers/ y se lee en runtime como sagui.skill.
REGISTERS = ("tech-minimal", "tech-warm", "tech-editorial")

# Sitios de referencia por URL: cuántos se miran y a qué anchos. Dos anchos alcanzan para leer
# composición; el tercero del verificador no agrega nada acá y cuesta una captura más.
MAX_REFERENCE_URLS = 3
REFERENCE_BREAKPOINTS = (1440, 375)

ROLE_KEY = "web_designer"
WRAPPER = "brandsite"
MAX_PLAN_TOKENS = 8192
MAX_SECTION_TOKENS = 6000
MAX_SECTIONS = 8

# Fence del bloque de tokens: delimita la región que el verificador puede editar de forma
# determinística. Todo lo que esté acá adentro se puede reescribir sin regenerar nada.
FENCE_START = "/* ==== SAGUI TOKENS (editables) ==== */"
FENCE_END = "/* ==== /SAGUI TOKENS ==== */"
_FENCE_RE = re.compile(
    re.escape(FENCE_START) + r".*?" + re.escape(FENCE_END), re.DOTALL)
_DECL_RE = r"(?P<pre>%s\s*:\s*)(?P<val>[^;}]+)"

PLAN_SCHEMA = """
Devolvé EXCLUSIVAMENTE un JSON (sin ``` ni texto alrededor) con esta forma exacta:

{"register": {"key": "tech-minimal|tech-warm|tech-editorial",
              "why": "<una línea atada a ESTE sujeto y ESTA audiencia>"},
 "subject": "<el sujeto concreto, no 'una empresa'>",
 "audience": "<a quién le habla>",
 "job": "<el único trabajo de la página: pedir turno, cotizar, descargar catálogo>",
 "tone": ["<tres palabras de tono>"],
 "concept": "<una oración: qué se siente al ver esta página y por qué le queda a este sujeto>",
 "palette": {"bg": "#rrggbb", "surface": "#rrggbb", "text": "#rrggbb",
             "text_muted": "#rrggbb", "accent": "#rrggbb", "accent_contrast": "#rrggbb"},
 "o_cc_map": {"o_cc1": "bg", "o_cc3": "surface", "o_cc5": "accent"},
 "type": {"display": {"family": "<familia de Google Fonts>", "weight": "700",
                      "case": "none|uppercase", "tracking": "-0.02em"},
          "body": {"family": "<familia de Google Fonts distinta a la display>", "weight": "400"},
          "scale": {"--fs-display": "clamp(2.4rem,1.6rem+3vw,4rem)", "--fs-h2": "...",
                    "--fs-h3": "...", "--fs-body": "1.05rem", "--fs-small": "0.9rem"}},
 "spacing": {"--space-xs": "0.5rem", "--space-s": "1rem", "--space-m": "1.75rem",
             "--space-l": "3rem", "--space-xl": "6rem"},
 "signature": "<el ÚNICO elemento por el que se va a recordar este sitio, atado al sujeto>",
 "motion": "<dónde sirve el movimiento, o 'ninguno'>",
 "copy_voice": "<registro, verbos, qué dice literalmente el CTA>",
 "sections": [
   {"id": "hero", "label": "Hero", "tone": "dark|light|surface|accent",
    "layout": "<una línea describiendo la composición>",
    "copy_brief": "<qué tiene que decir esta sección, con especificidad del sujeto>",
    "image_role": "background|inline|none"}
 ],
 "nav": [{"label": "Servicios", "anchor": "#servicios"}],
 "footer": {"brand": "<nombre>", "lines": ["<línea real>"]},
 "critique": ["<qué cambiaste del primer impulso y por qué, una línea por cambio>"]}

Reglas del plan:
• "register" es OBLIGATORIO y decide la COMPOSICIÓN: hero, ritmo, densidad, tipo de imagen,
  presupuesto de movimiento y forma del footer. NO aporta colores, tipografías ni firma — eso
  sigue saliendo del sujeto. Leé el archivo del registro que elegiste antes de escribir el resto
  del plan, y que todo lo demás sea consistente con él. Sin registro decidido no se genera nada.
• La paleta sale del MUNDO DEL SUJETO (sus materiales, su entorno, sus productos), no de un gusto
  general. Los roles son roles: el acento es acento, nunca el fondo de la página.
• Las dos familias tipográficas tienen que ser DISTINTAS entre sí y elegidas con intención.
• 4 a 7 secciones. El ritmo claro/oscuro tiene que estar en el campo "tone".
• "critique" no puede venir vacío: es la etapa 2 de la skill. Corré el control mental —
  ¿le habría dado este mismo plan a un competidor del mismo rubro? — y anotá qué cambiaste.
• Nada de testimonios, cifras, premios ni clientes inventados. Si no hay material real, la
  sección no existe.
"""

SECTION_PROMPT = """Sos el diseñador que ya fijó el plan. Ahora componés UNA sección, la que te
indico, siguiendo el plan al pie. Nada de re-decidir paleta ni tipografía: ya están.

SALIDA EXACTA, sin ``` ni texto alrededor:
===HTML===
<section id="<id>" class="sec <id>"> … </section>
===CSS===
(sólo reglas `.brandsite .<id> …` para el LAYOUT interno de esta sección)

Reglas duras:
• Los colores y tamaños salen SIEMPRE de las variables ya definidas: --c-bg, --c-surface,
  --c-text, --c-muted, --c-accent, --c-on-accent, --fs-display, --fs-h2, --fs-h3, --fs-body,
  --fs-small, --space-xs…--space-xl. NUNCA hardcodees un hex ni un px de paleta o tipografía.
• El PADDING VERTICAL de la sección ya lo pone `.brandsite .sec`. NO lo declares de nuevo: si cada
  sección redefine su padding con otra especificidad, las costuras entre bloques se rompen.
  Un solo nivel de especificidad por propiedad; nada de pisar `.sec` con `.<id>` para lo mismo.
• Escribí el copy real, en el idioma del brief, específico del sujeto. Cero relleno
  ("Bienvenidos", "soluciones integrales"), cero lorem, cero datos inventados.
• IMÁGENES: usá SOLO las URLs reales que te paso. Si no te paso ninguna, NO pongas <img> con una
  ruta inventada ni un placeholder: una imagen rota se ve peor que ninguna. Cuando no hay foto,
  componé con tipografía, color y espacio — es una decisión de diseño legítima, no un parche.
• Íconos SVG inline (trazo tipo Lucide/Heroicons), NUNCA emojis. Links con destino real
  (#ancla, /contactus, mailto:, tel:, https://wa.me/…): jamás href="#".
• Responsive real, sin overflow horizontal a 375px. cursor:pointer en clickeables, :hover y
  :focus visibles con transición 150-250ms, prefers-reduced-motion respetado.
• NO HAY JAVASCRIPT. Ninguno: no se inyectan scripts en el sitio generado. Por eso NUNCA escribas
  una regla que deje contenido oculto en reposo esperando que algo le agregue una clase
  (`.seccion{opacity:0}` + `.seccion.is-visible{opacity:1}` deja la sección invisible PARA
  SIEMPRE). Toda animación tiene que ser CSS puro y arrancar desde el estado VISIBLE: hover,
  focus, o una @keyframes que corra sola al cargar.
• Un solo <h1> en todo el sitio, y va en el hero.
"""


class SaguiDesigner(models.AbstractModel):
    _inherit = "primate.sagui.assistant"

    # ==================================================================
    #  Tools
    # ==================================================================
    def _tool_specs(self):
        specs = super()._tool_specs()
        specs += [{
            "name": "disenar_web",
            "description": "ROL DISEÑADOR WEB: diseña y construye un sitio en Odoo, con o sin "
                           "referencia. Si hay un PDF/mockup (adjunto o tomado de Documentos) lo "
                           "reproduce; si no hay, o el pedido es 'hacelo lindo / profesional / "
                           "rediseñá', diseña desde cero con etapa de plan y autocrítica. Devuelve "
                           "una PROPUESTA (tokens + secciones) para que el humano confirme: NO "
                           "construye nada todavía. Usá esta tool en vez de las viejas "
                           "analizar_sitio/disenar_sitio_marca cuando el pedido sea de diseño.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "brief": {"type": "string", "description": "Qué es el negocio, a quién le "
                              "habla, qué querés que haga la página. En el idioma del usuario."},
                    "attachment_ids": {"type": "array", "items": {"type": "integer"},
                        "description": "Adjuntos de referencia (PDF/mockup) y/o assets (logo). "
                                       "Omitir si no hay: el rol enruta solo a greenfield."},
                    "logo_attachment_id": {"type": "integer",
                        "description": "Si el logo vino por separado, su ir.attachment."},
                    "render_mode": {"type": "string", "enum": ["fiel", "editable"],
                        "description": "Cuerpo a medida (fiel, default) o snippets nativos "
                                       "(editable en el builder)."},
                    "name": {"type": "string", "description": "Título del sitio."},
                    "sections": {"type": "array", "items": {"type": "string"},
                        "description": "Si el usuario nombró las secciones, pasalas; mandan."},
                    "reference_urls": {"type": "array", "items": {"type": "string"},
                        "description": "Sitios que el usuario nombró como referencia (http/https). "
                                       "Se capturan y se leen SOLO como composición: paleta, "
                                       "tipografía y copy de esos sitios NO entran al diseño."},
                    "register": {"type": "string",
                        "enum": ["tech-minimal", "tech-warm", "tech-editorial"],
                        "description": "Registro de composición, si el usuario ya lo eligió al "
                                       "responder una propuesta anterior. Si se omite, lo decide "
                                       "el plan según el sujeto."},
                },
                "required": ["brief"],
            },
        }]
        return specs

    def _system_prompt(self):
        prompt = super()._system_prompt()
        prompt += (
            "\n\nDISEÑO WEB: para cualquier pedido de sitio/landing/rediseño usá disenar_web. No "
            "elijas vos el flujo: el rol enruta según haya o no referencia. Mostrale al usuario la "
            "propuesta (concepto, paleta con roles, tipografías, ritmo de secciones y firma) y "
            "esperá que responda «confirmar». Si el usuario dice que la referencia está en "
            "Documentos, primero documents_listar_carpeta.\n"
        )
        return prompt

    def _run_tool(self, name, args, user, channel=None, proposals=None):
        if name == "disenar_web":
            return self._tool_disenar_web(args, user, channel, proposals)
        return super()._run_tool(name, args, user, channel=channel, proposals=proposals)

    def _execute_pending(self, channel, author, pending):
        if pending.operation == "website_designer":
            return self._execute_designer(channel, author, pending)
        return super()._execute_pending(channel, author, pending)

    @api.model
    def _build_operations(self):
        return super()._build_operations() + ("website_designer",)

    # ==================================================================
    #  Sitios de referencia por URL (cuarto tipo de material)
    # ==================================================================
    @api.model
    def _parse_reference_urls(self, raw):
        """Normaliza las URLs de referencia. Devuelve (urls, descartadas).

        Solo http/https: cualquier otra cosa -un file://, un javascript:- no es un sitio que
        haya que mirar y sí es una forma de que el capturador abra algo que no debería.
        """
        urls, descartadas = [], []
        for item in (raw or []):
            texto = (item or "").strip()
            if not texto:
                continue
            if not re.match(r"^https?://[^\s/$.?#].[^\s]*$", texto, re.I):
                descartadas.append(texto)
                continue
            if texto not in urls:
                urls.append(texto)
        return urls[:MAX_REFERENCE_URLS], descartadas

    @api.model
    def _capture_references(self, env, run, urls):
        """Captura cada URL a 1440 y 375 y la guarda como evidencia del run.

        La captura NO se usa para reproducir nada: alimenta las observaciones de COMPOSICIÓN del
        plan. Se guarda igual, porque dentro de seis meses "nos inspiramos en tal sitio" sin la
        captura no se puede auditar.

        Returns:
            list: [{"url", "attachment_ids", "error"}] — un fallo de captura NO frena el diseño.
        """
        resultados = []
        for url in urls:
            partes = url.split("/", 3)
            base = "/".join(partes[:3])
            camino = "/" + (partes[3] if len(partes) > 3 else "")
            shots = env["sagui.verifier.web"]._shoot({
                "base_url": base, "url": camino, "external": True,
                "breakpoints": REFERENCE_BREAKPOINTS,
            })
            if not shots.get("ok"):
                _logger.warning("Sagui: no pude capturar la referencia %s (%s)",
                                url, shots.get("errors"))
                resultados.append({"url": url, "attachment_ids": [],
                                   "error": "; ".join(shots.get("errors") or [])})
                continue
            # El capturador deja PNG en disco; se leen igual que la evidencia del verificador.
            slug = re.sub(r"[^a-z0-9]+", "-", base.split("//")[-1].lower()).strip("-")
            adjuntos = []
            for shot in shots.get("shots") or []:
                ruta = shot.get("path")
                if not ruta or not os.path.exists(ruta):
                    continue
                try:
                    with open(ruta, "rb") as fh:
                        crudo = fh.read()
                except OSError:
                    continue
                # Sin image_no_postprocess, Odoo la achica a 1920 del lado largo y la
                # referencia llega al modelo como una tira de 200px de ancho.
                adjuntos.append(env["ir.attachment"].sudo().with_context(
                    image_no_postprocess=True).create({
                        "name": "referencia-%s-%sw.png" % (slug, shot.get("width")),
                        "raw": crudo, "mimetype": "image/png",
                        "res_model": "sagui.design.run", "res_id": run.id,
                    }).id)
            shutil.rmtree(shots.get("_out_dir") or "", ignore_errors=True)
            resultados.append({"url": url, "attachment_ids": adjuntos, "error": None})
        todos = [i for r in resultados for i in r["attachment_ids"]]
        if todos:
            run.sudo().write({"reference_capture_ids": [(6, 0, todos)]})
        return resultados

    # ==================================================================
    #  Enrutado
    # ==================================================================
    @api.model
    def _route(self, references, assets, brief):
        """Decide el flujo por PRESENCIA DE REFERENCIA, no por lo que suene el pedido.

        Un logo suelto no es una referencia de diseño: es material real que el plan tiene que
        respetar, pero el sistema de diseño hay que inventarlo igual → greenfield.
        """
        if references:
            return "reference"
        if assets:
            return "partial"
        return "greenfield"

    @api.model
    def _classify_material(self, env, attachment_ids, logo_attachment_id=None):
        """Separa lo que se reproduce (referencias) de lo que se coloca (assets)."""
        references, assets, problemas = [], [], []
        ids = [int(i) for i in (attachment_ids or []) if str(i).strip().lstrip("-").isdigit()]
        if logo_attachment_id:
            ids.append(int(logo_attachment_id))
        for att_id in dict.fromkeys(ids):
            att = env["ir.attachment"].browse(att_id).exists()
            if not att:
                problemas.append(_("el adjunto %s no existe") % att_id)
                continue
            try:
                att.check_access("read")
            except Exception:  # noqa: BLE001
                problemas.append(_("no podés ver el adjunto %s") % att_id)
                continue
            mimetype = (att.mimetype or "").split(";")[0].strip().lower()
            name = (att.name or "").lower()
            if logo_attachment_id and att.id == int(logo_attachment_id):
                assets.append(att)
            elif mimetype == "application/pdf":
                references.append(att)
            elif mimetype.startswith("image/"):
                if any(h in name for h in ("logo", "isotipo", "marca", "brand")):
                    assets.append(att)
                else:
                    references.append(att)
            else:
                problemas.append(_("«%s» no es PDF ni imagen") % (att.name or att_id))
        return references, assets, problemas

    # ==================================================================
    #  Tool principal: plan + propuesta
    # ==================================================================
    def _tool_disenar_web(self, args, user, channel=None, proposals=None):
        env = self.env(user=user.id)
        role = env["sagui.role"].get(ROLE_KEY, required=False)
        if not role:
            return _("Falta el rol «%s». Instalá/actualizá primate_sagui_designer.") % ROLE_KEY

        brief = (args.get("brief") or "").strip()
        if not brief:
            return _("Necesito un brief: qué es el negocio, a quién le habla y qué tiene que "
                     "lograr la página.")

        references, assets, problemas = self._classify_material(
            env, args.get("attachment_ids"), args.get("logo_attachment_id"))
        source = self._route(references, assets, brief)

        urls, urls_descartadas = self._parse_reference_urls(args.get("reference_urls"))

        run = env["sagui.design.run"].create({
            "name": (args.get("name") or "").strip() or brief[:60],
            "role_id": role.id, "source": source, "brief": brief,
            "render_mode": (args.get("render_mode") or "fiel"),
            "reference_attachment_ids": [(6, 0, [a.id for a in references])],
            "asset_attachment_ids": [(6, 0, [a.id for a in assets])],
            "reference_urls": "\n".join(urls),
            "state": "draft",
        })

        # Las capturas se sacan ANTES del plan: son material del plan, no ilustración posterior.
        capturas = self._capture_references(env, run, urls) if urls else []
        if capturas:
            problemas += [_("no pude capturar %s (%s)") % (c["url"], c["error"])
                          for c in capturas if c.get("error")]
        problemas += [_("«%s» no es una URL http/https y no se capturó") % u
                      for u in urls_descartadas]

        plan, error = self._make_plan(env, role, run, references, assets, brief,
                                      source, args.get("sections"),
                                      capturas=capturas, register=args.get("register"))
        if error:
            run.write({"state": "error"})
            return error

        # SIN REGISTRO NO SE CONSTRUYE, PERO TAMPOCO SE TIRA EL PLAN. Se le muestra al usuario
        # como propuesta pendiente de UNA decisión suya: la composición es de él, no del modelo
        # que no la eligió. El run queda guardado y la respuesta dice exactamente qué falta.
        faltantes = self._plan_is_ready(plan)
        if faltantes:
            run.write({
                "plan_json": json.dumps(plan, ensure_ascii=False),
                "critique": "\n".join(plan.get("critique") or []),
                "state": "proposed",
            })
            return json.dumps({
                "propuesta_incompleta": True,
                "falta_decidir": faltantes,
                "opciones_registro": [
                    {"key": "tech-minimal",
                     "cuando": "software, SaaS, agencias, consultoras, fintech: precisión, "
                               "una sola acción, el producto real como imagen"},
                    {"key": "tech-editorial",
                     "cuando": "marcas con contenido e ideas para publicar: ritmo claro/oscuro, "
                               "display grande, secciones de contenido en la home"},
                    {"key": "tech-warm",
                     "cuando": "servicios con personas en el centro -salud, educación, comercio "
                               "local, estudios, gastronomía-: tipografía con carácter, imagen propia"},
                ],
                "avisos": problemas,
                "nota": "NO construyas nada. Contale al usuario que el plan está armado pero "
                        "falta decidir CÓMO SE COMPONE la página, ofrecele las tres opciones en "
                        "una línea cada una y recomendá la que le cierre a su rubro diciendo por "
                        "qué. Cuando elija, volvé a llamar disenar_web con el mismo brief y el "
                        "argumento register.",
            }, ensure_ascii=False)

        run.write({
            "plan_json": json.dumps(plan, ensure_ascii=False),
            "critique": "\n".join(plan.get("critique") or []),
            "tokens_json": json.dumps(self._tokens_of(plan), ensure_ascii=False),
            "schema_json": json.dumps(plan.get("sections") or [], ensure_ascii=False),
            "state": "proposed",
        })

        pending = self._propose_designer(env, run, user, channel, proposals)
        return json.dumps({
            "propuesta": self._preview(plan, run, source),
            "propuesta_registrada": bool(pending),
            "nota": "Mostrale al usuario la propuesta EN PALABRAS: concepto, paleta con sus roles, "
                    "el par tipográfico, el ritmo de secciones y la firma; y contale qué cambiaste "
                    "en la autocrítica. Terminá pidiéndole que responda exactamente «confirmar» "
                    "para construir (o que te diga qué cambiar). NO escribas ningún token. NO "
                    "llames otra tool.",
        }, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------ plan (etapas 0-2 / pass 1)
    def _make_plan(self, env, role, run, references, assets, brief, source, sections,
                   capturas=None, register=None):
        """Una llamada al modelo con el rol + skills cargadas. Devuelve (plan, error)."""
        blocks = []
        for att in references[:4]:
            block = self._attachment_to_block(att)
            if block:
                blocks.append({"type": "text", "text": _("REFERENCIA a reproducir: %s") % att.name})
                blocks.append(block)
        for att in assets[:4]:
            block = self._attachment_to_block(att)
            if block:
                blocks.append({"type": "text",
                               "text": _("MATERIAL REAL (usalo tal cual, no lo reinventes): %s")
                                       % att.name})
                blocks.append(block)

        # LA ADVERTENCIA VA PEGADA A CADA IMAGEN, no una vez arriba: es la instrucción que más
        # fácil se pierde entre bloques, y la que separa "aprendí a componer" de "le copié la
        # marca a otro".
        for captura in (capturas or []):
            for att_id in captura.get("attachment_ids") or []:
                block = self._attachment_to_block(env["ir.attachment"].browse(att_id))
                if not block:
                    continue
                blocks.append({"type": "text", "text": _(
                    "SITIO DE REFERENCIA (%s) — MIRÁ SÓLO CÓMO ESTÁ COMPUESTO: tipo de hero, "
                    "orden y ritmo de secciones, densidad, tipo de imagen, movimiento, forma de "
                    "nav y footer. SU PALETA, SU TIPOGRAFÍA Y SU COPY NO ENTRAN AL PLAN: esos "
                    "salen del sujeto. Copiarle el color o la tipografía es el hallazgo C1."
                ) % captura.get("url")})
                blocks.append(block)

        if source == "reference":
            instruccion = _(
                "Hay REFERENCIA. Seguí odoo-site-from-design: el sistema de diseño es el del "
                "reference, no uno tuyo. Extraé la paleta con sus roles reales, la escala "
                "tipográfica, el orden y ritmo de secciones y la firma, y volcalos al JSON. En "
                "'critique' anotá qué tuviste que aproximar y por qué.")
        elif source == "partial":
            instruccion = _(
                "Hay material REAL PARCIAL (logo/colores) pero NO hay diseño a reproducir. Seguí "
                "odoo-site-greenfield-design (etapas 0 a 2) y tratá ese material como fijo: la "
                "paleta tiene que convivir con el logo, no pelearse.")
        else:
            instruccion = _(
                "NO hay referencia. Seguí odoo-site-greenfield-design, etapas 0 a 2, completas y "
                "por escrito, ANTES de cualquier HTML.")
        if sections:
            instruccion += _("\nEl usuario pidió estas secciones y mandan: %s") % ", ".join(sections)

        if (register or "").strip() in REGISTERS:
            instruccion += _(
                "\n\nEL REGISTRO YA ESTÁ DECIDIDO POR EL USUARIO: «%s». Usá ese y no otro; "
                "leelo completo y que todo el plan sea consistente con él."
            ) % register.strip()
        if capturas:
            instruccion += _(
                "\n\nHay sitios de referencia capturados. Escribí en 'critique' UNA línea con "
                "las observaciones de composición que sacaste de ellos, y elegí el registro más "
                "cercano anotando en qué se aparta la referencia. Nada de paleta ni tipografía "
                "de esos sitios.")
        blocks.append({"type": "text", "text": "%s\n\nBRIEF:\n%s\n\n%s"
                       % (instruccion, brief, PLAN_SCHEMA)})

        action = env["primate.ai.action"]._open(
            "disenar_web_plan", run.name, ref="sagui.design.run,%s" % run.id)
        env = env(context={**env.context, "ai_action_id": action.id})
        try:
            data = env["primate.ai.connector"].call(
                [{"role": "user", "content": blocks}],
                system=role.prompt(), max_tokens=MAX_PLAN_TOKENS, timeout=240)
        except Exception as e:  # noqa: BLE001
            return None, _("No pude armar el plan de diseño: %s") % e

        plan = self._safe_json("".join(b.get("text", "") for b in (data.get("content") or [])
                                       if b.get("type") == "text"))
        if not plan or not plan.get("sections") or not plan.get("palette"):
            return None, _("El plan volvió incompleto. Contame un poco más del negocio y "
                           "reintento.")
        plan = self._normalize_plan(plan, sections)
        # SIN REGISTRO NO SE GENERA. Se corta acá y no en la sección 3: descubrirlo a mitad de
        # la generación deja medio sitio construido con una composición que nadie eligió.
        faltantes = self._plan_is_ready(plan)
        if faltantes:
            return None, " ".join(faltantes)
        return plan, None

    @api.model
    def _normalize_plan(self, plan, requested=None):
        """Sanea el plan: ids de sección usables, tope de secciones, anclas de nav coherentes."""
        clean = []
        for i, sec in enumerate((plan.get("sections") or [])[:MAX_SECTIONS]):
            sid = re.sub(r"[^a-z0-9]+", "-", (sec.get("id") or sec.get("label") or "sec%s" % i)
                         .lower()).strip("-") or "sec%s" % i
            sec["id"] = sid
            sec["tone"] = sec.get("tone") if sec.get("tone") in (
                "dark", "light", "surface", "accent") else "light"
            clean.append(sec)
        plan["sections"] = clean
        ids = {s["id"] for s in clean}
        plan["nav"] = [n for n in (plan.get("nav") or [])
                       if (n.get("anchor") or "").lstrip("#") in ids][:6]
        if not plan.get("critique"):
            plan["critique"] = [_("(el plan no trajo autocrítica: revisalo con ojo crítico)")]
        plan["register"] = self._normalize_register(plan.get("register"))
        return plan

    @api.model
    def _normalize_register(self, register):
        """Sanea el registro del plan. Devuelve {"key", "why"} o {} si no vino uno válido.

        NO INVENTA UN DEFAULT. El mapeo por defecto es una decisión del agente atada al sujeto,
        y elegirlo acá por él convertiría "decidí la composición" en "te puse la de siempre",
        que es exactamente lo que el registro viene a evitar. Sin registro, _plan_is_ready()
        frena la generación y lo dice.
        """
        register = register if isinstance(register, dict) else {}
        key = (register.get("key") or "").strip().lower()
        if key not in REGISTERS:
            return {}
        return {"key": key, "why": (register.get("why") or "").strip()}

    @api.model
    def _plan_is_ready(self, plan):
        """Qué le falta al plan para poder generar. Lista vacía = listo.

        Hoy solo mira el registro, que es el único campo cuya ausencia frena todo: sin decidir
        cómo se compone la página, cada sección la compone de nuevo y el sitio sale ensamblado.
        """
        faltantes = []
        if not (plan.get("register") or {}).get("key"):
            faltantes.append(_(
                "El plan no decidió el REGISTRO de composición (%s). Elegí uno según el sujeto "
                "y la audiencia, y justificalo en una línea.") % ", ".join(REGISTERS))
        return faltantes

    @api.model
    def _register_notes(self, plan):
        """Las notas C5/C6 del registro elegido, para la rúbrica del verificador.

        Salen del propio archivo del registro y no de una copia acá: si alguien edita el .md,
        el verificador chequea lo nuevo sin tocar código.
        """
        key = (plan.get("register") or {}).get("key")
        if not key:
            return ""
        texto = self.env["sagui.skill"].content_of("register-%s" % key)
        if not texto:
            return ""
        marcador = "## Verifier notes"
        if marcador not in texto:
            return ""
        cuerpo = texto.split(marcador, 1)[1]
        # Hasta el próximo encabezado, si lo hubiera.
        cuerpo = cuerpo.split("\n## ", 1)[0].strip()
        # La primera línea puede ser un paréntesis aclaratorio del archivo, no una nota.
        lineas = [ln for ln in cuerpo.splitlines() if ln.strip().startswith("-")]
        return "\n".join(lineas)

    @api.model
    def _tokens_of(self, plan):
        """Los tokens efectivos: paleta con roles + escala tipográfica + escala de espaciado."""
        palette = plan.get("palette") or {}
        type_ = plan.get("type") or {}
        return {
            "palette": palette,
            "fonts": {"display": (type_.get("display") or {}).get("family"),
                      "body": (type_.get("body") or {}).get("family")},
            "scale": type_.get("scale") or {},
            "spacing": plan.get("spacing") or {},
        }

    @api.model
    def _preview(self, plan, run, source):
        return {
            "origen": {"reference": "con referencia (se reproduce el diseño)",
                       "partial": "referencia parcial (logo/colores fijos, diseño propio)",
                       "greenfield": "sin referencia (diseño desde cero)"}[source],
            "sujeto": plan.get("subject"),
            "concepto": plan.get("concept"),
            "paleta_con_roles": plan.get("palette"),
            "o_cc": plan.get("o_cc_map"),
            "tipografias": {"display": (plan.get("type") or {}).get("display"),
                            "cuerpo": (plan.get("type") or {}).get("body"),
                            "escala": (plan.get("type") or {}).get("scale")},
            "ritmo_de_secciones": [{"seccion": s.get("label") or s.get("id"),
                                    "tono": s.get("tone"),
                                    "layout": s.get("layout"),
                                    "imagen": s.get("image_role")}
                                   for s in plan.get("sections") or []],
            "firma": plan.get("signature"),
            "movimiento": plan.get("motion"),
            "voz_del_copy": plan.get("copy_voice"),
            "autocritica": plan.get("critique"),
            "modo_de_cuerpo": run.render_mode,
        }

    # ------------------------------------------------------------------ propuesta
    def _propose_designer(self, env, run, user, channel, proposals):
        if not channel:
            return None
        self.env["primate.sagui.pending.write"].sudo().search([
            ("channel_id", "=", channel.id), ("user_id", "=", user.id),
            ("state", "=", "pending"),
        ]).write({"state": "cancelled"})
        pending = self.env["primate.sagui.pending.write"].sudo().create({
            "token": secrets.token_hex(3), "channel_id": channel.id, "user_id": user.id,
            "operation": "website_designer", "model_name": "website.page",
            "values_json": json.dumps({"design_run_id": run.id}, ensure_ascii=False),
            "summary": _("Diseñar y construir «%(name)s» (%(mode)s, %(n)s secciones)") % {
                "name": run.name, "mode": run.render_mode,
                "n": len(json.loads(run.schema_json or "[]"))},
            "state": "pending",
        })
        if proposals is not None:
            proposals.append(pending.id)
        return pending

    # ==================================================================
    #  Ejecución: generar, construir, verificar
    # ==================================================================
    def _execute_designer(self, channel, author, pending):
        env = self.env(user=author.id)
        run = None
        try:
            data = json.loads(pending.values_json or "{}")
            run = env["sagui.design.run"].browse(data.get("design_run_id")).exists()
            if not run:
                raise ValueError(_("perdí el registro de la generación"))
            plan = json.loads(run.plan_json or "{}")
            if not plan:
                raise ValueError(_("perdí el plan; volvé a pedir el diseño"))

            run.write({"state": "building"})
            self.env.cr.commit()

            # FASE 1 — generación sección por sección (lo caro). Se persiste antes de tocar el sitio.
            body, css = self._generate_sections(env, run, plan)
            if not body:
                raise ValueError(_("no pude componer el cuerpo del sitio"))
            self.env.cr.commit()

            # FASE 2 — build. El tema es dueño de header/footer/paleta/menús/logo en los dos modos.
            result = self._build(env, run, plan, body, css)
            run.write({
                "state": "verifying",
                "website_id": result.get("website_id"),
                "page_id": result.get("page_id"),
                "page_url": result.get("url"),
                "prev_homepage_url": result.get("prev_homepage_url"),
            })
            pending.write({"state": "done", "result_info": "designer page_id=%s url=%s" % (
                result.get("page_id"), result.get("url"))})
            self.env.cr.commit()

            # FASE 3 — verificación visual + corrección determinística.
            reporte = self._verify_loop(env, run, plan)
            self.env.cr.commit()

            self._safe_post(channel, _(
                "✅ Listo: «%(site)s» está en %(url)s.\n\n%(reporte)s\n\nLa homepage anterior "
                "quedó guardada (%(prev)s): si no te gusta, pedime revertir."
            ) % {"site": result.get("website_name"), "url": result.get("url"),
                 "reporte": reporte, "prev": result.get("prev_homepage_url") or "/"})
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui designer: falló la construcción %s", pending.token)
            self.env.cr.rollback()
            try:
                pending.write({"state": "error", "result_info": str(e)[:200]})
                if run:
                    run.write({"state": "error"})
                self.env.cr.commit()
            except Exception:  # noqa: BLE001
                self.env.cr.rollback()
            self._safe_post(channel, _("❌ No pude construir el sitio (%(token)s): %(err)s") % {
                "token": pending.token, "err": e})

    # ------------------------------------------------------------------ generación por sección
    def _generate_sections(self, env, run, plan):
        """Una llamada por sección. Un blob único deriva de sección en sección; esto no."""
        role = run.role_id
        action = env["primate.ai.action"]._open(
            "disenar_web_build", run.name, ref="sagui.design.run,%s" % run.id)
        env = env(context={**env.context, "ai_action_id": action.id})

        plan_ctx = json.dumps({k: plan.get(k) for k in
                               ("subject", "concept", "palette", "type", "spacing", "signature",
                                "motion", "copy_voice", "tone")}, ensure_ascii=False)
        # Imágenes REALES disponibles. Si la lista va vacía se dice explícitamente, porque el
        # modelo, ante la duda, inventa un src y deja una imagen rota.
        imagenes = ["/web/image/%s" % a.id for a in run.asset_attachment_ids
                    if (a.mimetype or "").startswith("image/")]
        imagenes_txt = ("Imágenes reales disponibles (usá SOLO estas URLs): %s" % ", ".join(imagenes)
                        if imagenes else
                        "NO hay ninguna imagen disponible: no pongas <img>, resolvé con tipografía, "
                        "color y espacio.")
        htmls, csss = [], []
        for sec in plan.get("sections") or []:
            prompt = _(
                "PLAN (ya decidido, no lo cambies):\n%(plan)s\n\n"
                "SECCIÓN A COMPONER:\n%(sec)s\n\n"
                "%(imgs)s\n\n"
                "Las demás secciones del sitio, para que ésta encaje en el ritmo y no repita nada: "
                "%(otras)s"
            ) % {"plan": plan_ctx,
                 "sec": json.dumps(sec, ensure_ascii=False),
                 "imgs": imagenes_txt,
                 "otras": ", ".join("%s (%s)" % (s.get("label") or s.get("id"), s.get("tone"))
                                    for s in plan.get("sections") or [])}
            try:
                data = env["primate.ai.connector"].call(
                    [{"role": "user", "content": prompt}],
                    system=role.prompt(extra=SECTION_PROMPT) if role else SECTION_PROMPT,
                    max_tokens=MAX_SECTION_TOKENS, timeout=240)
            except Exception as e:  # noqa: BLE001
                _logger.warning("Sagui designer: falló la sección %s (%s)", sec.get("id"), e)
                continue
            text = "".join(b.get("text", "") for b in (data.get("content") or [])
                           if b.get("type") == "text")
            html, css = self._parse_section(text)
            if html:
                htmls.append(html)
                csss.append(css or "")
        if not htmls:
            return None, None
        css = self._base_css(plan) + "\n" + self._defuse_js_reveals("\n".join(csss))
        return "\n".join(htmls), css

    @api.model
    def _defuse_js_reveals(self, css):
        """Desarma los scroll-reveal que dependen de un JS que no existe.

        Patrón: `.brandsite .x{opacity:0}` + `.brandsite .x.is-visible{opacity:1}`. Como el sitio
        generado no lleva JavaScript (constraint 4), nadie agrega nunca esa clase y la sección
        queda invisible para siempre. El prompt ya lo prohíbe; esto es la red por si igual aparece,
        porque el costo de equivocarse es una sección entera en blanco.

        No se intenta parsear el CSS entero de una: un solo regex sobre todo el archivo se
        desincroniza con los bloques @media anidados y termina limpiando la regla equivocada. Se va
        declaración por declaración, se resuelve su selector mirando hacia atrás, y sólo se toca si
        existe la regla hermana que la revelaría.
        """
        reveal = ("is-visible", "visible", "in-view", "revealed", "show", "shown",
                  "active", "loaded", "animate", "animated", "reveal")
        presentes = [c for c in reveal if re.search(r"\.%s\b" % re.escape(c), css)]
        if not presentes:
            return css

        def selector_de(pos):
            """Selector de la regla que contiene la posición dada."""
            apertura = css.rfind("{", 0, pos)
            if apertura == -1:
                return None
            inicio = max(css.rfind("}", 0, apertura), css.rfind("{", 0, apertura)) + 1
            return css[inicio:apertura].strip()

        cortes = []
        for m in re.finditer(r"\s*(opacity\s*:\s*0(?:\.0+)?|visibility\s*:\s*hidden)\s*;?",
                             css, re.I):
            sel = selector_de(m.start())
            if not sel or sel.startswith("@"):
                continue
            if any(re.search(r"\.%s\b" % re.escape(c), sel) for c in presentes):
                continue  # ES la regla que revela: no se toca
            # ¿Existe la hermana que lo mostraría? Se busca por la última clase del selector.
            clases = re.findall(r"\.([a-zA-Z0-9_-]+)", sel)
            if not clases:
                continue
            base = clases[-1]
            hermana = any(
                re.search(r"\.%s\.%s\b" % (re.escape(base), re.escape(c)), css)
                for c in presentes)
            if hermana:
                cortes.append((m.start(), m.end(), sel))

        for inicio, fin, sel in reversed(cortes):
            _logger.info("Sagui designer: neutralicé un reveal dependiente de JS en «%s»", sel)
            css = css[:inicio] + css[fin:]
        return css

    # ------------------------------------------------------------------ tokens y base CSS
    @api.model
    def _base_css(self, plan):
        """Bloque de TOKENS (editable determinísticamente) + reglas base de especificidad uniforme.

        El padding vertical de las secciones vive acá y sólo acá: si cada sección lo redefine con
        otra especificidad, las costuras entre bloques se rompen. Es la disciplina de especificidad
        que exige la skill.
        """
        tokens = self._tokens_of(plan)
        palette = tokens["palette"]
        decls = []
        # Cada rol se publica bajo TODOS sus nombres plausibles. El plan habla de
        # "accent_contrast" y el CSS de "--c-on-accent": si el generador escribe
        # var(--c-accent-contrast) por arrastre del plan, la variable tiene que existir igual.
        # Un alias cuesta 20 bytes; un token inexistente deja una sección con texto invisible.
        for role_name, variantes in (
            ("bg", ("--c-bg",)),
            ("surface", ("--c-surface",)),
            ("text", ("--c-text", "--c-fg")),
            ("text_muted", ("--c-muted", "--c-text-muted")),
            ("accent", ("--c-accent",)),
            ("accent_contrast", ("--c-on-accent", "--c-accent-contrast")),
        ):
            value = (palette.get(role_name) or "").strip()
            if value:
                for var in variantes:
                    decls.append("%s:%s" % (var, value))
        for var, value in (tokens.get("scale") or {}).items():
            if str(var).startswith("--fs-"):
                decls.append("%s:%s" % (var, value))
        for var, value in (tokens.get("spacing") or {}).items():
            if str(var).startswith("--space-"):
                decls.append("%s:%s" % (var, value))

        fonts = tokens.get("fonts") or {}
        display = fonts.get("display") or "inherit"
        body = fonts.get("body") or "inherit"
        return "\n".join([
            FENCE_START,
            ".%s{%s;}" % (WRAPPER, ";".join(decls)),
            FENCE_END,
            # Base: cada propiedad se declara UNA vez, en un solo nivel de especificidad.
            ".%s{background:var(--c-bg);color:var(--c-text);font-family:'%s',sans-serif;}"
            % (WRAPPER, body),
            ".%s h1,.%s h2,.%s h3{font-family:'%s',sans-serif;text-wrap:balance;}"
            % (WRAPPER, WRAPPER, WRAPPER, display),
            ".%s h1{font-size:var(--fs-display);line-height:1.05;}" % WRAPPER,
            ".%s h2{font-size:var(--fs-h2);line-height:1.15;}" % WRAPPER,
            ".%s h3{font-size:var(--fs-h3);line-height:1.25;}" % WRAPPER,
            ".%s p,.%s li{font-size:var(--fs-body);line-height:1.6;}" % (WRAPPER, WRAPPER),
            # El espaciado entre secciones es propiedad de .sec y de nadie más.
            ".%s .sec{padding-block:var(--space-xl);padding-inline:var(--space-m);}" % WRAPPER,
            ".%s .sec > *{max-width:1200px;margin-inline:auto;}" % WRAPPER,
            ".%s a,.%s button{cursor:pointer;transition:color 200ms ease,background-color 200ms ease;}"
            % (WRAPPER, WRAPPER),
            ".%s :focus-visible{outline:2px solid var(--c-accent);outline-offset:2px;}" % WRAPPER,
            "@media (prefers-reduced-motion:reduce){.%s *{animation:none!important;"
            "transition:none!important;}}" % WRAPPER,
        ])

    # ------------------------------------------------------------------ build
    def _build(self, env, run, plan, body, css):
        palette = plan.get("palette") or {}
        type_ = plan.get("type") or {}
        # Mapeo de los roles del plan a las claves que espera el builder para recolorear el tema
        # (header/footer/menús viven fuera del wrapper y se estilan con esto).
        builder_palette = {
            "primary": palette.get("accent"), "accent": palette.get("accent"),
            "on_primary": palette.get("accent_contrast"),
            "on_accent": palette.get("accent_contrast"),
            "background": palette.get("bg"), "foreground": palette.get("text"),
            "muted": palette.get("surface"), "border": palette.get("text_muted"),
        }
        fonts = {
            "display": {"family": "'%s', sans-serif" % ((type_.get("display") or {}).get("family")
                                                        or "sans-serif"),
                        "google": self._google_url(type_)},
            "body": {"family": "'%s', sans-serif" % ((type_.get("body") or {}).get("family")
                                                     or "sans-serif"),
                     "google": self._google_url(type_)},
        }
        schema = {
            "nav": [{"label": n.get("label"), "target": n.get("anchor")}
                    for n in plan.get("nav") or []],
            "footer": plan.get("footer") or {"brand": run.name},
            "logo": None,
        }
        logo = run.asset_attachment_ids[:1]
        logo_url = ("/web/image/%s" % logo.id) if logo else None
        # El wrapper .brandsite ya lo pone la vista que crea el builder: no lo dupliques acá.

        if run.render_mode == "editable":
            schema["sections"] = plan.get("sections")
            schema["title"] = run.name
            return env["primate.website.builder"].build_snippet_site(
                schema, run.name, logo_url=logo_url, theme="theme_design_pipa")
        return env["primate.website.builder"].build_brandsite(
            body, css, run.name, schema=schema, logo_url=logo_url,
            theme="theme_design_pipa", palette=builder_palette, fonts=fonts)

    @api.model
    def _google_url(self, type_):
        """URL de Google Fonts para el par tipográfico del plan."""
        familias = []
        for key in ("display", "body"):
            fam = (type_.get(key) or {}).get("family")
            if fam and fam not in familias:
                familias.append(fam)
        if not familias:
            return ""
        partes = "&".join("family=%s:wght@400;600;700" % f.replace(" ", "+") for f in familias)
        return "https://fonts.googleapis.com/css2?%s&display=swap" % partes

    # ==================================================================
    #  Verificación + corrección determinística
    # ==================================================================
    def _verify_loop(self, env, run, plan):
        """Verificar → corregir tokens → re-verificar. Tope de vueltas; después se reporta."""
        role = run.role_id
        max_loops = max(1, role.verifier_max_loops or 3) if role else 3
        rubric_key = role.verifier_rubric_id.key if (role and role.verifier_rubric_id) else None
        if not rubric_key:
            run.write({"state": "done", "verdict": "pass",
                       "report": _("Sin rúbrica configurada: no se verificó.")})
            return run.report

        context = {"plan": plan, "tokens": self._tokens_of(plan)}
        # C5/C6 salen del registro elegido y viajan con el plan: la rúbrica dice que se chequean
        # como el resto del bloque C, y que si no hay registro simplemente no aplican.
        notas = self._register_notes(plan)
        if notas:
            context["register"] = {
                "key": (plan.get("register") or {}).get("key"),
                "extra_rubric": _(
                    "Notas del registro elegido, que se chequean como C5 y C6 del bloque C:\n%s"
                ) % notas,
            }
        pendientes, verificaciones = [], []
        result = {}
        for loop_no in range(max_loops):
            result = env["sagui.verifier.web"].verify(
                target={"url": run.page_url or "/", "website_id": run.website_id.id,
                        "design_run_id": run.id},
                rubric_key=rubric_key,
                context=context,
                reviewer={"provider": role.reviewer_provider, "model": role.reviewer_model},
                loop_no=loop_no, res_model="sagui.design.run", res_id=run.id,
            )
            if result.get("verification_id"):
                verificaciones.append(result["verification_id"])
            run.write({"loops_done": loop_no + 1,
                       "verification_ids": [(6, 0, verificaciones)]})
            self.env.cr.commit()

            findings = result.get("findings") or []
            if result.get("verdict") == "error":
                # La revisión no ocurrió (sin crédito, proveedor caído, no pude capturar).
                # Reintentar es tirar plata: se corta y se dice que no se verificó.
                pendientes = findings
                run.write({"state": "done", "verdict": "error",
                           "open_findings_json": json.dumps(pendientes, ensure_ascii=False)})
                run.report = _(
                    "No se pudo verificar: %s. El sitio quedó construido, pero NADIE revisó cómo "
                    "quedó — no lo tomes como aprobado."
                ) % (result.get("error") or _("el revisor no respondió"))
                return run.report
            if result.get("verdict") == "pass" or not findings:
                pendientes = []
                break
            aplicados, pendientes = self._apply_token_fixes(run, findings)
            self.env.cr.commit()
            if not aplicados:
                # Nada que corregir automáticamente: otra vuelta daría lo mismo.
                break

        run.write({
            "state": "done",
            "verdict": "pass" if not pendientes else "fail",
            "open_findings_json": json.dumps(pendientes, ensure_ascii=False),
        })
        return run.build_report()

    def _apply_token_fixes(self, run, findings):
        """Aplica los hallazgos de tipo token editando el bloque fenced. Nunca regenera.

        Devuelve (aplicados, pendientes). Un hallazgo 'token' sin valor concreto NO se inventa:
        se devuelve como pendiente para que lo vea el humano.
        """
        page = run.page_id
        if not page:
            return [], list(findings)
        html = page.primate_landing_html or ""
        match = _FENCE_RE.search(html)
        if not match:
            return [], list(findings)

        bloque = match.group(0)
        aplicados, pendientes = [], []
        for finding in findings:
            token = (finding.get("token") or "").strip()
            value = (finding.get("token_value") or "").strip()
            if finding.get("fix_kind") != "token" or not token or not value:
                pendientes.append(finding)
                continue
            if not token.startswith("--") or not re.fullmatch(r"[-a-z0-9]+", token):
                pendientes.append(finding)
                continue
            # Sólo se escribe un valor CSS plausible: nada de llaves ni punto y coma inyectados.
            if not re.fullmatch(r"[#\w\s.,()%/+*-]+", value):
                pendientes.append(finding)
                continue
            nuevo, n = re.subn(_DECL_RE % re.escape(token),
                               lambda m: m.group("pre") + value, bloque, count=1)
            if n:
                bloque = nuevo
                aplicados.append(finding)
            else:
                # El token no existía en el bloque: se agrega antes del cierre de la regla.
                bloque = bloque.replace("}", ";%s:%s}" % (token, value), 1)
                aplicados.append(finding)

        if aplicados:
            page.sudo().write({"primate_landing_html": _FENCE_RE.sub(
                lambda _m: bloque, html, count=1)})
            self.env.registry.clear_all_caches()
        return aplicados, pendientes
