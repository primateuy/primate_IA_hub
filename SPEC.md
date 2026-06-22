# SPEC.md — Implementación de Sagui para Odoo 19

Especificación por fases para Claude Code. Cada tarea tiene criterios de aceptación.
El esqueleto de archivos ya está; varios traen implementación de referencia (sobre todo el
conector). Completá los `TODO` y verificá los puntos marcados como **[VERIFICAR v19]**.

---

## Alcance

- **v1 (este entregable):** chat por Discuss con el bot Sagui + tools de lectura
  (`buscar_registros`, `agrupar_registros`, `describir_modelo`) + respuesta en el chat +
  log de uso de tokens.
- **Fase 2:** botón "Consultar a Sagui" en cualquier formulario, que abre el chat con el
  registro actual como contexto.
- **Fase 3:** tools de escritura (create/write) con confirmación en el chat y whitelist.

No implementar fase 2/3 todavía.

---

## Estructura de archivos

```
primate_ai_connector/
  __manifest__.py
  models/__init__.py
  models/ai_connector.py        # servicio: call() + run_conversation() (REFERENCIA, casi completo)
  models/ai_usage_log.py        # log de tokens/costo
  models/res_config_settings.py # API key + modelo en ir.config_parameter
  security/ir.model.access.csv
  views/res_config_settings_views.xml
  views/ai_usage_log_views.xml

primate_sagui/
  __manifest__.py
  models/__init__.py
  models/sagui_assistant.py    # tool specs + runner (with_user) + orquestación del mensaje
  models/discuss_channel.py     # hook message_post -> dispara a Sagui  [VERIFICAR v19]
  data/sagui_user.xml          # partner + user bot "Sagui"
  security/ir.model.access.csv
```

---

## Fase 1 — Tareas

### T1. Conector base (`primate_ai_connector`)
- `ai_connector.py` ya trae `call()`, `run_conversation()` y `_log_usage()` como referencia
  funcional. Revisá, ajustá imports a la convención de Odoo 19 y dejalo andando.
- `res_config_settings.py`: campos `primate_ai_api_key` y `primate_ai_model` mapeados a
  `ir.config_parameter` (`primate_ai.api_key`, `primate_ai.model`). Default del modelo:
  `claude-sonnet-4-6`.
- `ai_usage_log.py`: modelo `primate.ai.usage.log` con `model_name`, `input_tokens`,
  `output_tokens`, `user_id`, `cost` (computado: in/1e6*precio_in + out/1e6*precio_out;
  precios configurables o constantes para sonnet-4-6 = 3 / 15 por millón).
- Vistas mínimas: sección en Ajustes para la key/modelo; vista lista+pivot del log de uso.
- **Aceptación:** el módulo instala; desde un shell de Odoo,
  `env['primate.ai.connector'].call([{ "role":"user","content":"hola" }])` devuelve respuesta
  y crea un registro en el log.

### T2. Tools de lectura (`sagui_assistant.py`)
- `_tool_specs()` devuelve el JSON-schema de las tools (ver "Esquema de tools" abajo).
- `_run_tool(name, args, user)`: ejecuta la operación **con `self.env(user=user.id)`**
  (¡crítico para seguridad!). Implementar:
  - `buscar_registros`: `search` + `read(fields)`, `limit` capeado a 100, devuelve JSON.
  - `agrupar_registros`: `read_group(domain, fields, groupby)`.
  - `describir_modelo`: lista campos del modelo (`fields_get`) — nombre, tipo, string, relación.
- Serializá a JSON con `default=str` (fechas, etc.).
- **Aceptación:** `_run_tool('buscar_registros', {'model':'res.partner','limit':3}, user)`
  devuelve JSON con 3 partners visibles para ese usuario.

### T3. Orquestación + bot de Discuss
- `sagui_assistant.py` → método `process_user_message(channel, author, body)`:
  1. Arma `messages` con el historial reciente del canal (mapear mensajes a roles user/assistant).
  2. Llama `env['primate.ai.connector'].run_conversation(messages, system=SYSTEM_PROMPT,
     tool_specs=self._tool_specs(), tool_runner=lambda n,a: self._run_tool(n,a,author))`.
  3. Postea el texto final en el canal como mensaje del bot (markdown→HTML).
- `SYSTEM_PROMPT`: definí a Sagui como asistente interno de Odoo de PrimateUY; en español;
  conciso; usa las tools para mirar datos antes de afirmar; si no tiene acceso, lo dice.
