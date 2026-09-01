# Diseño técnico — Sagui, rol «Gestor de Proyectos» (`primate_sagui_pm`)

Estado: **propuesta, pendiente de OK**. No hay código escrito todavía.
Entorno de implementación y prueba: `~/Desktop/Odoo/clients/primate-innobyt`
(`primate_IA_hub` y `primateProd` son submódulos del mismo `addons_path`).

---

## 0. Lo que encontré (y cambia el encargo)

**0.1 El área ya existe, pero en el módulo equivocado y en el nivel equivocado.**
`primate_project_dashboard` define hoy:

- `project.task.area` — `Selection([('technical'),('functional'),('admin')])`, `index`, `tracking`
  (`models/project_task.py:15`).
- `hr.employee.area` — el mismo Selection, con `groups="hr.group_hr_user"`. Es el **denominador**
  de la capacidad por área (`_primate_employees_by_area`).
- `project.project` **no tiene área**.

O sea: el dashboard no "deriva el área de otro lado", la **define** él, en la tarea, como Selection
y sin responsable. Unificar = sacar el campo a una definición compartida, subirlo al proyecto y
convertirlo en Many2one (el responsable de área es requisito del encargo: las propuestas van a él).
Eso toca 6 métodos, 4 vistas, el tour y los tests del dashboard, más migración de datos.

**0.2 `sagui.verification` hoy sólo acepta evidencia de IMAGEN.**
`_collect` descarta todo item sin `attachment_id`; `_measure_evidence` lee la cabecera PNG y marca
como *degradada* cualquier evidencia cuyas dimensiones no pueda leer, lo que corta la verificación
con `verdict='error', infra=True` antes de llamar al revisor. Un verificador con evidencia por
consulta ORM **no corre** sin extender el modelo genérico. Es exactamente el tipo de cosa que el
segundo caso del Check/Verify tenía que destapar.

**0.3 No hace falta una bandeja nueva.** Ya hay dos, y el ruteo está resuelto en
`sagui.executable.task._route_to_review`: receta (humano presente) → `primate.sagui.pending.write`;
automatización (desatendida) → `sagui.automation.proposal`. El modo a) del encargo es una receta,
así que sus propuestas caen en `pending.write` tal como se pidió, sin código nuevo. La aprobación
**por bloque** es agrupación + acción masiva, no un modelo nuevo.

**0.4 El punto de reuso es `_intercept_write(env, op, {model, ids, values}, ctx)`.**
Un motor determinístico que empuje cada hallazgo por ahí hereda gratis el gate, el scope `auto`,
la auditoría (`sagui.task.action.log`) y el dry-run. No se inventa otro camino de escritura.

---

## 1. El área: `primate_project_area`

Módulo base nuevo, mínimo, `depends: ['project', 'hr']`.

```
primate.area
  name, code (unique), user_id  ← responsable del área, sequence, active, color
  data: area_technical / area_functional / area_admin  (code = technical|functional|admin,
        los mismos valores del Selection viejo → la migración mapea por code)

project.project.area_id   M2o primate.area, ondelete='restrict', index, tracking
project.task.area_id      M2o primate.area, index, tracking — hereda del proyecto, editable
hr.employee.area_id       M2o primate.area, groups='hr.group_hr_user' (mismo criterio de hoy)
```

**Herencia proyecto → tarea sin pisar decisiones.** Nada de `related` ni de compute stored
editable (se recalcula al mover la tarea y borra la edición manual). Regla explícita:
- en `create`, si no viene `area_id`, toma la del proyecto;
- en `write` de `project_id`, re-hereda **sólo** si el área actual era la del proyecto anterior
  (si alguien la cambió a mano, se respeta).

**Impacto en `primate_project_dashboard`** (commit `[REF]` en su rama, pre-PR):
`_primate_area_task_domain`, `_primate_area_counts`, `_primate_area_capacity`,
`_primate_employees_by_area`, `_primate_area_available_hours`, `_primate_dashboard_areas`,
`action_open_area_tasks`, las vistas de tarea/empleado, el tour y los tests pasan de `area` a
`area_id`. El payload del RPC pasa a llevar `area_id` **y** `area_code`: el JS y el tour siguen
enganchando por code, no por id, que cambia entre bases.

