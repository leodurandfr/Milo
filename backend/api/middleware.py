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
`http://milo.home` from the router's DNS. An allowlist of four literal origins would
have locked all four out.

The `/ws` upgrade passes the same Host check, and the Origin check whatever it asks
for. The socket changes nothing — `ws/manager.py` discards every inbound frame after
the `ready` handshake — but it pushes the whole state to whoever holds it, and CORS
does not cover a WebSocket: a page served from the internet that opens
`ws://milo.local/ws` sends a correct Host and reads every frame. Its browser still
names it in `Origin`, which it sends on every upgrade. A native client sends none,
or the unit's own name, and passes either way.

`client_address`, `RecoveryAccessPointGuard` and `RecoveryClientTracker`, at the end,
answer another question about the same request — whether it comes from someone on the
recovery access point, and what such a client may do.
"""
import ipaddress
import logging
import re
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketClose

from backend.config.constants import RECOVERY_AP_REFUSAL, RECOVERY_AP_SUBNET
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

# `milo`, `milo.local`, the `milo-2.local` Avahi falls back to when a second server
# claims the name (core/system/hostname_conflict.py), and the name a router's DNS
# gives the unit's DHCP lease. Every suffix is one no outside party can make resolve:
# `.local` is reserved by RFC 6762, `.home.arpa` by RFC 8375 and `.internal` by ICANN,
# while `.lan`, `.home` and `.localdomain` are not delegated. `.box` (fritz.box) is a
# delegated public TLD, so `milo.fritz.box` is refused.
_APPLIANCE_NAME = re.compile(
    rf"^(localhost|{EXPECTED_SERVER_HOSTNAME}(-\d+)?(\.(local|lan|home|home\.arpa|internal|localdomain))?)$"
)


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
    # Any *local* address literal, not only ours: dialling a number resolves no name,
    # so rebinding has nothing to rebind. The range test keeps out the literal that is
    # not a local one — an attacker serving the page from their own public IP. It also
    # keeps out 100.64/10, which `is_private` does not cover: the appliance is a LAN
    # device, and a carrier-grade NAT address is not a way onto it.
    return address.is_private


def _is_trusted_origin(origin: str) -> bool:
    """Everything CORS lets read a response, plus every name the appliance is served under.

    The first clause is what keeps the two surfaces in step: an origin added to
    `ALLOWED_ORIGINS` may read *and* write, rather than reading while its writes 403.
    """
    return origin in ALLOWED_ORIGINS or _is_appliance_host(_hostname(origin))


class RequestOriginGate:
    """Rejects cross-site and DNS-rebound requests with 403, and such upgrades of `/ws`.

    Pure ASGI rather than `BaseHTTPMiddleware`: this reads two headers and either
    passes through or short-circuits, and wrapping every response in a task group
    would tax the streaming ones (artwork, the ~20 s multiroom toggle) for nothing.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            reason = self._rejection(request.headers, request.method in _STATE_CHANGING_METHODS)
            if reason:
                logger.warning("Refused %s %s: %s", request.method, request.url.path, reason)
                response = JSONResponse(
                    status_code=403,
                    content={"status": "error", "message": "Untrusted request origin"},
                )
                await response(scope, receive, send)
                return
        elif scope["type"] == "websocket":
            reason = self._rejection(Headers(scope=scope), checks_origin=True)
            if reason:
                logger.warning("Refused WebSocket %s: %s", scope["path"], reason)
                # A close before the accept is answered as an HTTP 403 to the upgrade.
                await WebSocketClose(code=1008)(scope, receive, send)
                return

        await self.app(scope, receive, send)

    @staticmethod
    def _rejection(headers: Headers, checks_origin: bool) -> str:
        """Why this request is not ours to serve — empty when it is."""
        raw_host = headers.get("host", "")
        if not _is_appliance_host(_hostname(f"//{raw_host}")):
            return f"untrusted Host {raw_host!r}"

        origin = headers.get("origin")
        if checks_origin and origin and not _is_trusted_origin(origin):
            return f"untrusted Origin {origin!r}"

        return ""


