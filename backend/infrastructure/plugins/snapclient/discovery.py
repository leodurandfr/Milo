# backend/infrastructure/plugins/snapclient/discovery.py
"""
Découverte des serveurs Snapcast sur le réseau - Version simplifiée.
"""
import asyncio
import logging
import socket
import queue  # Standard queue pour communication inter-thread
from typing import List, Optional, Dict, Any, Set

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from zeroconf.asyncio import AsyncServiceInfo

from backend.infrastructure.plugins.snapclient.models import SnapclientServer

class SnapclientDiscoveryListener(ServiceListener):
    """
    Détecte les serveurs Snapcast via Zeroconf.
    """

    def __init__(self, event_queue, loop):
        """
        Initialise un listener pour la découverte des serveurs Snapcast.

        Args:
            event_queue: Queue standard pour les événements de découverte
            loop: Boucle d'événements principale
        """
        self.event_queue = event_queue
        self.loop = loop
        self.logger = logging.getLogger("plugin.snapclient.discovery.listener")
        self.own_hostname = socket.gethostname().lower()

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """
        Appelé quand un service est découvert.
        Met l'événement dans une queue synchrone.
        """
        self.logger.debug(f"Zeroconf service découvert: {name}")
        # Utiliser une queue standard au lieu d'une queue asyncio
        self.event_queue.put(("add", type_, name, zc))

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """
        Appelé quand un service est mis à jour.
        """
        self.logger.debug(f"Zeroconf service mis à jour: {name}")
        self.event_queue.put(("update", type_, name, zc))

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        """
        Appelé quand un service est supprimé.
        """
        self.logger.debug(f"Zeroconf service supprimé: {name}")
        self.event_queue.put(("remove", type_, name, zc))

