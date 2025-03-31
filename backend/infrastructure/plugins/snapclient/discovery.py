"""
Découverte des serveurs Snapcast sur le réseau via Avahi, avec gestion des timeouts.
"""
import asyncio
import logging
import socket
import re
from typing import List, Optional

from backend.infrastructure.plugins.snapclient.models import SnapclientServer

class SnapclientDiscovery:
    """Détecte les serveurs Snapcast sur le réseau local via Avahi."""
    
    def __init__(self):
        self.logger = logging.getLogger("plugin.snapclient.discovery")
        self._has_avahi = self._check_avahi_available()
        
        if not self._has_avahi:
            self.logger.warning("Avahi n'est pas disponible, la découverte sera limitée")
    
    def _check_avahi_available(self) -> bool:
        """Vérifie si Avahi est disponible sur le système."""
        try:
            import subprocess
            result = subprocess.run(
                ["which", "avahi-browse"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=2  # Ajouter un timeout ici aussi
            )
            return result.returncode == 0
        except Exception as e:
            self.logger.warning(f"Erreur lors de la vérification d'Avahi: {str(e)}")
            return False
    
    async def discover_servers(self) -> List[SnapclientServer]:
        """Découvre les serveurs Snapcast via Avahi avec timeout."""
        self.logger.info("Démarrage de la découverte des serveurs Snapcast")
        servers = []
        
        if not self._has_avahi:
            self.logger.error("Avahi n'est pas disponible, découverte impossible")
            return []
        
        # Obtenir notre propre nom d'hôte pour filtrer les résultats
        own_hostname = await self._get_own_hostname()
        
        try:
            # Lancer la commande avec un timeout strict
            self.logger.info("Exécution de avahi-browse -r _snapcast._tcp avec timeout")
            
            # Créer le processus sans attendre qu'il termine
            process = await asyncio.create_subprocess_exec(
                "avahi-browse", "-r", "_snapcast._tcp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Attendre le résultat avec un timeout de 5 secondes
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=5.0)
                output = stdout.decode()
                
                if stderr:
                    error = stderr.decode()
                    if error:
                        self.logger.warning(f"Erreur avahi-browse: {error}")
                
                if not output.strip():
                    self.logger.warning("Aucune sortie de avahi-browse, vérifier la configuration réseau")
                    return []
                
                # Analyser la sortie pour extraire les serveurs Snapcast externes
                avahi_servers = self._parse_avahi_output(output, own_hostname)
                
                if avahi_servers:
                    servers.extend(avahi_servers)
                    self.logger.info(f"Avahi a trouvé {len(avahi_servers)} serveurs externes")
                    for server in avahi_servers:
                        self.logger.info(f"Serveur trouvé: {server.name} ({server.host})")
                else:
                    self.logger.warning("Aucun serveur externe trouvé via Avahi")
                
            except asyncio.TimeoutError:
                self.logger.error("Timeout lors de l'exécution de avahi-browse")
                try:
                    # Nettoyer le processus qui a pris trop de temps
                    process.kill()
                except:
                    pass
                
                # Alternative: essayer une commande avahi-browse plus simple
                self.logger.info("Tentative avec une commande avahi-browse alternative")
                alt_result = await self._try_alternative_avahi_command(own_hostname)
                if alt_result:
                    servers.extend(alt_result)
                
        except Exception as e:
            self.logger.error(f"Erreur lors de la découverte Avahi: {str(e)}")
        
        return servers
    
    async def _try_alternative_avahi_command(self, own_hostname: str) -> List[SnapclientServer]:
        """Essaie une commande avahi-browse alternative en cas d'échec de la première."""
        try:
            # Cette commande est plus simple et devrait être plus rapide
            process = await asyncio.create_subprocess_exec(
                "avahi-browse", "-t", "-r", "_snapcast._tcp",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=3.0)
            output = stdout.decode()
            
            if not output.strip():
                return []
                
            return self._parse_avahi_output(output, own_hostname)
            
        except (asyncio.TimeoutError, Exception) as e:
            self.logger.error(f"Erreur avec la commande alternative: {str(e)}")
            return []
    
    def _parse_avahi_output(self, output: str, own_hostname: str) -> List[SnapclientServer]:
        """
        Parse la sortie de avahi-browse pour extraire les serveurs Snapcast.
        Ne conserve que les serveurs IPv4 et ignore les entrées localhost et notre propre hostname.
        """
        servers = []
        current_server = {}
        
        # Diviser la sortie en lignes
        lines = output.splitlines()
        
        for line in lines:
            # Ne traiter que les lignes de résultats complets (commençant par =)
            if line.startswith('='):
                # Nouvelle entrée de serveur
                if 'hostname' not in current_server and 'IPv4' in line:
                    current_server = {'type': 'IPv4'}
                    
            # Extraire les informations
            elif 'hostname = [' in line and current_server:
                hostname = line.split('[')[1].split(']')[0]
                current_server['hostname'] = hostname
                
            elif 'address = [' in line and current_server:
                address = line.split('[')[1].split(']')[0]
                current_server['address'] = address
                
            elif 'port = [' in line and current_server:
                port = int(line.split('[')[1].split(']')[0])
                current_server['port'] = port
                
                # Fin d'une entrée, vérifier et ajouter le serveur
                if self._is_valid_server(current_server, own_hostname):
                    server_name = current_server['hostname'].split('.')[0]
                    servers.append(SnapclientServer(
                        host=current_server['address'],
                        name=server_name,
                        port=current_server['port']
                    ))
                
                # Réinitialiser pour la prochaine entrée
                current_server = {}
        
        return servers
    
    def _is_valid_server(self, server: dict, own_hostname: str) -> bool:
        """
        Vérifie si un serveur découvert est valide (pas localhost, pas nous-même).
        """
        if not server or 'address' not in server or 'hostname' not in server:
            return False
        
        # Ignorer localhost
        if server['address'].startswith('127.'):
            return False
        
        # Ignorer notre propre hostname
        if own_hostname and own_hostname.lower() in server['hostname'].lower():
            return False
            
        # Ignorer les entrées oakos (c'est nous-même)
        if 'oakos' in server['hostname'].lower():
            return False
        
        return True
    
    async def _get_own_hostname(self) -> str:
        """Récupère notre propre nom d'hôte."""
        try:
            process = await asyncio.create_subprocess_exec(
                "hostname",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=1.0)
            hostname = stdout.decode().strip()
            self.logger.debug(f"Notre nom d'hôte: {hostname}")
            return hostname
        except Exception as e:
            self.logger.warning(f"Impossible de déterminer notre nom d'hôte: {e}")
            return "oakos"  # Fallback par défaut