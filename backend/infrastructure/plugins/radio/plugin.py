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
        self.radio_api = RadioBrowserAPI(cache_duration_minutes=60)
        self.station_manager = StationManager(settings_service)

        # État actuel
        self.current_station: Optional[Dict[str, Any]] = None
        self._is_playing = False
        self._metadata = {}
        self._current_device = "milo_radio"

        # Monitoring task
        self._monitor_task: Optional[asyncio.Task] = None
        self._stopping = False

        # Debouncing pour éviter les changements d'état trop rapides
        self._stable_state_count = 0
        self._last_detected_state = False
        self._state_stability_threshold = 4  # Nombre de vérifications consécutives avant changement (8 secondes)

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

            # Précharger les stations en arrière-plan (non-bloquant)
            asyncio.create_task(self._preload_stations())

            self.logger.info("Plugin Radio initialisé")
            return True

        except Exception as e:
            self.logger.error(f"Erreur initialisation Radio: {e}")
            return False

    async def _preload_stations(self) -> None:
        """Précharge les stations en arrière-plan"""
        try:
            self.logger.info("Préchargement des stations...")
            await self.radio_api.load_all_stations()
            self.logger.info("Stations préchargées avec succès")
        except Exception as e:
            self.logger.error(f"Erreur préchargement stations: {e}")

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
            self._metadata = {}
            self._stable_state_count = 0
            self._last_detected_state = False

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
            self._metadata = {}
            self._stable_state_count = 0
            self._last_detected_state = False

            await self.notify_state_change(PluginState.INACTIVE)
            self.logger.info("Plugin Radio arrêté")
            return True

        except Exception as e:
            self.logger.error(f"Erreur arrêt Radio: {e}")
            return False

    async def _monitor_playback(self) -> None:
        """
        Surveille l'état de lecture de mpv

        Vérifie périodiquement l'état et les métadonnées avec debouncing
        pour éviter les changements d'état trop rapides
        """
        try:
            while not self._stopping:
                try:
                    # Vérifier l'état de lecture
                    is_playing = await self.mpv.is_playing()

                    # Debouncing: compter les détections stables
                    if is_playing == self._last_detected_state:
                        self._stable_state_count += 1
                    else:
                        # État a changé, reset le compteur
                        self._last_detected_state = is_playing
                        self._stable_state_count = 1

                    # Mettre à jour l'état de lecture si stable
                    state_changed = False
                    if self._stable_state_count >= self._state_stability_threshold:
                        if is_playing != self._is_playing:
                            # Si une station est chargée et qu'on détecte "stopped",
                            # c'est probablement du buffering, pas un arrêt réel
                            # On ignore ce changement sauf si current_station est None
                            if not is_playing and self.current_station:
                                self.logger.debug("Pause temporaire détectée (buffering), ignorée")
                            else:
                                self._is_playing = is_playing
                                state_changed = True
                                self.logger.info(f"État lecture changé: {'playing' if is_playing else 'stopped'}")

                    # Le plugin_state est CONNECTED tant qu'une station est chargée
                    # isPlaying dans les métadonnées indique l'état réel de lecture
                    if self.current_station:
                        await self._update_metadata()

                        # Ne notifier que si l'état de lecture a changé de manière significative
                        if state_changed:
                            # Toujours envoyer CONNECTED si une station est sélectionnée
                            # L'état de lecture réel est dans metadata.is_playing
                            await self.notify_state_change(PluginState.CONNECTED, self._metadata)

                    await asyncio.sleep(2)  # Vérifier toutes les 2 secondes

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
            media_title = await self.mpv.get_media_title()
            mpv_metadata = await self.mpv.get_metadata()

            # Vérifier que current_station est un dict
            if self.current_station and not isinstance(self.current_station, dict):
                self.logger.warning(f"current_station n'est pas un dict: {type(self.current_station)}")
                self.current_station = None

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
                "media_title": media_title,  # Titre du morceau en cours (icy-title)
                "mpv_metadata": mpv_metadata
            }

        except Exception as e:
            self.logger.error(f"Erreur mise à jour métadonnées: {e}")

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
            return self.format_response(False, error="station_id requis")

        try:
            # Récupérer la station
            station = await self.radio_api.get_station_by_id(station_id)
            if not station:
                return self.format_response(False, error=f"Station {station_id} introuvable")

            # Incrémenter compteur Radio Browser
            asyncio.create_task(self.radio_api.increment_station_clicks(station_id))

            # Charger le stream dans mpv
            success = await self.mpv.load_stream(station['url'])

            if not success:
                # Marquer comme cassée
                await self.station_manager.mark_as_broken(station_id)
                return self.format_response(
                    False,
                    error=f"Impossible de charger le stream {station['name']}"
                )

            # Mettre à jour l'état
            self.current_station = station
            self._is_playing = True

            # Attendre un peu pour avoir les métadonnées
            await asyncio.sleep(0.5)
            await self._update_metadata()

            # Notifier changement d'état
            await self.notify_state_change(PluginState.CONNECTED, self._metadata)

            return self.format_response(
                True,
                message=f"Lecture de {station['name']}",
                station=station
            )

        except Exception as e:
            self.logger.error(f"Erreur lecture station: {e}")
            return self.format_response(False, error=str(e))

    async def _handle_stop_playback(self) -> Dict[str, Any]:
        """Arrête la lecture"""
        try:
            success = await self.mpv.stop()

            if success:
                self.current_station = None
                self._is_playing = False
                self._metadata = {}
                await self.notify_state_change(PluginState.READY, {"ready": True})

            return self.format_response(
                success,
                message="Lecture arrêtée" if success else "Échec arrêt lecture"
            )

        except Exception as e:
            return self.format_response(False, error=str(e))

    async def _handle_add_favorite(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Ajoute une station aux favoris"""
        station_id = data.get('station_id')
        if not station_id:
            return self.format_response(False, error="station_id requis")

        success = await self.station_manager.add_favorite(station_id)
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
