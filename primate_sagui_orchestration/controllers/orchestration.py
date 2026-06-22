# -*- coding: utf-8 -*-
# Endpoints que consume el RUNNER local (ver sagui_runner/). Esqueleto caminante (Fase 1).
#
# Auth por BEARER TOKEN guardado en ir.config_parameter clave 'sagui.runner_token' (nunca
# hardcodeado). auth='public' (el runner no tiene sesión Odoo): la autorización ES el token, que se
# valida con comparación de tiempo constante. Todo el acceso a datos va con sudo (el token manda).
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

TOKEN_PARAM = "sagui.runner_token"
JOB_MODEL = "sagui.orchestration.job"


class SaguiOrchestrationController(http.Controller):

    # ---- auth ----
    def _authorized(self):
        """True si el header Authorization: Bearer <token> coincide con el configurado."""
        expected = request.env["ir.config_parameter"].sudo().get_param(TOKEN_PARAM) or ""
        header = request.httprequest.headers.get("Authorization", "") or ""
        provided = header[7:].strip() if header.startswith("Bearer ") else ""
        # compare_digest evita timing attacks; exigimos token configurado y provisto.
        return bool(expected) and bool(provided) and hmac.compare_digest(provided, expected)

    @staticmethod
    def _json(data, status=200):
        return request.make_json_response(data, status=status)

    def _unauthorized(self):
        return self._json({"error": "unauthorized"}, status=401)

    def _job(self, job_id):
        return request.env[JOB_MODEL].sudo().browse(int(job_id)).exists()

    # ---- endpoints ----
    @http.route("/sagui/jobs/next", type="http", auth="public", methods=["GET"], csrf=False)
    def jobs_next(self, **kw):
        """Devuelve el job 'pending' más viejo serializado, o {} si no hay. No cambia estado."""
        if not self._authorized():
            return self._unauthorized()
        job = request.env[JOB_MODEL].sudo().search(
            [("state", "=", "pending")], order="id asc", limit=1)
        return self._json(job._serialize_for_runner() if job else {})

    @http.route("/sagui/jobs/<int:job_id>/start", type="http", auth="public", methods=["POST"], csrf=False)
    def jobs_start(self, job_id, **kw):
        """El runner empezó a procesar el job → state=started, started_at=now."""
        if not self._authorized():
            return self._unauthorized()
        job = self._job(job_id)
        if not job:
            return self._json({"error": "job not found"}, status=404)
        job.action_mark_started()
        return self._json({"ok": True, "job_id": job.id, "state": job.state})

    @http.route("/sagui/jobs/<int:job_id>/done", type="http", auth="public", methods=["POST"], csrf=False)
    def jobs_done(self, job_id, **kw):
        """El runner reportó el resultado → state=done|error, métricas, done_at, audit log.
        Body JSON: {status, cost_usd, num_turns, summary}."""
        if not self._authorized():
            return self._unauthorized()
        job = self._job(job_id)
        if not job:
            return self._json({"error": "job not found"}, status=404)
        try:
            payload = json.loads(request.httprequest.get_data() or b"{}")
        except (ValueError, TypeError):
            payload = {}
        job.action_mark_done(payload)
        return self._json({"ok": True, "job_id": job.id, "state": job.state})
