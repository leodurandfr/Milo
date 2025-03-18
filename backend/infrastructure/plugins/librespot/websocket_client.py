"""
Client WebSocket pour communiquer avec go-librespot.
"""
import aiohttp
import asyncio
import json
import logging
from typing import Dict, Any, Callable, Optional

class LibrespotWebSocketClient:
    """Client WebSocket pour les événements go-librespot"""
    
    def __init__(self, ws_url: str, event_handler: Callable[[str, Dict[str, Any]], None]):
        self.ws_url = ws_url
        self.event_handler = event_handler
        self.session = None
        self.ws_task = None
        self.logger = logging.getLogger("librespot.websocket")
        self.is_connected = False
    
    async def initialize(self, session: Optional[aiohttp.ClientSession] = None) -> bool:
        """Initialise le client WebSocket avec une session existante ou en crée une nouvelle"""
        try:
            if session:
                self.session = session
            else:
                self.session = aiohttp.ClientSession()
            
            self.logger.info(f"Client WebSocket initialisé avec URL: {self.ws_url}")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'initialisation du client WebSocket: {e}")
            return False
    
    async def start(self) -> None:
        """Démarre la connexion WebSocket"""
        if self.ws_task is None or self.ws_task.done():
            self.ws_task = asyncio.create_task(self._websocket_loop())
            self.logger.info("Connexion WebSocket démarrée")
    
    async def stop(self) -> None:
        """Arrête la connexion WebSocket"""
        if self.ws_task and not self.ws_task.done():
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass
            self.ws_task = None
            self.is_connected = False
            self.logger.info("Connexion WebSocket arrêtée")
    
    async def close(self) -> None:
        """Ferme la session si nous l'avons créée nous-mêmes"""
        if self.session and not self.session.closed:
            await self.session.close()
            self.session = None
    
    async def _websocket_loop(self) -> None:
        """Boucle principale de la connexion WebSocket avec reconnexion automatique"""
        retry_delay = 1
        max_retry_delay = 30
        
        while True:
            try:
                if not self.session or self.session.closed:
                    self.session = aiohttp.ClientSession()
                    
                self.logger.info(f"Tentative de connexion WebSocket à {self.ws_url}")
                
                async with self.session.ws_connect(self.ws_url) as ws:
                    self.logger.info("Connexion WebSocket établie")
                    self.is_connected = True
                    retry_delay = 1  # Réinitialiser le délai
                    
                    # Informer de la connexion WebSocket
                    await self.event_handler("ws_connected", {"ws_connected": True})
                    
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                event_type = data.get('type')
                                event_data = data.get('data', {})
                                
                                # Log l'événement reçu
                                self.logger.debug(f"Événement WebSocket reçu: {event_type}")
                                
                                # Appeler le gestionnaire d'événements
                                await self.event_handler(event_type, event_data)
                            except json.JSONDecodeError:
                                self.logger.error(f"Message non-JSON reçu: {msg.data}")
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            self.logger.error(f"Erreur WebSocket: {ws.exception()}")
                            break
                        elif msg.type == aiohttp.WSMsgType.CLOSED:
                            self.logger.warning("Connexion WebSocket fermée")
                            break
                
                self.is_connected = False
                self.logger.warning("Connexion WebSocket fermée, reconnexion...")
                
            except aiohttp.ClientError as e:
                self.is_connected = False
                self.logger.error(f"Erreur de connexion WebSocket: {e}")
            except asyncio.CancelledError:
                self.is_connected = False
                self.logger.info("Tâche WebSocket annulée")
                return
            except Exception as e:
                self.is_connected = False
                self.logger.error(f"Erreur WebSocket inattendue: {e}")
            
            # Attendre avant de reconnecter (avec backoff exponentiel)
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)