**Migración de datos** (en `primate_project_dashboard`, que es el que pierde la columna):
`pre-migrate` copia `project_task.area` y `hr_employee.area` a una tabla propia
(`primate_area_migracion`); `post-migrate` resuelve `area_id` por code.

**Decisiones de datos — RATIFICADAS, no defaults técnicos.** Son la respuesta a «¿por qué este
proyecto quedó sin área?», y por eso se documentan como decisión aceptada:

| situación | resultado | por qué |
|---|---|---|
| proyecto con tareas de varias áreas | el área **mayoritaria de sus tareas** | es el dato que ya existe, no una suposición |
| **empate** entre dos o más áreas | **vacío** | no se desempata inventando |
| proyecto **sin tareas** | **vacío** | no hay de dónde deducirla |
| **code desconocido** (base con el Selection extendido) | **vacío + warning con los ids** | nunca se mapea al azar y nunca corta la migración |
| proyecto con área ya cargada a mano | **se respeta** | lo cargado por una persona manda sobre lo deducido |

Vacío es un dato legítimo, no una falla: es exactamente el hallazgo de la regla «proyecto sin
área» del rol Gestor, que lo va a proponer con nombre y apellido.

**La lógica del mapeo NO vive en el script de `migrations/`.** Un script de migración no es
importable (no es parte del paquete Python del addon), así que un test sólo podría probar una
copia de la lógica — y una copia que se desincroniza es peor que no tener test. El mapeo va en una
función pura del addon:

```python
# primate_project_dashboard/models/area_migration.py
def migrar_area_por_code(cr, env, tabla_respaldo): ...   # tareas, proyectos y empleados
```

El `post-migrate` la llama; el test la llama con datos sembrados a mano. Se prueba el código real.

**`tests/test_area_migration.py`** — test propio, no alcanza con que la suite quede verde:

| caso | esperado |
|---|---|
| `technical` / `functional` / `admin` | resuelve a las tres `primate.area` por `code` |
| tarea con área nula | `area_id` vacío (no la primera área por defecto) |
| code desconocido (dato viejo de una base con un Selection extendido) | `area_id` vacío **+ warning en el log con los ids**; nunca se cae ni lo mapea al azar |
| proyecto con 3 tareas Técnica y 1 Funcional | `area_id` = Técnica |
| proyecto con 2 y 2 | `area_id` vacío (empate no se desempata inventando) |
| proyecto sin tareas | `area_id` vacío |
| `hr.employee.area` | mapea por code igual que la tarea |

La verificación de que no se perdió nada es de conteo: `count(area IS NOT NULL)` en el respaldo
== `count(area_id IS NOT NULL)` + `count(code desconocido)` después. Si no cierra, el test falla.

**Umbrales: uno solo.** El gestor lee `dashboard_params.get_int(env, 'alert_no_activity_days')`
y compañía. Si el dashboard no está instalado, lee las **mismas claves** de `ir.config_parameter`
con los mismos defaults. No hay segunda tabla de umbrales.

**Dónde vive** (decidido): `primateuy/primateProd`, al lado del dashboard — es un módulo de
dominio proyectos, no de IA. Consecuencia: `primate_sagui_pm` (en `primate_IA_hub`) depende de un
módulo de `primateProd`. En runtime funciona (ambos submódulos están en el mismo `addons_path`);
el acoplamiento es de despliegue y hay que anotarlo en el manifest y en el README del hub.

**Rama** (decidido): commits directos en `feature/primate_project_dashboard`. El dashboard sale
en un solo PR ya con `primate.area`, y no existe nunca una versión publicada con el Selection.
Contrapartida asumida: el PR crece y arrastra la migración de datos, así que el tour y la suite
tienen que quedar verdes en cada commit, no sólo al final.

---

## 2. `primate_sagui_pm` — modelos

