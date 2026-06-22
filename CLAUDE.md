# CLAUDE.md — Sagui para Odoo (PrimateUY)

> Contexto maestro para Claude Code. Leé este archivo y `SPEC.md` antes de tocar código.

## Qué estamos construyendo

**Sagui** es Claude conversacional viviendo **dentro de Odoo 19**. El usuario le habla por
un chat de Discuss ("Sagui, necesito que…") y Claude responde con todo su poder: redactar
documentación, analizar casos, revisar datos de Odoo y razonar — sin salir del sistema.

El foco es **conversación + lectura/análisis de datos + generación de texto**. NO es un
ejecutor de comandos del sistema operativo. La escritura de datos es secundaria y va con
confirmación explícita (ver Seguridad).

## Arquitectura: dos módulos

1. **`primate_ai_connector`** (base, reutilizable): conector con la API de Anthropic.
   - Guarda la API key y el modelo en `ir.config_parameter`.
   - Expone un servicio (`primate.ai.connector`) con: `call()` (una request) y
     `run_conversation()` (loop de tool-use multi-paso).
   - Loguea uso de tokens/costo (`primate.ai.usage.log`) — equivalente al medidor del
     widget de escritorio, pero dentro de Odoo.
   - De este módulo cuelgan Sagui y el resto del ecosistema de IA.

2. **`primate_sagui`** (la experiencia de chat):
   - Usuario/partner bot "Sagui" en Discuss.
   - Hook al `message_post` del `discuss.channel`: cuando el usuario le escribe al bot,
     dispara el servicio del conector con las **tools de lectura** y postea la respuesta.
   - Tools v1 (solo lectura): `buscar_registros`, `agrupar_registros`, `describir_modelo`.

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
- **v1 es solo lectura.** No implementes create/write/unlink todavía.
- Cuando se agregue escritura (fase 3), va con un paso de **confirmación en el chat** antes de
  ejecutar, y limitada por whitelist de modelos.
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

- Entorno multi-cliente bajo `~/Desktop/Odoo/` (worktrees + symlinks). Estos dos módulos van
  en el repo compartido del ecosistema.
- Actualizar: `odoo-bin -c <conf> -u primate_ai_connector,primate_sagui -d <db> --stop-after-init`
  para validar que instala/upgradea sin error.
- Probar el chat: instalar, configurar la API key en Ajustes, abrir Discuss y mandarle un DM al
  usuario Sagui.

## Qué NO hacer

- No reimplementar el parseo de JSON a mano: usá **tool-use nativo** de la Messages API
  (`tools` + `tool_use`/`tool_result`). Ya está la referencia en `ai_connector.py`.
- No usar `sudo()` para consultas de negocio.
- No agregar escritura de datos en v1.
- No inventar nombres de métodos/hooks de Discuss: **verificá contra el código real de Odoo 19**
  (`addons/mail`, `addons/mail_bot`) los puntos de extensión exactos antes de implementar el hook.
- No hardcodear la API key.
