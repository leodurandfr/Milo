"""
Module pour surveiller un serveur Snapcast en temps réel via WebSocket.
"""
import logging
import json
import asyncio
import websockets
import time
from typing import Callable, Dict, Any, Optional, Awaitable

class SnapcastMonitor:
    """
    Surveille l'état d'un serveur Snapcast en temps réel via l'API WebSocket.
    Version optimisée sans health check.
    """
    
    def __init__(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        """
        Initialise le moniteur Snapcast.
        
        Args:
            callback: Fonction appelée lors des événements WebSocket
        """
        self.logger = logging.getLogger("plugin.snapclient.monitor")
        self.callback = callback
        self.host = None
        self.ws_task = None
        self.is_connected = False
        self._stopping = False
        self._connection = None
    
    def set_connection_reference(self, connection) -> None:
        """
        Définit une référence à l'objet de connexion snapclient.
        
        Args:
            connection: Référence à l'objet SnapclientConnection
        """
        self._connection = connection
    
    async def start(self, host: str) -> bool:
        """
        Démarre la surveillance WebSocket pour un serveur spécifique.
        
        Args:
            host: Adresse du serveur Snapcast à surveiller
            
        Returns:
            bool: True si le démarrage a réussi, False sinon
        """
        try:
            # Arrêter le moniteur existant si nécessaire
            await self.stop()
            
            self.host = host
            self._stopping = False
            self.ws_task = asyncio.create_task(self._monitor_websocket())
            
            self.logger.info(f"Moniteur WebSocket démarré pour {host}")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du moniteur: {str(e)}")
            return False
    
    async def stop(self) -> bool:
        """
        Arrête la surveillance WebSocket.
        
        Returns:
            bool: True si l'arrêt a réussi, False sinon
        """
        try:
            self._stopping = True
            
            # Arrêter le moniteur WebSocket
            if self.ws_task and not self.ws_task.done():
                self.ws_task.cancel()
                try:
                    await self.ws_task
                except asyncio.CancelledError:
                    pass
                self.ws_task = None
            
            self.host = None
            self.is_connected = False
            self.logger.info("Moniteur WebSocket arrêté")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt du moniteur: {str(e)}")
            return False

    async def _monitor_websocket(self) -> None:
        """
        Surveille le WebSocket pour les événements serveur.
        Version améliorée avec meilleure détection de déconnexion.
        """
        while not self._stopping:
            try:
                # Construire l'URL WebSocket
                uri = f"ws://{self.host}:1780/jsonrpc"
                self.logger.debug(f"Connexion WebSocket à {uri}")
                
                try:
                    # Se connecter au WebSocket avec un timeout plus court
                    websocket = await asyncio.wait_for(
                        websockets.connect(uri, ping_interval=5, ping_timeout=10),  # Ajouter ping_interval et ping_timeout
                        timeout=2.0
                    )
                    
                    # Connexion établie
                    self.is_connected = True
                    self.logger.info(f"Connexion WebSocket établie avec {self.host}")
                    await self._notify_callback({
                        "event": "monitor_connected",
                        "host": self.host,
                        "timestamp": time.time()
                    })
                    
                    # Abonnement aux événements du serveur
                    await websocket.send(json.dumps({
                        "id": 4,
                        "jsonrpc": "2.0",
                        "method": "Server.Subscribe",
                        "params": {
                            "event": "on_update"
                        }
                    }))

                    # Boucle d'écoute des messages avec ping/pong régulier
                    last_message_time = time.time()
                    ping_interval = 3.0  # En secondes
                    
                    while not self._stopping:
                        try:
                            # Vérifier si on doit envoyer un ping
                            current_time = time.time()
                            if current_time - last_message_time > ping_interval:
                                # Envoyer une requête de ping (GetStatus fait office de ping)
                                try:
                                    await websocket.send(json.dumps({
                                        "id": 999,
                                        "jsonrpc": "2.0",
                                        "method": "Server.GetStatus"
                                    }))
                                    last_message_time = current_time
                                except Exception as e:
                                    self.logger.warning(f"Erreur lors de l'envoi du ping: {e}")
                                    break  # Sortir de la boucle pour reconnecter
                            
                            # Attendre un message avec timeout court
                            message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                            last_message_time = time.time()  # Actualiser le temps du dernier message
                            
                            # Traiter le message
                            data = json.loads(message)
                            await self._notify_callback({
                                "event": "server_event",
                                "data": data,
                                "timestamp": time.time()
                            })
                            
                        except asyncio.TimeoutError:
                            # Aucun message reçu pendant timeout mais c'est normal
                            continue
                        except websockets.exceptions.ConnectionClosed as e:
                            self.logger.warning(f"Connexion WebSocket fermée pendant réception: {e}")
                            break
                        except Exception as e:
                            self.logger.error(f"Erreur lors de la réception: {e}")
                            break
                    
                    # Fermer la connexion proprement
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                        
                except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
                    self.logger.warning(f"Impossible d'établir la connexion WebSocket à {self.host}: {e}")
                    
                    # Notifier la déconnexion si on était connecté
                    if self.is_connected:
                        self.is_connected = False
                        await self._notify_callback({
                            "event": "monitor_disconnected",
                            "host": self.host,
                            "reason": "connection_failed",
                            "error": str(e),
                            "timestamp": time.time()
                        })
                    
                    # Attendre avant de réessayer
                    await asyncio.sleep(1)
                    continue
                
            except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                # Connexion perdue
                if self.is_connected:
                    self.is_connected = False
                    self.logger.warning(f"Connexion WebSocket perdue: {str(e)}")
                    
                    # Notifier le callback
                    await self._notify_callback({
                        "event": "monitor_disconnected",
                        "host": self.host,
                        "reason": "connection_lost",
                        "error": str(e),
                        "timestamp": time.time()
                    })
                
                # Attendre avant de réessayer
                if not self._stopping:
                    await asyncio.sleep(1)
            
            except Exception as e:
                self.logger.error(f"Erreur dans le moniteur WebSocket: {str(e)}")
                
                # Notifier la déconnexion si on était connecté
                if self.is_connected:
                    self.is_connected = False
                    await self._notify_callback({
                        "event": "monitor_disconnected",
                        "host": self.host,
                        "reason": "unexpected_error",
                        "error": str(e),
                        "timestamp": time.time()
                    })
                    
                if not self._stopping:
                    await asyncio.sleep(1)
    
    async def _notify_callback(self, data: Dict[str, Any]) -> None:
        """
        Notifie le callback avec les données fournies.
        
        Args:
            data: Données à passer au callback
        """
        try:
            await self.callback(data)
        except Exception as e:
            self.logger.error(f"Erreur dans le callback du moniteur: {str(e)}")