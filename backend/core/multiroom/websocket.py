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

from backend.core.events import EventBus, get_event_bus
from backend.core.multiroom.models import ReconnectionContext
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.config.constants import CLIENT_API_PORT, DEFAULT_VOLUME_DB


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
        event_bus: EventBus = None
    ):
        self.state_machine = state_machine
        self.routing_service = routing_service
        self.settings_service = settings_service
        self.host = host
        self.port = port
        self.ws_url = f"ws://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)
        self.event_bus = event_bus or get_event_bus()

        # Client registry - set after construction via state_machine.client_registry
        self._registry: Optional["ClientRegistryService"] = None

        # Connection state
        self.session: Optional[aiohttp.ClientSession] = None
        self.websocket: Optional[aiohttp.ClientWebSocketResponse] = None
        self.running = False
        self.should_connect = False
        self.reconnect_task = None

        # Deduplication: track client IDs currently being processed
        self._processing_client_ids: set = set()

        # Initialization state - suppress verbose logs during startup
        self._is_initializing = False

        # ID for JSON-RPC requests
        self.request_id = 0

        # Ready event - signaled when WebSocket is connected and initialized
        self._ready_event = asyncio.Event()

        # Services injected post-construction via setters (resolved in initialize_services)
        self._snapcast_service = None
        self._volume_service = None
        self._crossover_service = None
        self._equalizer_client_proxy_service = None
        self._equalizer_settings_sync_service = None
        self._camilladsp_service = None

    @property
    def registry(self) -> Optional["ClientRegistryService"]:
        """Get the client registry from state_machine if not set directly."""
        if self._registry:
            return self._registry
        return getattr(self.state_machine, 'client_registry', None)

    async def initialize(self) -> bool:
        """Initialize the WebSocket service."""
        try:
            self.logger.info(f"Initializing Snapcast WebSocket service: {self.ws_url}")
            self.session = aiohttp.ClientSession()
            self.running = True

            # Check initial multiroom state from SETTINGS (most reliable at boot time)
            multiroom_state = False

            if self.settings_service:
                multiroom_state = await self.settings_service.get_setting("routing.multiroom_enabled") or False
                if multiroom_state:
                    self.logger.info("Multiroom enabled from settings")

            # Fallback: check routing_service state
            if not multiroom_state and self.routing_service:
                routing_state = self.routing_service.get_state()
                multiroom_state = routing_state.get('multiroom_enabled', False)

            # Final fallback: check systemd services
            if not multiroom_state and self.routing_service:
                snapcast_status = await self.routing_service.get_snapcast_status()
                multiroom_state = snapcast_status.get("multiroom_available", False)
                if multiroom_state:
                    self.logger.info("Multiroom detected from systemd services (fallback)")

            self.should_connect = multiroom_state

            if self.should_connect:
                self.logger.info("Multiroom already enabled, starting WebSocket connection")
                self.reconnect_task = asyncio.create_task(self._connection_loop())
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
            self.reconnect_task = asyncio.create_task(self._connection_loop())

    async def stop_connection(self) -> None:
        """Stop WebSocket connection when multiroom is disabled."""
        if not self.should_connect:
            return  # Already stopped

        self.logger.info("Stopping Snapcast WebSocket connection (multiroom disabled)")
        self.should_connect = False

        # Cancel reconnection task
        if self.reconnect_task:
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass
            self.reconnect_task = None

        # Close current WebSocket connection
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        # Reset ready event
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

        if self.reconnect_task:
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass

        if self.websocket:
            await self.websocket.close()

        if self.session:
            await self.session.close()

    # === Service setters (circular dependency resolution) ===

    def set_snapcast_service(self, service) -> None:
        """Set SnapcastService dependency."""
        self._snapcast_service = service

    def set_volume_service(self, service) -> None:
        """Set VolumeService dependency."""
        self._volume_service = service

    def set_crossover_service(self, service) -> None:
        """Set CrossoverService dependency."""
        self._crossover_service = service

    def set_equalizer_client_proxy_service(self, service) -> None:
        """Set EqualizerClientProxyService dependency."""
        self._equalizer_client_proxy_service = service

    def set_equalizer_settings_sync_service(self, service) -> None:
        """Set EqualizerSettingsSyncService dependency."""
        self._equalizer_settings_sync_service = service

    def set_camilladsp_service(self, service) -> None:
        """Set CamillaDSPService dependency."""
        self._camilladsp_service = service

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
            asyncio.create_task(self._clear_init_flag_after_delay(2.0))

            # Signal that WebSocket is ready
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
            self.logger.warning("Cannot connect to Snapcast server - server may not be running")
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
            self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: Starting initialization")

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
                    mac_id = ClientRegistryService.compute_mac_id(hostname, ip, mac)
                    client_name = client.get("config", {}).get("name") or hostname or mac_id

                    # Check if client is already in registry
                    is_new_client = self.registry.get_client(mac_id) is None if self.registry else True

                    if is_new_client:
                        is_local = (ip == "127.0.0.1")
                        local_marker = " LOCAL CLIENT" if is_local else ""
                        self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: New client {client_id} (mac_id: {mac_id}){local_marker}")

                    # Register/update client in registry
                    if self.registry:
                        await self.registry.register_client(mac_id, client_name, ip, host=hostname)
                        await self.registry.set_client_online(mac_id, True)

                    if is_new_client:
                        self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: Registered {mac_id}")
                        # Sync volume from snapserver for new clients
                        snapcast_volume = client.get("config", {}).get("volume", {}).get("percent", 0)
                        self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: Syncing volume from snapserver: {snapcast_volume}%")
                        await self._sync_existing_client_volume(client_id, client)

            client_count = len(self.registry.get_all_clients()) if self.registry else 0
            has_local = any(c.is_local for c in self.registry.get_all_clients().values()) if self.registry else False
            local_status = "LOCAL FOUND" if has_local else "LOCAL NOT YET CONNECTED"
            self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: Complete. Registered: {client_count} clients. {local_status}")

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
            self.logger.info(f"SNAPCAST NOTIFICATION RECEIVED: {method}")

        non_volume_notifications = {
            "Client.OnConnect": lambda p: self._handle_client_connect(p),
            "Client.OnDisconnect": lambda p: self._handle_client_disconnect(p),
            "Client.OnNameChanged": lambda p: self._handle_client_name_changed(p),
            "Server.OnUpdate": lambda p: self._handle_server_update(p)
        }

        if method in non_volume_notifications:
            await non_volume_notifications[method](params)
        elif method in ["Client.OnVolumeChanged", "Client.OnMute"]:
            await self._delegate_volume_event_to_volume_service(method, params)
        else:
            self.logger.debug(f"Unhandled notification: {method}")

    async def _handle_server_update(self, params: Dict[str, Any]) -> None:
        """Handle Server.OnUpdate - detects new clients, disconnections, and online status changes."""
        try:
            if not self._snapcast_service:
                self.logger.warning("SnapcastService not available for online status detection")
                return

            all_clients = await self._snapcast_service.get_clients()
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
            snapcast_id = client["id"]

            if mac_id not in known_mac_ids:
                self.logger.info(f"NEW CLIENT detected: {mac_id} (snapcast_id: {snapcast_id})")

                if snapcast_id in self._processing_client_ids:
                    self.logger.debug(f"Skipping Server.OnUpdate init for {snapcast_id} - already being processed")
                    continue

                self._processing_client_ids.add(snapcast_id)
                try:
                    if self.registry:
                        await self.registry.register_client(mac_id, client["name"], client["ip"], host=client["host"])
                        await self.registry.set_client_online(mac_id, client["online"])

                    await self._broadcast_snapcast_event("client_connected", {
                        "client_id": snapcast_id, "client_name": client["name"],
                        "client_host": client["host"], "client_ip": client["ip"],
                        "mac_id": mac_id, "online": client["online"]
                    })
                    await self._notify_volume_service_client_connected(snapcast_id, {"id": snapcast_id})
                finally:
                    self._processing_client_ids.discard(snapcast_id)

    async def _process_disconnected_clients(self, current_mac_ids: set, known_mac_ids: set) -> None:
        """Mark registry clients as offline when they no longer appear in Snapcast."""
        for mac_id in known_mac_ids:
            if mac_id not in current_mac_ids:
                self.logger.info(f"CLIENT DISCONNECTED: {mac_id}")
                if self.registry:
                    await self.registry.set_client_online(mac_id, False)
                await self._broadcast_snapcast_event("client_disconnected", {
                    "client_id": mac_id, "mac_id": mac_id
                })

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
                if self.registry:
                    await self.registry.set_client_online(mac_id, online)

                await self._broadcast_snapcast_event("client_availability_changed", {
                    "client_id": client["id"], "mac_id": mac_id,
                    "online": online, "last_seen_age": client.get("last_seen_age", 0)
                })

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

        if client_id in self._processing_client_ids:
            self.logger.debug(f"Skipping Client.OnConnect for {client_id} - already being processed")
            return

        self._processing_client_ids.add(client_id)

        try:
            client_host = client.get("host", {}).get("name") or "Unknown"
            client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")
            client_mac = client.get("host", {}).get("mac", "")
            snapcast_volume = client.get("config", {}).get("volume", {}).get("percent", 100)

            # Compute mac_id using canonical method
            mac_id = ClientRegistryService.compute_mac_id(client_host, client_ip, client_mac)
            client_name = client.get("config", {}).get("name") or client_host or mac_id

            is_local = (client_ip == "127.0.0.1")
            local_marker = " LOCAL CLIENT" if is_local else ""
            self.logger.info(f"[{time.time():.3f}] CLIENT_CONNECT: New client {client_id} (mac_id: {mac_id}){local_marker}")
            self.logger.info(f"  - Name: {client_name}, Host: {client_host}, IP: {client_ip}")
            self.logger.info(f"  - Snapcast volume: {snapcast_volume}% (passthrough)")

            # Register client using new API (but don't set online yet - wait for volume sync)
            if self.registry:
                await self.registry.register_client(mac_id, client_name, client_ip, host=client_host)

            self.logger.info(f"[{time.time():.3f}] CLIENT_CONNECT: Calling volume sync for {client_id}")
            sync_status = await self._notify_volume_service_client_connected(client_id, client)

            # Set client online AFTER volume is synced to prevent UI flicker
            # (otherwise frontend briefly shows stale volume before sync completes)
            if self.registry:
                await self.registry.set_client_online(mac_id, True)

            # Crossover recalculation is handled by CrossoverService._handle_registry_event
            # via CLIENT_CONNECTED event emitted by set_client_online()

            # Push snapclient buffer config to remote clients (fire-and-forget)
            if not is_local:
                asyncio.create_task(self._push_snapclient_config(client_ip))

            await self._broadcast_snapcast_event("client_connected", {
                "client_id": client_id,
                "client_name": client_name,
                "client_host": client_host,
                "client_ip": client_ip,
                "mac_id": mac_id,
                "volume": snapcast_volume,
                "muted": client.get("config", {}).get("volume", {}).get("muted", False),
                "online": True,
                "sync_status": sync_status
            })
        finally:
            self._processing_client_ids.discard(client_id)

    async def _handle_client_disconnect(self, params: Dict[str, Any]) -> None:
        """Handle client disconnected event."""
        client = params.get("client", {})
        client_id = client.get("id")
        client_name = client.get("config", {}).get("name")

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

        await self._broadcast_snapcast_event("client_disconnected", {
            "client_id": client_id,
            "client_name": client_name,
            "mac_id": mac_id
        })

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

        await self._broadcast_snapcast_event("client_name_changed", {
            "client_id": client_id,
            "name": name,
            "mac_id": mac_id
        })

    async def _delegate_volume_event_to_volume_service(self, method: str, params: Dict[str, Any]) -> None:
        """Broadcast Snapcast volume/mute events."""
        try:
            client_id = params.get("id")
            if not client_id:
                return

            volume_data = params.get("volume", {})
            snapcast_volume = volume_data.get("percent", 100)
            muted = volume_data.get("muted", False)

            if method == "Client.OnVolumeChanged":
                await self._broadcast_snapcast_event("client_volume_changed", {
                    "client_id": client_id,
                    "volume": snapcast_volume,
                    "muted": muted
                })
            elif method == "Client.OnMute":
                await self._broadcast_snapcast_event("client_mute_changed", {
                    "client_id": client_id,
                    "volume": snapcast_volume,
                    "muted": muted
                })

            self.logger.debug(f"Broadcast {method} for client {client_id}")

        except Exception as e:
            self.logger.error(f"Error broadcasting Snapcast event: {e}")

    async def _notify_volume_service_client_connected(self, client_id: str, client: Dict[str, Any]) -> Dict[str, Any]:
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
            self.logger.info(f"[{time.time():.3f}] NOTIFY_VOLUME: Starting volume sync for {client_id}")

            sync_status = await self._sync_existing_client_volume(client_id, client)

            # Apply pending settings
            if self._crossover_service:
                client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "") if isinstance(client.get("host"), dict) else ""
                if client_ip:
                    has_pending = self._crossover_service.has_pending_settings(client_ip)
                    if has_pending:
                        self.logger.info(f"  - Applying pending settings for reconnected client {client_ip}")
                        pending_success = await self._crossover_service.apply_pending_settings(client_ip)
                        sync_status["pending_applied"] = pending_success

        except Exception as e:
            self.logger.error(f"Error initializing new client: {e}", exc_info=True)

        return sync_status

    async def _sync_existing_client_volume(self, client_id: str, client: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ensure existing client is in Multiroom group with correct volume.

        Sequence:
        1. Detect reconnection context (FR7-FR10)
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
            self.logger.info(f"[{time.time():.3f}] SYNC_VOLUME: Starting for {client_id}")

            if not self._snapcast_service:
                self.logger.warning("SnapcastService not available")
                return sync_status

            host = client.get("host", {})
            hostname = host.get("name", "")
            ip = host.get("ip", "").replace("::ffff:", "")
            mac = host.get("mac", "")
            mac_id = ClientRegistryService.compute_mac_id(hostname, ip, mac)

            # 1. Detect reconnection context (FR7-FR10)
            context = ReconnectionContext.STANDALONE_ALONE  # Default
            if self.registry:
                context = self.registry.get_reconnection_context(mac_id)
            sync_status["context"] = context.value
            self.logger.info(
                f"[{time.time():.3f}] SYNC_VOLUME: Detected reconnection context for {mac_id}: {context.value}"
            )

            # 2. Join to multiroom group
            await self._snapcast_service.set_client_group_to_multiroom(client_id)
            self.logger.info(f"[{time.time():.3f}] SYNC_VOLUME: Client {client_id} joined multiroom group")

            # 3. Set Snapcast volume to 100% passthrough
            await self._snapcast_service.set_volume(client_id, 100)
            self.logger.info(f"[{time.time():.3f}] SYNC_VOLUME: Snapcast volume set to 100% for {client_id}")

            # 4. Apply correct equalizer volume based on context (FR7-FR10)
            target_volume = self._resolve_target_volume(mac_id, context)
            self.logger.info(
                f"[{time.time():.3f}] SYNC_VOLUME: Applying target volume "
                f"{target_volume:.1f} dB for {mac_id} (context: {context.value})"
            )
            volume_synced = await self._apply_target_volume_to_client(mac_id, target_volume)
            sync_status["volume_synced"] = volume_synced

            # 5. Sync equalizer settings based on context
            equalizer_synced = True
            if self.registry:
                if context in (ReconnectionContext.IN_ZONE_OTHERS_ONLINE, ReconnectionContext.IN_ZONE_ALL_OFFLINE):
                    # IN_ZONE contexts (FR7, FR8) - sync zone equalizer settings
                    zone = self.registry.get_zone_for_client(mac_id)
                    if zone and zone.equalizer_settings:
                        self.logger.info(
                            f"[{time.time():.3f}] SYNC_EQ: Syncing zone equalizer for {mac_id} "
                            f"(zone: {zone.id}, context: {context.value})"
                        )
                        equalizer_synced = await self._sync_zone_equalizer_to_client(mac_id, zone)
                    else:
                        self.logger.warning(
                            f"[{time.time():.3f}] SYNC_EQ: Client {mac_id} in zone context but no zone found"
                        )
                else:
                    # STANDALONE contexts - sync standalone equalizer settings
                    self.logger.info(
                        f"[{time.time():.3f}] SYNC_EQ: Syncing standalone equalizer for {mac_id} "
                        f"(context: {context.value})"
                    )
                    equalizer_synced = await self._sync_standalone_equalizer_to_client(mac_id)
            sync_status["equalizer_synced"] = equalizer_synced

            # 6. Broadcast volume state to frontend (AC5)
            # This notifies UI about the reconnected client with its synced volume
            if volume_synced:
                if self._volume_service:
                    try:
                        await self._volume_service._broadcast_volume_state(show_bar=False)
                        self.logger.info(
                            f"[{time.time():.3f}] SYNC_BROADCAST: Volume state broadcast for {mac_id}"
                        )
                    except Exception as e:
                        self.logger.warning(f"Failed to broadcast volume state: {e}")

            # 7. Broadcast client_state_changed event (AC5)
            # Includes sync_context for frontend to know why settings were applied
            if self.registry:
                client = self.registry.get_client(mac_id)
                if client:
                    await self._broadcast_snapcast_event("client_state_changed", {
                        "mac_id": mac_id,
                        "client": client.to_dict(),
                        "sync_context": context.value,
                        "equalizer_ready": equalizer_synced
                    })

            self.logger.info(
                f"[{time.time():.3f}] SYNC_VOLUME: Client {client_id} fully initialized "
                f"(context: {context.value})"
            )

        except Exception as e:
            self.logger.error(f"Error syncing existing client {client_id}: {e}", exc_info=True)

        return sync_status

    def _resolve_target_volume(self, mac_id: str, context: ReconnectionContext) -> float:
        """
        Resolve target reconnection volume for any context (FR7-FR10).

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
                    self.logger.info(f"FR7 - Using zone average {avg:.1f} dB for {mac_id}")
                    return avg
            self.logger.warning(f"Zone average unavailable for {mac_id}, falling back to startup volume")

        elif context == ReconnectionContext.STANDALONE_OTHERS_ONLINE:
            if self.registry:
                avg = self.registry.get_global_average_volume(exclude_mac_id=mac_id)
                if avg is not None:
                    self.logger.info(f"FR9 - Using global average {avg:.1f} dB for {mac_id}")
                    return avg
            self.logger.warning(f"Global average unavailable for {mac_id}, falling back to startup volume")

        # Level 2: startup_volume_db from VolumeService configuration
        if self._volume_service:
            startup_volume = self._volume_service.config.config.startup_volume_db
            self.logger.info(f"FR8/FR10 - Using startup volume {startup_volume:.1f} dB for {mac_id}")
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

        Args:
            mac_id: Client identifier
            target_volume_db: Volume to set in dB

        Returns:
            True if volume applied successfully, False otherwise
        """
        try:
            if not self._volume_service:
                self.logger.warning(f"No volume_service available to apply volume for {mac_id}")
                return False

            # Update client volume in state and apply to equalizer
            await self._volume_service.update_client_volume_db(mac_id, target_volume_db, broadcast=False)

            # Unmute DSP (CamillaDSP starts muted with -m flag)
            # Use persisted mute state (defaults to False = unmuted)
            persisted_mute = self._volume_service._state_store.get_client_mute(mac_id)
            await self._volume_service._equalizer_controller.set_equalizer_mute(mac_id, persisted_mute)
            self.logger.info(f"[{time.time():.3f}] MUTE_APPLY: Set {mac_id} mute={persisted_mute}")

            # Update registry if available
            if self.registry:
                await self.registry.update_volume(mac_id, volume_db=target_volume_db)

            self.logger.info(
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
        self, hostname: str, mac_id: str, setting_type: str, data: Any, is_local: bool = False
    ) -> bool:
        """
        Apply a single DSP setting to a client. Routes to local CamillaDSP or remote proxy.

        Args:
            hostname: Client IP address
            mac_id: Client identifier for logging
            setting_type: "filter/<id>", "compressor", or "loudness"
            data: Setting payload dict
            is_local: Route to local CamillaDSP service instead of proxy

        Returns:
            True if applied successfully, False on failure
        """
        try:
            if is_local:
                if not self._camilladsp_service:
                    return False
                if setting_type.startswith("filter/"):
                    filter_id = setting_type.split("/", 1)[1]
                    await self._camilladsp_service.set_filter(filter_id, **data)
                elif setting_type == "compressor":
                    await self._camilladsp_service.set_compressor(**data)
                elif setting_type == "loudness":
                    await self._camilladsp_service.set_loudness(**data)
            else:
                if not self._equalizer_client_proxy_service:
                    return False
                await self._equalizer_client_proxy_service.request(hostname, "PUT", f"/equalizer/{setting_type}", data)
            return True
        except Exception as e:
            self.logger.warning(f"Failed to apply equalizer {setting_type} to {mac_id}: {e}")
            return False

    async def _sync_zone_equalizer_to_client(self, mac_id: str, zone) -> bool:
        """Apply zone equalizer settings (filters, compressor, loudness) to a reconnected client."""
        try:
            client = self.registry.get_client(mac_id) if self.registry else None
            if not client or not client.ip:
                self.logger.warning(f"Cannot sync to {mac_id}: no IP address")
                return False

            hostname = client.ip
            is_local = (client.ip == "127.0.0.1")

            # Guard: need the appropriate service for the routing path
            if is_local and not self._camilladsp_service:
                self.logger.warning(f"No camilladsp_service for local DSP sync to {mac_id}")
                return False
            if not is_local and not self._equalizer_client_proxy_service:
                self.logger.warning(f"No equalizer_client_proxy_service for equalizer sync to {mac_id}")
                return False

            eq = zone.equalizer_settings
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
                    if await self._apply_equalizer_setting(hostname, mac_id, f"filter/{flt.id}", filter_data, is_local):
                        synced.append(f"filter:{flt.id}")
                    else:
                        failed.append(f"filter:{flt.id}")
                        filters_failed.append(flt.to_dict())

            # Sync compressor
            if eq.compressor:
                data = eq.compressor.to_dict()
                if await self._apply_equalizer_setting(hostname, mac_id, "compressor", data, is_local):
                    synced.append("compressor")
                else:
                    failed.append("compressor")
                    if self._crossover_service:
                        await self._crossover_service.queue_pending_settings(hostname, "compressor", data)

            # Sync loudness
            if eq.loudness:
                data = eq.loudness.to_dict()
                if await self._apply_equalizer_setting(hostname, mac_id, "loudness", data, is_local):
                    synced.append("loudness")
                else:
                    failed.append("loudness")
                    if self._crossover_service:
                        await self._crossover_service.queue_pending_settings(hostname, "loudness", data)

            # Queue failed filters for retry
            if filters_failed and self._crossover_service:
                await self._crossover_service.queue_pending_settings(hostname, "filters", filters_failed)

            if synced:
                self.logger.info(f"SYNC_EQ: Synced {synced} to {mac_id} from zone {zone.id}")
            if failed:
                self.logger.warning(f"SYNC_EQ: Queued failed {failed} for retry to {mac_id}")

            return len(failed) == 0

        except Exception as e:
            self.logger.error(f"Error syncing zone equalizer to {mac_id}: {e}", exc_info=True)
            return False

    async def _sync_standalone_equalizer_to_client(self, mac_id: str) -> bool:
        """Apply saved standalone equalizer settings to a reconnected client."""
        try:
            if not self._equalizer_settings_sync_service:
                self.logger.warning(f"No equalizer_settings_sync_service for standalone equalizer sync to {mac_id}")
                return True  # Not a failure, just no sync service

            client = self.registry.get_client(mac_id) if self.registry else None
            if not client or not client.ip:
                self.logger.warning(f"Cannot sync standalone equalizer to {mac_id}: no IP address")
                return False

            hostname = client.ip
            is_local = (client.ip == "127.0.0.1")
            saved = await self._equalizer_settings_sync_service.get_client_settings(mac_id)

            if not saved:
                self.logger.info(f"SYNC_STANDALONE: No saved settings for {mac_id}, defaults apply")
                return True

            self.logger.info(f"SYNC_STANDALONE: Applying saved settings for {mac_id}")
            synced = []
            failed = []
            filters_failed = []

            # Sync filters (stored as dict: {filter_id: {freq, gain, q, ...}})
            for filter_id, flt in saved.get('filters', {}).items():
                if not filter_id or not isinstance(flt, dict):
                    continue
                filter_data = {
                    'freq': flt.get('freq'), 'gain': flt.get('gain'), 'q': flt.get('q'),
                    'filter_type': flt.get('filter_type') or flt.get('type')
                }
                if await self._apply_equalizer_setting(hostname, mac_id, f"filter/{filter_id}", filter_data, is_local):
                    synced.append(f"filter:{filter_id}")
                else:
                    failed.append(f"filter:{filter_id}")
                    filters_failed.append({filter_id: filter_data})

            # Queue failed filters for retry
            if filters_failed and self._crossover_service:
                await self._crossover_service.queue_pending_settings(hostname, "filters", filters_failed)

            # Sync compressor
            if compressor := saved.get('compressor'):
                if await self._apply_equalizer_setting(hostname, mac_id, "compressor", compressor, is_local):
                    synced.append("compressor")
                else:
                    failed.append("compressor")
                    if self._crossover_service:
                        await self._crossover_service.queue_pending_settings(hostname, "compressor", compressor)

            # Sync loudness
            if loudness := saved.get('loudness'):
                if await self._apply_equalizer_setting(hostname, mac_id, "loudness", loudness, is_local):
                    synced.append("loudness")
                else:
                    failed.append("loudness")
                    if self._crossover_service:
                        await self._crossover_service.queue_pending_settings(hostname, "loudness", loudness)

            if synced:
                self.logger.info(f"SYNC_STANDALONE: Synced {synced} to {mac_id}")
            if failed:
                self.logger.warning(f"SYNC_STANDALONE: Failed to sync {failed} to {mac_id}")

            return len(failed) == 0

        except Exception as e:
            self.logger.error(f"Error syncing standalone equalizer to {mac_id}: {e}", exc_info=True)
            return False

    async def _broadcast_snapcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcast a Snapcast event via WebSocket and EventBus."""
        if self.state_machine:
            await self.state_machine.broadcast_event("snapcast", event_type, {
                **data,
                "source": "snapcast_websocket"
            })

        # Also emit via EventBus
        if self.event_bus:
            await self.event_bus.emit(f"multiroom.{event_type}", data)

        self.logger.debug(f"Broadcasted Snapcast event: {event_type}")
