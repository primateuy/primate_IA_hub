# -*- coding: utf-8 -*-
# Una ACCIÓN agrupa N llamadas a la API (usage logs) bajo un mismo contexto de negocio
# (una conversación, la generación de un sitio, una importación...). Habilita el "costo por
# acción" y el drill-down a sus operaciones. El contexto se propaga por env (ai_action_id) y
# el conector lo estampa en cada usage log (ver _log_usage).
from odoo import api, fields, models, _
from odoo.exceptions import AccessError

from .ai_usage_log import RATES, DEFAULT_RATE

# Parámetro global del presupuesto mensual de IA (USD). Lo define un manager.
BUDGET_PARAM = "primate_sagui.ai_monthly_budget"

# Tipos de acción conocidos del ecosistema (extensible con selection_add desde otros módulos).
ACTION_TYPES = [
    ("conversacion", "Conversación"),
    ("generar_sitio", "Generar sitio"),
    ("analizar_sitio", "Analizar sitio"),
    ("importar_productos", "Importar productos"),
    ("automatizacion", "Automatización"),
    ("receta", "Receta"),
    ("disenar", "Diseñar"),
    ("otro", "Otro"),
]


class PrimateAIAction(models.Model):
    _name = "primate.ai.action"
    _description = "Acción de IA (agrupa N llamadas para costo y drill-down)"
    _order = "create_date desc"

    name = fields.Char(string="Etiqueta", required=True, default="Acción")
    action_type = fields.Selection(
        ACTION_TYPES, string="Tipo", required=True, default="otro", index=True)
    # Referencia opcional al objeto de negocio, formato "model,id" o un id suelto.
    ref = fields.Char(string="Referencia", copy=False)
    user_id = fields.Many2one(
        "res.users", string="Usuario", required=True, index=True,
        default=lambda self: self.env.uid, ondelete="cascade")
    usage_log_ids = fields.One2many("primate.ai.usage.log", "action_id", string="Operaciones")

    # Totales agregados (stored) para listar/ordenar por costo sin recomputar.
    call_count = fields.Integer(string="Llamadas", compute="_compute_totals", store=True)
    input_tokens = fields.Integer(string="Tokens entrada", compute="_compute_totals", store=True)
    output_tokens = fields.Integer(string="Tokens salida", compute="_compute_totals", store=True)
    cache_read_tokens = fields.Integer(
        string="Tokens lectura caché", compute="_compute_totals", store=True)
    cache_write_tokens = fields.Integer(
        string="Tokens escritura caché", compute="_compute_totals", store=True)
    total_cost = fields.Float(
        string="Costo (USD)", compute="_compute_totals", store=True, digits=(12, 5))

    @api.depends(
        "usage_log_ids", "usage_log_ids.cost", "usage_log_ids.input_tokens",
        "usage_log_ids.output_tokens", "usage_log_ids.cache_read_tokens",
        "usage_log_ids.cache_write_tokens")
    def _compute_totals(self):
        for action in self:
            logs = action.usage_log_ids
            action.call_count = len(logs)
            action.input_tokens = sum(logs.mapped("input_tokens"))
            action.output_tokens = sum(logs.mapped("output_tokens"))
            action.cache_read_tokens = sum(logs.mapped("cache_read_tokens"))
            action.cache_write_tokens = sum(logs.mapped("cache_write_tokens"))
            action.total_cost = sum(logs.mapped("cost"))

    @api.model
    def _open(self, action_type, label, ref=False):
        """Crea (sudo: es infraestructura) y devuelve una acción del usuario actual.
        Propagá su id por contexto: env(context={'ai_action_id': action.id}) antes de
        llamar al conector, así cada usage log queda etiquetado con esta acción."""
        types = dict(ACTION_TYPES)
        name = (label or "").strip()[:120] or types.get(action_type, action_type)
        return self.sudo().create({
            "action_type": action_type if action_type in types else "otro",
            "name": name,
            "ref": ref or False,
            "user_id": self.env.uid,
        })

    # ---------------------------------------------------------------- dashboard
    @api.model
    def _date_domain(self, date_from=None, date_to=None):
        domain = []
        if date_from:
            domain.append(("create_date", ">=", date_from))
        if date_to:
            domain.append(("create_date", "<=", date_to))
        return domain

    @api.model
    def dashboard_data(self, date_from=None, date_to=None):
        """Agregaciones server-side (read_group, NO filas crudas) para el dashboard de
        Historial. Las record rules filtran: cada usuario ve SU consumo; el manager, todos."""
        Log = self.env["primate.ai.usage.log"]
        ldomain = self._date_domain(date_from, date_to)
        adomain = self._date_domain(date_from, date_to)

        # --- costo por ACCIÓN (top 25 por costo) ---
        actions = self.search(adomain, order="total_cost desc, id desc", limit=25)
        types = dict(ACTION_TYPES)
        by_action = [{
            "id": a.id,
            "label": a.name,
            "action_type": a.action_type,
            "action_type_label": types.get(a.action_type, a.action_type),
            "cost": a.total_cost,
            "calls": a.call_count,
            "user": a.user_id.name or "—",
            "input_tokens": a.input_tokens,
            "output_tokens": a.output_tokens,
        } for a in actions]

        # --- por USUARIO ---
        by_user = []
        for user, cost in Log._read_group(ldomain, ["user_id"], ["cost:sum"]):
            by_user.append({
                "user_id": user.id if user else 0,
                "user": user.name if user else "—",
                "cost": cost or 0.0,
            })
        by_user.sort(key=lambda r: r["cost"], reverse=True)

        # --- por MODELO ---
        by_model = []
        for model, cost, it, ot in Log._read_group(
                ldomain, ["model_name"],
                ["cost:sum", "input_tokens:sum", "output_tokens:sum"]):
            by_model.append({
                "model": model or "—",
                "cost": cost or 0.0,
                "input_tokens": it or 0,
                "output_tokens": ot or 0,
            })
        by_model.sort(key=lambda r: r["cost"], reverse=True)

        # --- en el TIEMPO (serie por día) ---
        by_day = []
        for day, cost in Log._read_group(
                ldomain, ["create_date:day"], ["cost:sum"], order="create_date:day"):
            if not day:
                continue
            by_day.append({
                "day": day.strftime("%Y-%m-%d"),
                "cost": cost or 0.0,
            })

        # --- AHORRO por caché: lo que las lecturas de caché habrían costado a precio
        #     de input lleno, menos lo que costaron (~10%). Por modelo (tarifas distintas). ---
        cache_saving = 0.0
        cache_read_cost = 0.0
        would_have_cost = 0.0
        for model, cr in Log._read_group(ldomain, ["model_name"], ["cache_read_tokens:sum"]):
            pin, _pout, _pcw, pcr = RATES.get(model, DEFAULT_RATE)
            cr = cr or 0
            full = (cr / 1e6) * pin
            cached = (cr / 1e6) * pcr
            would_have_cost += full
            cache_read_cost += cached
            cache_saving += (full - cached)

        # --- totales (KPIs) ---
        total_cost = sum(r["cost"] for r in by_model)
        total_in = sum(r["input_tokens"] for r in by_model)
        total_out = sum(r["output_tokens"] for r in by_model)

        # --- período PREVIO (para ▲/▼): ventana simétrica anterior a date_from ---
        prev = None
        if date_from:
            df = fields.Datetime.to_datetime(date_from)
            now = fields.Datetime.now()
            if df and now > df:
                window = now - df
                pdom = [("create_date", ">=", fields.Datetime.to_string(df - window)),
                        ("create_date", "<", fields.Datetime.to_string(df))]
                res = Log._read_group(pdom, [], ["cost:sum", "input_tokens:sum", "output_tokens:sum"])
                pc, pi, po = (res[0] if res else (0.0, 0, 0))
                prev = {"cost": pc or 0.0, "input_tokens": pi or 0, "output_tokens": po or 0}

        # --- presupuesto: límite mensual (global) vs gasto del mes en curso ---
        icp = self.env["ir.config_parameter"].sudo()
        try:
            budget_limit = float(icp.get_param(BUDGET_PARAM, 0) or 0)
        except (ValueError, TypeError):
            budget_limit = 0.0
        month_start = fields.Datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        mres = Log._read_group(
            [("create_date", ">=", fields.Datetime.to_string(month_start))], [], ["cost:sum"])
        spent_month = (mres[0][0] if mres else 0.0) or 0.0

        is_manager = (
            self.env.user.has_group("primate_ai_connector.group_ai_manager")
            or self.env.user.has_group("base.group_system"))

        return {
            "by_action": by_action,
            "by_user": by_user,
            "by_model": by_model,
            "by_day": by_day,
            "cache": {
                "saving": cache_saving,
                "cache_read_cost": cache_read_cost,
                "would_have_cost": would_have_cost,
            },
            "totals": {
                "cost": total_cost,
                "input_tokens": total_in,
                "output_tokens": total_out,
                "actions": len(by_action),
            },
            "prev": prev,
            "budget": {"limit": budget_limit, "spent": spent_month},
            "is_manager": is_manager,
            "currency": "USD",
        }

    @api.model
    def set_monthly_budget(self, value):
        """Define el presupuesto mensual de IA (USD). Solo manager/admin."""
        if not (self.env.user.has_group("primate_ai_connector.group_ai_manager")
                or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Solo un manager puede definir el presupuesto."))
        try:
            v = max(0.0, float(value or 0))
        except (ValueError, TypeError):
            v = 0.0
        self.env["ir.config_parameter"].sudo().set_param(BUDGET_PARAM, v)
        return {"ok": True, "limit": v}

    @api.model
    def action_operations(self, action_id):
        """Drill-down: dado action_id, devolvé sus operaciones (cada llamada con costo,
        tokens y modelo). La record rule asegura que solo accedas a acciones permitidas."""
        action = self.search([("id", "=", int(action_id))], limit=1)
        if not action:
            return {"ok": False, "error": _("Acción no encontrada o sin acceso.")}
        types = dict(ACTION_TYPES)
        ops = [{
            "id": log.id,
            "model": log.model_name or "—",
            "input_tokens": log.input_tokens,
            "output_tokens": log.output_tokens,
            "cache_read_tokens": log.cache_read_tokens,
            "cache_write_tokens": log.cache_write_tokens,
            "cost": log.cost,
            "create_date": fields.Datetime.to_string(log.create_date),
        } for log in action.usage_log_ids.sorted("id")]
        return {
            "ok": True,
            "id": action.id,
            "label": action.name,
            "action_type": action.action_type,
            "action_type_label": types.get(action.action_type, action.action_type),
            "user": action.user_id.name or "—",
            "cost": action.total_cost,
            "calls": action.call_count,
            "ops": ops,
        }
