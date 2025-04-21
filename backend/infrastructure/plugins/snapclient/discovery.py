"""
Découverte des serveurs Snapcast sur le réseau - Version optimisée.
"""
import asyncio
import logging
import socket
import queue
from typing import List, Optional, Dict, Any, Set

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncServiceInfo

from backend.infrastructure.plugins.snapclient.models import SnapclientServer

class SnapclientDiscoveryListener(ServiceListener):
    """Détecte les serveurs Snapcast via Zeroconf."""

    def __init__(self, event_queue, loop):
        self.event_queue = event_queue
        self.loop = loop
        self.logger = logging.getLogger("plugin.snapclient.discovery.listener")
        self.own_hostname = socket.gethostname().lower()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.logger.debug(f"Service découvert: {name}")
        self.event_queue.put(("add", type_, name, zc))

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.logger.debug(f"Service mis à jour: {name}")
        self.event_queue.put(("update", type_, name, zc))

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.logger.debug(f"Service supprimé: {name}")
        self.event_queue.put(("remove", type_, name, zc))

class SnapclientDiscovery:
    """Détecte les serveurs Snapcast sur le réseau via Zeroconf."""

    def __init__(self):
        self.logger = logging.getLogger("plugin.snapclient.discovery")
        self.zeroconf = None
        self.browser = None
        self.event_queue = queue.Queue()
        self.event_processor_task = None
        self.discovered_servers: Set[SnapclientServer] = set()
        self.server_callbacks = []
        self.own_hostname = socket.gethostname().lower()

    async def start(self):
        """Démarre la découverte des serveurs Snapcast."""
        if self.zeroconf:
            return True

        self.logger.info("Démarrage de la découverte Zeroconf")
        try:
            loop = asyncio.get_running_loop()
            self.zeroconf = Zeroconf()
            self.listener = SnapclientDiscoveryListener(self.event_queue, loop)
            self.browser = ServiceBrowser(
                self.zeroconf,
                "_snapcast._tcp.local.",
                self.listener
            )
            
            if not self.event_processor_task or self.event_processor_task.done():
                self.event_processor_task = asyncio.create_task(self._process_events())
                
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage Zeroconf: {str(e)}")
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
            try: self.browser.cancel()
            except Exception: pass
            self.browser = None

        if self.zeroconf:
            try: self.zeroconf.close()
            except Exception: pass
            self.zeroconf = None

        while not self.event_queue.empty():
            try: self.event_queue.get_nowait()
            except queue.Empty: break
            self.event_queue.task_done()

    async def _process_events(self):
        """Traite les événements de la queue en arrière-plan."""
        try:
            while True:
                try:
                    while not self.event_queue.empty():
                        event_type, service_type, name, zc = self.event_queue.get_nowait()
                        
                        if event_type in ("add", "update"):
                            service_info = AsyncServiceInfo(service_type, name)
                            await service_info.async_request(zc, timeout=2000)
                            
                            if not service_info.addresses or not service_info.server:
                                continue
                                
                            server = self._service_info_to_server(service_info)
                            if server:
                                await self._process_server(server, event_type)
                                
                        elif event_type == "remove":
                            hostname = name.split(".")[0].lower()
                            servers_to_remove = [s for s in self.discovered_servers
                                              if s.name.lower() == hostname]
                            
                            for server in servers_to_remove:
                                self.discovered_servers.discard(server)
                                for callback in self.server_callbacks:
                                    asyncio.create_task(callback("removed", server))
                                    
                        self.event_queue.task_done()
                        
                except queue.Empty:
                    pass
                    
                await asyncio.sleep(0.1)
                
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Erreur fatale dans le processeur d'événements: {str(e)}")

    def _service_info_to_server(self, info: AsyncServiceInfo) -> Optional[SnapclientServer]:
        """Convertit les informations de service en objet SnapclientServer."""
        try:
            addresses = info.parsed_addresses()
            if not addresses:
                return None

            host = next((addr for addr in addresses if ':' not in addr and not addr.startswith('169.254.')), None)
            if not host:
                host = addresses[0]

            name = info.server.split('.')[0] if info.server else info.name.split('.')[0]
            port = info.port

            # Filtrer les serveurs locaux - CETTE LOGIQUE DOIT RESTER
            if name.lower() == self.own_hostname:
                return None
            if 'oakos' in name.lower():
                return None

            return SnapclientServer(host=host, name=name, port=port)
        except Exception as e:
            self.logger.error(f"Erreur conversion service: {str(e)}")
            return None

    async def _process_server(self, server: SnapclientServer, event_type: str):
        """Traite un serveur découvert ou mis à jour."""
        existing = next((s for s in self.discovered_servers if s.host == server.host), None)
        callback_type = None
        
        if existing:
            if existing != server:
                self.discovered_servers.discard(existing)
                self.discovered_servers.add(server)
                callback_type = "updated"
            else:
                callback_type = "updated"
        else:
            self.discovered_servers.add(server)
            callback_type = "added"
            
        for callback in self.server_callbacks:
            asyncio.create_task(callback(callback_type, server))

    def register_callback(self, callback):
        """Enregistre un callback pour les événements de serveur."""
        if callback not in self.server_callbacks:
            self.server_callbacks.append(callback)

    def unregister_callback(self, callback):
        """Supprime un callback précédemment enregistré."""
        if callback in self.server_callbacks:
            self.server_callbacks.remove(callback)

    async def discover_servers(self) -> List[SnapclientServer]:
        """Découvre les serveurs Snapcast via Zeroconf."""
        if not self.zeroconf:
            await self.start()
            await asyncio.sleep(0.5)
        return list(self.discovered_servers)