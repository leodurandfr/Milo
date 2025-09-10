# backend/infrastructure/services/volume_service.py
"""
Service de gestion du volume pour Milo - Version production OPTIM
"""
import asyncio
import logging
import alsaaudio
import re
import json
import os
from typing import Optional, Dict, Any, List
import time
from pathlib import Path
from backend.infrastructure.services.settings_service import SettingsService

class VolumeService:
    """Service de gestion du volume système - Volume multiroom centralisé"""
    
    LAST_VOLUME_FILE = Path("/var/lib/milo/last_volume.json")
    
    def __init__(self, state_machine, snapcast_service):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        self.settings_service = SettingsService()
        self.mixer: Optional[alsaaudio.Mixer] = None
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()
        
        # Configuration volume
        self._alsa_min_volume = 0
        self._alsa_max_volume = 65
        self._default_startup_display_volume = 37
        self._restore_last_volume = False
        self._mobile_volume_steps = 5
        
        # États internes
        self._precise_display_volume = 0.0  # Mode direct
        self._multiroom_volume = 50.0  # Mode multiroom centralisé
        self._last_alsa_volume = 0
        self._alsa_cache_time = 0
        self._is_adjusting = False
        
        # États display par client (découplés)
        self._client_display_states = {}
        self._client_states_initialized = False
        
        # Caches
        self._snapcast_clients_cache = []
        self._snapcast_cache_time = 0
        self.SNAPCAST_CACHE_MS = 50
        
        # Batching WebSocket
        self._pending_volume_broadcast = False
        self._pending_show_bar = False
        self._broadcast_task = None
        self.BROADCAST_DELAY_MS = 30
        
        self._ensure_data_directory()
    
    def _ensure_data_directory(self) -> None:
        """Crée le répertoire de données si nécessaire"""
        try:
            self.LAST_VOLUME_FILE.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.logger.error(f"Failed to create data directory: {e}")
    
    def _load_volume_config(self) -> None:
        """Charge la configuration volume depuis settings"""
        try:
            self.settings_service._cache = None
            volume_config = self.settings_service.get_volume_config()
            self._alsa_min_volume = volume_config["alsa_min"]
            self._alsa_max_volume = volume_config["alsa_max"]
            self._default_startup_display_volume = volume_config["startup_volume"]
            self._restore_last_volume = volume_config["restore_last_volume"]
            self._mobile_volume_steps = volume_config["mobile_volume_steps"]
        except Exception as e:
            self.logger.error(f"Error loading volume config: {e}")
    
    def _save_last_volume(self, display_volume: int) -> None:
        """Sauvegarde le dernier volume en arrière-plan"""
        if not self._restore_last_volume:
            return
            
        async def save_async():
            try:
                data = {"last_volume": display_volume, "timestamp": time.time()}
                temp_file = self.LAST_VOLUME_FILE.with_suffix('.tmp')
                with open(temp_file, 'w') as f:
                    json.dump(data, f)
                temp_file.replace(self.LAST_VOLUME_FILE)
            except Exception as e:
                self.logger.error(f"Failed to save last volume: {e}")
        
        asyncio.create_task(save_async())
    
    def _load_last_volume(self) -> Optional[int]:
        """Charge le dernier volume sauvegardé"""
        try:
            if not self.LAST_VOLUME_FILE.exists():
                return None
            
            with open(self.LAST_VOLUME_FILE, 'r') as f:
                data = json.load(f)
            
            last_volume = data.get('last_volume')
            timestamp = data.get('timestamp', 0)
            age_days = (time.time() - timestamp) / (24 * 3600)
            
            if age_days > 7 or not (0 <= last_volume <= 100):
                return None
            
            self.logger.info(f"Restored last volume: {last_volume}%")
            return last_volume
        except Exception:
            return None
    
    def _determine_startup_volume(self) -> int:
        """Détermine le volume de démarrage selon la configuration"""
        if self._restore_last_volume:
            last_volume = self._load_last_volume()
            if last_volume is not None:
                return last_volume
        return self._default_startup_display_volume
    
    async def reload_volume_limits(self) -> bool:
        """Recharge les limites de volume"""
        try:
            old_display_volume = await self.get_display_volume()
            old_alsa_min = self._alsa_min_volume
            old_alsa_max = self._alsa_max_volume
            
            self._load_volume_config()
            
            if old_alsa_min == self._alsa_min_volume and old_alsa_max == self._alsa_max_volume:
                return True
            
            self._invalidate_all_caches()
            
            if not self._is_multiroom_enabled():
                old_alsa_volume = self._display_to_alsa_old_limits(old_display_volume, old_alsa_min, old_alsa_max)
                new_display_volume = self._alsa_to_display(old_alsa_volume)
                
                if old_alsa_volume < self._alsa_min_volume or old_alsa_volume > self._alsa_max_volume:
                    center_volume = (self._alsa_min_volume + self._alsa_max_volume) // 2
                    safe_display_volume = self._alsa_to_display(center_volume)
                    await self.set_display_volume(safe_display_volume, show_bar=False)
                else:
                    self._precise_display_volume = float(new_display_volume)
                    await self._schedule_broadcast(show_bar=False)
            
            return True
        except Exception as e:
            self.logger.error(f"Error reloading volume limits: {e}")
            return False
    
    async def reload_startup_config(self) -> bool:
        """Recharge la config de startup"""
        try:
            volume_config = self.settings_service.get_volume_config()
            self._default_startup_display_volume = volume_config["startup_volume"]
            self._restore_last_volume = volume_config["restore_last_volume"]
            return True
        except Exception as e:
            self.logger.error(f"Error reloading startup config: {e}")
            return False

    async def reload_volume_steps_config(self) -> bool:
        """Recharge les volume steps"""
        try:
            self.settings_service._cache = None
            volume_config = self.settings_service.get_volume_config()
            self._mobile_volume_steps = volume_config["mobile_volume_steps"]
            await self._schedule_broadcast(show_bar=False)
            return True
        except Exception as e:
            self.logger.error(f"Error reloading volume steps: {e}")
            return False

    def _display_to_alsa_old_limits(self, display_volume: int, old_min: int, old_max: int) -> int:
        """Convertit avec les anciennes limites"""
        old_alsa_range = old_max - old_min
        return round((display_volume / 100) * old_alsa_range) + old_min
    
    # === CONVERSIONS ===
    
    def _alsa_to_display(self, alsa_volume: int) -> int:
        """Convertit volume ALSA vers volume affiché (0-100%)"""
        alsa_range = self._alsa_max_volume - self._alsa_min_volume
        normalized = alsa_volume - self._alsa_min_volume
        return round((normalized / alsa_range) * 100)

    def _display_to_alsa(self, display_volume: int) -> int:
        """Convertit volume affiché (0-100%) vers volume ALSA"""
        alsa_range = self._alsa_max_volume - self._alsa_min_volume
        return round((display_volume / 100) * alsa_range) + self._alsa_min_volume
    
    def _clamp_display_volume(self, display_volume: float) -> float:
        """Limite le volume affiché entre 0-100%"""
        return max(0.0, min(100.0, display_volume))
    
    def _clamp_alsa_volume(self, alsa_volume: int) -> int:
        """Limite le volume ALSA entre les limites configurées"""
        return max(self._alsa_min_volume, min(self._alsa_max_volume, alsa_volume))
    
    def _invalidate_all_caches(self) -> None:
        """Invalide tous les caches"""
        self._client_display_states = {}
        self._client_states_initialized = False
        self._snapcast_clients_cache = []
        self._snapcast_cache_time = 0
    
    # === GESTION DES ÉTATS DISPLAY PAR CLIENT ===
    
    async def _initialize_client_display_states(self) -> None:
        """Initialise les états display des clients"""
        if self._client_states_initialized:
            return
            
        try:
            clients = await self._get_snapcast_clients_cached()
            total_volume = 0.0
            client_count = 0
            
            for client in clients:
                client_id = client["id"]
                alsa_volume = client.get("volume", 0)
                display_volume = self._alsa_to_display(alsa_volume)
                self._client_display_states[client_id] = float(display_volume)
                total_volume += display_volume
                client_count += 1
            
            if client_count > 0:
                self._multiroom_volume = total_volume / client_count
            
            self._client_states_initialized = True
        except Exception as e:
            self.logger.error(f"Error initializing client states: {e}")
    
    def _get_client_display_volume(self, client_id: str, fallback_alsa: int = 0) -> float:
        """Récupère le volume display d'un client"""
        if client_id in self._client_display_states:
            return self._client_display_states[client_id]
        else:
            display_volume = float(self._alsa_to_display(fallback_alsa))
            self._client_display_states[client_id] = display_volume
            return display_volume
    
    def _set_client_display_volume(self, client_id: str, display_volume: float) -> None:
        """Définit le volume display d'un client"""
        clamped_volume = self._clamp_display_volume(display_volume)
        self._client_display_states[client_id] = clamped_volume
    
    async def sync_client_volume_from_external(self, client_id: str, new_alsa_volume: int) -> None:
        """Synchronise l'état display depuis une source externe"""
        if self._is_adjusting:
            return
        
        new_display_volume = float(self._alsa_to_display(new_alsa_volume))
        self._client_display_states[client_id] = new_display_volume
        
        try:
            await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                "client_id": client_id,
                "volume": int(round(new_display_volume)),
                "muted": False,
                "source": "external_sync"
            })
        except Exception as e:
            self.logger.error(f"Error broadcasting client sync event: {e}")
        
        if self._is_multiroom_enabled():
            await self._recalculate_multiroom_volume()
            await self._schedule_broadcast(show_bar=False)
    
    async def _recalculate_multiroom_volume(self) -> None:
        """Recalcule le volume multiroom centralisé"""
        try:
            clients = await self._get_snapcast_clients_cached()
            active_clients = [c for c in clients if not c.get("muted", False)]
            
            if not active_clients:
                return
            
            total_volume = 0.0
            for client in active_clients:
                client_id = client["id"]
                display_volume = self._get_client_display_volume(client_id, client.get("volume", 0))
                total_volume += display_volume
            
            self._multiroom_volume = total_volume / len(active_clients)
        except Exception as e:
            self.logger.error(f"Error recalculating multiroom volume: {e}")
    
    # === INITIALISATION ===
    
    async def initialize(self) -> bool:
        """Initialise le service volume"""
        try:
            self._load_volume_config()
            
            try:
                self.mixer = alsaaudio.Mixer('Digital')
            except Exception as e:
                self.logger.error(f"Digital mixer not found: {e}")
                return False
            
            startup_display_volume = self._determine_startup_volume()
            startup_alsa_volume = self._display_to_alsa(startup_display_volume)
            
            if self._is_multiroom_enabled():
                await self._set_startup_volume_multiroom(startup_alsa_volume)
            else:
                await self._set_startup_volume_direct(startup_alsa_volume)
            
            self._precise_display_volume = float(startup_display_volume)
            self._multiroom_volume = float(startup_display_volume)
            self._last_alsa_volume = startup_alsa_volume
            self._alsa_cache_time = time.time()
            
            asyncio.create_task(self._delayed_initial_broadcast())
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize volume service: {e}")
            return False
    
    async def _delayed_initial_broadcast(self):
        """Diffuse l'état initial"""
        try:
            await asyncio.sleep(0.5)
            await self._schedule_broadcast(show_bar=False)
        except Exception as e:
            self.logger.error(f"Error in delayed initial broadcast: {e}")
    
    def _is_multiroom_enabled(self) -> bool:
        """Vérifie si le mode multiroom est activé"""
        try:
            if not self.state_machine or not hasattr(self.state_machine, 'routing_service') or not self.state_machine.routing_service:
                return False
            routing_state = self.state_machine.routing_service.get_state()
            return routing_state.multiroom_enabled
        except Exception:
            return False
    
    # === API PUBLIQUE ===
    
    async def get_display_volume(self) -> int:
        """Volume centralisé selon le mode"""
        if self._is_multiroom_enabled():
            return int(round(self._multiroom_volume))
        else:
            return int(round(self._precise_display_volume))
    
    async def set_display_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Définit le volume affiché (0-100%)"""
        async with self._volume_lock:
            try:
                self._is_adjusting = True
                display_clamped = self._clamp_display_volume(float(display_volume))
                
                if self._is_multiroom_enabled():
                    success = await self._set_multiroom_volume_centralized(int(display_clamped))
                else:
                    target_alsa_volume = self._display_to_alsa(int(display_clamped))
                    success = await self._apply_alsa_volume_direct(target_alsa_volume)
                    if success:
                        self._precise_display_volume = display_clamped
                
                if success:
                    self._save_last_volume(int(display_clamped))
                    await self._schedule_broadcast(show_bar)
                
                asyncio.create_task(self._mark_adjustment_done())
                return success
            except Exception as e:
                self.logger.error(f"Error setting display volume: {e}")
                self._is_adjusting = False
                return False
    
    async def adjust_display_volume(self, delta: int, show_bar: bool = True) -> bool:
        """Ajustement centralisé - Multiroom distribue uniformément"""
        async with self._volume_lock:
            try:
                self._is_adjusting = True
                
                if self._is_multiroom_enabled():
                    success = await self._adjust_multiroom_volume_centralized(delta)
                else:
                    success = await self._adjust_volume_direct(delta)
                
                if success:
                    final_display_volume = await self.get_display_volume()
                    self._save_last_volume(final_display_volume)
                    await self._schedule_broadcast(show_bar)
                
                asyncio.create_task(self._mark_adjustment_done())
                return success
            except Exception as e:
                self.logger.error(f"Error in adjust volume: {e}")
                self._is_adjusting = False
                return False
    
    async def _adjust_volume_direct(self, delta: int) -> bool:
        """Ajustement volume en mode direct"""
        try:
            current_precise = self._precise_display_volume
            new_precise = self._clamp_display_volume(current_precise + delta)
            new_display = int(round(new_precise))
            new_alsa = self._display_to_alsa(new_display)
            
            success = await self._apply_alsa_volume_direct(new_alsa)
            if success:
                self._precise_display_volume = new_precise
            return success
        except Exception as e:
            self.logger.error(f"Error in direct volume adjustment: {e}")
            return False
    
    async def _adjust_multiroom_volume_centralized(self, delta: int) -> bool:
        """Ajustement volume multiroom centralisé - Distribution uniforme"""
        try:
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                return False
            
            await self._initialize_client_display_states()
            
            # Ajuster le volume multiroom centralisé
            old_multiroom = self._multiroom_volume
            new_multiroom = self._clamp_display_volume(old_multiroom + delta)
            self._multiroom_volume = new_multiroom
            
            # Distribuer ce delta à TOUS les clients
            snapcast_operations = []
            client_events = []
            
            for client in clients:
                client_id = client["id"]
                current_alsa_volume = client.get("volume", 0)
                current_display_float = self._get_client_display_volume(client_id, current_alsa_volume)
                new_display_float = self._clamp_display_volume(current_display_float + delta)
                
                self._set_client_display_volume(client_id, new_display_float)
                
                new_alsa_volume = self._display_to_alsa(int(round(new_display_float)))
                new_alsa_volume = self._clamp_alsa_volume(new_alsa_volume)
                
                snapcast_operations.append((client_id, new_alsa_volume))
                client_events.append({
                    "client_id": client_id,
                    "volume": int(round(new_display_float)),
                    "muted": client.get("muted", False)
                })
            
            # Exécuter opérations Snapcast en parallèle
            await asyncio.gather(
                *[self.snapcast_service.set_volume(client_id, alsa_vol) 
                  for client_id, alsa_vol in snapcast_operations]
            )
            
            # Envoyer événements clients
            for client_event in client_events:
                await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                    "client_id": client_event["client_id"],
                    "volume": client_event["volume"],
                    "muted": client_event["muted"],
                    "source": "multiroom_centralized"
                })
            
            self._snapcast_clients_cache = []
            self._snapcast_cache_time = 0
            return True
        except Exception as e:
            self.logger.error(f"Error in centralized multiroom adjustment: {e}")
            return False
    
    async def _set_multiroom_volume_centralized(self, target_display_volume: int) -> bool:
        """Définit un volume absolu en mode multiroom centralisé"""
        try:
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                return False
            
            await self._initialize_client_display_states()
            self._multiroom_volume = float(target_display_volume)
            target_alsa_volume = self._display_to_alsa(target_display_volume)
            
            snapcast_operations = []
            client_events = []
            
            for client in clients:
                client_id = client["id"]
                self._set_client_display_volume(client_id, float(target_display_volume))
                snapcast_operations.append((client_id, target_alsa_volume))
                client_events.append({
                    "client_id": client_id,
                    "volume": target_display_volume,
                    "muted": client.get("muted", False)
                })
            
            await asyncio.gather(
                *[self.snapcast_service.set_volume(client_id, alsa_vol) 
                  for client_id, alsa_vol in snapcast_operations]
            )
            
            for client_event in client_events:
                await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                    "client_id": client_event["client_id"],
                    "volume": client_event["volume"],
                    "muted": client_event["muted"],
                    "source": "multiroom_set_absolute"
                })
            
            return True
        except Exception as e:
            self.logger.error(f"Error setting centralized multiroom volume: {e}")
            return False
    
    # === BATCHING WEBSOCKET ===
    
    async def _schedule_broadcast(self, show_bar: bool = True) -> None:
        """Planifie un broadcast groupé"""
        self._pending_volume_broadcast = True
        self._pending_show_bar = self._pending_show_bar or show_bar
        
        if self._broadcast_task is None or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(self._execute_delayed_broadcast())
    
    async def _execute_delayed_broadcast(self) -> None:
        """Exécute le broadcast après délai"""
        try:
            await asyncio.sleep(self.BROADCAST_DELAY_MS / 1000)
            
            if not self._pending_volume_broadcast:
                return
            
            show_bar = self._pending_show_bar
            self._pending_volume_broadcast = False
            self._pending_show_bar = False
            
            current_volume = await self.get_display_volume()
            
            await self.state_machine.broadcast_event("volume", "volume_changed", {
                "volume": current_volume,
                "multiroom_mode": self._is_multiroom_enabled(),
                "show_bar": show_bar,
                "source": "volume_service",
                "mobile_steps": self._mobile_volume_steps
            })
        except Exception as e:
            self.logger.error(f"Error in delayed broadcast: {e}")
    
    # === MÉTHODES UTILITAIRES ===
    
    async def increase_display_volume(self, delta: int = None) -> bool:
        """Augmente le volume"""
        step = delta if delta is not None else self._mobile_volume_steps
        return await self.adjust_display_volume(step)
    
    async def decrease_display_volume(self, delta: int = None) -> bool:
        """Diminue le volume"""
        step = delta if delta is not None else self._mobile_volume_steps
        return await self.adjust_display_volume(-step)
    
    async def _apply_alsa_volume_direct(self, alsa_volume: int) -> bool:
        """Applique directement un volume ALSA"""
        try:
            return await self._set_amixer_volume_fast(alsa_volume)
        except Exception:
            return False
    
    async def _get_amixer_volume_fast(self) -> Optional[int]:
        """Version ultra-rapide d'amixer get"""
        current_time = time.time()
        
        if self._is_adjusting and (current_time - self._alsa_cache_time) < 0.01:
            return self._last_alsa_volume
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "get", "Digital",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )
            stdout, _ = await proc.communicate()
            
            if proc.returncode != 0:
                return self._last_alsa_volume
            
            output = stdout.decode()
            match = re.search(r'\[(\d+)%\]', output)
            
            if match:
                volume = int(match.group(1))
                self._last_alsa_volume = volume
                self._alsa_cache_time = current_time
                return volume
            else:
                return self._last_alsa_volume
        except Exception:
            return self._last_alsa_volume
    
    async def _set_amixer_volume_fast(self, alsa_volume: int) -> bool:
        """Version ultra-rapide d'amixer set"""
        try:
            limited_volume = self._clamp_alsa_volume(alsa_volume)
            
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "set", "Digital", f"{limited_volume}%",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            
            self._last_alsa_volume = limited_volume
            self._alsa_cache_time = time.time()
            return proc.returncode == 0
        except Exception:
            return False
    
    async def _get_snapcast_clients_cached(self):
        """Récupère les clients avec cache"""
        current_time = time.time() * 1000
        
        if (current_time - self._snapcast_cache_time) < self.SNAPCAST_CACHE_MS:
            return self._snapcast_clients_cache
        
        try:
            clients = await self.snapcast_service.get_clients()
            self._snapcast_clients_cache = clients
            self._snapcast_cache_time = current_time
            return clients
        except Exception:
            return self._snapcast_clients_cache
    
    async def _mark_adjustment_done(self):
        """Marque la fin d'ajustement"""
        await asyncio.sleep(0.15)
        self._is_adjusting = False
    
    async def _set_startup_volume_direct(self, alsa_volume: int) -> bool:
        """Force le volume ALSA en mode direct"""
        try:
            success = await self._set_amixer_volume_fast(alsa_volume)
            if success:
                display_volume = self._alsa_to_display(alsa_volume)
                self.logger.info(f"Direct startup volume: {display_volume}%")
            return success
        except Exception as e:
            self.logger.error(f"Error setting startup volume (direct): {e}")
            return False

    async def _set_startup_volume_multiroom(self, alsa_volume: int) -> bool:
        """Force tous les clients Snapcast au volume ALSA"""
        try:
            clients = await self.snapcast_service.get_clients()
            if not clients:
                return True
            
            display_volume = self._alsa_to_display(alsa_volume)
            snapcast_operations = []
            
            for client in clients:
                client_id = client["id"]
                snapcast_operations.append((client_id, alsa_volume))
                self._set_client_display_volume(client_id, float(display_volume))
            
            await asyncio.gather(
                *[self.snapcast_service.set_volume(client_id, alsa_vol) 
                  for client_id, alsa_vol in snapcast_operations]
            )
            
            self._multiroom_volume = float(display_volume)
            self._client_states_initialized = True
            self.logger.info(f"Multiroom startup volume: {display_volume}%")
            return True
        except Exception as e:
            self.logger.error(f"Error setting startup volume (multiroom): {e}")
            return False
    
    # === API PUBLIQUE ===
    
    def convert_alsa_to_display(self, alsa_volume: int) -> int:
        """Conversion ALSA → Display"""
        return self._alsa_to_display(alsa_volume)
    
    def convert_display_to_alsa(self, display_volume: int) -> int:
        """Conversion Display → ALSA"""
        return self._display_to_alsa(display_volume)
    
    def get_volume_config_public(self) -> Dict[str, Any]:
        """Retourne la config volume"""
        return {
            "alsa_min": self._alsa_min_volume,
            "alsa_max": self._alsa_max_volume,
            "startup_volume": self._default_startup_display_volume,
            "restore_last_volume": self._restore_last_volume,
            "mobile_steps": self._mobile_volume_steps
        }
    
    async def get_status(self) -> dict:
        """Récupère l'état complet du volume"""
        try:
            multiroom_enabled = self._is_multiroom_enabled()
            display_volume = await self.get_display_volume()
            
            base_status = {
                "volume": display_volume,
                "multiroom_enabled": multiroom_enabled,
                "mixer_available": self.mixer is not None,
                "display_volume": True,
                "config": {
                    "alsa_min": self._alsa_min_volume,
                    "alsa_max": self._alsa_max_volume,
                    "startup_volume": self._default_startup_display_volume,
                    "restore_last_volume": self._restore_last_volume,
                    "mobile_steps": self._mobile_volume_steps
                }
            }
            
            if multiroom_enabled:
                clients = await self._get_snapcast_clients_cached()
                base_status.update({
                    "mode": "multiroom_centralized",
                    "multiroom_volume": self._multiroom_volume,
                    "client_count": len(clients),
                    "snapcast_available": await self.snapcast_service.is_available()
                })
            else:
                base_status["mode"] = "direct"
            
            return base_status
        except Exception as e:
            self.logger.error(f"Error getting volume status: {e}")
            return {
                "volume": int(round(self._precise_display_volume)),
                "mode": "error",
                "error": str(e),
                "config": self.get_volume_config_public()
            }