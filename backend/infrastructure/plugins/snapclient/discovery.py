# backend/infrastructure/plugins/snapclient/discovery.py
"""
Découverte des serveurs Snapcast sur le réseau - Version simplifiée.
"""
import asyncio
import logging
import socket
import queue  # Standard queue pour communication inter-thread
from typing import List, Optional, Dict, Any, Set

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncServiceInfo 

from backend.infrastructure.plugins.snapclient.models import SnapclientServer

class SnapclientDiscoveryListener(ServiceListener):
    """
    Détecte les serveurs Snapcast via Zeroconf.
    """
    
    def __init__(self, event_queue, loop):
        """
        Initialise un listener pour la découverte des serveurs Snapcast.
        
        Args:
            event_queue: Queue standard pour les événements de découverte
            loop: Boucle d'événements principale
        """
        self.event_queue = event_queue
        self.loop = loop
        self.logger = logging.getLogger("plugin.snapclient.discovery.listener")
        self.own_hostname = socket.gethostname().lower()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """
        Appelé quand un service est découvert.
        Met l'événement dans une queue synchrone.
        """
        self.logger.debug(f"Service découvert: {name}")
        # Utiliser une queue standard au lieu d'une queue asyncio
        self.event_queue.put(("add", type_, name, zc))

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """
        Appelé quand un service est mis à jour.
        """
        self.logger.debug(f"Service mis à jour: {name}")
        self.event_queue.put(("update", type_, name, zc))

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """
        Appelé quand un service est supprimé.
        """
        self.logger.debug(f"Service supprimé: {name}")
        self.event_queue.put(("remove", type_, name, zc))

