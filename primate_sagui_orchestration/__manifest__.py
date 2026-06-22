# -*- coding: utf-8 -*-
{
    'name': 'Primate Sagui — Orquestación Claude Code',
    'version': '19.0.1.0.0',
    'category': 'Productivity/AI',
    'summary': 'Puente Sagui ↔ Claude Code: jobs, captura de actividades → .md → fix, y endpoints '
               'que consume el runner local.',
    'description': "Orquestación Sagui <-> Claude Code (lado Odoo): jobs + endpoints que consume el "
                   "runner local, mapeo cliente->repo, acción 'Traer pendientes de hoy' (captura de "
                   "actividades -> .md -> job fix) y el paso VERIFY (tests_status). El runner local "
                   "(Python standalone, corre en la Mac) vive en la carpeta runner/. Solo se agrega "
                   "al instalar este módulo. Depende de primate_sagui.",
    'author': 'PrimateUY',
    'website': 'https://primate.uy',
    'license': 'LGPL-3',
    # Depende de primate_sagui: usa sagui.task.action.log (audit), el conector GitHub
    # (sagui.connector / primate.sagui.mcp) y mail.activity.
    'depends': ['primate_sagui'],
    'data': [
        'security/ir.model.access.csv',
        'data/sagui_orchestration_data.xml',
        'views/sagui_orchestration_views.xml',
        'views/sagui_activity_capture_views.xml',
    ],
    'installable': True,
    'application': True,
}