- `discuss_channel.py`: heredar `discuss.channel`, hook en `message_post`:
  - Detectar que el canal incluye al bot Sagui y que el **autor NO es el bot** (guardia
    anti-recursión).
  - Llamar `process_user_message(...)`.
  - **[VERIFICAR v19]:** el punto de extensión exacto. Mirá `addons/mail_bot/models/mail_bot.py`
    y `addons/mail/models/discuss_channel.py` en el código de la v19 instalada. Modelá el
    patrón sobre cómo OdooBot responde. Considerá ejecutar la respuesta en un cron/queue o con
    `with_delay` si el módulo `queue_job` está disponible, para no bloquear el `message_post`
    (la llamada a Claude tarda segundos). Si no, hacelo síncrono pero documentá el trade-off.
- `data/sagui_user.xml`: crear partner + `res.users` bot "Sagui" (active, share=False o
  el patrón de OdooBot). **[VERIFICAR v19]** cómo se marca un usuario como bot/OdooBot-like.
- **Aceptación:** instalado y con API key, al mandar un DM "¿cuántos clientes activos hay?"
  el bot responde un número correcto (vía tool), respetando los permisos del usuario.

### T4. Robustez
- Manejo de errores: si Claude o una tool fallan, postear un mensaje claro en el chat (no romper).
- `max_iterations` del loop = 6. Timeout de la request = 60s.
- Render markdown→HTML para la respuesta (usar el util de Odoo o `markupsafe`); sanitizar.

---

## Esquema de tools (para la Messages API)

```json
[
  {
    "name": "buscar_registros",
    "description": "Busca registros en un modelo de Odoo y devuelve los campos pedidos. Respeta los permisos del usuario.",
    "input_schema": {
      "type": "object",
      "properties": {
        "model": {"type": "string", "description": "Modelo técnico, ej. 'res.partner'"},
        "domain": {"type": "array", "description": "Dominio Odoo, ej. [['active','=',true]]"},
        "fields": {"type": "array", "items": {"type": "string"}},
        "limit": {"type": "integer"},
        "order": {"type": "string"}
      },
      "required": ["model"]
    }
  },
  {
    "name": "agrupar_registros",
    "description": "Agrupa/agrega registros (read_group) para KPIs y análisis.",
    "input_schema": {
      "type": "object",
      "properties": {
        "model": {"type": "string"},
        "domain": {"type": "array"},
        "fields": {"type": "array", "items": {"type": "string"}},
        "groupby": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["model", "fields", "groupby"]
    }
  },
  {
    "name": "describir_modelo",
    "description": "Devuelve los campos de un modelo (nombre, tipo, etiqueta, relación) para que sepas qué consultar.",
    "input_schema": {
      "type": "object",
      "properties": {"model": {"type": "string"}},
      "required": ["model"]
    }
  }
]
```

---

## Forma del request a la Messages API (referencia)

Endpoint: `POST https://api.anthropic.com/v1/messages`
Headers: `content-type: application/json`, `x-api-key: <key>`, `anthropic-version: 2023-06-01`.

Loop de tool-use:
1. Mandás `messages` + `system` + `tools`.
2. Si la respuesta trae bloques `tool_use`, ejecutás cada uno y reinyectás un mensaje
   `role:"user"` con bloques `tool_result` (`tool_use_id`, `content`). Volvés a 1.
3. Si no hay `tool_use`, juntás los bloques `text` → respuesta final.

Ver `primate_ai_connector/models/ai_connector.py::run_conversation` (ya implementado).

---

## Optimización de costo (ya incluida en el esqueleto — verificar y mantener)

Implementado en el conector; al completar la fase 1 no lo rompas:

- **Prompt caching** (`ai_connector.call` / `_apply_history_cache`): cachea el prefijo
  estático (system + tools) y el historial del loop con `cache_control: ephemeral`. Es GA en
  la Messages API (no requiere header beta). Mínimo cacheable ~1024 tokens: si el prefijo es
  chico no cachea, no pasa nada. El log guarda los tokens de caché para costo exacto.
- **Tiering** (`sagui_assistant._pick_model`): Haiku para consultas simples, Sonnet para
  análisis/documentación, con override `/rapido` y `/analiza`. Mejora futura opcional:
  pre-clasificar con una llamada barata a Haiku en vez de la heurística por longitud/palabras.
