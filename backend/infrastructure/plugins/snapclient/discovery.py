"""
Découverte des serveurs Snapcast sur le réseau.
"""
import logging
import socket
import asyncio
import ipaddress
import netifaces
from typing import List, Optional

from backend.infrastructure.plugins.snapclient.models import SnapclientServer
from backend.infrastructure.plugins.snapclient.protocol import SnapcastProtocol


class SnapclientDiscovery:
    """
    Détecte les serveurs Snapcast sur le réseau local.
    """
    
    def __init__(self):
        """Initialise le découvreur de serveurs Snapcast."""
        self.logger = logging.getLogger("plugin.snapclient.discovery")
        self.protocol = SnapcastProtocol()
    
    async def discover_servers(self) -> List[SnapclientServer]:
        """
        Découvre les serveurs Snapcast sur le réseau.
        
        Returns:
            List[SnapclientServer]: Liste des serveurs découverts
        """
        self.logger.info("Démarrage de la découverte des serveurs Snapcast")
        
        # Obtenir les adresses IP locales (IPv4 uniquement)
        local_ips = self._get_local_ips()
        
        # Construire la liste des réseaux locaux
        networks = []
        for ip in local_ips:
            try:
                # Pour chaque adresse locale, créer un réseau /24
                network = ipaddress.ip_network(f"{ip}/24", strict=False)
                networks.append(network)
            except ValueError:
                self.logger.warning(f"Impossible de créer un réseau à partir de l'adresse {ip}")
        
        # Scanner les réseaux pour trouver les serveurs Snapcast
        all_servers = []
        for network in networks:
            servers = await self._scan_network(network)
            all_servers.extend(servers)
        
        self.logger.info(f"Découverte terminée, {len(all_servers)} serveurs trouvés")
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