# The address nginx proxies from (`upstream milo_backend`, rootfs/etc/nginx/sites-enabled/milo).
_NGINX_PEER = "127.0.0.1"

# The subnet NetworkManager hands the clients of a `shared` connection; the recovery
# AP (core/network/service.py) is the only one.
_RECOVERY_AP_SUBNET = ipaddress.ip_network(RECOVERY_AP_SUBNET)


def client_address(scope) -> str | None:
    """Who sent this request: the client nginx names when nginx is the peer, else the peer.

    nginx puts the real client in `X-Real-IP`. The header is believed from nginx
    alone: uvicorn listens on 0.0.0.0, so anyone on the LAN can reach :8000 directly
    and send whatever header they like.
    """
    peer = scope.get("client")
    address = peer[0] if peer else None
    if address == _NGINX_PEER:
        return Headers(scope=scope).get("x-real-ip") or address
    return address


def is_recovery_ap_client(scope) -> bool:
    """Whether this request comes from a client of the unit's recovery access point."""
    try:
        return ipaddress.ip_address(client_address(scope)) in _RECOVERY_AP_SUBNET
    except ValueError:  # no peer, or one that is not an address (a test client)
        return False


_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# The writes a client of the AP may send while the unit is not set up — which lasts
# until its first link to a network once the wizard is through (api/setup.py), so a
# key mistyped over the AP is typed again there: the first-boot
# wizard's three (frontend/src/components/setup/, composables/useNetwork.js) and the
# adoption's one, which the adopting server sends straight to :8000 from a 10.42.0.x
# address (core/multiroom/wifi_adoption.py). Exact paths: none takes a parameter, so a
# path the router would dispatch to one of these four is that string and no other —
# held by tests/architecture/test_recovery_ap_guard.py, with every entry's existence.
PRE_SETUP_WRITES = frozenset({
    ("PUT", "/api/network/wifi/country"),
    ("POST", "/api/network/wifi/save"),
    ("POST", "/api/setup/complete"),
    ("POST", "/api/setup/become-client"),
})

# The writes a client of the unit's own AP may send once the unit is set up: saving
# a WiFi network, the one repair of a unit with no screen and no Ethernet whose home
# network changed (Settings › Network, composables/useNetwork.js). Saved, then
# tried: with `try_now`, which Settings › Network always sends over the AP, the AP
# goes down under the client that sent it `STATION_TRY_AFTER_SAVE_S` after the
# answer and the unit joins the network saved, the AP coming back only if that
# fails; without it, the home-network retry (`STATION_RETRY_S`,
# core/network/service.py) tries it at its next turn, ahead of those saved before
# it. So a client of the AP can move a unit set up onto a network of its choice
# (SECURITY.md, Known limits). Spelled as PRE_SETUP_WRITES is, and held the same
# way.
SET_UP_WRITES = frozenset({
    ("POST", "/api/network/wifi/save"),
})


