import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    url: str
    db: str
    username: str
    api_key: str
    transport: str
    host: str
    port: int


def load_config() -> Config:
    load_dotenv()

    def required(key: str) -> str:
        v = os.environ.get(key)
        if not v:
            raise RuntimeError(f"Falta variable de entorno: {key}")
        return v

    return Config(
        url=required("ODOO_URL").rstrip("/"),
        db=required("ODOO_DB"),
        username=required("ODOO_USERNAME"),
        api_key=required("ODOO_API_KEY"),
        transport=os.environ.get("MCP_TRANSPORT", "stdio").lower(),
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8765")),
    )
