# backend/infrastructure/services/snapcast_websocket_service.py
"""
Service WebSocket Snapcast pour notifications temps réel - Version OPTIM oakOS
"""
import asyncio
import json
import logging
import aiohttp
from typing import Dict, Any, Optional

class SnapcastWebSocketService:
    """Service WebSocket pour notifications Snapcast temps réel"""
    
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
        self.should_connect = False  # AJOUT : contrôle conditionnel
        self.reconnect_task = None
        
        # ID pour les requêtes JSON-RPC
        self.request_id = 0
    
    async def initialize(self) -> bool:
        """Initialise le service WebSocket (mais ne se connecte pas encore)"""
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
        """Boucle de connexion avec reconnexion automatique"""
        while self.running and self.should_connect:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"WebSocket connection error: {e}")
            
            if self.running and self.should_connect:
                self.logger.info("Reconnecting to Snapcast WebSocket in 5 seconds...")
                await asyncio.sleep(5)
    
    async def _connect_and_listen(self) -> None:
        """Se connecte et écoute les messages WebSocket"""
        try:
            self.logger.info(f"Connecting to Snapcast WebSocket: {self.ws_url}")
            
            self.websocket = await self.session.ws_connect(self.ws_url)
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
        
        self.logger.debug(f"Received Snapcast notification: {method}")
        
        # Map des notifications Snapcast vers événements oakOS
        notification_handlers = {
            "Client.OnConnect": self._handle_client_connect,
            "Client.OnDisconnect": self._handle_client_disconnect,
            "Client.OnVolumeChanged": self._handle_client_volume_changed,
            "Client.OnNameChanged": self._handle_client_name_changed,
            "Client.OnLatencyChanged": self._handle_client_latency_changed,
            "Client.OnMute": self._handle_client_mute_changed,
            "Server.OnUpdate": self._handle_server_update,
            "Stream.OnUpdate": self._handle_stream_update,
            "Group.OnMute": self._handle_group_mute,
            "Group.OnStreamChanged": self._handle_group_stream_changed
        }
        
        handler = notification_handlers.get(method)
        if handler:
            await handler(params)
        else:
            self.logger.debug(f"Unhandled notification: {method}")
    
    async def _handle_response(self, response: Dict[str, Any]) -> None:
        """Traite une réponse à une requête"""
        if "error" in response:
            self.logger.error(f"Snapcast RPC error: {response['error']}")
        # Les réponses ne génèrent pas d'événements pour l'instant
    
    # === HANDLERS DES NOTIFICATIONS SNAPCAST ===
    
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
    
    async def _handle_client_disconnect(self, params: Dict[str, Any]) -> None:
        """Client déconnecté"""
        client = params.get("client", {})
        await self._broadcast_snapcast_event("client_disconnected", {
            "client_id": client.get("id"),
            "client_name": client.get("config", {}).get("name"),
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
    
    async def _handle_client_latency_changed(self, params: Dict[str, Any]) -> None:
        """Latence client changée"""
        await self._broadcast_snapcast_event("client_latency_changed", {
            "client_id": params.get("id"),
            "latency": params.get("latency")
        })
    
    async def _handle_client_mute_changed(self, params: Dict[str, Any]) -> None:
        """Mute client changé"""
        volume_data = params.get("volume", {})
        await self._broadcast_snapcast_event("client_mute_changed", {
            "client_id": params.get("id"),
            "muted": volume_data.get("muted", False),
            "volume": volume_data.get("percent", 0)
        })
    
    async def _handle_server_update(self, params: Dict[str, Any]) -> None:
        """Mise à jour serveur"""
        await self._broadcast_snapcast_event("server_update", {
            "server": params.get("server", {})
        })
    
    async def _handle_stream_update(self, params: Dict[str, Any]) -> None:
        """Mise à jour stream"""
        await self._broadcast_snapcast_event("stream_update", {
            "stream": params.get("stream", {})
        })
    
    async def _handle_group_mute(self, params: Dict[str, Any]) -> None:
        """Groupe mute changé"""
        await self._broadcast_snapcast_event("group_mute_changed", {
            "group_id": params.get("id"),
            "muted": params.get("mute")
        })
    
    async def _handle_group_stream_changed(self, params: Dict[str, Any]) -> None:
        """Stream du groupe changé"""
        await self._broadcast_snapcast_event("group_stream_changed", {
            "group_id": params.get("id"),
            "stream_id": params.get("stream_id")
        })
    
    async def _broadcast_snapcast_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Diffuse un événement Snapcast via le système WebSocket oakOS"""
        if self.state_machine:
            await self.state_machine.broadcast_event("snapcast", event_type, {
                **data,
                "source": "snapcast_websocket"
            })
            
            self.logger.debug(f"Broadcasted Snapcast event: {event_type}")