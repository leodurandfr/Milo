# backend/infrastructure/services/volume_service.py
"""
Service de gestion du volume pour Milo - Version production OPTIM avec I/O async
"""
import asyncio
import logging
import alsaaudio
import re
import json
import os
import aiofiles
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
        self._rotary_volume_steps = 2
        
        # États internes
        self._precise_display_volume = 0.0
        self._multiroom_volume = 50.0
        self._last_alsa_volume = 0
        self._alsa_cache_time = 0
        self._adjustment_counter = 0  # Compteur pour gérer ajustements concurrents
        
        # États display par client
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

        # Task de sauvegarde volume
        self._save_volume_task = None

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
            self._rotary_volume_steps = volume_config["rotary_volume_steps"]
        except Exception as e:
            self.logger.error(f"Error loading volume config: {e}")
    
    def _save_last_volume(self, display_volume: int) -> None:
        """Sauvegarde le dernier volume en arrière-plan (vraiment async)"""
        if not self._restore_last_volume:
            return

        async def save_async():
            try:
                data = {"last_volume": display_volume, "timestamp": time.time()}
                temp_file = self.LAST_VOLUME_FILE.with_suffix('.tmp')
                async with aiofiles.open(temp_file, 'w') as f:
                    content = json.dumps(data)
                    await f.write(content)
                    await f.flush()
                temp_file.replace(self.LAST_VOLUME_FILE)
            except Exception as e:
                self.logger.error(f"Failed to save last volume: {e}")

        # Référencer la task pour éviter qu'elle ne se perde
        self._save_volume_task = asyncio.create_task(save_async())
    
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
        """Détermine le volume de démarrage"""
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

    async def reload_rotary_steps_config(self) -> bool:
        """Recharge les rotary steps"""
        try:
            self.settings_service._cache = None
            volume_config = self.settings_service.get_volume_config()
            self._rotary_volume_steps = volume_config["rotary_volume_steps"]
            return True
        except Exception as e:
            self.logger.error(f"Error reloading rotary steps: {e}")
            return False

    def _display_to_alsa_old_limits(self, display_volume: int, old_min: int, old_max: int) -> int:
        """Convertit avec les anciennes limites"""
        old_alsa_range = old_max - old_min
        return round((display_volume / 100) * old_alsa_range) + old_min
    
    # === CONVERSIONS ===
    
    def _alsa_to_display(self, alsa_volume: int) -> int:
        """Convertit ALSA → Display (0-100%)"""
        alsa_range = self._alsa_max_volume - self._alsa_min_volume
        normalized = alsa_volume - self._alsa_min_volume
        return round((normalized / alsa_range) * 100)

    def _display_to_alsa(self, display_volume: int) -> int:
        """Convertit Display → ALSA"""
        alsa_range = self._alsa_max_volume - self._alsa_min_volume
        return round((display_volume / 100) * alsa_range) + self._alsa_min_volume
    
    def _display_to_alsa_precise(self, display_volume: float) -> int:
        """Convertit Display précis → ALSA"""
        alsa_range = self._alsa_max_volume - self._alsa_min_volume
        return round((display_volume / 100.0) * alsa_range) + self._alsa_min_volume
    
    def _round_half_up(self, value: float) -> int:
        """Arrondi mathématique standard"""
        return int(value + 0.5)
    
    def _clamp_display_volume(self, display_volume: float) -> float:
        """Limite 0-100%"""
        return max(0.0, min(100.0, display_volume))
    
    def _clamp_alsa_volume(self, alsa_volume: int) -> int:
        """Limite entre min/max ALSA"""
        return max(self._alsa_min_volume, min(self._alsa_max_volume, alsa_volume))
    
    def _invalidate_all_caches(self) -> None:
        """Invalide tous les caches"""
        self._client_display_states = {}
        self._client_states_initialized = False
        self._snapcast_clients_cache = []
        self._snapcast_cache_time = 0
    
    # === GESTION CLIENT VOLUME ===
    
    async def initialize_new_client_volume(self, client_id: str, client_alsa_volume: int) -> bool:
        """Initialise nouveau client - Applique currentVolume en multiroom"""
        try:
            self.logger.info(f"🔵 initialize_new_client_volume: {client_id}, alsa={client_alsa_volume}")
            
            self.logger.info(f"  Current _client_display_states: {list(self._client_display_states.keys())}")
            
            if client_id in self._client_display_states:
                self.logger.info(f"⚪ Client {client_id} already known")
                await self.sync_client_volume_from_external(client_id, client_alsa_volume)
                return True
            
            self.logger.info(f"🟢 Client {client_id} is NEW")
            
            is_multiroom = self._is_multiroom_enabled()
            self.logger.info(f"  is_multiroom: {is_multiroom}")
            self.logger.info(f"  _multiroom_volume: {self._multiroom_volume}")
            
            if is_multiroom:
                target_display = self._multiroom_volume
                self.logger.info(f"  target_display: {target_display}")
                
                target_alsa = self._display_to_alsa_precise(target_display)
                self.logger.info(f"  target_alsa (before clamp): {target_alsa}")
                
                target_alsa_clamped = self._clamp_alsa_volume(target_alsa)
                self.logger.info(f"  target_alsa_clamped: {target_alsa_clamped}")
                
                self.logger.info(
                    f"🎯 Applying multiroom volume {self._round_half_up(target_display)}% "
                    f"(was {self._alsa_to_display(client_alsa_volume)}%)"
                )
                
                self.logger.info(f"  Calling snapcast_service.set_volume...")
                success = await self.snapcast_service.set_volume(client_id, target_alsa_clamped)
                self.logger.info(f"  snapcast set_volume returned: {success}")
                
                if success:
                    self._client_display_states[client_id] = target_display
                    
                    await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                        "client_id": client_id,
                        "volume": self._round_half_up(target_display),
                        "muted": False,
                        "source": "new_client_multiroom_sync"
                    })
                    
                    self.logger.info(f"✅ Client {client_id} synced")
                    return True
                else:
                    self.logger.error(f"❌ Failed to set volume for {client_id}")
                    return False
            else:
                self.logger.info(f"  Multiroom NOT enabled, using direct mode")
                display_volume = float(self._alsa_to_display(client_alsa_volume))
                self._client_display_states[client_id] = display_volume
                self.logger.info(f"⚪ Direct mode, keeping {self._round_half_up(display_volume)}%")
            
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Error initializing client: {e}", exc_info=True)
            return False
    
    async def _initialize_client_display_states(self) -> None:
        """Initialise les états display"""
        if self._client_states_initialized:
            return
            
        try:
            clients = await self._get_snapcast_clients_cached()
            total = 0.0
            count = 0
            
            for client in clients:
                client_id = client["id"]
                alsa_vol = client.get("volume", 0)
                display_vol = self._alsa_to_display(alsa_vol)
                self._client_display_states[client_id] = float(display_vol)
                total += display_vol
                count += 1
            
            if count > 0:
                self._multiroom_volume = total / count
            
            self._client_states_initialized = True
        except Exception as e:
            self.logger.error(f"Error initializing states: {e}")
    
    def _set_client_display_volume(self, client_id: str, display_volume: float) -> None:
        """Définit le volume display d'un client"""
        clamped = self._clamp_display_volume(display_volume)
        self._client_display_states[client_id] = clamped
    
    async def sync_client_volume_from_external(self, client_id: str, new_alsa_volume: int) -> None:
        """Sync externe"""
        if self._adjustment_counter > 0:
            return
        
        old = self._client_display_states.get(client_id, "unknown")
        new_display = float(self._alsa_to_display(new_alsa_volume))
        
        if isinstance(old, float) and abs(old - new_display) < 0.5:
            return
        
        self._client_display_states[client_id] = new_display
        
        try:
            await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                "client_id": client_id,
                "volume": self._round_half_up(new_display),
                "muted": False,
                "source": "external_sync"
            })
        except Exception as e:
            self.logger.error(f"Error broadcasting sync: {e}")
        
        if self._is_multiroom_enabled():
            await self._recalculate_multiroom_volume()
            await self._schedule_broadcast(show_bar=False)
    
    async def _recalculate_multiroom_volume(self) -> None:
        """Recalcule moyenne + sauvegarde"""
        try:
            clients = await self._get_snapcast_clients_cached()
            active = [c for c in clients if not c.get("muted", False)]
            
            if not active:
                return
            
            old_volume = self._multiroom_volume
            volumes = []
            
            for client in active:
                client_id = client["id"]
                precise = self._client_display_states.get(client_id)
                
                if precise is None:
                    alsa = client.get("volume", 0)
                    precise = float(self._alsa_to_display(alsa))
                    self._client_display_states[client_id] = precise
                
                volumes.append(precise)
            
            self._multiroom_volume = sum(volumes) / len(volumes)
            
            if abs(self._multiroom_volume - old_volume) > 0.5:
                rounded = self._round_half_up(self._multiroom_volume)
                self._save_last_volume(rounded)
            
        except Exception as e:
            self.logger.error(f"Error recalculating: {e}")
    
    # === INITIALISATION ===
    
    async def initialize(self) -> bool:
        """Initialise le service"""
        try:
            self._load_volume_config()
            
            try:
                self.mixer = alsaaudio.Mixer('Digital')
            except Exception as e:
                self.logger.error(f"Digital mixer not found: {e}")
                return False
            
            startup_display = self._determine_startup_volume()
            startup_alsa = self._display_to_alsa(startup_display)
            
            if self._is_multiroom_enabled():
                await self._set_startup_volume_multiroom(startup_alsa)
            else:
                await self._set_startup_volume_direct(startup_alsa)
            
            self._precise_display_volume = float(startup_display)
            self._multiroom_volume = float(startup_display)
            self._last_alsa_volume = startup_alsa
            self._alsa_cache_time = time.time()
            
            asyncio.create_task(self._delayed_initial_broadcast())
            return True
        except Exception as e:
            self.logger.error(f"Failed to initialize: {e}")
            return False
    
    async def _delayed_initial_broadcast(self):
        """Broadcast initial"""
        try:
            await asyncio.sleep(0.5)
            await self._schedule_broadcast(show_bar=False)
        except Exception as e:
            self.logger.error(f"Error in delayed broadcast: {e}")
    
    def _is_multiroom_enabled(self) -> bool:
        """Vérifie multiroom actif"""
        try:
            if not self.state_machine or not hasattr(self.state_machine, 'routing_service'):
                return False
            routing_state = self.state_machine.routing_service.get_state()
            return routing_state.get('multiroom_enabled', False)
        except Exception:
            return False
    
    # === API PUBLIQUE ===
    
    async def get_display_volume(self) -> int:
        """Volume centralisé"""
        if self._is_multiroom_enabled():
            return self._round_half_up(self._multiroom_volume)
        else:
            return self._round_half_up(self._precise_display_volume)
    
    async def set_display_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Définit le volume (0-100%) avec timeout"""
        try:
            async with asyncio.timeout(2.0):  # Timeout de 2 secondes
                async with self._volume_lock:
                    try:
                        self._adjustment_counter += 1
                        clamped = self._clamp_display_volume(float(display_volume))

                        if self._is_multiroom_enabled():
                            success = await self._set_multiroom_volume_centralized(int(clamped))
                        else:
                            alsa = self._display_to_alsa(int(clamped))
                            success = await self._apply_alsa_volume_direct(alsa)
                            if success:
                                self._precise_display_volume = clamped

                        if success:
                            self._save_last_volume(int(clamped))
                            await self._schedule_broadcast(show_bar)

                        asyncio.create_task(self._mark_adjustment_done())
                        return success
                    except Exception as e:
                        self.logger.error(f"Error setting volume: {e}")
                        self._adjustment_counter = max(0, self._adjustment_counter - 1)
                        return False
        except asyncio.TimeoutError:
            self.logger.error("Timeout waiting for volume lock (>2s)")
            return False
    
    async def adjust_display_volume(self, delta: int, show_bar: bool = True) -> bool:
        """Ajustement centralisé avec timeout"""
        try:
            async with asyncio.timeout(2.0):  # Timeout de 2 secondes
                async with self._volume_lock:
                    try:
                        self._adjustment_counter += 1

                        if self._is_multiroom_enabled():
                            success = await self._adjust_multiroom_volume_centralized(delta)
                        else:
                            success = await self._adjust_volume_direct(delta)

                        if success:
                            final = await self.get_display_volume()
                            self._save_last_volume(final)
                            await self._schedule_broadcast(show_bar)

                        asyncio.create_task(self._mark_adjustment_done())
                        return success
                    except Exception as e:
                        self.logger.error(f"Error adjusting: {e}")
                        self._adjustment_counter = max(0, self._adjustment_counter - 1)
                        return False
        except asyncio.TimeoutError:
            self.logger.error("Timeout waiting for volume lock (>2s)")
            return False
    
    async def _adjust_volume_direct(self, delta: int) -> bool:
        """Ajustement mode direct"""
        try:
            current = self._precise_display_volume
            new_precise = self._clamp_display_volume(current + delta)
            new_display = self._round_half_up(new_precise)
            new_alsa = self._display_to_alsa(new_display)
            
            success = await self._apply_alsa_volume_direct(new_alsa)
            if success:
                self._precise_display_volume = new_precise
            return success
        except Exception as e:
            self.logger.error(f"Error direct adjust: {e}")
            return False
    
    async def _adjust_multiroom_volume_centralized(self, delta: int) -> bool:
        """Ajustement multiroom précis"""
        try:
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                return False
            
            await self._initialize_client_display_states()
            
            operations = []
            events = []
            new_volumes = []
            
            for client in clients:
                client_id = client["id"]
                current = self._client_display_states.get(client_id)
                
                if current is None:
                    alsa = client.get("volume", 0)
                    current = float(self._alsa_to_display(alsa))
                    self._client_display_states[client_id] = current
                
                new_precise = self._clamp_display_volume(current + delta)
                self._client_display_states[client_id] = new_precise
                
                alsa = self._display_to_alsa_precise(new_precise)
                alsa_clamped = self._clamp_alsa_volume(alsa)
                
                operations.append((client_id, alsa_clamped))
                events.append({
                    "client_id": client_id,
                    "volume": self._round_half_up(new_precise),
                    "muted": client.get("muted", False)
                })
                
                if not client.get("muted", False):
                    new_volumes.append(new_precise)
            
            if new_volumes:
                self._multiroom_volume = sum(new_volumes) / len(new_volumes)
            
            await asyncio.gather(
                *[self.snapcast_service.set_volume(cid, alsa) for cid, alsa in operations]
            )
            
            for evt in events:
                await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                    "client_id": evt["client_id"],
                    "volume": evt["volume"],
                    "muted": evt["muted"],
                    "source": "multiroom_precise"
                })
            
            self._snapcast_clients_cache = []
            self._snapcast_cache_time = 0
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error multiroom adjust: {e}")
            return False
    
    async def _set_multiroom_volume_centralized(self, target: int) -> bool:
        """Set multiroom avec delta uniforme"""
        try:
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                return False
            
            await self._initialize_client_display_states()
            old = self._multiroom_volume
            delta = target - old
            
            operations = []
            events = []
            new_volumes = []
            
            for client in clients:
                client_id = client["id"]
                current = self._client_display_states.get(client_id)
                
                if current is None:
                    alsa = client.get("volume", 0)
                    current = float(self._alsa_to_display(alsa))
                    self._client_display_states[client_id] = current
                
                new_precise = self._clamp_display_volume(current + delta)
                self._client_display_states[client_id] = new_precise
                
                alsa = self._display_to_alsa_precise(new_precise)
                alsa_clamped = self._clamp_alsa_volume(alsa)
                
                operations.append((client_id, alsa_clamped))
                events.append({
                    "client_id": client_id,
                    "volume": self._round_half_up(new_precise),
                    "muted": client.get("muted", False)
                })
                
                if not client.get("muted", False):
                    new_volumes.append(new_precise)
            
            if new_volumes:
                self._multiroom_volume = sum(new_volumes) / len(new_volumes)
            else:
                self._multiroom_volume = float(target)
            
            await asyncio.gather(
                *[self.snapcast_service.set_volume(cid, alsa) for cid, alsa in operations]
            )
            
            for evt in events:
                await self.state_machine.broadcast_event("snapcast", "client_volume_changed", {
                    "client_id": evt["client_id"],
                    "volume": evt["volume"],
                    "muted": evt["muted"],
                    "source": "multiroom_uniform"
                })
            
            self._snapcast_clients_cache = []
            self._snapcast_cache_time = 0
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error set multiroom: {e}")
            return False
    
    # === BATCHING ===
    
    async def _schedule_broadcast(self, show_bar: bool = True) -> None:
        """Planifie broadcast"""
        self._pending_volume_broadcast = True
        self._pending_show_bar = self._pending_show_bar or show_bar
        
        if self._broadcast_task is None or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(self._execute_delayed_broadcast())
    
    async def _execute_delayed_broadcast(self) -> None:
        """Exécute broadcast"""
        try:
            await asyncio.sleep(self.BROADCAST_DELAY_MS / 1000)
            
            if not self._pending_volume_broadcast:
                return
            
            show_bar = self._pending_show_bar
            self._pending_volume_broadcast = False
            self._pending_show_bar = False
            
            volume = await self.get_display_volume()
            
            await self.state_machine.broadcast_event("volume", "volume_changed", {
                "volume": volume,
                "multiroom_mode": self._is_multiroom_enabled(),
                "show_bar": show_bar,
                "source": "volume_service",
                "mobile_steps": self._mobile_volume_steps
            })
        except Exception as e:
            self.logger.error(f"Error broadcast: {e}")
    
    # === UTILITAIRES ===
    
    async def increase_display_volume(self, delta: int = None) -> bool:
        """Augmente"""
        step = delta if delta is not None else self._mobile_volume_steps
        return await self.adjust_display_volume(step)
    
    async def decrease_display_volume(self, delta: int = None) -> bool:
        """Diminue"""
        step = delta if delta is not None else self._mobile_volume_steps
        return await self.adjust_display_volume(-step)
    
    async def _apply_alsa_volume_direct(self, alsa_volume: int) -> bool:
        """Applique ALSA direct"""
        try:
            return await self._set_amixer_volume_fast(alsa_volume)
        except Exception:
            return False
    
    async def _get_amixer_volume_fast(self) -> Optional[int]:
        """Lecture amixer rapide"""
        current_time = time.time()

        if self._adjustment_counter > 0 and (current_time - self._alsa_cache_time) < 0.01:
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
        """Écriture amixer rapide"""
        try:
            limited = self._clamp_alsa_volume(alsa_volume)
            
            proc = await asyncio.create_subprocess_exec(
                "amixer", "-M", "set", "Digital", f"{limited}%",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await proc.communicate()
            
            self._last_alsa_volume = limited
            self._alsa_cache_time = time.time()
            return proc.returncode == 0
        except Exception:
            return False
    
    async def _get_snapcast_clients_cached(self):
        """Clients avec cache"""
        current = time.time() * 1000
        
        if (current - self._snapcast_cache_time) < self.SNAPCAST_CACHE_MS:
            return self._snapcast_clients_cache
        
        try:
            clients = await self.snapcast_service.get_clients()
            self._snapcast_clients_cache = clients
            self._snapcast_cache_time = current
            return clients
        except Exception:
            return self._snapcast_clients_cache
    
    async def _mark_adjustment_done(self):
        """Fin ajustement"""
        await asyncio.sleep(0.15)
        self._adjustment_counter = max(0, self._adjustment_counter - 1)
    
    async def _set_startup_volume_direct(self, alsa_volume: int) -> bool:
        """Startup direct"""
        try:
            success = await self._set_amixer_volume_fast(alsa_volume)
            if success:
                display = self._alsa_to_display(alsa_volume)
                self.logger.info(f"Direct startup: {display}%")
            return success
        except Exception as e:
            self.logger.error(f"Error startup direct: {e}")
            return False

    async def _set_startup_volume_multiroom(self, alsa_volume: int) -> bool:
        """Startup multiroom"""
        try:
            clients = await self.snapcast_service.get_clients()
            if not clients:
                return True
            
            display = self._alsa_to_display(alsa_volume)
            operations = []
            
            for client in clients:
                client_id = client["id"]
                operations.append((client_id, alsa_volume))
                self._set_client_display_volume(client_id, float(display))
            
            await asyncio.gather(
                *[self.snapcast_service.set_volume(cid, alsa) for cid, alsa in operations]
            )
            
            self._multiroom_volume = float(display)
            self._client_states_initialized = True
            self.logger.info(f"Multiroom startup: {display}%")
            return True
        except Exception as e:
            self.logger.error(f"Error startup multiroom: {e}")
            return False
    
    # === API PUBLIQUE ===
    
    def convert_alsa_to_display(self, alsa_volume: int) -> int:
        """ALSA → Display"""
        return self._alsa_to_display(alsa_volume)
    
    def convert_display_to_alsa(self, display_volume: int) -> int:
        """Display → ALSA"""
        return self._display_to_alsa(display_volume)
    
    def get_volume_config_public(self) -> Dict[str, Any]:
        """Config volume"""
        return {
            "alsa_min": self._alsa_min_volume,
            "alsa_max": self._alsa_max_volume,
            "startup_volume": self._default_startup_display_volume,
            "restore_last_volume": self._restore_last_volume,
            "mobile_steps": self._mobile_volume_steps,
            "rotary_steps": self._rotary_volume_steps
        }
    
    def get_rotary_step(self) -> int:
        """Retourne le step actuel du rotary encoder"""
        return self._rotary_volume_steps
    
    async def get_status(self) -> dict:
        """État complet"""
        try:
            multiroom = self._is_multiroom_enabled()
            volume = await self.get_display_volume()

            status = {
                "volume": volume,
                "multiroom_enabled": multiroom,
                "mixer_available": self.mixer is not None,
                "display_volume": True,
                "config": self.get_volume_config_public()
            }

            if multiroom:
                clients = await self._get_snapcast_clients_cached()
                details = []

                for client in clients:
                    cid = client["id"]
                    precise = self._client_display_states.get(cid, "not_cached")

                    details.append({
                        "id": cid,
                        "name": client.get("name", "Unknown"),
                        "alsa_volume": client.get("volume", 0),
                        "display_volume_precise": precise,
                        "display_volume_rounded": self._round_half_up(precise) if isinstance(precise, float) else "N/A",
                        "muted": client.get("muted", False)
                    })

                status.update({
                    "mode": "multiroom",
                    "multiroom_volume": self._round_half_up(self._multiroom_volume),
                    "client_count": len(clients),
                    "clients": details
                })
            else:
                status.update({
                    "mode": "direct",
                    "precise_display_volume": self._precise_display_volume
                })

            return status
        except Exception as e:
            self.logger.error(f"Error status: {e}")
            return {"volume": 50, "mode": "error", "error": str(e)}

    async def cleanup(self) -> None:
        """Nettoie et attend la fin des tasks en cours"""
        try:
            # Attendre la fin de la task de broadcast si elle existe
            if self._broadcast_task and not self._broadcast_task.done():
                await self._broadcast_task

            # Attendre la fin de la task de sauvegarde volume si elle existe
            if self._save_volume_task and not self._save_volume_task.done():
                await self._save_volume_task

            self.logger.info("VolumeService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during volume service cleanup: {e}")