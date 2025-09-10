# backend/infrastructure/services/volume_service.py
"""
Service de gestion du volume pour Milo - Version avec état display découplé par client + LOGS COMPLETS
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
    """Service de gestion du volume système - Version avec état display découplé + LOGS"""
    
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
        
        # État interne - PRÉCIS en float pour éviter les erreurs d'arrondi (MODE DIRECT UNIQUEMENT)
        self._precise_display_volume = 0.0  # Volume display précis (float) - utilisé uniquement en mode direct
        self._last_alsa_volume = 0
        self._alsa_cache_time = 0
        self._is_adjusting = False
        
        # NOUVEAU : État display indépendant par client (découplé de l'ALSA)
        self._client_display_states = {}  # {client_id: display_volume_int}
        self._client_states_initialized = False
        
        # Cache clients snapcast (simple, sans volume précis)
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
        """Charge la configuration volume depuis settings avec cache invalidé"""
        try:
            # Invalider le cache du SettingsService pour forcer reload
            self.settings_service._cache = None
            
            volume_config = self.settings_service.get_volume_config()
            self._alsa_min_volume = volume_config["alsa_min"]
            self._alsa_max_volume = volume_config["alsa_max"]
            self._default_startup_display_volume = volume_config["startup_volume"]
            self._restore_last_volume = volume_config["restore_last_volume"]
            self._mobile_volume_steps = volume_config["mobile_volume_steps"]
            
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
            old_display_volume = await self.get_display_volume()
            old_alsa_min = self._alsa_min_volume
            old_alsa_max = self._alsa_max_volume
            
            # CORRECTION : Invalider le cache SettingsService AVANT de recharger
            self.settings_service._cache = None
            
            # Recharger la config
            self._load_volume_config()
            
            # Si les limites n'ont pas changé, pas besoin d'ajuster
            if old_alsa_min == self._alsa_min_volume and old_alsa_max == self._alsa_max_volume:
                self.logger.info("Volume limits unchanged, no adjustment needed")
                return True
            
            # NOUVEAU : Invalider les états clients pour forcer recalcul
            self._client_display_states = {}
            self._client_states_initialized = False
            
            # En mode direct : ajuster le volume interne précis
            if not self._is_multiroom_enabled():
                old_alsa_volume = self._display_to_alsa_old_limits(old_display_volume, old_alsa_min, old_alsa_max)
                new_display_volume = self._alsa_to_display(old_alsa_volume)
                
                if new_display_volume != old_display_volume:
                    if old_alsa_volume < self._alsa_min_volume or old_alsa_volume > self._alsa_max_volume:
                        # Volume hors des nouvelles limites → ramener au centre
                        center_volume = (self._alsa_min_volume + self._alsa_max_volume) // 2
                        safe_display_volume = self._alsa_to_display(center_volume)
                        
                        self.logger.info(f"Volume out of new limits, adjusting: {old_display_volume}% -> {safe_display_volume}%")
                        await self.set_display_volume(safe_display_volume, show_bar=False)
                    else:
                        # Volume dans les limites mais interpolation différente → ajuster
                        self.logger.info(f"Volume interpolation changed: {old_display_volume}% -> {new_display_volume}%")
                        self._precise_display_volume = float(new_display_volume)
                        await self._broadcast_volume_change(show_bar=False)
            # En mode multiroom : réinitialiser les états clients
            else:
                self.logger.info("Multiroom mode: client display states will be recalculated")
                await self._broadcast_volume_change(show_bar=False)
            
            self.logger.info("Volume limits reloaded successfully")
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

    async def reload_volume_steps_config(self) -> bool:
        """Recharge SEULEMENT les volume steps sans toucher au volume actuel"""
        try:
            # CORRECTION : Invalider le cache SettingsService pour forcer reload
            self.settings_service._cache = None
            
            # Recharger seulement les steps
            volume_config = self.settings_service.get_volume_config()
            self._mobile_volume_steps = volume_config["mobile_volume_steps"]
            
            # Diffuser le changement via WebSocket
            await self._broadcast_volume_change(show_bar=False)
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error reloading volume steps: {e}")
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
    
    def _clamp_display_volume(self, display_volume: float) -> float:
        """Limite le volume affiché entre 0-100% (garde la précision float)"""
        return max(0.0, min(100.0, display_volume))
    
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
    
    # === NOUVEAU : GESTION DES ÉTATS DISPLAY DÉCOUPLÉS ===
    
    async def _initialize_client_display_states(self) -> None:
        """Initialise les états display des clients depuis leurs volumes ALSA actuels"""
        if self._client_states_initialized:
            self.logger.info(f"🏁 INIT_STATES: Already initialized, skipping")
            return
            
        try:
            clients = await self._get_snapcast_clients_cached()
            self.logger.info(f"🏁 INIT_STATES: Initializing {len(clients)} clients")
            
            for client in clients:
                client_id = client["id"]
                client_name = client.get("name", "Unknown")
                alsa_volume = client.get("volume", 0)
                display_volume = self._alsa_to_display(alsa_volume)
                self._client_display_states[client_id] = display_volume
                self.logger.info(f"🏁 INIT_STATES: Client '{client_name}' ({client_id}) → {display_volume}% display from {alsa_volume} ALSA")
            
            self._client_states_initialized = True
            self.logger.info(f"🏁 INIT_STATES: Completed - {len(self._client_display_states)} clients initialized")
            self.logger.info(f"🏁 INIT_STATES: States = {self._client_display_states}")
            
        except Exception as e:
            self.logger.error(f"Error initializing client display states: {e}")
    
    def _get_client_display_volume(self, client_id: str, fallback_alsa: int = 0) -> int:
        """Récupère le volume display d'un client (état interne découplé)"""
        if client_id in self._client_display_states:
            volume = self._client_display_states[client_id]
            self.logger.info(f"🔍 GET_CLIENT_DISPLAY: {client_id} → {volume}% (from cache)")
            return volume
        else:
            # Fallback : calculer depuis ALSA et initialiser
            display_volume = self._alsa_to_display(fallback_alsa)
            self._client_display_states[client_id] = display_volume
            self.logger.info(f"🔍 GET_CLIENT_DISPLAY: {client_id} → {display_volume}% (fallback from ALSA {fallback_alsa})")
            return display_volume
    
    def _set_client_display_volume(self, client_id: str, display_volume: int) -> None:
        """Définit le volume display d'un client (état interne découplé)"""
        old_volume = self._client_display_states.get(client_id, "unknown")
        clamped_volume = max(0, min(100, display_volume))
        self._client_display_states[client_id] = clamped_volume
        self.logger.info(f"🔧 SET_CLIENT_DISPLAY: {client_id} → {old_volume}% ➤ {clamped_volume}%")
    
    def _sync_client_display_from_external(self, client_id: str, new_alsa_volume: int) -> None:
        """Synchronise l'état display quand un client est modifié depuis l'extérieur"""
        new_display_volume = self._alsa_to_display(new_alsa_volume)
        old_display_volume = self._client_display_states.get(client_id, "unknown")
        self._client_display_states[client_id] = new_display_volume
        self.logger.info(f"🔄 SYNC_CLIENT_EXTERNAL: {client_id} → {old_display_volume}% ➤ {new_display_volume}% (from ALSA {new_alsa_volume})")
    
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
            
            # État interne - volume précis en float (utilisé uniquement en mode direct)
            self._precise_display_volume = float(startup_display_volume)
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
    
    # === CALCUL DE MOYENNE DYNAMIQUE EN MULTIROOM (DEPUIS ÉTATS DISPLAY) ===
    
    async def _calculate_clients_average_from_display_states(self) -> Optional[int]:
        """Calcule la moyenne des volumes display des clients depuis leurs états internes"""
        try:
            # Initialiser les états si nécessaire
            await self._initialize_client_display_states()
            
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                self.logger.info(f"📊 AVERAGE_CALC: No snapcast clients for average calculation")
                return None
            
            # Filtrer les clients non-mutés
            active_clients = [c for c in clients if not c.get("muted", False)]
            
            if not active_clients:
                self.logger.info(f"📊 AVERAGE_CALC: No active (non-muted) clients, returning 0")
                return 0  # Retourner 0 si tous sont mutés
            
            self.logger.info(f"📊 AVERAGE_CALC: Calculating from {len(active_clients)} active clients")
            
            # Calculer la moyenne depuis les états display internes
            total_display_volume = 0
            client_volumes = []
            for client in active_clients:
                client_id = client["id"]
                client_name = client.get("name", "Unknown")
                alsa_volume = client.get("volume", 0)  # Pour fallback seulement
                display_volume = self._get_client_display_volume(client_id, alsa_volume)
                total_display_volume += display_volume
                client_volumes.append(f"{client_name}:{display_volume}%")
            
            # Moyenne arrondie à l'entier
            average = round(total_display_volume / len(active_clients))
            
            self.logger.info(f"📊 AVERAGE_CALC: Clients volumes: [{', '.join(client_volumes)}] → Average: {average}%")
            return average
            
        except Exception as e:
            self.logger.error(f"Error calculating clients average from display states: {e}")
            return None
    
    # === API PUBLIQUE - VOLUME AFFICHÉ (0-100%) ===
    
    async def get_display_volume(self) -> int:
        """Volume dynamique selon le mode - NOUVEAU : depuis états display en multiroom"""
        if self._is_multiroom_enabled():
            # Mode multiroom : calculer la moyenne depuis les états display découplés
            self.logger.info(f"🎯 GET_DISPLAY_VOLUME: Multiroom mode - calculating average from display states")
            average_volume = await self._calculate_clients_average_from_display_states()
            if average_volume is not None:
                self.logger.info(f"🎯 GET_DISPLAY_VOLUME: Returning multiroom average: {average_volume}%")
                return average_volume
            else:
                # Fallback si pas de clients ou erreur
                fallback_volume = int(round(self._precise_display_volume))
                self.logger.warning(f"🎯 GET_DISPLAY_VOLUME: Multiroom fallback: {fallback_volume}%")
                return fallback_volume
        else:
            # Mode direct : utiliser le volume interne précis
            direct_volume = int(round(self._precise_display_volume))
            self.logger.info(f"🎯 GET_DISPLAY_VOLUME: Direct mode: {direct_volume}%")
            return direct_volume
    
    async def set_display_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Définit le volume affiché (0-100%) avec sauvegarde automatique"""
        async with self._volume_lock:
            try:
                self._is_adjusting = True
                display_clamped = self._clamp_display_volume(float(display_volume))
                
                if self._is_multiroom_enabled():
                    success = await self._set_absolute_volume_multiroom_decoupled(int(display_clamped))
                else:
                    # Mode direct : logique existante
                    target_alsa_volume = self._display_to_alsa(int(display_clamped))
                    success = await self._apply_alsa_volume_direct(target_alsa_volume)
                    
                    if success:
                        self._precise_display_volume = display_clamped
                
                if success:
                    self._save_last_volume(int(display_clamped))
                    await self._broadcast_volume_change_fast(show_bar)
                
                self._is_adjusting = False
                return success
            except Exception as e:
                self.logger.error(f"Error setting display volume: {e}")
                self._is_adjusting = False
                return False
    
    async def adjust_display_volume(self, delta: int, show_bar: bool = True) -> bool:
        """DÉCOUPLÉ : Ajustements relatifs sur états display indépendants"""
        async with self._volume_lock:
            try:
                self.logger.info(f"🎵 ADJUST_DISPLAY_VOLUME: delta={delta}%, show_bar={show_bar}, multiroom={self._is_multiroom_enabled()}")
                
                # Marquer ajustement
                was_adjusting = self._is_adjusting
                self._is_adjusting = True
                
                # Compteur pour logs
                if not was_adjusting:
                    self._adjustment_count = 1
                    self._first_adjustment_time = time.time()
                else:
                    self._adjustment_count += 1
                
                if self._is_multiroom_enabled():
                    # NOUVELLE LOGIQUE MULTIROOM DÉCOUPLÉE : État display indépendant
                    self.logger.info(f"🔀 Using multiroom decoupled logic")
                    success = await self._apply_volume_delta_multiroom_decoupled(delta)
                else:
                    # Mode direct : logique existante avec volume précis
                    current_precise = self._precise_display_volume
                    new_precise = self._clamp_display_volume(current_precise + delta)
                    
                    current_display = int(round(current_precise))
                    new_display = int(round(new_precise))
                    
                    # Calculs ALSA
                    current_alsa = self._display_to_alsa(current_display)
                    new_alsa = self._display_to_alsa(new_display)
                    alsa_delta = new_alsa - current_alsa
                    
                    self.logger.info(f"🔀 Direct mode adjust: {current_display}% + {delta} = {new_display}% (ALSA: {current_alsa} → {new_alsa})")
                    
                    success = await self._apply_alsa_volume_direct(new_alsa)
                    
                    if success:
                        self._precise_display_volume = new_precise
                
                if success:
                    # En multiroom, get_display_volume() recalculera la moyenne depuis les états display
                    final_display_volume = await self.get_display_volume()
                    self.logger.info(f"🎵 ADJUST_VOLUME_SUCCESS: final_volume={final_display_volume}%")
                    self._save_last_volume(final_display_volume)
                    asyncio.create_task(self._broadcast_volume_change_fast(show_bar))
                else:
                    self.logger.error(f"🎵 ADJUST_VOLUME_FAILED")
                
                # Marquer fin d'ajustement
                asyncio.create_task(self._mark_adjustment_done())
                
                return success
                
            except Exception as e:
                self.logger.error(f"Error in adjust volume: {e}")
                self._is_adjusting = False
                return False
    
    async def _set_absolute_volume_multiroom_decoupled(self, target_display_volume: int) -> bool:
        """Définit un volume absolu en mode multiroom avec états découplés"""
        try:
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                return False
            
            # Initialiser les états si nécessaire
            await self._initialize_client_display_states()
            
            # Convertir le volume display cible vers ALSA  
            target_alsa_volume = self._display_to_alsa(target_display_volume)
            
            self.logger.info(f"Setting absolute multiroom volume (decoupled): {target_display_volume}% (ALSA: {target_alsa_volume}) for {len(clients)} clients")
            
            # Uniformiser tous les clients au même volume display ET ALSA
            for client in clients:
                client_id = client["id"]
                
                # Mettre à jour l'état display interne
                self._set_client_display_volume(client_id, target_display_volume)
                
                # Appliquer le volume ALSA
                await self.snapcast_service.set_volume(client_id, target_alsa_volume)
                
                self.logger.debug(f"Client {client['name']} set to {target_display_volume}% display ({target_alsa_volume} ALSA)")
            
            return True
        except Exception as e:
            self.logger.error(f"Error setting absolute multiroom volume (decoupled): {e}")
            return False
    
    async def _apply_volume_delta_multiroom_decoupled(self, delta: int) -> bool:
        """NOUVELLE MÉTHODE DÉCOUPLÉE : Delta sur états display indépendants"""
        try:
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                self.logger.warning(f"🔀 DELTA_MULTIROOM: No clients found")
                return False
            
            # Initialiser les états si nécessaire
            await self._initialize_client_display_states()
            
            self.logger.info(f"🔀 DELTA_MULTIROOM: Applying decoupled delta {delta}% to {len(clients)} clients individually")
            self.logger.info(f"🔀 DELTA_MULTIROOM: Current display states: {self._client_display_states}")
            
            # Appliquer le delta à chaque client sur son état display indépendant
            for client in clients:
                client_id = client["id"]
                client_name = client.get("name", "Unknown")
                current_alsa_volume = client.get("volume", 0)
                
                # 1. Récupérer l'état display actuel (indépendant)
                current_display_volume = self._get_client_display_volume(client_id, current_alsa_volume)
                
                # 2. Appliquer le delta sur l'état display (TOUJOURS possible)
                new_display_volume = max(0, min(100, current_display_volume + delta))
                
                # 3. Mettre à jour l'état display interne
                self._set_client_display_volume(client_id, new_display_volume)
                
                # 4. Convertir vers ALSA et appliquer
                new_alsa_volume = self._display_to_alsa(new_display_volume)
                new_alsa_volume = self._clamp_alsa_volume(new_alsa_volume)
                
                await self.snapcast_service.set_volume(client_id, new_alsa_volume)
                
                # Log détaillé pour debug
                alsa_changed = "✅" if new_alsa_volume != current_alsa_volume else "⚪"
                self.logger.info(f"🔀 Client '{client_name}': display {current_display_volume}% + {delta} = {new_display_volume}% (ALSA: {current_alsa_volume} → {new_alsa_volume}) {alsa_changed}")
            
            # Log final des états
            self.logger.info(f"🔀 DELTA_MULTIROOM: Final display states: {self._client_display_states}")
            
            # Invalider le cache clients pour forcer le recalcul
            self._snapcast_clients_cache = []
            self._snapcast_cache_time = 0
            self.logger.info(f"🔀 DELTA_MULTIROOM: Snapcast cache invalidated")
            
            return True
        except Exception as e:
            self.logger.error(f"Error in decoupled multiroom volume delta: {e}")
            return False
    
    # === MÉTHODES D'APPLICATION ALSA SIMPLIFIÉES ===
    
    async def _apply_alsa_volume_direct(self, alsa_volume: int) -> bool:
        """Applique directement un volume ALSA"""
        try:
            return await self._set_amixer_volume_fast(alsa_volume)
        except Exception:
            return False
    
    async def increase_display_volume(self, delta: int = None) -> bool:
        """Augmente le volume affiché avec steps configurables"""
        step = delta if delta is not None else self._mobile_volume_steps
        return await self.adjust_display_volume(step)
    
    async def decrease_display_volume(self, delta: int = None) -> bool:
        """Diminue le volume affiché avec steps configurables"""
        step = delta if delta is not None else self._mobile_volume_steps
        return await self.adjust_display_volume(-step)
    
    # === MÉTHODES AMIXER OPTIMISÉES ===
    
    async def _get_amixer_volume_fast(self) -> Optional[int]:
        """Version ultra-rapide d'amixer get - avec cache léger"""
        current_time = time.time()
        
        # Cache plus léger (seulement 10ms au lieu de 20ms)
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
    
    # === API PUBLIQUE POUR SYNCHRONISATION EXTERNE ===
    
    async def sync_client_volume_from_external(self, client_id: str, new_alsa_volume: int) -> None:
        """Synchronise l'état display quand un client est modifié depuis l'extérieur - SEULEMENT si pas en cours d'ajustement"""
        # NOUVEAU : Ne pas écraser les états display pendant nos ajustements
        if self._is_adjusting:
            self.logger.info(f"🔄 SYNC_CLIENT_EXTERNAL: Skipping sync for {client_id} - adjustment in progress")
            return
            
        self._sync_client_display_from_external(client_id, new_alsa_volume)
    
    # === MÉTHODES UTILITAIRES ===
    
    async def _mark_adjustment_done(self):
        """Marque la fin d'ajustement après un délai"""
        await asyncio.sleep(0.1)
        self._is_adjusting = False
        
        if self._adjustment_count > 1:
            duration = time.time() - self._first_adjustment_time
            self.logger.info(f"Adjustment burst: {self._adjustment_count} changes in {duration:.2f}s")
    
    async def _broadcast_volume_change_fast(self, show_bar: bool = True) -> None:
        """Broadcast optimisé pour rapidité AVEC mobile_steps"""
        try:
            # MODIFIÉ : Utiliser get_display_volume() dynamique (depuis états display en multiroom)
            current_volume = await self.get_display_volume()
            
            await self.state_machine.broadcast_event("volume", "volume_changed", {
                "volume": current_volume,
                "multiroom_mode": self._is_multiroom_enabled(),
                "show_bar": show_bar,
                "source": "volume_service",
                "mobile_steps": self._mobile_volume_steps
            })
        except Exception as e:
            if self._adjustment_count <= 3:
                self.logger.error(f"Error in fast broadcast: {e}")
    
    async def _broadcast_volume_change(self, show_bar: bool = True) -> None:
        """Broadcast standard AVEC mobile_steps"""
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
        """Force tous les clients Snapcast au volume ALSA et initialise les états display"""
        try:
            clients = await self.snapcast_service.get_clients()
            if not clients:
                self.logger.warning("No Snapcast clients found for startup volume")
                return True
            
            display_volume = self._alsa_to_display(alsa_volume)
            
            for client in clients:
                client_id = client["id"]
                
                # Appliquer le volume ALSA
                await self.snapcast_service.set_volume(client_id, alsa_volume)
                
                # Initialiser l'état display découplé
                self._set_client_display_volume(client_id, display_volume)
                
                self.logger.info(f"Startup: Client {client['name']} volume set to {alsa_volume} ({display_volume}% display)")
            
            self._client_states_initialized = True
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
    
    # === MÉTHODES DE COMPATIBILITÉ ===
    
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
        """Récupère l'état complet du volume avec configuration startup et mobile steps"""
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
                    "mode": "multiroom",
                    "client_count": len(clients),
                    "snapcast_available": await self.snapcast_service.is_available(),
                    "display_states_count": len(self._client_display_states)  # NOUVEAU : Info debug
                })
            else:
                base_status["mode"] = "direct"
            
            return base_status
            
        except Exception as e:
            self.logger.error(f"Error getting volume status: {e}")
            return {
                "volume": int(round(self._precise_display_volume)),  # Fallback sur volume interne
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