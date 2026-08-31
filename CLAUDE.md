# CLAUDE.md — Sagui para Odoo (PrimateUY)

> Contexto maestro para Claude Code. Leé este archivo y `SPEC.md` antes de tocar código.

## Qué estamos construyendo

**Sagui** es Claude conversacional viviendo **dentro de Odoo 19**. El usuario le habla por
un chat de Discuss ("Sagui, necesito que…") y Claude responde con todo su poder: redactar
documentación, analizar casos, revisar datos de Odoo y razonar — sin salir del sistema.

El foco es **conversación + lectura/análisis de datos + generación de texto**. NO es un
ejecutor de comandos del sistema operativo. La escritura de datos es secundaria y va con
confirmación explícita (ver Seguridad).

## Arquitectura

`primate_ai_connector` es la base y de ahí cuelga todo lo demás.

1. **`primate_ai_connector`** (base, reutilizable): conector con la API de Anthropic.
   - Guarda la API key y el modelo en `ir.config_parameter`.
   - Expone `primate.ai.connector` con `call()` (una request), `run_conversation()` (loop de
     tool-use multi-paso) y `stream_conversation()` (SSE para la UI web).
   - Loguea tokens/costo (`primate.ai.usage.log`) y agrupa el gasto por acción
     (`primate.ai.action`), que es lo que permite costear una generación entera.

2. **`primate_sagui`** (el núcleo): bot en Discuss, historial, y el **esquema genérico** que
   usan los roles.
   - Tools de lectura (`buscar_registros`, `agrupar_registros`, `describir_modelo`) y de
     escritura, éstas siempre detrás del gate de `primate.sagui.pending.write`.
   - `sagui.skill` / `sagui.role`: las skills son `.md` VERSIONADOS en un addon que se cargan
     en runtime. Una sola fuente de verdad — el mismo archivo que lee Claude Code guía a Sagui
     en producción. No dupliques una skill hardcodeándola en un prompt de Python.
   - `sagui.verification`: el Check/Verify genérico (post-condiciones + evidencia → hallazgos
     estructurados). La rúbrica se le pasa como CONTENIDO o como `sagui.skill` de tipo
     checklist, **nunca como ruta de archivo**: es genérico y no sabe en qué addon vive quien
     lo usa.
   - `sagui.executable.task` → `sagui.recipe` y `sagui.automation`: tareas guardadas y
     programadas, con auditoría en `sagui.task.action.log`.
   - `sagui.connector`: framework de conectores MCP con credenciales por usuario (cifradas).

3. **`primate_sagui_web`**: interfaz Owl — panel en el systray y app de página completa, con
   streaming por SSE contra el mismo backend.

4. **`primate_website_generator`**: `primate.website.builder`, el motor que construye el sitio
   (página sobre `website.layout`, tema, menús, footer, logo). No habla con el modelo: recibe
   HTML/CSS ya generado.

5. **`primate_sagui_designer`**: el rol "Diseñador Web Odoo". Enruta por presencia de
   referencia, arma el plan, genera sección por sección y verifica con capturas reales
   (`tools/shoot.py` por subprocess) más un revisor independiente. Sus skills viven en
   `skills/`.

6. **`primate_sagui_orchestration`**: puente con Claude Code headless (jobs, runner local,
   captura de actividades).

7. **`theme_design_pipa`**: tema de marca que aplican los sitios generados.

Además, `odoo-mcp/` es un servidor MCP (XML-RPC) para consultar proyectos y tareas desde
Claude Code. Es otra familia de tools: usa credencial propia, NO los permisos del usuario.

## Stack / versión

- **Odoo 19** (rama base `19.0`).
- Python 3 (el de Odoo). HTTP con `requests` (ya disponible en Odoo).
- Modelo Claude por defecto: **`claude-sonnet-4-6`** (análisis/documentación necesitan buena
  cabeza; Haiku queda para tareas de enrutado simples).
- API string del modelo: `claude-sonnet-4-6`. Versión de API: `2023-06-01`.

## Costo / optimización (parte de la fase 1, ya en el conector)

Sonnet en un loop de tool-use reenvía el historial + resultados en cada vuelta, así que el
costo se va en tokens de entrada. Palancas ya implementadas:

- **Prompt caching:** `call()` marca el system y la última tool con `cache_control`, y
  `run_conversation()` cachea el historial acumulado (`_apply_history_cache` marca solo el
  último bloque y limpia los viejos). Es GA en la Messages API (sin header beta). Las lecturas
  de caché cuestan ~10% del input. El log de uso captura `cache_creation_input_tokens` /
  `cache_read_input_tokens` (`cache_write_tokens` / `cache_read_tokens`) para que el costo sea
  exacto.
- **Tiering de modelo:** `primate.ai.model` (Sonnet, análisis) y `primate.ai.model_fast`
  (Haiku, consultas simples). `primate.sagui.assistant._pick_model()` enruta por heurística
  (+ override `/rapido` y `/analiza`; con imágenes/PDF usa siempre el de análisis).
- Además: capear `limit`/`fields` en las tools (no devolver registros enormes), `max_tokens`
  acotado y respuestas concisas (el system prompt ya lo pide).

## Seguridad — REGLAS DURAS (no negociables)

- **Ejecutá SIEMPRE las operaciones de datos como el usuario que pregunta**:
  `self.env(user=user.id)[model]...`. **Nunca `sudo()` para datos del usuario.** Así los
  ACLs y record rules de Odoo aplican solos: Sagui no ve ni toca lo que el usuario no podría.
