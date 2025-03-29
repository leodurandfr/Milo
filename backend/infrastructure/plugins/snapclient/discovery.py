"""
Découverte des serveurs Snapcast sur le réseau.
"""
import asyncio
import logging
import socket
import ipaddress
import netifaces
import subprocess
from typing import List, Optional

from backend.infrastructure.plugins.snapclient.models import SnapclientServer
from backend.infrastructure.plugins.snapclient.protocol import SnapcastProtocol

# Remplacer le contenu de la classe SnapclientDiscovery par celui-ci
class SnapclientDiscovery:
    """
    Détecte les serveurs Snapcast sur le réseau local en utilisant mDNS/Avahi si disponible,
    ou en revenant à un scan réseau si nécessaire.
    """
    
    def __init__(self):
        """Initialise le découvreur de serveurs Snapcast."""
        self.logger = logging.getLogger("plugin.snapclient.discovery")
        self.protocol = SnapcastProtocol()
        self._has_avahi = self._check_avahi_available()
    
    def _check_avahi_available(self) -> bool:
        """
        Vérifie si Avahi/mDNS est disponible sur le système.
        
        Returns:
            bool: True si Avahi est disponible, False sinon
        """
        try:
            result = subprocess.run(
                ["which", "avahi-browse"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            return result.returncode == 0
        except Exception as e:
            self.logger.warning(f"Erreur lors de la vérification d'Avahi: {str(e)}")
            return False
    
    async def discover_servers(self) -> List[SnapclientServer]:
        """
        Découvre les serveurs Snapcast sur le réseau.
        Utilise mDNS/Avahi si disponible, sinon revient au scan réseau.
        
        Returns:
            List[SnapclientServer]: Liste des serveurs découverts
        """
        self.logger.info("Démarrage de la découverte des serveurs Snapcast")
        
        servers = []
        
        # Essayer d'abord avec mDNS/Avahi si disponible
        if self._has_avahi:
            self.logger.debug("Tentative de découverte via mDNS/Avahi")
            try:
                avahi_servers = await self._discover_via_avahi()
                if avahi_servers:
                    self.logger.info(f"Découverte mDNS/Avahi réussie: {len(avahi_servers)} serveurs trouvés")
                    servers.extend(avahi_servers)
            except Exception as e:
                self.logger.warning(f"Échec de la découverte mDNS/Avahi: {str(e)}")
        
        # Si aucun serveur n'a été trouvé via mDNS ou si mDNS n'est pas disponible,
        # utiliser le scan réseau traditionnel
        if not servers:
            self.logger.debug("Utilisation du scan réseau traditionnel")
            try:
                scan_servers = await self._discover_via_network_scan()
                servers.extend(scan_servers)
            except Exception as e:
                self.logger.error(f"Échec du scan réseau: {str(e)}")
        
        self.logger.info(f"Découverte terminée, {len(servers)} serveurs trouvés")
        return servers
    
    async def _discover_via_avahi(self) -> List[SnapclientServer]:
        """
        Découvre les serveurs Snapcast en utilisant mDNS/Avahi.
        
        Returns:
            List[SnapclientServer]: Liste des serveurs découverts via mDNS
        """
        servers = []
        
        try:
            # Exécuter avahi-browse pour trouver les services Snapcast
            process = await asyncio.create_subprocess_exec(
                "avahi-browse", "-ptr", "_snapcast._tcp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                self.logger.warning(f"avahi-browse a échoué: {stderr.decode()}")
                return []
            
            # Analyser la sortie pour extraire les informations des serveurs
            lines = stdout.decode().splitlines()
            current_service = {}
            
            for line in lines:
                if line.startswith("=") and "IPv4" in line and "_snapcast._tcp" in line:
                    parts = line.split(";")
                    if len(parts) >= 8:
                        service_name = parts[3].strip()
                        current_service = {"name": service_name}
                
                elif line.startswith("   address") and current_service:
                    addr_parts = line.split("[")
                    if len(addr_parts) >= 2:
                        ip_addr = addr_parts[1].strip("]").strip()
                        current_service["host"] = ip_addr
                
                elif line.startswith("   port") and current_service:
                    port_parts = line.split("[")
                    if len(port_parts) >= 2:
                        port = int(port_parts[1].strip("]").strip())
                        current_service["port"] = port
                
                # Si nous avons toutes les informations nécessaires, créer un serveur
                if current_service and "name" in current_service and "host" in current_service:
                    port = current_service.get("port", 1704)
                    server = SnapclientServer(
                        host=current_service["host"],
                        name=current_service["name"],
                        port=port
                    )
                    servers.append(server)
                    current_service = {}
            
            return servers
            
        except Exception as e:
            self.logger.error(f"Erreur lors de la découverte via Avahi: {str(e)}")
            return []
    
    async def _discover_via_network_scan(self) -> List[SnapclientServer]:
        """
        Découvre les serveurs Snapcast en scannant le réseau.
        Méthode de secours si mDNS/Avahi n'est pas disponible.
        
        Returns:
            List[SnapclientServer]: Liste des serveurs découverts via le scan réseau
        """
        # Réutiliser l'implémentation existante pour le scan réseau
        local_ips = self._get_local_ips()
        networks = []
        
        for ip in local_ips:
            try:
                network = ipaddress.ip_network(f"{ip}/24", strict=False)
                networks.append(network)
            except ValueError:
                self.logger.warning(f"Impossible de créer un réseau à partir de l'adresse {ip}")
        
        all_servers = []
        for network in networks:
            servers = await self._scan_network(network)
            all_servers.extend(servers)
        
        return all_servers
    
    def _get_local_ips(self) -> List[str]:
        """
        Récupère les adresses IP locales (IPv4 uniquement).
        
        Returns:
            List[str]: Liste des adresses IP locales
        """
        local_ips = []
        
        try:
            # Obtenir les interfaces réseau
            interfaces = netifaces.interfaces()
            
            for interface in interfaces:
                try:
                    # Obtenir les adresses IPv4
                    addrs = netifaces.ifaddresses(interface).get(netifaces.AF_INET, [])
                    
                    for addr in addrs:
                        ip = addr.get('addr')
                        if ip and not ip.startswith('127.'):
                            local_ips.append(ip)
                except Exception as e:
                    self.logger.warning(f"Erreur lors de la récupération des adresses de l'interface {interface}: {str(e)}")
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des interfaces réseau: {str(e)}")
            # Fallback en cas d'erreur
            try:
                hostname = socket.gethostname()
                ip = socket.gethostbyname(hostname)
                if ip and not ip.startswith('127.'):
                    local_ips.append(ip)
            except Exception as e2:
                self.logger.error(f"Erreur lors de la récupération de l'IP via le nom d'hôte: {str(e2)}")
        
        self.logger.debug(f"Adresses IP locales: {local_ips}")
        return local_ips
    
    async def _scan_network(self, network: ipaddress.IPv4Network) -> List[SnapclientServer]:
        """
        Scanne un réseau à la recherche de serveurs Snapcast.
        
        Args:
            network: Réseau à scanner
            
        Returns:
            List[SnapclientServer]: Liste des serveurs découverts
        """
        self.logger.debug(f"Scan du réseau {network}")
        
        # Vérifier qu'il s'agit bien d'un réseau IPv4
        if not isinstance(network, ipaddress.IPv4Network):
            self.logger.warning(f"Réseau non supporté: {network}")
            return []
        
        servers = []
        tasks = []
        
        # Récupérer les adresses IP locales pour les exclure
        local_ips = self._get_local_ips()
        
        # Limiter le scan aux 254 premières adresses du réseau
        hosts = list(network.hosts())[:254]
        
        # Scanner chaque hôte du réseau, en excluant les adresses locales
        for host in hosts:
            host_str = str(host)
            # Ne pas scanner les adresses IP locales du Raspberry Pi
            if host_str not in local_ips:
                task = asyncio.create_task(self._check_host(host_str))
                tasks.append(task)
        
        # Attendre tous les résultats avec un timeout global
        try:
            # Utiliser wait_for pour limiter le temps total de scan
            results = await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=30.0  # 30 secondes max pour scanner le réseau
            )
            
            # Traiter les résultats
            for result in results:
                if isinstance(result, Exception):
                    self.logger.debug(f"Erreur lors du scan d'un hôte: {str(result)}")
                elif result:
                    servers.append(result)
        except asyncio.TimeoutError:
            self.logger.warning(f"Timeout lors du scan du réseau {network}")
            # Annuler toutes les tâches restantes
            for task in tasks:
                if not task.done():
                    task.cancel()
        
        return servers
    
    async def _check_host(self, host: str) -> Optional[SnapclientServer]:
        """
        Vérifie si un hôte exécute un serveur Snapcast.
        
        Args:
            host: Adresse IP de l'hôte à vérifier
            
        Returns:
            Optional[SnapclientServer]: Serveur Snapcast trouvé, ou None
        """
        # Port Snapcast standard
        port = 1704
        
        try:
            # Tenter de se connecter au port Snapcast
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=0.5  # 500ms de timeout par hôte
            )
            
            # Fermer la connexion
            writer.close()
            await writer.wait_closed()
            
            # Utiliser le protocole pour obtenir le nom réel du serveur
            server_name = await self.protocol.get_server_name(host)
            
            # Créer un serveur avec le nom obtenu
            server = SnapclientServer(host=host, name=server_name, port=port)
            self.logger.info(f"Serveur Snapcast trouvé: {server_name} ({host})")
            
            return server
        except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
            # Pas de serveur Snapcast sur cet hôte
            return None
        except Exception as e:
            self.logger.debug(f"Erreur lors de la vérification de l'hôte {host}: {str(e)}")
            return None