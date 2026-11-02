# backend/core/multiroom/websocket.py
"""
Snapcast WebSocket Service - Real-time notifications from Snapcast server.

Handles WebSocket connection to Snapcast for client connect/disconnect events,
volume changes, and server updates. Uses ClientRegistryService as the single
source of truth for client/availability tracking.
"""
import asyncio
import json
import logging
import time
from typing import Dict, Any, Optional, TYPE_CHECKING

import aiohttp

from backend.core.multiroom.identity import compute_mac_id
from backend.core.multiroom.routing import resolve_snapclient_config
from backend.core.multiroom.snapcast import SnapcastService
from backend.core.multiroom.client_registry import (
    ClientRegistryService,
    REGISTRY_EVENT_CLASSES,
)
from backend.config.constants import (
    CLIENT_API_PORT,
    DEFAULT_VOLUME_DB,
    MULTIROOM_STREAMS,
    get_client_display_name,
)
from backend.shared.background import BackgroundTaskSet

if TYPE_CHECKING:
    from backend.core.settings import SettingsService
    from backend.core.state import AudioStateMachine


class SnapcastWebSocketService:
    """
    WebSocket service for Snapcast notifications.

    Handles real-time events from Snapcast server:
    - Client connect/disconnect
    - Volume and mute changes
    - Server updates (availability changes)
    """

    # Reconcile sweep cadence. Combined with SnapcastService.LAST_SEEN_FRESHNESS_S,
    # a silently vanished client is detected in at most the sum of the two.
    # See _reconcile_loop for why a timer is required at all.
    RECONCILE_INTERVAL_S = 30

    # Bounds the handshake only: the session carries it, and aiohttp applies a
    # session timeout to the upgrade request, never to the socket after it.
    CONNECT_TIMEOUT_S = 5

    def __init__(
        self,
        state_machine: "AudioStateMachine",
        routing_service,
        settings_service: Optional["SettingsService"] = None,
        host: str = "localhost",
        port: int = 1780,
        snapcast_service: Optional[SnapcastService] = None,
        crossover_service=None,
        equalizer_client_proxy_service=None,
        pending_clients_service=None,
        satellite_wifi_service=None,
    ):
        self.state_machine = state_machine
        self.routing_service = routing_service
        self.settings_service = settings_service
        self.host = host
        self.port = port
        self.ws_url = f"ws://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)

        # Client registry (set via set_registry after construction)
        self._registry: Optional["ClientRegistryService"] = None

        # Connection state
        self.session: Optional[aiohttp.ClientSession] = None
        self.websocket: Optional[aiohttp.ClientWebSocketResponse] = None
        self.running = False
        self.should_connect = False
        self.reconnect_task = None
        self.reconcile_task = None

        # Deduplication: track mac_ids with an in-flight sync task
        self._syncing_mac_ids: set = set()
        # What each of those admissions sent, noted where each setting was
        # read, so what moved since is delivered once the client is online
        # (_deliver_what_moved_during_admission). Lives as long as the guard.
        self._admission_sent: Dict[str, Dict[str, Any]] = {}

        # Background tasks (fire-and-forget sync, config push, etc.)
        # Tracked so they can be cancelled cleanly in stop_connection().
        self._bg = BackgroundTaskSet(self.logger, "snapcast_ws")

        # Initialization state - suppress verbose logs during startup
        self._is_initializing = False

        # ID for JSON-RPC requests
        self.request_id = 0

        # Ready event - signaled when WebSocket is connected and initialized
        self._ready_event = asyncio.Event()

        # Acyclic deps (constructor-injected). volume_service / registry close a
        # real cycle / need ordered subscription → set post-construction.
        self._snapcast_service = snapcast_service
        self._crossover_service = crossover_service
        self._equalizer_client_proxy_service = equalizer_client_proxy_service
        self._pending_clients_service = pending_clients_service
        self._satellite_wifi_service = satellite_wifi_service
        self._volume_service = None

    @property
    def registry(self) -> Optional["ClientRegistryService"]:
        """Get the client registry."""
        return self._registry

    @property
    def connected(self) -> bool:
        """True when the snapserver control WebSocket is open."""
        return self.websocket is not None and not self.websocket.closed

    async def initialize(self) -> bool:
        """Initialize the WebSocket service."""
        try:
            self.logger.info(f"Initializing Snapcast WebSocket service: {self.ws_url}")
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.CONNECT_TIMEOUT_S)
            )
            self.running = True

            # Single source of truth: AudioRoutingService.multiroom_enabled
            # (settings-backed). No fallback chain needed — the
            # routing service IS the authority on whether multiroom is on.
            multiroom_state = bool(self.routing_service.multiroom_enabled) if self.routing_service else False

            self.should_connect = multiroom_state

            if self.should_connect:
                self.logger.info("Multiroom already enabled, starting WebSocket connection")
                self._spawn_loops()
            else:
                self.logger.info("Multiroom disabled, WebSocket will connect when multiroom is enabled")

            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize Snapcast WebSocket: {e}")
            return False

    async def start_connection(self) -> None:
        """Start WebSocket connection when multiroom is enabled."""
        if self.should_connect:
            return  # Already in progress

        self.logger.info("Starting Snapcast WebSocket connection (multiroom enabled)")
        self.should_connect = True

        if not self.reconnect_task and self.running:
            self._spawn_loops()

    async def stop_connection(self) -> None:
        """Stop WebSocket connection when multiroom is disabled."""
        if not self.should_connect:
            return  # Already stopped

        self.logger.info("Stopping Snapcast WebSocket connection (multiroom disabled)")
        self.should_connect = False

        # Capture the socket BEFORE cancelling: _connect_and_listen nulls the
        # attribute in its finally as the task unwinds, and cancel_all() drains
        # the tasks — so a close guarded on self.websocket afterwards can never
        # fire, and the TCP connection to snapserver leaked on every disable.
        websocket = self.websocket

        # Cancel all in-flight background tasks (connection loop, sync retries,
        # config push, etc.)
        await self._bg.cancel_all()
        self.reconnect_task = None
        self.reconcile_task = None
        self._syncing_mac_ids.clear()
        self._admission_sent.clear()

        await self._close_websocket(websocket)
        await self._take_every_client_offline()

        self._ready_event.clear()

    async def _close_websocket(self, websocket) -> None:
        """Close a captured snapserver socket, bounded.

        aiohttp's close() waits for the peer's CLOSE frame, and the usual reason
        we are here is that snapserver is going down with multiroom — so an
        unbounded wait would block a request (PUT /api/routing/multiroom) or the
        lifespan teardown on a peer that will never answer.
        """
        if websocket is None or websocket.closed:
            return
        try:
            await asyncio.wait_for(websocket.close(), timeout=2.0)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            self.logger.warning(f"Snapcast WebSocket did not close cleanly: {e}")

    async def wait_for_ready(self, timeout: float = 10.0) -> bool:
        """Wait for WebSocket to be connected and initialized."""
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            self.logger.warning(f"Timeout waiting for Snapcast WebSocket ready after {timeout}s")
            return False

    async def cleanup(self) -> None:
        """Clean up resources."""
        self.logger.info("Cleaning up Snapcast WebSocket service")
        self.running = False
        self.should_connect = False

        # Captured first, for the reason spelled out in stop_connection.
        websocket = self.websocket

        await self._bg.cancel_all()

        await self._close_websocket(websocket)

        if self.session:
            await self.session.close()

    # === Service setters (circular dependency resolution) ===

    def set_registry(self, registry) -> None:
        """Set ClientRegistryService dependency and own its broadcast.

        The registry is a pure store; this service is responsible for
        translating its events into "multiroom" WebSocket broadcasts.
        """
        self._registry = registry
        registry.subscribe(self._broadcast_registry_event)

    async def _broadcast_registry_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Forward a registry event as a typed multiroom WebSocket broadcast."""
        event_cls = REGISTRY_EVENT_CLASSES.get(event_type)
        if event_cls is None:
            self.logger.error(f"No WS event class for registry event {event_type!r} — dropped")
            return
        await self.state_machine.broadcast(event_cls(**data))

    def set_volume_service(self, service) -> None:
        """Set VolumeService dependency (closes the volume ↔ snapcast_ws cycle)."""
        self._volume_service = service

    async def _take_every_client_offline(self) -> None:
        """Nobody watches a client once the control socket is gone, so none stays online.

        snapserver going down announces the local snapclient alone. Held online
        across the gap, a satellite unplugged meanwhile had every neighbor's
        admission push its zone's filters to a dead address, and one whose
        re-admission gave up was never tried again: the sweep's online flip
        only retries a client the registry holds offline.
        """
        if self.registry:
            await self.registry.set_clients_offline(self.registry.get_client_ids())

    def _spawn_loops(self) -> None:
        """Start the two long-lived loops that only run while multiroom is on."""
        self.reconnect_task = self._bg.spawn(self._connection_loop(), label="connection_loop")
        self.reconcile_task = self._bg.spawn(self._reconcile_loop(), label="reconcile_loop")

    async def _connection_loop(self) -> None:
        """Connection loop with intelligent reconnection."""
        reconnect_delay = 5
        max_delay = 30

        while self.running and self.should_connect:
            try:
                if await self._connect_and_listen():
                    reconnect_delay = 5
                    await self._take_every_client_offline()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"WebSocket connection error: {e}")

            if self.running and self.should_connect:
                self.logger.info(f"Reconnecting to Snapcast WebSocket in {reconnect_delay} seconds...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_delay)

    async def _connect_and_listen(self) -> bool:
        """Connect and listen for WebSocket messages.

        Returns whether the connection got as far as ready. A refused or
        timed-out connect is caught here, not raised, so this is the only way
        the reconnect loop can tell a failure from a connection that dropped.
        """
        ready = False
        # Clear ready state so wait_for_ready() blocks until this connection
        # is fully initialized. Without this, a stale True from a previous
        # connection causes callers to proceed against a dead socket.
        self._ready_event.clear()

        try:
            self.logger.info(f"Connecting to Snapcast WebSocket: {self.ws_url}")

            try:
                self.websocket = await self.session.ws_connect(self.ws_url)
            except TimeoutError:
                self.logger.warning(
                    f"Snapcast server did not answer the WebSocket handshake within {self.CONNECT_TIMEOUT_S} s"
                )
                return False
            self.logger.info("Connected to Snapcast WebSocket")

            # Send initial ping to verify connection
            await self._send_request("Server.GetRPCVersion")

            # Initialize already connected clients
            self._is_initializing = True
            await self._initialize_existing_clients()
            self._bg.spawn(self._clear_init_flag_after_delay(2.0), label="clear_init_flag")

            self._ready_event.set()
            ready = True
            self.logger.info("Snapcast WebSocket ready and initialized")

            # Listen for messages
            async for msg in self.websocket:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_message(data)
                    except json.JSONDecodeError as e:
                        self.logger.error(f"Invalid JSON received: {e}")
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    self.logger.error(f"WebSocket error: {self.websocket.exception()}")
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSE:
                    self.logger.info("WebSocket connection closed")
                    break

        except aiohttp.ClientConnectorError:
            self.logger.info("Cannot connect to Snapcast server - server may not be running")
        except Exception as e:
            self.logger.error(f"WebSocket connection failed: {e}")
        finally:
            self.websocket = None
        return ready

    async def _clear_init_flag_after_delay(self, delay: float) -> None:
        """Clear the initialization flag after a delay."""
        await asyncio.sleep(delay)
        self._is_initializing = False
        self.logger.debug("Snapcast WebSocket initialization phase complete")

    async def _initialize_existing_clients(self) -> None:
        """Admit the clients that were already connected when the socket opened.

        Their Client.OnConnect fired before there was a socket to hear it, so
        this is the only notification path that will ever see them — which is
        every satellite that was up before the backend, i.e. the whole fleet
        after a power cut.
        """
        try:
            self.logger.debug(f"[{time.time():.3f}] INIT_CLIENTS: Starting initialization")

            if not self._snapcast_service:
                self.logger.warning("SnapcastService not available")
                return

            status = await self._snapcast_service.get_server_status()
            if not status:
                self.logger.warning("Could not get Snapcast status")
                return

            # One definition of "alive" for the whole file. Snapserver's own
            # `connected` flag outlives a client that vanished without a TCP FIN,
            # so reading the status directly re-marked a long-gone satellite
            # online on every reconnection (a multiroom toggle was enough);
            # extract_clients applies the same lastSeen freshness rule as the
            # reconcile sweep, and drops snapweb and stale local entries with it.
            live_clients = self._snapcast_service.extract_clients(status)

            claimed = sum(
                1 for group in status.get("server", {}).get("groups", [])
                for client in group.get("clients", []) if client.get("connected")
            )
            if claimed > len(live_clients):
                self.logger.info(
                    f"INIT_CLIENTS: snapserver claims {claimed} connected, "
                    f"{len(live_clients)} are live — the rest are not admitted"
                )

            for client in live_clients:
                mac_id = client["mac_id"]
                is_local = (client["ip"] == "127.0.0.1")
                local_marker = " LOCAL CLIENT" if is_local else ""
                self.logger.debug(
                    f"[{time.time():.3f}] INIT_CLIENTS: Client {client['id']} "
                    f"(mac_id: {mac_id}){local_marker}"
                )

                await self._register_snapclient(
                    mac_id, client["name"] or mac_id, client["ip"], client["host"],
                    is_local=is_local,
                )

                # Same admission sequence as every other path, for a known client
                # too: sync first and show the client online only once the
                # hardware confirmed, with retries because a satellite's API is
                # often still booting when its snapclient is already connected.
                # Registering a new client online here left a failed sync
                # unretried, and the speaker muted (CamillaDSP starts with -m) for
                # as long as it was up. Readmitting a known one without the sync
                # assumed it never stopped playing, but between two connections of
                # this socket nobody watched it (direct mode, a snapserver
                # restart): a satellite back from offline during a multiroom
                # toggle never got the mute it was given while away, and played
                # while every screen showed it muted. The sync re-applies the
                # client's own stored state, so a speaker that did keep playing
                # hears nothing change.
                self._bg.spawn(
                    self._sync_reconnecting_client_volume(
                        mac_id, set_online_after=True, snapcast_id=client["id"]
                    ),
                    label=f"sync_init_client_{mac_id}",
                )

            client_count = len(self.registry.get_all_clients()) if self.registry else 0
            has_local = any(c.is_local for c in self.registry.get_all_clients().values()) if self.registry else False
            local_status = "LOCAL FOUND" if has_local else "LOCAL NOT YET CONNECTED"
            self.logger.debug(f"[{time.time():.3f}] INIT_CLIENTS: Complete. Registered: {client_count} clients. {local_status}")

        except Exception as e:
            self.logger.error(f"Error initializing existing clients: {e}", exc_info=True)

    async def _send_request(self, method: str, params: Optional[Dict] = None) -> None:
        """Send a JSON-RPC request."""
        if not self.websocket:
            return

        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self.request_id
        }

        if params:
            request["params"] = params

        try:
            await self.websocket.send_str(json.dumps(request))
        except Exception as e:
            self.logger.error(f"Failed to send request: {e}")

    async def _handle_message(self, data: Dict[str, Any]) -> None:
        """Process a received JSON-RPC message."""
        try:
            if "method" in data and "id" not in data:
                await self._handle_notification(data)
            elif "result" in data or "error" in data:
                await self._handle_response(data)

        except Exception as e:
            self.logger.error(f"Error handling message: {e}")

    async def _handle_notification(self, notification: Dict[str, Any]) -> None:
        """Process a Snapcast notification."""
        method = notification.get("method")
        params = notification.get("params", {})

        if self._is_initializing:
            self.logger.debug(f"SNAPCAST NOTIFICATION (init phase): {method}")
        else:
            self.logger.debug(f"SNAPCAST NOTIFICATION RECEIVED: {method}")

        non_volume_notifications = {
            "Client.OnConnect": lambda p: self._handle_client_connect(p),
            "Client.OnDisconnect": lambda p: self._handle_client_disconnect(p),
            "Client.OnNameChanged": lambda p: self._handle_client_name_changed(p),
            "Server.OnUpdate": lambda p: self._handle_server_update(p)
        }

        if method in non_volume_notifications:
            await non_volume_notifications[method](params)
        elif method not in ["Client.OnVolumeChanged", "Client.OnMute"]:
            # Volume/mute events are handled by the volume service path,
            # not via WebSocket notifications
            self.logger.debug(f"Unhandled notification: {method}")

    async def _handle_server_update(self, params: Dict[str, Any]) -> None:
        """Handle Server.OnUpdate - detects new clients, disconnections, and online status changes."""
        try:
            if not self._snapcast_service:
                self.logger.warning("SnapcastService not available for online status detection")
                return

            await self._reconcile_with_server_status("SERVER_UPDATE")

        except Exception as e:
            self.logger.error(f"Error handling Server.OnUpdate: {e}", exc_info=True)

    async def _reconcile_with_server_status(self, label: str) -> None:
        """Reconcile the registry against a Server.GetStatus read now, or not at all.

        Never through get_clients(): that call flattens an RPC failure to [],
        which ``_reconcile_clients`` would read as "every client disconnected"
        and act on. A timeout on a loaded Pi took the whole fleet offline that way.
        """
        status = await self._snapcast_service.get_server_status()
        if not status:
            self.logger.debug(f"{label}: no server status — skipping this pass")
            return

        all_clients = self._snapcast_service.extract_clients(status)
        self.logger.debug(f"{label}: Got {len(all_clients)} clients from Snapcast")
        await self._reconcile_clients(all_clients)

    async def _reconcile_clients(self, all_clients: list) -> None:
        """Align the registry with a Snapcast client list (the authority on liveness)."""
        current_mac_ids = {c["mac_id"] for c in all_clients}
        known_mac_ids = set(self.registry.get_client_ids()) if self.registry else set()

        await self._process_new_clients(all_clients, known_mac_ids)
        await self._process_disconnected_clients(current_mac_ids, known_mac_ids)
        await self._process_online_status_changes(all_clients)

    async def _reconcile_loop(self) -> None:
        """Re-derive every client's online state on a timer.

        Snapserver only declares a client disconnected when its socket errors,
        and it writes nothing to an idle client's socket — so a satellite that
        vanishes without a TCP FIN (power cut, Wi-Fi drop) stays `connected:
        true` there indefinitely and no Client.OnDisconnect / Server.OnUpdate
        notification is ever emitted. Without this sweep the freshness rule in
        SnapcastService._parse_clients (lastSeen younger than
        SnapcastService.LAST_SEEN_FRESHNESS_S) is never evaluated, the registry
        keeps the client online forever, and the frontend offers controls for a
        speaker that is gone.
        """
        while self.running and self.should_connect:
            await asyncio.sleep(self.RECONCILE_INTERVAL_S)
            try:
                if not self.connected or not self._snapcast_service or not self.registry:
                    continue

                await self._reconcile_with_server_status("RECONCILE")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"Error in client reconcile sweep: {e}", exc_info=True)

    async def _register_snapclient(
        self, mac_id: str, fallback_name: str, ip: str, host: str, is_local: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Register a snapclient in the registry, honouring its pending configuration.

        Two notifications can be the first to see a new client — `Client.OnConnect`
        and `Server.OnUpdate` — and whichever wins must produce the same registry
        entry. That is why the pending lookup lives here and not in one caller:
        registering with the Snapcast host name instead loses the name, speaker
        type and volume_control the user just picked in the wizard, and
        `register_client` preserves an existing non-empty name, so a later
        notification cannot repair it.

        Returns the pending entry that was transferred, or None.
        """
        pending = (
            self._pending_clients_service.get_client(mac_id)
            if self._pending_clients_service else None
        )
        if pending and pending.get("name"):
            self.logger.info(
                f"  - Pending client matched: name='{pending['name']}', "
                f"speaker_type='{pending.get('speaker_type')}'"
            )

        name = (pending.get("name") if pending else None) or fallback_name

        if self.registry:
            kwargs = {"host": host}
            if pending:
                if pending.get("speaker_type"):
                    kwargs["speaker_type"] = pending["speaker_type"]
                kwargs["volume_control"] = pending.get("volume_control", True)
            elif is_local and self._volume_service:
                # Sync hardware volume_control to registry (e.g. DAC mode read at boot)
                kwargs["volume_control"] = self._volume_service.volume_control
            # When no pending entry and not local, volume_control is not passed —
            # register_client preserves existing value for known clients
            await self.registry.register_client(mac_id, name, ip, **kwargs)

            # Transfer complete — the registry now owns this client's identity.
            if pending and self._pending_clients_service:
                await self._pending_clients_service.remove_client(mac_id)

        return pending

    async def _process_new_clients(self, all_clients: list, known_mac_ids: set) -> None:
        """Register clients present in Snapcast but not yet in the registry."""
        for client in all_clients:
            mac_id = client["mac_id"]

            if mac_id not in known_mac_ids:
                self.logger.info(f"NEW CLIENT detected: {mac_id} (snapcast_id: {client['id']})")

                if mac_id in self._syncing_mac_ids:
                    self.logger.debug(f"Skipping Server.OnUpdate init for {mac_id} - sync already in flight")
                    continue

                # Register but keep OFFLINE — client stays invisible in
                # frontend until volume is synced and confirmed on hardware.
                await self._register_snapclient(
                    mac_id, client["name"], client["ip"], client["host"],
                    is_local=(client["ip"] == "127.0.0.1"),
                )

                # Sync volume then set online. The sync task owns the
                # _syncing_mac_ids guard and clears it when done.
                self._bg.spawn(
                    self._sync_reconnecting_client_volume(
                        mac_id, set_online_after=True, snapcast_id=client["id"]
                    ),
                    label=f"sync_new_client_{mac_id}",
                )

    async def _process_disconnected_clients(self, current_mac_ids: set, known_mac_ids: set) -> None:
        """Mark registry clients as offline when they no longer appear in Snapcast.

        In one registry call, so a zone that went at once (a power strip) is
        recalculated from no member online rather than from neighbors still
        flagged online at dead addresses. Only a real transition is worth a
        line: this runs on every reconcile pass, so logging unconditionally
        wrote the same "disconnected" line every 30s for as long as a satellite
        stayed unplugged.
        """
        if not self.registry:
            return
        for mac_id in await self.registry.set_clients_offline(known_mac_ids - current_mac_ids):
            self.logger.info(f"CLIENT DISCONNECTED: {mac_id}")

    async def _process_online_status_changes(self, all_clients: list) -> None:
        """Bring back the known clients Snapcast lists again.

        Every client in the list is live — the offline direction is handled by
        absence, in ``_process_disconnected_clients``.
        """
        for client in all_clients:
            mac_id = client["mac_id"]

            registry_client = self.registry.get_client(mac_id) if self.registry else None
            if not registry_client or registry_client.online:
                continue

            self.logger.info(f"Client {mac_id} online status: False -> True")

            # Client reconnecting: do NOT set online yet — the sync task
            # sets online after hardware confirms volume (set_online_after=True).
            # This prevents a window where the frontend shows the client
            # at a stale volume before sync completes.
            self._bg.spawn(
                self._sync_reconnecting_client_volume(
                    mac_id, set_online_after=True, snapcast_id=client["id"]
                ),
                label=f"sync_reconnect_{mac_id}",
            )

            # Crossover recalculation is handled by CrossoverService._handle_registry_event
            # via CLIENT_CONNECTED/CLIENT_DISCONNECTED events emitted by set_client_online()

    async def _handle_response(self, response: Dict[str, Any]) -> None:
        """Process a response to a request."""
        if "error" in response:
            self.logger.error(f"Snapcast RPC error: {response['error']}")

    def _identity_of(
        self, client: Dict[str, Any], host: str, ip: str, event: str
    ) -> Optional[str]:
        """The mac_id a notification's entry is keyed by, or None to skip it.

        A remote entry with no usable id is skipped at warning, as the status
        walk (`SnapcastService._parse_clients`) skips it. The local client's
        unreadable MAC (RuntimeError) still raises.
        """
        client_id = client.get("id") or ""
        try:
            return compute_mac_id(host, ip, client_id)
        except ValueError as e:
            self.logger.warning(f"{event}: skipping client id={client_id!r}: {e}")
            return None

    async def _handle_client_connect(self, params: Dict[str, Any]) -> None:
        """Handle client connected event."""
        client = params.get("client", {})
        client_id = client.get("id")

        client_host = client.get("host", {}).get("name") or "Unknown"
        client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")

        if not SnapcastService.is_speaker_entry(client):
            self.logger.debug(f"CLIENT_CONNECT: {client_id} ({client_host}) is not a speaker, not admitted")
            return

        # Compute mac_id early so we can dedup by stable identifier
        mac_id = self._identity_of(client, client_host, client_ip, "Client.OnConnect")
        if mac_id is None:
            return

        client_name = client.get("config", {}).get("name") or get_client_display_name(client_host) or mac_id
        is_local = (client_ip == "127.0.0.1")
        local_marker = " LOCAL CLIENT" if is_local else ""
        self.logger.debug(f"[{time.time():.3f}] CLIENT_CONNECT: New client {client_id} (mac_id: {mac_id}){local_marker}")
        self.logger.debug(f"  - Name: {client_name}, Host: {client_host}, IP: {client_ip}")

        # Register client (but don't set online yet — the sync does that once the
        # hardware confirmed).
        await self._register_snapclient(
            mac_id, client_name, client_ip, client_host, is_local=is_local
        )

        # Dedup the SYNC, never the registration above. This guard used to sit
        # before it, and _register_snapclient is the only path that refreshes a
        # known client's ip/host: a reconnect landing inside the sync's ~15 s
        # retry budget therefore skipped the identity refresh entirely. After a
        # DHCP lease change caught by that window the speaker keeps playing (it
        # dials out to snapserver) while every push Milō makes goes to the old
        # address — volume, EQ and hardware all fail silently, and the registry
        # shows it offline. Registering twice is idempotent: the existing branch
        # preserves a non-empty name and only overwrites ip/host, so the cost is
        # one redundant CLIENT_UPDATED event.
        if mac_id in self._syncing_mac_ids:
            self.logger.debug(
                f"Client.OnConnect for {mac_id}: identity refreshed, sync already in flight"
            )
            return

        # Hand the sync to a task rather than awaiting it: this runs inside the
        # snapserver message loop, and a satellite that is still booting takes the
        # sync through its full retry budget. Server.OnUpdate, on the same loop,
        # already spawns for that reason.
        self._bg.spawn(
            self._sync_reconnecting_client_volume(
                mac_id, set_online_after=True, snapcast_id=client_id
            ),
            label=f"sync_connect_{mac_id}",
        )

    async def _handle_client_disconnect(self, params: Dict[str, Any]) -> None:
        """Handle client disconnected event."""
        client = params.get("client", {})

        client_host = client.get("host", {}).get("name", "Unknown")
        client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")

        mac_id = self._identity_of(client, client_host, client_ip, "Client.OnDisconnect")
        if mac_id is None:
            return

        self.logger.info(f"CLIENT DISCONNECTED: {client_host} (mac_id: {mac_id})")

        if self.registry:
            await self.registry.set_client_online(mac_id, False)

        # Note: Crossover recalculation is handled by CrossoverService via
        # CLIENT_DISCONNECTED event (triggered by set_client_online above)

    async def _handle_client_name_changed(self, params: Dict[str, Any]) -> None:
        """Handle client name changed event."""
        client_id = params.get("id")
        name = params.get("name")
        mac_id = client_id  # fallback if lookup fails

        if self._snapcast_service:
            try:
                status = await self._snapcast_service.get_server_status()
                for group in status.get("server", {}).get("groups", []):
                    for client in group.get("clients", []):
                        if client.get("id") == client_id:
                            host_info = client.get("host", {})
                            mac_id = compute_mac_id(
                                host_info.get("name", ""),
                                host_info.get("ip", "").replace("::ffff:", ""),
                                client_id
                            )
                            break
            except Exception as e:
                self.logger.warning(f"Could not resolve mac_id for {client_id}: {e}")

        # Update the registry so the name change persists to settings.json
        if self.registry and name:
            await self.registry.update_client(mac_id, name=name)

    def _resolve_target_volume(self, mac_id: str) -> float:
        """
        Resolve the level a client comes back at.

        A client's level changes only when someone changes it — so a client
        that reappears gets its own last level back, never a reading derived
        from whoever happens to be online next to it. This is the same rule the
        boot push (`_do_push_volume_to_all_clients`) and the local startup
        volume (`_apply_startup_volume`) already apply; the admission used to be
        the one path that answered differently.

        `restore_last_volume` is not read here. It says what the levels are at
        the *startup* of Milō, and the store applies it once, when it loads:
        read at every admission, it sent a satellite back to startup_volume_db
        each time it reconnected — after an app update, a reboot, a Wi-Fi drop —
        although nobody had touched its level.

        Resolution order:
        1. The volume store's level for this client. The store is seeded at
           startup_volume_db when the registry first admits a client, so this
           answers startup_volume_db for one nobody has set and its own level
           for one someone has — which is why the message says where the number
           came from rather than claiming it was "stored" (measured on the unit:
           a fresh satellite reads startup_volume_db here, and a log line
           calling that "stored" is the one place the two are
           indistinguishable).
        2. The configured startup_volume_db, for a client the store lacks.
        3. DEFAULT_VOLUME_DB constant (no volume service at all)
        """
        if self._volume_service:
            config = self._volume_service.volume_config
            stored = self._volume_service.state_store.get_client_volume(mac_id)
            if stored is not None:
                self.logger.info(f"Volume store holds {stored:.1f} dB for {mac_id}")
                return stored

            self.logger.info(f"Using startup volume {config.startup_volume_db:.1f} dB for {mac_id}")
            return config.startup_volume_db

        # No volume service at all: constant fallback
        self.logger.warning(f"No volume service available, using DEFAULT_VOLUME_DB for {mac_id}")
        return DEFAULT_VOLUME_DB

    async def _apply_target_volume_to_client(
        self,
        mac_id: str,
        target_volume_db: float
    ) -> bool:
        """
        Apply a specific volume to a client's equalizer and update state.

        Always updates the state store (so the UI shows the correct target
        volume). Applies to hardware on best-effort — returns False if the
        hardware call fails so callers can retry.

        Args:
            mac_id: Client identifier
            target_volume_db: Volume to set in dB

        Returns:
            True only if BOTH the volume and the mute reached the hardware.
            The state store is updated regardless. The caller's retry loop is
            what recovers a False, so reporting one is the whole mechanism.
        """
        try:
            if not self._volume_service:
                self.logger.warning(f"No volume_service available to apply volume for {mac_id}")
                return False

            # Always update the state store first (UI correctness)
            await self._volume_service.state_store.set_client_volume(mac_id, target_volume_db)
            # As stored: clamped by the same limits the controller applies on the way out.
            self._note_sent(mac_id, "level", self._volume_service.state_store.get_client_volume(mac_id))

            # Apply to hardware — force=True bypasses the online check in the
            # router so we can sync clients that are registered but not yet
            # marked online (they stay offline/muted in the frontend until
            # this succeeds).
            eq = self._volume_service.equalizer_controller
            volume_ok = await eq.set_equalizer_volume(mac_id, target_volume_db, force=True)

            # Always attempt unmute even if volume failed — a muted client with
            # wrong volume is worse than an unmuted client with wrong volume.
            # CamillaDSP starts muted with -m flag, so skipping unmute on volume
            # failure would leave the client permanently silent.
            #
            # That trade only holds because the fader a failed volume leaves
            # behind is bounded, and what bounds it is the `--gain` floor both
            # CamillaDSP units start at — not the ordering, which is narrowed
            # but not closed: a satellite's connection loop is the only thing
            # that opens a connection, yet it publishes `_connected` just before
            # restoring, so a command landing in that hop still meets an
            # unrestored daemon. At the floor that is inaudible, which is the
            # whole point of having one. On 2026-09-20 neither half held — the
            # satellite unit carried no `--gain` and `_exec` could connect on
            # its own — and this line unmuted a 0 dB fader for 3 s in a room set
            # to -52.8 dB. Pinned by test_camilladsp_startup_floor.py for the
            # floor and test_services_equalizer.py for the single door.
            persisted_mute = self._volume_service.state_store.get_client_mute(mac_id)
            self._note_sent(mac_id, "mute", persisted_mute)
            mute_ok = await eq.set_equalizer_mute(mac_id, persisted_mute, force=True)
            self.logger.debug(f"[{time.time():.3f}] MUTE_APPLY: Set {mac_id} mute={persisted_mute}")

            if not volume_ok:
                self.logger.warning(f"VOLUME_APPLY: Hardware failed for {mac_id}, state updated to {target_volume_db:.1f} dB (unmute still applied)")
            if not mute_ok:
                # Discarding this outcome is what admitted a client snapserver
                # had, the UI showed online, and CamillaDSP still held muted from
                # its -m start flag: a speaker silent for the whole session, with
                # nothing anywhere to retry it.
                self.logger.warning(f"MUTE_APPLY: Hardware failed for {mac_id} (mute={persisted_mute})")

            if not (volume_ok and mute_ok):
                return False

            self.logger.debug(
                f"[{time.time():.3f}] VOLUME_APPLY: Set {mac_id} to {target_volume_db:.1f} dB"
            )
            return True

        except Exception as e:
            self.logger.error(f"Error applying volume to {mac_id}: {e}", exc_info=True)
            return False

    async def _push_snapclient_config(self, client_ip: str):
        """Push current snapclient buffer config to a remote client on reconnection."""
        try:
            buffer_time, fragments = await resolve_snapclient_config(self.settings_service)

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = f"http://{client_ip}:{CLIENT_API_PORT}/snapclient/config"
                async with session.put(url, json={"buffer_time": buffer_time, "fragments": fragments}) as resp:
                    if resp.status == 200:
                        self.logger.info(f"Snapclient config synced to {client_ip} (buffer_time={buffer_time}ms)")
                    else:
                        body = await resp.text()
                        self.logger.warning(f"Failed to sync snapclient config to {client_ip}: {resp.status} {body}")
        except Exception as e:
            self.logger.warning(f"Could not push snapclient config to {client_ip}: {e}")

    async def _sync_standalone_equalizer_to_client(self, mac_id: str) -> bool:
        """Push a reconnecting REMOTE client's own EQ record to the satellite.

        Reads the registry's per-client EQ store (`client_equalizer[mac]`, the
        single source of truth for any remote client — zone members hold
        identical records), so the satellite recovers ALL of its state: filters,
        compressor, loudness, mono and the master enabled/bypass flag.

        The local client is never driven through this path: it owns
        equalizer.json and is applied to the DAC at boot by CamillaDSPService
        (and updated in place by the per-client access layer for live changes),
        so a local target is an explicit no-op here.
        """
        try:
            client = self.registry.get_client(mac_id) if self.registry else None
            if not client or not client.ip:
                self.logger.warning(f"Cannot sync equalizer to {mac_id}: no IP address")
                return False

            # Local client owns equalizer.json (restored at boot) — not re-synced here.
            if client.is_local:
                return True

            hostname = client.ip

            eq = self.registry.get_client_equalizer(mac_id) if self.registry else None
            self._note_sent(mac_id, "record", eq)
            if not eq:
                self.logger.info(f"SYNC_EQ: No saved settings for {mac_id}, defaults apply")
                return True

            self.logger.info(f"SYNC_EQ: Applying saved settings for {mac_id}")

            if not self._equalizer_client_proxy_service:
                return False

            synced = await self._equalizer_client_proxy_service.apply_record(hostname, eq)
            if synced:
                self.logger.info(f"SYNC_EQ: Synced record to {mac_id}")
                return True

            # The record is the unit of truth, so a partial push is requeued whole:
            # replaying it is idempotent and converges the client in one shot,
            # whereas per-setting retries are what leave a satellite half-applied.
            self.logger.warning(f"SYNC_EQ: Failed to sync record to {mac_id}, queued as pending")
            if self._crossover_service:
                await self._crossover_service.queue_pending_settings(mac_id, "record", eq)
            return False

        except Exception as e:
            self.logger.error(f"Error syncing equalizer to {mac_id}: {e}", exc_info=True)
            return False

    async def _sync_client_gain_to_client(self, mac_id: str) -> bool:
        """Apply a client's level trim on admission — the local unit included.

        Sibling of _sync_standalone_equalizer_to_client, for the one DSP setting
        that is a client property (Client.gain_db) instead of part of the EQ
        record, so the record push carries it nowhere. It is pushed whatever its
        value, 0.0 included: clearing a stale trim is the same operation.

        The two halves recover it differently, which is why this runs for both.
        A satellite persists its own CamillaDSP config, so this only has to carry
        a trim that *changed* while it was away. The local unit persists nothing
        (`_set_config` is set_active only) — so this admission, which also runs
        for it at boot, is what puts its trim back at all. That is the same shape
        as the crossover, which CrossoverService re-applies from CLIENT_CONNECTED.

        force=True: during admission the client is deliberately not online yet
        (set_online_after), and the router would skip it.
        """
        try:
            client = self.registry.get_client(mac_id) if self.registry else None
            if not client or not self._volume_service:
                return True

            gain_db = client.gain_db
            self._note_sent(mac_id, "gain", gain_db)
            eq = self._volume_service.equalizer_controller
            if not await eq.set_equalizer_gain(mac_id, gain_db, force=True):
                self.logger.warning(f"SYNC_GAIN: Hardware refused the trim for {mac_id}")
                return False

            self.logger.info(f"SYNC_GAIN: Applied {gain_db:+.1f} dB trim to {mac_id}")
            return True

        except Exception as e:
            self.logger.warning(f"SYNC_GAIN: Failed to sync the trim to {mac_id}: {e}")
            return False

    async def _pin_snapserver(
        self, mac_id: str, snapcast_id: Optional[str], delay_ms: int
    ) -> bool:
        """Lift snapserver's mutes, push the client's delay and point its group at
        the active source's stream; True once all of it is acknowledged.

        Snapserver stays a passthrough — attenuation is CamillaDSP's job on the
        client. Every snapclient runs `--mixer none`, which ignores snapserver's
        volume but still obeys its mute, the client's or its group's (measured
        on 0.35), and snapserver keeps both across its restarts: one set by any
        other JSON-RPC client would silence the room for good, with Milō
        showing it unmuted. The per-client delay is native Snapcast latency Milō
        owns, and a delay set while the client was away never reached
        snapserver. The group comes back on whatever stream snapserver kept for
        it, and a switch made while the client was away moved only the groups
        snapserver then listed; with no source playing there is no stream to
        choose, and it is left alone.

        No notification carries the client's group (Client.OnConnect names the
        client alone), so it is read here, at each attempt. Every call answers a
        refusal (HTTP error, timeout, JSON-RPC error) with False rather than
        raising, and a status that could not be read is one too: snapserver's
        mute is never assumed lifted. A caller without a snapcast id has nothing
        to pin: snapserver keys its clients by its own id.
        """
        if not (snapcast_id and self._snapcast_service):
            return True
        snapcast = self._snapcast_service
        if not await snapcast.set_volume(snapcast_id, 100):
            self.logger.warning(f"SYNC_RECONNECT: snapserver refused to lift the mute of {mac_id}")
            return False
        if not await snapcast.set_latency(snapcast_id, delay_ms):
            self.logger.warning(f"SYNC_RECONNECT: snapserver refused the {delay_ms} ms delay of {mac_id}")
            return False

        group = snapcast.group_of(await snapcast.get_server_status(), snapcast_id)
        if group is None:
            self.logger.warning(f"SYNC_RECONNECT: snapserver lists no group holding {mac_id}")
            return False
        group_id = group["id"]
        if group["muted"]:
            self.logger.warning(f"SYNC_RECONNECT: snapserver kept the group of {mac_id} muted, lifting it")
            if not await snapcast.set_group_mute(group_id, False):
                self.logger.warning(f"SYNC_RECONNECT: snapserver refused to lift the group mute of {mac_id}")
                return False

        # Read again past each answer: a transition meanwhile points every group
        # at its own stream, and this answer can land after it.
        playing, stream = group["stream_id"], self._active_stream()
        while stream is not None and stream != playing:
            self.logger.info(f"SNAPCAST_SET_STREAM: group={group_id} {playing} -> {stream}")
            if not await snapcast.set_group_stream(group_id, stream):
                self.logger.warning(
                    f"SYNC_RECONNECT: snapserver refused to point {mac_id} at the {stream} stream"
                )
                return False
            playing, stream = stream, self._active_stream()
        return True

    def _active_stream(self) -> Optional[str]:
        """The snapserver stream of the active source; None while none plays."""
        stream = MULTIROOM_STREAMS.get(self.state_machine.system_state.active_source)
        return stream.name if stream else None

    def _note_sent(self, mac_id: str, setting: str, value: Any) -> None:
        """Record what the admission in flight for mac_id pushed (no-op outside one)."""
        sent = self._admission_sent.get(mac_id)
        if sent is not None:
            sent[setting] = value

    async def _deliver_what_moved_during_admission(
        self, mac_id: str, delay_ms: int, snapcast_id: Optional[str]
    ) -> None:
        """Send a client just shown online what changed while it was admitted.

        The admission reads each setting once and pushes it while the client is
        still offline, and a write made meanwhile — a zone mute or move, a
        delay, an EQ edit, a trim — is stored only: the router skips an offline
        client and the routes leave it to the admission. Read again once the
        client is online, whatever differs from what was pushed is sent by the
        ordinary paths, and any later write reaches the client live, so one
        pass closes the window. A setting the admission never pushed (a helper
        that bailed out, the local client's EQ) is not compared.

        The client is online by now, so a failure here is logged and never
        retries the admission.
        """
        try:
            client = self.registry.get_client(mac_id)
            if client is None:
                return
            sent = self._admission_sent.get(mac_id, {})
            store = self._volume_service.state_store
            controller = self._volume_service.equalizer_controller

            # Read and submitted with no await in between, like a volume move:
            # the last value submitted is the stored one. Awaited last, so they
            # travel beside the pushes below; the controller reports a refusal.
            answers = []
            level = store.get_client_volume(mac_id)
            if "level" in sent and level is not None and level != sent["level"]:
                answers.append(controller.submit_volume(mac_id, level))
            mute = store.get_client_mute(mac_id)
            if "mute" in sent and mute != sent["mute"]:
                answers.append(controller.submit_mute(mac_id, mute))

            if snapcast_id and self._snapcast_service and client.delay_ms != delay_ms:
                if not await self._snapcast_service.set_latency(snapcast_id, client.delay_ms):
                    self.logger.warning(
                        f"SYNC_RECONNECT: snapserver refused the {client.delay_ms} ms delay "
                        f"set for {mac_id} during its admission"
                    )

            if "record" in sent and self.registry.get_client_equalizer(mac_id) is not sent["record"]:
                await self._sync_standalone_equalizer_to_client(mac_id)
            if "gain" in sent and client.gain_db != sent["gain"]:
                await self._sync_client_gain_to_client(mac_id)

            for answer in answers:
                await answer
        except Exception as e:
            self.logger.error(
                f"SYNC_RECONNECT: could not deliver what changed during the admission of {mac_id}: {e}",
                exc_info=True,
            )

    async def _sync_reconnecting_client_volume(
        self, mac_id: str, set_online_after: bool = False,
        max_retries: int = 5, retry_delay: float = 3.0,
        snapcast_id: Optional[str] = None
    ) -> bool:
        """
        Bring a client that just (re)appeared to the state Milō holds for it.

        The one admission recipe, shared by all four notifications that can see
        a client arrive: restore the snapserver passthrough (the client's mute
        and its group's) and the client's delay, point its group at the active
        stream, resolve the level the client itself last held, apply it, re-push
        its EQ record and its snapclient buffer config, then show it online and
        send it whatever was changed while this ran — a write meanwhile was only
        stored, since the client was not online yet
        (_deliver_what_moved_during_admission). Retries because a remote client
        is often still booting when its snapclient connects, its API (port 8001)
        answering seconds after snapserver has it.

        Owns the _syncing_mac_ids guard: marks the mac_id as in-flight on
        entry and clears it on exit, preventing duplicate sync tasks for
        the same client. What the admission pushed (_admission_sent) lives
        exactly as long.

        Args:
            mac_id: Client identifier
            set_online_after: If True, mark client online in registry after
                successful sync (keeps client offline/muted in frontend until
                volume is confirmed on hardware).
            snapcast_id: Snapcast client id, when the caller has it. Used to
                restore the snapserver passthrough, the delay and the stream
                (see _pin_snapserver).
        """
        if not self.registry or not self._volume_service:
            return False

        if mac_id in self._syncing_mac_ids:
            self.logger.debug(f"SYNC_RECONNECT: {mac_id} already syncing, skipping duplicate")
            return False

        self._syncing_mac_ids.add(mac_id)
        self._admission_sent[mac_id] = {}
        try:
            return await self._do_sync_reconnecting_client_volume(
                mac_id, set_online_after, max_retries, retry_delay, snapcast_id
            )
        finally:
            self._syncing_mac_ids.discard(mac_id)
            self._admission_sent.pop(mac_id, None)

    async def _do_sync_reconnecting_client_volume(
        self, mac_id: str, set_online_after: bool,
        max_retries: int, retry_delay: float,
        snapcast_id: Optional[str] = None
    ) -> bool:
        """Internal sync implementation (called under _syncing_mac_ids guard)."""
        target_volume: Optional[float] = None
        for attempt in range(max_retries + 1):
            # A client deleted during a retry wait is not admitted: the apply
            # below would put its level back in the store the delete emptied.
            client = self.registry.get_client(mac_id)
            if client is None:
                self.logger.info(f"SYNC_RECONNECT: {mac_id} removed during admission, abandoning")
                return False
            try:
                # Pinned inside the attempt, not before them: a refusal is a
                # failed attempt, retried like an apply the satellite refused,
                # and never followed by showing the client online.
                pushed_delay_ms = client.delay_ms
                pinned = await self._pin_snapserver(mac_id, snapcast_id, pushed_delay_ms)
                # Asked again past the pin's awaits, with none between here and
                # the apply's store write: a delete that landed while snapserver
                # answered would be undone the same way.
                if self.registry.get_client(mac_id) is None:
                    self.logger.info(f"SYNC_RECONNECT: {mac_id} removed during admission, abandoning")
                    return False
                # Resolved on every attempt, after the pin: a zone or global
                # move made while this one waited moved the away room's stored
                # level, and the room comes back where its room went, not where
                # it stood when the admission began. The apply writes the level
                # back to the store, so one read before an await undid a move.
                target_volume = self._resolve_target_volume(mac_id)
                self.logger.info(f"SYNC_RECONNECT: {mac_id} target={target_volume:.1f} dB")
                volume_synced = pinned and await self._apply_target_volume_to_client(mac_id, target_volume)
                if volume_synced:
                    # Re-push the client's EQ record now that it's reachable again.
                    # This path (Server.OnUpdate online-status flip) historically
                    # synced volume only; without this, a member that missed a
                    # zone-EQ change while offline would keep stale EQ until the next
                    # full Client.OnConnect. No-op for the local client (is_local
                    # guard inside the callee). Done before showing online so the
                    # client is fully configured first.
                    await self._sync_standalone_equalizer_to_client(mac_id)

                    # The level trim rides beside the record, not inside it: it
                    # is a client property, so the push above carries it nowhere
                    # and a trim changed while the speaker was away would never
                    # reach its DSP.
                    await self._sync_client_gain_to_client(mac_id)

                    # Same reason the EQ record is re-pushed: buffer settings
                    # changed while the client was away never reached it, and
                    # only the Client.OnConnect path used to send them.
                    client = self.registry.get_client(mac_id)
                    if client and not client.is_local:
                        self._bg.spawn(
                            self._push_snapclient_config(client.ip),
                            label=f"push_snapclient_config_{client.ip}",
                        )
                        # The server's WiFi as a standby link, for when the
                        # satellite's cable goes.
                        if self._satellite_wifi_service is not None:
                            self._bg.spawn(
                                self._satellite_wifi_service.push(client.ip),
                                label=f"push_wifi_{client.ip}",
                            )

                    # Volume confirmed on hardware — now safe to show online
                    if set_online_after and self.registry:
                        await self.registry.set_client_online(mac_id, True)
                        await self._deliver_what_moved_during_admission(
                            mac_id, pushed_delay_ms, snapcast_id
                        )
                    if self._volume_service:
                        await self._volume_service.broadcast_volume_state(show_bar=False)
                    self.logger.info(f"SYNC_RECONNECT: {mac_id} synced to {target_volume:.1f} dB (attempt {attempt + 1})")
                    return True
                elif pinned:
                    self.logger.warning(
                        f"SYNC_RECONNECT: {mac_id} hardware apply returned False "
                        f"(attempt {attempt + 1}/{max_retries + 1})"
                    )
            except Exception as e:
                self.logger.warning(f"SYNC_RECONNECT: {mac_id} attempt {attempt + 1} failed: {e}")

            if attempt < max_retries:
                self.logger.info(f"SYNC_RECONNECT: {mac_id} retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)

        target = f" (target was {target_volume:.1f} dB)" if target_volume is not None else ""
        self.logger.warning(
            f"SYNC_RECONNECT: {mac_id} GAVE UP after {max_retries + 1} attempts — "
            f"client may be desynchronized{target}"
        )
        return False

