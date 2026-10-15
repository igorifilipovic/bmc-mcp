"""Bearer-token authentication for the MCP endpoint (pure ASGI middleware)."""
from __future__ import annotations

import hashlib
import hmac
import json
import os

MIN_TOKEN_LENGTH = 24


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def load_token_digests() -> list[bytes]:
    """Accepted tokens, kept only as SHA-256 digests in memory.

    Sources: MCP_AUTH_TOKENS (comma separated) and MCP_AUTH_TOKENS_FILE (one per line).
    An entry may be a plain token or `sha256:<hex digest>`.
    """
    items = [x.strip() for x in os.environ.get("MCP_AUTH_TOKENS", "").split(",") if x.strip()]
    token_file = os.environ.get("MCP_AUTH_TOKENS_FILE")
    if token_file:
        with open(token_file) as fh:
            items += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]

    digests: list[bytes] = []
    for item in items:
        if item.startswith("sha256:"):
            digests.append(bytes.fromhex(item[len("sha256:"):]))
        else:
            if len(item) < MIN_TOKEN_LENGTH:
                raise ValueError(
                    f"Auth token too short (minimum {MIN_TOKEN_LENGTH} characters). "
                    "Generate one with: openssl rand -hex 32"
                )
            digests.append(_digest(item))
    if not digests:
        raise ValueError("No auth tokens configured (set MCP_AUTH_TOKENS or MCP_AUTH_TOKENS_FILE)")
    return digests


class BearerAuthMiddleware:
    def __init__(self, app, digests: list[bytes]):
        self.app = app
        self.digests = digests

    async def _respond(self, send, status: int, body: dict, extra_headers=()):
        payload = json.dumps(body).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                *extra_headers,
            ],
        })
        await send({"type": "http.response.body", "body": payload})

    def _valid(self, token: str) -> bool:
        provided = _digest(token)
        ok = False
        for d in self.digests:  # no early exit
            ok |= hmac.compare_digest(provided, d)
        return ok

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Unauthenticated liveness probe for the container healthcheck; reveals nothing.
        if scope["path"] == "/healthz":
            await self._respond(send, 200, {"status": "ok"})
            return

        header = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        scheme, _, token = header.partition(" ")
        if scheme.lower() == "bearer" and token.strip() and self._valid(token.strip()):
            await self.app(scope, receive, send)
            return

        await self._respond(
            send,
            401,
            {"error": "unauthorized"},
            [(b"www-authenticate", b'Bearer realm="bmc-mcp"')],
        )
