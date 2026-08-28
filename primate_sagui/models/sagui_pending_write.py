# -*- coding: utf-8 -*-
# Operación de escritura PROPUESTA por Sagui, a la espera de confirmación del usuario
# en el chat. La escritura real nunca la decide la IA: queda acá en estado 'pending'
# hasta que el usuario responde "confirmar <token>", lo que dispara la ejecución
# determinística (con los permisos del usuario) desde primate.sagui.assistant.
from odoo import api, fields, models


class SaguiPendingWrite(models.Model):
    _name = "primate.sagui.pending.write"
    _description = "Escritura propuesta por Sagui pendiente de confirmación"
    _order = "create_date desc"

    token = fields.Char(string="Token", required=True, index=True, copy=False)
    channel_id = fields.Many2one(
        "discuss.channel", string="Canal", required=True, index=True, ondelete="cascade"
    )
    user_id = fields.Many2one(
        "res.users", string="Usuario", required=True, index=True, ondelete="cascade"
    )
    operation = fields.Selection(
        [("create", "Crear"), ("write", "Modificar"), ("unlink", "Eliminar"), ("import", "Importar"),
         ("external", "Acción externa (conector)"),
         ("website", "Generar sitio"), ("website_greenfield", "Generar sitio (marca)")],
        string="Operación", required=True,
    )
    # Si la propuesta viene de una RECETA (confirmación interactiva), se ejecuta vía
    # sagui.recipe._apply_write (core compartido), no por el flujo de chat _execute_pending.
    recipe_id = fields.Many2one("sagui.recipe", string="Receta", ondelete="cascade", index=True)
    model_name = fields.Char(string="Modelo", required=True)
    res_ids = fields.Char(string="IDs destino (JSON)")  # solo para 'write'
    # Para 'create'/'write': mapa campo->valor. Para 'import': {attachment_id, mapping, options}.
    values_json = fields.Text(string="Valores (JSON)", required=True)
    summary = fields.Text(string="Resumen")
    state = fields.Selection(
        [
            ("pending", "Pendiente"),
            ("processing", "Construyendo"),  # build diferido a cron (sitios web): evita bloquear el chat
            ("done", "Aplicada"),
            ("cancelled", "Cancelada"),
            ("error", "Error"),
            ("expired", "Expirada"),
        ],
        string="Estado", default="pending", required=True, index=True,
    )
    result_info = fields.Char(string="Resultado")
    # Intentos de build (sitios): el cron lo incrementa y commitea ANTES de construir, así un kill
    # por timeout no se pierde y no se re-genera infinitamente (cap de reintentos = tope de tokens).
    build_attempts = fields.Integer(string="Intentos de build", default=0, copy=False)
    # CUÁNDO SE EMPEZÓ A CONSTRUIR ESTE, que no es lo mismo que write_date -cualquier escritura
    # lo mueve-. Con el cron barriendo cada 10 minutos y builds que tardan varios, sin esto un
    # build sano en curso se ve igual que uno muerto y la barrida siguiente lo retomaría,
    # duplicando el trabajo y quemando tokens de nuevo.
    build_started_at = fields.Datetime(string="Build iniciado", copy=False)

    _token_uniq = models.Constraint(
        "UNIQUE (token)", "El token de la operación debe ser único.")
