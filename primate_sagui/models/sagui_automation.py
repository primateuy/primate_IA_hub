# -*- coding: utf-8 -*-
# AUTOMATIZACIONES de Sagui = tarea ejecutable (sagui.executable.task) + SCHEDULE + entrega
# desatendida. Reusa TODO el core de FASE 2 (interceptación/scope/auditoría/dry-run) del mixin.
# Lo propio de acá: agenda (cron), modos de entrega, aprobador, bandeja (proposal), kill switch.
#
# REGLA DURA: corre y notifica como el DUEÑO; escrituras fuera de scope -> BANDEJA (sin humano).
import json
import logging
from datetime import datetime, time, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, AccessError

from .sagui_executable_task import NOTHING

_logger = logging.getLogger(__name__)

PROPOSAL_TTL_DAYS = 7
KILL_PARAM = "primate_sagui.automations_paused"   # kill switch GLOBAL


class SaguiAutomation(models.Model):
    _name = "sagui.automation"
    _inherit = ["sagui.executable.task"]
    _description = "Automatización de Sagui (digest/alerta y acciones programadas)"
    _order = "name"

    name = fields.Char(string="Nombre", required=True)
    user_id = fields.Many2one(
        "res.users", string="Dueño", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.uid)
    active = fields.Boolean(string="Activa", default=True)

    # ---- agenda ----
    schedule_type = fields.Selection(
        [("hourly", "Cada hora"), ("daily", "Diaria"), ("weekly", "Semanal")],
        string="Frecuencia", required=True, default="daily")
    time_of_day = fields.Float(string="Hora", default=8.0, help="Hora del día (8.5 = 08:30).")
    weekday = fields.Selection(
        [("0", "Lunes"), ("1", "Martes"), ("2", "Miércoles"), ("3", "Jueves"),
         ("4", "Viernes"), ("5", "Sábado"), ("6", "Domingo")], string="Día", default="0")

    # ---- entrega ----
    delivery_discuss = fields.Boolean(string="Avisar por Discuss", default=True)
    delivery_inapp = fields.Boolean(string="Avisar in-app", default=True)
    approver_id = fields.Many2one("res.users", string="Aprobador", help="Quién aprueba la bandeja (default: el dueño).")

    # ---- estado ----
    last_run = fields.Datetime(string="Último run", readonly=True, copy=False)
    next_run = fields.Datetime(string="Próximo run", readonly=True, copy=False, index=True)
    last_result = fields.Text(string="Último resultado", readonly=True, copy=False)
    last_status = fields.Selection(
        [("ok", "Reportó"), ("nothing", "Sin novedad"), ("error", "Error"), ("paused", "Pausada")],
        string="Último estado", readonly=True, copy=False)
    has_unread = fields.Boolean(string="Resultado sin leer", default=False, copy=False)
    run_ids = fields.One2many("sagui.automation.run", "automation_id", string="Ejecuciones")
    proposal_ids = fields.One2many("sagui.automation.proposal", "automation_id", string="Propuestas")
    run_count = fields.Integer(string="# Ejecuciones", compute="_compute_counts")
    pending_count = fields.Integer(string="# Pendientes", compute="_compute_counts")

    def _compute_counts(self):
        for a in self:
            a.run_count = len(a.run_ids)
            a.pending_count = len(a.proposal_ids.filtered(lambda p: p.state == "pending"))

    # ---------------- hooks del core ----------------
    def _is_unattended(self):
        return True   # corre sin humano → no expone ni ejecuta escrituras externas de conectores

    def _task_action_type(self):
        return "automatizacion"

    def _audit_extra(self, ctx, proposal):
        run = ctx.get("run")
        return {"automation_id": self.id, "run_id": run.id if run else False,
                "proposal_id": proposal.id if proposal else False}

    def _route_to_review(self, env, op, model, ids, values, summary, count, reason, ctx):
        prop = self._make_proposal(env, op, model, ids, values, summary, count, reason, ctx.get("run"))
        ctx["review"].append(prop.id)
        return _("PROPUESTO para aprobación (motivo: %(r)s). %(s)s — queda en la bandeja del "
                 "aprobador; NO se aplicó.") % {"r": reason, "s": summary}

    # ---------------- agenda ----------------
    def _compute_next_run(self, base=None):
        self.ensure_one()
        now = base or fields.Datetime.now()
        if self.schedule_type == "hourly":
            return now + timedelta(hours=1)
        hh = min(int(self.time_of_day or 0), 23)
        mm = int(round((float(self.time_of_day or 0) - int(self.time_of_day or 0)) * 60)) % 60
        target_today = datetime.combine(now.date(), time(hh, mm))
        if self.schedule_type == "daily":
            return target_today if target_today > now else target_today + timedelta(days=1)
        wd = int(self.weekday or 0)
        nxt = datetime.combine((now + timedelta(days=(wd - now.weekday()) % 7)).date(), time(hh, mm))
        return nxt if nxt > now else nxt + timedelta(days=7)

    def _sync_next_run(self):
        for a in self:
            a.next_run = a._compute_next_run() if a.active else False

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._sync_next_run()
        return recs

    def write(self, vals):
        res = super().write(vals)
        if {"schedule_type", "time_of_day", "weekday", "active"} & set(vals):
            self._sync_next_run()
        return res

    # ---------------- presupuesto / kill ----------------
    @api.model
    def _killed(self):
        return self.env["ir.config_parameter"].sudo().get_param(KILL_PARAM) in ("1", "True", "true")

    def _owner_over_budget(self):
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        try:
            limit = float(icp.get_param("primate_sagui.ai_monthly_budget", 0) or 0)
        except (ValueError, TypeError):
            limit = 0.0
        if limit <= 0:
            return False
        month_start = fields.Datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        res = self.env["primate.ai.usage.log"].sudo()._read_group(
            [("user_id", "=", self.user_id.id),
             ("create_date", ">=", fields.Datetime.to_string(month_start))], [], ["cost:sum"])
        return ((res[0][0] if res else 0.0) or 0.0) >= limit

    # ---------------- ejecución ----------------
    def action_run_now(self):
        self.ensure_one()
        return self._run_one(manual=True)

    def _run_one(self, manual=False, dry_run=False):
        """Ejecuta como el dueño. Reusa el run-loop del mixin; las escrituras fuera de scope van a
        la bandeja (sin humano). Devuelve {status, output, applied, proposed, simulated}."""
        self.ensure_one()
        owner = self.user_id
        if self._owner_over_budget():
            if not dry_run:
                self.sudo().write({"active": False, "last_status": "paused", "next_run": False,
                                   "last_run": fields.Datetime.now()})
                self._notify(owner, _("Pausé «%s»: superaste el presupuesto mensual de IA.") % self.name)
                self.env["sagui.automation.run"].sudo().create(
                    {"automation_id": self.id, "status": "paused", "output": _("Pausada por presupuesto.")})
            return {"status": "paused", "output": _("Pausada por presupuesto."),
                    "applied": [], "proposed": [], "simulated": []}

        env = self.env(user=owner.id)
        action = env["primate.ai.action"]._open(
            "automatizacion", (_("[simulación] ") if dry_run else "") + self.name,
            ref="sagui.automation,%s" % self.id)
        ctx = self._build_run_ctx(dry_run=dry_run)
        run = None
        if not dry_run:
            run = env["sagui.automation.run"].sudo().create(
                {"automation_id": self.id, "status": "ok", "output": ""})
        ctx["run"] = run

        status, output = "error", ""
        try:
            output = self._run_loop(env, owner, self.instruction, action, ctx)
            status = "nothing" if (not output or NOTHING in output) else "ok"
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui automation %s falló", self.id)
            status, output = "error", _("Error: %s") % e

        if dry_run:
            return {"status": status, "output": output, "applied": [],
                    "proposed": [], "simulated": ctx["simulated"]}

        if status == "ok" and output and self.delivery_discuss:
            self._notify(owner, "🔔 **%s**\n\n%s" % (self.name, output))
        elif status == "error" and self.delivery_discuss:
            self._notify(owner, _("⚠️ «%s» falló: %s") % (self.name, output))
        if ctx["review"]:
            self._notify_approver(len(ctx["review"]))

        action.invalidate_recordset()
        vals = {"last_run": fields.Datetime.now(), "last_result": output, "last_status": status,
                "next_run": self._compute_next_run() if self.active else False}
        if status == "ok" and self.delivery_inapp:
            vals["has_unread"] = True
        self.sudo().write(vals)
        if run:
            run.sudo().write({
                "status": status, "output": (output or "")[:8000], "action_id": action.id,
                "input_tokens": action.input_tokens, "output_tokens": action.output_tokens,
                "cost": action.total_cost,
                "applied_count": len(ctx["applied"]), "proposed_count": len(ctx["review"])})
        return {"status": status, "output": output, "applied": ctx["applied"],
                "proposed": ctx["review"], "simulated": ctx["simulated"]}

    def _make_proposal(self, env, op, model, ids, values, summary, count, reason, run):
        before = False
        if op in ("write", "unlink") and ids:
            recs = env[model].browse(ids).exists()
            flds = list(values.keys()) if op == "write" else ["display_name"]
            if recs:
                before = json.dumps(recs.read(flds), default=str, ensure_ascii=False)
        return self.env["sagui.automation.proposal"].sudo().create({
            "automation_id": self.id, "run_id": run.id if run else False,
            "approver_id": (self.approver_id or self.user_id).id,
            "operation": op, "model_name": model,
            "res_ids": json.dumps(ids) if ids else False,
            "values_json": json.dumps(values, default=str, ensure_ascii=False),
            "before_json": before, "summary": summary, "record_count": count, "reason": reason,
            "state": "pending", "expiry": fields.Datetime.now() + timedelta(days=PROPOSAL_TTL_DAYS)})

    # ---------------- notificaciones ----------------
    def _notify(self, owner, text):
        try:
            bot = self.env.ref("primate_sagui.partner_sagui_bot", raise_if_not_found=False)
            if not bot:
                return
            channel = self.env(user=owner.id)["discuss.channel"]._get_or_create_chat([bot.id])
            self.env["primate.sagui.assistant"]._post_bot_reply(channel, text)
        except Exception:  # noqa: BLE001
            _logger.warning("No pude notificar la automatización %s", self.id, exc_info=True)

    def _notify_approver(self, n):
        approver = self.approver_id or self.user_id
        self._notify(approver, _("📥 «%(name)s» dejó %(n)s acción(es) esperando tu aprobación en la "
                                 "bandeja de Automatizaciones.") % {"name": self.name, "n": n})

    # ---------------- cron ----------------
    @api.model
    def _cron_run_due(self):
        if self._killed():
            _logger.info("Sagui: automatizaciones en pausa global (kill switch); no corro nada.")
            return
        now = fields.Datetime.now()
        due = self.sudo().search([("active", "=", True), ("next_run", "!=", False), ("next_run", "<=", now)])
        for auto in due:
            try:
                with self.env.cr.savepoint():
                    auto._run_one(manual=False)
            except Exception:  # noqa: BLE001
                _logger.exception("Sagui: falló la automatización %s en el cron", auto.id)
        self.env["sagui.automation.proposal"].sudo()._expire_due()

    # ---------------- API web ----------------
    @api.model
    def web_list(self):
        return [self._web_dict(a) for a in self.search([])]

    @api.model
    def _web_dict(self, a):
        labels = dict(self._fields["schedule_type"].selection)
        modes = dict(self._fields["mode"].selection)
        return {
            "id": a.id, "name": a.name, "active": a.active, "instruction": a.instruction or "",
            "schedule_type": a.schedule_type, "schedule_label": labels.get(a.schedule_type, ""),
            "time_of_day": a.time_of_day, "weekday": a.weekday,
            "delivery_discuss": a.delivery_discuss, "delivery_inapp": a.delivery_inapp,
            "mode": a.mode, "mode_label": modes.get(a.mode, ""),
            "allowed_models": a.allowed_models or "", "allowed_operations": a.allowed_operations or "",
            "field_whitelist": a.field_whitelist or "", "record_domain": a.record_domain or "",
            "max_records_per_run": a.max_records_per_run, "allow_unlink": a.allow_unlink,
            "last_run": fields.Datetime.to_string(a.last_run) if a.last_run else False,
            "next_run": fields.Datetime.to_string(a.next_run) if a.next_run else False,
            "last_status": a.last_status or False, "last_result": a.last_result or "",
            "has_unread": a.has_unread, "run_count": a.run_count, "pending_count": a.pending_count}

    @api.model
    def web_save(self, vals, automation_id=None):
        keys = ("name", "instruction", "schedule_type", "time_of_day", "weekday",
                "delivery_discuss", "delivery_inapp", "mode", "allowed_models",
                "allowed_operations", "field_whitelist", "record_domain",
                "max_records_per_run", "allow_unlink")
        clean = {k: vals[k] for k in keys if k in vals}
        if "time_of_day" in clean:
            clean["time_of_day"] = float(clean["time_of_day"] or 0)
        if "max_records_per_run" in clean:
            clean["max_records_per_run"] = int(clean["max_records_per_run"] or 0)
        if automation_id:
            auto = self.search([("id", "=", int(automation_id))])
            if not auto:
                raise UserError(_("Automatización no encontrada."))
            auto.write(clean)
        else:
            clean.setdefault("name", _("Nueva automatización"))
            clean.setdefault("instruction", "")
            auto = self.create(clean)
        return self._web_dict(auto)

    @api.model
    def web_toggle(self, automation_id, active):
        auto = self.search([("id", "=", int(automation_id))])
        if auto:
            auto.write({"active": bool(active)})
        return self._web_dict(auto) if auto else False

    @api.model
    def web_delete(self, automation_id):
        auto = self.search([("id", "=", int(automation_id))])
        if auto:
            auto.unlink()
        return True

    @api.model
    def web_run_now(self, automation_id):
        auto = self.search([("id", "=", int(automation_id))])
        if not auto:
            raise UserError(_("Automatización no encontrada."))
        res = auto._run_one(manual=True)
        return {"status": res["status"], "output": res["output"], "applied": res["applied"],
                "proposed": res["proposed"], "automation": self._web_dict(auto)}

    @api.model
    def web_dry_run(self, automation_id):
        auto = self.search([("id", "=", int(automation_id))])
        if not auto:
            raise UserError(_("Automatización no encontrada."))
        res = auto._run_one(dry_run=True)
        return {"status": res["status"], "output": res["output"], "simulated": res["simulated"]}

    @api.model
    def web_mark_read(self, automation_id):
        auto = self.search([("id", "=", int(automation_id))])
        if auto:
            auto.write({"has_unread": False})
        return True

    @api.model
    def web_kill_all(self, paused):
        if not (self.env.user.has_group("primate_ai_connector.group_ai_manager")
                or self.env.user.has_group("base.group_system")):
            raise AccessError(_("Solo un manager puede pausar todas las automatizaciones."))
        self.env["ir.config_parameter"].sudo().set_param(KILL_PARAM, "1" if paused else "0")
        return {"paused": bool(paused)}

    @api.model
    def web_kill_state(self):
        is_mgr = (self.env.user.has_group("primate_ai_connector.group_ai_manager")
                  or self.env.user.has_group("base.group_system"))
        return {"paused": self._killed(), "is_manager": is_mgr}


