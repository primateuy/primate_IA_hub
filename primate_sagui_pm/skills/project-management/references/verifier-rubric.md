# Rúbrica del verificador de gestión

Post-condiciones de DATOS sobre el alcance de la corrida. Cada ítem es una consulta con respuesta
exacta: se cumple o no se cumple. No hay criterio, no hay interpretación y no hay opinión — por eso
la revisa el proveedor `rules` y no un modelo de lenguaje.

Se corre **después de aplicar propuestas** y al cierre de cada corrida continua. Verifica el
ESTADO en que quedó la base, no lo que hizo Sagui: una tarea sin responsable creada a mano dos
minutos después de la corrida también reprueba, y tiene que reprobar.

| id | Post-condición | Qué la viola |
|---|---|---|
| **C1** | Ninguna tarea abierta del alcance sin responsable | `user_ids` vacío |
| **C2** | Ninguna tarea abierta del alcance sin etapa | `stage_id` vacío |
| **C3** | Toda tarea abierta del alcance está en un proyecto con área | el proyecto de la tarea sin `area_id` |
| **C4** | Ninguna actividad del alcance vencida hace más de N días sin explicación | `date_deadline` vencido más de N y `note` vacía |
| **C5** | Ninguna propuesta aplicada de la corrida cayó fuera del alcance | una propuesta en estado aplicado cuyo proyecto no está en el alcance |

N es el mismo umbral del dashboard (`alert_no_activity_days`), igual que en el playbook.

**C4 admite explicación.** Una actividad vencida con una nota que dice por qué no es un problema
de higiene: alguien decidió y lo dejó escrito. Lo que reprueba es el silencio.

**C5 es la que se mira primero cuando algo huele mal.** Si falla, no es un dato sucio: es que el
alcance no se respetó, que es la promesa más fuerte del rol.

## Qué NO es un fallo de gestión

Si una consulta no se puede correr —un dominio inválido, un modelo que no está instalado— eso es
un fallo de **recolección**, no de gestión. El verificador corta con veredicto de infra y lo dice
así. Reportarlo como "gestión reprobada" mandaría a arreglar datos que están bien.