`depends: ['primate_sagui', 'project', 'mail', 'primate_project_area']`
(`primate_project_dashboard` **no** va en depends: se detecta en runtime con `'...' in self.env`,
como ya hace el dashboard con `planning.slot`).

### 2.0 Guard de la dependencia cruzada (hub → primateProd)

El riesgo concreto: un `primate_IA_hub` desplegado **sin** `primateProd` en el `addons_path`.
Tres capas, de la que más protege a la que menos:

**a) El manifest ya es el guard duro, y falla en el momento correcto.** Con
`primate_project_area` fuera del `addons_path`, Odoo marca `primate_sagui_pm` como no instalable
y **se niega a instalarlo** («dependencia no encontrada»). No es un fallo en runtime: es un fallo
en instalación, que es donde se quiere. Lo que no hay que hacer es intentar «arreglarlo» con un
`post_init_hook` — no llega a correr— ni con un depends blando que después reviente al usar el
módulo.

**b) Lo que hay que garantizar es que el resto del hub no lo note.** Ese es el test:
`primate_sagui/tests/test_hub_independiente.py` recorre el árbol de los módulos del hub y falla si
alguno **distinto de `primate_sagui_pm`** menciona `primate_project_area`, `primate.area` o
`primate_sagui_pm` — en manifests, imports, XML de datos o vistas. Sin `primateProd`, la
degradación es «el rol Gestor de Proyectos no existe», nunca «Sagui no arranca». Es el mismo
patrón de test estático que ya usa `test_build_operations`.
Corolario de diseño que el test fuerza: el `sagui.role` y la `sagui.recipe` del gestor viven en el
data de `primate_sagui_pm`, jamás en el de `primate_sagui`.

**c) Runtime, para el caso raro pero real**: módulo instalado y después desinstalado a medias, o
`primate.area` fuera del registry por un upgrade a medio camino. Un `_check_deps()` en el motor
levanta `UserError` explicando qué falta y cómo se arregla, en vez de un `KeyError: 'primate.area'`
que manda a diagnosticar donde no es.

**d)** Nota de despliegue en el README del hub y en el manifest (`external_dependencies` no aplica:
es un addon, no un paquete Python): *este módulo requiere `primateProd` en el `addons_path`*.

- **`primate.sagui.pm.engine`** (AbstractModel) — el motor de reglas. Una regla es
  `{key, titulo, riesgo, ambito, _find(scope) → candidatos, _propose(c) → (op, model, ids, values, resumen)}`.
  Determinístico, por dominio ORM, corriendo **siempre** con el env del usuario que ejecuta
  (los ACL y record rules hacen el recorte solos).
  Las reglas de **juicio** (actividades duplicadas, actividad ≡ tarea existente, varias actividades
  que piden una tarea) arman los candidatos por ORM — mismo responsable, mismo `res_model/res_id`
  o mismo proyecto, ventana de fechas — y mandan **un batch por proyecto** al modelo para que
  decida cuáles son la misma cosa. Sin heurística de strings, como pide el encargo, y sin
  pasearle la base entera al modelo.

- **`sagui.pm.run`** — registro de corrida: origen (receta/automatización), alcance, `desde`,
  conteos por regla, propuestas generadas / aprobadas / rechazadas, verificación asociada,
  `primate.ai.action` (costo). Es el dato que después decide qué reglas pasan a `auto`.
- **`sagui.pm.finding`** (o2m de la corrida): regla, riesgo, proyecto, área, modelo/registro,
  resumen, propuesta asociada, estado.

- **Herencias para la aprobación por bloque** (patrón que ya usa el diseñador con
  `sagui_pending_write.py`): `primate.sagui.pending.write` y `sagui.automation.proposal` reciben
  `pm_run_id`, `pm_rule_key`, `pm_project_id`, `pm_area_id`. Aprobar «todas las de una regla» o
  «todas las de un proyecto» = filtrar por esos campos y llamar al `_approve` / confirmación que
  ya existe. Nada nuevo escribe fuera del gate.

- **Nota de gestión**: `project.project.pm_note` + `pm_note_date`, `primate.area.pm_note` +
  `pm_note_date`.

