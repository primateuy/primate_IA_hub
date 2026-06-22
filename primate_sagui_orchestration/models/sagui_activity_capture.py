# -*- coding: utf-8 -*-
# FASE 2a — captura de actividades pendientes → .md de contexto → job 'fix'.
#
# A pedido MANUAL del usuario (gate humano #1, sin cron), Sagui junta sus mail.activity vencidas/de
# hoy, resuelve a qué repo de cliente corresponde cada una, escribe un .md de contexto en el repo del
# vault (commit real vía el conector GitHub existente) y encola un job 'fix' por actividad, con el
# contexto EMBEBIDO en el prompt (CC trabaja en el repo del cliente, sin depender del vault clonado).
import json
import logging
import re

from odoo import api, fields, models, _
from odoo.tools import html2plaintext

_logger = logging.getLogger(__name__)

FIX_ALLOWED_TOOLS = "Read,Edit"
FIX_MAX_TURNS = 15
VAULT_DIR = "Actividades Pendientes"


class SaguiRepoMapping(models.Model):
    _name = "sagui.repo.mapping"
    _description = "Mapeo cliente → repo (orquestación Sagui)"
    _order = "client_key"

    client_key = fields.Char(string="Clave de cliente", required=True, index=True)
    repo_path = fields.Char(string="Ruta del repo", required=True)
    branch = fields.Char(string="Rama", default="17.0")
    tests_cmd = fields.Char(string="Comando de tests")
    modulo = fields.Char(string="Módulo")
    company_id = fields.Many2one("res.company", string="Compañía", ondelete="set null")
    partner_id = fields.Many2one("res.partner", string="Partner", ondelete="set null")

    _sql_constraints = [
        ("client_key_uniq", "unique(client_key)", "La clave de cliente debe ser única."),
    ]


