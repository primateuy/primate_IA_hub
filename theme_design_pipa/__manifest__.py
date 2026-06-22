# -*- coding: utf-8 -*-
{
    'name': 'Theme Design Pipa',
    'description': 'Tema de marca generado desde el diseño (Design Pipa).',
    'category': 'Theme',
    'version': '19.0.1.0.0',
    'author': 'PrimateUY',
    'license': 'LGPL-3',
    'depends': ['website'],
    # Las variables primarias (paleta + fuentes) se registran vía data/ir_asset.xml con
    # directive="append" para cargar DESPUÉS de website (donde se definen $o-color-palettes,
    # o-make-palette, etc.). Registrarlas con prepend desde el manifest rompe la compilación.
    'data': [
        'data/ir_asset.xml',
    ],
    'assets': {
        # Estilo on-brand (header/footer/botones) en el frontend.
        'web.assets_frontend': [
            'theme_design_pipa/static/src/scss/theme.scss',
        ],
    },
    'installable': True,
}