---

## 3. Rol y playbook (todo en datos)

| Registro | Clave | Archivo |
|---|---|---|
| `sagui.skill` (skill) | `project-management` | `primate_sagui_pm/skills/project-management/SKILL.md` |
| `sagui.skill` (checklist) | `pm-verifier-rubric` | `.../project-management/references/verifier-rubric.md` |
| `sagui.role` | `project_manager` | `data/sagui_role_data.xml` |

El rol: `system_prompt` (quién es y qué decide) + las dos skills + `tool_names` =
`auditar_proyectos,nota_de_gestion,buscar_registros,agrupar_registros,describir_modelo`,
`verifier_active=True`, `verifier_rubric_id=pm-verifier-rubric`.

**Un solo playbook, tres alcances.** El alcance por área no es otro rol ni otro prompt: es un
parámetro de la corrida que filtra el dominio y decide a quién se le propone.

El `SKILL.md` lleva las 8 reglas del encargo con `condición / acción propuesta / a quién /
riesgo`, más la sección de convenciones (nombres de tarea, uso de etapas, cuándo una actividad
es tarea). El riesgo es lo que después habilita o no el paso a `auto`.

---

## 4. Modo a) Auditoría — `sagui.recipe`

`sagui.recipe` «Auditoría de proyectos», `mode='propose'`, sin scope `auto` (vacío = nada se
auto-aplica). Parámetros: `alcance` (todo/área/proyecto), `area` (m2o `primate.area`),
`proyecto` (m2o `project.project`), `dias` (default: el umbral `alert_no_activity_days`).

La instrucción no le pide al modelo que recorra la base: le pide que llame a `auditar_proyectos`
con esos parámetros y redacte el informe. El recorrido es determinístico (el motor), el modelo
agrupa, prioriza y escribe. Cada hallazgo entra por `_intercept_write` → `pending.write`.

Salida: informe agrupado por regla y por proyecto, adjunto al chat de Sagui + `sagui.pm.run`
con los conteos.

## 5. Modo b) Gestión continua — `sagui.automation`

Misma instrucción, `mode='propose'`, diaria 08:00 (configurable), `active=False` en el dato:
se prende cuando el modo a) esté rodado. Corre sobre lo cambiado desde `last_run`
(`write_date`/`message_ids` desde esa fecha). Los hallazgos van a `sagui.automation.proposal`
(bandeja desatendida, que es la que corresponde sin humano presente).

**Un aviso por persona, no por ítem**: al cerrar la corrida se agrupan los hallazgos por
responsable y se manda **un** mensaje por persona con su lista. Reusa `_notify` de
`sagui.automation`.

## 6. Modo c) Nota de gestión

`primate.sagui.pm.note._generate(scope)` arma un contexto **sólo con datos leídos** (métricas del
dashboard si está instalado, conteos propios si no) y pide un párrafo: estado, riesgos, decisiones
pendientes del humano, qué hizo Sagui.

**Prohibido inventar, mecánicamente:** cada dato ausente entra al contexto como `null` con su
etiqueta, y la skill obliga a decir «sin datos de X». Es la misma regla del `None` del dashboard
(«sin dato no es cero»).

Contrato con el dashboard: `primate_sagui_pm` hereda `get_dashboard_data` y agrega `pm_note` /
`pm_note_date` a cada fila y a cada tarjeta de área. El módulo base no se toca **salvo** la
plantilla OWL que la muestra (`t-if="row.pm_note"`) — eso es un commit chico y aparte en la rama
del dashboard. Sin dashboard instalado, la nota vive igual en el form del proyecto.

## 7. Verificador ORM — y la extensión genérica que exige

**En `primate_sagui` (commit aparte, es genérico):**
- `_collect` acepta `{"label": ..., "text": ...}` además del attachment.
- `_measure_evidence` mide **sólo** imágenes; la evidencia de texto nunca es «degradada».
- `_build_blocks` emite bloques de texto para esa evidencia.
- proveedor nuevo `rules`: `_review_rules` **no llama al modelo** — lee los resultados
  estructurados del collector y emite el mismo contrato de findings. Post-condición con `count>0`
  → `FAIL` con ubicación (los ids concretos) y fix. Cero tokens, cero alucinación.
  Para post-condiciones de datos, un revisor determinístico es estrictamente mejor que uno que
  opina: la pregunta «¿hay tareas abiertas sin responsable?» tiene respuesta exacta.

