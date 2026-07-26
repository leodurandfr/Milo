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
from typing import Dict, Any, Optional

import aiohttp

from backend.core.multiroom.models import ReconnectionContext
from backend.core.multiroom.client_registry import (
    ClientRegistryService,
    REGISTRY_EVENT_CLASSES,
)
from backend.config.constants import CLIENT_API_PORT, DEFAULT_VOLUME_DB, get_client_display_name
from backend.shared.background import BackgroundTaskSet


class SnapcastWebSocketService:
    """
    WebSocket service for Snapcast notifications.

    Handles real-time events from Snapcast server:
    - Client connect/disconnect
    - Volume and mute changes
    - Server updates (availability changes)
    """

    def __init__(
        self,
        state_machine,
        routing_service,
        settings_service=None,
        host: str = "localhost",
        port: int = 1780,
        snapcast_service=None,
        crossover_service=None,
        equalizer_client_proxy_service=None,
        pending_clients_service=None,
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

        # Deduplication: track mac_ids with an in-flight sync task
        self._syncing_mac_ids: set = set()

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
            self.session = aiohttp.ClientSession()
            self.running = True

            # Single source of truth: AudioRoutingService.multiroom_enabled
            # (settings-backed). No fallback chain needed — the
            # routing service IS the authority on whether multiroom is on.
            multiroom_state = bool(self.routing_service.multiroom_enabled) if self.routing_service else False

            self.should_connect = multiroom_state

            if self.should_connect:
                self.logger.info("Multiroom already enabled, starting WebSocket connection")
                self.reconnect_task = self._bg.spawn(self._connection_loop(), label="connection_loop")
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
            self.reconnect_task = self._bg.spawn(self._connection_loop(), label="connection_loop")

    async def stop_connection(self) -> None:
        """Stop WebSocket connection when multiroom is disabled."""
        if not self.should_connect:
            return  # Already stopped

        self.logger.info("Stopping Snapcast WebSocket connection (multiroom disabled)")
        self.should_connect = False

        # Cancel all in-flight background tasks (connection loop, sync retries,
        # config push, etc.)
        await self._bg.cancel_all()
        self.reconnect_task = None
        self._syncing_mac_ids.clear()

        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        self._ready_event.clear()

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

        await self._bg.cancel_all()

        if self.websocket:
            await self.websocket.close()

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

    async def _connection_loop(self) -> None:
        """Connection loop with intelligent reconnection."""
        reconnect_delay = 5
        max_delay = 30

        while self.running and self.should_connect:
            try:
                await self._connect_and_listen()
                reconnect_delay = 5
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"WebSocket connection error: {e}")

            if self.running and self.should_connect:
                self.logger.info(f"Reconnecting to Snapcast WebSocket in {reconnect_delay} seconds...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_delay)

    async def _connect_and_listen(self) -> None:
        """Connect and listen for WebSocket messages."""
        # Clear ready state so wait_for_ready() blocks until this connection
        # is fully initialized. Without this, a stale True from a previous
        # connection causes callers to proceed against a dead socket.
        self._ready_event.clear()

        try:
            self.logger.info(f"Connecting to Snapcast WebSocket: {self.ws_url}")

            timeout = aiohttp.ClientTimeout(total=5)
            self.websocket = await self.session.ws_connect(self.ws_url, timeout=timeout)
            self.logger.info("Connected to Snapcast WebSocket")

            # Send initial ping to verify connection
            await self._send_request("Server.GetRPCVersion")

            # Initialize already connected clients
            self._is_initializing = True
            await self._initialize_existing_clients()
            self._bg.spawn(self._clear_init_flag_after_delay(2.0), label="clear_init_flag")

            self._ready_event.set()
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

    async def _clear_init_flag_after_delay(self, delay: float) -> None:
        """Clear the initialization flag after a delay."""
        await asyncio.sleep(delay)
        self._is_initializing = False
        self.logger.debug("Snapcast WebSocket initialization phase complete")

    async def _initialize_existing_clients(self) -> None:
        """Initialize clients already connected at WebSocket connection time."""
        try:
            self.logger.debug(f"[{time.time():.3f}] INIT_CLIENTS: Starting initialization")

            if not self._snapcast_service:
                self.logger.warning("SnapcastService not available")
                return

            status = await self._snapcast_service.get_server_status()
            if not status:
                self.logger.warning("Could not get Snapcast status")
                return

            groups = status.get('server', {}).get('groups', [])

            for group in groups:
                for client in group.get('clients', []):
                    if not client.get('connected'):
                        continue

                    client_id = client.get('id')

                    # Get mac_id for this client using canonical method
                    host = client.get("host", {})
                    hostname = host.get("name", "")
                    ip = host.get("ip", "").replace("::ffff:", "")
                    mac = host.get("mac", "")

                    if ClientRegistryService.is_stale_local_client(client_id or "", ip):
                        self.logger.warning(f"INIT_CLIENTS: Skipping stale local client id={client_id}")
                        continue

                    mac_id = ClientRegistryService.compute_mac_id(hostname, ip, mac)
                    client_name = client.get("config", {}).get("name") or get_client_display_name(hostname) or mac_id

                    # Check if client is already in registry
                    is_new_client = self.registry.get_client(mac_id) is None if self.registry else True

                    if is_new_client:
                        is_local = (ip == "127.0.0.1")
                        local_marker = " LOCAL CLIENT" if is_local else ""
                        self.logger.debug(f"[{time.time():.3f}] INIT_CLIENTS: New client {client_id} (mac_id: {mac_id}){local_marker}")

                    if self.registry:
                        kwargs = {"host": hostname}
                        is_local = (ip == "127.0.0.1")
                        if is_local and self._volume_service:
                            # Sync hardware volume_control to registry (e.g. DAC mode read at boot)
                            kwargs["volume_control"] = self._volume_service.volume_control
                        await self.registry.register_client(mac_id, client_name, ip, **kwargs)
                        await self.registry.set_client_online(mac_id, True)

                    if is_new_client:
                        self.logger.debug(f"[{time.time():.3f}] INIT_CLIENTS: Registered {mac_id}")
                        # Sync volume from snapserver for new clients
                        snapcast_volume = client.get("config", {}).get("volume", {}).get("percent", 0)
                        self.logger.debug(f"[{time.time():.3f}] INIT_CLIENTS: Syncing volume from snapserver: {snapcast_volume}%")
                        await self._sync_existing_client_volume(client_id, client)

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

            self.logger.debug("SERVER_UPDATE: Fetching client list from Snapcast...")
            all_clients = await self._snapcast_service.get_clients()
            self.logger.debug(f"SERVER_UPDATE: Got {len(all_clients)} clients from Snapcast")
            current_mac_ids = {c["mac_id"] for c in all_clients}
            known_mac_ids = set(self.registry.get_client_ids()) if self.registry else set()

            await self._process_new_clients(all_clients, known_mac_ids)
            await self._process_disconnected_clients(current_mac_ids, known_mac_ids)
            await self._process_online_status_changes(all_clients)

        except Exception as e:
            self.logger.error(f"Error handling Server.OnUpdate: {e}", exc_info=True)

    async def _process_new_clients(self, all_clients: list, known_mac_ids: set) -> None:
        """Register clients present in Snapcast but not yet in the registry."""
        for client in all_clients:
            mac_id = client["mac_id"]

            if mac_id not in known_mac_ids:
                self.logger.info(f"NEW CLIENT detected: {mac_id} (snapcast_id: {client['id']})")

                if mac_id in self._syncing_mac_ids:
                    self.logger.debug(f"Skipping Server.OnUpdate init for {mac_id} - sync already in flight")
                    continue

                if self.registry:
                    # Register but keep OFFLINE — client stays invisible in
                    # frontend until volume is synced and confirmed on hardware.
                    await self.registry.register_client(mac_id, client["name"], client["ip"], host=client["host"])

                # Sync volume then set online. The sync task owns the
                # _syncing_mac_ids guard and clears it when done.
                if client["online"]:
                    self._bg.spawn(
                        self._sync_reconnecting_client_volume(mac_id, set_online_after=True),
                        label=f"sync_new_client_{mac_id}",
                    )

    async def _process_disconnected_clients(self, current_mac_ids: set, known_mac_ids: set) -> None:
        """Mark registry clients as offline when they no longer appear in Snapcast."""
        for mac_id in known_mac_ids:
            if mac_id not in current_mac_ids:
                self.logger.info(f"CLIENT DISCONNECTED: {mac_id}")
                if self.registry:
                    await self.registry.set_client_online(mac_id, False)

    async def _process_online_status_changes(self, all_clients: list) -> None:
        """Detect and apply online/offline transitions for known clients."""
        for client in all_clients:
            mac_id = client["mac_id"]
            online = client["online"]

            registry_client = self.registry.get_client(mac_id) if self.registry else None
            if not registry_client:
                continue
            previous_online = registry_client.online

            if online != previous_online:
                self.logger.info(f"Client {mac_id} online status: {previous_online} -> {online}")

                if online and not previous_online:
                    # Client reconnecting: do NOT set online yet — the sync task
                    # sets online after hardware confirms volume (set_online_after=True).
                    # This prevents a window where the frontend shows the client
                    # at a stale volume before sync completes.
                    self._bg.spawn(
                        self._sync_reconnecting_client_volume(mac_id, set_online_after=True),
                        label=f"sync_reconnect_{mac_id}",
                    )
                elif self.registry:
                    # Client going offline: update immediately
                    await self.registry.set_client_online(mac_id, False)

                # Crossover recalculation is handled by CrossoverService._handle_registry_event
                # via CLIENT_CONNECTED/CLIENT_DISCONNECTED events emitted by set_client_online()

    async def _handle_response(self, response: Dict[str, Any]) -> None:
        """Process a response to a request."""
        if "error" in response:
            self.logger.error(f"Snapcast RPC error: {response['error']}")

    async def _handle_client_connect(self, params: Dict[str, Any]) -> None:
        """Handle client connected event."""
        client = params.get("client", {})
        client_id = client.get("id")

        client_host = client.get("host", {}).get("name") or "Unknown"
        client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")
        client_mac = client.get("host", {}).get("mac", "")

        # Compute mac_id early so we can dedup by stable identifier
        mac_id = ClientRegistryService.compute_mac_id(client_host, client_ip, client_mac)

        if mac_id in self._syncing_mac_ids:
            self.logger.debug(f"Skipping Client.OnConnect for {mac_id} - sync already in flight")
            return

        self._syncing_mac_ids.add(mac_id)

        try:
            snapcast_volume = client.get("config", {}).get("volume", {}).get("percent", 100)
            client_name = client.get("config", {}).get("name") or get_client_display_name(client_host) or mac_id

            is_local = (client_ip == "127.0.0.1")
            local_marker = " LOCAL CLIENT" if is_local else ""
            self.logger.debug(f"[{time.time():.3f}] CLIENT_CONNECT: New client {client_id} (mac_id: {mac_id}){local_marker}")
            self.logger.debug(f"  - Name: {client_name}, Host: {client_host}, IP: {client_ip}")
            self.logger.debug(f"  - Snapcast volume: {snapcast_volume}% (passthrough)")

            # Check if this client has pending configuration (registered via API before Snapcast)
            pending = None
            if self._pending_clients_service:
                pending = self._pending_clients_service.get_client(mac_id)
                if pending and pending.get("name"):
                    self.logger.info(f"  - Pending client matched: name='{pending['name']}', speaker_type='{pending.get('speaker_type')}'")

            # Register client using new API (but don't set online yet - wait for volume sync)
            # Use pending name/speaker_type if available (set during configuration flow)
            reg_name = (pending.get("name") if pending else None) or client_name
            reg_speaker_type = pending.get("speaker_type") if pending else None

            if self.registry:
                kwargs = {"host": client_host}
                if reg_speaker_type:
                    kwargs["speaker_type"] = reg_speaker_type
                if pending:
                    kwargs["volume_control"] = pending.get("volume_control", True)
                elif is_local and self._volume_service:
                    # Sync hardware volume_control to registry (e.g. DAC mode read at boot)
                    kwargs["volume_control"] = self._volume_service.volume_control
                # When no pending entry and not local, volume_control is not passed —
                # register_client preserves existing value for known clients
                await self.registry.register_client(mac_id, reg_name, client_ip, **kwargs)

            self.logger.debug(f"[{time.time():.3f}] CLIENT_CONNECT: Calling volume sync for {client_id}")
            sync_status = await self._notify_volume_service_client_connected(client_id, client, mac_id)

            # Only set online if volume was successfully applied to hardware.
            # If sync failed, the fire-and-forget retry from _process_new_clients
            # will set online via set_online_after=True when hardware confirms.
            if sync_status.get("volume_synced") and self.registry:
                await self.registry.set_client_online(mac_id, True)
            elif not sync_status.get("volume_synced"):
                self.logger.warning(
                    f"CLIENT_CONNECT: {mac_id} volume sync FAILED — client stays offline until retry succeeds "
                    f"(context: {sync_status.get('context', 'unknown')})"
                )

            # Crossover recalculation is handled by CrossoverService._handle_registry_event
            # via CLIENT_CONNECTED event emitted by set_client_online()

            # Transfer complete — remove from pending storage
            if pending and self._pending_clients_service:
                await self._pending_clients_service.remove_client(mac_id)

            # Push snapclient buffer config to remote clients (fire-and-forget)
            if not is_local:
                self._bg.spawn(
                    self._push_snapclient_config(client_ip),
                    label=f"push_snapclient_config_{client_ip}",
                )
        finally:
            self._syncing_mac_ids.discard(mac_id)

    async def _handle_client_disconnect(self, params: Dict[str, Any]) -> None:
        """Handle client disconnected event."""
        client = params.get("client", {})

        client_host = client.get("host", {}).get("name", "Unknown")
        client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")
        client_mac = client.get("host", {}).get("mac", "")

        # Compute mac_id using canonical method
        mac_id = ClientRegistryService.compute_mac_id(client_host, client_ip, client_mac)

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
                            mac_id = ClientRegistryService.compute_mac_id(
                                host_info.get("name", ""),
                                host_info.get("ip", "").replace("::ffff:", ""),
                                host_info.get("mac", "")
                            )
                            break
            except Exception as e:
                self.logger.warning(f"Could not resolve mac_id for {client_id}: {e}")

        # Update the registry so the name change persists to settings.json
        if self.registry and name:
            await self.registry.update_client(mac_id, name=name)

    async def _notify_volume_service_client_connected(self, client_id: str, client: Dict[str, Any], mac_id: str) -> Dict[str, Any]:
        """
        Initialize new client: set Multiroom group, sync volume, apply pending settings.

        Returns:
            Dict with sync status: {volume_synced, equalizer_synced, pending_applied}
        """
        sync_status = {
            "volume_synced": False,
            "equalizer_synced": False,
            "pending_applied": False
        }
        try:
            self.logger.debug(f"[{time.time():.3f}] NOTIFY_VOLUME: Starting volume sync for {client_id}")

            sync_status = await self._sync_existing_client_volume(client_id, client)

            # Apply pending settings (keyed by mac_id)
            if self._crossover_service:
                has_pending = self._crossover_service.has_pending_settings(mac_id)
                if has_pending:
                    self.logger.info(f"  - Applying pending settings for reconnected client {mac_id}")
                    pending_success = await self._crossover_service.apply_pending_settings(mac_id)
                    sync_status["pending_applied"] = pending_success

        except Exception as e:
            self.logger.error(f"Error initializing new client: {e}", exc_info=True)

        return sync_status

    async def _sync_existing_client_volume(self, client_id: str, client: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ensure existing client is in Multiroom group with correct volume.

        Sequence:
        1. Detect reconnection context
        2. Join client to multiroom group
        3. Set Snapcast volume to 100% passthrough
        4. Apply correct equalizer volume based on context
        5. Sync zone/standalone equalizer settings based on context

        Returns:
            Dict with sync status: {volume_synced, equalizer_synced, pending_applied, context}
        """
        sync_status = {
            "volume_synced": False,
            "equalizer_synced": False,
            "pending_applied": False,
            "context": None
        }
        try:
            self.logger.debug(f"[{time.time():.3f}] SYNC_VOLUME: Starting for {client_id}")

            if not self._snapcast_service:
                self.logger.warning("SnapcastService not available")
                return sync_status

            host = client.get("host", {})
            hostname = host.get("name", "")
            ip = host.get("ip", "").replace("::ffff:", "")
            mac = host.get("mac", "")
            mac_id = ClientRegistryService.compute_mac_id(hostname, ip, mac)

            # 1. Detect reconnection context
            context = ReconnectionContext.STANDALONE_ALONE  # Default
            if self.registry:
                context = self.registry.get_reconnection_context(mac_id)
            sync_status["context"] = context.value
            self.logger.debug(
                f"[{time.time():.3f}] SYNC_VOLUME: Detected reconnection context for {mac_id}: {context.value}"
            )

            # 2. Set Snapcast volume to 100% passthrough
            await self._snapcast_service.set_volume(client_id, 100)
            self.logger.debug(f"[{time.time():.3f}] SYNC_VOLUME: Snapcast volume set to 100% for {client_id}")

            # 4. Apply correct equalizer volume based on context
            target_volume = self._resolve_target_volume(mac_id, context)
            self.logger.debug(
                f"[{time.time():.3f}] SYNC_VOLUME: Applying target volume "
                f"{target_volume:.1f} dB for {mac_id} (context: {context.value})"
            )
            volume_synced = await self._apply_target_volume_to_client(mac_id, target_volume)
            sync_status["volume_synced"] = volume_synced

            # 5. Sync equalizer settings. In the unified per-client model every
            #    remote client (zone member or standalone) recovers its OWN EQ
            #    record — members of a zone hold identical records, so there is no
            #    separate zone-EQ path. Only the volume context above still differs
            #    by zone/standalone. The local client owns equalizer.json (restored
            #    at boot by CamillaDSPService) and is skipped via an explicit
            #    is_local guard inside the callee, so this is a no-op for it.
            equalizer_synced = True
            if self.registry:
                self.logger.debug(
                    f"[{time.time():.3f}] SYNC_EQ: Syncing per-client equalizer for {mac_id} "
                    f"(context: {context.value})"
                )
                equalizer_synced = await self._sync_standalone_equalizer_to_client(mac_id)
            sync_status["equalizer_synced"] = equalizer_synced

            # 6. Broadcast volume state to frontend
            # This notifies UI about the reconnected client with its synced volume
            if volume_synced:
                if self._volume_service:
                    try:
                        await self._volume_service.broadcast_volume_state(show_bar=False)
                        self.logger.debug(
                            f"[{time.time():.3f}] SYNC_BROADCAST: Volume state broadcast for {mac_id}"
                        )
                    except Exception as e:
                        self.logger.warning(f"Failed to broadcast volume state: {e}")

            self.logger.debug(
                f"[{time.time():.3f}] SYNC_VOLUME: Client {client_id} fully initialized "
                f"(context: {context.value})"
            )

        except Exception as e:
            self.logger.error(f"Error syncing existing client {client_id}: {e}", exc_info=True)

        return sync_status

    def _resolve_target_volume(self, mac_id: str, context: ReconnectionContext) -> float:
        """
        Resolve target reconnection volume for any context.

        Resolution order:
        1. If others are online: zone average (IN_ZONE) or global average (STANDALONE)
        2. Configured startup_volume_db
        3. DEFAULT_VOLUME_DB constant
        """
        # Level 1: peer average when others are online
        if context == ReconnectionContext.IN_ZONE_OTHERS_ONLINE:
            client = self.registry.get_client(mac_id) if self.registry else None
            if client and client.zone_id:
                avg = self.registry.get_zone_average_volume(client.zone_id, exclude_mac_id=mac_id)
                if avg is not None:
                    self.logger.info(f"Using zone average {avg:.1f} dB for {mac_id}")
                    return avg
            self.logger.warning(f"Zone average unavailable for {mac_id}, falling back to startup volume")

        elif context == ReconnectionContext.STANDALONE_OTHERS_ONLINE:
            if self.registry:
                avg = self.registry.get_global_average_volume(exclude_mac_id=mac_id)
                if avg is not None:
                    self.logger.info(f"Using global average {avg:.1f} dB for {mac_id}")
                    return avg
            self.logger.warning(f"Global average unavailable for {mac_id}, falling back to startup volume")

        # Level 2: startup_volume_db from VolumeService configuration
        if self._volume_service:
            startup_volume = self._volume_service.volume_config.startup_volume_db
            self.logger.info(f"Using startup volume {startup_volume:.1f} dB for {mac_id}")
            return startup_volume

        # Level 3: constant fallback
        self.logger.warning(f"No volume service available, using DEFAULT_VOLUME_DB for {mac_id}")
        return DEFAULT_VOLUME_DB

    async def _apply_target_volume_to_client(
        self,
        mac_id: str,
        target_volume_db: float
    ) -> bool:
        """
        Apply a specific volume to a client's equalizer and update state.

        Always updates state store and registry (so the UI shows the correct
        target volume). Applies to hardware on best-effort — returns False if
        the hardware call fails so callers can retry.

        Args:
            mac_id: Client identifier
            target_volume_db: Volume to set in dB

        Returns:
            True if hardware application succeeded, False otherwise.
            State/registry are updated regardless.
        """
        try:
            if not self._volume_service:
                self.logger.warning(f"No volume_service available to apply volume for {mac_id}")
                return False

            # Always update state store and registry first (UI correctness)
            await self._volume_service.state_store.set_client_volume(mac_id, target_volume_db)
            if self.registry:
                await self.registry.update_volume(mac_id, volume_db=target_volume_db)

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
            persisted_mute = self._volume_service.state_store.get_client_mute(mac_id)
            await eq.set_equalizer_mute(mac_id, persisted_mute, force=True)
            self.logger.debug(f"[{time.time():.3f}] MUTE_APPLY: Set {mac_id} mute={persisted_mute}")

            if not volume_ok:
                self.logger.warning(f"VOLUME_APPLY: Hardware failed for {mac_id}, state updated to {target_volume_db:.1f} dB (unmute still applied)")
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
            buffer_time = 80
            fragments = 4
            if self.settings_service:
                val = await self.settings_service.get_setting('multiroom.snapclient_buffer_time')
                if val is not None:
                    buffer_time = val
                val = await self.settings_service.get_setting('multiroom.snapclient_fragments')
                if val is not None:
                    fragments = val

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

    async def _apply_equalizer_setting(
        self, hostname: str, mac_id: str, setting_type: str, data: Any
    ) -> bool:
        """
        Push a single DSP setting to a REMOTE client via the proxy.

        The local client is restored from equalizer.json by CamillaDSPService and
        is never driven through this re-sync path.

        Args:
            hostname: Client IP address
            mac_id: Client identifier for logging
            setting_type: "filter/<id>", "compressor", "loudness", "mono", or "enabled"
            data: Setting payload dict (for "mono"/"enabled": {"enabled": bool})

        Returns:
            True if applied successfully, False on failure
        """
        try:
            if not self._equalizer_client_proxy_service:
                return False
            await self._equalizer_client_proxy_service.request(hostname, "PUT", f"/equalizer/{setting_type}", data)
            return True
        except Exception as e:
            self.logger.warning(f"Failed to apply equalizer {setting_type} to {mac_id}: {e}")
            return False

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
            if not eq:
                self.logger.info(f"SYNC_EQ: No saved settings for {mac_id}, defaults apply")
                return True

            self.logger.info(f"SYNC_EQ: Applying saved settings for {mac_id}")
            synced = []
            failed = []
            filters_failed = []

            # Sync filters
            if eq.filters:
                for flt in eq.filters:
                    if not flt.id:
                        continue
                    filter_data = {
                        'freq': flt.frequency, 'gain': flt.gain, 'q': flt.q,
                        'filter_type': flt.filter_type.value if hasattr(flt.filter_type, 'value') else flt.filter_type
                    }
                    if await self._apply_equalizer_setting(hostname, mac_id, f"filter/{flt.id}", filter_data):
                        synced.append(f"filter:{flt.id}")
                    else:
                        failed.append(f"filter:{flt.id}")
                        filters_failed.append(flt.to_dict())

            # Queue failed filters for retry
            if filters_failed and self._crossover_service:
                await self._crossover_service.queue_pending_settings(mac_id, "filters", filters_failed)

            # Sync compressor
            if eq.compressor:
                data = eq.compressor.to_dict()
                if await self._apply_equalizer_setting(hostname, mac_id, "compressor", data):
                    synced.append("compressor")
                else:
                    failed.append("compressor")
                    if self._crossover_service:
                        await self._crossover_service.queue_pending_settings(mac_id, "compressor", data)

            # Sync loudness
            if eq.loudness:
                data = eq.loudness.to_dict()
                if await self._apply_equalizer_setting(hostname, mac_id, "loudness", data):
                    synced.append("loudness")
                else:
                    failed.append("loudness")
                    if self._crossover_service:
                        await self._crossover_service.queue_pending_settings(mac_id, "loudness", data)

            # Sync mono
            mono_data = {"enabled": eq.mono}
            if await self._apply_equalizer_setting(hostname, mac_id, "mono", mono_data):
                synced.append("mono")
            else:
                failed.append("mono")
                if self._crossover_service:
                    await self._crossover_service.queue_pending_settings(mac_id, "mono", mono_data)

            # Sync master enabled/bypass LAST (after the effects it gates).
            enabled_data = {"enabled": eq.enabled}
            if await self._apply_equalizer_setting(hostname, mac_id, "enabled", enabled_data):
                synced.append("enabled")
            else:
                failed.append("enabled")
                if self._crossover_service:
                    await self._crossover_service.queue_pending_settings(mac_id, "enabled", enabled_data)

            if synced:
                self.logger.info(f"SYNC_EQ: Synced {synced} to {mac_id}")
            if failed:
                self.logger.warning(f"SYNC_EQ: Failed to sync {failed} to {mac_id}")

            return len(failed) == 0

        except Exception as e:
            self.logger.error(f"Error syncing equalizer to {mac_id}: {e}", exc_info=True)
            return False

    async def _sync_reconnecting_client_volume(
        self, mac_id: str, set_online_after: bool = False,
        max_retries: int = 5, retry_delay: float = 3.0
    ) -> bool:
        """
        Sync volume for a known client that just came back online.

        Lightweight version of _sync_existing_client_volume that works with
        just a mac_id (no full Snapcast client object needed). Retries on
        failure because remote clients may still be booting when their
        snapclient connects before their API (port 8001) is ready.

        Owns the _syncing_mac_ids guard: marks the mac_id as in-flight on
        entry and clears it on exit, preventing duplicate sync tasks for
        the same client.

        Args:
            mac_id: Client identifier
            set_online_after: If True, mark client online in registry after
                successful sync (keeps client offline/muted in frontend until
                volume is confirmed on hardware).
        """
        if not self.registry or not self._volume_service:
            return False

        if mac_id in self._syncing_mac_ids:
            self.logger.debug(f"SYNC_RECONNECT: {mac_id} already syncing, skipping duplicate")
            return False

        self._syncing_mac_ids.add(mac_id)
        try:
            return await self._do_sync_reconnecting_client_volume(
                mac_id, set_online_after, max_retries, retry_delay
            )
        finally:
            self._syncing_mac_ids.discard(mac_id)

    async def _do_sync_reconnecting_client_volume(
        self, mac_id: str, set_online_after: bool,
        max_retries: int, retry_delay: float
    ) -> bool:
        """Internal sync implementation (called under _syncing_mac_ids guard)."""
        context = self.registry.get_reconnection_context(mac_id)
        target_volume = self._resolve_target_volume(mac_id, context)

        self.logger.info(
            f"SYNC_RECONNECT: {mac_id} context={context.value}, "
            f"target={target_volume:.1f} dB"
        )

        for attempt in range(max_retries + 1):
            try:
                volume_synced = await self._apply_target_volume_to_client(mac_id, target_volume)
                if volume_synced:
                    # Re-push the client's EQ record now that it's reachable again.
                    # This path (Server.OnUpdate online-status flip) historically
                    # synced volume only; without this, a member that missed a
                    # zone-EQ change while offline would keep stale EQ until the next
                    # full Client.OnConnect. No-op for the local client (is_local
                    # guard inside the callee). Done before showing online so the
                    # client is fully configured first.
                    await self._sync_standalone_equalizer_to_client(mac_id)
                    # Volume confirmed on hardware — now safe to show online
                    if set_online_after and self.registry:
                        await self.registry.set_client_online(mac_id, True)
                    if self._volume_service:
                        await self._volume_service.broadcast_volume_state(show_bar=False)
                    self.logger.info(f"SYNC_RECONNECT: {mac_id} synced to {target_volume:.1f} dB (attempt {attempt + 1})")
                    return True
                else:
                    self.logger.warning(
                        f"SYNC_RECONNECT: {mac_id} hardware apply returned False "
                        f"(attempt {attempt + 1}/{max_retries + 1})"
                    )
            except Exception as e:
                self.logger.warning(f"SYNC_RECONNECT: {mac_id} attempt {attempt + 1} failed: {e}")

            if attempt < max_retries:
                self.logger.info(f"SYNC_RECONNECT: {mac_id} retrying in {retry_delay}s...")
                await asyncio.sleep(retry_delay)

        self.logger.warning(
            f"SYNC_RECONNECT: {mac_id} GAVE UP after {max_retries + 1} attempts — "
            f"client may be desynchronized (target was {target_volume:.1f} dB)"
        )
        return False

