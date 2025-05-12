"""
Découverte des serveurs Snapcast sur le réseau - Version concise.
"""
import asyncio
import logging
import socket
from typing import List, Callable, Set, Dict, Any

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncServiceInfo

from backend.infrastructure.plugins.snapclient.models import SnapclientServer

class SnapclientDiscoveryListener(ServiceListener):
    """Détecte les serveurs Snapcast via Zeroconf."""
    
    def __init__(self, callback):
        self.callback = callback
        self.logger = logging.getLogger("plugin.snapclient.discovery")
        self.own_hostname = socket.gethostname().lower()
    
    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        asyncio.create_task(self._process_service(zc, type_, name, "added"))
    
    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        asyncio.create_task(self._process_service(zc, type_, name, "updated"))
    
    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.logger.debug(f"Service supprimé: {name}")
        asyncio.create_task(self._process_service(zc, type_, name, "removed"))
    
    async def _process_service(self, zc: Zeroconf, type_: str, name: str, event: str) -> None:
        """Traite un service découvert"""
        if event == "removed":
            # Pour la suppression, on extrait juste le hostname
            hostname = name.split(".")[0].lower()
            await self.callback(event, SnapclientServer(host="", name=hostname, port=0))
            return
            
        # Pour l'ajout ou la mise à jour, récupérer les infos complètes
        service_info = AsyncServiceInfo(type_, name)
        await service_info.async_request(zc, timeout=2000)
        
        if not service_info.addresses:
            return
            
        # Extraire les informations
        addresses = service_info.parsed_addresses()
        host = next((addr for addr in addresses if ':' not in addr and not addr.startswith('169.254.')),
                    addresses[0] if addresses else None)
        
        if not host:
            return
            
        hostname = service_info.server.split('.')[0] if service_info.server else name.split('.')[0]
        
        # Filtrer les serveurs locaux
        if hostname.lower() == self.own_hostname or 'oakos' in hostname.lower():
            return
            
        server = SnapclientServer(host=host, name=hostname, port=service_info.port or 1704)
        await self.callback(event, server)

class SnapclientDiscovery:
    """Détecte les serveurs Snapcast sur le réseau via Zeroconf."""

    def __init__(self):
        self.logger = logging.getLogger("plugin.snapclient.discovery")
        self.zeroconf = None
        self.browser = None
        self.discovered_servers: Set[SnapclientServer] = set()
        self.callbacks = []
    
    async def start(self) -> bool:
        """Démarre la découverte des serveurs Snapcast."""
        if self.zeroconf:
            return True

        try:
            self.zeroconf = Zeroconf()
            self.listener = SnapclientDiscoveryListener(self._on_server_event)
            self.browser = ServiceBrowser(
                self.zeroconf,
                "_snapcast._tcp.local.",
                self.listener
            )
            return True
        except Exception as e:
            self.logger.error(f"Erreur démarrage Zeroconf: {e}")
            await self.stop()
            return False

    async def stop(self) -> bool:
        """Arrête la découverte des serveurs Snapcast."""
        try:
            if self.browser:
                self.browser.cancel()
                self.browser = None

            if self.zeroconf:
                self.zeroconf.close()
                self.zeroconf = None
                
            self.discovered_servers.clear()
            return True
        except Exception as e:
            self.logger.error(f"Erreur arrêt Zeroconf: {e}")
            return False
    
    async def _on_server_event(self, event_type: str, server: SnapclientServer) -> None:
        """Traite un événement de serveur"""
        if event_type == "removed":
            servers_to_remove = [s for s in self.discovered_servers if s.name.lower() == server.name.lower()]
            for s in servers_to_remove:
                self.discovered_servers.discard(s)
                for callback in self.callbacks:
                    await callback("removed", s)
        else:
            # Ajout ou mise à jour
            existing = next((s for s in self.discovered_servers if s.host == server.host), None)
            
            if existing:
                if existing != server:
                    self.discovered_servers.discard(existing)
                    self.discovered_servers.add(server)
                    for callback in self.callbacks:
                        await callback("updated", server)
            else:
                self.discovered_servers.add(server)
                for callback in self.callbacks:
                    await callback("added", server)

    def register_callback(self, callback):
        """Enregistre un callback pour les événements de serveur."""
        if callback not in self.callbacks:
            self.callbacks.append(callback)

    async def discover_servers(self) -> List[SnapclientServer]:
        """Découvre les serveurs Snapcast via Zeroconf."""
        if not self.zeroconf:
            await self.start()
            await asyncio.sleep(0.5)
        return list(self.discovered_servers)