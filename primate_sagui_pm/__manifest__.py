# -*- coding: utf-8 -*-
{
    'name': 'Primate Sagui — Gestor de Proyectos',
    'version': '19.0.1.0.0',
    'category': 'Productivity/AI',
    'summary': 'Rol de gestor de proyectos para Sagui: auditoría de higiene de proyectos, '
               'tareas y actividades, todo en modo propuesta',
    'description': """
Rol "Gestor de Proyectos" para Sagui
====================================

Mantiene los datos de proyectos en un estado en el que el dashboard ejecutivo dice la verdad, y
le suma la capa que un dashboard no da: el juicio.

Recorre proyectos, tareas y actividades con un playbook versionado (`skills/project-management/
SKILL.md`) y produce hallazgos de tres tipos, que NO son lo mismo:

* **propuesta** — hay un valor concreto y aplicable. Va al gate de escritura de Sagui y espera
  aprobación humana. Nunca se aplica sola.
* **pregunta** — hay un problema pero ningún valor deducible. Se reporta y no se escribe nada:
  inventar un valor para que parezca una propuesta es la falla que mata la credibilidad de la
  bandeja.
* **nota** — un hecho de gestión, no de higiene (el desvío contra el plan, por ejemplo).

Todo arranca en modo `propose`. Qué reglas pueden pasar a automático se decide después, con la
tasa de propuestas aprobadas sin cambios que registra `sagui.pm.run`, no por intuición.

REQUIERE `primate_project_area`, que vive en el repo `primateProd`. Es la dependencia cruzada
entre los dos repos: sin `primateProd` en el addons_path, Odoo no deja instalar ESTE módulo y
el resto del ecosistema Sagui sigue funcionando igual.
""",
    'author': 'PrimateUY',
    'website': 'https://primate.uy',
    'license': 'LGPL-3',
    'depends': [
        'primate_sagui',
        'project',
        'mail',
        # Vive en primateProd, NO en este repo. Ver la nota del README.
        'primate_project_area',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/sagui_skill_data.xml',
        'data/sagui_role_data.xml',
        'data/sagui_recipe_data.xml',
        'views/sagui_pm_run_views.xml',
    ],
    'installable': True,
    'application': False,
}
