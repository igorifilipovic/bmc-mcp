"""MCP server entrypoint: HTTPS + Bearer auth, read-only BMC tools."""
from __future__ import annotations

import asyncio
import os
from typing import Literal

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .auth import BearerAuthMiddleware, load_token_digests
from .config import BmcHost, load_hosts
from .redfish import RedfishError, get_events, open_session

PORT = int(os.environ.get("MCP_PORT", "8001"))
MAX_PAGES = int(os.environ.get("BMC_MAX_LOG_PAGES", "20"))

INSTRUCTIONS = (
    "Read-only access to server BMCs (iDRAC, iRMC, iLO, iLOM) via Redfish. "
    "Start with list_servers, then use get_power_state / get_health_summary for status "
    "and get_critical_events / get_event_logs for logs. Nothing here can change a server."
)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


def create_server(hosts: dict[str, BmcHost]) -> FastMCP:
    mcp = FastMCP(
        "bmc-readonly",
        instructions=INSTRUCTIONS,
        host="0.0.0.0",
        port=PORT,
        stateless_http=True,
        json_response=True,
    )

    def _host(name: str) -> BmcHost:
        for key, h in hosts.items():
            if key.lower() == name.strip().lower():
                return h
        raise ValueError(f"Unknown server '{name}'. Known servers: {', '.join(hosts)}")

    async def _power(host: BmcHost) -> dict:
        async with open_session(host) as rf:
            systems = await rf.systems()
        return {"server": host.name, "systems": systems}

    @mcp.tool(annotations=READ_ONLY)
    async def list_servers() -> list[dict]:
        """List the BMCs this service can query (name, vendor, address). No credentials are exposed."""
        return [
            {"name": h.name, "vendor": h.vendor, "address": h.address}
            for h in hosts.values()
        ]

    @mcp.tool(annotations=READ_ONLY)
    async def get_power_state(server: str) -> dict:
        """Power state (On/Off/...) and overall health of a server, as reported by its BMC."""
        data = await _power(_host(server))
        return {
            "server": data["server"],
            "systems": [
                {k: s[k] for k in ("id", "power_state", "state", "health", "health_rollup")}
                for s in data["systems"]
            ],
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_health_summary(server: str) -> dict:
        """Model, BIOS, power state and health (OK/Warning/Critical) for a server."""
        return await _power(_host(server))

    @mcp.tool(annotations=READ_ONLY)
    async def get_fleet_status() -> list[dict]:
        """Power state and health of ALL configured servers at once. Unreachable BMCs are reported with an error."""

        async def one(h: BmcHost) -> dict:
            try:
                data = await _power(h)
                s = data["systems"][0]
                return {
                    "server": h.name,
                    "power_state": s["power_state"],
                    "health": s["health"],
                    "health_rollup": s["health_rollup"],
                }
            except Exception as e:  # report, never fail the whole fleet call
                return {"server": h.name, "error": str(e)}

        return list(await asyncio.gather(*(one(h) for h in hosts.values())))

    @mcp.tool(annotations=READ_ONLY)
    async def list_log_services(server: str) -> list[dict]:
        """Log services exposed by the BMC (e.g. SEL, Lclog, IML, IEL). Use ids as log_service in get_event_logs."""
        host = _host(server)
        async with open_session(host) as rf:
            services = await rf.log_services()
        return [
            {k: s[k] for k in ("id", "name", "scope")} for s in services
        ]

    @mcp.tool(annotations=READ_ONLY)
    async def get_event_logs(
        server: str,
        log_service: str | None = None,
        limit: int = 50,
        min_severity: Literal["OK", "Warning", "Critical"] = "OK",
        since_hours: float | None = None,
    ) -> dict:
        """Event/log entries from a server's BMC, newest first.

        log_service: optional id/name filter (e.g. 'SEL'); default is all log services.
        min_severity: only entries at or above this level.
        since_hours: only entries from the last N hours.
        If fetch_truncated is true the log was larger than the page cap and may be incomplete.
        """
        limit = max(1, min(limit, 500))
        return await get_events(
            _host(server),
            log_service=log_service,
            limit=limit,
            min_severity=min_severity,
            since_hours=since_hours,
            max_pages=MAX_PAGES,
        )

    @mcp.tool(annotations=READ_ONLY)
    async def get_critical_events(
        server: str, since_hours: float = 24, limit: int = 50
    ) -> dict:
        """Entries with severity Critical from the last N hours (default 24) across all log services of a server."""
        limit = max(1, min(limit, 500))
        return await get_events(
            _host(server),
            log_service=None,
            limit=limit,
            min_severity="Critical",
            since_hours=since_hours,
            max_pages=MAX_PAGES,
        )

    return mcp


def main() -> None:
    hosts = load_hosts(os.environ.get("MCP_HOSTS_FILE", "/app/config/hosts.yaml"))
    digests = load_token_digests()
    cert = os.environ.get("MCP_TLS_CERT", "/certs/server.crt")
    key = os.environ.get("MCP_TLS_KEY", "/certs/server.key")

    app = create_server(hosts).streamable_http_app()  # served at /mcp
    app.add_middleware(BearerAuthMiddleware, digests=digests)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        ssl_certfile=cert,
        ssl_keyfile=key,
        log_level=os.environ.get("MCP_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