class SnapclientDiscovery:
    """
    Détecte les serveurs Snapcast sur le réseau via Zeroconf.
    Version simplifiée.
    """

    def __init__(self):
        self.logger = logging.getLogger("plugin.snapclient.discovery")
        self.zeroconf = None
        self.browser = None
        # Utiliser une queue standard au lieu d'une queue asyncio
        self.event_queue = queue.Queue()
        self.event_processor_task = None
        self.discovered_servers: Set[SnapclientServer] = set()
        self.server_callbacks = []
        self.own_hostname = socket.gethostname().lower()

    async def start(self):
        """Démarre la découverte des serveurs Snapcast."""
        if self.zeroconf:
            self.logger.debug("Discovery déjà démarrée.")
            return True

        self.logger.info("Démarrage de la découverte Zeroconf des serveurs Snapcast")
        try:
            # Stocker la boucle d'événements principale
            loop = asyncio.get_running_loop()

            # Créer l'instance Zeroconf
            self.zeroconf = Zeroconf()
            self.listener = SnapclientDiscoveryListener(self.event_queue, loop)
            self.browser = ServiceBrowser(
                self.zeroconf,
                "_snapcast._tcp.local.",
                self.listener
            )

            # Démarrer le processeur d'événements
            if not self.event_processor_task or self.event_processor_task.done():
                self.event_processor_task = asyncio.create_task(self._process_events())

            self.logger.info("Découverte Zeroconf démarrée avec succès")
            return True

        except Exception as e:
            self.logger.error(f"Erreur lors du démarrage de la découverte Zeroconf: {str(e)}")
            await self.stop()
            return False

    async def stop(self):
        """Arrête la découverte des serveurs Snapcast."""
        self.logger.info("Arrêt de la découverte Zeroconf")

        if self.event_processor_task and not self.event_processor_task.done():
            self.event_processor_task.cancel()
            try:
                await self.event_processor_task
            except asyncio.CancelledError:
                pass
            self.event_processor_task = None

        if self.browser:
            try: self.browser.cancel() # Peut lancer une exception si déjà fermé
            except Exception as e: self.logger.debug(f"Erreur (ignorée) lors de browser.cancel: {e}")
            self.browser = None

        if self.zeroconf:
            try: self.zeroconf.close()
            except Exception as e: self.logger.debug(f"Erreur (ignorée) lors de zeroconf.close: {e}")
            self.zeroconf = None

        # Vider la queue au cas où
        while not self.event_queue.empty():
            try: self.event_queue.get_nowait()
            except queue.Empty: break
            self.event_queue.task_done()

        self.logger.info("Découverte Zeroconf arrêtée")

    async def _process_events(self):
        """
        Traite les événements de la queue standard en arrière-plan.
        Cette fonction s'exécute dans la boucle d'événements asyncio.
        """
        try:
            while True:
                # Traiter tous les événements en attente avant de dormir
                processed_count = 0
                try:
                    while not self.event_queue.empty():
                        event_type, service_type, name, zc = self.event_queue.get_nowait()
                        processed_count += 1
                        self.logger.debug(f"Traitement événement: {event_type} pour {name}")
                        try:
                            if event_type in ("add", "update"):
                                # Utiliser AsyncServiceInfo au lieu de get_service_info
                                service_info = AsyncServiceInfo(service_type, name)
                                await service_info.async_request(zc, timeout=2000) # Demande les infos

                                # Vérifier si on a reçu des informations valides
                                if not service_info.addresses or not service_info.server:
                                    self.logger.warning(f"Infos incomplètes reçues pour {name} (adresses: {service_info.addresses}, server: {service_info.server}), service ignoré.")
                                    continue

                                server = self._service_info_to_server(service_info)
                                if server:
                                    await self._process_server(server, event_type) # Passer event_type

                            elif event_type == "remove":
                                # Traiter la suppression du serveur
                                hostname = name.split(".")[0].lower()
                                servers_to_remove = [s for s in self.discovered_servers
                                                   if s.name.lower() == hostname]

                                if not servers_to_remove:
                                     self.logger.debug(f"Demande de suppression pour {name}, mais pas/plus trouvé dans la liste interne.")

                                for server in servers_to_remove:
                                    # Supprimer de la liste interne
                                    self.discovered_servers.discard(server)
                                    self.logger.info(f"Serveur supprimé de la liste interne: {server.name} ({server.host})")
                                    # Notifier les callbacks
                                    for callback in self.server_callbacks:
                                        try:
                                            # Utiliser create_task pour ne pas bloquer
                                            asyncio.create_task(callback("removed", server))
                                            self.logger.debug(f"Callback 'removed' appelé pour {server.name}")
                                        except Exception as e:
                                            self.logger.error(f"Erreur dans callback 'removed': {e}")

                        except Exception as e:
                            self.logger.error(f"Erreur lors du traitement interne d'un événement ({event_type}, {name}): {str(e)}", exc_info=True)
                        finally:
                            self.event_queue.task_done() # Marquer la tâche comme terminée

                except queue.Empty:
                    pass # Normal si la queue est vide

                # Attendre seulement si on n'a rien traité
                if processed_count == 0:
                    await asyncio.sleep(0.1)
                # Sinon, revérifier immédiatement s'il y a d'autres événements

        except asyncio.CancelledError:
            self.logger.debug("Processeur d'événements arrêté")
        except Exception as e:
            self.logger.error(f"Erreur fatale dans le processeur d'événements: {str(e)}", exc_info=True)

    def _service_info_to_server(self, info: AsyncServiceInfo) -> Optional[SnapclientServer]:
        """
        Convertit les informations de service en objet SnapclientServer.
        """
        try:
            # Extraire les adresses
            addresses = info.parsed_addresses()
            if not addresses:
                self.logger.warning(f"Aucune adresse IP parsée pour {info.name}")
                return None

            # Prioriser IPv4 non link-local (169.254)
            host = next((addr for addr in addresses if ':' not in addr and not addr.startswith('169.254.')), None)
            if not host: # Fallback vers la première adresse IP si pas d'IPv4 convenable
                 host = addresses[0]
                 self.logger.debug(f"Pas d'IPv4 préférée trouvée pour {info.name}, utilisation de {host}")

            # Extraire le nom d'hôte
            name = info.server.split('.')[0] if info.server else info.name.split('.')[0]

            # Extraire le port
            port = info.port

            # Vérifier si c'est nous-même ou un nom réservé
            if name.lower() == self.own_hostname:
                self.logger.debug(f"Ignoré serveur local via hostname: {name}")
                return None
            if 'oakos' in name.lower(): # Ignorer aussi si le nom contient oakos
                 self.logger.debug(f"Ignoré serveur contenant 'oakos': {name}")
                 return None

            return SnapclientServer(host=host, name=name, port=port)

        except Exception as e:
            self.logger.error(f"Erreur lors de la conversion des informations de service pour {info.name}: {str(e)}")
            return None

    async def _process_server(self, server: SnapclientServer, event_type: str):
        """
        Traite un serveur découvert ou mis à jour. Appelle TOUJOURS le callback
        pour 'add' et 'update'.

        Args:
            server: Le serveur Snapcast
            event_type: Type d'événement ('add' ou 'update')
        """
        existing = next((s for s in self.discovered_servers if s.host == server.host), None)
        callback_type = None
        notify = False

        if existing:
            # C'est une mise à jour (ou un 'add' pour un serveur déjà vu)
            # Comparaison d'objets dataclass (vérifie toutes les valeurs)
            if existing != server:
                self.discovered_servers.discard(existing)
                self.discovered_servers.add(server)
                self.logger.info(f"Serveur mis à jour dans la liste interne: {server.name} ({server.host}) -> {server.port}")
                callback_type = "updated"
                notify = True
            else:
                # Serveur existant et identique, mais on notifie quand même (type 'update')
                # Cela permet au plugin de réagir même si rien n'a changé ici
                self.logger.debug(f"Serveur vu à nouveau via Zeroconf (update, sans changement de détails): {server.name} ({server.host})")
                callback_type = "updated" # On utilise 'updated' même si pas de changement
                notify = True # <<< FORCER LA NOTIFICATION pour 'updated'
        else:
            # Nouveau serveur (jamais vu auparavant par ce module de découverte)
            self.discovered_servers.add(server)
            self.logger.info(f"Nouveau serveur ajouté à la liste interne: {server.name} ({server.host})")
            callback_type = "added"
            notify = True

        # Appeler les callbacks si une notification est nécessaire
        if notify and callback_type:
            self.logger.debug(f"Notification du plugin nécessaire: event='{callback_type}', server={server.name}")
            for callback in self.server_callbacks:
                try:
                    # Utiliser create_task pour ne pas bloquer le processeur d'événements
                    # pendant l'exécution potentiellement longue du callback du plugin
                    asyncio.create_task(callback(callback_type, server))
                    self.logger.debug(f"Tâche créée pour appeler le callback '{callback_type}' pour {server.name}")
                except Exception as e:
                    self.logger.error(f"Erreur lors de la création de la tâche pour le callback '{callback_type}': {e}")
        elif not notify:
             self.logger.debug(f"Aucune notification du plugin nécessaire pour {server.name} (event: {event_type})")


    def register_callback(self, callback):
        """
        Enregistre un callback qui sera appelé quand un serveur est découvert/mis à jour/supprimé.
        Le callback doit être une coroutine prenant (event_type, server) comme arguments.

        Args:
            callback: Coroutine à appeler
        """
        if callback not in self.server_callbacks:
            self.server_callbacks.append(callback)
            # Utiliser repr(callback) ou callback.__name__ pour un log plus utile
            self.logger.debug(f"Callback enregistré: {repr(callback)}")

    def unregister_callback(self, callback):
        """
        Supprime un callback précédemment enregistré.

        Args:
            callback: Callback à supprimer
        """
        if callback in self.server_callbacks:
            self.server_callbacks.remove(callback)
            self.logger.debug(f"Callback désenregistré: {repr(callback)}")

    async def discover_servers(self) -> List[SnapclientServer]:
        """
        Découvre les serveurs Snapcast via Zeroconf (retourne la liste actuelle).

        Returns:
            List[SnapclientServer]: Liste des serveurs découverts
        """
        # Démarrer la découverte si ce n'est pas déjà fait
        if not self.zeroconf:
            await self.start()
            # Attendre un peu pour laisser la découverte initiale se faire
            await asyncio.sleep(0.5)

        # Retourner une copie de la liste actuelle des serveurs
        return list(self.discovered_servers)