import html
import re
from typing import Any

from mcp.server.fastmcp import FastMCP

from .odoo import OdooClient


def _strip_html(s: Any) -> str:
    if not s:
        return ""
    text = str(s)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _m2o(v: Any) -> dict | None:
    """Convierte una tupla Many2one [id, name] de Odoo a dict, o None si vacío."""
    if not v:
        return None
    return {"id": v[0], "name": v[1]}


def register_tools(mcp: FastMCP, odoo: OdooClient) -> None:

    @mcp.tool()
    def whoami() -> dict:
        """Verifica la conexión a Odoo y devuelve datos del usuario autenticado.

        Útil como primer test después de configurar las credenciales.
        """
        user = odoo.read(
            "res.users",
            [odoo.uid],
            ["id", "name", "login", "email", "company_id"],
        )[0]
        return {
            "url": odoo.url,
            "db": odoo.db,
            "uid": odoo.uid,
            "user": {
                "id": user["id"],
                "name": user["name"],
                "login": user["login"],
                "email": user.get("email") or None,
                "company": _m2o(user.get("company_id")),
            },
        }

    @mcp.tool()
    def list_projects(user_id: int | None = None, limit: int = 100) -> list[dict]:
        """Lista proyectos (project.project), opcionalmente filtrando por manager."""
        domain: list = []
        if user_id is not None:
            domain.append(("user_id", "=", user_id))
        rows = odoo.search_read(
            "project.project",
            domain=domain,
            fields=["id", "name", "user_id", "task_count", "active", "partner_id"],
            limit=limit,
            order="name asc",
        )
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "manager": _m2o(r.get("user_id")),
                "partner": _m2o(r.get("partner_id")),
                "task_count": r.get("task_count"),
                "active": r.get("active"),
            }
            for r in rows
        ]

    @mcp.tool()
    def list_task_messages(task_id: int, limit: int = 20) -> list[dict]:
        """Mensajes del chatter (mail.message) de una tarea, del más reciente al más antiguo.

        Devuelve autor, fecha, tipo, asunto y body sin HTML.
        """
        rows = odoo.search_read(
            "mail.message",
            domain=[
                ("model", "=", "project.task"),
                ("res_id", "=", task_id),
            ],
            fields=[
                "id",
                "author_id",
                "date",
                "message_type",
                "subtype_id",
                "subject",
                "body",
            ],
            limit=limit,
            order="date desc",
        )
        return [
            {
                "id": r["id"],
                "author": _m2o(r.get("author_id")),
                "date": r.get("date"),
                "type": r.get("message_type"),
                "subtype": _m2o(r.get("subtype_id")),
                "subject": r.get("subject") or None,
                "body": _strip_html(r.get("body")),
            }
            for r in rows
        ]

    @mcp.tool()
    def list_tasks(
        project_id: int | None = None,
        user_id: int | None = None,
        stage_id: int | None = None,
        state: str | None = None,
        only_open: bool = True,
        limit: int = 100,
    ) -> list[dict]:
        """Lista tareas (project.task) con filtros opcionales.

        - state: '01_in_progress' | '02_changes_requested' | '03_approved'
                 | '04_waiting_normal' | '1_done' | '1_canceled'
        - only_open: True excluye tareas cerradas (done/canceled).
        """
        domain: list = []
        if project_id:
            domain.append(("project_id", "=", project_id))
        if user_id:
            domain.append(("user_ids", "in", [user_id]))
        if stage_id:
            domain.append(("stage_id", "=", stage_id))
        if state:
            domain.append(("state", "=", state))
        if only_open:
            domain.append(("state", "not in", ["1_done", "1_canceled"]))

        rows = odoo.search_read(
            "project.task",
            domain=domain,
            fields=[
                "id",
                "name",
                "project_id",
                "stage_id",
                "state",
                "user_ids",
                "date_deadline",
                "parent_id",
                "child_ids",
                "priority",
                "tag_ids",
            ],
            limit=limit,
            order="priority desc, date_deadline asc, id desc",
        )
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "project": _m2o(r.get("project_id")),
                "stage": _m2o(r.get("stage_id")),
                "state": r.get("state"),
                "assignee_ids": r.get("user_ids") or [],
                "deadline": r.get("date_deadline") or None,
                "parent": _m2o(r.get("parent_id")),
                "subtask_ids": r.get("child_ids") or [],
                "priority": r.get("priority"),
                "tag_ids": r.get("tag_ids") or [],
            }
            for r in rows
        ]

    @mcp.tool()
    def get_task(
        task_id: int,
        include_messages: bool = True,
        message_limit: int = 20,
    ) -> dict:
        """Detalle completo de una tarea: descripción, asignados, padre, hijas,
        etiquetas, milestone y opcionalmente los últimos mensajes del chatter.
        """
        rows = odoo.read(
            "project.task",
            [task_id],
            [
                "id",
                "name",
                "description",
                "project_id",
                "stage_id",
                "state",
                "user_ids",
                "date_deadline",
                "parent_id",
                "child_ids",
                "priority",
                "tag_ids",
                "create_date",
                "write_date",
                "partner_id",
                "milestone_id",
            ],
        )
        if not rows:
            raise ValueError(f"Tarea {task_id} no existe o no tenés acceso")
        t = rows[0]

        assignees: list[dict] = []
        if t.get("user_ids"):
            assignees = odoo.read("res.users", t["user_ids"], ["id", "name", "login"])

        tags: list[dict] = []
        if t.get("tag_ids"):
            tags = odoo.read("project.tags", t["tag_ids"], ["id", "name", "color"])

        out: dict = {
            "id": t["id"],
            "name": t["name"],
            "project": _m2o(t.get("project_id")),
            "stage": _m2o(t.get("stage_id")),
            "state": t.get("state"),
            "priority": t.get("priority"),
            "deadline": t.get("date_deadline") or None,
            "created": t.get("create_date"),
            "updated": t.get("write_date"),
            "partner": _m2o(t.get("partner_id")),
            "milestone": _m2o(t.get("milestone_id")),
            "parent": _m2o(t.get("parent_id")),
            "subtask_ids": t.get("child_ids") or [],
            "assignees": assignees,
            "tags": tags,
            "description": _strip_html(t.get("description")),
        }

        if include_messages:
            out["messages"] = list_task_messages(task_id, limit=message_limit)

        return out

    @mcp.tool()
    def list_task_attachments(task_id: int) -> list[dict]:
        """Adjuntos de una tarea (metadatos: nombre, mimetype, tamaño, autor)."""
        rows = odoo.search_read(
            "ir.attachment",
            domain=[
                ("res_model", "=", "project.task"),
                ("res_id", "=", task_id),
            ],
            fields=["id", "name", "mimetype", "file_size", "create_date", "create_uid"],
            order="create_date desc",
        )
        return rows

    @mcp.tool()
    def get_task_tree(project_id: int, max_depth: int = 5) -> dict:
        """Árbol jerárquico de tareas de un proyecto: raíces y subtareas recursivamente.

        Útil para tener vista general de la organización del proyecto.
        """
        rows = odoo.search_read(
            "project.task",
            domain=[("project_id", "=", project_id)],
            fields=["id", "name", "parent_id", "stage_id", "state", "user_ids"],
            limit=2000,
            order="id asc",
        )
        by_id = {r["id"]: r for r in rows}
        children: dict[int | None, list[int]] = {}
        for r in rows:
            parent = r["parent_id"][0] if r.get("parent_id") else None
            children.setdefault(parent, []).append(r["id"])

        def build(node_id: int, depth: int) -> dict:
            r = by_id[node_id]
            node = {
                "id": r["id"],
                "name": r["name"],
                "stage": _m2o(r.get("stage_id")),
                "state": r.get("state"),
                "assignee_ids": r.get("user_ids") or [],
                "children": [],
            }
            if depth < max_depth:
                for child_id in children.get(node_id, []):
                    if child_id in by_id:
                        node["children"].append(build(child_id, depth + 1))
            return node

        roots = [build(rid, 0) for rid in children.get(None, []) if rid in by_id]
        return {"project_id": project_id, "task_count": len(rows), "tree": roots}

    @mcp.tool()
    def list_my_activities(limit: int = 50) -> list[dict]:
        """Actividades pendientes (mail.activity) asignadas al usuario autenticado.

        Incluye actividades sobre cualquier modelo, no solo tareas. Ordenadas por deadline.
        """
        rows = odoo.search_read(
            "mail.activity",
            domain=[("user_id", "=", odoo.uid)],
            fields=[
                "id",
                "summary",
                "note",
                "date_deadline",
                "res_model",
                "res_id",
                "res_name",
                "activity_type_id",
                "state",
            ],
            limit=limit,
            order="date_deadline asc",
        )
        return [
            {
                "id": r["id"],
                "summary": r.get("summary") or None,
                "note": _strip_html(r.get("note")),
                "deadline": r.get("date_deadline") or None,
                "state": r.get("state"),
                "type": _m2o(r.get("activity_type_id")),
                "linked": {
                    "model": r.get("res_model"),
                    "id": r.get("res_id"),
                    "name": r.get("res_name"),
                },
            }
            for r in rows
        ]

    @mcp.tool()
    def search_tasks(query: str, limit: int = 20) -> list[dict]:
        """Búsqueda de tareas por texto en nombre o descripción."""
        domain = [
            "|",
            ("name", "ilike", query),
            ("description", "ilike", query),
        ]
        rows = odoo.search_read(
            "project.task",
            domain=domain,
            fields=["id", "name", "project_id", "stage_id", "state", "user_ids"],
            limit=limit,
            order="write_date desc",
        )
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "project": _m2o(r.get("project_id")),
                "stage": _m2o(r.get("stage_id")),
                "state": r.get("state"),
                "assignee_ids": r.get("user_ids") or [],
            }
            for r in rows
        ]
