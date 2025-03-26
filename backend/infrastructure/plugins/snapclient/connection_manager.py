"""
Gestionnaire de connexions pour le plugin snapclient.
"""
import asyncio
import logging
import time
import subprocess
import socket
from typing import Dict, Any, Optional, List, Set, Callable
from backend.infrastructure.plugins.base import BaseAudioPlugin


class ConnectionManager:
    """
    Gère les connexions entrantes de serveurs Snapcast et les transitions
    entre différents serveurs.
    """
    
    def __init__(self, event_bus, metadata_processor, process_manager):
        self.logger = logging.getLogger("snapclient.connection")
        self.event_bus = event_bus
        self.metadata_processor = metadata_processor
        self.process_manager = process_manager
        
        # État des connexions
        self.active_connection: Optional[Dict[str, Any]] = None
        self.pending_connections: Dict[str, Dict[str, Any]] = {}
        self.rejected_hosts: Set[str] = set()
        self.connection_history: List[Dict[str, Any]] = []
        
        # État interne
        self.connection_task = None
        self.listen_task = None
        
        # Nouvel attribut pour mémoriser le dernier serveur connecté
        self.last_successful_server = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5  # Augmenté pour plus de persistance
        self.reconnection_task = None
        self.reconnection_cooldown = 3  # Délai entre tentatives de reconnexion (secondes)
    
    async def start(self) -> None:
        """Démarre le gestionnaire de connexions"""
        if self.connection_task is None or self.connection_task.done():
            self.connection_task = asyncio.create_task(self._monitor_connections())
            self.logger.info("Gestionnaire de connexions Snapcast démarré")
    
    async def stop(self) -> None:
        """Arrête le gestionnaire de connexions"""
        if self.connection_task and not self.connection_task.done():
            self.connection_task.cancel()
            try:
                await self.connection_task
            except asyncio.CancelledError:
                pass
            self.connection_task = None
            self.logger.info("Gestionnaire de connexions Snapcast arrêté")
    
    async def handle_new_server_discovery(self, server_info: Dict[str, Any]) -> None:
        """
        Gère la découverte d'un nouveau serveur Snapcast.
        Décide si une connexion automatique est appropriée ou si une demande
        utilisateur est nécessaire.
        
        Args:
            server_info: Informations sur le serveur découvert
        """
        host = server_info["host"]
        
        # Ignorer les hôtes rejetés récemment
        if host in self.rejected_hosts:
            self.logger.debug(f"Serveur ignoré (rejeté précédemment): {host}")
            return
        
        # Vérifier si c'est un ancien serveur auquel on était connecté
        if host == self.last_successful_server and not self.active_connection:
            self.logger.info(f"Ancien serveur reconnecté détecté: {host}")
            await self.connect_to_server(host)
            return
        
        # Si aucune connexion active, se connecter automatiquement
        if not self.active_connection:
            await self.connect_to_server(host)
            return
        
        # Si c'est le même serveur, ignorer
        if self.active_connection["host"] == host:
            self.logger.debug(f"Serveur déjà connecté: {host}")
            return
            
        # Sinon, ajouter aux connexions en attente
        self.logger.info(f"Nouvelle connexion demandée de: {host}")
        
        # Créer la demande de connexion
        request = {
            "host": host,
            "timestamp": time.time(),
            "auto_accept_time": time.time() + 30,  # Réduit à 30 secondes pour auto-accepter plus rapidement
            "status": "pending"
        }
        
        # Ajouter aux connexions en attente
        self.pending_connections[host] = request
        
        # Publier un événement pour notifier l'utilisateur avec état standardisé
        await self.metadata_processor.publish_plugin_state(
            BaseAudioPlugin.STATE_DEVICE_CHANGE_REQUESTED,
            {
                "host": host,
                "request_id": host,  # Utiliser l'hôte comme ID de requête pour simplicité
                "current_host": self.active_connection["host"] if self.active_connection else None,
                "forceUpdate": True
            }
        )
    
    async def accept_connection_request(self, host: str) -> bool:
        """
        Accepte une demande de connexion entrante.
        
        Args:
            host: Hôte à accepter
            
        Returns:
            bool: True si l'acceptation a réussi, False sinon
        """
        if host not in self.pending_connections:
            self.logger.warning(f"Tentative d'accepter une connexion non demandée: {host}")
            return False
        
        # Marquer comme acceptée
        self.pending_connections[host]["status"] = "accepted"
        
        # Se connecter au serveur
        return await self.connect_to_server(host)
    
    async def reject_connection_request(self, host: str) -> bool:
        """
        Rejette une demande de connexion entrante.
        
        Args:
            host: Hôte à rejeter
            
        Returns:
            bool: True si le rejet a réussi, False sinon
        """
        if host not in self.pending_connections:
            self.logger.warning(f"Tentative de rejeter une connexion non demandée: {host}")
            return False
            
        # Supprimer de la liste des demandes en attente
        request = self.pending_connections.pop(host)
        request["status"] = "rejected"
        
        # Ajouter à l'historique
        self.connection_history.append(request)
        
        # Ajouter à la liste des hôtes rejetés (temporairement)
        self.rejected_hosts.add(host)
        
        # Planifier la suppression de l'hôte de la liste des rejetés après un certain temps
        asyncio.create_task(self._remove_from_rejected_after_delay(host, 300))  # 5 minutes
        
        # Publier un événement pour informer du rejet
        await self.event_bus.publish("snapclient_connection_rejected", {
            "source": "snapclient",
            "host": host
        })
        
        return True
    
    async def try_reconnect(self) -> bool:
        """
        Tente de se reconnecter au dernier serveur connecté avec succès.
        
        Returns:
            bool: True si la reconnexion a réussi, False sinon
        """
        if not self.last_successful_server:
            self.logger.debug("Aucun serveur précédent pour la reconnexion")
            return False
            
        # Limiter le nombre de tentatives
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            self.logger.warning(f"Abandon de la reconnexion après {self.reconnect_attempts} tentatives")
            return False
            
        self.reconnect_attempts += 1
        host = self.last_successful_server
        
        self.logger.info(f"Tentative de reconnexion au serveur: {host} (essai {self.reconnect_attempts}/{self.max_reconnect_attempts})")
        
        try:
            result = await self.connect_to_server(host)
            
            if result:
                self.logger.info(f"Reconnexion réussie au serveur: {host}")
                self.reconnect_attempts = 0  # Réinitialiser le compteur de tentatives
                return True
            else:
                self.logger.warning(f"Échec de reconnexion au serveur: {host}")
                # Planifier une nouvelle tentative après un délai
                if self.reconnect_attempts < self.max_reconnect_attempts:
                    asyncio.create_task(self._delayed_reconnect())
                return False
                
        except Exception as e:
            self.logger.error(f"Erreur lors de la tentative de reconnexion: {str(e)}")
            # Planifier une nouvelle tentative après un délai
            if self.reconnect_attempts < self.max_reconnect_attempts:
                asyncio.create_task(self._delayed_reconnect())
            return False
    
    async def _delayed_reconnect(self) -> None:
        """Tentative de reconnexion après un délai."""
        await asyncio.sleep(self.reconnection_cooldown)
        if not self.active_connection and self.last_successful_server:
            self.logger.info(f"Nouvelle tentative de reconnexion planifiée au serveur: {self.last_successful_server}")
            await self.try_reconnect()
    
    async def connect_to_server(self, host: str) -> bool:
        """
        Se connecte à un serveur Snapcast spécifique.
        S'assure qu'une seule connexion est active à la fois.
        
        Args:
            host: Hôte du serveur
            
        Returns:
            bool: True si la connexion a réussi, False sinon
        """
        self.logger.info(f"Tentative de connexion au serveur Snapcast: {host}")
        
        # Vérifier si l'hôte est une adresse locale (à ignorer)
        try:
            # Utiliser la liste des adresses locales déjà calculée (si disponible)
            if hasattr(self, 'local_addresses'):
                local_addresses = self.local_addresses
            else:
                # Autrement, obtenir les adresses locales
                local_addresses = ["127.0.0.1", "localhost"]
                try:
                    # Obtenir toutes les interfaces réseau
                    import netifaces
                    for interface in netifaces.interfaces():
                        addrs = netifaces.ifaddresses(interface).get(netifaces.AF_INET, [])
                        for addr in addrs:
                            ip = addr.get('addr')
                            if ip and ip not in local_addresses:
                                local_addresses.append(ip)
                except Exception as e:
                    self.logger.warning(f"Impossible de récupérer les adresses locales: {str(e)}")

            # Vérification de l'adresse
            if host in local_addresses:
                self.logger.warning(f"Tentative de connexion à un serveur local ignorée: {host}")
                return False
                
        except Exception as e:
            self.logger.error(f"Erreur lors de la vérification d'adresse locale: {str(e)}")
        
        # Vérifier si on est déjà connecté au même serveur
        if self.active_connection and self.active_connection["host"] == host:
            self.logger.info(f"Déjà connecté à {host}")
            return True
        
        # Déconnecter d'abord toute connexion active existante
        if self.active_connection:
            old_host = self.active_connection["host"]
            self.logger.info(f"Déconnexion de l'hôte actuel ({old_host}) avant de se connecter à {host}")
            await self.disconnect()
        
        # Publier un événement de changement d'état avec état standardisé
        await self.metadata_processor.publish_plugin_state(
            BaseAudioPlugin.STATE_READY_TO_CONNECT, 
            {
                "host": host,
                "timestamp": time.time(),
                "forceUpdate": True
            }
        )
        
        # Essayer d'obtenir un nom d'hôte pour l'adresse IP
        device_name = host
        try:
            hostname = socket.getfqdn(host)
            if hostname and hostname != host:
                device_name = hostname
                self.logger.info(f"Nom d'hôte résolu pour {host}: {device_name}")
        except Exception:
            pass
        
        try:
            # Démarrer/redémarrer le processus snapclient avec le nouvel hôte
            self.logger.info(f"Lancement du processus snapclient vers {host}")
            if await self.process_manager.restart_process(host):
                # Mettre à jour la connexion active
                self.active_connection = {
                    "host": host,
                    "connected_at": time.time(),
                    "status": "connected",
                    "device_name": device_name
                }
                
                # Nouvel ajout: enregistrer le serveur pour reconnexion future
                self.last_successful_server = host
                self.reconnect_attempts = 0  # Réinitialiser le compteur de tentatives
                
                # Supprimer des connexions en attente si présent
                if host in self.pending_connections:
                    request = self.pending_connections.pop(host)
                    request["status"] = "accepted"
                    self.connection_history.append(request)
                
                # Publier un événement de connexion réussie avec état standardisé
                await self.metadata_processor.publish_plugin_state(
                    BaseAudioPlugin.STATE_CONNECTED, 
                    {
                        "host": host,
                        "deviceConnected": True,
                        "connected": True,
                        "device_name": device_name,  # Utiliser le nom résolu si disponible
                        "forceUpdate": True
                    }
                )
                self.logger.info(f"État STATE_CONNECTED publié pour {host}")
                
                self.logger.info(f"Connexion réussie au serveur Snapcast: {host}")
                return True
            else:
                self.logger.error(f"Échec du processus snapclient pour l'hôte {host}")
                
                # Publier un événement d'échec - retour à l'état ready_to_connect
                await self.metadata_processor.publish_plugin_state(
                    BaseAudioPlugin.STATE_READY_TO_CONNECT, 
                    {
                        "host": host,
                        "error": "connection_failed",
                        "forceUpdate": True
                    }
                )
                
                # Planifier une nouvelle tentative après un délai
                if not self.reconnection_task or self.reconnection_task.done():
                    self.reconnection_task = asyncio.create_task(self._delayed_reconnect())
                
                return False
        except Exception as e:
            self.logger.error(f"Exception lors de la connexion au serveur Snapcast {host}: {str(e)}")
            
            # Publier un événement d'échec - retour à l'état ready_to_connect
            await self.metadata_processor.publish_plugin_state(
                BaseAudioPlugin.STATE_READY_TO_CONNECT, 
                {
                    "host": host,
                    "error": f"exception: {str(e)}",
                    "forceUpdate": True
                }
            )
            
            # Planifier une nouvelle tentative après un délai
            if not self.reconnection_task or self.reconnection_task.done():
                self.reconnection_task = asyncio.create_task(self._delayed_reconnect())
            
            return False
    
    async def disconnect(self) -> bool:
        """
        Déconnecte du serveur Snapcast actuel de manière robuste.
        
        Returns:
            bool: True si la déconnexion a réussi, False sinon
        """
        if not self.active_connection:
            self.logger.debug("Aucune connexion active à déconnecter")
            
            # Vérifier quand même si un processus est en cours d'exécution
            if self.process_manager.is_running():
                self.logger.warning("Processus snapclient actif sans connexion enregistrée, arrêt")
                await self.process_manager.stop_process()
                
            return True
        
        host = self.active_connection["host"]
        self.logger.info(f"Déconnexion du serveur Snapcast: {host}")
        
        # Mémoriser l'hôte comme dernier serveur connecté (si pas déjà fait)
        if not self.last_successful_server:
            self.last_successful_server = host
        
        # Arrêter le processus snapclient avec plusieurs tentatives si nécessaire
        success = await self.process_manager.stop_process()
        
        if not success:
            self.logger.warning("Première tentative d'arrêt échouée, nouveau essai avec force kill")
            try:
                # Utiliser pkill comme dernier recours
                subprocess.run(["pkill", "-9", "snapclient"], check=False)
                await asyncio.sleep(0.5)
                success = True
            except Exception as e:
                self.logger.error(f"Erreur lors de la tentative de kill forcé: {str(e)}")
                success = False
        
        # Mettre à jour l'état interne même en cas d'échec
        previous_connection = self.active_connection
        self.active_connection = None
        
        # Publier un événement de déconnexion avec état standardisé
        await self.metadata_processor.publish_plugin_state(
            BaseAudioPlugin.STATE_INACTIVE,
            {
                "deviceConnected": False,
                "connected": False,
                "forceUpdate": True
            }
        )
        
        # Forcer une publication d'événement direct pour le WebSocket
        if hasattr(self.metadata_processor, 'event_bus'):
            await self.metadata_processor.event_bus.publish("snapclient_disconnected", {
                "source": "snapclient",
                "status": "disconnected",
                "connected": False,
                "deviceConnected": False,
                "timestamp": time.time()
            })
        
        if success:
            self.logger.info(f"Déconnexion réussie du serveur Snapcast: {host}")
        else:
            self.logger.error(f"Problèmes lors de la déconnexion du serveur Snapcast: {host}")
            
        return success
    
    def get_active_connection(self) -> Optional[Dict[str, Any]]:
        """
        Récupère les informations sur la connexion active.
        
        Returns:
            Optional[Dict[str, Any]]: Informations sur la connexion active ou None
        """
        return self.active_connection
    
    def get_pending_connections(self) -> List[Dict[str, Any]]:
        """
        Récupère la liste des connexions en attente.
        
        Returns:
            List[Dict[str, Any]]: Liste des connexions en attente
        """
        return list(self.pending_connections.values())
    
    async def _monitor_connections(self) -> None:
        """Surveille les connexions en attente pour les auto-accepter si nécessaire"""
        try:
            while True:
                try:
                    current_time = time.time()
                    hosts_to_auto_accept = []
                    
                    # Vérifier les connexions en attente
                    for host, request in self.pending_connections.items():
                        # Auto-accepter après le délai configuré si aucune action n'a été prise
                        if request["status"] == "pending" and current_time >= request["auto_accept_time"]:
                            self.logger.info(f"Auto-acceptation de la connexion après délai: {host}")
                            hosts_to_auto_accept.append(host)
                    
                    # Traiter les auto-acceptations
                    for host in hosts_to_auto_accept:
                        await self.accept_connection_request(host)
                    
                    # Vérifier également si nous devons tenter de reconnecter à un serveur précédent
                    if not self.active_connection and self.last_successful_server and self.reconnect_attempts < self.max_reconnect_attempts:
                        # Limiter les tentatives pour ne pas surcharger
                        await self.try_reconnect()
                        
                except Exception as e:
                    self.logger.error(f"Erreur lors de la surveillance des connexions: {str(e)}")
                
                await asyncio.sleep(5)  # Vérifier toutes les 5 secondes
                
        except asyncio.CancelledError:
            self.logger.debug("Surveillance des connexions annulée")
            raise
    
    async def _remove_from_rejected_after_delay(self, host: str, delay: int) -> None:
        """
        Retire un hôte de la liste des rejetés après un délai.
        
        Args:
            host: Hôte à retirer
            delay: Délai en secondes
        """
        await asyncio.sleep(delay)
        if host in self.rejected_hosts:
            self.rejected_hosts.remove(host)
            self.logger.debug(f"Hôte retiré de la liste des rejetés: {host}")