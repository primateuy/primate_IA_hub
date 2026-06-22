# -*- coding: utf-8 -*-
{
    'name': 'Primate AI Connector',
    'version': '19.0.1.0.0',
    'category': 'Productivity/AI',
    'summary': 'Conector base con la API de Claude (Anthropic) para el ecosistema de IA de PrimateUY',
    'author': 'PrimateUY',
    'website': 'https://primate.uy',
    'license': 'LGPL-3',
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
        'security/ai_security.xml',
        'views/res_config_settings_views.xml',
        'views/ai_usage_log_views.xml',
    ],
    'installable': True,
    'application': False,
}
