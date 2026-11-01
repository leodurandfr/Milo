"""Request gate for port 8001: who may *send* a request to a satellite.

There is no authentication here, as on the server: network position is the
authority, and what that leaves open is a page driving a browser on the LAN.
`POST /app/update` is a multipart form and `POST /api/hardware/reboot` has no
body, so both are CORS simple requests, sent with no preflight. The first hands
a tarball to the deploy wrapper, which installs its `rootfs/` as root, sudoers
included; that the page cannot read the reply changes nothing.

The server's twin is `backend/api/middleware.py::RequestOriginGate`. This one is
rewritten, not imported, because a satellite update ships `milo-client/` alone
(CLAUDE.md invariant 2). It is also stricter: the server has browsers among its
clients, a satellite has none. Its one caller is the server, over aiohttp, which
dials an IP literal (or `<hostname>.local`) and sends no `Origin`.

1. **`Host`, on every method.** Under DNS rebinding the page is same-origin to
   its browser and sends no `Origin`, but the Host header still names the
   attacker's domain. Accepted: `localhost`, `milo-client` and the
   `milo-client-<n>` Avahi falls back to, with or without `.local`, and any
   private address literal (dialling a number resolves no name).
2. **`Origin`, on a state-changing method.** Refused whatever it says: a browser
   sends one on every POST/PUT/PATCH/DELETE, and no page writes here.

`X-Forwarded-*` is never read. Nothing proxies this port, so such a header is
the sender's own claim.
"""
import ipaddress
import logging
import re
from typing import Optional
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_SATELLITE_NAME = re.compile(r"^(localhost|milo-client(-\d+)?(\.local)?)$")


def _host_name(raw_host: str) -> Optional[str]:
    """Bare lowercase host of a `Host` header, port and IPv6 brackets dropped."""
    try:
        return urlsplit(f"//{raw_host}").hostname
    except ValueError:  # a malformed IPv6 literal is not a host we answer for
        return None


def _is_satellite_host(host: Optional[str]) -> bool:
    """True for a name or address by which the server reaches this unit."""
    if not host:
        return False
    if _SATELLITE_NAME.match(host):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    # Not 100.64/10, which `is_private` leaves out: a carrier-grade NAT address
    # is not a way onto the LAN.
    return address.is_private


def _rejection(request: Request) -> str:
    """Why this request is not the server's — empty when it is."""
    raw_host = request.headers.get("host", "")
    if not _is_satellite_host(_host_name(raw_host)):
        return f"untrusted Host {raw_host!r}"

    origin = request.headers.get("origin")
    if origin is not None and request.method in _STATE_CHANGING_METHODS:
        return f"a browser's Origin {origin!r} on a write"

    return ""


class OriginGate:
    """Rejects browser-driven writes and DNS-rebound requests with 403.

    Pure ASGI, like the server's: it reads two headers and either passes through
    or answers, and `BaseHTTPMiddleware` would wrap every response in a task
    group for nothing. HTTP only, because the app serves no WebSocket: a route
    that adds one needs the server's `websocket` branch here too.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            reason = _rejection(request)
            if reason:
                logger.warning("Refused %s %s: %s", request.method, request.url.path, reason)
                response = JSONResponse(
                    status_code=403,
                    content={"status": "error", "message": "Untrusted request origin"},
                )
                await response(scope, receive, send)
                return

        await self.app(scope, receive, send)
