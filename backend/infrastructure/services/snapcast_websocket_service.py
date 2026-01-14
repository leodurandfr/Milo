# backend/infrastructure/services/snapcast_websocket_service.py
"""
Streamlined Snapcast WebSocket Service - WITHOUT volume management (delegated to VolumeService)

Uses ClientRegistryService as the single source of truth for client/availability tracking.
"""
import asyncio
import json
import logging
import time
import aiohttp
from typing import Dict, Any, Optional

from backend.infrastructure.services.client_registry_service import ClientRegistryService


class SnapcastWebSocketService:
    """WebSocket service for Snapcast NON-VOLUME notifications - VolumeService handles all volume"""

    def __init__(self, state_machine, routing_service, settings_service=None, host: str = "localhost", port: int = 1780):
        self.state_machine = state_machine
        self.routing_service = routing_service
        self.settings_service = settings_service
        self.host = host
        self.port = port
        self.ws_url = f"ws://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)

        # Client registry - set after construction via state_machine.client_registry
        self._registry: Optional[ClientRegistryService] = None

        # Connection state
        self.session: Optional[aiohttp.ClientSession] = None
        self.websocket: Optional[aiohttp.ClientWebSocketResponse] = None
        self.running = False
        self.should_connect = False
        self.reconnect_task = None

        # Deduplication: track client IDs currently being processed
        # Prevents race conditions when Client.OnConnect and Server.OnUpdate fire simultaneously
        self._processing_client_ids: set = set()

        # Initialization state - suppress verbose logs during startup
        self._is_initializing = False

        # ID for JSON-RPC requests
        self.request_id = 0

        # Ready event - signaled when WebSocket is connected and initialized
        self._ready_event = asyncio.Event()

    @property
    def registry(self) -> Optional[ClientRegistryService]:
        """Get the client registry from state_machine if not set directly."""
        if self._registry:
            return self._registry
        return getattr(self.state_machine, 'client_registry', None)
    
    async def initialize(self) -> bool:
        """Initializes the WebSocket service"""
        try:
            self.logger.info(f"Initializing Snapcast WebSocket service: {self.ws_url}")
            self.session = aiohttp.ClientSession()
            self.running = True

            # Check initial multiroom state from SETTINGS (most reliable at boot time)
            # routing_service.get_state() may not have loaded persisted state yet
            multiroom_state = False

            if self.settings_service:
                # Read directly from settings - this is the authoritative source
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
        """Starts WebSocket connection when multiroom is enabled"""
        if self.should_connect:
            return  # Already in progress
            
        self.logger.info("Starting Snapcast WebSocket connection (multiroom enabled)")
        self.should_connect = True
        
        if not self.reconnect_task and self.running:
            self.reconnect_task = asyncio.create_task(self._connection_loop())
    
    async def stop_connection(self) -> None:
        """Stops WebSocket connection when multiroom is disabled"""
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

        # NO LONGER clear caches - keep memory of existing clients
        # to avoid resetting their volumes when multiroom is re-enabled
        # (snapserver already persists volumes correctly in server.json)

    async def wait_for_ready(self, timeout: float = 10.0) -> bool:
        """Wait for WebSocket to be connected and initialized"""
        try:
            await asyncio.wait_for(self._ready_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            self.logger.warning(f"Timeout waiting for Snapcast WebSocket ready after {timeout}s")
            return False

    async def cleanup(self) -> None:
        """Cleans up resources"""
        self.logger.info("Cleaning up Snapcast WebSocket service")
        self.running = False
        self.should_connect = False

        # Cancel reconnection task
        if self.reconnect_task:
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass
        
        # Close WebSocket connection
        if self.websocket:
            await self.websocket.close()

        # Close session
        if self.session:
            await self.session.close()
    
    async def _connection_loop(self) -> None:
        """Connection loop with intelligent reconnection"""
        reconnect_delay = 5  # Initial delay
        max_delay = 30       # Maximum delay
        
        while self.running and self.should_connect:
            try:
                await self._connect_and_listen()
                # Reset delay if connection successful
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
        """Connects and listens for WebSocket messages"""
        try:
            self.logger.info(f"Connecting to Snapcast WebSocket: {self.ws_url}")

            timeout = aiohttp.ClientTimeout(total=5)
            self.websocket = await self.session.ws_connect(self.ws_url, timeout=timeout)
            self.logger.info("Connected to Snapcast WebSocket")

            # Send initial ping to verify connection
            await self._send_request("Server.GetRPCVersion")

            # Initialize already connected clients (suppress notification logs during this phase)
            # Keep _is_initializing True for 2 seconds after init to catch async notifications
            self._is_initializing = True
            await self._initialize_existing_clients()
            asyncio.create_task(self._clear_init_flag_after_delay(2.0))

            # Signal that WebSocket is ready
            self._ready_event.set()
            self.logger.info("✅ Snapcast WebSocket ready and initialized")

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
        """Clears the initialization flag after a delay to suppress async notifications"""
        await asyncio.sleep(delay)
        self._is_initializing = False
        self.logger.debug("Snapcast WebSocket initialization phase complete, notifications now logged")

    async def _initialize_existing_clients(self) -> None:
        """Initializes clients already connected at the time of WebSocket connection"""
        try:
            self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: Starting initialization of existing clients")

            # Retrieve server status
            snapcast_service = getattr(self.state_machine, 'snapcast_service', None)
            if not snapcast_service:
                self.logger.warning("SnapcastService not available")
                return

            status = await snapcast_service.get_server_status()
            if not status:
                self.logger.warning("Could not get Snapcast status")
                return

            groups = status.get('server', {}).get('groups', [])

            for group in groups:
                for client in group.get('clients', []):
                    if not client.get('connected'):
                        continue

                    client_id = client.get('id')

                    # Get dsp_id for this client
                    host = client.get("host", {})
                    hostname = host.get("name", "")
                    ip = host.get("ip", "").replace("::ffff:", "")
                    dsp_id = snapcast_service._get_stable_dsp_id(hostname, ip)
                    client_name = client.get("config", {}).get("name", hostname or dsp_id)

                    # Check if client is already in registry
                    existing_client = self.registry.get_client(dsp_id) if self.registry else None

                    if not existing_client:
                        is_local = (dsp_id == "local")
                        local_marker = " ⭐ LOCAL CLIENT" if is_local else ""
                        self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: New client {client_id} (dsp_id: {dsp_id}){local_marker}")

                        # Register client in registry
                        if self.registry:
                            await self.registry.register_client({
                                "dsp_id": dsp_id,
                                "snapcast_id": client_id,
                                "name": client_name,
                                "host": hostname,
                                "ip": ip,
                                "available": True
                            })
                        self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: Registered {dsp_id}, calling volume sync")

                        # ALWAYS sync from snapserver (no heuristics)
                        # Snapserver is the source of truth for client volumes
                        # NEVER overwrite persisted volumes in server.json
                        snapcast_volume = client.get("config", {}).get("volume", {}).get("percent", 0)
                        self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: Syncing volume from snapserver: {snapcast_volume}%")
                        await self._sync_existing_client_volume(client_id, client)
                        self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: Volume sync complete for {dsp_id}")
                    else:
                        # Client already known - just update availability (no volume sync)
                        # This handles the case where multiroom was disabled then re-enabled
                        self.logger.debug(f"Client {dsp_id} already known, updating availability")
                        if self.registry:
                            await self.registry.update_availability(dsp_id, True)

            client_count = len(self.registry.get_all_clients()) if self.registry else 0
            local_found = self.registry.get_client("local") is not None if self.registry else False
            local_status = "✅ LOCAL FOUND" if local_found else "⚠️ LOCAL NOT YET CONNECTED"
            self.logger.info(f"[{time.time():.3f}] INIT_CLIENTS: Complete. Registered: {client_count} clients. {local_status}")

        except Exception as e:
            self.logger.error(f"Error initializing existing clients: {e}", exc_info=True)

    async def _send_request(self, method: str, params: Optional[Dict] = None) -> None:
        """Sends a JSON-RPC request"""
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
        """Processes a received JSON-RPC message"""
        try:
            # Snapcast notification (no "id" in notifications)
            if "method" in data and "id" not in data:
                await self._handle_notification(data)
            # Response to a request (with "id")
            elif "result" in data or "error" in data:
                await self._handle_response(data)
                
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
    
    async def _handle_notification(self, notification: Dict[str, Any]) -> None:
        """Processes a Snapcast notification"""
        method = notification.get("method")
        params = notification.get("params", {})

        # Suppress verbose logs during initialization (notifications from initial sync)
        if self._is_initializing:
            self.logger.debug(f"📨 SNAPCAST NOTIFICATION (init phase): {method}")
        else:
            self.logger.info(f"📨 SNAPCAST NOTIFICATION RECEIVED: {method}")
        
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
        """Handles Server.OnUpdate - detects new clients, disconnections, and availability changes"""
        try:
            # Get clients with availability from snapcast_service
            snapcast_service = getattr(self.state_machine, 'snapcast_service', None)
            if not snapcast_service:
                self.logger.warning("SnapcastService not available for availability detection")
                return

            all_clients = await snapcast_service.get_clients()

            # Build set of current snapcast client IDs and dsp_ids
            current_snapcast_ids = {c["id"] for c in all_clients}
            current_dsp_ids = {c["dsp_id"] for c in all_clients}

            # Get known clients from registry
            known_dsp_ids = set(self.registry.get_client_ids()) if self.registry else set()

            # Detect new clients (by dsp_id since that's what registry uses)
            for client in all_clients:
                dsp_id = client["dsp_id"]
                snapcast_id = client["id"]

                if dsp_id not in known_dsp_ids:
                    self.logger.info(f"🟢 NEW CLIENT detected: {dsp_id} (snapcast_id: {snapcast_id})")

                    # Deduplication: skip if already being processed by Client.OnConnect
                    if snapcast_id in self._processing_client_ids:
                        self.logger.debug(f"Skipping Server.OnUpdate init for {snapcast_id} - already being processed")
                        continue

                    # Mark as processing
                    self._processing_client_ids.add(snapcast_id)

                    try:
                        # Register client in registry
                        if self.registry:
                            await self.registry.register_client({
                                "dsp_id": dsp_id,
                                "snapcast_id": snapcast_id,
                                "name": client["name"],
                                "host": client["host"],
                                "ip": client["ip"],
                                "available": client["available"]
                            })

                        # Broadcast client connected with availability (for legacy frontend handlers)
                        await self._broadcast_snapcast_event("client_connected", {
                            "client_id": snapcast_id,
                            "client_name": client["name"],
                            "client_host": client["host"],
                            "client_ip": client["ip"],
                            "dsp_id": dsp_id,
                            "available": client["available"]
                        })

                        # Initialize client (set group, volume)
                        await self._notify_volume_service_client_connected(snapcast_id, {"id": snapcast_id})
                    finally:
                        # Remove from processing set
                        self._processing_client_ids.discard(snapcast_id)

            # Detect disconnected clients (clients in registry but not in snapcast)
            for dsp_id in known_dsp_ids:
                if dsp_id not in current_dsp_ids:
                    self.logger.info(f"🔴 CLIENT DISCONNECTED: {dsp_id}")
                    # Mark as unavailable in registry (don't unregister - keeps settings)
                    if self.registry:
                        await self.registry.update_availability(dsp_id, False)
                    # Broadcast for legacy frontend handlers
                    await self._broadcast_snapcast_event("client_disconnected", {
                        "client_id": dsp_id,
                        "dsp_id": dsp_id
                    })

            # Detect availability changes for existing clients
            for client in all_clients:
                dsp_id = client["dsp_id"]
                available = client["available"]

                # Get previous availability from registry
                registry_client = self.registry.get_client(dsp_id) if self.registry else None
                previous_available = registry_client.available if registry_client else True

                if available != previous_available:
                    self.logger.info(f"🔄 Client {dsp_id} availability: {previous_available} → {available} (lastSeen: {client.get('last_seen_age', 0)}s ago)")

                    # Update registry
                    if self.registry:
                        await self.registry.update_availability(dsp_id, available)

                    # Broadcast for legacy frontend handlers
                    await self._broadcast_snapcast_event("client_availability_changed", {
                        "client_id": client["id"],
                        "dsp_id": dsp_id,
                        "available": available,
                        "last_seen_age": client.get("last_seen_age", 0)
                    })

                    # Recalculate crossover for zones containing this client
                    # (e.g., if subwoofer disconnects, remove highpass from speakers)
                    if self.registry and hasattr(self.state_machine, 'crossover_service'):
                        crossover_service = self.state_machine.crossover_service
                        zone = self.registry.get_zone_for_client(dsp_id)
                        if zone:
                            self.logger.info(f"🔄 Recalculating crossover for zone {zone.id} (client {dsp_id} availability changed)")
                            await crossover_service.apply_zone_crossover(zone.id)

        except Exception as e:
            self.logger.error(f"Error handling Server.OnUpdate: {e}", exc_info=True)
        
    
    async def _handle_response(self, response: Dict[str, Any]) -> None:
        """Processes a response to a request"""
        if "error" in response:
            self.logger.error(f"Snapcast RPC error: {response['error']}")
    
    # === STREAMLINED HANDLERS - WITHOUT VOLUME MANAGEMENT ===

    async def _handle_client_connect(self, params: Dict[str, Any]) -> None:
        """Client connected - Snapcast volume is always 100% passthrough, real volume via CamillaDSP"""
        client = params.get("client", {})
        client_id = client.get("id")

        # Deduplication: skip if already being processed by Server.OnUpdate
        if client_id in self._processing_client_ids:
            self.logger.debug(f"Skipping Client.OnConnect for {client_id} - already being processed")
            return

        # Mark as processing
        self._processing_client_ids.add(client_id)

        try:
            client_name = client.get("config", {}).get("name", "Unknown")
            client_host = client.get("host", {}).get("name", "Unknown")
            client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")
            snapcast_volume = client.get("config", {}).get("volume", {}).get("percent", 100)

            # Calculate dsp_id using same logic as _extract_clients
            snapcast_service = getattr(self.state_machine, 'snapcast_service', None)
            if snapcast_service:
                dsp_id = snapcast_service._get_stable_dsp_id(client_host, client_ip)
            else:
                # Fallback if service not available
                dsp_id = "local" if client_host == "milo" else (client_host if client_host.startswith("milo-client") else client_ip)

            is_local = (dsp_id == "local")
            local_marker = " ⭐ LOCAL CLIENT" if is_local else ""
            self.logger.info(f"[{time.time():.3f}] CLIENT_CONNECT: New client {client_id} (dsp_id: {dsp_id}){local_marker}")
            self.logger.info(f"  - Name: {client_name}, Host: {client_host}, IP: {client_ip}")
            self.logger.info(f"  - Snapcast volume: {snapcast_volume}% (passthrough)")

            # Register client in registry
            if self.registry:
                await self.registry.register_client({
                    "dsp_id": dsp_id,
                    "snapcast_id": client_id,
                    "name": client_name,
                    "host": client_host,
                    "ip": client_ip,
                    "available": True
                })

            self.logger.info(f"[{time.time():.3f}] CLIENT_CONNECT: Calling _notify_volume_service_client_connected for {client_id}")
            await self._notify_volume_service_client_connected(client_id, client)

            # Broadcast for legacy frontend handlers
            await self._broadcast_snapcast_event("client_connected", {
                "client_id": client_id,
                "client_name": client_name,
                "client_host": client_host,
                "client_ip": client_ip,
                "dsp_id": dsp_id,
                "volume": snapcast_volume,
                "muted": client.get("config", {}).get("volume", {}).get("muted", False),
                "available": True
            })
        finally:
            # Remove from processing set
            self._processing_client_ids.discard(client_id)
    
    async def _handle_client_disconnect(self, params: Dict[str, Any]) -> None:
        """Client disconnected - Streamlined version"""
        client = params.get("client", {})
        client_id = client.get("id")
        client_name = client.get("config", {}).get("name")

        # Calculate dsp_id for volume service update
        client_host = client.get("host", {}).get("name", "Unknown")
        client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")

        snapcast_service = getattr(self.state_machine, 'snapcast_service', None)
        if snapcast_service:
            dsp_id = snapcast_service._get_stable_dsp_id(client_host, client_ip)
        else:
            dsp_id = "local" if client_host == "milo" else (client_host if client_host.startswith("milo-client") else client_ip)

        self.logger.info(f"🔴 CLIENT DISCONNECTED: {client_host} (dsp_id: {dsp_id})")

        # Update registry availability (don't unregister - keeps settings)
        if self.registry:
            await self.registry.update_availability(dsp_id, False)

        # Recalculate crossover for zones containing this client
        # (e.g., if subwoofer disconnects, remove highpass from speakers)
        if self.registry and hasattr(self.state_machine, 'crossover_service'):
            crossover_service = self.state_machine.crossover_service
            zone = self.registry.get_zone_for_client(dsp_id)
            if zone:
                self.logger.info(f"🔄 Recalculating crossover for zone {zone.id} (client {dsp_id} disconnected)")
                await crossover_service.apply_zone_crossover(zone.id)

        # Broadcast for legacy frontend handlers
        await self._broadcast_snapcast_event("client_disconnected", {
            "client_id": client_id,
            "client_name": client_name,
            "dsp_id": dsp_id
        })
    
    async def _handle_client_name_changed(self, params: Dict[str, Any]) -> None:
        """Client name changed - Include dsp_id for frontend sync"""
        client_id = params.get("id")
        name = params.get("name")

        # Get client host info to compute dsp_id
        # dsp_id = 'local' for main Milo, hostname or IP for remote clients
        dsp_id = client_id  # fallback

        try:
            status = await self._request("Server.GetStatus")
            for group in status.get("server", {}).get("groups", []):
                for client in group.get("clients", []):
                    if client.get("id") == client_id:
                        host = client.get("host", {}).get("name", "")
                        ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")
                        if host == "milo":
                            dsp_id = "local"
                        elif host.startswith("milo-client"):
                            dsp_id = host
                        else:
                            dsp_id = ip or host
                        break
        except Exception as e:
            self.logger.warning(f"Could not get client info for dsp_id: {e}")

        await self._broadcast_snapcast_event("client_name_changed", {
            "client_id": client_id,
            "name": name,
            "dsp_id": dsp_id
        })
    
    # === NEW: DELEGATION TO VOLUME SERVICE + BROADCAST MUTE ===

    async def _delegate_volume_event_to_volume_service(self, method: str, params: Dict[str, Any]) -> None:
        """Broadcasts Snapcast volume/mute events (volume always 100% passthrough, real volume via CamillaDSP)"""
        try:
            client_id = params.get("id")
            if not client_id:
                return

            volume_data = params.get("volume", {})
            snapcast_volume = volume_data.get("percent", 100)
            muted = volume_data.get("muted", False)

            # Broadcast event for UI (Snapcast volume is informational only)
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

            self.logger.debug(f"Broadcast {method} for client {client_id} (snapcast_volume={snapcast_volume}, muted={muted})")

        except Exception as e:
            self.logger.error(f"Error broadcasting Snapcast event: {e}")
    
    async def _notify_volume_service_client_connected(self, client_id: str, client: Dict[str, Any]) -> None:
        """Initializes new client: sets Multiroom group, syncs volume, applies pending settings."""
        try:
            self.logger.info(f"[{time.time():.3f}] NOTIFY_VOLUME: Starting volume sync for {client_id}")

            # Common initialization (Multiroom group + volume sync)
            await self._sync_existing_client_volume(client_id, client)

            # Apply any pending settings for this client (queued while offline)
            crossover_service = getattr(self.state_machine, 'crossover_service', None)
            if crossover_service:
                client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "") if isinstance(client.get("host"), dict) else ""
                if client_ip:
                    has_pending = crossover_service.has_pending_settings(client_ip)
                    if has_pending:
                        self.logger.info(f"  - Applying pending settings for reconnected client {client_ip}")
                        await crossover_service.apply_pending_settings(client_ip)

        except Exception as e:
            self.logger.error(f"❌ Error initializing new client: {e}", exc_info=True)

    async def _sync_existing_client_volume(self, client_id: str, client: Dict[str, Any]) -> None:
        """
        Ensures existing client is in Multiroom group with correct volume.

        Sequence:
        1. Join client to multiroom group
        2. Set Snapcast volume to 100% passthrough
        3. Apply correct DSP volume (handles mute/unmute internally via volume_service)

        Note: DSP mute/unmute is handled in volume_service.sync_existing_client_from_snapcast()
        to prevent volume blast on reconnect. Snapcast mute is informational only.
        """
        try:
            self.logger.info(f"[{time.time():.3f}] SYNC_VOLUME: Starting for {client_id}")

            snapcast_service = getattr(self.state_machine, 'snapcast_service', None)
            if not snapcast_service:
                self.logger.warning("SnapcastService not available")
                return

            # Get DSP ID early (needed for volume sync)
            host = client.get("host", {})
            hostname = host.get("name", "")
            ip = host.get("ip", "").replace("::ffff:", "")
            dsp_id = snapcast_service._get_stable_dsp_id(hostname, ip)

            # 1. Join to multiroom group
            await snapcast_service.set_client_group_to_multiroom(client_id)
            self.logger.info(f"[{time.time():.3f}] SYNC_VOLUME: Client {client_id} joined multiroom group")

            # 2. Set Snapcast volume to 100% passthrough
            await snapcast_service.set_volume(client_id, 100)
            self.logger.info(f"[{time.time():.3f}] SYNC_VOLUME: Snapcast volume set to 100% for {client_id}")

            # 3. Apply correct DSP volume (mute/unmute handled internally)
            self.logger.info(f"[{time.time():.3f}] SYNC_VOLUME: Calling DSP volume sync for {dsp_id}")
            await self._sync_client_volume_and_broadcast(dsp_id)

            self.logger.info(f"[{time.time():.3f}] SYNC_VOLUME: Client {client_id} fully initialized")

        except Exception as e:
            self.logger.error(f"Error syncing existing client {client_id}: {e}", exc_info=True)

    async def _sync_client_volume_and_broadcast(self, dsp_id: str) -> None:
        """Apply correct volume to client DSP and broadcast state to frontend."""
        try:
            volume_service = getattr(self.state_machine, 'volume_service', None)
            if not volume_service:
                self.logger.warning(f"No volume_service available to sync volume for {dsp_id}")
                return

            # Apply correct volume to client DSP and broadcast state
            self.logger.info(f"[{time.time():.3f}] DSP_BROADCAST: Calling sync_existing_client_from_snapcast for {dsp_id}")
            await volume_service.sync_existing_client_from_snapcast(dsp_id)
            self.logger.info(f"[{time.time():.3f}] DSP_BROADCAST: sync complete for {dsp_id}")

        except Exception as e:
            self.logger.error(f"Error syncing client volume for {dsp_id}: {e}", exc_info=True)

    async def _broadcast_snapcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcasts a Snapcast event via the Milo WebSocket system"""
        if self.state_machine:
            await self.state_machine.broadcast_event("snapcast", event_type, {
                **data,
                "source": "snapcast_websocket"
            })
            
            self.logger.debug(f"Broadcasted Snapcast event: {event_type}")