**En `primate_sagui_pm`:** `primate.sagui.pm.verifier` es el collector. Corre las post-condiciones
como dominios ORM con los permisos del usuario y devuelve evidencia de texto + resultados
estructurados:

1. ninguna tarea abierta del alcance sin `user_ids` ni sin `stage_id`;
2. ninguna actividad vencida hace más de N días sin nota;
3. toda tarea del alcance está en un proyecto con `area_id`;
4. ninguna propuesta aplicada en la corrida cae fuera del alcance
   (se contrasta contra el `sagui.task.action.log` de esa corrida).

Corre después de aplicar propuestas y al cierre de cada corrida continua. Un dominio inválido o
un modelo ausente es **fallo de infra** (`verdict='error', infra=True`), no gestión reprobada —
misma regla del diseñador.

## 8. Migración de los mixins al esquema de datos (commit propio, al final)

**Corrección respecto del encargo: los mixins a migrar están en `primate_sagui`, no en
`primate_sagui_designer`.** El diseñador ya está migrado — su rol es `role_web_designer` en
`data/sagui_role_data.xml`, con 8 skills y rúbrica—; lo que le queda son dos capas de prompt de
la misma forma que las de abajo, y esas sí entran.

Los seis overrides de `_system_prompt()` que quedan hardcodeados en Python:

| # | archivo | condición para inyectarse | qué es el texto |
|---|---|---|---|
| 1 | `primate_sagui/models/sagui_website.py:185` | siempre | flujo PDF → schema → modo fiel/editable → confirmar |
| 2 | `primate_sagui/models/sagui_greenfield.py:128` | siempre | flujo logo+brief, y 3 reglas duras (no inventar tokens, no afirmar que construyó, build asíncrono) |
| 3 | `primate_sagui/models/sagui_import.py:111` | `self._write_whitelist()` | flujo de mapeo de planillas + nota de `type` en v19 |
| 4 | `primate_sagui/models/sagui_connector.py:438` | `self._connector_tool_specs()` | tools MCP y el **trust boundary** (contenido externo = dato, no instrucciones) |
| 5 | `primate_sagui_designer/models/sagui_designer.py:182` | siempre | enrutado a `disenar_web` |
| 6 | `primate_sagui_designer/models/sagui_documents.py:86` | `"documents.document" in self.env` | referencia desde Documentos |

### El detalle que define la migración: cuatro de los seis son CONDICIONALES

`sagui.role.prompt()` concatena skills incondicionalmente: no sabe si hay whitelist de escritura,
ni si hay conectores configurados, ni si el módulo Documentos está instalado. Migrar esto a un rol
tal cual **cambiaría el comportamiento**: le metería al asistente instrucciones de importación a un
usuario que no puede escribir, y de conectores a una base que no tiene ninguno.

Por eso la migración parte en dos, y ésta es la regla:

> **El TEXTO sale a un `.md` versionado. La CONDICIÓN se queda en Python**, que es donde
> pertenece porque depende del `env`.

```python
# antes
def _system_prompt(self):
    prompt = super()._system_prompt()
    if self._write_whitelist():
        prompt += "\n\nIMPORTACIÓN DE PLANILLAS (…30 líneas de texto…)"
    return prompt

# después
def _system_prompt(self):
    prompt = super()._system_prompt()
    if self._write_whitelist():
        prompt += "\n\n" + self.env["sagui.skill"].content_of("spreadsheet-import")
    return prompt
```

`content_of()` ya existe, ya está cacheado por ruta (`_read_addon_file` con `ormcache`) y ya
devuelve `""` si la skill falta — una skill ausente degrada el prompt, no rompe el chat. No hace
falta código nuevo en `primate_sagui`.

