# -*- coding: utf-8 -*-
{
    'name': 'Primate Sagui — Diseñador Web Odoo',
    'version': '19.0.1.1.0',
    'category': 'Productivity/AI',
    'summary': 'Rol de diseñador web para Sagui: plan de diseño, generación por sección y '
               'verificación visual con revisión independiente',
    'description': """
Rol "Diseñador Web Odoo" para Sagui
===================================

Enruta por presencia de referencia (PDF/mockup → reproducción; sin referencia → diseño desde
cero con plan y autocrítica; referencia parcial → diseño propio con el material real fijo),
presenta una propuesta al humano antes de construir, genera el sitio sección por sección y lo
verifica contra una rúbrica con capturas reales y un revisor independiente.

Incluye la referencia de diseño desde el módulo Documentos y el verificador visual, que es el
primer caso concreto del Check/Verify genérico (sagui.verification).
""",
    'author': 'PrimateUY',
    'website': 'https://primate.uy',
    'license': 'LGPL-3',
    'depends': ['primate_sagui', 'primate_website_generator', 'website'],
    # playwright: capturas de la página real para el verificador visual (corre por subprocess).
    'external_dependencies': {'python': ['playwright']},
    'data': [
        'security/ir.model.access.csv',
        'data/sagui_skill_data.xml',
        'data/sagui_role_data.xml',
        'views/sagui_design_run_views.xml',
        'views/sagui_verification_views.xml',
    ],
    'installable': True,
    'application': False,
}
