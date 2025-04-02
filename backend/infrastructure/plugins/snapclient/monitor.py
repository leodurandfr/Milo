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
    Permet une détection instantanée des changements d'état du serveur.
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
        
        # Ajout d'un intervalle de vérification de santé
        self.health_check_task = None
    
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
            
            # Démarrer une vérification périodique de la santé de la connexion
            self.health_check_task = asyncio.create_task(self._health_check_loop())
            
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
            
            # Arrêter la vérification de santé
            if self.health_check_task and not self.health_check_task.done():
                self.health_check_task.cancel()
                try:
                    await self.health_check_task
                except asyncio.CancelledError:
                    pass
                self.health_check_task = None
            
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
    
    async def _health_check_loop(self) -> None:
        """
        Vérifie périodiquement si la connexion WebSocket est toujours active.
        Si la connexion est perdue, notifie le callback.
        """
        try:
            while not self._stopping:
                # OPTIMISATION: Réduire l'intervalle pour une détection plus rapide (2s → 1s)
                await asyncio.sleep(1)  
                
                if not self.is_connected and self.host:
                    # Connexion perdue, notifier
                    await self._notify_callback({
                        "event": "monitor_disconnected",
                        "host": self.host,
                        "reason": "health_check_failed"
                    })
                
                # Vérifier si le serveur est toujours en vie
                if self.host and self.is_connected:
                    try:
                        # Optimisé: Réduire le timeout pour une détection plus rapide
                        reader, writer = await asyncio.wait_for(
                            asyncio.open_connection(self.host, 1704),
                            timeout=0.3  # Réduit de 0.5s à 0.3s
                        )
                        writer.close()
                        await writer.wait_closed()
                    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
                        # Serveur inaccessible
                        self.is_connected = False
                        self.logger.warning(f"Health check: serveur {self.host} inaccessible")
                        
                        # Notifier le callback immédiatement
                        await self._notify_callback({
                            "event": "monitor_disconnected",
                            "host": self.host,
                            "reason": "server_unreachable",
                            "timestamp": time.time()
                        })
        except asyncio.CancelledError:
            # Tâche annulée normalement
            pass
        except Exception as e:
            self.logger.error(f"Erreur dans la boucle de vérification de santé: {str(e)}")

    
    async def _monitor_websocket(self) -> None:
        """
        Surveille le WebSocket pour les événements serveur.
        Cette méthode s'exécute en continu jusqu'à l'arrêt du moniteur.
        """
        while not self._stopping:
            try:
                # Construire l'URL WebSocket
                uri = f"ws://{self.host}:1780/jsonrpc"
                self.logger.debug(f"Connexion WebSocket à {uri}")
                
                try:
                    # Version compatible avec toutes les versions de websockets
                    websocket_connect_task = websockets.connect(uri)
                    websocket = await asyncio.wait_for(websocket_connect_task, timeout=2.0)
                    
                    # Connexion établie
                    self.is_connected = True
                    self.logger.info(f"Connexion WebSocket établie avec {self.host}")
                    await self._notify_callback({
                        "event": "monitor_connected",
                        "host": self.host,
                        "timestamp": time.time()
                    })
                    
                    # Envoi d'une requête initiale pour s'abonner aux événements
                    await websocket.send(json.dumps({
                        "id": 3, 
                        "jsonrpc": "2.0",
                        "method": "Client.Subscribe",
                        "params": {
                            "event": "on_connect"
                        }
                    }))

                    # S'abonner aux événements du serveur
                    await websocket.send(json.dumps({
                        "id": 4,
                        "jsonrpc": "2.0",
                        "method": "Server.Subscribe",
                        "params": {
                            "event": "on_update"
                        }
                    }))

                    self.logger.info("Abonnements aux événements du serveur envoyés")
                    
                    # Boucle d'écoute des messages
                    while not self._stopping:
                        try:
                            # Attendre un message avec timeout
                            message = await asyncio.wait_for(websocket.recv(), timeout=3.0)
                            self.logger.debug(f"Message WebSocket reçu: {message[:100]}...")
                            
                            data = json.loads(message)
                            
                            # Notifier le callback
                            await self._notify_callback({
                                "event": "server_event",
                                "data": data,
                                "timestamp": time.time()
                            })
                            
                        except asyncio.TimeoutError:
                            # Timeout normal pour vérifier _stopping
                            if not self._stopping:
                                # Envoyer un ping pour maintenir la connexion active
                                try:
                                    await websocket.send(json.dumps({
                                        "id": 999,
                                        "jsonrpc": "2.0",
                                        "method": "Server.GetStatus"
                                    }))
                                    self.logger.debug("Ping envoyé au serveur WebSocket")
                                except Exception as ping_e:
                                    self.logger.warning(f"Erreur lors de l'envoi du ping: {str(ping_e)}")
                                    break
                            continue
                        except Exception as e:
                            self.logger.warning(f"Erreur lors de la réception d'un message: {str(e)}")
                            # Pause avant de réessayer
                            await asyncio.sleep(0.5)
                    
                    # Fermer la connexion proprement
                    await websocket.close()
                    
                except asyncio.TimeoutError:
                    # Timeout de la connexion initiale
                    self.logger.warning(f"Timeout lors de la connexion WebSocket à {uri}")
                    await asyncio.sleep(2)
                    continue
            
            except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError) as e:
                # Connexion perdue ou serveur arrêté
                if self.is_connected:
                    self.is_connected = False
                    self.logger.warning(f"Connexion WebSocket perdue: {str(e)}")
                    
                    # Notifier le callback
                    await self._notify_callback({
                        "event": "monitor_disconnected",
                        "host": self.host,
                        "reason": f"connection_lost: {str(e)}",
                        "timestamp": time.time()
                    })
                
                # Attendre avant de réessayer
                if not self._stopping:
                    await asyncio.sleep(2)
            
            except Exception as e:
                self.logger.error(f"Erreur dans le moniteur WebSocket: {str(e)}")
                if not self._stopping:
                    await asyncio.sleep(5)  # Attendre plus longtemps en cas d'erreur grave
    
    async def _notify_callback(self, data: Dict[str, Any]) -> None:
        """
        Notifie le callback avec les données fournies.
        Gère les erreurs pour éviter de casser la boucle principale.
        
        Args:
            data: Données à passer au callback
        """
        try:
            await self.callback(data)
        except Exception as e:
            self.logger.error(f"Erreur dans le callback du moniteur: {str(e)}")