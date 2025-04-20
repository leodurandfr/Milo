# backend/infrastructure/plugins/snapclient/plugin.py
"""
Plugin principal Snapclient pour oakOS - Version minimale.
"""
import asyncio
import logging
import time
from typing import Dict, Any, List, Optional

from backend.application.event_bus import EventBus
from backend.infrastructure.plugins.base import BaseAudioPlugin
from backend.infrastructure.plugins.snapclient.process import SnapclientProcess
from backend.infrastructure.plugins.snapclient.discovery import SnapclientDiscovery
from backend.infrastructure.plugins.snapclient.connection import SnapclientConnection
from backend.infrastructure.plugins.snapclient.models import SnapclientServer
from backend.infrastructure.plugins.snapclient.monitor import SnapcastMonitor

class SnapclientPlugin(BaseAudioPlugin):
    """
    Plugin pour la source audio Snapclient (MacOS via Snapcast).
    Version minimale simplifiée.
    """

    def __init__(self, event_bus: EventBus, config: Dict[str, Any]):
        """
        Initialise le plugin Snapclient.
        """
        super().__init__(event_bus, "snapclient")

        # Configuration du plugin
        self.config = config
        self.executable_path = config.get("executable_path", "/usr/bin/snapclient")
        self.blacklisted_servers = []

        # Créer les sous-composants
        self.process_manager = SnapclientProcess(self.executable_path)
        self.discovery = SnapclientDiscovery()
        self.connection_manager = SnapclientConnection(self.process_manager, self)
        self.monitor = SnapcastMonitor(self._handle_monitor_event)

        # État interne
        self.discovered_servers = [] # Liste interne des serveurs connus par le plugin

        # Verrou pour protéger l'accès concurrentiel à l'état de connexion
        self._connection_lock = asyncio.Lock()

    async def initialize(self) -> bool:
        """
        Initialise le plugin.
        """
        self.logger.info("Initialisation du plugin Snapclient")
        if not await self.process_manager.check_executable():
            self.logger.error(f"L'exécutable snapclient n'existe pas: {self.executable_path}")
            return False
        # Enregistrer le callback pour être notifié par la découverte
        self.discovery.register_callback(self._handle_server_discovery)
        self.logger.info("Plugin Snapclient initialisé et callback de découverte enregistré.")
        return True

    async def start(self) -> bool:
        """
        Démarre le plugin et lance la découverte des serveurs.
        """
        # Le verrou protège contre les appels concurrents à start/stop
        async with self._connection_lock:
            if self.is_active:
                self.logger.warning("Tentative de démarrage du plugin alors qu'il est déjà actif.")
                return True # Ou False selon la sémantique désirée

            try:
                self.logger.info("Démarrage du plugin Snapclient (sous verrou)")
                self.blacklisted_servers = [] # Réinitialiser la blacklist au démarrage
                self.is_active = True
                await self.transition_to_state(self.STATE_READY) # Passer à l'état prêt
                # Démarrer la découverte Zeroconf
                await self.discovery.start()
                self.logger.info("Découverte Zeroconf démarrée via le plugin Snapclient.")
                return True
            except Exception as e:
                self.logger.error(f"Erreur lors du démarrage du plugin: {str(e)}", exc_info=True)
                self.is_active = False # Assurer que l'état est cohérent en cas d'erreur
                await self.transition_to_state(self.STATE_INACTIVE) # Revenir à inactif
                return False

    async def stop(self) -> bool:
        """
        Arrête le plugin et désactive la source audio Snapclient.
        """
        async with self._connection_lock: # Protéger l'arrêt
            if not self.is_active:
                 self.logger.warning("Tentative d'arrêt du plugin alors qu'il est déjà inactif.")
                 return True

            try:
                self.logger.info("Arrêt du plugin Snapclient (sous verrou)")
                self.is_active = False
                await self.monitor.stop() # Arrêter le moniteur
                await self.discovery.stop() # Arrêter la découverte
                # Déconnecter et arrêter le processus (comportement par défaut de disconnect)
                await self.connection_manager.disconnect(stop_process=True)
                self.connection_manager.clear_pending_requests() # Nettoyer les requêtes en attente
                self.discovered_servers = [] # Vider la liste des serveurs connus
                await self.transition_to_state(self.STATE_INACTIVE) # Passer à l'état inactif
                self.logger.info("Plugin Snapclient arrêté avec succès.")
                return True
            except Exception as e:
                self.logger.error(f"Erreur lors de l'arrêt du plugin: {str(e)}", exc_info=True)
                # Même en cas d'erreur, on essaie de laisser l'état comme inactif
                await self.transition_to_state(self.STATE_INACTIVE)
                return False # Indiquer l'échec

    async def _handle_server_discovery(self, event_type, server):
        """
        Gère les événements de découverte des serveurs ('added', 'updated', 'removed').
        Réagit à 'added' ET 'updated' pour la connexion auto.
        """
        self.logger.debug(f"Callback _handle_server_discovery reçu: event='{event_type}', server={server.name}")
        try:
            # --- Gestion de la suppression (sous verrou) ---
            if event_type == "removed":
                async with self._connection_lock:
                     current_server_obj = self.connection_manager.current_server
                     # Si le serveur supprimé est celui auquel nous étions connectés
                     if (current_server_obj and current_server_obj.host == server.host):
                        self.logger.warning(f"[Discovery Locked] Serveur connecté {server.name} ({server.host}) a disparu (Zeroconf). Arrêt et nettoyage...")

                        # Publier l'événement de disparition
                        await self.event_bus.publish("snapclient_server_disappeared", {
                            "source": "snapclient", "host": server.host, "reason": "zeroconf_removed",
                            "plugin_state": self.STATE_READY, "connected": False, "deviceConnected": False
                        })

                        await self.monitor.stop() # Arrêter le moniteur immédiatement
                        await self.transition_to_state(self.STATE_READY) # Passer à Ready

                        # Déconnecter ET arrêter le processus local associé
                        self.logger.debug(f"[Discovery Locked] Appel disconnect(stop_process=True) pour {server.name} suite à remove Zeroconf.")
                        await self.connection_manager.disconnect(stop_process=True)
                        self.logger.info(f"[Discovery Locked] Déconnexion/arrêt suite à disparition Zeroconf terminée pour {server.name}.")

                     # Mettre à jour la liste interne des serveurs connus du plugin
                     self.discovered_servers = [s for s in self.discovered_servers if s.host != server.host]
                     self.logger.debug(f"[Discovery Locked] Serveur {server.name} retiré de la liste plugin.")
                return # Fin du traitement pour 'removed'

            # --- Gestion de l'ajout ou de la mise à jour (sous verrou) ---
            if event_type == "added" or event_type == "updated":
                 async with self._connection_lock:
                    # Mettre à jour la liste interne du plugin
                    found_index = -1
                    for i, s in enumerate(self.discovered_servers):
                        if s.host == server.host:
                            found_index = i
                            break

                    if found_index != -1:
                        if self.discovered_servers[found_index] != server:
                             self.logger.debug(f"[Discovery Locked] Mise à jour serveur dans la liste plugin: {server.name}")
                             self.discovered_servers[found_index] = server
                        # else: serveur identique déjà dans la liste plugin, on ne fait rien à la liste
                    else:
                        self.logger.debug(f"[Discovery Locked] Ajout serveur à la liste plugin: {server.name}")
                        self.discovered_servers.append(server)

                    # Publier l'événement de découverte/mise à jour vers le bus d'événements
                    await self.event_bus.publish("snapclient_server_discovered", {
                        "source": "snapclient",
                        "server": server.to_dict(),
                        "event_type": event_type # Peut être utile pour le frontend
                    })

                    # --- LOGIQUE DE CONNEXION AUTO ---
                    # Vérifier si on doit tenter une connexion automatique
                    should_connect = (
                        self.is_active and
                        self.current_state == self.STATE_READY and
                        not self.connection_manager.current_server and # Pas déjà connecté
                        server.host not in self.blacklisted_servers # Pas blacklisté
                    )

                    if should_connect:
                        self.logger.info(f"[Discovery Locked] Connexion auto déclenchée pour {server.name} (event: {event_type})")
                        success = await self.connection_manager.connect(server)

                        if success:
                            # Si la connexion réussit, mettre à jour l'état du plugin
                            await self.transition_to_state(self.STATE_CONNECTED, {
                                "connected": True, "deviceConnected": True,
                                "host": server.host, "device_name": server.name
                            })
                            self.logger.info(f"[Discovery Locked] Connexion auto réussie à {server.name}")
                            # Le moniteur est démarré DANS connect()
                        else:
                             self.logger.error(f"[Discovery Locked] Échec de la connexion automatique à {server.name}")
                    else:
                        # Log détaillé pour comprendre pourquoi la connexion auto n'a pas eu lieu
                        self.logger.debug(f"[Discovery Locked] Pas de connexion auto pour {server.name} (event: {event_type}). Conditions: "
                                          f"active={self.is_active}, state={self.current_state}, "
                                          f"current_srv={bool(self.connection_manager.current_server)}, "
                                          f"blacklisted={server.host in self.blacklisted_servers}")
            else:
                 # Cas où l'event_type serait inconnu (ne devrait pas arriver)
                 self.logger.warning(f"Type d'événement de découverte inconnu reçu: {event_type}")

        except Exception as e:
            self.logger.error(f"Erreur dans _handle_server_discovery (event={event_type}, server={server.name}): {str(e)}", exc_info=True)

    async def get_status(self) -> Dict[str, Any]:
        """ Récupère l'état actuel du plugin. """
        # Pas besoin de verrou pour une lecture simple
        try:
            connection_info = self.connection_manager.get_connection_info()
            process_info = await self.process_manager.get_process_info()
            status = {
                "source": "snapclient", "plugin_state": self.current_state,
                "is_active": self.is_active,
                "device_connected": connection_info.get("device_connected", False),
                # Retourner la liste interne au plugin, qui est mise à jour par la découverte
                "discovered_servers": [s.to_dict() for s in self.discovered_servers],
                "blacklisted_servers": self.blacklisted_servers,
                "process_info": process_info, "metadata": {}
            }
            if connection_info.get("device_connected"):
                status.update({
                    "connected": True, "deviceConnected": True,
                    "host": connection_info.get("host"),
                    "device_name": connection_info.get("device_name")
                })
                status["metadata"].update({
                    "device_name": connection_info.get("device_name"),
                    "host": connection_info.get("host")
                })
            return status
        except Exception as e:
            self.logger.error(f"Erreur lors de la récupération du statut: {str(e)}")
            return {"source": "snapclient", "plugin_state": self.current_state,
                    "is_active": self.is_active, "error": str(e)}

    async def get_connection_info(self) -> Dict[str, Any]:
        """ Récupère les informations de connexion. """
        # Lecture simple, pas besoin de verrou
        return self.connection_manager.get_connection_info()

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """ Traite une commande spécifique pour ce plugin via API. """
        # Le verrou est géré DANS les méthodes spécifiques si nécessaire
        try:
            self.logger.info(f"Traitement de la commande API: {command}")
            if not self.is_active and command not in ["get_status"]:
                self.logger.warning(f"Commande {command} reçue alors que le plugin est inactif.")
                return {"success": False, "error": "Plugin inactif", "inactive": True}

            if command == "connect":
                return await self._handle_connect_command(data)
            elif command == "disconnect":
                return await self._handle_disconnect_command(data)
            elif command == "discover":
                return await self._handle_discover_command(data)
            elif command == "restart":
                return await self._handle_restart_command(data)
            else:
                self.logger.warning(f"Commande API inconnue reçue: {command}")
                return {"success": False, "error": f"Commande inconnue: {command}"}
        except Exception as e:
            self.logger.error(f"Erreur lors du traitement de la commande API {command}: {str(e)}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def _handle_discover_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande API de découverte des serveurs."""
        # Pas besoin de verrou pour juste lister, utilise la liste de discovery
        try:
            # Utiliser directement la liste de discovery pour être à jour
            servers_from_discovery = await self.discovery.discover_servers()
            # Mettre à jour notre liste locale aussi
            self.discovered_servers = servers_from_discovery

            # Vérifier si on est connecté (sans verrou ici, lecture rapide)
            current_conn_info = self.connection_manager.get_connection_info()
            if current_conn_info["device_connected"] and not data.get("force", False):
                return {"success": True, "servers": [s.to_dict() for s in servers_from_discovery],
                        "count": len(servers_from_discovery), "message": f"Connecté à {current_conn_info['device_name']}",
                        "already_connected": True}

            # Filtrer les serveurs blacklistés pour l'affichage
            filtered_servers = [s for s in servers_from_discovery if s.host not in self.blacklisted_servers]
            return {"success": True, "servers": [s.to_dict() for s in filtered_servers],
                    "count": len(filtered_servers), "message": f"{len(filtered_servers)} serveurs trouvés (après filtrage blacklist)"}
        except Exception as e:
            self.logger.error(f"Erreur lors de la commande API discover: {str(e)}")
            return {"success": False, "error": f"Erreur lors de la découverte: {str(e)}"}

    async def _handle_connect_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande API de connexion à un serveur."""
        host = data.get("host")
        if not host: return {"success": False, "error": "Host is required"}

        async with self._connection_lock: # Verrouiller pour connecter
            if host in self.blacklisted_servers:
                self.logger.warning(f"[Connect Locked] Tentative de connexion manuelle au serveur blacklisté: {host}")
                return {"success": False, "error": f"Le serveur {host} a été déconnecté manuellement et est blacklisté.", "blacklisted": True}

            # Chercher dans la liste interne du plugin (qui devrait être à jour)
            server = next((s for s in self.discovered_servers if s.host == host), None)
            if not server:
                self.logger.warning(f"[Connect Locked] Serveur API {host} non trouvé dans la liste découverte, création virtuelle.")
                server = SnapclientServer(host=host, name=f"Snapserver ({host})")

            self.logger.info(f"[Connect Locked] Tentative de connexion manuelle (API) à {server.name}")
            success = await self.connection_manager.connect(server) # Tente la connexion

            if success:
                # Mettre à jour l'état du plugin
                await self.transition_to_state(self.STATE_CONNECTED, {
                    "connected": True, "deviceConnected": True,
                    "host": server.host, "device_name": server.name
                })
                self.logger.info(f"[Connect Locked] Connexion manuelle réussie à {server.name}")
            else:
                 self.logger.error(f"[Connect Locked] Échec connexion manuelle (API) à {server.name}")

            return {"success": success, "message": f"Tentative de connexion à {server.name}" if success else f"Échec connexion à {server.name}",
                    "server": server.to_dict() if success else None}

    async def _handle_disconnect_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande API de déconnexion."""
        async with self._connection_lock: # Verrouiller pour déconnecter
            current_server = self.connection_manager.current_server
            server_name_for_log = current_server.name if current_server else 'inconnu'
            server_host_for_blacklist = current_server.host if current_server else None

            self.logger.info(f"[Disconnect Locked] Tentative déconnexion manuelle (API) de {server_name_for_log}")

            # Ajouter à la blacklist lors d'une déconnexion manuelle explicite
            if server_host_for_blacklist and server_host_for_blacklist not in self.blacklisted_servers:
                self.blacklisted_servers.append(server_host_for_blacklist)
                self.logger.info(f"[Disconnect Locked] Serveur {server_host_for_blacklist} ajouté à blacklist (déco API).")

            # Déconnecter et arrêter le processus (comportement par défaut)
            success = await self.connection_manager.disconnect(stop_process=True)

            if success:
                # Passer à l'état Ready si la déconnexion réussit
                await self.transition_to_state(self.STATE_READY)
                self.logger.info(f"[Disconnect Locked] Déconnexion manuelle réussie pour {server_name_for_log}")
            else:
                # L'erreur est déjà logguée dans disconnect()
                self.logger.error(f"[Disconnect Locked] Échec lors de la déconnexion manuelle de {server_name_for_log}")
                # On essaie quand même de passer à Ready pour débloquer
                await self.transition_to_state(self.STATE_READY, {"error": "disconnect_failed"})


            return {"success": success, "message": f"Déconnexion de {server_name_for_log}",
                    "blacklisted": self.blacklisted_servers} # Renvoyer la liste mise à jour

    async def _handle_restart_command(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite la commande API de redémarrage du processus."""
        # Pas besoin de verrou ici, car restart() gère l'arrêt/démarrage interne
        host = None
        # Lire l'hôte actuel sans verrou
        current_conn_info = self.connection_manager.get_connection_info()
        if current_conn_info["device_connected"]:
            host = current_conn_info["host"]

        self.logger.info(f"Commande API de redémarrage du processus (hôte actuel: {host})")
        success = await self.process_manager.restart(host)

        # Si on redémarre alors qu'on était connecté, relancer le moniteur
        # Vérifier l'état actuel sans verrou (peut être légèrement décalé mais ok pour ce cas)
        if success and self.current_state == self.STATE_CONNECTED and host:
             self.logger.info("Redémarrage processus réussi, relance moniteur WebSocket pour {host}.")
             # Démarrer le moniteur à nouveau pour l'hôte qui était connecté
             await self.monitor.start(host)
        elif success:
             self.logger.info("Redémarrage processus réussi (plugin non connecté ou pas d'hôte).")

        return {"success": success, "message": "Processus snapclient redémarré" if success else "Échec redémarrage"}

    async def _handle_monitor_event(self, data: Dict[str, Any]) -> None:
        """ Traite les événements provenant du moniteur WebSocket. """
        event_type = data.get("event")
        host = data.get("host") # Host concerné par l'événement du moniteur

        if event_type == "monitor_connected":
            # Log et publication simple, pas besoin de verrou ici a priori
            self.logger.info(f"⚡ Moniteur WebSocket connecté au serveur: {host}")
            await self.event_bus.publish("snapclient_monitor_connected", {
                "source": "snapclient", "host": host, "plugin_state": self.current_state,
                "timestamp": time.time()
            })

        elif event_type == "monitor_disconnected":
            reason = data.get("reason", "raison inconnue")
            error = data.get("error", "")
            self.logger.warning(f"⚡ Moniteur WebSocket déconnecté du serveur: {host}: {reason} {error}")

            # Publier l'événement de déconnexion du moniteur
            await self.event_bus.publish("snapclient_monitor_disconnected", {
                "source": "snapclient", "host": host, "reason": reason, "error": error,
                "plugin_state": self.current_state, "connected": False, "deviceConnected": False,
                "timestamp": time.time()
            })

            # Utiliser le verrou pour gérer la déconnexion logique du plugin
            async with self._connection_lock:
                current_server_obj = self.connection_manager.current_server
                # Vérifier si la déconnexion concerne le serveur ACTIF DANS LE VERROU
                if self.is_active and current_server_obj and current_server_obj.host == host:
                    self.logger.warning(f"[Monitor Locked] Serveur {host} déconnecté (détecté par monitor). Gestion...")

                    # 1. Arrêter le moniteur (nettoie son état interne: host=None, is_connected=False)
                    self.logger.debug(f"[Monitor Locked] Arrêt explicite moniteur pour {host}.")
                    await self.monitor.stop()

                    # 2. Publier disparition serveur vers le bus d'événements
                    await self.event_bus.publish("snapclient_server_disappeared", {
                        "source": "snapclient", "host": host, "reason": f"monitor_disconnected ({reason})",
                        "plugin_state": self.STATE_READY, "connected": False, "deviceConnected": False,
                        "timestamp": time.time()
                    })

                    # 3. Transition de l'état interne du plugin vers Ready
                    await self.transition_to_state(self.STATE_READY, {
                        "connected": False, "deviceConnected": False,
                        "disconnection_reason": f"monitor_disconnected_{reason}",
                        "disconnected_host": host
                    })

                    # 4. Déconnecter le gestionnaire ET arrêter le processus snapclient
                    #    Force un état propre pour la prochaine connexion automatique.
                    self.logger.debug(f"[Monitor Locked] Appel disconnect(stop_process=True) pour {host} suite à déconnexion monitor.")
                    await self.connection_manager.disconnect(stop_process=True)

                    self.logger.info(f"[Monitor Locked] Gestion déconnexion moniteur pour {host} terminée (processus arrêté). Plugin prêt.")
                else:
                     # Si le moniteur se déconnecte mais que ce n'était pas/plus notre serveur actif, on logue juste.
                     self.logger.debug(f"[Monitor Locked] Moniteur déconnecté pour {host}, mais pas/plus le serveur actif ({current_server_obj.host if current_server_obj else 'aucun'}). Aucune action de déconnexion du plugin nécessaire.")

        elif event_type == "server_event":
             # Traitement normal des événements reçus du serveur Snapcast via le moniteur
             server_data = data.get("data", {})
             # Optionnel: logguer seulement certains types d'événements serveur pour éviter le bruit
             # if server_data.get("method") == "Client.OnVolumeChanged": etc.
             self.logger.debug(f"Événement reçu du serveur Snapcast {host}")
             await self.event_bus.publish("snapclient_server_event", {
                 "source": "snapclient",
                 "host": host,
                 "event_data": server_data,
                 "timestamp": data.get("timestamp", time.time())
             })