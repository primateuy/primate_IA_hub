# -*- coding: utf-8 -*-
{
    'name': 'Primate Sagui Web',
    'version': '19.0.1.0.0',
    'category': 'Productivity/AI',
    'summary': 'Interfaz Owl para Sagui: panel en el systray + app de página completa, con streaming',
    'author': 'PrimateUY',
    'website': 'https://primate.uy',
    'license': 'LGPL-3',
    'depends': ['primate_sagui', 'web'],
    'data': [
        'data/sagui_app_action.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'primate_sagui_web/static/src/**/*',
        ],
    },
    'installable': True,
    'application': False,
}
