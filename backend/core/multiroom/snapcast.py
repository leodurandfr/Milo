# backend/core/multiroom/snapcast.py
"""
Snapcast REST service - JSON-RPC commands to Snapcast server.

This service handles all REST commands to the Snapcast server. WebSocket
notifications are handled separately by SnapcastWebSocketService.
"""
import asyncio
import contextlib
import difflib
import logging
import os
import tempfile
import time
from typing import List, Dict, Any, Optional
from pathlib import Path

import aiohttp
import aiofiles

from backend.config.constants import get_client_display_name, DEPLOY_UPDATE_CMD, MULTIROOM_STREAMS
from backend.core.multiroom.identity import compute_mac_id, is_stale_local_client
from backend.core.multiroom.routing import DEFAULT_SNAPCLIENT_CONFIG
from backend.shared.decorators import handle_errors

# Codec whitelist — single source for validate_config and the /server-config
# capabilities payload the frontend builds its codec options from.
SUPPORTED_CODECS = ["flac", "pcm", "opus", "ogg"]

# The `source =` lines of snapserver.conf's [stream]: the `Multiroom` meta stream
# (`default_source`), then one capture per source, all from the one source ->
# stream table. Written over whatever the file holds, by every write of it.
MULTIROOM_STREAM_SOURCES = (
    f"meta:///{'/'.join(s.name for s in MULTIROOM_STREAMS.values())}?name=Multiroom",
    *(f"alsa:///?name={s.name}&device={s.device}&idle_threshold=5000" for s in MULTIROOM_STREAMS.values()),
)

# Where snapserver's JSON-RPC listens and the backend dials, on loopback: it
# answers any client with no authentication and no Origin check, and a group it
# mutes or moves stays so in server.json across restarts -- from the LAN, a page
# open in any browser could silence or reroute a room for good.
SNAPSERVER_CONTROL_ADDRESS = "127.0.0.1"
SNAPSERVER_CONTROL_PORT = 1780

# The sections of snapserver.conf that open a control port, the backend's whole:
# JSON-RPC over HTTP and WebSocket on loopback, with no web page (snapweb) and
# no mDNS record of a port nobody else can reach, and the TCP control port
# (1705) closed. Written over whatever the file holds, by every write of it.
# The stream port (1704, `[stream]`/`[tcp-streaming]`) is the satellites' and
# keeps its own lines.
SNAPSERVER_CONTROL_SECTIONS = {
    "http": (
        ("enabled", "true"),
        ("bind_to_address", SNAPSERVER_CONTROL_ADDRESS),
        ("port", str(SNAPSERVER_CONTROL_PORT)),
        ("publish_http", "false"),
    ),
    "tcp-control": (("enabled", "false"),),
}

# Accepted ranges for the two numeric stream parameters, declared here because
# validate_config is what enforces them. `core/multiroom/calibration.py` clamps
# its computed values against these same tuples rather than restating the
# bounds: a proposal outside them would be a payload this very method rejects.
BUFFER_MS_RANGE = (150, 3000)
CHUNK_MS_RANGE = (15, 50)

# Where snapserver.conf is staged for milo-deploy-update, which takes its source
# only from /tmp or /var/tmp: the `milo` user cannot write /etc.
STAGING_DIR = "/tmp"


def _stage(content: str) -> str:
    """Write `content` to a file of its own under STAGING_DIR and return its
    path. Blocking: run it off the event loop.

    World-readable, as a plain open() made it: mkstemp's 0600 would reach /etc
    through the helper's `cp` wherever it creates the destination rather than
    overwriting it, and snapserver does not run as root.
    """
    fd, path = tempfile.mkstemp(prefix="snapserver.conf.", dir=STAGING_DIR)
    with os.fdopen(fd, "w") as f:
        os.fchmod(f.fileno(), 0o644)
        f.write(content)
    return path


class SnapcastRequestError(RuntimeError):
    """A Snapcast JSON-RPC request failed at the transport/protocol layer.

    Raised by ``SnapcastService._request`` so callers can distinguish a real
    failure (unreachable, timeout, non-200, JSON-RPC error) from a valid but
    empty ``result`` ({}). Fail-open callers absorb it via @handle_errors or a
    scoped except; command callers let it surface as a False return.
    """

