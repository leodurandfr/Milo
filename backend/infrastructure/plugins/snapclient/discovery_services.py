"""
Service de découverte de serveurs Snapcast sur le réseau.
Utilise mDNS/Avahi pour détecter automatiquement les serveurs.
"""
import asyncio
import logging
import socket
import subprocess
import re
from typing import List, Dict, Any, Optional, Callable, Set
import time

class DiscoveryService:
    """Service pour découvrir les serveurs Snapcast sur le réseau via mDNS/Avahi"""
    
    def __init__(self, discovery_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.logger = logging.getLogger("snapclient.discovery")
        self.discovery_callback = discovery_callback
        self.discovery_task = None
        self.known_servers: Dict[str, Dict[str, Any]] = {}
        self.last_scan_time = 0
        self.min_scan_interval = 30  # Minimum de 30 secondes entre les scans complets
        self.scanning = False
        self.last_known_active_server = None  # Serveur précédemment actif
        
        self.local_addresses = self._get_local_addresses()
        self.logger.info(f"Adresses locales qui seront ignorées: {self.local_addresses}")

    def _get_local_addresses(self) -> List[str]:
        """
        Récupère toutes les adresses IP locales du Raspberry Pi (IPv4 et IPv6).
        
        Returns:
            List[str]: Liste des adresses IP locales à ignorer
        """
        local_addresses = ["127.0.0.1", "localhost", "::1"]
        
        try:
            # Obtenir toutes les interfaces réseau
            import netifaces
            for interface in netifaces.interfaces():
                # Récupérer les adresses IPv4
                ipv4_addrs = netifaces.ifaddresses(interface).get(netifaces.AF_INET, [])
                for addr in ipv4_addrs:
                    ip = addr.get('addr')
                    if ip and ip not in local_addresses:
                        local_addresses.append(ip)
                
                # Récupérer les adresses IPv6
                ipv6_addrs = netifaces.ifaddresses(interface).get(netifaces.AF_INET6, [])
                for addr in ipv6_addrs:
                    ip = addr.get('addr')
                    if ip:
                        # Supprimer le suffixe d'interface pour IPv6 si présent
                        if '%' in ip:
                            ip = ip.split('%')[0]
                        if ip not in local_addresses:
                            local_addresses.append(ip)
            
            # Ajouter une entrée de log pour mieux déboguer
            self.logger.info(f"Adresses locales identifiées à ignorer: {local_addresses}")
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des adresses locales: {str(e)}")
            
        return local_addresses
    
    async def start(self) -> None:
        """Démarre le service de découverte en arrière-plan"""
        if self.discovery_task is None or self.discovery_task.done():
            self.discovery_task = asyncio.create_task(self._discovery_loop())
            self.logger.info("Service de découverte Snapcast démarré")
            
    async def stop(self) -> None:
        """Arrête le service de découverte"""
        if self.discovery_task and not self.discovery_task.done():
            self.discovery_task.cancel()
            try:
                await self.discovery_task
            except asyncio.CancelledError:
                pass
            self.discovery_task = None
            self.logger.info("Service de découverte Snapcast arrêté")
            
    def get_known_servers(self) -> List[Dict[str, Any]]:
        """
        Retourne la liste des serveurs connus.
        
        Returns:
            List[Dict[str, Any]]: Liste des serveurs découverts
        """
        return list(self.known_servers.values())
    
    def set_last_known_active_server(self, host: str) -> None:
        """
        Définit le dernier serveur actif pour optimiser les recherches futures.
        
        Args:
            host: Hôte du dernier serveur actif
        """
        self.last_known_active_server = host
        self.logger.info(f"Dernier serveur actif défini: {host}")
    
    async def _test_single_server(self, host, port=1704, timeout=0.5) -> bool:
        """Test la connexion à un serveur spécifique."""
        try:
            future = asyncio.open_connection(host, port)
            reader, writer = await asyncio.wait_for(future, timeout=timeout)
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False
    
    async def force_scan(self) -> List[Dict[str, Any]]:
        """
        Force un scan immédiat du réseau pour découvrir les serveurs.
        Inclut une recherche prioritaire pour le dernier serveur actif.
        
        Returns:
            List[Dict[str, Any]]: Liste des serveurs découverts
        """
        if self.scanning:
            self.logger.debug("Un scan est déjà en cours, attente...")
            while self.scanning:
                await asyncio.sleep(0.5)
            return list(self.known_servers.values())
        
        self.scanning = True
        try:
            # Rechercher des derniers serveurs connus spécifiquement
            # Cette optimisation permet de retrouver plus rapidement un serveur qui vient de redémarrer
            if self.last_known_active_server:
                host = self.last_known_active_server
                self.logger.info(f"Recherche prioritaire du dernier serveur actif: {host}")
                
                # Tester une connexion directe à ce serveur spécifique
                try:
                    result = await self._test_single_server(host)
                    if result:
                        self.logger.info(f"Dernier serveur actif retrouvé: {host}")
                        # Mise à jour si le serveur est retrouvé
                        if host not in self.known_servers:
                            self.known_servers[host] = {
                                "host": host, 
                                "first_seen": time.time(), 
                                "last_seen": time.time()
                            }
                            # Notifier via le callback si défini
                            if self.discovery_callback:
                                self.discovery_callback(self.known_servers[host])
                        else:
                            self.known_servers[host]["last_seen"] = time.time()
                except Exception as e:
                    self.logger.debug(f"Erreur lors de la recherche du dernier serveur: {str(e)}")
            
            # Poursuivre avec le scan standard
            await self._scan_network()
            self.last_scan_time = time.time()
            return list(self.known_servers.values())
        finally:
            self.scanning = False
    
    async def _discovery_loop(self) -> None:
        """Boucle principale du service de découverte"""
        try:
            while True:
                try:
                    # Ne pas scanner trop fréquemment
                    if time.time() - self.last_scan_time >= self.min_scan_interval:
                        self.scanning = True
                        try:
                            await self._scan_network()
                            self.last_scan_time = time.time()
                        finally:
                            self.scanning = False
                    
                    # Vérifier si les serveurs sont toujours accessibles
                    await self._check_server_availability()
                        
                except Exception as e:
                    self.logger.error(f"Erreur lors de la découverte des serveurs: {str(e)}")
                
                await asyncio.sleep(10)  # Vérifier toutes les 10 secondes
                
        except asyncio.CancelledError:
            self.logger.debug("Boucle de découverte annulée")
            raise
            
    async def _scan_network(self) -> None:
        """
        Scanne le réseau pour trouver les serveurs Snapcast via mDNS/Avahi.
        Méthode principale qui utilise plusieurs approches pour maximiser les chances de détection.
        """
        self.logger.debug("Démarrage du scan réseau pour les serveurs Snapcast...")
        
        # Combiner les résultats de différentes méthodes
        servers: Set[str] = set()
        
        # Méthode 1: Utiliser avahi-browse si disponible
        avahi_servers = await self._scan_with_avahi()
        servers.update(avahi_servers)
        
        # Méthode 2: Utiliser des connexions directes aux ports connus
        direct_servers = await self._scan_direct()
        servers.update(direct_servers)
        
        # Traiter les résultats
        current_servers = set(self.known_servers.keys())
        
        # Identifier les nouveaux serveurs
        new_servers = servers - current_servers
        for server in new_servers:
            server_info = {"host": server, "first_seen": time.time(), "last_seen": time.time()}
            self.known_servers[server] = server_info
            self.logger.info(f"Nouveau serveur Snapcast découvert: {server}")
            
            # Notifier via le callback si défini
            if self.discovery_callback:
                self.discovery_callback(server_info)
        
        # Mettre à jour les serveurs existants
        common_servers = servers.intersection(current_servers)
        for server in common_servers:
            self.known_servers[server]["last_seen"] = time.time()
        
        # Noter les serveurs qui n'ont pas été vus dans ce scan
        missing_servers = current_servers - servers
        for server in missing_servers:
            # Ne pas supprimer immédiatement, juste noter qu'ils n'ont pas été vus
            self.logger.debug(f"Serveur non vu dans ce scan: {server}")
    
    async def _scan_with_avahi(self) -> Set[str]:
        """
        Scanne le réseau en utilisant avahi-browse pour mDNS.
        Filtre les adresses locales.
        
        Returns:
            Set[str]: Ensemble d'adresses de serveurs découverts
        """
        servers = set()
        try:
            # Vérifier si avahi-browse est disponible
            try:
                process = await asyncio.create_subprocess_exec(
                    "which", "avahi-browse",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                if process.returncode != 0:
                    self.logger.warning("avahi-browse n'est pas disponible, impossible d'utiliser mDNS")
                    return servers
            except:
                self.logger.warning("Impossible de vérifier la disponibilité d'avahi-browse")
                return servers
                
            # Utiliser avahi-browse pour trouver les serveurs Snapcast
            process = await asyncio.create_subprocess_exec(
                "avahi-browse", "-rpt", "_snapcast._tcp",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            
            if process.returncode != 0:
                self.logger.warning(f"Erreur lors de l'exécution d'avahi-browse: {stderr.decode()}")
                return servers
                
            # Analyser la sortie pour extraire les adresses
            output = stdout.decode()
            for line in output.splitlines():
                if "=" in line:  # Les lignes avec informations contiennent "="
                    parts = line.split(";")
                    if len(parts) >= 8:
                        host = parts[7].strip()
                        if host:
                            servers.add(host)
            
            # Filtrer les adresses locales
            filtered_servers = set()
            for host in servers:
                # Ignorer les adresses locales IPv4 et IPv6
                should_ignore = False
                
                for local_addr in self.local_addresses:
                    # Comparaison directe pour IPv4
                    if host == local_addr:
                        should_ignore = True
                        break
                    
                    # Pour IPv6, comparer les parties significatives
                    if ':' in host and ':' in local_addr:
                        # Normaliser les adresses IPv6 pour comparaison
                        host_parts = host.split('%')[0].lower()  # Retirer les suffixes d'interface
                        local_parts = local_addr.split('%')[0].lower()
                        if host_parts == local_parts:
                            should_ignore = True
                            break
                
                if not should_ignore:
                    filtered_servers.add(host)
                else:
                    self.logger.info(f"Ignoré serveur local: {host}")
            
            self.logger.debug(f"Serveurs découverts via avahi-browse (après filtrage): {filtered_servers}")
            return filtered_servers
            
        except Exception as e:
            self.logger.error(f"Erreur lors du scan avec avahi: {str(e)}")
            
        return servers
            
    async def _scan_direct(self) -> Set[str]:
        """
        Scanne le réseau en essayant de se connecter directement aux ports connus de Snapcast.
        Utilisé comme méthode de secours si avahi-browse ne trouve rien.
        Filtre les adresses locales.
        
        Returns:
            Set[str]: Ensemble d'adresses de serveurs découverts
        """
        servers = set()
        try:
            # Obtenir l'adresse IP locale pour déterminer le sous-réseau
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))  # Se connecter à une adresse externe (Google DNS)
            local_ip = s.getsockname()[0]
            s.close()
            
            # Déterminer le sous-réseau (ex: 192.168.1)
            subnet = '.'.join(local_ip.split('.')[:3])
            
            # Fonction pour tester une connexion à un hôte:port
            async def test_connection(host, port=1704, timeout=0.5):
                try:
                    future = asyncio.open_connection(host, port)
                    reader, writer = await asyncio.wait_for(future, timeout=timeout)
                    writer.close()
                    await writer.wait_closed()
                    return True
                except:
                    return False
            
            # Prioriser le test du dernier serveur actif
            if self.last_known_active_server:
                host = self.last_known_active_server
                if await test_connection(host):
                    servers.add(host)
                    self.logger.info(f"Dernier serveur actif {host} trouvé disponible")
            
            # Scan parallèle limité pour éviter de surcharger le réseau
            tasks = []
            for i in range(1, 255):
                host = f"{subnet}.{i}"
                
                # Éviter de tester à nouveau le dernier serveur actif
                if host == self.last_known_active_server:
                    continue
                    
                tasks.append(test_connection(host))
                
                # Exécuter par lots de 50 pour ne pas surcharger
                if len(tasks) >= 50 or i == 254:
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for j, result in enumerate(results):
                        host_idx = i - len(tasks) + j + 1
                        host = f"{subnet}.{host_idx}"
                        if result is True:
                            servers.add(host)
                    tasks = []
            
            # Filtrer les adresses locales
            filtered_servers = set()
            for host in servers:
                if host not in self.local_addresses:
                    filtered_servers.add(host)
                else:
                    self.logger.debug(f"Ignoré serveur local: {host}")
            
            self.logger.debug(f"Serveurs découverts via scan direct (après filtrage): {filtered_servers}")
            return filtered_servers
            
        except Exception as e:
            self.logger.error(f"Erreur lors du scan direct: {str(e)}")
            
        return servers
    
    async def _check_server_availability(self) -> None:
        """
        Vérifie si les serveurs connus sont toujours disponibles.
        Cette méthode est plus légère que le scan complet et sert à maintenir
        la liste des serveurs à jour entre les scans complets.
        """
        servers_to_remove = []
        current_time = time.time()
        
        # Vérifier d'abord le dernier serveur actif en priorité
        if self.last_known_active_server and self.last_known_active_server in self.known_servers:
            server = self.last_known_active_server
            info = self.known_servers[server]
            
            try:
                # Tester la connexion
                future = asyncio.open_connection(server, 1704)
                reader, writer = await asyncio.wait_for(future, timeout=1.0)
                writer.close()
                await writer.wait_closed()
                
                # Serveur toujours disponible, mettre à jour le timestamp
                info["last_seen"] = current_time
                self.logger.debug(f"Dernier serveur actif {server} toujours disponible")
                
            except:
                self.logger.info(f"Dernier serveur actif {server} actuellement indisponible")
        
        # Vérifier les autres serveurs connus
        for server, info in self.known_servers.items():
            # Ignorer le dernier serveur actif (déjà vérifié)
            if server == self.last_known_active_server:
                continue
                
            # Si un serveur n'a pas été vu depuis plus de 5 minutes, tester sa disponibilité
            if current_time - info["last_seen"] > 300:  # 5 minutes
                try:
                    # Tester la connexion
                    future = asyncio.open_connection(server, 1704)
                    reader, writer = await asyncio.wait_for(future, timeout=1.0)
                    writer.close()
                    await writer.wait_closed()
                    
                    # Serveur toujours disponible, mettre à jour le timestamp
                    info["last_seen"] = current_time
                    
                except:
                    # Si le serveur n'a pas été vu depuis plus de 10 minutes, le supprimer
                    if current_time - info["last_seen"] > 600:  # 10 minutes
                        # Ne pas supprimer le dernier serveur actif, même s'il est inaccessible
                        if server == self.last_known_active_server:
                            self.logger.info(f"Dernier serveur actif {server} inaccessible mais conservé")
                        else:
                            self.logger.info(f"Serveur Snapcast supprimé car inaccessible: {server}")
                            servers_to_remove.append(server)
        
        # Supprimer les serveurs qui ne sont plus disponibles
        for server in servers_to_remove:
            del self.known_servers[server]