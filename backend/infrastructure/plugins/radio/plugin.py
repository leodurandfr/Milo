"""
Plugin Radio pour Milo - Streaming web radio via mpv
"""
import asyncio
import logging
from typing import Dict, Any, Optional

from backend.infrastructure.plugins.base import UnifiedAudioPlugin
from backend.domain.audio_state import PluginState
from backend.infrastructure.plugins.radio.mpv_controller import MpvController
from backend.infrastructure.plugins.radio.radio_browser_api import RadioBrowserAPI
from backend.infrastructure.plugins.radio.station_manager import StationManager


class RadioPlugin(UnifiedAudioPlugin):
    """
    Plugin Radio pour Milo

    Suit le pattern des autres plugins (Librespot, Bluetooth, ROC):
    - Contrôle un service systemd externe (milo-radio.service avec mpv)
    - Gère les métadonnées (station actuelle, titre du stream)
    - Support multiroom et equalizer via routing service

    États:
        INACTIVE → service arrêté
        READY → service démarré (mpv en idle)
        CONNECTED → station en cours de lecture
    """

    def __init__(self, config: Dict[str, Any], state_machine=None, settings_service=None):
        super().__init__("radio", state_machine)

        self.config = config
        self.service_name = config.get("service_name", "milo-radio.service")
        self.ipc_socket_path = config.get("ipc_socket", "/tmp/milo-radio-ipc.sock")
        self.settings_service = settings_service

        # Composants
        self.mpv = MpvController(self.ipc_socket_path)
        self.station_manager = StationManager(settings_service, state_machine)
        # Note: station_manager est passé à RadioBrowserAPI pour fusionner stations personnalisées
        self.radio_api = RadioBrowserAPI(cache_duration_minutes=60, station_manager=self.station_manager)

        # État actuel
        self.current_station: Optional[Dict[str, Any]] = None
        self._is_playing = False
        self._is_buffering = False
        self._metadata = {}
        self._current_device = "milo_radio"

        # Monitoring task
        self._monitor_task: Optional[asyncio.Task] = None
        self._stopping = False

    async def _do_initialize(self) -> bool:
        """Initialisation du plugin Radio"""
        try:
            # Vérifier que le service existe
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "list-unit-files", self.service_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await proc.communicate()

            if proc.returncode != 0 or self.service_name not in stdout.decode():
                raise RuntimeError(f"Service {self.service_name} non trouvé")

            # Vérifier que mpv est installé
            proc = await asyncio.create_subprocess_exec(
                "which", "mpv",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.wait()

            if proc.returncode != 0:
                raise RuntimeError("mpv n'est pas installé")

            # Initialiser les composants
            await self.station_manager.initialize()

            self.logger.info("Plugin Radio initialisé")
            return True

        except Exception as e:
            self.logger.error(f"Erreur initialisation Radio: {e}")
            return False

    async def _do_start(self) -> bool:
        """Démarrage du service Radio"""
        try:
            # Démarrer le service systemd (mpv)
            if not await self.control_service(self.service_name, "start"):
                return False

            # Attendre que le service soit prêt
            await asyncio.sleep(1)

            # Vérifier que le service est actif
            is_active = await self.service_manager.is_active(self.service_name)
            if not is_active:
                self.logger.error("Service mpv démarré mais pas actif")
                return False

            # Connecter au socket IPC de mpv
            if not await self.mpv.connect(max_retries=10, retry_delay=0.5):
                self.logger.error("Impossible de se connecter au socket IPC mpv")
                return False

            # Démarrer la surveillance de l'état mpv
            self._stopping = False
            self._monitor_task = asyncio.create_task(self._monitor_playback())

            # Notifier état READY
            await self.notify_state_change(PluginState.READY, {
                "ready": True,
                "mpv_connected": self.mpv.is_connected
            })

            self.logger.info("Service Radio démarré et prêt")
            return True

        except Exception as e:
            self.logger.error(f"Erreur démarrage Radio: {e}")
            return False

    async def restart(self) -> bool:
        """Redémarre le service mpv"""
        try:
            self.logger.info("Redémarrage du service Radio")

            # Arrêter la surveillance
            self._stopping = True
            if self._monitor_task:
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass

            # Déconnecter mpv
            await self.mpv.disconnect()

            # Reset état
            self.current_station = None
            self._is_playing = False
            self._is_buffering = False
            self._metadata = {}

            # Redémarrer le service
            success = await self.control_service(self.service_name, "restart")

            if not success:
                self.logger.error(f"Échec redémarrage service {self.service_name}")
                return False

            # Attendre que le service soit prêt
            await asyncio.sleep(1)

            # Reconnexion IPC
            if not await self.mpv.connect(max_retries=10, retry_delay=0.5):
                self.logger.error("Impossible de se reconnecter au socket IPC après restart")
                return False

            # Redémarrer la surveillance
            self._stopping = False
            self._monitor_task = asyncio.create_task(self._monitor_playback())

            # Notifier état READY
            async def notify_ready_state():
                await asyncio.sleep(0.1)
                await self.notify_state_change(PluginState.READY, {"ready": True})

            asyncio.create_task(notify_ready_state())

            self.logger.info("Service Radio redémarré")
            return True

        except Exception as e:
            self.logger.error(f"Erreur redémarrage Radio: {e}")
            return False

    async def stop(self) -> bool:
        """Arrête le plugin Radio"""
        try:
            self.logger.info("Arrêt du plugin Radio")

            # Arrêter la surveillance
            self._stopping = True
            if self._monitor_task:
                self._monitor_task.cancel()
                try:
                    await self._monitor_task
                except asyncio.CancelledError:
                    pass

            # Arrêter la lecture
            if self._is_playing:
                await self.mpv.stop()

            # Déconnecter mpv
            await self.mpv.disconnect()

            # Fermer l'API Radio Browser
            await self.radio_api.close()

            # Arrêter le service
            await self.control_service(self.service_name, "stop")

            # Reset état
            self.current_station = None
            self._is_playing = False
            self._is_buffering = False
            self._metadata = {}

            await self.notify_state_change(PluginState.INACTIVE)
            self.logger.info("Plugin Radio arrêté")
            return True

        except Exception as e:
            self.logger.error(f"Erreur arrêt Radio: {e}")
            return False

    async def _monitor_playback(self) -> None:
        """
        Surveille l'état de lecture de mpv

        Vérifie périodiquement l'état et les métadonnées
        """
        try:
            while not self._stopping:
                try:
                    # Vérifier l'état de lecture
                    is_playing = await self.mpv.is_playing()

                    # DEBUG: Logger l'état brut de mpv (seulement pendant buffering)
                    if self.current_station and self._is_buffering:
                        playback_time = await self.mpv.get_property("playback-time")
                        self.logger.info(f"🔍 Monitor: is_playing={is_playing}, playback_time={playback_time}")

                    # Mettre à jour l'état immédiatement (pas de debouncing avec core-idle)
                    if is_playing != self._is_playing:
                        self._is_playing = is_playing
                        self.logger.info(f"État lecture changé: {'playing' if is_playing else 'stopped'}")

                        # Si on passe à playing et qu'on était en buffering, terminer le buffering
                        if is_playing and self._is_buffering:
                            self._is_buffering = False
                            self.logger.info("✅ Buffering terminé, stream en lecture")

                    # Le plugin_state est CONNECTED tant qu'une station est chargée
                    # isPlaying dans les métadonnées indique l'état réel de lecture
                    if self.current_station:
                        await self._update_metadata()

                        # Broadcast à chaque update pour synchroniser tous les clients
                        await self.notify_state_change(PluginState.CONNECTED, self._metadata)

                    # Polling rapide pour détecter rapidement le début de la lecture
                    await asyncio.sleep(0.5)  # Vérifier toutes les 0.5 secondes

                except Exception as e:
                    self.logger.error(f"Erreur surveillance lecture: {e}")
                    await asyncio.sleep(5)

        except asyncio.CancelledError:
            self.logger.debug("Surveillance lecture annulée")
        except Exception as e:
            self.logger.error(f"Erreur critique surveillance: {e}")

    async def _update_metadata(self) -> None:
        """Met à jour les métadonnées depuis mpv"""
        try:
            # Vérifier que current_station est un dict AVANT d'accéder aux propriétés
            if self.current_station and not isinstance(self.current_station, dict):
                self.logger.error(f"current_station n'est pas un dict: {type(self.current_station)}, valeur: {self.current_station}")
                self.current_station = None
                self._metadata = {}
                return

            self._metadata = {
                "station_id": self.current_station.get('id') if self.current_station else None,
                "station_name": self.current_station.get('name') if self.current_station else None,
                "station_url": self.current_station.get('url') if self.current_station else None,
                "country": self.current_station.get('country') if self.current_station else None,
                "genre": self.current_station.get('genre') if self.current_station else None,
                "favicon": self.current_station.get('favicon') if self.current_station else None,
                "bitrate": self.current_station.get('bitrate') if self.current_station else None,
                "codec": self.current_station.get('codec') if self.current_station else None,
                "is_favorite": self.station_manager.is_favorite(
                    self.current_station.get('id')
                ) if self.current_station else False,
                "is_playing": self._is_playing,
                "buffering": self._is_buffering
            }

        except Exception as e:
            self.logger.error(f"Erreur mise à jour métadonnées: {e}", exc_info=True)

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite les commandes du plugin

        Commandes supportées:
            - play_station: Joue une station par ID
            - stop_playback: Arrête la lecture
            - add_favorite: Ajoute aux favoris
            - remove_favorite: Retire des favoris
            - mark_broken: Marque une station comme cassée
            - reset_broken: Reset les stations cassées
        """
        try:
            if command == "play_station":
                return await self._handle_play_station(data)

            elif command == "stop_playback":
                return await self._handle_stop_playback()

            elif command == "add_favorite":
                return await self._handle_add_favorite(data)

            elif command == "remove_favorite":
                return await self._handle_remove_favorite(data)

            elif command == "mark_broken":
                return await self._handle_mark_broken(data)

            elif command == "reset_broken":
                return await self._handle_reset_broken()

            return self.format_response(False, error=f"Commande non supportée: {command}")

        except Exception as e:
            self.logger.error(f"Erreur traitement commande {command}: {e}")
            return self.format_response(False, error=str(e))

    async def _handle_play_station(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Joue une station radio"""
        station_id = data.get('station_id')
        if not station_id:
            self.logger.error("❌ Commande play_station sans station_id")
            return self.format_response(False, error="station_id requis")

        try:
            # Récupérer la station
            station = await self.radio_api.get_station_by_id(station_id)
            if not station:
                self.logger.error(f"❌ Station introuvable: {station_id}")
                return self.format_response(False, error=f"Station {station_id} introuvable")

            self.logger.info(f"📻 Lecture de la station: {station['name']} (URL: {station['url']})")

            # Incrémenter compteur Radio Browser
            asyncio.create_task(self.radio_api.increment_station_clicks(station_id))

            # Mettre à jour l'état : buffering en cours
            self.current_station = station
            self._is_playing = False
            self._is_buffering = True
            await self._update_metadata()

            # Notifier immédiatement l'état de buffering
            await self.notify_state_change(PluginState.CONNECTED, self._metadata)

            # Charger le stream dans mpv
            success = await self.mpv.load_stream(station['url'])

            if not success:
                # Marquer comme cassée et reset buffering
                self._is_buffering = False
                self.current_station = None
                await self.station_manager.mark_as_broken(station_id)
                self.logger.error(f"❌ Impossible de charger le stream: {station['name']} ({station['url']})")
                return self.format_response(
                    False,
                    error=f"Impossible de charger le stream {station['name']}"
                )

            # Le buffering continuera jusqu'à ce que _monitor_playback détecte is_playing=true
            # On ne met pas _is_playing = True ici, on laisse _monitor_playback le faire

            return self.format_response(
                True,
                message=f"Chargement de {station['name']}",
                station=station
            )

        except Exception as e:
            self.logger.error(f"Erreur lecture station: {e}")
            self._is_buffering = False
            return self.format_response(False, error=str(e))

    async def _handle_stop_playback(self) -> Dict[str, Any]:
        """Arrête la lecture"""
        try:
            # Toujours arrêter mpv (ignore l'erreur si déjà arrêté)
            await self.mpv.stop()

            # Toujours reset l'état, même si mpv retourne une erreur
            # (cas où on appelle stop() alors qu'on est déjà arrêté)
            self.current_station = None
            self._is_playing = False
            self._is_buffering = False

            # Créer des métadonnées avec is_playing: false pour notifier le frontend
            self._metadata = {
                "is_playing": False,
                "buffering": False,
                "ready": True
            }

            await self.notify_state_change(PluginState.READY, self._metadata)

            return self.format_response(
                True,
                message="Lecture arrêtée"
            )

        except Exception as e:
            return self.format_response(False, error=str(e))

    async def _handle_add_favorite(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Ajoute une station aux favoris et sauvegarde ses métadonnées pour le cache"""
        station_id = data.get('station_id')
        if not station_id:
            return self.format_response(False, error="station_id requis")

        # Ajouter aux favoris
        success = await self.station_manager.add_favorite(station_id)

        if success:
            # OPTIMISATION: Enrichir le cache avec les métadonnées de la station
            # pour éviter les appels API futurs lors du chargement des favoris
            try:
                # Utiliser l'objet station fourni (avec favicon dédupliqué) si disponible
                station = data.get('station')

                # Sinon, récupérer les métadonnées depuis l'API (fallback)
                if not station:
                    self.logger.debug(f"⚠️ Pas d'objet station fourni, récupération depuis API pour {station_id}")
                    station = await self.radio_api.get_station_by_id(station_id)
                else:
                    self.logger.debug(f"✅ Utilisation de l'objet station fourni (favicon dédupliqué) pour {station_id}")

                if station and not station_id.startswith("custom_"):
                    # Sauvegarder dans station_images pour le cache
                    # (les custom stations sont déjà dans custom_stations)
                    # Le favicon est soit dédupliqué (fourni par frontend), soit depuis l'API
                    await self.station_manager.cache_station_metadata(
                        station_id=station_id,
                        station_name=station.get('name', ''),
                        favicon=station.get('favicon', ''),  # Favicon dédupliqué si fourni par frontend
                        country=station.get('country', ''),
                        genre=station.get('genre', '')
                    )
                    self.logger.info(f"✅ Métadonnées cachées pour {station.get('name')} ({station_id})")
            except Exception as e:
                # Ne pas faire échouer l'ajout du favori si le cache échoue
                self.logger.warning(f"⚠️ Impossible de cacher métadonnées pour {station_id}: {e}")

        return self.format_response(
            success,
            message="Station ajoutée aux favoris" if success else "Échec ajout favori"
        )

    async def _handle_remove_favorite(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Retire une station des favoris"""
        station_id = data.get('station_id')
        if not station_id:
            return self.format_response(False, error="station_id requis")

        success = await self.station_manager.remove_favorite(station_id)
        return self.format_response(
            success,
            message="Station retirée des favoris" if success else "Échec retrait favori"
        )

    async def _handle_mark_broken(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Marque une station comme cassée"""
        station_id = data.get('station_id')
        if not station_id:
            return self.format_response(False, error="station_id requis")

        success = await self.station_manager.mark_as_broken(station_id)
        return self.format_response(
            success,
            message="Station marquée comme cassée" if success else "Échec marquage"
        )

    async def _handle_reset_broken(self) -> Dict[str, Any]:
        """Reset les stations cassées"""
        success = await self.station_manager.reset_broken_stations()
        return self.format_response(
            success,
            message="Stations cassées réinitialisées" if success else "Échec reset"
        )

    async def get_status(self) -> Dict[str, Any]:
        """Récupère l'état actuel du plugin"""
        try:
            service_status = await self.service_manager.get_status(self.service_name)
            mpv_status = await self.mpv.get_status()
            stats = self.station_manager.get_stats()

            return {
                "service_active": service_status.get("active", False),
                "mpv_connected": mpv_status.get("connected", False),
                "is_playing": self._is_playing,
                "current_station": self.current_station,
                "metadata": self._metadata,
                "current_device": self._current_device,
                "favorites_count": stats['favorites_count'],
                "broken_stations_count": stats['broken_stations_count']
            }

        except Exception as e:
            self.logger.error(f"Erreur status: {e}")
            return {
                "service_active": False,
                "mpv_connected": False,
                "is_playing": False,
                "current_station": None,
                "metadata": {},
                "current_device": self._current_device,
                "error": str(e)
            }

    async def get_initial_state(self) -> Dict[str, Any]:
        """État initial pour les WebSockets"""
        return await self.get_status()
