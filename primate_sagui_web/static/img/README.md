# Assets de marca de Sagui

Dejá acá los archivos exportados del brand board (el JPEG del board NO sirve como asset).
Esta carpeta NO se bundlea (está fuera de `static/src/`); se sirve por URL
`/primate_sagui_web/static/img/<archivo>`.

## Archivos esperados

| Archivo                 | Uso                                              | Cómo activarlo |
|-------------------------|--------------------------------------------------|----------------|
| `sagui_logo.svg`        | Logo en el header del shell y del panel del chat | En `app/sagui_app.xml` y `chat/sagui_chat.xml`, reemplazar el `<span class="o_sagui_wordmark">` por `<img class="o_sagui_brandimg" src="/primate_sagui_web/static/img/sagui_logo.svg" alt="Sagui AI"/>` |
| `sagui_icon.png`        | Ícono de la app (menú de aplicaciones)           | Reemplazar `static/description/icon.png` por el ícono nuevo (el `web_icon` del menú ya apunta ahí) |
| `sagui_favicon.png`     | Favicon del backend                              | Se carga vía `res.company.favicon` (data XML); ver `data/sagui_branding.xml` cuando se agregue el archivo |

Mientras tanto, el header usa el **wordmark textual** "Sagui AI" (queda on-brand). El favicon
queda con el default de Odoo hasta que se cargue `sagui_favicon.png`.