- **Tamaño de resultados de tools:** `limit` capeado a 100; pedir `fields` acotados; en
  `describir_modelo`, considerar resumir modelos con cientos de campos. Es el mayor costo
  oculto (se reenvía en cada iteración del loop).
- **Salida:** `max_tokens` razonable y el system prompt pide respuestas concisas.

## Fase Web (Owl) — módulo `primate_sagui_web`

Interfaz visual sobre el MISMO backend. Dos superficies + streaming. Scaffold ya incluido;
completá los `TODO`/`[VERIFICAR v19]`.

### Arquitectura del streaming (SSE por HTTP, no bus)
- El cliente Owl hace `POST /sagui/ask` con `{message, history}` y **lee la respuesta como
  stream** (`fetch` + `response.body.getReader()` + `TextDecoder`, parseando eventos
  `data: {...}` separados por línea en blanco).
- El controller (`controllers/main.py`) devuelve `text/event-stream` y itera el generador
  `primate.ai.connector.stream_conversation(...)`, que hace el loop de tool-use con
  `stream:true` en la Messages API y va produciendo eventos: `text` (delta), `tool`
  (`phase: start|done`), `error`, `done` (con `usage`). La parte de SSE de Claude ya está
  implementada como referencia en `models/sagui_stream.py`.
- **[VERIFICAR v19]:** la forma idiomática de devolver streaming HTTP en Odoo 19
  (`request.make_response` con generador vs. utilidades `http.Stream`/werkzeug `Response`), y
  que el worker/cursor tolere el stream (respuestas de largo de chat están bien). Verificar
  también que no haya buffering intermedio (nginx) que rompa el SSE (`X-Accel-Buffering: no`).

### Tareas
- **W1. Servicio** (`services/sagui_service.js`): `ask(message, handlers)` consume el SSE y
  dispara `onText/onTool/onError/onDone`; mantiene `history`. (Scaffold listo.)
- **W2. Componente de chat** (`chat/sagui_chat.js` + `.xml`): burbujas user/assistant, chips
  de tools en vivo, textarea + enviar, autoscroll. **TODO:** render markdown→HTML seguro de la
  respuesta (no texto plano); botones de acción en respuestas (abrir registro vía `doAction`);
  statcards/mini-tablas para resultados de datos (fase pulido).
- **W3. Systray** (`systray/sagui_systray.js` + `.xml`): botón "Sagui" que abre el panel
  deslizante con `<SaguiChat compact=true/>`. **[VERIFICAR v19]** registro en
  `registry.category("systray")` y orden.
- **W4. App de página completa** (`app/sagui_app.js` + `.xml`): client action
  `primate_sagui_app` que renderiza `<SaguiChat/>`; menú "Sagui" en `data/sagui_app_action.xml`.
  **[VERIFICAR v19]** registro en `registry.category("actions")` y `web_icon` del menú app.
- **W5. Estética**: `static/src/sagui.scss` (tokens HUD: vidrio oscuro, acento periwinkle
  `#8FA6FF`, ámbar `#F2B05E`, monospace para datos, punto que late). Respetar foco de teclado
  y `prefers-reduced-motion`.
- **W6. Voz (opcional, nativa acá)**: en navegador real `webkitSpeechRecognition` SÍ funciona
  (a diferencia del WKWebView de Übersicht). Botón de micrófono en el chat que dicta al textarea.
- **Aceptación:** instalado, con API key cargada, desde el botón del systray (en cualquier
  pantalla) y desde el menú Sagui, escribir un pedido devuelve la respuesta **apareciendo en
  streaming**, con los chips de tools mientras consulta datos, respetando permisos del usuario.

### Mantené (de fases previas)
- Caching y tiering ya están en el conector y se usan también en streaming (`stream_conversation`
  aplica `_apply_history_cache` y respeta el `model` elegido por `_pick_model`).
- El bot de Discuss (`primate_sagui`) se mantiene para móvil; ambas superficies pegan al mismo
  backend.

## Notas finales

- El conector es genérico a propósito: los otros módulos del ecosistema de IA deberían
  apoyarse en `primate.ai.connector` en vez de hablarle a la API por su cuenta.
- Las tools de lectura son básicamente las mismas operaciones del servidor MCP de Odoo
  (XML-RPC) ya existente: reusá esa lógica/criterios donde aplique.
