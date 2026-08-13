# backend/api/middleware.py
"""Request gate: CORS says who may *read* a response, never who may *send* a request.

There is no authentication here by design — network position is the authority.
What that leaves open is an off-LAN attacker driving an on-LAN browser: a bodyless
`POST http://milo.local/api/programs/milo/update` is a CORS *simple request*, so it
travels with no preflight and the appliance starts a self-update and reboots
mid-listening. That the attacker's page cannot read the reply changes nothing about
what already happened. Nineteen routes are shaped like that, `restart` and `shutdown`
among them.

Two checks, because either alone is bypassable:

1. **`Origin`, on a state-changing method.** A browser sets it on every non-GET
   request, so a foreign value is exact evidence of a cross-site call. A *missing*
   Origin is evidence of nothing — that is curl, and it is Milo-Mac, whose
   `URLRequest` sets `Content-Type` and nothing else (vendored snapshot,
   `MiloAPIService.swift:220`). Missing passes.
2. **`Host`, on every method.** Under DNS rebinding the attacker's own name resolves
   to the appliance, so the browser believes the request is same-origin and a read
   carries no `Origin` at all. The Host header still names what the victim's browser
   was pointed at, and that is never one of ours.

Both accept the same set of names and addresses, `_is_appliance_host`, which is wider
than the CORS allowlist on purpose: CORS only ever describes *cross*-origin readers,
while these checks also see every same-origin request — and those arrive under
whatever name the user reached the unit by. `http://localhost` for the Pi kiosk,
`http://192.168.1.x` when mDNS is down, `http://milo-2.local` after an Avahi rename,
the Tailscale address from a phone off the LAN. An allowlist of four literal origins
would have locked all four out.

The `/ws` upgrade does not pass through here — the gate is HTTP-scope only. It carries
no state-changing surface: `ws/manager.py` discards every inbound frame after the
`ready` handshake.
"""
import ipaddress
import logging
import re
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.core.system.hostname_conflict import EXPECTED_SERVER_HOSTNAME

logger = logging.getLogger(__name__)

# The cross-origin readers CORS admits. Read by `CORSMiddleware` and by the gate
# below, so the two cannot drift.
ALLOWED_ORIGINS = [
    "http://milo.local",
    "https://milo.local",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]

_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# `milo`, `milo.local`, and the `milo-2.local` Avahi falls back to when a second
# server claims the name (core/system/hostname_conflict.py).
_APPLIANCE_NAME = re.compile(rf"^(localhost|{EXPECTED_SERVER_HOSTNAME}(-\d+)?(\.local)?)$")

# Tailscale hands out 100.64/10, which `is_private` does not cover, and the unit is
# reached there from off the LAN.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")


def _hostname(url: str) -> str | None:
    """Bare lowercase host of an `Origin` value, or of a `Host` header given as `//host`."""
    try:
        return urlsplit(url).hostname
    except ValueError:  # a malformed IPv6 literal is not a host we answer for
        return None


def _is_appliance_host(host: str | None) -> bool:
    """True for a name or address by which this unit is legitimately reached."""
    if not host:
        return False
    if _APPLIANCE_NAME.match(host):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    # Any address literal, not only ours: dialling a number resolves no name, so
    # rebinding has nothing to rebind. The range test keeps out the one literal that
    # is not a local one — an attacker serving the page from their own public IP.
    return address.is_private or (address.version == 4 and address in _SHARED_ADDRESS_SPACE)


def _is_trusted_origin(origin: str) -> bool:
    """Everything CORS lets read a response, plus every name the appliance is served under.

    The first clause is what keeps the two surfaces in step: an origin added to
    `ALLOWED_ORIGINS` may read *and* write, rather than reading while its writes 403.
    """
    return origin in ALLOWED_ORIGINS or _is_appliance_host(_hostname(origin))


class RequestOriginGate:
    """Rejects cross-site and DNS-rebound requests with 403.

    Pure ASGI rather than `BaseHTTPMiddleware`: this reads two headers and either
    passes through or short-circuits, and wrapping every response in a task group
    would tax the streaming ones (artwork, the ~20 s multiroom toggle) for nothing.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        reason = self._rejection(request)
        if reason:
            logger.warning("Refused %s %s: %s", request.method, request.url.path, reason)
            response = JSONResponse(
                status_code=403,
                content={"status": "error", "message": "Untrusted request origin"},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _rejection(request: Request) -> str:
        """Why this request is not ours to serve — empty when it is."""
        raw_host = request.headers.get("host", "")
        if not _is_appliance_host(_hostname(f"//{raw_host}")):
            return f"untrusted Host {raw_host!r}"

        origin = request.headers.get("origin")
        if request.method in _STATE_CHANGING_METHODS and origin and not _is_trusted_origin(origin):
            return f"untrusted Origin {origin!r}"

        return ""
