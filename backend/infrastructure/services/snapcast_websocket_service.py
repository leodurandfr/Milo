# backend/infrastructure/services/snapcast_websocket_service.py
"""
Service WebSocket Snapcast OPTIM - Observer pattern au lieu de cross-référence
"""
import asyncio
import json
import logging
import aiohttp
from typing import Dict, Any, Optional

class SnapcastWebSocketService:
    """Service WebSocket pour notifications Snapcast temps réel - Version OPTIM"""
    
    def __init__(self, state_machine, routing_service, host: str = "localhost", port: int = 1780):
        self.state_machine = state_machine
        self.routing_service = routing_service  # OPTIM : Pour vérifier l'état initial
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
        
        # ID pour les requêtes JSON-RPC
        self.request_id = 0
    
    async def initialize(self) -> bool:
        """Initialise le service WebSocket - Version OPTIM simplifiée"""
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
        """Boucle de connexion avec reconnexion intelligente OPTIM"""
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
                # OPTIM : Backoff exponentiel pour éviter le spam de reconnexion
                self.logger.info(f"Reconnecting to Snapcast WebSocket in {reconnect_delay} seconds...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_delay)
    
    async def _connect_and_listen(self) -> None:
        """Se connecte et écoute les messages WebSocket"""
        try:
            self.logger.info(f"Connecting to Snapcast WebSocket: {self.ws_url}")
            
            # OPTIM : Timeout de connexion plus court
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
        """Traite une notification Snapcast - Version OPTIM avec mapping simplifié"""
        method = notification.get("method")
        params = notification.get("params", {})
        
        self.logger.debug(f"Received Snapcast notification: {method}")
        
        # OPTIM : Mapping simplifié des notifications importantes
        critical_notifications = {
            "Client.OnConnect": lambda p: self._handle_client_connect(p),
            "Client.OnDisconnect": lambda p: self._handle_client_disconnect(p),
            "Client.OnVolumeChanged": lambda p: self._handle_client_volume_changed(p),
            "Client.OnNameChanged": lambda p: self._handle_client_name_changed(p),
            "Client.OnMute": lambda p: self._handle_client_mute_changed(p)
        }
        
        if method in critical_notifications:
            await critical_notifications[method](params)
        else:
            # OPTIM : Log debug pour les autres notifications (pas de handlers inutiles)
            self.logger.debug(f"Unhandled notification: {method}")
    
    async def _handle_response(self, response: Dict[str, Any]) -> None:
        """Traite une réponse à une requête"""
        if "error" in response:
            self.logger.error(f"Snapcast RPC error: {response['error']}")
    
    # === HANDLERS DES NOTIFICATIONS CRITIQUES OPTIM ===
    
    async def _handle_client_connect(self, params: Dict[str, Any]) -> None:
        """Client connecté"""
        client = params.get("client", {})
        await self._broadcast_snapcast_event("client_connected", {
            "client_id": client.get("id"),
            "client_name": client.get("config", {}).get("name"),
            "client_host": client.get("host", {}).get("name"),
            "client_ip": client.get("host", {}).get("ip", "").replace("::ffff:", ""),
            "client": client
        })
    
    async def _handle_client_connect(self, params: Dict[str, Any]) -> None:
        """Client connecté - NOUVEAU: Synchronise le volume avec le système principal"""
        client = params.get("client", {})
        client_id = client.get("id")
        client_name = client.get("config", {}).get("name", "Unknown")
        client_host = client.get("host", {}).get("name", "Unknown")
        client_ip = client.get("host", {}).get("ip", "").replace("::ffff:", "")
        
        # 🚀 NOUVEAU: Synchroniser le volume du nouveau client
        if client_id:
            try:
                # Récupérer le volume actuel du système principal via VolumeService
                if hasattr(self.state_machine, 'volume_service') and self.state_machine.volume_service:
                    current_volume = await self.state_machine.volume_service.get_volume()
                    # Convertir le volume d'affichage (0-100) vers ALSA (0-65)
                    alsa_volume = self.state_machine.volume_service._interpolate_from_display(current_volume)
                    
                    # Appliquer le volume au nouveau client via Snapcast
                    if hasattr(self.state_machine, 'snapcast_service') and self.state_machine.snapcast_service:
                        success = await self.state_machine.snapcast_service.set_volume(client_id, alsa_volume)
                        if success:
                            self.logger.info(f"🎵 New client {client_name} volume synchronized to {alsa_volume} (ALSA) = {current_volume}% (display)")
                        else:
                            self.logger.warning(f"⚠️ Failed to sync volume for new client {client_name}")
                    else:
                        self.logger.warning("⚠️ Snapcast service not available for volume sync")
                else:
                    self.logger.warning("⚠️ Volume service not available for volume sync")
                    
            except Exception as e:
                self.logger.error(f"Error synchronizing volume for new client {client_name}: {e}")
        
        # Broadcast de connexion (existant)
        await self._broadcast_snapcast_event("client_connected", {
            "client_id": client_id,
            "client_name": client_name,
            "client_host": client_host,
            "client_ip": client_ip,
            "client": client
        })
    
    async def _handle_client_volume_changed(self, params: Dict[str, Any]) -> None:
        """Volume client changé"""
        client_id = params.get("id")
        volume_data = params.get("volume", {})
        
        await self._broadcast_snapcast_event("client_volume_changed", {
            "client_id": client_id,
            "volume": volume_data.get("percent", 0),
            "muted": volume_data.get("muted", False)
        })
    
    async def _handle_client_name_changed(self, params: Dict[str, Any]) -> None:
        """Nom client changé"""
        await self._broadcast_snapcast_event("client_name_changed", {
            "client_id": params.get("id"),
            "name": params.get("name")
        })
    
    async def _handle_client_mute_changed(self, params: Dict[str, Any]) -> None:
        """Mute client changé"""
        volume_data = params.get("volume", {})
        await self._broadcast_snapcast_event("client_mute_changed", {
            "client_id": params.get("id"),
            "muted": volume_data.get("muted", False),
            "volume": volume_data.get("percent", 0)
        })
    
    async def _broadcast_snapcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Diffuse un événement Snapcast via le système WebSocket Milo"""
        if self.state_machine:
            await self.state_machine.broadcast_event("snapcast", event_type, {
                **data,
                "source": "snapcast_websocket"
            })
            
            self.logger.debug(f"Broadcasted Snapcast event: {event_type}")