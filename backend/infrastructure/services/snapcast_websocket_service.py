# backend/infrastructure/services/snapcast_websocket_service.py
"""
Service WebSocket Snapcast ALLÉGÉ - SANS gestion du volume (délégué au VolumeService)
"""
import asyncio
import json
import logging
import aiohttp
from typing import Dict, Any, Optional

class SnapcastWebSocketService:
    """Service WebSocket pour notifications Snapcast NON-VOLUME - VolumeService gère tout le volume"""
    
    def __init__(self, state_machine, routing_service, host: str = "localhost", port: int = 1780):
        self.state_machine = state_machine
        self.routing_service = routing_service
        self.host = host
        self.port = port
        self.ws_url = f"ws://{host}:{port}/jsonrpc"
        self.logger = logging.getLogger(__name__)
        
        # État de connexion
        self.session: Optional[aiohttp.ClientSession] = None
        self.websocket: Optional[aiohttp.ClientWebSocketResponse] = None
        self.running = False
        self.should_connect = False
        self.reconnect_task = None
        self._known_client_ids = set()

        
        # ID pour les requêtes JSON-RPC
        self.request_id = 0
    
    async def initialize(self) -> bool:
        """Initialise le service WebSocket"""
        try:
            self.logger.info(f"Initializing Snapcast WebSocket service: {self.ws_url}")
            self.session = aiohttp.ClientSession()
            self.running = True
            
            # Vérifier l'état initial du multiroom
            if self.routing_service:
                routing_state = self.routing_service.get_state()
                self.should_connect = routing_state.multiroom_enabled
                
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
        """Démarre la connexion WebSocket quand le multiroom est activé"""
        if self.should_connect:
            return  # Déjà en cours
            
        self.logger.info("Starting Snapcast WebSocket connection (multiroom enabled)")
        self.should_connect = True
        
        if not self.reconnect_task and self.running:
            self.reconnect_task = asyncio.create_task(self._connection_loop())
    
    async def stop_connection(self) -> None:
        """Arrête la connexion WebSocket quand le multiroom est désactivé"""
        if not self.should_connect:
            return  # Déjà arrêté
            
        self.logger.info("Stopping Snapcast WebSocket connection (multiroom disabled)")
        self.should_connect = False
        
        # Annuler la tâche de reconnexion
        if self.reconnect_task:
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass
            self.reconnect_task = None
        
        # Fermer la connexion WebSocket actuelle
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
    
    async def cleanup(self) -> None:
        """Nettoie les ressources"""
        self.logger.info("Cleaning up Snapcast WebSocket service")
        self.running = False
        self.should_connect = False
        
        # Annuler la tâche de reconnexion
        if self.reconnect_task:
            self.reconnect_task.cancel()
            try:
                await self.reconnect_task
            except asyncio.CancelledError:
                pass
        
        # Fermer la connexion WebSocket
        if self.websocket:
            await self.websocket.close()
        
        # Fermer la session
        if self.session:
            await self.session.close()
    
    async def _connection_loop(self) -> None:
        """Boucle de connexion avec reconnexion intelligente"""
        reconnect_delay = 5  # Délai initial
        max_delay = 30       # Délai maximum
        
        while self.running and self.should_connect:
            try:
                await self._connect_and_listen()
                # Reset délai si connexion réussie
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
        """Se connecte et écoute les messages WebSocket"""
        try:
            self.logger.info(f"Connecting to Snapcast WebSocket: {self.ws_url}")
            
            timeout = aiohttp.ClientTimeout(total=5)
            self.websocket = await self.session.ws_connect(self.ws_url, timeout=timeout)
            self.logger.info("Connected to Snapcast WebSocket")
            
            # Envoyer un ping initial pour vérifier la connexion
            await self._send_request("Server.GetRPCVersion")
            
            # Écouter les messages
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
    
    async def _send_request(self, method: str, params: Optional[Dict] = None) -> None:
        """Envoie une requête JSON-RPC"""
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
        """Traite un message JSON-RPC reçu"""
        try:
            # Notification Snapcast (pas de "id" dans les notifications)
            if "method" in data and "id" not in data:
                await self._handle_notification(data)
            # Réponse à une requête (avec "id")
            elif "result" in data or "error" in data:
                await self._handle_response(data)
                
        except Exception as e:
            self.logger.error(f"Error handling message: {e}")
    
    async def _handle_notification(self, notification: Dict[str, Any]) -> None:
        """Traite une notification Snapcast"""
        method = notification.get("method")
        params = notification.get("params", {})
        
        self.logger.info(f"📨 SNAPCAST NOTIFICATION RECEIVED: {method}")
        
        non_volume_notifications = {
            "Client.OnConnect": lambda p: self._handle_client_connect(p),
            "Client.OnDisconnect": lambda p: self._handle_client_disconnect(p),
            "Client.OnNameChanged": lambda p: self._handle_client_name_changed(p),
            "Server.OnUpdate": lambda p: self._handle_server_update(p)  # ← NOUVEAU
        }
        
        if method in non_volume_notifications:
            await non_volume_notifications[method](params)
        elif method in ["Client.OnVolumeChanged", "Client.OnMute"]:
            await self._delegate_volume_event_to_volume_service(method, params)
        else:
            self.logger.debug(f"Unhandled notification: {method}")
    
    async def _handle_server_update(self, params: Dict[str, Any]) -> None:
        """NOUVEAU : Gère Server.OnUpdate et détecte les nouveaux clients"""
        try:
            server = params.get("server", {})
            groups = server.get("groups", [])
            
            # Extraire tous les clients connectés
            current_client_ids = set()
            new_clients = []
            
            for group in groups:
                for client in group.get("clients", []):
                    if not client.get("connected"):
                        continue
                    
                    client_id = client.get("id")
                    current_client_ids.add(client_id)
                    
                    # Nouveau client détecté ?
                    if client_id not in self._known_client_ids:
                        self.logger.info(f"🟢 NEW CLIENT DETECTED in Server.OnUpdate: {client_id}")
                        new_clients.append(client)
            
            # Mettre à jour le cache
            self._known_client_ids = current_client_ids
            
            # Initialiser les nouveaux clients
            for client in new_clients:
                client_id = client.get("id")
                client_volume = client.get("config", {}).get("volume", {}).get("percent", 0)
                
                self.logger.info(f"  - Initializing new client {client_id} (current ALSA: {client_volume}%)")
                await self._notify_volume_service_client_connected(client_id, client)
            
        except Exception as e:
            self.logger.error(f"Error handling Server.OnUpdate: {e}", exc_info=True) 
        
    
    async def _handle_response(self, response: Dict[str, Any]) -> None:
        """Traite une réponse à une requête"""
        if "error" in response:
            self.logger.error(f"Snapcast RPC error: {response['error']}")
    
    # === HANDLERS ALLÉGÉS - SANS GESTION VOLUME ===
    
    async def _handle_client_connect(self, params: Dict[str, Any]) -> None:
        """Client connecté - Version avec volume converti (sans fallback)"""
        client = params.get("client", {})
        client_id = client.get("id")
        client_name = client.get("config", {}).get("name", "Unknown")
        client_host = client.get("host", {}).get("name", "Unknown")
        client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")
        alsa_volume = client.get("config", {}).get("volume", {}).get("percent", 0)
        
        # Conversion ALSA → Display (OBLIGATOIRE)
        volume_service = getattr(self.state_machine, 'volume_service', None)
        if not volume_service:
            self.logger.error("❌ VolumeService not available - cannot convert volume")
            return  # Ne pas envoyer l'événement avec un volume incorrect
        
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
        """Client déconnecté - Version allégée"""
        client = params.get("client", {})
        
        await self._broadcast_snapcast_event("client_disconnected", {
            "client_id": client.get("id"),
            "client_name": client.get("config", {}).get("name")
            # SUPPRIMÉ : données volume
        })
    
    async def _handle_client_name_changed(self, params: Dict[str, Any]) -> None:
        """Nom client changé - Version allégée"""
        await self._broadcast_snapcast_event("client_name_changed", {
            "client_id": params.get("id"),
            "name": params.get("name")
        })
    
    # === NOUVEAU : DÉLÉGATION AU VOLUME SERVICE ===
    
    async def _delegate_volume_event_to_volume_service(self, method: str, params: Dict[str, Any]) -> None:
        """NOUVEAU : Délègue les événements volume au VolumeService"""
        try:
            volume_service = getattr(self.state_machine, 'volume_service', None)
            if not volume_service:
                self.logger.warning("VolumeService not available for delegation")
                return
            
            client_id = params.get("id")
            if not client_id:
                return
            
            if method == "Client.OnVolumeChanged":
                volume_data = params.get("volume", {})
                alsa_volume = volume_data.get("percent", 0)
                # Déléguer la synchronisation au VolumeService
                await volume_service.sync_client_volume_from_external(client_id, alsa_volume)
                
            elif method == "Client.OnMute":
                volume_data = params.get("volume", {})
                alsa_volume = volume_data.get("percent", 0)
                # Déléguer la synchronisation au VolumeService  
                await volume_service.sync_client_volume_from_external(client_id, alsa_volume)
            
            self.logger.debug(f"Delegated {method} to VolumeService for client {client_id}")
            
        except Exception as e:
            self.logger.error(f"Error delegating to VolumeService: {e}")
    
    async def _notify_volume_service_client_connected(self, client_id: str, client: Dict[str, Any]) -> None:
        """Notifie VolumeService d'un nouveau client + bascule sur Multiroom"""
        try:
            self.logger.info(f"🔵 _notify_volume_service_client_connected for {client_id}")
            
            volume_service = getattr(self.state_machine, 'volume_service', None)
            snapcast_service = getattr(self.state_machine, 'snapcast_service', None)
            
            if not volume_service:
                self.logger.warning("⚠️ VolumeService not available")
                return
            
            # Extraire le volume ALSA du client
            alsa_volume = client.get("config", {}).get("volume", {}).get("percent", 0)
            self.logger.info(f"  - Client ALSA volume: {alsa_volume}")
            
            # NOUVEAU : Basculer le groupe sur Multiroom AVANT d'initialiser le volume
            if snapcast_service:
                self.logger.info(f"  - Setting client group to Multiroom...")
                await snapcast_service.set_client_group_to_multiroom(client_id)
            
            # Initialiser le volume
            result = await volume_service.initialize_new_client_volume(client_id, alsa_volume)
            self.logger.info(f"  - initialize_new_client_volume result: {result}")
            
        except Exception as e:
            self.logger.error(f"❌ Error initializing new client: {e}", exc_info=True)
            
        
    async def _broadcast_snapcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Diffuse un événement Snapcast via le système WebSocket Milo - VERSION ALLÉGÉE"""
        if self.state_machine:
            await self.state_machine.broadcast_event("snapcast", event_type, {
                **data,
                "source": "snapcast_websocket"
            })
            
            self.logger.debug(f"Broadcasted Snapcast event: {event_type}")