# The one canned configuration the UI offers, beside the automatic analysis and
# manual editing. It is not a fourth opinion: it is exactly what a freshly
# flashed unit runs, so selecting it means "put this back as it came".
#
# There used to be three -- 'responsive', 'balanced', 'robust' -- and their
# names were the whole problem: nothing in "robust" tells a person what it costs
# or when to want it. The analysis now measures the house and computes all four
# values, which is what those three were approximating by hand.
#
# Dropping them also exposed something they had been hiding: the shipped values
# matched none of them, so a unit nobody had ever configured showed no selected
# preset at all.
#
# `provisioning/snapcast.sh` is what actually writes these into snapserver.conf
# at image build, and it cannot import Python -- the two are pinned together by
# tests/architecture/test_default_preset.py.
NETWORK_PRESETS = [
    {"id": "default",
     "config": {"buffer_ms": 300, "codec": "flac", "chunk_ms": 40,
                "snapclient_buffer_time": DEFAULT_SNAPCLIENT_CONFIG["buffer_time"]}},
]


class SnapcastService:
    """
    Snapcast REST service for JSON-RPC commands.

    Handles all direct communication with Snapcast server via HTTP/JSON-RPC.
    WebSocket notifications are handled by SnapcastWebSocketService.
    """

    # A client that dies without closing its socket (power cut, Wi-Fi drop) stays
    # `connected: true` in snapserver forever, so liveness is read from lastSeen
    # instead. snapclient stamps it ~1x/s via its time-sync messages — measured
    # age stays under 0.7s on a healthy LAN client, playing or idle — so 20s is
    # ~30x the nominal margin: wide enough to absorb a brief Wi-Fi hiccup without
    # flapping the client offline, and the smallest of the two terms that decide
    # detection latency (the other is SnapcastWebSocketService.RECONCILE_INTERVAL_S).
    LAST_SEEN_FRESHNESS_S = 20

    # snapweb's player calls itself "Snapweb client": a client whose name holds
    # this, in any case, is no speaker (is_speaker_entry).
    SNAPWEB_NAME = "snapweb"

    # How long an idle connection to snapserver stays in the pool. snapserver
    # logs every control session the client closes as an `[Error]` (`Failed to
    # shudown socket`), so this must outlast the reconcile sweep's interval
    # (SnapcastWebSocketService.RECONCILE_INTERVAL_S), its steady caller:
    # aiohttp's 15 s default closed the connection between two sweeps.
    KEEPALIVE_S = 75

    def __init__(self, systemd_manager, host: str = "localhost", port: int = 1780):
        self.base_url = f"http://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)
        self._systemd = systemd_manager
        self._request_id = 0
        self._session: Optional[aiohttp.ClientSession] = None
        self.snapserver_conf = Path("/etc/snapserver.conf")

    def _get_session(self) -> aiohttp.ClientSession:
        """Return the shared HTTP session, created on first use.

        Never recreated: after a snapserver restart the pool drops the dead
        connection and dials the new daemon by itself, and after `cleanup()` a
        request fails rather than opening a session nothing would close.
        """
        if self._session is None:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(keepalive_timeout=self.KEEPALIVE_S)
            )
        return self._session

    async def cleanup(self) -> None:
        """Close the shared HTTP session for good. Called from app shutdown."""
        if self._session is not None:
            await self._session.close()

    async def _request(self, method: str, params: dict = None) -> dict:
        """Execute a JSON-RPC request and return its ``result`` field.

        Raises SnapcastRequestError on any transport/protocol failure
        (unreachable, timeout, non-200, JSON-RPC error) so callers can tell a
        real failure apart from a valid-but-empty ``result`` ({}) — a bare
        ``bool(result)`` on a swallowed {} could not. Fail-open callers wrap
        this with @handle_errors / a scoped except; do not swallow here.
        """
        self._request_id += 1
        request = {"id": self._request_id, "jsonrpc": "2.0", "method": method}
        if params:
            request["params"] = params

        timeout = aiohttp.ClientTimeout(total=3)
        start = time.time()
        try:
            session = self._get_session()
            async with session.post(self.base_url, json=request, timeout=timeout) as response:
                elapsed = (time.time() - start) * 1000
                if elapsed > 500:
                    self.logger.debug(f"SNAPCAST_SLOW: {method} took {elapsed:.0f}ms")
                if response.status != 200:
                    raise SnapcastRequestError(f"{method}: HTTP {response.status}")
                data = await response.json()
        except SnapcastRequestError:
            raise
        except Exception as e:
            raise SnapcastRequestError(f"{method}: {type(e).__name__}: {e}") from e

        if "error" in data:
            raise SnapcastRequestError(f"{method}: {data['error']}")
        return data.get("result", {})

    # === CLIENT COMMANDS ===

    @handle_errors(default=False, level='warning')
    async def set_volume(self, client_id: str, volume: int, muted: bool = False) -> bool:
        """Set a client's volume (0-100).

        Args:
            client_id: Snapcast client ID
            volume: Volume percentage (0-100)
            muted: Mute state to set (default False = unmuted passthrough)
        """
        self.logger.info(f"SNAPCAST_SET_VOLUME: client={client_id}, volume={volume}%, muted={muted}")
        await self._request("Client.SetVolume", {
            "id": client_id,
            "volume": {"percent": max(0, min(100, volume)), "muted": muted}
        })
        return True

    @handle_errors(default=False, level='warning')
    async def set_latency(self, client_id: str, ms: int) -> bool:
        """Set a client's native Snapcast latency (playback delay) in ms.

        This is the per-client delay Milō owns as its source of truth (mirror of
        set_volume): applied when the user changes it and re-pushed on admission
        so a reconnecting client recovers it. It is Snapcast's own latency, NOT a
        CamillaDSP filter.

        Args:
            client_id: Snapcast client id (== the client's mac_id by construction)
            ms: Latency in milliseconds
        """
        self.logger.info(f"SNAPCAST_SET_LATENCY: client={client_id}, latency={ms}ms")
        await self._request("Client.SetLatency", {
            "id": client_id,
            "latency": max(0, ms),
        })
        return True

    # === GROUP COMMANDS ===

    @handle_errors(default=False, level='warning')
    async def set_group_mute(self, group_id: str, muted: bool) -> bool:
        """Mute or unmute one snapserver group (JSON-RPC `Group.SetMute`)."""
        self.logger.info(f"SNAPCAST_SET_GROUP_MUTE: group={group_id}, muted={muted}")
        await self._request("Group.SetMute", {"id": group_id, "mute": muted})
        return True

    @handle_errors(default=False, level='warning')
    async def set_group_stream(self, group_id: str, stream_id: str) -> bool:
        """Point one snapserver group at a stream (JSON-RPC `Group.SetStream`)."""
        await self._request("Group.SetStream", {"id": group_id, "stream_id": stream_id})
        return True

    @handle_errors(default=False, level='warning')
    async def point_groups_at(self, stream_id: str) -> bool:
        """Point every group at `stream_id`.

        Groups already on it are left alone, so snapserver re-sends nothing to a
        room that is right. True only when every group that had to move did.
        """
        status = await self._request("Server.GetStatus")
        groups = status.get("server", {}).get("groups", [])

        moves = []
        for group in groups:
            old = group.get("stream_id")
            if old == stream_id:
                continue
            self.logger.info(f"SNAPCAST_SET_STREAM: group={group['id']} {old} -> {stream_id}")
            moves.append(self.set_group_stream(group["id"], stream_id))
        return all(await asyncio.gather(*moves))

    # === CLIENT QUERIES ===

    @handle_errors(default=[])
    async def get_clients(self) -> List[Dict[str, Any]]:
        """Get clients with MAC-based deduplication and availability detection.

        Fail-open: flattens a request failure to []. Its one caller is the
        diagnostic, which only lists. Callers that would read an empty list as
        "every client vanished" — every path in SnapcastWebSocketService — fetch
        the status themselves and use ``extract_clients`` instead.
        """
        status = await self._request("Server.GetStatus")
        return self.extract_clients(status)

    def _parse_clients(self, status: dict) -> List[Dict[str, Any]]:
        """
        Parse online clients from server status.

        Only live clients are returned — a stale or disconnected one is skipped,
        never returned with a flag. Absence is how the callers detect a departure
        (``_process_disconnected_clients``).

        Args:
            status: Snapcast server status response

        Returns:
            List of client dicts, deduplicated by MAC address
        """
        raw_clients = []
        now = time.time()

        for group in status.get("server", {}).get("groups", []):
            for client_data in group.get("clients", []):
                if not client_data.get("connected"):
                    continue

                if not self.is_speaker_entry(client_data):
                    continue
                name = self._entry_name(client_data)

                host = client_data["host"]["name"]
                ip = client_data["host"]["ip"].replace("::ffff:", "")
                client_id = client_data.get("id", "")

                if is_stale_local_client(client_id, ip):
                    self.logger.warning(f"Skipping stale local client id={client_id}")
                    continue

                # Calculate online status based on lastSeen timestamp
                last_seen_data = client_data.get("lastSeen", {})
                last_seen_sec = last_seen_data.get("sec", 0)
                last_seen_age = now - last_seen_sec

                is_online = (
                    client_data.get("connected", False)
                    and last_seen_age < self.LAST_SEEN_FRESHNESS_S
                )

                # Main device is always online (localhost)
                if ip == "127.0.0.1":
                    is_online = True

                if not is_online:
                    continue

                # A remote entry with no usable id costs itself, not the list:
                # every caller reads the list as the whole fleet. Read only once
                # the entry is live, so a gone one is not named on every pass.
                # The local client's unreadable MAC (RuntimeError) still fails
                # the parse: skipping it would take this unit's own speaker offline.
                try:
                    mac_id = compute_mac_id(host, ip, client_id)
                except ValueError as e:
                    self.logger.warning(f"Skipping client id={client_id!r}: {e}")
                    continue

                raw_clients.append({
                    "id": client_data["id"],
                    "name": name,
                    "muted": client_data["config"]["volume"]["muted"],
                    "host": host,
                    "ip": ip,
                    "mac_id": mac_id,
                    "last_seen_age": int(last_seen_age),
                })

        return self._deduplicate_by_mac(raw_clients)

    def extract_clients(self, status: dict) -> List[Dict[str, Any]]:
        """Extract online clients from a server status already fetched by the caller.

        Public because SnapcastWebSocketService must be able to tell "the RPC
        failed" from "no client is connected": the connect sweep, and the
        reconcile the 30 s sweep and Server.OnUpdate share, guard on an empty
        status first and only then parse, which ``get_clients``' fail-open []
        cannot express.
        """
        return self._parse_clients(status)

    @staticmethod
    def _entry_name(client_data: dict) -> str:
        """The name a snapserver client entry goes by: its own, else its host's."""
        return client_data.get("config", {}).get("name") or get_client_display_name(
            client_data.get("host", {}).get("name") or ""
        )

    @classmethod
    def is_speaker_entry(cls, client_data: dict) -> bool:
        """Whether a snapserver client entry may be admitted as a room.

        snapweb's in-browser player is a snapserver client too, and nothing
        behind its address answers Milō: admitted, it is a room that stays
        offline forever, persisted and listed by every reader of the registry.
        Both admission readers ask this -- the status walk here and
        `Client.OnConnect`, which carries one entry and no status -- so no
        notification admits snapweb whichever arrives first. Reads the entry
        with `.get()`, as that handler does: a key missing from one notification
        is no reason to fail it.
        """
        return cls.SNAPWEB_NAME not in cls._entry_name(client_data).lower()

    @staticmethod
    def group_of(status: dict, client_id: str) -> Optional[Dict[str, Any]]:
        """The group holding `client_id` in a status already fetched, or None.

        Read raw, not through ``extract_clients``: a group is snapserver's state
        for a client whether or not Milō counts that client live.
        """
        return next(
            (
                group for group in status.get("server", {}).get("groups", [])
                if any(c.get("id") == client_id for c in group.get("clients", []))
            ),
            None,
        )

    def _deduplicate_by_mac(self, clients: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate clients based on computed mac_id."""
        if not clients:
            return clients

        mac_groups: Dict[str, List[Dict[str, Any]]] = {}
        for client in clients:
            key = client.get("mac_id") or client.get("id")
            mac_groups.setdefault(key, []).append(client)

        deduplicated = []
        for mac, group in mac_groups.items():
            if len(group) == 1:
                deduplicated.append(group[0])
            else:
                self.logger.info(
                    f"Duplicate clients detected (MAC: {mac}): "
                    f"{[c['ip'] for c in group]} - keeping first"
                )
                deduplicated.append(group[0])

        return deduplicated

    # === AVAILABILITY CHECK ===

    async def is_available(self) -> bool:
        """Check if Snapcast server is available (app-level readiness probe)."""
        try:
            await self._request("Server.GetRPCVersion")
            return True
        except SnapcastRequestError as e:
            self.logger.debug(f"Snapcast availability check failed: {e}")
            return False

    async def wait_until_available(self, timeout: float = 10.0) -> bool:
        """Poll ``is_available`` until the daemon answers or ``timeout`` elapses.

        Wall-clock bounded via a monotonic deadline: because each probe carries
        its own 3s request timeout, a plain ``range(int(timeout))`` loop would
        run several times ``timeout`` against a hung server. The deadline caps
        total wait at ~``timeout`` (plus at most one in-flight probe).
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.is_available():
                return True
            await asyncio.sleep(1)
        return False

    @handle_errors(default={}, level='warning')
    async def get_server_status(self) -> dict:
        """Get complete Snapcast server status.

        Fail-open query: callers guard on an empty dict, so a request failure
        is logged and flattened to {} rather than propagated. Logged at warning,
        not error: the 30 s reconcile sweep can land in the ~100 ms between
        snapserver stopping and its WebSocket close being read (every backend
        restart stops snapserver first), and an error there is a UI banner for
        an outage nobody needs to act on.
        """
        return await self._request("Server.GetStatus")

    # === SERVER CONFIGURATION ===

    @handle_errors(default={})
    async def get_server_config(self) -> Dict[str, Any]:
        """Get the canonical stream configuration.

        Returns the flat shape ``update_server_config`` consumes, so the read
        and the write of this resource agree on one body. The `buffer_ms`
        field is only present in snapserver.conf (not exposed by the daemon
        JSON-RPC), so the file is parsed for it and merged with the daemon's
        live `chunk_ms` / `codec` / `sampleformat` values when available.
        """
        api_task = self._request("Server.GetStatus")
        file_task = self._read_snapserver_conf()

        status, file_config = await asyncio.gather(api_task, file_task)

        file_stream = file_config.get("parsed_config", {}).get("stream", {})
        # Server.GetStatus nests everything under "server" — groups AND streams
        # (_parse_clients already reads groups that way). Reading streams from
        # the top level always found nothing, so `query` was always empty and
        # this whole merge collapsed to the file: after a snapserver restart the
        # daemon refused, the settings page kept reporting the values Milō wrote
        # rather than the ones snapserver runs. Every stream carries the global
        # defaults, so the first one is representative.
        streams = status.get("server", {}).get("streams", [])
        query = streams[0].get("uri", {}).get("query", {}) if streams else {}

        return {
            "buffer_ms": int(file_stream.get("buffer") or 1000),
            "chunk_ms": int(query.get("chunk_ms") or file_stream.get("chunk_ms") or 20),
            "codec": query.get("codec") or file_stream.get("codec") or "flac",
            "sampleformat": query.get("sampleformat") or "48000:32:2",
        }

    @handle_errors(default={})
    async def _read_snapserver_conf(self) -> Dict[str, Any]:
        """Parse snapserver.conf file."""
        if not self.snapserver_conf.exists():
            return {}

        async with aiofiles.open(self.snapserver_conf, 'r') as f:
            content = await f.read()

        config = {}
        current_section = None

        for line in content.split('\n'):
            line = line.strip()

            if not line or line.startswith('#'):
                continue

            if line.startswith('[') and line.endswith(']'):
                current_section = line[1:-1]
                config.setdefault(current_section, {})
                continue

            if '=' in line and current_section:
                key, value = line.split('=', 1)
                key, value = key.strip(), value.strip()

                if key == 'source':
                    config[current_section].setdefault('sources', []).append(value)
                else:
                    config[current_section][key] = value

        return {"parsed_config": config, "raw_content": content}

    @handle_errors(default=False)
    async def update_server_config(self, config: Dict[str, Any]) -> bool:
        """Update server configuration and restart."""
        if not self.validate_config(config):
            return False

        # Force sampleformat to 32-bit for high-quality audio pipeline
        config["sampleformat"] = "48000:32:2"

        success = await self._update_config_file(config)
        if not success:
            return False

        return await self._restart_snapserver()

    def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate configuration parameters.

        Public because the route runs it before its first write: settings.json,
        snapclient.env and the push to every satellite all come before
        `update_server_config`, which keeps this call as its own guard.

        An unknown key is rejected, not ignored: validating only the keys it
        recognised meant a body in the wrong shape passed, wrote nothing,
        returned success and restarted snapserver anyway — the only way this
        endpoint can lie.

        The two `snapclient_*` keys are deliberately absent: they belong to
        snapclient.env, not snapserver.conf, and the route pops them before
        calling here — so entries for them validated nothing at all. Their range
        lives with the value they bound (`SNAPCLIENT_LIMITS` in routing.py) and
        is enforced by the route. Should one ever reach this method again, the
        unknown-key rule above rejects it loudly, which is the point.
        """
        validators = {
            "buffer_ms": lambda x: isinstance(x, int) and BUFFER_MS_RANGE[0] <= x <= BUFFER_MS_RANGE[1],
            "codec": lambda x: x in SUPPORTED_CODECS,
            "chunk_ms": lambda x: isinstance(x, int) and CHUNK_MS_RANGE[0] <= x <= CHUNK_MS_RANGE[1],
        }

        unknown = set(config) - set(validators) - {"sampleformat"}
        if unknown:
            self.logger.error(f"Unknown server config keys: {sorted(unknown)}")
            return False

        for key, validator in validators.items():
            if key in config and not validator(config[key]):
                self.logger.error(f"Invalid {key}: {config[key]}")
                return False

        return True

    @handle_errors(default=False)
    async def reconcile_conf(self) -> bool:
        """Rewrite the lines of snapserver.conf the backend owns; True iff it changed.

        Those are the captures (the `source =` lines, from the capture map) and
        the control sections (SNAPSERVER_CONTROL_SECTIONS). snapserver.conf is
        written at image build and no OTA ships it, so a source added, moved or
        retired since a card was flashed, or a control port closed since, reaches
        its conf only through this. Never restarts snapserver: the caller knows
        whether it should run, and a restart of the stopped unit would start it.
        """
        if not self.snapserver_conf.exists():
            self.logger.warning("snapserver.conf not found, nothing in it was reconciled")
            return False

        async with aiofiles.open(self.snapserver_conf, 'r') as f:
            content = await f.read()

        updated_content = self._modify_config_content(content, {})
        if updated_content == content:
            return False

        moved = [
            line for line in difflib.unified_diff(content.splitlines(), updated_content.splitlines(), lineterm="", n=0)
            if line[:1] in "+-" and not line.startswith(("+++", "---"))
        ]
        self.logger.info(f"snapserver.conf differs from what the backend writes, rewriting: {moved}")
        return await self._write_config_file(updated_content)

    @handle_errors(default=False)
    async def _update_config_file(self, config: Dict[str, Any]) -> bool:
        """Update configuration file via milo-deploy-update write-config."""
        if not self.snapserver_conf.exists():
            self.logger.error("snapserver.conf not found")
            return False

        async with aiofiles.open(self.snapserver_conf, 'r') as f:
            content = await f.read()

        return await self._write_config_file(self._modify_config_content(content, config))

    async def _write_config_file(self, content: str) -> bool:
        """Deploy `content` as snapserver.conf; both writers of the file come here.

        Each write stages its own file. The wrapper `cp`s its source into place
        and then `rm -f`s it, so on one shared path a second writer truncated
        the first's content before its copy, and the first's removal left the
        second copying a file that was gone. The `finally` removes what a
        refused deploy left behind.
        """
        temp_file = await asyncio.to_thread(_stage, content)
        try:
            proc = await asyncio.create_subprocess_exec(
                "sudo", DEPLOY_UPDATE_CMD,
                "write-config", temp_file, str(self.snapserver_conf),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            _, stderr = await proc.communicate()
        finally:
            with contextlib.suppress(FileNotFoundError):
                await asyncio.to_thread(os.remove, temp_file)

        if proc.returncode == 0:
            self.logger.info("snapserver.conf updated successfully")
            return True
        else:
            self.logger.error(f"Failed to update config: {stderr.decode()}")
            return False

    def _modify_config_content(self, content: str, config: Dict[str, Any]) -> str:
        """Modify file content with new configuration.

        The `source =` lines of [stream] become MULTIROOM_STREAM_SOURCES whatever
        the payload, position by position: the i-th line takes the i-th source, a
        surplus line is dropped, a shortfall is added after the last one. In
        place rather than regrouped, so the blank line between the meta stream
        and the captures stays where the file has it, and a rewritten file
        rewrites to itself -- the reconcile restarts snapserver on any change.

        Each section of SNAPSERVER_CONTROL_SECTIONS holds exactly its settings,
        the same way: an owned key is written over its first line, any other
        setting there is dropped (snapweb's `doc_root`), a key it lacks is added
        after its last setting, and a section the file lacks is added at its end.
        """
        lines = content.split('\n')
        updated_lines = []
        section = None
        expected_sources = iter(MULTIROOM_STREAM_SOURCES)
        after_last_source = None
        unwritten = {}
        after_last_control = {}

        # Map snapserver.conf field names (left) to incoming payload keys (right).
        # The conf uses `buffer` for what we expose on the wire as `buffer_ms`.
        param_mapping = {
            "buffer": "buffer_ms",
            "codec": "codec",
            "chunk_ms": "chunk_ms",
            "sampleformat": "sampleformat"
        }

        for line in lines:
            stripped_line = line.strip()

            # snapserver reads what follows a `#` as a comment, a header's too.
            header = stripped_line.split("#", 1)[0].strip()
            if header.startswith("["):
                section = header[1:-1] if header.endswith("]") else None
                updated_lines.append(line)
                if section == "stream":
                    after_last_source = len(updated_lines)
                elif section in SNAPSERVER_CONTROL_SECTIONS:
                    unwritten.setdefault(section, dict(SNAPSERVER_CONTROL_SECTIONS[section]))
                    after_last_control[section] = len(updated_lines)
                continue

            if "=" not in stripped_line or stripped_line.startswith("#"):
                updated_lines.append(line)
                continue

            key = stripped_line.split("=")[0].strip()

            if section == "stream":
                if key == "source":
                    source = next(expected_sources, None)
                    if source is not None:
                        updated_lines.append(f"source = {source}")
                        after_last_source = len(updated_lines)
                elif key in param_mapping and param_mapping[key] in config:
                    param_key = param_mapping[key]
                    if param_key == "sampleformat":
                        updated_lines.append("sampleformat = 48000:32:2")
                    else:
                        updated_lines.append(f"{key} = {config[param_key]}")
                else:
                    updated_lines.append(line)
            elif section in SNAPSERVER_CONTROL_SECTIONS:
                value = unwritten[section].pop(key, None)
                if value is not None:
                    updated_lines.append(f"{key} = {value}")
                    after_last_control[section] = len(updated_lines)
            else:
                updated_lines.append(line)

        # Inserted from the bottom up, so each index still points where it did.
        insertions = []
        missing = [f"source = {source}" for source in expected_sources]
        if missing and after_last_source is None:
            self.logger.warning("snapserver.conf has no [stream] section, so no capture was written")
        elif missing:
            insertions.append((after_last_source, missing))
        for name, settings in unwritten.items():
            if settings:
                insertions.append((after_last_control[name], [f"{k} = {v}" for k, v in settings.items()]))
        for index, block in sorted(insertions, key=lambda insertion: insertion[0], reverse=True):
            updated_lines[index:index] = block

        absent = [name for name in SNAPSERVER_CONTROL_SECTIONS if name not in unwritten]
        if absent:
            final_newline = bool(updated_lines) and updated_lines[-1] == ""
            if final_newline:
                updated_lines.pop()
            for name in absent:
                if updated_lines and updated_lines[-1].strip():
                    updated_lines.append("")
                updated_lines.append(f"[{name}]")
                updated_lines += [f"{k} = {v}" for k, v in SNAPSERVER_CONTROL_SECTIONS[name]]
            if final_newline:
                updated_lines.append("")

        return '\n'.join(updated_lines)

    @handle_errors(default=False)
    async def _restart_snapserver(self) -> bool:
        """Restart Snapcast server."""
        self.logger.info("Restarting snapserver...")

        if not await self._systemd.restart("milo-snapserver-multiroom.service"):
            return False

        # Let the old instance fully exit before probing (a lingering socket
        # would answer as "available" and mask the restart), then wait for the
        # fresh daemon's JSON-RPC to come up.
        await asyncio.sleep(3)
        if await self.wait_until_available(timeout=10.0):
            self.logger.info("Snapserver restarted successfully")
            return True

        self.logger.warning("Snapserver restarted but API not available yet")
        return False
