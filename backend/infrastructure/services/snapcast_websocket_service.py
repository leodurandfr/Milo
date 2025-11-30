# backend/infrastructure/services/snapcast_websocket_service.py
"""
Streamlined Snapcast WebSocket Service - WITHOUT volume management (delegated to VolumeService)
"""
import asyncio
import json
import logging
import aiohttp
from typing import Dict, Any, Optional

class SnapcastWebSocketService:
    """WebSocket service for Snapcast NON-VOLUME notifications - VolumeService handles all volume"""
    
    def __init__(self, state_machine, routing_service, host: str = "localhost", port: int = 1780):
        self.state_machine = state_machine
        self.routing_service = routing_service
        self.host = host
        self.port = port
        self.ws_url = f"ws://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)
        
        # Connection state
        self.session: Optional[aiohttp.ClientSession] = None
        self.websocket: Optional[aiohttp.ClientWebSocketResponse] = None
        self.running = False
        self.should_connect = False
        self.reconnect_task = None
        self._known_client_ids = set()

        # Initialization state - suppress verbose logs during startup
        self._is_initializing = False

        # ID for JSON-RPC requests
        self.request_id = 0
    
    async def initialize(self) -> bool:
        """Initializes the WebSocket service"""
        try:
            self.logger.info(f"Initializing Snapcast WebSocket service: {self.ws_url}")
            self.session = aiohttp.ClientSession()
            self.running = True

            # Check initial multiroom state
            if self.routing_service:
                routing_state = self.routing_service.get_state()
                multiroom_state = routing_state.get('multiroom_enabled', False)

                # Fallback: routing_service may not be initialized, check systemd
                if not multiroom_state:
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

        # NO LONGER clear caches - keep memory of existing clients
        # to avoid resetting their volumes when multiroom is re-enabled
        # (snapserver already persists volumes correctly in server.json)
    
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
            self.logger.info("Initializing existing Snapcast clients...")

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

                    # Check if it's a new client
                    if client_id not in self._known_client_ids:
                        self.logger.info(f"🟢 CLIENT at startup: {client_id}")
                        self._known_client_ids.add(client_id)

                        # ALWAYS sync from snapserver (no heuristics)
                        # Snapserver is the source of truth for client volumes
                        # NEVER overwrite persisted volumes in server.json
                        snapcast_volume = client.get("config", {}).get("volume", {}).get("percent", 0)
                        self.logger.info(f"  Syncing client volume from snapserver: {snapcast_volume}%")
                        await self._sync_existing_client_volume(client_id, client)
                    else:
                        self.logger.debug(f"Client {client_id} already known")

            self.logger.info(f"Initialization complete. Known clients: {len(self._known_client_ids)}")

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
        """Handles Server.OnUpdate and detects new clients AND disconnections"""
        try:
            server = params.get("server", {})
            groups = server.get("groups", [])

            # Extract all connected clients
            current_client_ids = set()
            new_clients = []
            
            for group in groups:
                for client in group.get("clients", []):
                    if not client.get("connected"):
                        continue
                    
                    client_id = client.get("id")
                    current_client_ids.add(client_id)

                    # New client detected?
                    if client_id not in self._known_client_ids:
                        self.logger.info(f"🟢 NEW CLIENT DETECTED in Server.OnUpdate: {client_id}")
                        new_clients.append(client)

            # NEW: Detect disappeared clients (disconnected)
            disconnected_client_ids = self._known_client_ids - current_client_ids

            for disconnected_id in disconnected_client_ids:
                self.logger.info(f"🔴 CLIENT DISCONNECTED detected in Server.OnUpdate: {disconnected_id}")
                await self._broadcast_snapcast_event("client_disconnected", {
                    "client_id": disconnected_id,
                    "client_name": "Unknown"  # No longer have access to the name
                })

            # Update cache
            self._known_client_ids = current_client_ids

            # Initialize new clients
            for client in new_clients:
                client_id = client.get("id")
                client_volume = client.get("config", {}).get("volume", {}).get("percent", 0)
                
                self.logger.info(f"  - Initializing new client {client_id} (current ALSA: {client_volume}%)")
                await self._notify_volume_service_client_connected(client_id, client)
            
        except Exception as e:
            self.logger.error(f"Error handling Server.OnUpdate: {e}", exc_info=True)
        
    
    async def _handle_response(self, response: Dict[str, Any]) -> None:
        """Processes a response to a request"""
        if "error" in response:
            self.logger.error(f"Snapcast RPC error: {response['error']}")
    
    # === STREAMLINED HANDLERS - WITHOUT VOLUME MANAGEMENT ===

    async def _handle_client_connect(self, params: Dict[str, Any]) -> None:
        """Client connected - Version with converted volume (no fallback)"""
        client = params.get("client", {})
        client_id = client.get("id")
        client_name = client.get("config", {}).get("name", "Unknown")
        client_host = client.get("host", {}).get("name", "Unknown")
        client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")
        alsa_volume = client.get("config", {}).get("volume", {}).get("percent", 0)

        # ALSA → Display conversion (REQUIRED)
        volume_service = getattr(self.state_machine, 'volume_service', None)
        if not volume_service:
            self.logger.error("❌ VolumeService not available - cannot convert volume")
            return  # Don't send event with incorrect volume
        
        display_volume = volume_service.convert_alsa_to_display(alsa_volume)
        
        self.logger.info(f"🔵 NEW CLIENT CONNECTED:")
        self.logger.info(f"  - ID: {client_id}")
        self.logger.info(f"  - Name: {client_name}")
        self.logger.info(f"  - Host: {client_host}")
        self.logger.info(f"  - IP: {client_ip}")
        self.logger.info(f"  - ALSA volume: {alsa_volume}% → Display: {display_volume}%")
        
        await self._notify_volume_service_client_connected(client_id, client)
        
        await self._broadcast_snapcast_event("client_connected", {
            "client_id": client_id,
            "client_name": client_name,
            "client_host": client_host,
            "client_ip": client_ip,
            "volume": display_volume,
            "muted": client.get("config", {}).get("volume", {}).get("muted", False)
        })
    
    async def _handle_client_disconnect(self, params: Dict[str, Any]) -> None:
        """Client disconnected - Streamlined version"""
        client = params.get("client", {})
        
        await self._broadcast_snapcast_event("client_disconnected", {
            "client_id": client.get("id"),
            "client_name": client.get("config", {}).get("name")
        })
    
    async def _handle_client_name_changed(self, params: Dict[str, Any]) -> None:
        """Client name changed - Streamlined version"""
        await self._broadcast_snapcast_event("client_name_changed", {
            "client_id": params.get("id"),
            "name": params.get("name")
        })
    
    # === NEW: DELEGATION TO VOLUME SERVICE + BROADCAST MUTE ===

    async def _delegate_volume_event_to_volume_service(self, method: str, params: Dict[str, Any]) -> None:
        """Delegates volume events to VolumeService AND broadcasts for UI"""
        try:
            volume_service = getattr(self.state_machine, 'volume_service', None)
            if not volume_service:
                self.logger.warning("VolumeService not available for delegation")
                return

            client_id = params.get("id")
            if not client_id:
                return

            volume_data = params.get("volume", {})
            alsa_volume = volume_data.get("percent", 0)
            muted = volume_data.get("muted", False)

            # Delegate synchronization to VolumeService
            await volume_service.sync_client_volume_from_external(client_id, alsa_volume)

            # Broadcast correct event type based on notification
            display_volume = volume_service.convert_alsa_to_display(alsa_volume)
            if method == "Client.OnVolumeChanged":
                await self._broadcast_snapcast_event("client_volume_changed", {
                    "client_id": client_id,
                    "volume": display_volume,
                    "muted": muted
                })
            elif method == "Client.OnMute":
                await self._broadcast_snapcast_event("client_mute_changed", {
                    "client_id": client_id,
                    "volume": display_volume,
                    "muted": muted
                })

            self.logger.debug(f"Delegated {method} to VolumeService for client {client_id} (volume={display_volume}, muted={muted})")

        except Exception as e:
            self.logger.error(f"Error delegating to VolumeService: {e}")
    
    async def _notify_volume_service_client_connected(self, client_id: str, client: Dict[str, Any]) -> None:
        """Notifies VolumeService of a new client + switches to Multiroom"""
        try:
            self.logger.info(f"🔵 _notify_volume_service_client_connected for {client_id}")
            
            volume_service = getattr(self.state_machine, 'volume_service', None)
            snapcast_service = getattr(self.state_machine, 'snapcast_service', None)
            
            if not volume_service:
                self.logger.warning("⚠️ VolumeService not available")
                return
            
            # Extract client's ALSA volume
            alsa_volume = client.get("config", {}).get("volume", {}).get("percent", 0)
            self.logger.info(f"  - Client ALSA volume: {alsa_volume}")

            # NEW: Switch group to Multiroom BEFORE initializing volume
            if snapcast_service:
                self.logger.info(f"  - Setting client group to Multiroom...")
                await snapcast_service.set_client_group_to_multiroom(client_id)

            # Initialize volume
            result = await volume_service.initialize_new_client_volume(client_id, alsa_volume)
            self.logger.info(f"  - initialize_new_client_volume result: {result}")
            
        except Exception as e:
            self.logger.error(f"❌ Error initializing new client: {e}", exc_info=True)

    async def _sync_existing_client_volume(self, client_id: str, client: Dict[str, Any]) -> None:
        """Synchronizes existing client volume from Snapcast"""
        try:
            self.logger.info(f"🔄 _sync_existing_client_volume for {client_id}")

            volume_service = getattr(self.state_machine, 'volume_service', None)
            snapcast_service = getattr(self.state_machine, 'snapcast_service', None)

            if not volume_service:
                self.logger.warning("⚠️ VolumeService not available")
                return

            snapcast_alsa_volume = client.get("config", {}).get("volume", {}).get("percent", 0)
            self.logger.info(f"  - Client ALSA volume from Snapcast: {snapcast_alsa_volume}")

            # Sync existing volume without modifying it
            await volume_service.sync_existing_client_from_snapcast(client_id, snapcast_alsa_volume)

            # Switch group to Multiroom (necessary even for existing clients)
            if snapcast_service:
                self.logger.info(f"  - Setting client group to Multiroom...")
                await snapcast_service.set_client_group_to_multiroom(client_id)

        except Exception as e:
            self.logger.error(f"❌ Error syncing existing client {client_id}: {e}", exc_info=True)

    async def _broadcast_snapcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Broadcasts a Snapcast event via the Milo WebSocket system"""
        if self.state_machine:
            await self.state_machine.broadcast_event("snapcast", event_type, {
                **data,
                "source": "snapcast_websocket"
            })
            
            self.logger.debug(f"Broadcasted Snapcast event: {event_type}")