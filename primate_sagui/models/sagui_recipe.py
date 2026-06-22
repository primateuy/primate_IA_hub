# -*- coding: utf-8 -*-
# RECETAS de Sagui = tarea ejecutable (sagui.executable.task) ON-DEMAND, parametrizada y reutilizable.
# Comparte TODO el core con Automatizaciones (instruction + mode + scope + run-loop + auditoría +
# dry-run). Diferencia: corre con permisos del USUARIO que la ejecuta (humano presente) → las
# escrituras fuera de scope van a CONFIRMACIÓN INTERACTIVA (primate.sagui.pending.write), no a la
# bandeja desatendida. Los params {placeholder} se sustituyen en la instrucción al correr.
import base64
import io
import json
import logging
import secrets

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .sagui_executable_task import NOTHING

_logger = logging.getLogger(__name__)

PTYPES = [("text", "Texto"), ("number", "Número"), ("date", "Fecha"),
          ("selection", "Selección"), ("many2one", "Registro (many2one)")]


class SaguiRecipe(models.Model):
    _name = "sagui.recipe"
    _inherit = ["sagui.executable.task"]
    _description = "Receta de Sagui (tarea guardada, parametrizada y reutilizable)"
    _order = "favorite desc, name"

    name = fields.Char(string="Nombre", required=True)
    description = fields.Text(string="Descripción")
    category = fields.Char(string="Categoría")
    tags = fields.Char(string="Tags", help="Separados por coma.")
    user_id = fields.Many2one(
        "res.users", string="Dueño", required=True, index=True, ondelete="cascade",
        default=lambda self: self.env.uid)
    shared = fields.Boolean(string="Compartida con el equipo", default=False)
    favorite = fields.Boolean(string="Favorita", default=False)
    output_type = fields.Selection(
        [("chat", "Respuesta en el chat"), ("file", "Archivo descargable")],
        string="Salida", default="chat", required=True)
    param_ids = fields.One2many("sagui.recipe.param", "recipe_id", string="Parámetros")

    # ---------------- hooks del core ----------------
    def _task_action_type(self):
        return "receta"

    def _audit_extra(self, ctx, proposal):
        return {"recipe_id": self.id, "user_id": self.env.uid}

    def _route_to_review(self, env, op, model, ids, values, summary, count, reason, ctx):
        """Humano presente → confirmación interactiva con pending.write (no la bandeja)."""
        bot = self.env.ref("primate_sagui.partner_sagui_bot", raise_if_not_found=False)
        channel = env["discuss.channel"]._get_or_create_chat([bot.id]) if bot else False
        token = secrets.token_hex(3)
        before = False
        if op in ("write", "unlink") and ids:
            recs = env[model].browse(ids).exists()
            flds = list(values.keys()) if op == "write" else ["display_name"]
            if recs:
                before = json.dumps(recs.read(flds), default=str, ensure_ascii=False)
        self.env["primate.sagui.pending.write"].sudo().create({
            "token": token, "channel_id": channel.id if channel else False, "user_id": self.env.uid,
            "operation": op, "model_name": model, "res_ids": json.dumps(ids) if ids else False,
            "values_json": json.dumps(values, default=str, ensure_ascii=False),
            "summary": summary, "state": "pending", "recipe_id": self.id})
        ctx.setdefault("review_items", []).append(
            {"token": token, "op": op, "model": model, "summary": summary, "count": count,
             "before": json.loads(before) if before else [], "values": values})
        ctx["review"].append(token)
        return _("PROPUESTO (token %(t)s, motivo: %(r)s): %(s)s — confirmá para aplicar.") % {
            "t": token, "r": reason, "s": summary}

    # ---------------- params ----------------
    def _resolve_instruction(self, params):
        """Sustituye {param} en la instrucción con los valores provistos (o el default)."""
        self.ensure_one()
        text = self.instruction or ""
        for p in self.param_ids:
            raw = params.get(p.name) if params else None
            if raw in (None, ""):
                raw = p.default or ""
            if p.ptype == "many2one" and raw not in (None, "") and p.relation:
                rec = self.env[p.relation].browse(int(raw)).exists() if str(raw).isdigit() else None
                sub = ("%s (id %s)" % (rec.display_name, raw)) if rec else str(raw)
            else:
                sub = str(raw)
            text = text.replace("{%s}" % p.name, sub)
        return text

    # ---------------- ejecución (on-demand, como el usuario) ----------------
    def _run(self, params=None, dry_run=False):
        self.ensure_one()
        env = self.env                      # corre como el USUARIO que ejecuta (humano presente)
        actor = self.env.user
        instruction = self._resolve_instruction(params or {})
        if self.output_type == "file" and not dry_run:
            instruction += _("\n\n(IMPORTANTE: devolvé SOLO el contenido como filas CSV — la primera "
                             "fila son los encabezados — sin texto extra antes ni después.)")
        action = env["primate.ai.action"]._open(
            "receta", (_("[simulación] ") if dry_run else "") + self.name, ref="sagui.recipe,%s" % self.id)
        ctx = self._build_run_ctx(dry_run=dry_run)
        ctx["review_items"] = []
        status, output = "error", ""
        try:
            output = self._run_loop(env, actor, instruction, action, ctx)
            status = "nothing" if (not output or NOTHING in output) else "ok"
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui recipe %s falló", self.id)
            status, output = "error", _("Error: %s") % e
        res = {"status": status, "output": output, "applied": ctx["applied"],
               "simulated": ctx["simulated"], "pendings": ctx.get("review_items", [])}
        if status == "ok" and self.output_type == "file" and not dry_run:
            res["file"] = self._render_file(output)
        return res

    def _render_file(self, text):
        """CSV → xlsx (openpyxl); si no parsea como tabla, .md. Devuelve {filename, url}."""
        self.ensure_one()
        fname = (self.name or "reporte").strip().replace("/", "-")
        data, ext, mimetype = None, "md", "text/markdown"
        try:
            rows = [r for r in __import__("csv").reader(io.StringIO((text or "").strip())) if r]
        except Exception:  # noqa: BLE001
            rows = []
        if len(rows) >= 2:
            try:
                import openpyxl
                wb = openpyxl.Workbook()
                ws = wb.active
                for r in rows:
                    ws.append(r)
                buf = io.BytesIO()
                wb.save(buf)
                data, ext = buf.getvalue(), "xlsx"
                mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            except Exception:  # noqa: BLE001
                data = None
        if data is None:
            data = (text or "").encode("utf-8")
        att = self.env["ir.attachment"].create({
            "name": "%s.%s" % (fname, ext), "datas": base64.b64encode(data).decode(),
            "mimetype": mimetype, "res_model": "sagui.recipe", "res_id": self.id})
        return {"filename": att.name, "url": "/web/content/%s?download=true" % att.id}

    # ---------------- API web ----------------
    @api.model
    def web_list(self):
        """Mías + compartidas (la record rule ya filtra a lo visible)."""
        out = []
        for r in self.search([]):
            out.append({
                "id": r.id, "name": r.name, "description": r.description or "",
                "category": r.category or "", "tags": r.tags or "", "favorite": r.favorite,
                "shared": r.shared, "mine": r.user_id.id == self.env.uid, "owner": r.user_id.name,
                "mode": r.mode, "output_type": r.output_type, "param_count": len(r.param_ids)})
        return out

    @api.model
    def web_categories(self):
        cats = set()
        for r in self.search([]):
            if r.category:
                cats.add(r.category.strip())
        return sorted(cats)

    @api.model
    def web_get(self, recipe_id):
        r = self.search([("id", "=", int(recipe_id))])
        if not r:
            raise UserError(_("Receta no encontrada."))
        modes = dict(self._fields["mode"].selection)
        return {
            "id": r.id, "name": r.name, "description": r.description or "", "category": r.category or "",
            "tags": r.tags or "", "shared": r.shared, "favorite": r.favorite, "mine": r.user_id.id == self.env.uid,
            "instruction": r.instruction or "", "mode": r.mode, "mode_label": modes.get(r.mode, ""),
            "allowed_models": r.allowed_models or "", "allowed_operations": r.allowed_operations or "",
            "field_whitelist": r.field_whitelist or "", "record_domain": r.record_domain or "",
            "max_records_per_run": r.max_records_per_run, "allow_unlink": r.allow_unlink,
            "output_type": r.output_type,
            "params": [{
                "id": p.id, "name": p.name, "label": p.label or p.name, "ptype": p.ptype,
                "required": p.required, "default": p.default or "", "relation": p.relation or "",
                "selection_options": p.selection_options or "",
            } for p in r.param_ids.sorted("sequence")]}

    @api.model
    def web_save(self, vals, recipe_id=None):
        keys = ("name", "description", "category", "tags", "shared", "favorite", "instruction",
                "mode", "allowed_models", "allowed_operations", "field_whitelist", "record_domain",
                "max_records_per_run", "allow_unlink", "output_type")
        clean = {k: vals[k] for k in keys if k in vals}
        if "max_records_per_run" in clean:
            clean["max_records_per_run"] = int(clean["max_records_per_run"] or 0)
        # params: lista de dicts -> reemplazar el o2m
        params = vals.get("params")
        if params is not None:
            cmds = [(5, 0, 0)]
            for i, p in enumerate(params):
                cmds.append((0, 0, {
                    "sequence": i, "name": (p.get("name") or "").strip(),
                    "label": p.get("label") or "", "ptype": p.get("ptype") or "text",
                    "required": bool(p.get("required")), "default": p.get("default") or "",
                    "relation": p.get("relation") or "", "selection_options": p.get("selection_options") or "",
                }))
            clean["param_ids"] = cmds
        if recipe_id:
            r = self.search([("id", "=", int(recipe_id))])
            if not r:
                raise UserError(_("Receta no encontrada."))
            r.write(clean)
        else:
            clean.setdefault("name", _("Nueva receta"))
            clean.setdefault("instruction", "")
            r = self.create(clean)
        return self.web_get(r.id)

    @api.model
    def web_delete(self, recipe_id):
        r = self.search([("id", "=", int(recipe_id))])
        if r:
            r.unlink()
        return True

    @api.model
    def web_toggle_favorite(self, recipe_id):
        r = self.search([("id", "=", int(recipe_id))])
        if r:
            r.write({"favorite": not r.favorite})
        return {"id": r.id, "favorite": r.favorite} if r else False

    @api.model
    def web_run(self, recipe_id, params=None):
        r = self.search([("id", "=", int(recipe_id))])
        if not r:
            raise UserError(_("Receta no encontrada."))
        return r._run(params or {}, dry_run=False)

    @api.model
    def web_dry_run(self, recipe_id, params=None):
        r = self.search([("id", "=", int(recipe_id))])
        if not r:
            raise UserError(_("Receta no encontrada."))
        return r._run(params or {}, dry_run=True)

    # confirmación interactiva de un pending.write de receta (reusa _apply_write del core)
    @api.model
    def web_confirm_pending(self, token):
        Pending = self.env["primate.sagui.pending.write"].sudo()
        p = Pending.search([("token", "=", token)], limit=1)
        if not p or p.user_id.id != self.env.uid or p.state != "pending":
            return {"ok": False, "error": _("Propuesta no encontrada o ya resuelta.")}
        recipe = p.recipe_id
        if not recipe:
            return {"ok": False, "error": _("Propuesta sin receta asociada.")}
        env = self.env  # ejecuta como el usuario (ACL real aplica)
        try:
            # Acción EXTERNA (conector): la ejecuta el MCP server, no el ORM.
            if p.operation == "external":
                ok, info = self.env["sagui.connector"]._execute_external_pending(p)
                p.write({"state": "done" if ok else "error", "result_info": (info or "")[:200]})
                return {"ok": ok, "info": info}
            values = json.loads(p.values_json or "{}")
            ids = json.loads(p.res_ids or "null") or []
            ctx = recipe._build_run_ctx()
            info = recipe._apply_write(env, p.operation, p.model_name, ids, values, ctx)
            p.write({"state": "done", "result_info": info})
            return {"ok": True, "info": info}
        except Exception as e:  # noqa: BLE001
            _logger.exception("Sagui recipe: falló confirmar pending %s", token)
            p.write({"state": "error", "result_info": str(e)[:200]})
            return {"ok": False, "error": str(e)[:200]}

    @api.model
    def web_cancel_pending(self, token):
        p = self.env["primate.sagui.pending.write"].sudo().search([("token", "=", token)], limit=1)
        if p and p.user_id.id == self.env.uid and p.state == "pending":
            p.write({"state": "cancelled"})
        return True

    # PUENTE: crear una automatización a partir de una receta (instrucción + params bakeados)
    @api.model
    def web_schedule(self, recipe_id, params, schedule):
        r = self.search([("id", "=", int(recipe_id))])
        if not r:
            raise UserError(_("Receta no encontrada."))
        instruction = r._resolve_instruction(params or {})
        sched = schedule or {}
        auto = self.env["sagui.automation"].create({
            "name": _("[Receta] %s") % r.name, "instruction": instruction, "mode": r.mode,
            "allowed_models": r.allowed_models, "allowed_operations": r.allowed_operations,
            "field_whitelist": r.field_whitelist, "record_domain": r.record_domain,
            "max_records_per_run": r.max_records_per_run, "allow_unlink": r.allow_unlink,
            "schedule_type": sched.get("schedule_type", "daily"),
            "time_of_day": float(sched.get("time_of_day", 8.0) or 8.0),
            "weekday": sched.get("weekday", "0")})
        return {"automation_id": auto.id, "name": auto.name}


class SaguiRecipeParam(models.Model):
    _name = "sagui.recipe.param"
    _description = "Parámetro tipado de una receta de Sagui"
    _order = "sequence, id"

    recipe_id = fields.Many2one("sagui.recipe", string="Receta", required=True, index=True, ondelete="cascade")
    sequence = fields.Integer(string="Secuencia", default=10)
    name = fields.Char(string="Clave {param}", required=True)
    label = fields.Char(string="Etiqueta")
    ptype = fields.Selection(PTYPES, string="Tipo", default="text", required=True)
    required = fields.Boolean(string="Requerido", default=False)
    default = fields.Char(string="Valor por defecto")
    selection_options = fields.Char(string="Opciones (coma)", help="Para tipo selección.")
    relation = fields.Char(string="Modelo relacionado", help="Para tipo many2one, ej. 'res.partner'.")