- `sudo()` se permite SOLO para infraestructura interna (leer la API key, escribir el log de
  uso), nunca para responder consultas de negocio.
- **La escritura existe y va SIEMPRE con confirmación humana.** Nada se crea, modifica ni
  publica sin que el usuario responda «confirmar»: la operación queda en
  `primate.sagui.pending.write` y recién ahí se ejecuta, con los permisos del usuario y
  revalidando la whitelist de modelos (pudo cambiar entre la propuesta y la confirmación).
  Si agregás un flujo que escribe, pasá por ese gate; no inventes otro camino.
- Lo que construye un sitio corre **diferido en el cron**, no dentro del request del chat.
  Sumá tu operación en `_build_operations()`, que es la fuente única: hay un test que falla
  si algún archivo vuelve a enumerar esa lista a mano.
- Límite de iteraciones del loop de tools (`max_iterations`, default 6) para evitar bucles.
- Cap de `limit` en búsquedas (máx 100 registros por tool call).
- Nunca loguees ni expongas la API key. No la pongas en el código ni en datos versionados.

## Convenciones PrimateUY

- **Manifest**: usar el formato del template (ver los `__manifest__.py` ya scaffoldeados).
  Versión `19.0.1.0.0`. Author `PrimateUY`. License `LGPL-3`.
- **Estructura de carpetas**: `models/`, `views/`, `security/`, `data/`, `static/` según
  corresponda. Un modelo por archivo cuando crece.
- **Commits en español** con tag: `[ADD]` nueva funcionalidad, `[FIX]` corrección,
  `[IMP]` mejora. Ej: `[ADD] primate_sagui: bot de Discuss y loop de tools`.
- **Branching**: base `19.0`; promoción por merge a `staging` → `support` → `prod`.
- Código y comentarios en español (consistente con el resto del stack de PrimateUY).

## Cómo correr / probar

- Entorno multi-cliente bajo `~/Desktop/Odoo/` (worktrees + symlinks). Todos estos módulos
  viven en este repo compartido del ecosistema.
- Actualizar: `odoo-bin -c <conf> -u primate_ai_connector,primate_sagui,primate_sagui_designer
  -d <db> --stop-after-init` para validar que instala/upgradea sin error.
- Tests: `--test-enable --test-tags /primate_sagui,/primate_sagui_designer`. Ojo con los tests
  que dependen de otro módulo: Odoo corre los de cada módulo apenas lo carga, así que un test
  en `primate_sagui` que necesite el diseñador se saltea SIEMPRE (el diseñador se carga
  después). Va en el módulo que tiene la dependencia declarada.
- Probar el chat: instalar, configurar la API key en Ajustes, abrir Discuss y mandarle un DM al
  usuario Sagui.

## Trampas de Odoo 19 (verificadas contra el código instalado)

Cosas que fallan en silencio o mandan a diagnosticar donde no es. Todas comprobadas en este
proyecto, no leídas en un changelog.

- **`_sql_constraints` se IGNORA.** Sólo deja un warning en el log y la constraint nunca llega
  a Postgres: podíamos insertar dos `sagui.skill` con la misma `key`, y dos propuestas podían
  compartir token. Se declaran como `_nombre = models.Constraint("UNIQUE (col)", "mensaje")`.
- **`res.users.groups_id` → `group_ids`.** Con el nombre viejo revienta con
  `ValueError: Invalid field 'groups_id'`.
- **`documents.folder` NO existe.** Las carpetas son `documents.document` con `type='folder'`,
  jerarquía por `folder_id`, permisos por `documents.access`. Una ruta "A / B / C" se resuelve
  recorriendo el árbol.
- **La barra de edición del frontend** es `.o_frontend_to_backend_nav`, se sirve con `d-none` y
  sólo `redirect.js` la muestra si el usuario no es público. Verificarla en una captura no
  prueba nada: se comprueba por DOM. Y como es `position: fixed`, **`offsetParent` da `null`
  aunque esté visible** — usar `getComputedStyle` + `getBoundingClientRect`.
- **Un `ir.asset` que apunta a un attachment-por-URL no se inlinea** en `web.assets_frontend`.
  Por eso el builder embebe el CSS como `<style>` dentro de `website.page.primate_landing_html`.
- **`Markup.replace()` escapa su argumento.** Editar HTML leído de un campo `fields.Html` con
  `.replace(a, b)` guarda `b` escapado y el cambio "no se aplica". Convertir con `str()` antes,
  o usar `re.sub` (el módulo `re` devuelve `str` plano).
- **El header y el footer son del WEBSITE, no de la página.** Cada generación los pisa. Si
  verificás una generación vieja después de otra nueva, estás midiendo el chrome de la nueva.

## Qué NO hacer

- No reimplementar el parseo de JSON a mano: usá **tool-use nativo** de la Messages API
  (`tools` + `tool_use`/`tool_result`). Ya está la referencia en `ai_connector.py`.
- No usar `sudo()` para consultas de negocio.
- No agregar escritura de datos en v1.
- No inventar nombres de métodos/hooks de Discuss: **verificá contra el código real de Odoo 19**
  (`addons/mail`, `addons/mail_bot`) los puntos de extensión exactos antes de implementar el hook.
- No hardcodear la API key.
