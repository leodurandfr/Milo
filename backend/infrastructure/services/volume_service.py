# backend/infrastructure/services/volume_service.py
"""
Service de gestion du volume pour oakOS - Version avec logique multiroom
"""
import asyncio
import logging
import alsaaudio
from typing import Optional

class VolumeService:
    """Service de gestion du volume système avec support multiroom"""
    
    # Limites volume ALSA pour HiFiBerry AMP2
    MIN_ALSA_VOLUME = 40
    MAX_ALSA_VOLUME = 98
    
    def __init__(self, state_machine, snapcast_service):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service  # AJOUT
        self.mixer: Optional[alsaaudio.Mixer] = None
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()
        self._current_volume = 0
    
    async def initialize(self) -> bool:
        """Initialise le service volume"""
        try:
            self.logger.info("Initializing ALSA Digital mixer for HiFiBerry AMP2")
            self.mixer = alsaaudio.Mixer('Digital')
            
            # Récupérer le volume initial
            initial_alsa = self._get_alsa_volume()
            self._current_volume = self._alsa_to_display(initial_alsa)
            
            self.logger.info(f"Volume service initialized - ALSA: {initial_alsa}, Display: {self._current_volume}%")
            
            # Publier l'état initial
            await self._broadcast_volume_change(show_bar=False)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize volume service: {e}")
            return False
    
    def _is_multiroom_enabled(self) -> bool:
        """Vérifie si le mode multiroom est activé"""
        try:
            if not self.state_machine or not hasattr(self.state_machine, 'routing_service') or not self.state_machine.routing_service:
                return False
            routing_state = self.state_machine.routing_service.get_state()
            return routing_state.multiroom_enabled
        except Exception as e:
            self.logger.error(f"Error checking multiroom state: {e}")
            return False
    
    def _get_alsa_volume(self) -> int:
        """Récupère le volume ALSA actuel"""
        try:
            volumes = self.mixer.getvolume()
            return int(sum(volumes) / len(volumes))
        except Exception as e:
            self.logger.error(f"Error getting ALSA volume: {e}")
            return self.MIN_ALSA_VOLUME
    
    def _set_alsa_volume(self, alsa_volume: int) -> None:
        """Définit le volume ALSA"""
        try:
            # Clamp dans les limites
            clamped = max(self.MIN_ALSA_VOLUME, min(self.MAX_ALSA_VOLUME, alsa_volume))
            self.mixer.setvolume(clamped)
            self.logger.debug(f"ALSA volume set to {clamped}")
        except Exception as e:
            self.logger.error(f"Error setting ALSA volume: {e}")
    
    def _alsa_to_display(self, alsa_volume: int) -> int:
        """Convertit volume ALSA (40-98) vers volume affiché (0-100)"""
        alsa_range = self.MAX_ALSA_VOLUME - self.MIN_ALSA_VOLUME
        normalized = alsa_volume - self.MIN_ALSA_VOLUME
        return round((normalized / alsa_range) * 100)
    
    def _display_to_alsa(self, display_volume: int) -> int:
        """Convertit volume affiché (0-100) vers volume ALSA (40-98)"""
        # Clamp display dans 0-100
        display_clamped = max(0, min(100, display_volume))
        alsa_range = self.MAX_ALSA_VOLUME - self.MIN_ALSA_VOLUME
        return round((display_clamped / 100) * alsa_range) + self.MIN_ALSA_VOLUME
    
    async def get_volume(self) -> int:
        """Récupère le volume affiché actuel (0-100)"""
        try:
            if self._is_multiroom_enabled():
                # Mode multiroom : récupérer depuis Snapcast
                return await self._get_volume_multiroom()
            else:
                # Mode direct : récupérer depuis ALSA
                alsa_volume = self._get_alsa_volume()
                return self._alsa_to_display(alsa_volume)
        except Exception as e:
            self.logger.error(f"Error getting volume: {e}")
            return 0
    
    async def _get_volume_multiroom(self) -> int:
        """Récupère le volume en mode multiroom (depuis oakOS client snapcast)"""
        try:
            clients = await self.snapcast_service.get_clients()
            
            # Chercher le client oakOS (localhost/127.0.0.1)
            oakos_client = None
            for client in clients:
                if client["ip"] in ["127.0.0.1", "localhost"] or "oakos" in client["name"].lower():
                    oakos_client = client
                    break
            
            if oakos_client:
                return oakos_client["volume"]
            else:
                self.logger.warning("oakOS client not found in snapcast clients")
                return self._current_volume
                
        except Exception as e:
            self.logger.error(f"Error getting multiroom volume: {e}")
            return self._current_volume
    
    async def set_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Définit le volume (0-100) avec logique multiroom"""
        async with self._volume_lock:
            try:
                # Clamp le volume
                display_clamped = max(0, min(100, display_volume))
                
                if self._is_multiroom_enabled():
                    # Mode multiroom : utiliser Snapcast pour tous les clients
                    success = await self._set_volume_multiroom(display_clamped)
                else:
                    # Mode direct : utiliser ALSA local
                    success = await self._set_volume_direct(display_clamped)
                
                if success:
                    self._current_volume = display_clamped
                    await self._broadcast_volume_change(show_bar=show_bar)
                
                return success
                
            except Exception as e:
                self.logger.error(f"Error setting volume: {e}")
                return False
    
    async def _set_volume_direct(self, display_volume: int) -> bool:
        """Définit le volume ALSA directement (mode non-multiroom)"""
        try:
            alsa_volume = self._display_to_alsa(display_volume)
            self.logger.debug(f"Setting volume direct: display={display_volume}% → ALSA={alsa_volume}")
            self._set_alsa_volume(alsa_volume)
            return True
        except Exception as e:
            self.logger.error(f"Error setting volume direct: {e}")
            return False
    
    async def _set_volume_multiroom(self, target_volume: int) -> bool:
        """Définit le volume via Snapcast (mode multiroom - volume proportionnel)"""
        try:
            # Récupérer le volume actuel du client oakOS pour calculer le delta
            current_oakos_volume = await self._get_volume_multiroom()
            delta = target_volume - current_oakos_volume
            
            self.logger.debug(f"Setting volume multiroom: oakOS {current_oakos_volume}% → {target_volume}% (delta: {delta:+d}%)")
            
            # Récupérer tous les clients
            clients = await self.snapcast_service.get_clients()
            if not clients:
                self.logger.warning("No snapcast clients found")
                return False
            
            # Appliquer le delta proportionnellement à tous les clients
            success_count = 0
            updated_clients = []
            
            for client in clients:
                try:
                    current_volume = client["volume"]
                    new_volume = max(0, min(100, current_volume + delta))
                    
                    if await self.snapcast_service.set_volume(client["id"], new_volume):
                        success_count += 1
                        updated_clients.append({
                            "id": client["id"],
                            "name": client["name"],
                            "old_volume": current_volume,
                            "new_volume": new_volume
                        })
                        self.logger.debug(f"Volume adjusted for {client['name']}: {current_volume}% → {new_volume}% (delta: {delta:+d}%)")
                    else:
                        self.logger.warning(f"Failed to set volume for client {client['name']}")
                except Exception as e:
                    self.logger.error(f"Error setting volume for client {client['name']}: {e}")
            
            # Publier mise à jour des volumes clients via WebSocket
            if updated_clients:
                await self._broadcast_clients_volume_update(updated_clients)
            
            # Considérer comme succès si au moins un client a été mis à jour
            return success_count > 0
            
        except Exception as e:
            self.logger.error(f"Error setting volume multiroom: {e}")
            return False
    
    async def adjust_volume(self, delta: int, show_bar: bool = True) -> bool:
        """Ajuste le volume par delta"""
        try:
            current = await self.get_volume()
            new_volume = current + delta
            return await self.set_volume(new_volume, show_bar=show_bar)
        except Exception as e:
            self.logger.error(f"Error adjusting volume by {delta}: {e}")
            return False
    
    async def _broadcast_volume_change(self, show_bar: bool = True) -> None:
        """Publie un changement de volume via WebSocket unifié"""
        try:
            # En mode multiroom, obtenir le volume depuis Snapcast
            if self._is_multiroom_enabled():
                display_volume = await self._get_volume_multiroom()
                alsa_volume = None  # Pas pertinent en mode multiroom
            else:
                alsa_volume = self._get_alsa_volume()
                display_volume = self._alsa_to_display(alsa_volume)
            
            await self.state_machine.broadcast_event("volume", "volume_changed", {
                "volume": display_volume,
                "alsa_volume": alsa_volume,
                "multiroom_mode": self._is_multiroom_enabled(),
                "show_bar": show_bar,
                "source": "volume_service"
            })
            
            self.logger.debug(f"Broadcasted volume change: {display_volume}% (multiroom: {self._is_multiroom_enabled()})")
            
        except Exception as e:
            self.logger.error(f"Error broadcasting volume change: {e}")
    
    async def _broadcast_clients_volume_update(self, updated_clients: list) -> None:
        """Publie la mise à jour des volumes de tous les clients Snapcast"""
        try:
            await self.state_machine.broadcast_event("snapcast", "clients_volume_updated", {
                "updated_clients": updated_clients,
                "source": "volume_service"
            })
            
            self.logger.debug(f"Broadcasted clients volume update: {len(updated_clients)} clients")
            
        except Exception as e:
            self.logger.error(f"Error broadcasting clients volume update: {e}")
    
    async def get_status(self) -> dict:
        """Récupère l'état complet du volume"""
        try:
            multiroom_enabled = self._is_multiroom_enabled()
            
            if multiroom_enabled:
                display_volume = await self._get_volume_multiroom()
                return {
                    "volume": display_volume,
                    "mode": "multiroom",
                    "multiroom_enabled": True,
                    "snapcast_available": await self.snapcast_service.is_available(),
                    "mixer_available": self.mixer is not None
                }
            else:
                alsa_volume = self._get_alsa_volume()
                display_volume = self._alsa_to_display(alsa_volume)
                return {
                    "volume": display_volume,
                    "alsa_volume": alsa_volume,
                    "mode": "direct",
                    "multiroom_enabled": False,
                    "min_alsa": self.MIN_ALSA_VOLUME,
                    "max_alsa": self.MAX_ALSA_VOLUME,
                    "mixer_available": self.mixer is not None
                }
                
        except Exception as e:
            self.logger.error(f"Error getting volume status: {e}")
            return {
                "volume": 0,
                "mode": "error",
                "error": str(e)
            }