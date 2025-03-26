"""
Plugin principal Snapclient pour oakOS.
"""
import asyncio
import logging
import socket
from typing import Dict, Any, Optional, List

from backend.application.event_bus import EventBus
from backend.application.interfaces.audio_source import AudioSourcePlugin
from backend.infrastructure.plugins.base import BaseAudioPlugin
from backend.infrastructure.plugins.snapclient.process import SnapclientProcess
from backend.infrastructure.plugins.snapclient.discovery import SnapclientDiscovery
from backend.infrastructure.plugins.snapclient.connection import SnapclientConnection
from backend.infrastructure.plugins.snapclient.models import SnapclientServer


class SnapclientPlugin(BaseAudioPlugin):
    """
    Plugin principal pour la source audio Snapclient (MacOS via Snapcast).
    Implémente l'interface AudioSourcePlugin en étendant BaseAudioPlugin.
    """
    
    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        """
        Initialise le plugin Snapclient.
        
        Args:
            event_bus: Bus d'événements pour la communication
            config: Configuration du plugin
        """
        super().__init__(event_bus, "snapclient")
        
        # Configuration du plugin
        self.config = config
        self.executable_path = config.get("executable_path", "/usr/bin/snapclient")
        self.polling_interval = config.get("polling_interval", 5.0)
        self.auto_discover = config.get("auto_discover", True)
        self.auto_connect = config.get("auto_connect", True)
        
        # Créer les sous-composants
        self.process_manager = SnapclientProcess(self.executable_path)
        self.discovery = SnapclientDiscovery()
        self.connection_manager = SnapclientConnection(self.process_manager)
        self.connection_manager.set_auto_connect(self.auto_connect)
        
        # État interne
        self.discovered_servers: List[SnapclientServer] = []
        self.discovery_task = None
        self.logger = logging.getLogger("plugin.snapclient")
        
        # Liste des serveurs blacklistés - ne sera réinitialisée que lors du changement de source audio
        self.blacklisted_servers: List[str] = []
    
    async def initialize(self) -> bool:
        """
        Initialise le plugin.
        
        Returns:
            bool: True si l'initialisation a réussi, False sinon
        """
        self.logger.info("Initialisation du plugin Snapclient")
        
        # Vérifier que l'exécutable snapclient existe
        if not await self.process_manager.check_executable():
            self.logger.error(f"L'exécutable snapclient n'existe pas ou n'est pas exécutable: {self.executable_path}")
            return False
        
        return True
    
    async def start(self) -> bool:
        """
        Démarre le plugin et active la source audio Snapclient.
        
        Returns:
            bool: True si le démarrage a réussi, False sinon
        """
        try:
            self.logger.info("Démarrage du plugin Snapclient")
            self.is_active = True
            
            # Réactiver l'auto-connect lors d'un changement de source audio
            self.auto_connect = True  # Réinitialiser la valeur de configuration
            self.connection_manager.set_auto_connect(self.auto_connect)
            self.logger.info("Auto-connect réactivé lors du démarrage")
            
            # Publier l'état initial
            await self.publish_plugin_state(self.STATE_INACTIVE)
            
            # Démarrer en mode découverte
            await self.publish_plugin_state(self.STATE_READY_TO_CONNECT)
            
            # Lancer la découverte des serveurs si activée
            if self.auto_discover:
                self.discovery_task = asyncio.create_task(self._run_discovery_loop())
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage du plugin Snapclient: {str(e)}")
            return False
    
    async def stop(self) -> bool:
        """
        Arrête le plugin et désactive la source audio Snapclient.
        
        Returns:
            bool: True si l'arrêt a réussi, False sinon
        """
        try:
            self.logger.info("Arrêt du plugin Snapclient")
            self.is_active = False
            
            # Arrêter la tâche de découverte
            if self.discovery_task and not self.discovery_task.done():
                self.discovery_task.cancel()
                try:
                    await self.discovery_task
                except asyncio.CancelledError:
                    pass
            
            # Déconnecter le serveur actuel
            await self.connection_manager.disconnect()
            
            # Effacer les demandes en attente
            self.connection_manager.clear_pending_requests()
            
            # Réinitialiser la blacklist lors de l'arrêt du plugin
            # (pour permettre la reconnexion lors du prochain démarrage)
            self.blacklisted_servers = []
            self.logger.info("Blacklist de serveurs réinitialisée")
            
            # Publier l'état final
            await self.publish_plugin_state(self.STATE_INACTIVE)
            
            return True
        except Exception as e:
            self.logger.error(f"Erreur lors de l'arrêt du plugin Snapclient: {str(e)}")
            return False
    
    async def get_status(self) -> Dict[str, Any]:
        """
        Récupère l'état actuel du plugin.
        
        Returns:
            Dict[str, Any]: État actuel du plugin
        """
        try:
            # Récupérer les informations de connexion
            connection_info = self.connection_manager.get_connection_info()
            
            # Récupérer les informations sur le processus
            process_info = await self.process_manager.get_process_info()
            
            # Construire le statut global
            status = {
                "source": "snapclient",
                "is_active": self.is_active,
                "device_connected": connection_info.get("device_connected", False),
                "discovered_servers": [s.to_dict() for s in self.discovered_servers],
                "pending_requests": self.connection_manager.get_pending_requests(),
                "process_info": process_info,
                "blacklisted_servers": self.blacklisted_servers,
                "metadata": {}
            }
            
            # Ajouter les informations de connexion si disponibles
            if connection_info.get("device_connected"):
                status["metadata"].update({
                    "device_name": connection_info.get("device_name"),
                    "host": connection_info.get("host")
                })
            
            return status
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du statut: {str(e)}")
            return {
                "source": "snapclient",
                "is_active": self.is_active,
                "error": str(e)
            }
    
    async def get_connection_info(self) -> Dict[str, Any]:
        """
        Récupère les informations de connexion.
        
        Returns:
            Dict[str, Any]: Informations de connexion
        """
        return self.connection_manager.get_connection_info()
    
    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande spécifique pour ce plugin.
        
        Args:
            command: Nom de la commande
            data: Données associées à la commande
            
        Returns:
            Dict[str, Any]: Résultat de la commande
        """
        try:
            self.logger.info(f"Traitement de la commande {command} avec données: {data}")
            
            if command == "discover":
                # Forcer une découverte des serveurs
                try:
                    servers = await self.discovery.discover_servers()
                    if servers is None:
                        servers = []
                    self.discovered_servers = servers
                    
                    result = {
                        "success": True,
                        "servers": [s.to_dict() for s in servers],
                        "count": len(servers)
                    }
                    
                    # Gérer les serveurs découverts
                    if self.connection_manager.auto_connect:
                        # Filtrer les serveurs blacklistés
                        filtered_servers = []
                        for server in servers:
                            if server.host not in self.blacklisted_servers:
                                filtered_servers.append(server)
                            else:
                                self.logger.info(f"Serveur blacklisté ignoré dans discover: {server.host}")
                                
                        action_result = await self.connection_manager.handle_discovered_servers(filtered_servers, self.blacklisted_servers)
                        if action_result:  # S'assurer que action_result n'est pas None
                            result.update(action_result)
                        
                        # Si une action a été effectuée, mettre à jour l'état
                        action = action_result.get("action") if action_result else None
                        if action == "auto_connected" and action_result.get("server"):
                            if self.connection_manager.get_connection_info().get("device_connected"):
                                await self.publish_plugin_state(self.STATE_CONNECTED, {
                                    "connected": True,
                                    "deviceConnected": True,
                                    "host": self.connection_manager.current_server.host,
                                    "device_name": self.connection_manager.current_server.name
                                })
                    
                    return result
                except Exception as e:
                    self.logger.error(f"Erreur lors de la découverte des serveurs: {str(e)}")
                    return {
                        "success": False,
                        "error": f"Erreur lors de la découverte des serveurs: {str(e)}",
                        "servers": [],
                        "count": 0
                    }
                
            elif command == "connect":
                # Se connecter à un serveur spécifique
                host = data.get("host")
                if not host:
                    return {"success": False, "error": "Host is required"}
                
                # Vérifier si le serveur est blacklisté
                if host in self.blacklisted_servers:
                    self.logger.warning(f"Tentative de connexion à un serveur blacklisté: {host}")
                    return {
                        "success": False,
                        "error": f"Le serveur {host} a été déconnecté manuellement. Changez de source audio pour pouvoir vous y reconnecter.",
                        "blacklisted": True
                    }
                
                # Trouver le serveur dans la liste des serveurs découverts
                server = None
                for s in self.discovered_servers:
                    if s.host == host:
                        server = s
                        break
                
                if not server:
                    self.logger.warning(f"Serveur non trouvé pour l'hôte {host}, création d'un serveur virtuel")
                    server = SnapclientServer(host=host, name=f"Snapserver ({host})")
                
                # Se connecter au serveur
                success = await self.connection_manager.connect(server)
                
                if success:
                    # Publier l'état connecté
                    # Assurer que le nom est clairement défini
                    device_name = server.name
                    if device_name == f"Snapserver ({server.host})":
                        # Essayer de trouver un meilleur nom
                        try:
                            import socket
                            hostname = socket.gethostbyaddr(server.host)
                            if hostname and hostname[0]:
                                device_name = hostname[0]
                        except:
                            # En cas d'échec, utiliser le nom par défaut
                            pass
                    
                    await self.publish_plugin_state(self.STATE_CONNECTED, {
                        "connected": True,
                        "deviceConnected": True,
                        "host": server.host,
                        "device_name": device_name
                    })
                
                return {
                    "success": success,
                    "message": f"Connexion au serveur {server.name}" if success else f"Échec de la connexion au serveur {server.name}",
                    "server": server.to_dict() if success else None
                }
                
            elif command == "disconnect":
                # Se déconnecter du serveur actuel
                current_server = self.connection_manager.current_server
                
                if current_server:
                    # Avant de déconnecter, ajouter l'hôte à la blacklist
                    server_host = current_server.host
                    if server_host not in self.blacklisted_servers:
                        self.blacklisted_servers.append(server_host)
                        self.logger.info(f"Serveur {server_host} ajouté à la blacklist")
                    
                    # Désactiver l'auto-connect pour éviter la reconnexion automatique
                    self.connection_manager.set_auto_connect(False)
                    self.logger.info("Auto-connect désactivé suite à une déconnexion manuelle")
                
                success = await self.connection_manager.disconnect()
                
                if success:
                    # Publier l'état prêt à se connecter
                    await self.publish_plugin_state(self.STATE_READY_TO_CONNECT)
                
                return {
                    "success": success,
                    "message": f"Déconnexion du serveur {current_server.name if current_server else 'inconnu'}" if success else "Échec de la déconnexion",
                    "blacklisted": self.blacklisted_servers
                }
                
            elif command == "accept_connection":
                # Accepter une demande de connexion
                request_id = data.get("request_id")
                if not request_id:
                    # Essayer de récupérer l'hôte
                    host = data.get("host")
                    if not host:
                        return {"success": False, "error": "Request ID or host is required"}
                    
                    # Trouver la demande correspondant à cet hôte
                    for rid, req in self.connection_manager.pending_requests.items():
                        if req.server.host == host:
                            request_id = rid
                            break
                    
                    if not request_id:
                        return {"success": False, "error": f"No pending request found for host {host}"}
                
                result = await self.connection_manager.handle_connection_request(request_id, True)
                
                if result.get("success"):
                    # Publier l'état connecté
                    await self.publish_plugin_state(self.STATE_CONNECTED, {
                        "connected": True,
                        "deviceConnected": True,
                        "host": result.get("server", {}).get("host"),
                        "device_name": result.get("server", {}).get("name", "Mac-mini-de-Léo")  # Utiliser le nom du serveur
                    })
                
                return result
                
            elif command == "reject_connection":
                # Rejeter une demande de connexion
                request_id = data.get("request_id")
                if not request_id:
                    # Essayer de récupérer l'hôte
                    host = data.get("host")
                    if not host:
                        return {"success": False, "error": "Request ID or host is required"}
                    
                    # Trouver la demande correspondant à cet hôte
                    for rid, req in self.connection_manager.pending_requests.items():
                        if req.server.host == host:
                            request_id = rid
                            break
                    
                    if not request_id:
                        return {"success": False, "error": f"No pending request found for host {host}"}
                
                result = await self.connection_manager.handle_connection_request(request_id, False)
                
                if result.get("success"):
                    # Si on est toujours connecté, publier l'état connecté
                    if self.connection_manager.current_server:
                        await self.publish_plugin_state(self.STATE_CONNECTED, {
                            "connected": True,
                            "deviceConnected": True,
                            "host": self.connection_manager.current_server.host,
                            "device_name": self.connection_manager.current_server.name
                        })
                    else:
                        # Sinon, publier l'état prêt à se connecter
                        await self.publish_plugin_state(self.STATE_READY_TO_CONNECT)
                
                return result
                
            elif command == "restart":
                # Redémarrer le processus
                host = None
                if self.connection_manager.current_server:
                    host = self.connection_manager.current_server.host
                
                success = await self.process_manager.restart(host)
                
                return {
                    "success": success,
                    "message": "Processus snapclient redémarré" if success else "Échec du redémarrage du processus snapclient"
                }
                
            elif command == "test_audio":
                # Tester l'audio avec la commande aplay
                try:
                    # Vérifier si le processus snapclient est en cours d'exécution
                    process_info = await self.process_manager.get_process_info()
                    if not process_info.get("running", False):
                        return {
                            "success": False,
                            "error": "Le processus snapclient n'est pas en cours d'exécution"
                        }
                    
                    # Jouer un bip sonore pour tester l'audio
                    try:
                        # Créer un fichier wave temporaire
                        import wave
                        import struct
                        import tempfile
                        import os
                        
                        temp_file = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                        
                        # Créer un fichier WAV avec un bip sonore
                        sample_rate = 44100
                        duration = 1  # secondes
                        frequency = 440  # Hz (La)
                        
                        with wave.open(temp_file.name, 'w') as wf:
                            wf.setnchannels(1)
                            wf.setsampwidth(2)
                            wf.setframerate(sample_rate)
                            
                            # Générer un son sinusoïdal
                            for i in range(int(duration * sample_rate)):
                                value = int(32767 * 0.5 * 
                                         (1.0 if i < 0.1 * sample_rate else
                                          (0.0 if i > 0.9 * sample_rate else
                                           (0.9 - (i / sample_rate)) / 0.8)) * 
                                         (1.0 if i < 0.01 * sample_rate else
                                          (0.0 if i > 0.99 * sample_rate else 1.0)) *
                                         (1.0 if (i // 4000) % 2 == 0 else 0.0))
                                data = struct.pack('<h', value)
                                wf.writeframes(data)
                        
                        # Jouer le fichier avec aplay
                        process = await asyncio.create_subprocess_exec(
                            "aplay", temp_file.name,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE
                        )
                        
                        await process.wait()
                        
                        # Nettoyer le fichier temporaire
                        try:
                            os.unlink(temp_file.name)
                        except:
                            pass
                        
                        return {
                            "success": True,
                            "message": "Test audio exécuté avec succès"
                        }
                    except Exception as audio_e:
                        self.logger.error(f"Erreur lors du test audio: {str(audio_e)}")
                        return {
                            "success": False,
                            "error": f"Erreur lors du test audio: {str(audio_e)}"
                        }
                    
                except Exception as e:
                    self.logger.error(f"Erreur lors du test audio: {str(e)}")
                    return {
                        "success": False,
                        "error": f"Erreur lors du test audio: {str(e)}"
                    }
                
            else:
                self.logger.warning(f"Commande inconnue: {command}")
                return {"success": False, "error": f"Unknown command: {command}"}
                
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de la commande {command}: {str(e)}")
            return {"success": False, "error": str(e)}
    
    async def _run_discovery_loop(self):
        """
        Exécute la boucle de découverte des serveurs à intervalles réguliers
        et vérifie l'état de la connexion.
        """
        try:
            while self.is_active:
                self.logger.debug("Exécution de la découverte périodique des serveurs")
                
                                    # Si nous sommes connectés, vérifier que le serveur est toujours disponible
                if self.connection_manager.current_server:
                    is_server_alive = False
                    server_host = self.connection_manager.current_server.host
                    
                    # Vérifier si le serveur est blacklisté (cas de race possible où un serveur blacklisté est toujours connecté)
                    if server_host in self.blacklisted_servers:
                        self.logger.warning(f"Serveur connecté {server_host} présent dans la blacklist, déconnexion forcée")
                        await self.connection_manager.disconnect()
                        await self.publish_plugin_state(self.STATE_READY_TO_CONNECT, {
                            "connected": False,
                            "deviceConnected": False
                        })
                        continue  # Passer à l'itération suivante
                    
                    # Vérifier si le processus snapclient est toujours en cours d'exécution
                    process_info = await self.process_manager.get_process_info()
                    if not process_info.get("running", False):
                        self.logger.warning(f"Le processus snapclient n'est plus en cours d'exécution, déconnexion du serveur {server_host}")
                        await self.connection_manager.disconnect()
                        await self.publish_plugin_state(self.STATE_READY_TO_CONNECT, {
                            "connected": False,
                            "deviceConnected": False
                        })
                        # Continuer pour découvrir de nouveaux serveurs
                    else:
                        # Vérification simple mais efficace avec socket
                        try:
                            # Utilisation directe de la socket
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.settimeout(0.5)  # Timeout court pour être réactif
                            result = sock.connect_ex((server_host, 1704))
                            sock.close()
                            
                            is_server_alive = (result == 0)  # 0 = connexion réussie
                            
                            if not is_server_alive:
                                self.logger.warning(f"Le serveur {server_host} n'est plus disponible (code={result}), déconnexion")
                                await self.connection_manager.disconnect()
                                await self.publish_plugin_state(self.STATE_READY_TO_CONNECT, {
                                    "connected": False,
                                    "deviceConnected": False
                                })
                                
                                # Force kill any remaining snapclient processes
                                try:
                                    await asyncio.create_subprocess_exec(
                                        "killall", "-9", "snapclient",
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE
                                    )
                                    self.logger.info("Commande killall -9 snapclient exécutée")
                                except Exception as killall_e:
                                    self.logger.error(f"Erreur lors du killall: {str(killall_e)}")
                        except Exception as sock_e:
                            self.logger.warning(f"Erreur lors de la vérification socket: {str(sock_e)}")
                            # Considérer le serveur comme down en cas d'erreur
                            is_server_alive = False
                            await self.connection_manager.disconnect()
                            await self.publish_plugin_state(self.STATE_READY_TO_CONNECT, {
                                "connected": False,
                                "deviceConnected": False
                            })
                
                                    # Ne découvrez de nouveaux serveurs que si nous ne sommes pas connectés
                # et que la découverte automatique est activée
                if not self.connection_manager.current_server and self.auto_discover:
                    servers = await self.discovery.discover_servers()
                    if servers is None:
                        servers = []
                        
                    # Filtrer les serveurs blacklistés
                    filtered_servers = []
                    for server in servers:
                        if server.host not in self.blacklisted_servers:
                            filtered_servers.append(server)
                        else:
                            self.logger.info(f"Serveur blacklisté ignoré: {server.host}")
                            
                    self.discovered_servers = filtered_servers
                    self.logger.info(f"Découverte de serveurs terminée, {len(filtered_servers)} trouvés (après filtrage)")
                    
                    # Gérer les serveurs découverts uniquement si auto_connect est activé
                    if self.connection_manager.auto_connect and filtered_servers:
                        try:
                            result = await self.connection_manager.handle_discovered_servers(servers)
                            
                            # Si une action a été effectuée, mettre à jour l'état
                            action = result.get("action") if result else None
                            
                            if action == "auto_connected" and result.get("server"):
                                # Serveur connecté automatiquement
                                await self.publish_plugin_state(self.STATE_CONNECTED, {
                                    "connected": True,
                                    "deviceConnected": True,
                                    "host": result.get("server", {}).get("host"),
                                    "device_name": result.get("server", {}).get("name")
                                })
                        except Exception as e:
                            self.logger.error(f"Erreur lors du traitement des serveurs découverts: {str(e)}")
                
                # Attendre l'intervalle de polling
                await asyncio.sleep(self.polling_interval)
                
        except asyncio.CancelledError:
            self.logger.info("Tâche de découverte annulée")
        except Exception as e:
            self.logger.error(f"Erreur dans la boucle de découverte: {str(e)}")