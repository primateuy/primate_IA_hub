# -*- coding: utf-8 -*-
{
    'name': 'Primate Sagui',
    'version': '19.0.1.0.0',
    'category': 'Productivity/AI',
    'summary': 'Claude conversacional dentro de Odoo: chateá con Sagui para consultar y analizar datos',
    'author': 'PrimateUY',
    'website': 'https://primate.uy',
    'license': 'LGPL-3',
    'depends': ['primate_ai_connector', 'mail', 'base_import', 'primate_website_generator'],
    # fitz: PyMuPDF (PDF). mcp: cliente MCP para el framework de conectores.
    'external_dependencies': {'python': ['fitz', 'mcp']},
    'data': [
        'security/ir.model.access.csv',
        'security/sagui_conversation_rules.xml',
        'security/sagui_connector_rules.xml',
        'security/sagui_automation_rules.xml',
        'data/sagui_user.xml',
        'data/sagui_cron.xml',
        'data/sagui_recipe_data.xml',
        'views/res_config_settings_views.xml',
        'views/sagui_pending_write_views.xml',
        'views/sagui_connector_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'primate_sagui/static/src/js/sagui_consult_button.js',
            'primate_sagui/static/src/xml/sagui_consult_button.xml',
        ],
    },
    'installable': True,
    'application': True,
}