class SaguiActivityCapture(models.TransientModel):
    _name = "sagui.activity.capture"
    _description = "Traer actividades pendientes (captura manual → jobs)"

    result = fields.Text(string="Resultado", readonly=True)

    # ------------------------------------------------------------------ acción manual
    def action_capture(self):
        """Botón 'Traer pendientes de hoy'. Corre la captura como el USUARIO actual."""
        self.ensure_one()
        resumen = self._capturar_pendientes()
        self.result = resumen["texto"]
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Captura de pendientes"),
                "message": resumen["texto"],
                "type": "success" if resumen["procesadas"] else "warning",
                "sticky": True,
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "sagui.orchestration.job",
                    "view_mode": "list,form",
                    "domain": [("activity_id", "!=", False)],
                    "name": _("Jobs creados"),
                },
            },
        }

    @api.model
    def _capturar_pendientes(self):
        """Junta las mail.activity vencidas/de hoy del usuario, arma .md, commitea al vault y crea
        un job 'fix' por cada una con mapping. Las que no resuelven se saltan + audit. No rompe."""
        user = self.env.user
        hoy = fields.Date.context_today(self)
        actividades = self.env["mail.activity"].search([
            ("user_id", "=", user.id), ("date_deadline", "<=", hoy)])
        procesadas, saltadas, lineas = 0, 0, []
        Job = self.env["sagui.orchestration.job"]

        for act in actividades:
            mapping = self._resolve_repo_mapping(act)
            if not mapping:
                saltadas += 1
                self._audit_skip(act, "sin mapping de repo")
                lineas.append("· saltada #%s (%s): sin mapping" % (act.id, (act.summary or "")[:40]))
                continue

            contexto = self._build_context(act)
            md = self._build_md(act, mapping, contexto)
            path = "%s/%s-%s.md" % (VAULT_DIR, act.id, self._slug(act.summary or str(act.id)))
            commit_ok = self._commit_md_to_vault(path, md, "Sagui: actividad %s" % act.id)

            job = Job.create({
                "name": "Fix: %s" % ((act.summary or ("actividad %s" % act.id))[:60]),
                "job_type": "fix",
                "state": "pending",
                "repo_path": mapping.repo_path,
                "branch": mapping.branch or "17.0",
                "allowed_tools": FIX_ALLOWED_TOOLS,
                "max_turns": FIX_MAX_TURNS,
                "tests_cmd": mapping.tests_cmd or "",
                # FASE 3: rama de trabajo donde el runner commiteará el fix (head del futuro PR).
                "work_branch": "sagui/fix-%s" % act.id,
                "activity_id": act.id,
                "prompt": self._build_prompt(act, mapping, contexto),
            })
            procesadas += 1
            lineas.append("· job #%s ← actividad #%s (%s)%s" % (
                job.id, act.id, mapping.client_key, "" if commit_ok else " [.md no commiteado]"))

        texto = _("Procesadas: %(p)s · Saltadas: %(s)s\n%(d)s") % {
            "p": procesadas, "s": saltadas, "d": "\n".join(lineas) or _("(sin actividades pendientes)")}
        _logger.info("Sagui captura: procesadas=%s saltadas=%s", procesadas, saltadas)
        return {"procesadas": procesadas, "saltadas": saltadas, "texto": texto}

    # ------------------------------------------------------------------ resolución (PLUGGABLE)
    @api.model
    def _resolve_repo_mapping(self, activity):
        """Devuelve el sagui.repo.mapping del cliente de la actividad, o False.

        ===> PUNTO A AJUSTAR A LA DATA REAL. <===
        Heurística base: mira el record relacionado (res_model/res_id). Si es project.task, sube al
        project para sacar company/partner; si no, usa el company_id/partner_id del propio record.
        Luego matchea un mapping por company_id o partner_id. Si no resuelve, devuelve False (el caller
        salta la actividad y la deja en el audit log)."""
        Mapping = self.env["sagui.repo.mapping"]
        rec = None
        if activity.res_model and activity.res_id and activity.res_model in self.env:
            rec = self.env[activity.res_model].browse(activity.res_id).exists()

        company = partner = False
        if rec:
            if rec._name == "project.task" and "project_id" in rec._fields and rec.project_id:
                proj = rec.project_id
                company = proj.company_id or (rec.company_id if "company_id" in rec._fields else False)
                partner = proj.partner_id or (rec.partner_id if "partner_id" in rec._fields else False)
            else:
                company = rec.company_id if "company_id" in rec._fields else False
                partner = rec.partner_id if "partner_id" in rec._fields else False

        if company:
            m = Mapping.search([("company_id", "=", company.id)], limit=1)
            if m:
                return m
        if partner:
            m = Mapping.search([("partner_id", "=", partner.id)], limit=1)
            if m:
                return m
        return False

    # ------------------------------------------------------------------ .md y prompt
    @api.model
    def _build_context(self, activity):
        """Texto de contexto: resumen/nota de la actividad + comentarios del chatter del record."""
        partes = []
        resumen = (activity.summary or "").strip()
        nota = html2plaintext(activity.note or "").strip()
        if resumen:
            partes.append("Resumen: %s" % resumen)
        if nota:
            partes.append("Nota:\n%s" % nota)

        comentarios = self._collect_comments(activity)
        if comentarios:
            partes.append("Comentarios:\n%s" % comentarios)
        return "\n\n".join(partes) or _("(sin descripción)")

    @api.model
    def _collect_comments(self, activity):
        """mail.message (chatter) del record relacionado: autor + fecha + cuerpo (a texto)."""
        if not (activity.res_model and activity.res_id and activity.res_model in self.env):
            return ""
        rec = self.env[activity.res_model].browse(activity.res_id).exists()
        if not rec or "message_ids" not in rec._fields:
            return ""
        out = []
        for msg in rec.message_ids.sorted("id")[:30]:
            cuerpo = html2plaintext(msg.body or "").strip()
            if not cuerpo:
                continue
            autor = msg.author_id.display_name or (msg.email_from or "—")
            fecha = fields.Datetime.to_string(msg.date) if msg.date else ""
            out.append("- [%s] %s: %s" % (fecha, autor, cuerpo))
        return "\n".join(out)

    @api.model
    def _build_md(self, activity, mapping, contexto):
        """Markdown con frontmatter válido + cuerpo (registro humano en Obsidian)."""
        front = (
            "---\n"
            "task_id: %(task_id)s\n"
            "cliente: %(cliente)s\n"
            "modulo: %(modulo)s\n"
            "repo_path: %(repo_path)s\n"
            "branch: %(branch)s\n"
            "status: nueva\n"
            "created: %(created)s\n"
            "tests_cmd: %(tests_cmd)s\n"
            "---\n"
        ) % {
            "task_id": activity.id,
            "cliente": mapping.client_key,
            "modulo": mapping.modulo or "",
            "repo_path": mapping.repo_path,
            "branch": mapping.branch or "",
            "created": fields.Datetime.to_string(fields.Datetime.now()),
            "tests_cmd": mapping.tests_cmd or "",
        }
        titulo = "# %s\n\n" % (activity.summary or ("Actividad %s" % activity.id))
        return front + "\n" + titulo + contexto + "\n"

    @api.model
    def _build_prompt(self, activity, mapping, contexto):
        """Instrucciones de fix + contexto EMBEBIDO (CC trabaja en el repo del cliente, cwd)."""
        return (
            "Sos Claude Code trabajando en el repositorio de un cliente (estás en su raíz). "
            "Resolvé la siguiente tarea reportada en Odoo. Hacé el cambio MÍNIMO y seguro; leé el "
            "código relevante antes de tocar. NO hagas commits ni push (el flujo lo maneja Sagui).\n\n"
            "Cliente: %(cliente)s%(modulo)s\n"
            "Actividad Odoo #%(task_id)s\n\n"
            "=== CONTEXTO ===\n%(contexto)s\n=== FIN CONTEXTO ===\n\n"
            "Aplicá el fix que corresponda según el contexto."
        ) % {
            "cliente": mapping.client_key,
            "modulo": (" · módulo %s" % mapping.modulo) if mapping.modulo else "",
            "task_id": activity.id,
            "contexto": contexto,
        }

    @staticmethod
    def _slug(texto):
        s = re.sub(r"[^a-z0-9]+", "-", (texto or "").lower()).strip("-")
        return (s or "actividad")[:50]

    # ------------------------------------------------------------------ commit al vault (GitHub)
    @api.model
    def _commit_md_to_vault(self, path, content, message):
        """Commitea el .md al repo del vault vía el conector GitHub existente (no reimplementa auth).
        Best-effort: si el vault/conector no está configurado, loguea y sigue (el job igual se crea)."""
        icp = self.env["ir.config_parameter"].sudo()
        coords = (icp.get_param("sagui.vault_github") or "").strip()   # "owner/repo"
        if "/" not in coords:
            _logger.info("Vault GitHub no configurado (param sagui.vault_github); salto el commit.")
            return False
        owner, repo = coords.split("/", 1)
        branch = (icp.get_param("sagui.vault_github_branch") or "main").strip()

        Conn = self.env["sagui.connector"]
        conn_id = icp.get_param("sagui.vault_connector_id")
        domain = [("id", "=", int(conn_id))] if conn_id else \
            [("connector_type", "=", "github"), ("enabled", "=", True)]
        conn = Conn.search(domain, limit=1)
        if not conn:
            _logger.warning("Sin conector GitHub para el vault; salto el commit del .md.")
            return False
        cred = self.env["sagui.connector.credential"].search(
            [("connector_id", "=", conn.id), ("user_id", "=", self.env.uid)], limit=1)
        if not cred or not cred.api_key_enc:
            _logger.warning("Sin credencial GitHub del usuario para el vault; salto el commit.")
            return False
        try:
            # Llamada DIRECTA (infra de Sagui, no escritura iniciada por el LLM → sin gating).
            # Requiere el conector con escrituras habilitadas (server no read-only).
            self.env["primate.sagui.mcp"].call_tool(cred, "create_or_update_file", {
                "owner": owner, "repo": repo, "branch": branch,
                "path": path, "content": content, "message": message})
            return True
        except Exception as e:  # noqa: BLE001
            _logger.warning("Falló el commit del .md al vault (%s): %s", path, e)
            return False

    # ------------------------------------------------------------------ audit
    @api.model
    def _audit_skip(self, activity, motivo):
        """Deja constancia en el audit log de una actividad saltada (no rompe la captura)."""
        try:
            self.env["sagui.task.action.log"].sudo().create({
                "operation": "write",
                "model_name": "mail.activity",
                "res_ids": json.dumps([activity.id]),
                "after_json": json.dumps(
                    {"saltada": True, "motivo": motivo, "summary": (activity.summary or "")[:120]},
                    ensure_ascii=False, default=str),
            })
        except Exception:  # noqa: BLE001
            _logger.warning("No pude auditar el skip de la actividad %s", activity.id, exc_info=True)