class SaguiAutomationRun(models.Model):
    _name = "sagui.automation.run"
    _description = "Ejecución de una automatización de Sagui"
    _order = "id desc"

    automation_id = fields.Many2one(
        "sagui.automation", string="Automatización", required=True, index=True, ondelete="cascade")
    status = fields.Selection(
        [("ok", "Reportó"), ("nothing", "Sin novedad"), ("error", "Error"), ("paused", "Pausada")],
        string="Estado")
    output = fields.Text(string="Salida")
    action_id = fields.Many2one("primate.ai.action", string="Acción IA", ondelete="set null")
    input_tokens = fields.Integer(string="Tokens entrada")
    output_tokens = fields.Integer(string="Tokens salida")
    cost = fields.Float(string="Costo (USD)", digits=(12, 5))
    applied_count = fields.Integer(string="Aplicadas (auto)")
    proposed_count = fields.Integer(string="Propuestas")


class SaguiAutomationProposal(models.Model):
    _name = "sagui.automation.proposal"
    _description = "Acción propuesta por una automatización (espera aprobación)"
    _order = "id desc"

    automation_id = fields.Many2one(
        "sagui.automation", string="Automatización", required=True, index=True, ondelete="cascade")
    run_id = fields.Many2one("sagui.automation.run", string="Ejecución", ondelete="set null")
    approver_id = fields.Many2one("res.users", string="Aprobador", index=True)
    operation = fields.Selection(
        [("create", "Crear"), ("write", "Modificar"), ("unlink", "Eliminar")],
        string="Operación", required=True)
    model_name = fields.Char(string="Modelo", required=True)
    res_ids = fields.Char(string="IDs (JSON)")
    values_json = fields.Text(string="Valores (JSON)")
    before_json = fields.Text(string="Antes (JSON)")
    summary = fields.Text(string="Resumen")
    record_count = fields.Integer(string="# Records", default=1)
    reason = fields.Char(string="Motivo (a bandeja)")
    state = fields.Selection(
        [("pending", "Pendiente"), ("approved", "Aprobada"), ("rejected", "Rechazada"),
         ("executed", "Ejecutada"), ("expired", "Expirada"), ("error", "Error")],
        string="Estado", default="pending", required=True, index=True)
    result_info = fields.Char(string="Resultado")
    expiry = fields.Datetime(string="Vence", index=True)

    @api.model
    def _expire_due(self):
        now = fields.Datetime.now()
        self.sudo().search([("state", "=", "pending"), ("expiry", "!=", False),
                            ("expiry", "<", now)]).write({"state": "expired"})

    def _approve(self):
        """Ejecuta la propuesta con permisos del DUEÑO (no del aprobador). El ACL del dueño manda."""
        for p in self:
            if p.state != "pending":
                continue
            if p.expiry and p.expiry < fields.Datetime.now():
                p.state = "expired"
                continue
            auto = p.automation_id
            env = self.env(user=auto.user_id.id)
            try:
                values = json.loads(p.values_json or "{}")
                ids = json.loads(p.res_ids or "null") or []
                ctx = auto._build_run_ctx()
                ctx["run"] = p.run_id
                info = auto._apply_write(env, p.operation, p.model_name, ids, values, ctx, proposal=p)
                p.write({"state": "executed", "result_info": info})
            except Exception as e:  # noqa: BLE001
                _logger.exception("Sagui: falló ejecutar la propuesta %s", p.id)
                p.write({"state": "error", "result_info": str(e)[:200]})
        return True

    def _reject(self):
        self.filtered(lambda p: p.state == "pending").write({"state": "rejected"})
        return True

    @api.model
    def web_inbox(self):
        groups = {}
        for p in self.search([("state", "=", "pending")]):
            g = groups.setdefault(p.automation_id.id, {
                "automation_id": p.automation_id.id, "automation": p.automation_id.name,
                "owner": p.automation_id.user_id.name, "items": []})
            g["items"].append(p._web_dict())
        return list(groups.values())

    def _web_dict(self):
        self.ensure_one()
        ops = dict(self._fields["operation"].selection)
        try:
            before = json.loads(self.before_json) if self.before_json else []
        except ValueError:
            before = []
        try:
            values = json.loads(self.values_json) if self.values_json else {}
        except ValueError:
            values = {}
        return {"id": self.id, "operation": self.operation, "operation_label": ops.get(self.operation, ""),
                "model": self.model_name, "summary": self.summary or "", "record_count": self.record_count,
                "reason": self.reason or "", "values": values, "before": before,
                "expiry": fields.Datetime.to_string(self.expiry) if self.expiry else False}

    @api.model
    def web_approve(self, proposal_ids):
        props = self.search([("id", "in", [int(i) for i in proposal_ids])])
        props._approve()
        return [{"id": p.id, "state": p.state, "result": p.result_info or ""} for p in props]

    @api.model
    def web_reject(self, proposal_ids):
        self.search([("id", "in", [int(i) for i in proposal_ids])])._reject()
        return True