class SnapclientDiscovery:
    """
    Détecte les serveurs Snapcast sur le réseau via Zeroconf.
    Version simplifiée.
    """
    
    def __init__(self):
        self.logger = logging.getLogger("plugin.snapclient.discovery")
        self.zeroconf = None
        self.browser = None
        # Utiliser une queue standard au lieu d'une queue asyncio
        self.event_queue = queue.Queue()
        self.event_processor_task = None
        self.discovered_servers: Set[SnapclientServer] = set()
        self.server_callbacks = []
        self.own_hostname = socket.gethostname().lower()
        
    async def start(self):
        """Démarre la découverte des serveurs Snapcast."""
        if self.zeroconf:
            return True
            
        self.logger.info("Démarrage de la découverte Zeroconf des serveurs Snapcast")
        
        try:
            # Stocker la boucle d'événements principale
            loop = asyncio.get_running_loop()
            
            # Créer l'instance Zeroconf dans une tâche séparée pour éviter le blocage
            self.zeroconf = Zeroconf()
            self.listener = SnapclientDiscoveryListener(self.event_queue, loop)
            self.browser = ServiceBrowser(
                self.zeroconf, 
                "_snapcast._tcp.local.",
                self.listener
            )
            
            # Démarrer le processeur d'événements
            if not self.event_processor_task or self.event_processor_task.done():
                self.event_processor_task = asyncio.create_task(self._process_events())
                
            self.logger.info("Découverte Zeroconf démarrée avec succès")
            return True
            
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage de la découverte Zeroconf: {str(e)}")
            await self.stop()
            return False
    
    async def stop(self):
        """Arrête la découverte des serveurs Snapcast."""
        self.logger.info("Arrêt de la découverte Zeroconf")
        
        if self.event_processor_task and not self.event_processor_task.done():
            self.event_processor_task.cancel()
            try:
                await self.event_processor_task
            except asyncio.CancelledError:
                pass
            self.event_processor_task = None
            
        if self.browser:
            self.browser.cancel()
            self.browser = None
            
        if self.zeroconf:
            self.zeroconf.close()
            self.zeroconf = None
            
        self.logger.info("Découverte Zeroconf arrêtée")
    
    async def _process_events(self):
        """
        Traite les événements de la queue standard en arrière-plan.
        Cette fonction s'exécute dans la boucle d'événements asyncio.
        """
        try:
            while True:
                # Vérifier la queue de manière non bloquante
                try:
                    while not self.event_queue.empty():
                        event_type, service_type, name, zc = self.event_queue.get_nowait()
                        try:
                            if event_type in ("add", "update"):
                                # Utiliser AsyncServiceInfo au lieu de get_service_info
                                service_info = AsyncServiceInfo(service_type, name)
                                await service_info.async_request(zc, timeout=2000)
                                
                                # Vérifier si on a reçu des informations
                                if not service_info.addresses:
                                    continue
                                    
                                server = self._service_info_to_server(service_info)
                                if server:
                                    await self._process_server(server, event_type == "add")
                                    
                            elif event_type == "remove":
                                # Traiter la suppression du serveur
                                hostname = name.split(".")[0].lower()
                                servers_to_remove = [s for s in self.discovered_servers 
                                                   if s.name.lower() == hostname]
                                
                                for server in servers_to_remove:
                                    self.discovered_servers.discard(server)
                                    self.logger.info(f"Serveur supprimé: {server.name} ({server.host})")
                                    for callback in self.server_callbacks:
                                        try:
                                            await callback("removed", server)
                                        except Exception as e:
                                            self.logger.error(f"Erreur dans callback: {e}")
                            
                        except Exception as e:
                            self.logger.error(f"Erreur lors du traitement d'un événement: {str(e)}")
                        finally:
                            self.event_queue.task_done()
                except queue.Empty:
                    pass
                
                # Attendre avant de vérifier à nouveau
                await asyncio.sleep(0.1)
                    
        except asyncio.CancelledError:
            self.logger.debug("Processeur d'événements arrêté")
        except Exception as e:
            self.logger.error(f"Erreur dans le processeur d'événements: {str(e)}")
    
    def _service_info_to_server(self, info: AsyncServiceInfo) -> Optional[SnapclientServer]:
        """
        Convertit les informations de service en objet SnapclientServer.
        
        Args:
            info: Informations sur le service Snapcast
            
        Returns:
            SnapclientServer ou None si le serveur est invalide
        """
        try:
            # Extraire les adresses
            addresses = info.parsed_addresses()
            if not addresses:
                return None
                
            # Utiliser la première adresse IPv4 disponible
            host = next((addr for addr in addresses 
                        if not addr.startswith('fe80:') and ':' not in addr), 
                        addresses[0])
                
            # Extraire le nom d'hôte
            name = info.server.split('.')[0] if info.server else info.name.split('.')[0]
            
            # Extraire le port
            port = info.port
            
            # Vérifier si c'est nous-même
            if name.lower() == self.own_hostname:
                self.logger.debug(f"Ignoré serveur local: {name}")
                return None
                
            # Ignorer les entrées oakos (c'est nous-même)
            if 'oakos' in name.lower():
                return None
                
            return SnapclientServer(host=host, name=name, port=port)
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la conversion des informations de service: {str(e)}")
            return None
    
    async def _process_server(self, server: SnapclientServer, is_new: bool):
        """
        Traite un serveur découvert ou mis à jour.
        
        Args:
            server: Le serveur Snapcast
            is_new: True si c'est un nouveau serveur, False si c'est une mise à jour
        """
        # Vérifier si le serveur existe déjà
        existing = next((s for s in self.discovered_servers if s.host == server.host), None)
        
        if existing:
            # Mettre à jour le serveur existant si nécessaire
            if (existing.name != server.name or existing.port != server.port):
                self.discovered_servers.discard(existing)
                self.discovered_servers.add(server)
                self.logger.info(f"Serveur mis à jour: {server.name} ({server.host})")
                
                # Appeler les callbacks
                for callback in self.server_callbacks:
                    asyncio.create_task(callback("updated", server))
        else:
            # Ajouter le nouveau serveur
            self.discovered_servers.add(server)
            self.logger.info(f"Nouveau serveur découvert: {server.name} ({server.host})")
            
            # Appeler les callbacks
            for callback in self.server_callbacks:
                asyncio.create_task(callback("added", server))
    
    def register_callback(self, callback):
        """
        Enregistre un callback qui sera appelé quand un serveur est découvert/mis à jour/supprimé.
        Le callback doit être une coroutine prenant (event_type, server) comme arguments.
        
        Args:
            callback: Coroutine à appeler
        """
        if callback not in self.server_callbacks:
            self.server_callbacks.append(callback)
    
    def unregister_callback(self, callback):
        """
        Supprime un callback précédemment enregistré.
        
        Args:
            callback: Callback à supprimer
        """
        if callback in self.server_callbacks:
            self.server_callbacks.remove(callback)
    
    async def discover_servers(self) -> List[SnapclientServer]:
        """
        Découvre les serveurs Snapcast via Zeroconf.
        
        Returns:
            List[SnapclientServer]: Liste des serveurs découverts
        """
        # Démarrer la découverte si ce n'est pas déjà fait
        if not self.zeroconf:
            await self.start()
            
            # Attendre un peu pour laisser la découverte initiale se faire
            await asyncio.sleep(0.5)
        
        # Retourner la liste actuelle des serveurs
        return list(self.discovered_servers)