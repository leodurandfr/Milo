# backend/infrastructure/services/volume_service.py
"""
Service de gestion du volume pour Milo - Version avec startup volume configurable
"""
import asyncio
import logging
import alsaaudio
import re
import json
import os
from typing import Optional, Dict, Any
import time
from pathlib import Path
from backend.infrastructure.services.settings_service import SettingsService

class VolumeService:
    """Service de gestion du volume système - Version avec startup volume configurable"""
    
    # Fichier pour sauvegarder le dernier volume
    LAST_VOLUME_FILE = Path("/var/lib/milo/last_volume.json")
    
    def __init__(self, state_machine, snapcast_service):
        self.state_machine = state_machine
        self.snapcast_service = snapcast_service
        self.settings_service = SettingsService()
        self.mixer: Optional[alsaaudio.Mixer] = None
        self.logger = logging.getLogger(__name__)
        self._volume_lock = asyncio.Lock()
        
        # Variables de configuration (chargées depuis settings)
        self._alsa_min_volume = 0
        self._alsa_max_volume = 65
        self._default_startup_display_volume = 37
        self._restore_last_volume = False
        self._mobile_volume_steps = 5
        
        # État interne - toujours en volume affiché (0-100%)
        self._current_display_volume = 0
        self._last_alsa_volume = 0
        self._alsa_cache_time = 0
        self._is_adjusting = False
        
        # Cache clients snapcast
        self._snapcast_clients_cache = []
        self._snapcast_cache_time = 0
        self.SNAPCAST_CACHE_MS = 50
        
        # Optimisations logging
        self._adjustment_count = 0
        self._first_adjustment_time = 0
        
        # Créer le répertoire pour les données persistantes
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
            volume_config = self.settings_service.get_volume_config()
            self._alsa_min_volume = volume_config["alsa_min"]
            self._alsa_max_volume = volume_config["alsa_max"]
            self._default_startup_display_volume = volume_config["startup_volume"]
            self._restore_last_volume = volume_config["restore_last_volume"]
            self._mobile_volume_steps = volume_config["mobile_volume_steps"]
            
            self.logger.info(f"Volume config loaded: ALSA({self._alsa_min_volume}-{self._alsa_max_volume}), startup={self._default_startup_display_volume}% (restore_last={self._restore_last_volume}), steps={self._mobile_volume_steps}")
            
        except Exception as e:
            self.logger.error(f"Error loading volume config, using defaults: {e}")
            self._alsa_min_volume = 0
            self._alsa_max_volume = 65
            self._default_startup_display_volume = 37
            self._restore_last_volume = False
            self._mobile_volume_steps = 5
    
    def _save_last_volume(self, display_volume: int) -> None:
        """Sauvegarde le dernier volume (en arrière-plan pour éviter les blocages)"""
        if not self._restore_last_volume:
            return
            
        async def save_async():
            try:
                data = {
                    "last_volume": display_volume,
                    "timestamp": time.time()
                }
                
                # Écriture atomique via fichier temporaire
                temp_file = self.LAST_VOLUME_FILE.with_suffix('.tmp')
                with open(temp_file, 'w') as f:
                    json.dump(data, f)
                temp_file.replace(self.LAST_VOLUME_FILE)
                
                self.logger.debug(f"Last volume saved: {display_volume}%")
                
            except Exception as e:
                self.logger.error(f"Failed to save last volume: {e}")
        
        asyncio.create_task(save_async())
    
    def _load_last_volume(self) -> Optional[int]:
        """Charge le dernier volume sauvegardé"""
        try:
            if not self.LAST_VOLUME_FILE.exists():
                self.logger.info("No last volume file found")
                return None
            
            with open(self.LAST_VOLUME_FILE, 'r') as f:
                data = json.load(f)
            
            last_volume = data.get('last_volume')
            timestamp = data.get('timestamp', 0)
            
            # Vérifier que le fichier n'est pas trop ancien (max 7 jours)
            age_days = (time.time() - timestamp) / (24 * 3600)
            if age_days > 7:
                self.logger.info(f"Last volume file too old ({age_days:.1f} days), ignoring")
                return None
            
            # Valider que le volume est dans les limites actuelles
            if not (0 <= last_volume <= 100):
                self.logger.warning(f"Last volume out of range: {last_volume}%, ignoring")
                return None
            
            self.logger.info(f"Loaded last volume: {last_volume}% (saved {age_days:.1f} days ago)")
            return last_volume
            
        except Exception as e:
            self.logger.error(f"Failed to load last volume: {e}")
            return None
    
    def _determine_startup_volume(self) -> int:
        """Détermine le volume de démarrage selon la configuration"""
        if self._restore_last_volume:
            # Mode restore_last : charger le dernier volume
            last_volume = self._load_last_volume()
            if last_volume is not None:
                self.logger.info(f"Using restored volume: {last_volume}%")
                return last_volume
            else:
                # Fallback sur le volume fixe si pas de fichier
                self.logger.info(f"No valid last volume, falling back to fixed: {self._default_startup_display_volume}%")
                return self._default_startup_display_volume
        else:
            # Mode fixe : utiliser startup_volume
            self.logger.info(f"Using fixed startup volume: {self._default_startup_display_volume}%")
            return self._default_startup_display_volume
    
    async def reload_volume_limits(self) -> bool:
        """Recharge SEULEMENT les limites de volume et ajuste le volume actuel si nécessaire"""
        try:
            self.logger.info("Reloading volume limits...")
            
            # Sauvegarder l'ancien volume affiché et les anciennes limites
            old_display_volume = self._current_display_volume
            old_alsa_min = self._alsa_min_volume
            old_alsa_max = self._alsa_max_volume
            
            # Recharger la config
            self._load_volume_config()
            
            # Si les limites n'ont pas changé, pas besoin d'ajuster
            if old_alsa_min == self._alsa_min_volume and old_alsa_max == self._alsa_max_volume:
                self.logger.info("Volume limits unchanged, no adjustment needed")
                return True
            
            # Recalculer les plages avec les nouvelles limites
            old_alsa_volume = self._display_to_alsa_old_limits(old_display_volume, old_alsa_min, old_alsa_max)
            new_display_volume = self._alsa_to_display(old_alsa_volume)
            
            # Si le volume calculé avec les nouvelles limites est différent
            if new_display_volume != old_display_volume:
                # Ajuster le volume actuel avec les nouvelles limites
                if old_alsa_volume < self._alsa_min_volume or old_alsa_volume > self._alsa_max_volume:
                    # Volume hors des nouvelles limites -> ramener au centre
                    center_volume = (self._alsa_min_volume + self._alsa_max_volume) // 2
                    safe_display_volume = self._alsa_to_display(center_volume)
                    
                    self.logger.info(f"Volume out of new limits, adjusting: {old_display_volume}% -> {safe_display_volume}%")
                    await self.set_display_volume(safe_display_volume, show_bar=False)
                else:
                    # Volume dans les limites mais interpolation différente -> ajuster
                    self.logger.info(f"Volume interpolation changed: {old_display_volume}% -> {new_display_volume}%")
                    self._current_display_volume = new_display_volume
                    await self._broadcast_volume_change(show_bar=False)
            
            self.logger.info(f"Volume limits reloaded successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Error reloading volume limits: {e}")
            return False
    
    async def reload_startup_config(self) -> bool:
        """Recharge SEULEMENT la config de startup sans toucher au volume actuel"""
        try:
            self.logger.info("Reloading startup volume config...")
            
            # Sauvegarder les anciennes valeurs startup
            old_startup_volume = self._default_startup_display_volume
            old_restore_last = self._restore_last_volume
            
            # Recharger seulement la config startup
            volume_config = self.settings_service.get_volume_config()
            self._default_startup_display_volume = volume_config["startup_volume"]
            self._restore_last_volume = volume_config["restore_last_volume"]
            # NE PAS toucher aux limites ALSA ni au volume actuel
            
            self.logger.info(f"Startup config reloaded: startup={self._default_startup_display_volume}% (restore_last={self._restore_last_volume})")
            return True
            
        except Exception as e:
            self.logger.error(f"Error reloading startup config: {e}")
            return False

    async def sync_current_volume_to_all_snapcast_clients(self) -> bool:
        """Synchronise le volume actuel avec tous les clients Snapcast"""
        try:
            if not self._is_multiroom_enabled():
                self.logger.warning("Multiroom not enabled, cannot sync to snapcast clients")
                return False
                
            current_display_volume = self._current_display_volume
            current_alsa_volume = self._display_to_alsa(current_display_volume)
            
            clients = await self.snapcast_service.get_clients()
            if not clients:
                self.logger.info("No Snapcast clients to sync")
                return True
            
            self.logger.info(f"Syncing current volume ({current_display_volume}% = {current_alsa_volume} ALSA) to {len(clients)} Snapcast clients")
            
            # Appliquer le volume actuel à tous les clients
            for client in clients:
                await self.snapcast_service.set_volume(client["id"], current_alsa_volume)
                self.logger.debug(f"Synced volume to client {client['name']}: {current_alsa_volume} ALSA")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error syncing volume to snapcast clients: {e}")
            return False
    
    def _display_to_alsa_old_limits(self, display_volume: int, old_min: int, old_max: int) -> int:
        """Convertit avec les anciennes limites (pour comparaison lors du reload)"""
        old_alsa_range = old_max - old_min
        return round((display_volume / 100) * old_alsa_range) + old_min
    
    # === INTERPOLATION DYNAMIQUE ===
    
    def _alsa_to_display(self, alsa_volume: int) -> int:
        """Convertit volume ALSA vers volume affiché (0-100%) avec limites configurables"""
        alsa_range = self._alsa_max_volume - self._alsa_min_volume
        normalized = alsa_volume - self._alsa_min_volume
        return round((normalized / alsa_range) * 100)

    def _display_to_alsa(self, display_volume: int) -> int:
        """Convertit volume affiché (0-100%) vers volume ALSA avec limites configurables"""
        alsa_range = self._alsa_max_volume - self._alsa_min_volume
        return round((display_volume / 100) * alsa_range) + self._alsa_min_volume
    
    def _clamp_display_volume(self, display_volume: int) -> int:
        """Limite le volume affiché entre 0-100%"""
        return max(0, min(100, display_volume))
    
    def _clamp_alsa_volume(self, alsa_volume: int) -> int:
        """Limite le volume ALSA entre les limites configurées"""
        return max(self._alsa_min_volume, min(self._alsa_max_volume, alsa_volume))
    
    # === CONVERSIONS SNAPCAST ===
    
    def _snapcast_to_display(self, snapcast_volume: int) -> int:
        """Convertit volume Snapcast vers volume affiché (utilise les mêmes limites que ALSA)"""
        snapcast_range = self._alsa_max_volume - self._alsa_min_volume
        normalized = snapcast_volume - self._alsa_min_volume  
        return round((normalized / snapcast_range) * 100)

    def _display_to_snapcast(self, display_volume: int) -> int:
        """Convertit volume affiché vers volume Snapcast (utilise les mêmes limites que ALSA)"""
        snapcast_range = self._alsa_max_volume - self._alsa_min_volume
        return round((display_volume / 100) * snapcast_range) + self._alsa_min_volume
    
    # === INITIALISATION AVEC STARTUP VOLUME ===
    
    async def initialize(self) -> bool:
        """Initialise le service volume avec startup volume configurable"""
        try:
            # Charger la configuration volume
            self._load_volume_config()
            
            self.logger.info(f"Initializing volume service with configurable startup (ALSA: {self._alsa_min_volume}-{self._alsa_max_volume})")
            
            try:
                self.mixer = alsaaudio.Mixer('Digital')
                self.logger.info("ALSA Digital mixer found")
            except Exception as e:
                self.logger.error(f"Digital mixer not found: {e}")
                return False
            
            # Déterminer le volume de démarrage selon la configuration
            startup_display_volume = self._determine_startup_volume()
            startup_alsa_volume = self._display_to_alsa(startup_display_volume)
            
            if self._is_multiroom_enabled():
                await self._set_startup_volume_multiroom(startup_alsa_volume)
            else:
                await self._set_startup_volume_direct(startup_alsa_volume)
            
            # État interne
            self._current_display_volume = startup_display_volume
            self._last_alsa_volume = startup_alsa_volume
            self._alsa_cache_time = time.time()
            
            mode_info = "restored" if self._restore_last_volume else "fixed"
            self.logger.info(f"Startup volume set to {startup_display_volume}% ({mode_info}) = {startup_alsa_volume} (ALSA)")
            
            asyncio.create_task(self._delayed_initial_broadcast())
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to initialize volume service: {e}")
            return False
    
    async def _delayed_initial_broadcast(self):
        """Diffuse l'état initial"""
        try:
            await asyncio.sleep(0.5)
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
    
    # === API PUBLIQUE - VOLUME AFFICHÉ (0-100%) AVEC SAUVEGARDE ===
    
    async def get_display_volume(self) -> int:
        """Récupère le volume affiché actuel (0-100%)"""
        try:
            if self._is_multiroom_enabled():
                return await self._get_display_volume_multiroom()
            else:
                return await self._get_display_volume_direct()
        except Exception:
            return self._current_display_volume
    
    async def set_display_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Définit le volume affiché (0-100%) avec sauvegarde automatique"""
        async with self._volume_lock:
            try:
                self._is_adjusting = True
                display_clamped = self._clamp_display_volume(display_volume)
                
                if self._is_multiroom_enabled():
                    success = await self._set_display_volume_multiroom(display_clamped)
                else:
                    success = await self._set_display_volume_direct(display_clamped)
                
                if success:
                    self._current_display_volume = display_clamped
                    # Sauvegarder le volume si restore_last est activé
                    self._save_last_volume(display_clamped)
                    await self._broadcast_volume_change_fast(show_bar)
                
                self._is_adjusting = False
                return success
            except Exception as e:
                self.logger.error(f"Error setting display volume: {e}")
                self._is_adjusting = False
                return False
    
    async def adjust_display_volume(self, delta: int, show_bar: bool = True) -> bool:
        """Ajuste le volume affiché par delta avec sauvegarde automatique"""
        async with self._volume_lock:
            try:
                # Marquer ajustement
                was_adjusting = self._is_adjusting
                self._is_adjusting = True
                
                # Compteur pour logs
                if not was_adjusting:
                    self._adjustment_count = 1
                    self._first_adjustment_time = time.time()
                else:
                    self._adjustment_count += 1
                
                # Récupérer le volume réel actuel
                current_volume = await self.get_display_volume()
                new_volume = self._clamp_display_volume(current_volume + delta)
                
                # Log optimisé
                if self._adjustment_count <= 3 or self._adjustment_count % 5 == 0:
                    self.logger.debug(f"Fast adjust #{self._adjustment_count}: {current_volume}% + {delta} = {new_volume}%")
                
                # Application
                if self._is_multiroom_enabled():
                    success = await self._set_display_volume_multiroom(new_volume)
                else:
                    success = await self._set_display_volume_direct(new_volume)
                
                if success:
                    self._current_display_volume = new_volume
                    # Sauvegarder le volume si restore_last est activé
                    self._save_last_volume(new_volume)
                    asyncio.create_task(self._broadcast_volume_change_fast(show_bar))
                
                # Marquer fin d'ajustement
                asyncio.create_task(self._mark_adjustment_done())
                
                return success
                
            except Exception as e:
                self.logger.error(f"Error in fast adjust: {e}")
                self._is_adjusting = False
                return False
    
    async def increase_display_volume(self, delta: int = None) -> bool:
        """Augmente le volume affiché avec steps configurables"""
        step = delta if delta is not None else self._mobile_volume_steps
        return await self.adjust_display_volume(step)
    
    async def decrease_display_volume(self, delta: int = None) -> bool:
        """Diminue le volume affiché avec steps configurables"""
        step = delta if delta is not None else self._mobile_volume_steps
        return await self.adjust_display_volume(-step)
    
    # === IMPLÉMENTATIONS PRIVÉES - MODE DIRECT ===
    
    async def _get_display_volume_direct(self) -> int:
        """Récupère le volume affiché en mode direct"""
        alsa_volume = await self._get_amixer_volume_fast()
        if alsa_volume is None:
            return self._current_display_volume
        return self._alsa_to_display(alsa_volume)
    
    async def _set_display_volume_direct(self, display_volume: int) -> bool:
        """Définit le volume affiché en mode direct"""
        try:
            alsa_volume = self._display_to_alsa(display_volume)
            return await self._set_amixer_volume_fast(alsa_volume)
        except Exception:
            return False
    
    # === IMPLÉMENTATIONS PRIVÉES - MODE MULTIROOM ===
    
    async def _get_display_volume_multiroom(self) -> int:
        """Récupère le volume affiché moyen en mode multiroom"""
        try:
            clients = await self._get_snapcast_clients_cached()
            
            if not clients:
                return self._current_display_volume
            
            # Les volumes clients viennent de Snapcast, les convertir vers volume affiché
            total_display_volume = sum(self._snapcast_to_display(client["volume"]) for client in clients)
            average_display_volume = round(total_display_volume / len(clients))
            return average_display_volume
        except Exception:
            return self._current_display_volume
    
    async def _set_display_volume_multiroom(self, display_volume: int) -> bool:
        """Définit le volume affiché en mode multiroom avec delta uniforme"""
        try:
            clients = await self._get_snapcast_clients_cached()
            
            if not clients:
                return False
            
            # Calculer la moyenne actuelle en supposant que client["volume"] vient de Snapcast
            current_snapcast_volumes = [client["volume"] for client in clients]
            current_average_snapcast = sum(current_snapcast_volumes) / len(current_snapcast_volumes)
            
            # Convertir la moyenne Snapcast vers volume affiché pour calcul du delta
            current_average_display = self._snapcast_to_display(current_average_snapcast)
            delta_display = display_volume - current_average_display
            
            # Appliquer le delta à chaque client
            for client in clients:
                current_client_snapcast = client["volume"]
                current_client_display = self._snapcast_to_display(current_client_snapcast)
                new_client_display = self._clamp_display_volume(round(current_client_display + delta_display))
                new_client_snapcast = self._display_to_snapcast(new_client_display)
                
                await self.snapcast_service.set_volume(client["id"], new_client_snapcast)
            
            return True
        except Exception:
            return False
    
    # === MÉTHODES AMIXER OPTIMISÉES ===
    
    async def _get_amixer_volume_fast(self) -> Optional[int]:
        """Version ultra-rapide d'amixer get - avec cache agressif"""
        current_time = time.time()
        
        if self._is_adjusting and (current_time - self._alsa_cache_time) < 0.02:
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
    
    # === CACHE SNAPCAST ===
    
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
            return self._snapcast_clients_cache
    
    # === MÉTHODES UTILITAIRES ===
    
    async def _mark_adjustment_done(self):
        """Marque la fin d'ajustement après un délai"""
        await asyncio.sleep(0.1)
        self._is_adjusting = False
        
        if self._adjustment_count > 1:
            duration = time.time() - self._first_adjustment_time
            self.logger.info(f"Adjustment burst: {self._adjustment_count} changes in {duration:.2f}s")
    
    async def _broadcast_volume_change_fast(self, show_bar: bool = True) -> None:
        """Broadcast optimisé pour rapidité"""
        try:
            await self.state_machine.broadcast_event("volume", "volume_changed", {
                "volume": self._current_display_volume,
                "multiroom_mode": self._is_multiroom_enabled(),
                "show_bar": show_bar,
                "source": "volume_service",
                "mobile_steps": self._mobile_volume_steps
            })
        except Exception as e:
            if self._adjustment_count <= 3:
                self.logger.error(f"Error in fast broadcast: {e}")
    
    async def _broadcast_volume_change(self, show_bar: bool = True) -> None:
        """Broadcast standard"""
        await self._broadcast_volume_change_fast(show_bar)
    
    async def _set_startup_volume_direct(self, alsa_volume: int) -> bool:
        """Force le volume ALSA en mode direct"""
        try:
            success = await self._set_amixer_volume_fast(alsa_volume)
            if success:
                display_volume = self._alsa_to_display(alsa_volume)
                self.logger.info(f"Direct mode: ALSA volume set to {alsa_volume} ({display_volume}% display)")
            return success
        except Exception as e:
            self.logger.error(f"Error setting startup volume (direct): {e}")
            return False

    async def _set_startup_volume_multiroom(self, alsa_volume: int) -> bool:
        """Force tous les clients Snapcast au volume ALSA"""
        try:
            clients = await self.snapcast_service.get_clients()
            if not clients:
                self.logger.warning("No Snapcast clients found for startup volume")
                return True
            
            display_volume = self._alsa_to_display(alsa_volume)
            for client in clients:
                await self.snapcast_service.set_volume(client["id"], alsa_volume)
                self.logger.info(f"Multiroom: Client {client['name']} volume set to {alsa_volume} ({display_volume}% display)")
            
            return True
        except Exception as e:
            self.logger.error(f"Error setting startup volume (multiroom): {e}")
            return False
    
    # === API DE CONVERSION PUBLIQUE ===
    
    def convert_alsa_to_display(self, alsa_volume: int) -> int:
        """Conversion publique ALSA → Display (pour les composants frontend)"""
        return self._alsa_to_display(alsa_volume)
    
    def convert_display_to_alsa(self, display_volume: int) -> int:
        """Conversion publique Display → ALSA (pour les composants frontend)"""
        return self._display_to_alsa(display_volume)
    
    def get_volume_config_public(self) -> Dict[str, Any]:
        """Retourne la config volume pour les composants frontend"""
        return {
            "alsa_min": self._alsa_min_volume,
            "alsa_max": self._alsa_max_volume,
            "startup_volume": self._default_startup_display_volume,
            "restore_last_volume": self._restore_last_volume,
            "mobile_steps": self._mobile_volume_steps
        }
    
    async def get_volume(self) -> int:
        """Compatibilité: Utiliser get_display_volume()"""
        return await self.get_display_volume()
    
    async def set_volume(self, volume: int, show_bar: bool = True) -> bool:
        """Compatibilité: Utiliser set_display_volume()"""
        return await self.set_display_volume(volume, show_bar)
    
    async def adjust_volume(self, delta: int, show_bar: bool = True) -> bool:
        """Compatibilité: Utiliser adjust_display_volume()"""
        return await self.adjust_display_volume(delta, show_bar)
    
    async def increase_volume(self) -> bool:
        """Augmente le volume avec steps configurables"""
        return await self.increase_display_volume()
    
    async def decrease_volume(self) -> bool:
        """Diminue le volume avec steps configurables"""
        return await self.decrease_display_volume()
    
    # === STATUS AVEC STARTUP CONFIG ===
    
    async def get_status(self) -> dict:
        """Récupère l'état complet du volume avec configuration startup"""
        try:
            multiroom_enabled = self._is_multiroom_enabled()
            display_volume = self._current_display_volume
            
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
                    "mode": "multiroom",
                    "client_count": len(clients),
                    "snapcast_available": await self.snapcast_service.is_available()
                })
            else:
                base_status["mode"] = "direct"
            
            return base_status
            
        except Exception as e:
            self.logger.error(f"Error getting volume status: {e}")
            return {
                "volume": self._current_display_volume,
                "mode": "error",
                "error": str(e),
                "display_volume": True,
                "config": {
                    "alsa_min": self._alsa_min_volume,
                    "alsa_max": self._alsa_max_volume,
                    "startup_volume": self._default_startup_display_volume,
                    "restore_last_volume": self._restore_last_volume,
                    "mobile_steps": self._mobile_volume_steps
                }
            }