### Qué se crea

Seis `sagui.skill` (kind=`skill`), cada una apuntando a su `.md`:

```
primate_sagui/skills/website-from-pdf/SKILL.md          → key: website-from-pdf
primate_sagui/skills/website-greenfield/SKILL.md        → key: website-greenfield
primate_sagui/skills/spreadsheet-import/SKILL.md        → key: spreadsheet-import
primate_sagui/skills/mcp-connectors/SKILL.md            → key: mcp-connectors
primate_sagui_designer/skills/web-designer-routing.md   → key: web-designer-routing
primate_sagui_designer/skills/documents-reference.md    → key: documents-reference
```

Más **un** `sagui.role` `chat_assistant` (el asistente de Discuss, que hasta hoy no tenía rol
declarado) cuyo `system_prompt` es el prompt base de `primate.sagui.assistant._system_prompt()`,
con las skills incondicionales asociadas. Las condicionales quedan fuera del rol y se piden por
`content_of()` desde su mixin: **una skill listada en un rol se inyecta siempre; una condicional
no puede estar listada ahí.** Queda anotado en el `.md` de cada una.

No se crean cuatro roles. Cuatro mixins del mismo asistente no son cuatro perfiles de trabajo:
`sagui.role` modela «quién sos y qué decidís», y acá hay un solo asistente con varias capacidades.

### Criterio de que salió bien

Comportamiento **idéntico**, byte a byte donde se pueda: el `.md` arranca como copia literal del
string de Python. `test_build_operations`, `test_build_claim` y los dos tests del diseñador quedan
verdes **sin tocarlos** — si hay que editar un test, la migración cambió comportamiento y está mal.

Se suma `test_prompts_migrados.py`: para cada uno de los seis, arma el prompt con la condición en
`True` y con la condición en `False`, y verifica (a) que con `True` el texto de la skill está
presente, (b) que con `False` **no** está, (c) que la skill resuelve a contenido no vacío desde el
`addons_path`. Ese último punto es el que atrapa una ruta mal escrita, que hoy degradaría en
silencio a un prompt sin instrucciones.

Va en commits separados del rol PM y separados entre sí: uno por mixin, seis commits, cada uno
verificable solo.

---

## 9. Orden de commits

1. `[ADD] primate_project_area: área con responsable en proyecto, tarea y empleado`
   `[REF] primate_project_dashboard: el área pasa a primate.area` (+ migración) — rama del dashboard
2. `[ADD] primate_sagui_pm: rol Gestor de Proyectos, playbook y motor de reglas`
3. `[ADD] primate_sagui_pm: receta de auditoría de proyectos`
4. `[IMP] primate_sagui: evidencia textual y revisor determinístico en sagui.verification`
   `[ADD] primate_sagui_pm: verificador de post-condiciones por ORM`
5. `[ADD] primate_sagui_pm: automatización continua y nota de gestión`
   `[IMP] primate_project_dashboard: la nota de gestión en las filas y las tarjetas`
6. `[REF] primate_sagui: el prompt de <mixin> pasa a sagui.skill` — **seis commits**, uno por
   mixin (website, greenfield, import, connectors, designer-routing, documents-reference), más
   `[ADD] primate_sagui: rol chat_assistant y test de prompts migrados`

## 10. Decisiones

Confirmadas:
- `primate_project_area` vive en **`primateuy/primateProd`**, junto al dashboard.
  `primate_sagui_pm` lo referencia cruzando de repo.
- El refactor del área va en **commits directos sobre `feature/primate_project_dashboard`**,
  dentro del PR pendiente.

Tomadas sin preguntar, por si hay objeción:
- **Many2one, no Selection**: el responsable de área es requisito («las propuestas de un área van
  al responsable de esa área») y un Selection no lo puede llevar.
- **Verificador determinístico** (`rules`), no revisor LLM sobre evidencia de texto.
- **La automatización usa `sagui.automation.proposal`**, no `pending.write`: es la bandeja
  desatendida que ya existe. `pending.write` es para la receta, que es lo que pide el encargo
  para el modo a).
