"""
Découverte des serveurs Snapcast sur le réseau via multiple méthodes.
"""
import asyncio
import logging
import socket
import subprocess
from typing import List, Optional

from backend.infrastructure.plugins.snapclient.models import SnapclientServer
from backend.infrastructure.plugins.snapclient.protocol import SnapcastProtocol

class SnapclientDiscovery:
    """
    Détecte les serveurs Snapcast sur le réseau local en utilisant Avahi et scan réseau direct.
    """
    
    def __init__(self):
        """Initialise le découvreur de serveurs Snapcast."""
        self.logger = logging.getLogger("plugin.snapclient.discovery")
        self.protocol = SnapcastProtocol()
        self._has_avahi = self._check_avahi_available()
        
        if not self._has_avahi:
            self.logger.warning("Avahi n'est pas disponible. La découverte automatique sera limitée.")
    
    def _check_avahi_available(self) -> bool:
        """Vérifie si Avahi est disponible sur le système."""
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
        """Découvre les serveurs Snapcast sur le réseau via méthodes combinées."""
        self.logger.info("Démarrage multi-méthode de découverte des serveurs Snapcast")
        
        # Lancer les deux méthodes de découverte en parallèle
        servers = []
        
        # 1. Méthode directe (rapide et fiable)
        direct_servers = await self._scan_network_direct()
        if direct_servers:
            self.logger.info(f"Scan direct: {len(direct_servers)} serveurs trouvés")
            servers.extend(direct_servers)
            for server in direct_servers:
                self.logger.info(f"Serveur (scan direct): {server.name} ({server.host})")
        
        # 2. Méthode Avahi (plus complète mais plus lente)
        if self._has_avahi:
            try:
                # Exécuter avahi-browse avec un délai court
                avahi_servers = await asyncio.wait_for(
                    self._discover_via_avahi(),
                    timeout=3.0  # Timeout court pour éviter de bloquer
                )
                
                # Filtrer pour éviter les doublons
                for server in avahi_servers:
                    if not any(s.host == server.host for s in servers):
                        servers.append(server)
                        self.logger.info(f"Serveur (Avahi): {server.name} ({server.host})")
            except asyncio.TimeoutError:
                self.logger.warning("Timeout lors de la découverte Avahi")
            except Exception as e:
                self.logger.error(f"Erreur lors de la découverte Avahi: {str(e)}")
        
        self.logger.info(f"Découverte terminée, total de {len(servers)} serveurs trouvés")
        return servers
    
    async def _discover_via_avahi(self) -> List[SnapclientServer]:
        """Exécute avahi-browse dans un processus asynchrone."""
        servers = []
        
        try:
            # Créer un processus avahi-browse asynchrone
            process = await asyncio.create_subprocess_exec(
                "avahi-browse", "-r", "_snapcast._tcp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Attendre les résultats avec un timeout
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                self.logger.error(f"Erreur avahi-browse: {stderr.decode()}")
                return []
            
            # Analyser les résultats
            for line in stdout.decode().splitlines():
                # Chercher les lignes d'adresse IPv4
                if "address = [" in line and "]" in line and ":" not in line:
                    ip = line.split("[")[1].split("]")[0].strip()
                    
                    # Ignorer localhost
                    if ip.startswith("127.") or "oakos" in line.lower():
                        continue
                    
                    # Trouver le nom associé à cette IP
                    name = None
                    try:
                        hostname = socket.gethostbyaddr(ip)
                        name = hostname[0].split(".")[0]
                    except:
                        name = f"Snapserver ({ip})"
                    
                    # Créer le serveur
                    if not any(s.host == ip for s in servers):
                        servers.append(SnapclientServer(
                            host=ip,
                            name=name,
                            port=1704
                        ))
            
            return servers
            
        except Exception as e:
            self.logger.error(f"Erreur avahi-browse: {str(e)}")
            return []
    
    async def _scan_network_direct(self) -> List[SnapclientServer]:
        """Scan direct des ports 1704 sur les adresses réseau courantes."""
        servers = []
        
        # Obtenir notre IP locale
        local_ip = self._get_local_ip()
        if not local_ip:
            self.logger.warning("Impossible de déterminer l'IP locale")
            return servers
        
        # Extraire le préfixe réseau
        prefix = '.'.join(local_ip.split('.')[:3])
        self.logger.debug(f"Scan réseau {prefix}.x")
        
        # Adresses à scanner
        targets = []
        
        # Adresses courantes (routeurs, serveurs, etc.)
        for last_octet in [1, 2, 3, 4, 5, 10, 20, 30, 50, 100, 150, 200, 254]:
            ip = f"{prefix}.{last_octet}"
            if ip != local_ip:  # Éviter notre propre IP
                targets.append(ip)
        
        # Scannons ces IPs en parallèle
        tasks = []
        for ip in targets:
            tasks.append(self._check_server(ip))
        
        # Attendre tous les résultats
        results = await asyncio.gather(*tasks)
        
        # Collecter les serveurs fonctionnels
        for server in results:
            if server:
                servers.append(server)
        
        return servers
    
    async def _check_server(self, ip: str) -> Optional[SnapclientServer]:
        """Vérifie si un serveur Snapcast est accessible à cette adresse."""
        try:
            # Connexion TCP rapide pour vérifier que le port est ouvert
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, 1704),
                timeout=0.5
            )
            writer.close()
            await writer.wait_closed()
            
            # Serveur trouvé! Obtenir son nom
            try:
                hostname = socket.gethostbyaddr(ip)
                name = hostname[0].split('.')[0]
            except:
                name = f"Snapserver ({ip})"
            
            return SnapclientServer(
                host=ip,
                name=name,
                port=1704
            )
        except:
            return None
    
    def _get_local_ip(self) -> Optional[str]:
        """Obtient l'adresse IP locale."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))  # Pas de connexion réelle établie
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return None
    
    async def get_server_by_host(self, host: str) -> Optional[SnapclientServer]:
        """Récupère les informations d'un serveur par son adresse IP."""
        try:
            # Vérifier l'accessibilité
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, 1704),
                timeout=1.0
            )
            writer.close()
            await writer.wait_closed()
            
            # Obtenir le nom
            try:
                hostname = socket.gethostbyaddr(host)
                name = hostname[0].split('.')[0]
            except:
                name = f"Snapserver ({host})"
            
            return SnapclientServer(host=host, name=name, port=1704)
        except Exception as e:
            self.logger.warning(f"Impossible de connecter {host}: {e}")
            return None