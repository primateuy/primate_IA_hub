import sys

from mcp.server.fastmcp import FastMCP

from .config import load_config
from .odoo import OdooClient
from .tools import register_tools


def main() -> None:
    cfg = load_config()
    odoo = OdooClient(cfg.url, cfg.db, cfg.username, cfg.api_key)

    # Verifica autenticación al arrancar para fallar rápido con un mensaje útil.
    try:
        _ = odoo.uid
        print(
            f"[odoo-mcp] Conectado a {cfg.url} db={cfg.db} uid={odoo.uid}",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"[odoo-mcp] ERROR conectando a Odoo: {e}", file=sys.stderr)
        sys.exit(1)

    mcp = FastMCP("odoo-mcp", host=cfg.host, port=cfg.port)
    register_tools(mcp, odoo)

    if cfg.transport == "stdio":
        mcp.run(transport="stdio")
    elif cfg.transport in ("sse", "http", "streamable-http"):
        mcp.run(transport="sse")
    else:
        print(f"[odoo-mcp] MCP_TRANSPORT desconocido: {cfg.transport}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
