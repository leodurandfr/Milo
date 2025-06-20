# backend/infrastructure/services/volume_service.py
"""
Service de gestion du volume pour oakOS - Version OPTIM avec WebSocket unifié
"""
import asyncio
import logging
import alsaaudio
from typing import Optional

class VolumeService:
    """Service de gestion du volume système ALSA"""
    
    # Limites volume ALSA pour HiFiBerry AMP2
    MIN_ALSA_VOLUME = 40
    MAX_ALSA_VOLUME = 98
    
    def __init__(self, state_machine):
        self.state_machine = state_machine
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
            alsa_volume = self._get_alsa_volume()
            return self._alsa_to_display(alsa_volume)
        except Exception as e:
            self.logger.error(f"Error getting volume: {e}")
            return 0
    
    async def set_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Définit le volume (0-100)"""
        async with self._volume_lock:
            try:
                # Clamp et convertir
                display_clamped = max(0, min(100, display_volume))
                alsa_volume = self._display_to_alsa(display_clamped)
                
                self.logger.debug(f"Setting volume: display={display_clamped}% → ALSA={alsa_volume}")
                
                # Appliquer à ALSA
                self._set_alsa_volume(alsa_volume)
                self._current_volume = display_clamped
                
                # Notifier via WebSocket
                await self._broadcast_volume_change(show_bar=show_bar)
                
                return True
                
            except Exception as e:
                self.logger.error(f"Error setting volume: {e}")
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
            alsa_volume = self._get_alsa_volume()
            display_volume = self._alsa_to_display(alsa_volume)
            
            await self.state_machine.broadcast_event("volume", "volume_changed", {
                "volume": display_volume,
                "alsa_volume": alsa_volume,
                "show_bar": show_bar,
                "source": "volume_service"
            })
            
            self.logger.debug(f"Broadcasted volume change: {display_volume}% (ALSA: {alsa_volume})")
            
        except Exception as e:
            self.logger.error(f"Error broadcasting volume change: {e}")
    
    async def get_status(self) -> dict:
        """Récupère l'état complet du volume"""
        try:
            alsa_volume = self._get_alsa_volume()
            display_volume = self._alsa_to_display(alsa_volume)
            
            return {
                "volume": display_volume,
                "alsa_volume": alsa_volume,
                "min_alsa": self.MIN_ALSA_VOLUME,
                "max_alsa": self.MAX_ALSA_VOLUME,
                "mixer_available": self.mixer is not None
            }
        except Exception as e:
            self.logger.error(f"Error getting volume status: {e}")
            return {
                "volume": 0,
                "alsa_volume": self.MIN_ALSA_VOLUME,
                "error": str(e)
            }