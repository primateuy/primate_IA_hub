# -*- coding: utf-8 -*-
# ORQUESTACIÓN Sagui ↔ Claude Code (lado server). Un "job" es una unidad de trabajo que el RUNNER
# local consume: lo pollea, invoca Claude Code headless en el repo, y reporta el resultado acá.
#
# FASE 1 (esqueleto caminante): un job dummy recorre Sagui → runner → Claude Code → vuelta a Sagui.
# Sin lógica real de actividades ni tests todavía. Ver 02-Subsistemas/orquestacion-e2e.md, ADR-0002.
import json
import logging

from odoo import api, fields, models, _

_logger = logging.getLogger(__name__)


class SaguiOrchestrationJob(models.Model):
    _name = "sagui.orchestration.job"
    _description = "Job de orquestación Sagui ↔ Claude Code"
    _order = "id"

    name = fields.Char(string="Referencia", required=True, default="Job")
    job_type = fields.Selection(
        [("fix", "Fix"), ("upload", "Upload")], string="Tipo", required=True, default="fix")
    # Estado del JOB (distinto del status del archivo de actividad, que llega en fase 2).
    # awaiting_approval: un job 'upload' que espera el gate humano antes de pushear (fase 3).
    state = fields.Selection(
        [("pending", "Pendiente"), ("awaiting_approval", "Esperando aprobación"),
         ("started", "En curso"), ("done", "Hecho"), ("rejected", "Rechazado"), ("error", "Error")],
        string="Estado", required=True, default="pending", index=True, copy=False)

    repo_path = fields.Char(string="Ruta del repo")
    branch = fields.Char(string="Rama (base)", default="17.0")
    # FASE 3 (upload/PR): la rama de trabajo donde el runner commitea el fix, y el PR resultante.
    work_branch = fields.Char(string="Rama de trabajo", copy=False,
                              help="Rama donde el runner commitea el fix (head del PR). Ej: sagui/fix-<id>.")
    commit_sha = fields.Char(string="Commit", readonly=True, copy=False)
    pr_url = fields.Char(string="Pull Request", readonly=True, copy=False)
    pr_title = fields.Char(string="Título del PR", copy=False)
    pr_body = fields.Text(string="Cuerpo del PR", copy=False)
    source_job_id = fields.Many2one("sagui.orchestration.job", string="Job origen (fix)",
                                    ondelete="set null", copy=False)
    prompt = fields.Text(string="Prompt")
    allowed_tools = fields.Char(string="Tools permitidas (Claude Code)")
    max_turns = fields.Integer(string="Máx. turnos", default=10)
    # Comando de tests del cliente (lo corre el RUNNER, no Sagui). Viene del mapping en la captura.
    tests_cmd = fields.Char(string="Comando de tests")
    tests_status = fields.Selection(
        [("ok", "Pasaron"), ("fail", "Fallaron"), ("skipped", "No corridos")],
        string="Estado de tests", readonly=True, copy=False)
    tests_output_tail = fields.Text(string="Cola de salida de tests", readonly=True, copy=False)
    # Actividad de Odoo que originó el job (fase 2: captura de pendientes).
    activity_id = fields.Many2one("mail.activity", string="Actividad origen", ondelete="set null")

    # Métricas que reporta el runner al terminar.
    cost_usd = fields.Float(string="Costo (USD)", digits=(12, 4), readonly=True, copy=False)
    num_turns = fields.Integer(string="Turnos", readonly=True, copy=False)
    summary = fields.Text(string="Resumen", readonly=True, copy=False)
    started_at = fields.Datetime(string="Inicio", readonly=True, copy=False)
    done_at = fields.Datetime(string="Fin", readonly=True, copy=False)

    # ------------------------------------------------------------------ runner API
    def _serialize_for_runner(self):
        """JSON que consume el runner local (claves chatas, 'type' en vez de 'job_type')."""
        self.ensure_one()
        return {
            "job_id": self.id,
            "type": self.job_type,
            "repo_path": self.repo_path or "",
            "prompt": self.prompt or "",
            "allowed_tools": self.allowed_tools or "",
            "max_turns": self.max_turns or 10,
            "tests_cmd": self.tests_cmd or "",
            # FASE 3: fix → work_branch donde commitear; upload → base/head + título/cuerpo del PR.
            "branch": self.branch or "",
            "work_branch": self.work_branch or "",
            "pr_title": self.pr_title or "",
            "pr_body": self.pr_body or "",
        }

    def action_mark_started(self):
        """El runner avisó que empezó a procesar el job."""
        self.ensure_one()
        self.write({"state": "started", "started_at": fields.Datetime.now()})
        return True

    def action_mark_done(self, payload):
        """El runner reportó el resultado: {status, cost_usd, num_turns, summary}. Setea el estado,
        guarda métricas y deja el evento en el audit log existente de Sagui."""
        self.ensure_one()
        payload = payload or {}
        status = (payload.get("status") or "").lower()
        state = "done" if status == "ok" else "error"
        try:
            cost = float(payload.get("cost_usd") or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        try:
            turns = int(payload.get("num_turns") or 0)
        except (TypeError, ValueError):
            turns = 0
        # Resultado del paso VERIFY (lo corre el runner, independiente de Claude Code).
        tests_status = payload.get("tests_status") or False
        if tests_status not in ("ok", "fail", "skipped"):
            tests_status = False
        vals = {
            "state": state,
            "cost_usd": cost,
            "num_turns": turns,
            "summary": payload.get("summary") or "",
            "tests_status": tests_status,
            "tests_output_tail": payload.get("tests_output_tail") or "",
            "done_at": fields.Datetime.now(),
        }
        # FASE 3: el runner reporta la rama de trabajo + commit (fix) y la PR url (upload).
        if payload.get("work_branch"):
            vals["work_branch"] = payload["work_branch"]
        if payload.get("commit_sha"):
            vals["commit_sha"] = payload["commit_sha"]
        if payload.get("pr_url"):
            vals["pr_url"] = payload["pr_url"]
        self.write(vals)
        self._audit_done(state, payload)

        if self.job_type == "fix":
            if tests_status in ("ok", "fail"):
                self._update_activity_followup(tests_status)
            # Fix verde + hay rama commiteada → encolar el upload (gate humano).
            if state == "done" and tests_status == "ok" and self.work_branch and self.commit_sha:
                self._create_upload_job()
            self._notify_user()
        elif self.job_type == "upload":
            if state == "done" and self.pr_url:
                self._update_activity_followup_upload()
            self._notify_user()
        return True

    # ------------------------------------------------------------------ FASE 3: upload / PR
    def _create_upload_job(self):
        """Tras un fix verde, encola un job 'upload' EN ESPERA DE APROBACIÓN (gate humano).
        El runner solo lo levantará cuando un humano lo apruebe (state → pending)."""
        self.ensure_one()
        # No duplicar si ya existe un upload para este fix.
        existente = self.search([("source_job_id", "=", self.id), ("job_type", "=", "upload")], limit=1)
        if existente:
            return existente
        act = self.activity_id
        ref = ("actividad #%s" % act.id) if act else ("job #%s" % self.id)
        titulo = "[Sagui] Fix %s%s" % (ref, (": " + act.summary) if (act and act.summary) else "")
        cuerpo = ((self.summary or "").strip()[:1500]
                  + "\n\n_Generado por Sagui / Claude Code (fix job #%s)._" % self.id)
        up = self.sudo().create({
            "name": "Upload: %s" % self.name,
            "job_type": "upload",
            "state": "awaiting_approval",
            "repo_path": self.repo_path,
            "branch": self.branch,            # base del PR
            "work_branch": self.work_branch,  # head del PR
            "pr_title": titulo[:200],
            "pr_body": cuerpo,
            "activity_id": act.id if act else False,
            "source_job_id": self.id,
            # placeholders para pasar la validación del runner (el upload no usa CC):
            "prompt": "(upload — git push + PR, sin Claude Code)",
            "allowed_tools": "",
            "max_turns": 0,
        })
        return up

    def action_approve_upload(self):
        """Gate humano: aprueba el upload → pasa a 'pending' para que el runner lo levante."""
        for job in self:
            if job.job_type == "upload" and job.state == "awaiting_approval":
                job.write({"state": "pending"})
        return True

    def action_reject_upload(self):
        for job in self:
            if job.job_type == "upload" and job.state == "awaiting_approval":
                job.write({"state": "rejected"})
        return True

    def _update_activity_followup_upload(self):
        """Deja el PR abierto en el chatter del record de la actividad. Best-effort."""
        act = self.activity_id
        if not act:
            return
        try:
            if act.res_model and act.res_id and act.res_model in self.env:
                rec = self.env[act.res_model].sudo().browse(act.res_id).exists()
                if rec and hasattr(rec, "message_post"):
                    rec.message_post(body=(
                        "Sagui — PR abierto para el fix (job #%s): %s" % (self.id, self.pr_url or "—")))
        except Exception:  # noqa: BLE001
            _logger.warning("No pude registrar el PR en la actividad del job %s", self.id, exc_info=True)

    def _audit_done(self, state, payload):
        """Registra el cierre del job en el audit log existente (sagui.task.action.log)."""
        try:
            self.env["sagui.task.action.log"].sudo().create({
                "operation": "write",
                "model_name": "sagui.orchestration.job",
                "res_ids": json.dumps(self.ids),
                "after_json": json.dumps({
                    "job": self.name, "type": self.job_type, "state": state,
                    "cost_usd": self.cost_usd, "num_turns": self.num_turns,
                    "tests_status": self.tests_status or "n/a",
                    "summary": (self.summary or "")[:500],
                }, ensure_ascii=False, default=str),
            })
        except Exception:  # noqa: BLE001 - un fallo de auditoría no debe tumbar el endpoint
            _logger.warning("No pude auditar el cierre del job %s", self.id, exc_info=True)

    def _update_activity_followup(self, tests_status):
        """Deja el estado de seguimiento (tests-ok/tests-fail) en el chatter del record de la
        actividad linkeada. Best-effort: un fallo acá no debe tumbar el done."""
        act = self.activity_id
        if not act:
            return
        estado = "tests-ok" if tests_status == "ok" else "tests-fail"
        try:
            if act.res_model and act.res_id and act.res_model in self.env:
                rec = self.env[act.res_model].sudo().browse(act.res_id).exists()
                if rec and hasattr(rec, "message_post"):
                    rec.message_post(body=(
                        "Sagui — el fix de la actividad terminó: %s "
                        "(job #%s, %s)." % (estado, self.id, self.tests_status)))
        except Exception:  # noqa: BLE001
            _logger.warning("No pude actualizar el seguimiento de la actividad del job %s", self.id, exc_info=True)

    def _notify_user(self):
        """Notifica al usuario el cierre de un job 'fix' por los canales configurados.
        Pluggable + best-effort: cada canal falla aislado y nunca tumba el done.
        Canales (ir.config_parameter 'sagui.notify_channels', coma; default 'discuss'):
          - discuss:  el bot Sagui le avisa al asignado de la actividad (sin setup).
          - telegram: Bot API (params sagui.telegram_bot_token + sagui.telegram_chat_id).
          - webhook:  POST del resultado en JSON a sagui.notify_webhook_url (enganchá WhatsApp/etc.)."""
        self.ensure_one()
        icp = self.env["ir.config_parameter"].sudo()
        canales = [c.strip() for c in (icp.get_param("sagui.notify_channels") or "discuss").split(",") if c.strip()]
        msg = self._notify_message()
        if "discuss" in canales:
            self._notify_discuss(msg)
        if "telegram" in canales:
            self._notify_telegram(msg, icp)
        if "webhook" in canales:
            self._notify_webhook(icp)
        _logger.info("Sagui notify job #%s por %s", self.id, canales)

    def _notify_message(self):
        """Texto humano del resultado del job (contextual: fix vs upload)."""
        self.ensure_one()
        # FASE 3 — upload (PR).
        if self.job_type == "upload":
            if self.state == "done" and self.pr_url:
                return "✅ Sagui — PR abierto para «%s»: %s" % (self.name, self.pr_url)
            return "❌ Sagui — falló el upload de «%s»: %s" % (self.name, (self.summary or "")[:200])
        # Fix.
        icono = {"ok": "✅", "fail": "❌", "skipped": "⏭️"}.get(self.tests_status or "", "•")
        partes = ["%s Sagui — «%s»" % (icono, self.name)]
        if self.tests_status:
            partes.append("tests: %s" % self.tests_status)
        partes.append("estado: %s" % self.state)
        partes.append("$%.4f · %s turnos" % (self.cost_usd or 0.0, self.num_turns or 0))
        if self.activity_id:
            partes.append("actividad #%s" % self.activity_id.id)
        cuerpo = " · ".join(partes)
        if self.tests_status == "fail" and self.tests_output_tail:
            cuerpo += "\n```\n%s\n```" % (self.tests_output_tail or "")[-600:]
        # Fix verde con upload encolado a la espera de aprobación.
        if self.tests_status == "ok" and self.work_branch and self.commit_sha:
            cuerpo += "\n→ Listo para subir: aprobá el upload para abrir el PR."
        return cuerpo

    def _notify_target_user(self):
        """A quién avisar: el asignado de la actividad origen (si hay)."""
        self.ensure_one()
        return self.activity_id.user_id if (self.activity_id and self.activity_id.user_id) else False

    def _notify_discuss(self, msg):
        try:
            target = self._notify_target_user()
            if not target:
                _logger.info("Sagui notify job #%s: sin usuario destino para Discuss, salto.", self.id)
                return
            bot = self.env.ref("primate_sagui.partner_sagui_bot", raise_if_not_found=False)
            if not bot:
                return
            channel = self.env(user=target.id)["discuss.channel"]._get_or_create_chat([bot.id])
            self.env["primate.sagui.assistant"]._post_bot_reply(channel, msg)
        except Exception:  # noqa: BLE001
            _logger.warning("Sagui notify Discuss falló (job %s)", self.id, exc_info=True)

    def _notify_telegram(self, msg, icp):
        try:
            token = (icp.get_param("sagui.telegram_bot_token") or "").strip()
            chat_id = (icp.get_param("sagui.telegram_chat_id") or "").strip()
            if not token or not chat_id:
                _logger.info("Sagui notify Telegram: sin token/chat_id configurado, salto.")
                return
            import requests
            requests.post(
                "https://api.telegram.org/bot%s/sendMessage" % token,
                json={"chat_id": chat_id, "text": msg}, timeout=10)
        except Exception:  # noqa: BLE001
            _logger.warning("Sagui notify Telegram falló (job %s)", self.id, exc_info=True)

    def _notify_webhook(self, icp):
        try:
            url = (icp.get_param("sagui.notify_webhook_url") or "").strip()
            if not url:
                _logger.info("Sagui notify webhook: sin URL configurada, salto.")
                return
            import requests
            requests.post(url, json={
                "job_id": self.id, "name": self.name, "state": self.state,
                "tests_status": self.tests_status or "n/a", "cost_usd": self.cost_usd or 0.0,
                "num_turns": self.num_turns or 0, "summary": (self.summary or "")[:500],
                "activity_id": self.activity_id.id if self.activity_id else False,
            }, timeout=10)
        except Exception:  # noqa: BLE001
            _logger.warning("Sagui notify webhook falló (job %s)", self.id, exc_info=True)
