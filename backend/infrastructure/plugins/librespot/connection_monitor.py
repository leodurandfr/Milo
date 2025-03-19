# Remplacer dans backend/infrastructure/plugins/librespot/connection_monitor.py

import asyncio
import logging
import time
from typing import Dict, Any, Callable, Optional

class ConnectionMonitor:
    """Surveille l'état de connexion des appareils à go-librespot"""
    
    def __init__(self, api_client, metadata_processor, polling_interval: float = 3.0):
        self.api_client = api_client
        self.metadata_processor = metadata_processor
        self.base_polling_interval = polling_interval  # Intervalle de base
        self.current_polling_interval = polling_interval  # Intervalle actuel
        self.logger = logging.getLogger("librespot.connection")
        self.polling_task = None
        self.is_connected = False
        self.last_check_time = time.time()
        self.was_connected = False
        self.connection_lost_counter = 0
        self.max_lost_checks = 3  # Nombre de vérifications consécutives échouées avant de déclarer une déconnexion
        self.is_playing = False  # Suivi de l'état de lecture
        
        # Intervalles optimisés selon l'état
        self.playing_interval = polling_interval  # Intervalle quand lecture en cours
        self.paused_interval = polling_interval * 2  # Intervalle quand en pause
        self.idle_interval = polling_interval * 5  # Intervalle quand inactif/déconnecté
    
    async def start(self) -> None:
        """Démarre la surveillance de connexion"""
        if self.polling_task is None or self.polling_task.done():
            self.polling_task = asyncio.create_task(self._polling_loop())
            self.logger.debug("Surveillance de connexion démarrée")
    
    async def stop(self) -> None:
        """Arrête la surveillance de connexion"""
        if self.polling_task and not self.polling_task.done():
            self.polling_task.cancel()
            try:
                await self.polling_task
            except asyncio.CancelledError:
                pass
            self.polling_task = None
            self.logger.debug("Surveillance de connexion arrêtée")
    
    async def check_connection(self) -> bool:
        """
        Vérifie si un appareil est connecté à go-librespot de manière fiable.
        
        Returns:
            bool: True si un appareil est connecté
        """
        try:
            status = await self.api_client.fetch_status()
            self.logger.debug(f"Vérification de connexion, statut: {status}")
            
            # Vérifier des conditions plus larges pour déterminer si on est connecté
            
            # 1. Vérifier si un username est présent (connexion Spotify)
            if status.get("username"):
                self.logger.info(f"Appareil connecté détecté via username: {status.get('username')}")
                return True
                
            # 2. Vérifier les informations du player
            player_status = status.get("player", {})
            
            # Mettre à jour l'état de lecture pour ajuster l'intervalle de polling
            self.is_playing = player_status.get("is_playing", False)
            
            # Si le player a un statut, on est probablement connecté
            if player_status:
                # Vérifier des indices de connexion dans le player
                if (player_status.get("current_track") or 
                    player_status.get("is_playing", False) or 
                    player_status.get("position_ms", 0) > 0 or
                    player_status.get("duration_ms", 0) > 0):
                    self.logger.info("Appareil connecté détecté via player status")
                    return True
                    
                # Même si pas de piste en cours, s'il y a des indications de device
                if "device" in status or "device_id" in status or "device_name" in status:
                    self.logger.info("Appareil connecté détecté via device info")
                    return True
            
            # 3. Vérifier les données de connexion directes
            if status.get("connected") == True or status.get("deviceConnected") == True:
                self.logger.info("Appareil connecté détecté via flags connecté")
                return True
                
            # Si aucune condition n'est remplie, on n'est probablement pas connecté
            self.logger.debug("Aucun appareil connecté détecté")
            return False
            
        except Exception as e:
            # En cas d'erreur, considérer que l'absence d'information n'est pas une déconnexion
            self.logger.debug(f"Erreur lors de la vérification de connexion: {e}")
            return False

    async def _polling_loop(self) -> None:
        """Boucle de surveillance des métadonnées et de l'état de connexion"""
        connection_check_counter = 0
                
        try:
            while True:
                try:
                    # Vérifier la connexion à chaque itération
                    is_connected = await self.check_connection()
                    
                    # Adapter l'intervalle de polling en fonction de l'état
                    if is_connected:
                        if self.is_playing:
                            # En lecture: intervalle standard
                            self.current_polling_interval = self.playing_interval
                        else:
                            # En pause: intervalle plus long
                            self.current_polling_interval = self.paused_interval
                    else:
                        # Déconnecté: intervalle encore plus long
                        self.current_polling_interval = self.idle_interval
                    
                    # Si la connexion est perdue, incrémenter le compteur
                    if self.was_connected and not is_connected:
                        self.connection_lost_counter += 1
                        self.logger.debug(f"Possible perte de connexion détectée ({self.connection_lost_counter}/{self.max_lost_checks})")
                    else:
                        # Réinitialiser le compteur si la connexion est rétablie
                        self.connection_lost_counter = 0
                    
                    # Si plusieurs vérifications consécutives échouent, déclarer une déconnexion
                    if self.connection_lost_counter >= self.max_lost_checks:
                        self.logger.info(f"Déconnexion confirmée après {self.max_lost_checks} échecs consécutifs")
                        self.was_connected = False
                        self.connection_lost_counter = 0
                        self.is_connected = False
                        
                        # Publier un événement de déconnexion explicite
                        await self.metadata_processor.publish_status("disconnected")
                        
                        # Continuer pour éviter de traiter d'autres cas
                        await asyncio.sleep(self.current_polling_interval)
                        continue
                    
                    # Si l'état de connexion a changé, publier immédiatement
                    if is_connected != self.was_connected:
                        self.logger.info(f"État de connexion modifié: {self.was_connected} -> {is_connected}")
                        self.was_connected = is_connected
                        self.is_connected = is_connected
                        
                        # Publier l'événement de connexion ou déconnexion
                        await self.metadata_processor.publish_status("connected" if is_connected else "disconnected")
                    
                    # Si connecté, obtenir les métadonnées
                    if is_connected:
                        status = await self.api_client.fetch_status()
                        metadata = await self.metadata_processor.extract_from_status(status)
                        
                        # S'assurer que metadata n'est pas vide
                        if metadata:
                            # Publier les métadonnées
                            await self.metadata_processor.publish_metadata(metadata)
                                
                            # Si la lecture est en cours, publier également l'état "playing"
                            if metadata.get("is_playing", False):
                                await self.metadata_processor.publish_status("playing")
                            else:
                                await self.metadata_processor.publish_status("paused")
                    else:
                        # Si nous n'étions pas connectés, publier un état déconnecté périodiquement
                        if connection_check_counter % 10 == 0:  # Tous les ~10 intervalles
                            await self.metadata_processor.publish_status("disconnected")
                    
                    self.last_check_time = time.time()
                    connection_check_counter += 1
                            
                except Exception as e:
                    # Log de débogage plus détaillé
                    if "Erreur API (204)" in str(e):
                        self.logger.debug("Aucun contenu musical disponible (204)")
                    else:
                        self.logger.warning(f"Erreur dans la boucle de surveillance: {str(e)}")
                        
                    # Si nous avons eu une erreur et que nous étions connectés auparavant,
                    # incrémenter le compteur d'erreurs
                    if self.was_connected:
                        self.connection_lost_counter += 1
                        self.logger.debug(f"Erreur de communication lors de la vérification de connexion ({self.connection_lost_counter}/{self.max_lost_checks})")
                        
                        # Si trop d'erreurs consécutives, déclarer une déconnexion
                        if self.connection_lost_counter >= self.max_lost_checks:
                            self.logger.warning(f"Déconnexion présumée après {self.max_lost_checks} erreurs consécutives")
                            self.was_connected = False
                            self.connection_lost_counter = 0
                            self.is_connected = False
                            
                            # Publier un événement de déconnexion explicite
                            await self.metadata_processor.publish_status("disconnected")
                
                await asyncio.sleep(self.current_polling_interval)
                
        except asyncio.CancelledError:
            self.logger.debug("Boucle de surveillance arrêtée")
            
        except Exception as e:
            self.logger.error(f"Erreur inattendue dans la boucle de surveillance: {str(e)}")