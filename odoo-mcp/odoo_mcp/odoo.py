import xmlrpc.client
from functools import cached_property
from typing import Any


class OdooClient:
    """Cliente XML-RPC mínimo para Odoo. Autentica perezosamente al primer uso."""

    def __init__(self, url: str, db: str, username: str, api_key: str):
        self.url = url
        self.db = db
        self.username = username
        self.api_key = api_key
        self._uid: int | None = None

    @cached_property
    def _common(self) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common", allow_none=True)

    @cached_property
    def _models(self) -> xmlrpc.client.ServerProxy:
        return xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object", allow_none=True)

    @property
    def uid(self) -> int:
        if self._uid is None:
            uid = self._common.authenticate(self.db, self.username, self.api_key, {})
            if not uid:
                raise RuntimeError(
                    f"Autenticación Odoo fallida en {self.url} (db={self.db}, user={self.username})"
                )
            self._uid = uid
        return self._uid

    def execute(
        self,
        model: str,
        method: str,
        args: list | None = None,
        kwargs: dict | None = None,
    ) -> Any:
        return self._models.execute_kw(
            self.db,
            self.uid,
            self.api_key,
            model,
            method,
            args or [],
            kwargs or {},
        )

    def search_read(
        self,
        model: str,
        domain: list | None = None,
        fields: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        order: str | None = None,
    ) -> list[dict]:
        kw: dict[str, Any] = {"offset": offset}
        if fields:
            kw["fields"] = fields
        if limit:
            kw["limit"] = limit
        if order:
            kw["order"] = order
        return self.execute(model, "search_read", [domain or []], kw)

    def read(
        self,
        model: str,
        ids: list[int],
        fields: list[str] | None = None,
    ) -> list[dict]:
        kw: dict[str, Any] = {}
        if fields:
            kw["fields"] = fields
        return self.execute(model, "read", [ids], kw)
