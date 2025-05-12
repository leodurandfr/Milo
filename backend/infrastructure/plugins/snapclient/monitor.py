"""
Moniteur WebSocket pour Snapcast - Version concise
"""
import logging
import json
import asyncio
import websockets
from typing import Callable, Dict, Any, Awaitable

class SnapcastMonitor:
    """Surveille l'état d'un serveur Snapcast via WebSocket."""
    
    def __init__(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        self.logger = logging.getLogger("plugin.snapclient.monitor")
        self.callback = callback
        self.host = None
        self.ws_task = None
        self.is_connected = False
        self._stopping = False
    
    async def start(self, host: str) -> bool:
        """Démarre la surveillance WebSocket."""
        try:
            # Arrêter le moniteur existant si nécessaire
            await self.stop()
            
            self.host = host
            self._stopping = False
            self.ws_task = asyncio.create_task(self._monitor_loop())
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage moniteur: {e}")
            return False
    
    async def stop(self) -> bool:
        """Arrête la surveillance WebSocket."""
        self._stopping = True
        
        if self.ws_task and not self.ws_task.done():
            self.ws_task.cancel()
            try:
                await self.ws_task
            except asyncio.CancelledError:
                pass
        
        self.host = None
        self.is_connected = False
        self.ws_task = None
        
        return True
    
    async def _monitor_loop(self) -> None:
        """Boucle principale de surveillance."""
        retry_count = 0
        max_retry_delay = 30
        
        while not self._stopping:
            try:
                # Construire l'URL WebSocket
                uri = f"ws://{self.host}:1780/jsonrpc"
                
                # Se connecter au WebSocket
                async with websockets.connect(uri, ping_interval=5, ping_timeout=10) as ws:
                    self.is_connected = True
                    retry_count = 0
                    
                    # Notifier la connexion
                    await self.callback({
                        "event": "monitor_connected",
                        "host": self.host
                    })
                    
                    # Abonnement aux événements
                    await ws.send(json.dumps({
                        "id": 4,
                        "jsonrpc": "2.0",
                        "method": "Server.Subscribe",
                        "params": {"event": "on_update"}
                    }))
                    
                    # Boucle d'écoute
                    while not self._stopping:
                        try:
                            # Recevoir les messages
                            message = await asyncio.wait_for(ws.recv(), timeout=5.0)
                            
                            # Traiter le message
                            data = json.loads(message)
                            await self.callback({
                                "event": "server_event",
                                "data": data
                            })
                            
                        except asyncio.TimeoutError:
                            # Envoyer une requête pour vérifier que la connexion est toujours active
                            try:
                                await ws.send(json.dumps({
                                    "id": 999,
                                    "jsonrpc": "2.0",
                                    "method": "Server.GetStatus"
                                }))
                            except Exception:
                                # Connexion perdue
                                break
                        except websockets.exceptions.ConnectionClosed:
                            break
                
            except (asyncio.TimeoutError, ConnectionRefusedError, websockets.exceptions.ConnectionClosed) as e:
                # Connexion perdue
                if self.is_connected:
                    self.is_connected = False
                    await self.callback({
                        "event": "monitor_disconnected",
                        "host": self.host,
                        "reason": str(e)
                    })
                
                # Backoff exponentiel
                retry_count += 1
                delay = min(max_retry_delay, 1 * (2 ** min(retry_count, 5)))
                
                # Attendre avant de réessayer
                for _ in range(int(delay * 2)):
                    if self._stopping:
                        break
                    await asyncio.sleep(0.5)
                    
            except Exception as e:
                self.logger.error(f"Erreur moniteur: {e}")
                
                if self.is_connected:
                    self.is_connected = False
                    await self.callback({
                        "event": "monitor_disconnected",
                        "host": self.host,
                        "reason": str(e)
                    })
                
                await asyncio.sleep(5)