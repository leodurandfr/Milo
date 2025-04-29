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
    Version optimisée avec backoff exponentiel et log réduits.
    """
    
    def __init__(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        """
        Initialise le moniteur Snapcast.
        """
        self.logger = logging.getLogger("plugin.snapclient.monitor")
        self.callback = callback
        self.host = None
        self.ws_task = None
        self.is_connected = False
        self._stopping = False
        self._connection = None
        
        # Paramètres pour le backoff exponentiel et la réduction des logs
        self._reconnect_attempt = 0
        self._max_reconnect_delay = 30  # secondes
        self._log_frequency = 5  # ne logger qu'une tentative sur 5 après plusieurs échecs
    
    def set_connection_reference(self, connection) -> None:
        """Définit une référence à l'objet de connexion snapclient."""
        self._connection = connection
    
    async def start(self, host: str) -> bool:
        """Démarre la surveillance WebSocket pour un serveur spécifique."""
        try:
            # Arrêter le moniteur existant si nécessaire
            await self.stop()
            
            self.host = host
            self._stopping = False
            self._reconnect_attempt = 0  # Réinitialiser le compteur de tentatives
            self.ws_task = asyncio.create_task(self._monitor_websocket())
            
            self.logger.info(f"Moniteur WebSocket démarré pour {host}")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du moniteur: {str(e)}")
            return False
    
    async def stop(self) -> bool:
        """Arrête la surveillance WebSocket."""
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
            self._reconnect_attempt = 0
            self.logger.info("Moniteur WebSocket arrêté")
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt du moniteur: {str(e)}")
            return False

    async def _monitor_websocket(self) -> None:
        """
        Surveille le WebSocket pour les événements serveur.
        Implémente un backoff exponentiel et limite les logs.
        """
        while not self._stopping:
            try:
                # Vérifier si le host est toujours défini (peut être réinitialisé par stop())
                if not self.host:
                    await asyncio.sleep(1)
                    continue
                
                # Construire l'URL WebSocket
                uri = f"ws://{self.host}:1780/jsonrpc"
                
                # Déterminer si on doit logger cette tentative
                should_log = (self._reconnect_attempt == 0 or 
                             self._reconnect_attempt % self._log_frequency == 0)
                
                if should_log:
                    self.logger.debug(f"Tentative {self._reconnect_attempt+1} de connexion WebSocket à {uri}")
                
                try:
                    # Se connecter au WebSocket avec un timeout
                    websocket = await asyncio.wait_for(
                        websockets.connect(uri, ping_interval=5, ping_timeout=10),
                        timeout=2.0
                    )
                    
                    # Connexion réussie : réinitialiser le compteur de tentatives
                    self._reconnect_attempt = 0
                    
                    # Mettre à jour l'état et notifier
                    self.is_connected = True
                    self.logger.info(f"Connexion WebSocket établie avec {self.host}")
                    await self._notify_callback({
                        "event": "monitor_connected",
                        "host": self.host,
                        "timestamp": time.time()
                    })
                    
                    # Abonnement aux événements
                    await websocket.send(json.dumps({
                        "id": 4,
                        "jsonrpc": "2.0",
                        "method": "Server.Subscribe",
                        "params": {
                            "event": "on_update"
                        }
                    }))

                    # Boucle d'écoute des messages
                    last_message_time = time.time()
                    ping_interval = 3.0
                    
                    while not self._stopping:
                        try:
                            # Gérer les pings
                            current_time = time.time()
                            if current_time - last_message_time > ping_interval:
                                try:
                                    await websocket.send(json.dumps({
                                        "id": 999,
                                        "jsonrpc": "2.0",
                                        "method": "Server.GetStatus"
                                    }))
                                    last_message_time = current_time
                                except Exception as e:
                                    if should_log:
                                        self.logger.warning(f"Erreur lors de l'envoi du ping: {e}")
                                    break
                            
                            # Recevoir les messages
                            message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                            last_message_time = time.time()
                            
                            # Traiter le message reçu
                            data = json.loads(message)
                            await self._notify_callback({
                                "event": "server_event",
                                "data": data,
                                "timestamp": time.time()
                            })
                            
                        except asyncio.TimeoutError:
                            continue
                        except websockets.exceptions.ConnectionClosed as e:
                            if should_log:
                                self.logger.warning(f"Connexion WebSocket fermée pendant réception: {e}")
                            break
                        except Exception as e:
                            self.logger.error(f"Erreur lors de la réception: {e}")
                            break
                    
                    # Fermer proprement
                    try:
                        await websocket.close()
                    except Exception:
                        pass
                        
                except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as e:
                    # Incrémenter le compteur de tentatives
                    self._reconnect_attempt += 1
                    
                    # Ne logger que si nécessaire
                    if should_log:
                        self.logger.warning(f"Impossible d'établir la connexion WebSocket à {self.host}: {e}")
                    
                    # Notifier la déconnexion uniquement lors de la première erreur
                    if self.is_connected:
                        self.is_connected = False
                        await self._notify_callback({
                            "event": "monitor_disconnected",
                            "host": self.host,
                            "reason": "connection_failed",
                            "error": str(e),
                            "timestamp": time.time()
                        })
                    
                    # Backoff exponentiel pour les reconnexions
                    # max(min_delay, min(max_delay, base_delay * 2^attempt))
                    delay = min(self._max_reconnect_delay, 1 * (2 ** min(self._reconnect_attempt, 10)))
                    if should_log:
                        self.logger.debug(f"Attente de {delay}s avant la prochaine tentative")
                    
                    # Vérifier régulièrement si on doit s'arrêter pendant le délai
                    for _ in range(int(delay * 2)):
                        if self._stopping:
                            break
                        await asyncio.sleep(0.5)
                    continue
                
            except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, OSError) as e:
                # Connexion perdue
                self._reconnect_attempt += 1
                
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
                
                # Attendre avec backoff exponentiel
                if not self._stopping:
                    delay = min(self._max_reconnect_delay, 1 * (2 ** min(self._reconnect_attempt, 10)))
                    await asyncio.sleep(delay)
            
            except Exception as e:
                self._reconnect_attempt += 1
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
                    delay = min(self._max_reconnect_delay, 1 * (2 ** min(self._reconnect_attempt, 10)))
                    await asyncio.sleep(delay)
    
    async def _notify_callback(self, data: Dict[str, Any]) -> None:
        """Notifie le callback avec les données fournies."""
        try:
            await self.callback(data)
        except Exception as e:
            self.logger.error(f"Erreur dans le callback du moniteur: {str(e)}")