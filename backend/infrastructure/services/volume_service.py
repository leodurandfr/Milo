# backend/infrastructure/services/volume_service.py
"""
Service de gestion du volume pour Milo - Version ultra-optimisée pour clics rapides
"""
import asyncio
import logging
import alsaaudio
import re
from typing import Optional
import time

class VolumeService:
    """Service de gestion du volume système - Version ultra-rapide pour spam clicks"""
    
    MIN_VOLUME = 0
    MAX_VOLUME = 65
    
    def __init__(self, state_machine, snapcast_service):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        self.mixer: Optional[alsaaudio.Mixer] = None
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()
        
        # 🚀 OPTIMISATIONS ULTRA-RAPIDES
        self._current_volume = 0
        self._last_amixer_volume = 0
        self._amixer_cache_time = 0
        self._is_adjusting = False
        
        # Cache clients snapcast (évite les requêtes répétées)
        self._snapcast_clients_cache = []
        self._snapcast_cache_time = 0
        self.SNAPCAST_CACHE_MS = 50  # 50ms cache pour clients
        
        # Optimisations logging (moins de logs pendant les ajustements)
        self._adjustment_count = 0
        self._first_adjustment_time = 0
    
    def _interpolate_to_display(self, actual_volume: int) -> int:
        actual_range = self.MAX_VOLUME - self.MIN_VOLUME
        normalized = actual_volume - self.MIN_VOLUME
        return round((normalized / actual_range) * 100)

    def _interpolate_from_display(self, display_volume: int) -> int:
        actual_range = self.MAX_VOLUME - self.MIN_VOLUME
        return round((display_volume / 100) * actual_range) + self.MIN_VOLUME
    
    async def initialize(self) -> bool:
        """Initialise le service volume"""
        try:
            self.logger.info(f"Initializing ULTRA-FAST volume service (limits: {self.MIN_VOLUME}-{self.MAX_VOLUME}%)")
            
            try:
                self.mixer = alsaaudio.Mixer('Digital')
                self.logger.info("ALSA Digital mixer found")
            except Exception as e:
                self.logger.error(f"Digital mixer not found: {e}")
                return False
            
            if self._is_multiroom_enabled():
                await self._enforce_multiroom_limits()
                self._current_volume = await self.get_volume()
            else:
                initial_volume = await self._get_amixer_volume_fast()
                if initial_volume is None:
                    self.logger.error("Failed to get initial volume from amixer")
                    return False
                
                limited_volume = max(self.MIN_VOLUME, min(self.MAX_VOLUME, initial_volume))
                
                if limited_volume != initial_volume:
                    self.logger.info(f"Enforcing amixer limits: {initial_volume}% → {limited_volume}%")
                    success = await self._set_amixer_volume_fast(limited_volume)
                    if not success:
                        return False
                
                self._current_volume = self._interpolate_to_display(limited_volume)
                self._last_amixer_volume = limited_volume
                self._amixer_cache_time = time.time()
            
            self.logger.info(f"Ultra-fast volume service ready - Display: {self._current_volume}%")
            asyncio.create_task(self._delayed_initial_broadcast())
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize volume service: {e}")
            return False
    
    async def _delayed_initial_broadcast(self):
        """Diffuse l'état initial"""
        try:
            await asyncio.sleep(0.5)  # Réduit à 500ms
            await self._broadcast_volume_change(show_bar=False)
        except Exception as e:
            self.logger.error(f"Error in delayed initial broadcast: {e}")
    
    def _is_multiroom_enabled(self) -> bool:
        """Vérifie si le mode multiroom est activé"""
        try:
            if not self.state_machine or not hasattr(self.state_machine, 'routing_service') or not self.state_machine.routing_service:
                return False
            routing_state = self.state_machine.routing_service.get_state()
            return routing_state.multiroom_enabled
        except Exception as e:
            return False
    
    # 🚀 VERSION ULTRA-RAPIDE d'amixer (moins de parsing, cache agressif)
    async def _get_amixer_volume_fast(self) -> Optional[int]:
        """Version ultra-rapide d'amixer get - avec cache agressif"""
        current_time = time.time()
        
        # Cache de 20ms pendant les ajustements
        if self._is_adjusting and (current_time - self._amixer_cache_time) < 0.02:
            return self._last_amixer_volume
        
        try:
            # Commande amixer plus directe
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "get", "Digital",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL  # Pas d'erreurs pendant les ajustements
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode != 0:
                return self._last_amixer_volume  # Fallback sur cache
            
            # Parsing optimisé (première occurrence seulement)
            output = stdout.decode()
            match = re.search(r'\[(\d+)%\]', output)
            
            if match:
                volume = int(match.group(1))
                self._last_amixer_volume = volume
                self._amixer_cache_time = current_time
                return volume
            else:
                return self._last_amixer_volume
                
        except Exception:
            return self._last_amixer_volume  # Toujours un fallback
    
    # 🚀 VERSION ULTRA-RAPIDE d'amixer set
    async def _set_amixer_volume_fast(self, volume: int) -> bool:
        """Version ultra-rapide d'amixer set"""
        try:
            limited_volume = max(self.MIN_VOLUME, min(self.MAX_VOLUME, volume))
            
            # Commande directe sans capture stderr pendant les ajustements
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "set", "Digital", f"{limited_volume}%",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            
            # Mettre à jour le cache immédiatement
            self._last_amixer_volume = limited_volume
            self._amixer_cache_time = time.time()
            
            return proc.returncode == 0
            
        except Exception:
            return False
    
    # 🚀 CACHE CLIENTS SNAPCAST pour éviter les requêtes répétées
    async def _get_snapcast_clients_cached(self):
        """Récupère les clients avec cache ultra-rapide"""
        current_time = time.time() * 1000
        
        if (current_time - self._snapcast_cache_time) < self.SNAPCAST_CACHE_MS:
            return self._snapcast_clients_cache
        
        try:
            clients = await self.snapcast_service.get_clients()
            self._snapcast_clients_cache = clients
            self._snapcast_cache_time = current_time
            return clients
        except Exception:
            return self._snapcast_clients_cache  # Fallback sur cache
    
    async def get_volume(self) -> int:
        """Version optimisée get_volume"""
        try:
            if self._is_multiroom_enabled():
                return await self._get_volume_multiroom_fast()
            else:
                actual_volume = await self._get_amixer_volume_fast()
                if actual_volume is None:
                    return self._current_volume
                return self._interpolate_to_display(actual_volume)
        except Exception:
            return self._current_volume
    
    async def _get_volume_multiroom_fast(self) -> int:
        """Version rapide multiroom avec cache"""
        try:
            clients = await self._get_snapcast_clients_cached()
            
            if not clients:
                return self._current_volume
            
            total_volume = sum(client["volume"] for client in clients)
            average_volume = round(total_volume / len(clients))
            return self._interpolate_to_display(average_volume)
        except Exception:
            return self._current_volume
    
    # 🚀 ADJUST_VOLUME ULTRA-OPTIMISÉ
    async def adjust_volume(self, delta: int, show_bar: bool = True) -> bool:
        """Version ultra-rapide pour spam clicks"""
        async with self._volume_lock:
            try:
                # Marquer qu'on est en ajustement (active les caches)
                was_adjusting = self._is_adjusting
                self._is_adjusting = True
                
                # Compteur pour logs optimisés
                if not was_adjusting:
                    self._adjustment_count = 1
                    self._first_adjustment_time = time.time()
                else:
                    self._adjustment_count += 1
                
                # Calcul rapide du nouveau volume
                current_volume = self._current_volume  # Utiliser la valeur cached
                new_volume = max(0, min(100, current_volume + delta))
                
                # Log optimisé (moins verbose pendant les ajustements)
                if self._adjustment_count <= 3 or self._adjustment_count % 5 == 0:
                    self.logger.debug(f"🚀 Fast adjust #{self._adjustment_count}: {current_volume}% + {delta} = {new_volume}%")
                
                # Application ultra-rapide
                if self._is_multiroom_enabled():
                    success = await self._set_volume_multiroom_fast(new_volume)
                else:
                    success = await self._set_volume_direct_fast(new_volume)
                
                if success:
                    self._current_volume = new_volume
                    # Broadcast immédiat (pas d'attente)
                    asyncio.create_task(self._broadcast_volume_change_fast(show_bar))
                
                # Marquer fin d'ajustement après un délai
                asyncio.create_task(self._mark_adjustment_done())
                
                return success
                
            except Exception as e:
                self.logger.error(f"Error in fast adjust: {e}")
                self._is_adjusting = False
                return False
    
    async def _mark_adjustment_done(self):
        """Marque la fin d'ajustement après un délai"""
        await asyncio.sleep(0.1)  # 100ms après le dernier ajustement
        self._is_adjusting = False
        
        # Log résumé
        if self._adjustment_count > 1:
            duration = time.time() - self._first_adjustment_time
            self.logger.info(f"📊 Adjustment burst: {self._adjustment_count} changes in {duration:.2f}s")
    
    async def _set_volume_direct_fast(self, display_volume: int) -> bool:
        """Version ultra-rapide direct"""
        try:
            actual_volume = self._interpolate_from_display(display_volume)
            return await self._set_amixer_volume_fast(actual_volume)
        except Exception:
            return False
    
    async def _set_volume_multiroom_fast(self, display_volume: int) -> bool:
        """Version rapide multiroom (simplifié)"""
        try:
            target_volume = self._interpolate_from_display(display_volume)
            clients = await self._get_snapcast_clients_cached()
            
            if not clients:
                return False
            
            # Logique simplifiée pour la rapidité
            current_average = sum(client["volume"] for client in clients) / len(clients)
            
            if current_average <= 5:
                delta = target_volume - current_average
                for client in clients:
                    new_vol = max(self.MIN_VOLUME, min(self.MAX_VOLUME, round(client["volume"] + delta)))
                    await self.snapcast_service.set_volume(client["id"], new_vol)
            else:
                ratio = target_volume / current_average
                for client in clients:
                    new_vol = max(self.MIN_VOLUME, min(self.MAX_VOLUME, round(client["volume"] * ratio)))
                    await self.snapcast_service.set_volume(client["id"], new_vol)
            
            return True
        except Exception:
            return False
    
    # 🚀 BROADCAST ULTRA-RAPIDE
    async def _broadcast_volume_change_fast(self, show_bar: bool = True) -> None:
        """Broadcast optimisé pour rapidité"""
        try:
            # Pas d'appels système pendant les ajustements rapides
            await self.state_machine.broadcast_event("volume", "volume_changed", {
                "volume": self._current_volume,
                "amixer_volume": self._last_amixer_volume if not self._is_multiroom_enabled() else None,
                "multiroom_mode": self._is_multiroom_enabled(),
                "show_bar": show_bar,
                "source": "fast_volume_service",
                "limits": {"min": self.MIN_VOLUME, "max": self.MAX_VOLUME}
            })
        except Exception as e:
            if self._adjustment_count <= 3:  # Log seulement les premières erreurs
                self.logger.error(f"Error in fast broadcast: {e}")
    
    # Méthodes existantes simplifiées...
    async def set_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Version standard set_volume (pour API directe)"""
        async with self._volume_lock:
            try:
                self._is_adjusting = True
                display_clamped = max(0, min(100, display_volume))
                
                if self._is_multiroom_enabled():
                    success = await self._set_volume_multiroom_fast(display_clamped)
                else:
                    success = await self._set_volume_direct_fast(display_clamped)
                
                if success:
                    self._current_volume = display_clamped
                    await self._broadcast_volume_change_fast(show_bar)
                
                self._is_adjusting = False
                return success
            except Exception as e:
                self.logger.error(f"Error setting volume: {e}")
                self._is_adjusting = False
                return False
    
    async def _enforce_multiroom_limits(self) -> None:
        """Force les limites sur tous les clients Snapcast au démarrage"""
        try:
            clients = await self.snapcast_service.get_clients()
            if not clients:
                return
            
            for client in clients:
                current_volume = client["volume"]
                limited_volume = max(self.MIN_VOLUME, min(self.MAX_VOLUME, current_volume))
                
                if limited_volume != current_volume:
                    await self.snapcast_service.set_volume(client["id"], limited_volume)
                    self.logger.info(f"Enforced limits on {client['name']}: {current_volume}% → {limited_volume}%")
        except Exception as e:
            self.logger.error(f"Error enforcing multiroom limits: {e}")
    
    async def get_status(self) -> dict:
        """Récupère l'état complet du volume"""
        try:
            multiroom_enabled = self._is_multiroom_enabled()
            display_volume = self._current_volume
            
            if multiroom_enabled:
                clients = await self._get_snapcast_clients_cached()
                return {
                    "volume": display_volume,
                    "mode": "multiroom_fast",
                    "multiroom_enabled": True,
                    "client_count": len(clients),
                    "snapcast_available": await self.snapcast_service.is_available(),
                    "mixer_available": self.mixer is not None,
                    "limits": {"min": self.MIN_VOLUME, "max": self.MAX_VOLUME}
                }
            else:
                return {
                    "volume": display_volume,
                    "amixer_volume": self._last_amixer_volume,
                    "mode": "direct_fast",
                    "multiroom_enabled": False,
                    "mixer_available": self.mixer is not None,
                    "limits": {"min": self.MIN_VOLUME, "max": self.MAX_VOLUME}
                }
        except Exception as e:
            self.logger.error(f"Error getting volume status: {e}")
            return {
                "volume": self._current_volume,
                "mode": "error",
                "error": str(e),
                "limits": {"min": self.MIN_VOLUME, "max": self.MAX_VOLUME}
            }