# Sagui para Odoo — Build-kit para Claude Code

Este paquete es lo que **Claude Code** necesita para programar Sagui: Claude conversacional
dentro de Odoo 19 (chat por Discuss, lectura/análisis de datos, documentación). Vos no
escribís el módulo a mano: le das esto a Claude Code y él lo implementa.

## Qué hay adentro

- **`CLAUDE.md`** — contexto maestro: qué es, arquitectura, convenciones PrimateUY, reglas de
  seguridad. (Claude Code lo lee automáticamente si está en la raíz del proyecto.)
- **`SPEC.md`** — especificación por fases con tareas y criterios de aceptación.
- **`.claude/settings.json`** — permisos sugeridos para Claude Code (ajustá a tus convenciones).
- **`primate_ai_connector/`** — módulo base (conector con la API de Claude). El servicio
  `ai_connector.py` ya trae la llamada y el **loop de tool-use** implementados como referencia.
- **`primate_sagui/`** — el chat: bot de Discuss + tools de lectura. La lógica de tools y la
  orquestación están scaffoldeadas; los puntos marcados **[VERIFICAR v19]** los resuelve
  Claude Code contra el código real de la versión.

## Cómo usarlo

1. Copiá las dos carpetas de módulos (`primate_ai_connector/`, `primate_sagui/`) al repo
   compartido del ecosistema de IA, en tu addons-path (rama base `19.0`).
2. Dejá `CLAUDE.md` y `SPEC.md` en la raíz desde donde vas a correr Claude Code (o en la
   carpeta de los módulos). Mové/mergeá `.claude/settings.json` con tu config habitual.
3. Abrí Claude Code ahí y dale una instrucción tipo:

   > Leé `CLAUDE.md` y `SPEC.md`. Implementá la **Fase 1** completa. Antes de tocar los
   > puntos `[VERIFICAR v19]`, revisá el código real de `addons/mail` y `addons/mail_bot`
   > de la versión instalada. Respetá las reglas de seguridad (ejecución `with_user`, sin
   > `sudo` para datos de negocio, solo lectura). Probá que instala con
   > `odoo-bin -u primate_ai_connector,primate_sagui -d <db> --stop-after-init`.

4. Tras instalar: Ajustes → Primate IA → pegá tu API key de Anthropic. Después abrí Discuss y
   mandale un DM a **Sagui**.

## Notas

- Modelo por defecto: `claude-sonnet-4-6` (análisis/documentación). Configurable en Ajustes.
- El conector es genérico: los demás módulos del ecosistema de IA deberían apoyarse en
  `primate.ai.connector` en lugar de hablarle a la API por su cuenta.
- Commits en español con `[ADD]`/`[FIX]`/`[IMP]`. Promoción `19.0` → staging → support → prod.
- El log de uso (`primate.ai.usage.log`, menú en Administración) es el equivalente dentro de
  Odoo al medidor de tokens del widget de escritorio.

## Roadmap

- **Fase 1 (este kit):** chat Discuss + tools de lectura + log de uso.
- **Fase 2:** botón "Consultar a Sagui" en cualquier formulario, con el registro actual como contexto.
- **Fase 3:** tools de escritura (create/write) con confirmación en el chat y whitelist de modelos.
