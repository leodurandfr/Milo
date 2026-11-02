"""snapserver's JSON-RPC, as far as Milō drives it.

Served on an ephemeral loopback port (`serving`), or answered in process by an
`aiohttp.ClientSession` stand-in (`client_session`) — never snapserver's 1780,
which answers on this machine and reaches speakers in occupied rooms. The fake knows exactly the streams `provisioning/snapcast.sh`
declares and keeps the state snapserver persists in its server.json — one group
per client, each with its own stream and its own mute — and applies every
command to it, so a test reads what the rooms would hear rather than what was
sent. Every request is appended to `journal`, which a test may share with
whatever else it orders against.
"""
import asyncio
import copy
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from aiohttp import web

from backend.core.multiroom.snapcast import SnapcastService

PROVISIONING = Path(__file__).resolve().parents[2] / "provisioning" / "snapcast.sh"

LOCAL, CANAPE, BUREAU = "2c:cf:67:00:00:01", "dc:a6:32:7e:d3:43", "d8:3a:dd:68:e7:e4"
CLIENT_IPS = {LOCAL: "127.0.0.1", CANAPE: "192.168.1.153", BUREAU: "192.168.1.60"}


def shipped_stream_names():
    """Every stream snapserver.conf declares: the ten captures and the meta."""
    names = re.findall(
        r"^source\s*=\s*\w+:///[^\n]*?[?&]name=([^&\s]+)",
        PROVISIONING.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert len(names) >= 11, f"only parsed {names} from {PROVISIONING}"
    return set(names)


def _group(client_id, ip):
    return {"id": f"group-{client_id}", "name": "", "muted": False, "stream_id": "Multiroom",
            "clients": [_client(client_id, ip)]}


def _client(client_id, ip):
    return {
        "id": client_id,
        "connected": True,
        "config": {"name": "", "latency": 0, "volume": {"percent": 100, "muted": False}},
        "host": {"name": "milo-client", "ip": f"::ffff:{ip}", "mac": client_id},
        "lastSeen": {"sec": int(time.time()), "usec": 0},
    }


class FakeSnapserver:
    """Server.GetStatus and the Client.* / Group.* commands Milō sends.

    `clients` maps each client id to its address (`CLIENT_IPS` by default).
    `refuse` maps a method to how many of its next calls answer a JSON-RPC
    error (None: all of them). `on_request` runs as each request arrives, which
    is how a test lands something between a command and its answer.
    """

    def __init__(self, journal=None, streams=None, clients=None):
        self.journal = [] if journal is None else journal
        self.streams = shipped_stream_names() if streams is None else streams
        self.refuse = {}
        self.on_request = None
        self.hang = False
        self.released = asyncio.Event()
        self.port = None
        self.groups = [_group(client, ip) for client, ip in (CLIENT_IPS if clients is None else clients).items()]

    def group_of(self, client_id):
        return next(
            g for g in self.groups if any(c["id"] == client_id for c in g["clients"])
        )

    def client(self, client_id):
        return next(c for c in self.group_of(client_id)["clients"] if c["id"] == client_id)

    def stream_of(self, client_id):
        return self.group_of(client_id)["stream_id"]

    def is_muted(self, client_id):
        """What the room hears: snapserver sends a client `client.muted || group.muted`."""
        return self.client(client_id)["config"]["volume"]["muted"] or self.group_of(client_id)["muted"]

    def set_streams(self, stream_id):
        for group in self.groups:
            group["stream_id"] = stream_id

    def _refused(self, method):
        if method not in self.refuse:
            return False
        left = self.refuse[method]
        if left is not None:
            if left <= 0:
                return False
            self.refuse[method] = left - 1
        return True

    async def answer(self, body):
        """The JSON-RPC reply to one request body."""
        method, params = body["method"], body.get("params", {})
        self.journal.append(("rpc", method, params))
        if self.on_request is not None:
            self.on_request(method, params)
        if self.hang:
            await self.released.wait()

        def error(message):
            return {"id": body["id"], "jsonrpc": "2.0", "error": {"code": -32603, "message": message}}

        if self._refused(method):
            return error("refused")

        if method == "Server.GetRPCVersion":
            result = {"major": 2, "minor": 0, "patch": 0}
        elif method == "Server.GetStatus":
            result = {"server": {
                "groups": copy.deepcopy(self.groups),
                "streams": [{"id": name, "status": "idle"} for name in sorted(self.streams)],
            }}
        elif method in ("Client.SetVolume", "Client.SetLatency"):
            try:
                client = self.client(params.get("id"))
            except StopIteration:
                return error("Client not found")
            if method == "Client.SetVolume":
                client["config"]["volume"] = dict(params["volume"])
                result = {"volume": client["config"]["volume"]}
            else:
                client["config"]["latency"] = params["latency"]
                result = {"latency": params["latency"]}
        elif method in ("Group.SetMute", "Group.SetStream"):
            group = next((g for g in self.groups if g["id"] == params.get("id")), None)
            if group is None:
                return error("Group not found")
            if method == "Group.SetMute":
                group["muted"] = params["mute"]
                result = {"mute": params["mute"]}
            else:
                if params.get("stream_id") not in self.streams:
                    return error("Internal error")
                group["stream_id"] = params["stream_id"]
                result = {"stream_id": params["stream_id"]}
        else:
            return error("Method not found")
        return {"id": body["id"], "jsonrpc": "2.0", "result": result}

    async def handle(self, request):
        return web.json_response(await self.answer(await request.json()))

    def client_session(self, url, fallback):
        """An `aiohttp.ClientSession` stand-in answering `url` in process, as this
        snapserver, and handing every other request to a `fallback` session.

        For a test whose satellites are already doubled by replacing
        `aiohttp.ClientSession` (`no_satellite_network`): a real session to a
        served fake would be replaced too.
        """
        fake = self

        class _Response:
            status = 200

            def __init__(self, payload):
                self._payload = payload

            async def json(self):
                return self._payload

        class _Answer:
            def __init__(self, body):
                self._body = body

            async def __aenter__(self):
                return _Response(await fake.answer(self._body))

            async def __aexit__(self, *exc):
                return False

        class _Session:
            def __init__(self, *args, **kwargs):
                self._fallback = fallback(*args, **kwargs)

            async def __aenter__(self):
                await self._fallback.__aenter__()
                return self

            async def __aexit__(self, *exc):
                return await self._fallback.__aexit__(*exc)

            def post(self, target, json=None, **kwargs):
                if target == url:
                    return _Answer(json)
                return self._fallback.post(target, json=json, **kwargs)

            def get(self, target, **kwargs):
                return self._fallback.get(target, **kwargs)

            def put(self, target, **kwargs):
                return self._fallback.put(target, **kwargs)

        return _Session


@asynccontextmanager
async def serving(fake):
    """Serve `fake` on 127.0.0.1 until the block ends; its port is `fake.port`."""
    app = web.Application()
    app.router.add_post("/jsonrpc", fake.handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    fake.port = site._server.sockets[0].getsockname()[1]
    try:
        yield fake
    finally:
        fake.released.set()
        await runner.cleanup()


def listing_every_pinned_client(snapcast):
    """Make a `SnapcastService` double answer the admission of any client.

    For tests about something other than snapserver. Every command is
    acknowledged, and each client given a volume is listed live from then on,
    alone in an unmuted group on the meta stream: snapserver refuses a command
    for a client it does not know, so one it took the passthrough for is one it
    lists. Returns the double.
    """
    pinned = []

    async def set_volume(client_id, volume, muted=False):
        if client_id not in pinned:
            pinned.append(client_id)
        return True

    snapcast.set_volume = AsyncMock(side_effect=set_volume)
    snapcast.set_latency = AsyncMock(return_value=True)
    snapcast.get_server_status = AsyncMock(
        side_effect=lambda: {"server": {"groups": [_group(client, "192.168.1.2") for client in pinned]}}
    )
    snapcast.group_of = MagicMock(side_effect=SnapcastService.group_of)
    snapcast.set_group_mute = AsyncMock(return_value=True)
    snapcast.set_group_stream = AsyncMock(return_value=True)
    return snapcast