class RecoveryAccessPointGuard:
    """Refuses, with 403, every write a client of the recovery access point sends
    but the few listed above.

    The AP is open — brcmfmac offers neither OWE nor SAE in AP mode — so its clients
    are anyone in radio range of an outage, and this API authenticates nobody. The
    same holds while an adoption takes this unit onto a speaker's open hotspot, where
    it is one client among whoever else joined (`NetworkService.open_ap_reachable`,
    which a refusal is judged on). Such a
    client may read (GET, HEAD, OPTIONS, and `/ws`, which takes no command once its
    `ready` handshake is in) and send `PRE_SETUP_WRITES` before setup. A client of
    the unit's own AP (`NetworkService.recovery_ap_possible`) may also send
    `SET_UP_WRITES` once the unit is set up. Nothing else: everything else a unit
    set up needs is repaired from its touch screen (the kiosk is a loopback client,
    never one of the AP's) or over Ethernet.

    Never a speaker's hotspot: while an adoption holds this unit on one, nobody
    legitimate saves this unit's WiFi from there, and `save_network`, with no AP
    of the unit's own to try it from, deletes the saved name's other profiles.

    Deny by default, in one place, rather than a dependency per route: the per-route
    gate this replaced guarded three routes and left WiFi, power, hardware and
    updates open. A request is judged as the router will see it
    (`scope["path"]`, decoded, matched verbatim), and anything that is not exactly an
    entry — a trailing slash, a doubled slash, another case — is refused, never let
    through. Without a network service (a dev host) no AP exists and it passes; a
    settings.json that could not be read (`SettingsService.read_failed`) counts as
    set up, since the defaults served in its place say "not set up" of any unit.

    One warning per client per AP episode: a phone left on the AP refused every write
    it tries would otherwise fill errors.log. The episode ends when a request finds
    the AP no longer possible, which is all this guard can see of it.

    Installed outside `RecoveryClientTracker`, so a refused request never counts as
    someone using the AP, and inside CORS, so an admitted reader reads the refusal
    like any other answer.
    """

    def __init__(self, app, network_service, settings_service):
        self.app = app
        self.network_service = network_service
        self.settings_service = settings_service
        self._warned: set[str] = set()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.network_service is None:
            await self.app(scope, receive, send)
            return
        if not self.network_service.open_ap_reachable:
            self._warned.clear()
            await self.app(scope, receive, send)
            return
        if scope["method"] in _READ_METHODS or not is_recovery_ap_client(scope):
            await self.app(scope, receive, send)
            return
        write = (scope["method"], scope["path"])
        if write in PRE_SETUP_WRITES and not await self._set_up():
            await self.app(scope, receive, send)
            return
        if write in SET_UP_WRITES and self.network_service.recovery_ap_possible:
            await self.app(scope, receive, send)
            return

        address = client_address(scope)
        if address in self._warned:
            logger.debug("Refused %s %s to %s", scope["method"], scope["path"], address)
        else:
            self._warned.add(address)
            logger.warning(
                "Refused %s %s to %s, a client of the recovery access point"
                " (its further refusals are logged at debug until the AP comes down)",
                scope["method"], scope["path"], address,
            )
        await JSONResponse(status_code=403, content={"detail": RECOVERY_AP_REFUSAL})(
            scope, receive, send,
        )

    async def _set_up(self) -> bool:
        """Whether the unit is set up; a settings.json that could not be read counts
        as set up.

        `get_setting` never raises: a failed read serves the defaults, whose
        `setup_completed` is False, and says so in `read_failed`, asked after it.
        The failed read is SettingsService's to report, once, at error, and the
        refusal it leads to is warned of once per client like any other, so it is
        only a debug line here: a warning per write would let anyone in radio
        range rotate that error out of errors.log.
        """
        if self.settings_service is None:
            return True
        completed = await self.settings_service.get_setting("setup_completed")
        if self.settings_service.read_failed:
            logger.debug("Recovery AP guard: settings.json could not be read, counted as set up")
            return True
        return completed is not False


class RecoveryClientTracker:
    """Tells the network service while a request or a socket from its access point is open.

    On a WiFi-only unit the recovery AP is taken down every few minutes to try the
    home network again (`STATION_RETRY_S`, core/network/service.py), and that must
    not happen under someone setting the unit up over it. The setup page can sit on
    `/ws` for minutes without one HTTP request, so a socket counts for as long as it
    is open. Installed inside both gates, so a request either refuses counts for
    nothing.
    """

    def __init__(self, app, network_service):
        self.app = app
        self.network_service = network_service

    async def __call__(self, scope, receive, send):
        if (
            self.network_service.hotspot_active
            and scope["type"] in ("http", "websocket")
            and is_recovery_ap_client(scope)
        ):
            with self.network_service.serving_recovery_client():
                await self.app(scope, receive, send)
            return
        await self.app(scope, receive, send)
