"""
Module pour interagir directement avec le serveur Snapcast.
"""
import logging
import json
import asyncio
import socket
from typing import Dict, Any, Optional

class SnapcastProtocol:
    """Interface de communication avec le serveur Snapcast."""
    
    def __init__(self):
        self.logger = logging.getLogger("plugin.snapclient.protocol")
    
    async def get_server_info(self, host: str, port: int = 1705) -> Optional[Dict[str, Any]]:
        """
        Récupère les informations du serveur Snapcast.
        
        Args:
            host: Adresse IP du serveur
            port: Port du serveur (1705 pour le contrôle Snapcast)
            
        Returns:
            Optional[Dict[str, Any]]: Informations du serveur ou None en cas d'erreur
        """
        try:
            # Créer la connexion TCP au serveur Snapcast
            reader, writer = await asyncio.open_connection(host, port)
            
            # Créer la commande JSON RPC
            command = {
                "id": 1,
                "jsonrpc": "2.0",
                "method": "Server.GetStatus"
            }
            
            # Envoyer la commande
            writer.write(json.dumps(command).encode() + b'\n')
            await writer.drain()
            
            # Lire la réponse
            response_data = await reader.read(4096)
            writer.close()
            await writer.wait_closed()
            
            # Analyser la réponse
            if response_data:
                response = json.loads(response_data.decode())
                if 'result' in response:
                    server_info = response['result']
                    self.logger.info(f"Informations du serveur récupérées: {server_info.get('server', {}).get('host_name', 'Inconnu')}")
                    return server_info
            
            return None
        except (ConnectionRefusedError, asyncio.TimeoutError) as e:
            self.logger.warning(f"Impossible de se connecter au serveur Snapcast {host}:{port} - {str(e)}")
            return None
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération des informations du serveur: {str(e)}")
            return None
    
    async def get_server_name(self, host: str) -> str:
        """
        Récupère le nom du serveur Snapcast.
        
        Args:
            host: Adresse IP du serveur
            
        Returns:
            str: Nom du serveur ou une valeur par défaut
        """
        try:
            # Essayer d'abord avec le protocole Snapcast
            server_info = await self.get_server_info(host)
            if server_info and 'server' in server_info:
                # Récupérer le nom d'hôte du serveur
                host_name = server_info['server'].get('host_name')
                if host_name:
                    # Nettoyer le nom (supprimer les suffixes de domaine)
                    host_name = host_name.replace('.local', '').replace('.home', '')
                    return host_name
            
            # Si la méthode Snapcast échoue, essayer avec mDNS
            try:
                # Essayer d'utiliser avahi-resolve si disponible (systèmes Linux)
                process = await asyncio.create_subprocess_exec(
                    'avahi-resolve', '-a', host,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()
                
                if stdout:
                    result = stdout.decode().strip().split('\t')
                    if len(result) > 1:
                        name = result[1].replace('.local', '').replace('.home', '')
                        return name
            except Exception:
                # En cas d'échec, essayer socket.gethostbyaddr
                try:
                    hostname = socket.gethostbyaddr(host)
                    if hostname and hostname[0]:
                        name = hostname[0].replace('.local', '').replace('.home', '')
                        return name
                except Exception:
                    pass
            
            # Si tout échoue, utiliser la valeur par défaut
            return f"Snapserver ({host})"
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du nom du serveur: {str(e)}")
            return f"Snapserver ({host})"