# backend/infrastructure/services/volume_service.py
"""
Service de gestion du volume pour Milo - Version SIMPLE ET FONCTIONNELLE (retour aux bases)
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
    """Service de gestion du volume système - Version SIMPLE avec états display découplés purs"""
    
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
        
        # NOUVEAU : État display indépendant par client (découplé de l'ALSA) - PUREMENT EN DISPLAY
        self._client_display_states = {}  # {client_id: display_volume_float} - États purs sans conversion
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
            
            # NOUVEAU : Invalider les caches
            self._invalidate_all_caches()
            
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
            
            # Recharger seulement la config startup
            volume_config = self.settings_service.get_volume_config()
            self._default_startup_display_volume = volume_config["startup_volume"]
            self._restore_last_volume = volume_config["restore_last_volume"]
            
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
    
    # === GESTION OPTIMISÉE DES CACHES ===
    
    def _invalidate_all_caches(self) -> None:
        """Invalide tous les caches"""
        self._client_display_states = {}
        self._client_states_initialized = False
        self._snapcast_clients_cache = []
        self._snapcast_cache_time = 0
        self.logger.debug("All caches invalidated")
    
    # === GESTION DES ÉTATS DISPLAY DÉCOUPLÉS PURS SIMPLE ===
    
    async def _initialize_client_display_states(self) -> None:
        """Initialise les états display des clients depuis leurs volumes ALSA actuels"""
        if self._client_states_initialized:
            return
            
        try:
            clients = await self._get_snapcast_clients_cached()
            self.logger.info(f"🏁 INIT_STATES: Initializing {len(clients)} clients")
            
            for client in clients:
                client_id = client["id"]
                client_name = client.get("name", "Unknown")
                alsa_volume = client.get("volume", 0)
                display_volume = self._alsa_to_display(alsa_volume)
                # NOUVEAU : Stocker en float pour éviter les erreurs d'arrondi
                self._client_display_states[client_id] = float(display_volume)
                self.logger.debug(f"Client '{client_name}' → {display_volume}% display")
            
            self._client_states_initialized = True
            self.logger.info(f"🏁 INIT_STATES: {len(self._client_display_states)} clients initialized")
            
        except Exception as e:
            self.logger.error(f"Error initializing client display states: {e}")
    
    def _get_client_display_volume(self, client_id: str, fallback_alsa: int = 0) -> float:
        """Récupère le volume display d'un client (état interne découplé) - RETOURNE FLOAT"""
        if client_id in self._client_display_states:
            return self._client_display_states[client_id]
        else:
            # Fallback : calculer depuis ALSA et initialiser
            display_volume = float(self._alsa_to_display(fallback_alsa))
            self._client_display_states[client_id] = display_volume
            self.logger.debug(f"🔍 GET_CLIENT_DISPLAY: {client_id} → {display_volume}% (fallback)")
            return display_volume
    
    def _set_client_display_volume(self, client_id: str, display_volume: float) -> None:
        """Définit le volume display d'un client (état interne découplé) - ACCEPTE FLOAT"""
        clamped_volume = self._clamp_display_volume(display_volume)
        self._client_display_states[client_id] = clamped_volume
        self.logger.debug(f"🔧 SET_CLIENT_DISPLAY: {client_id} → {clamped_volume}%")
    
    def _sync_client_display_from_external(self, client_id: str, new_alsa_volume: int) -> None:
        """Synchronise l'état display quand un client est modifié depuis l'extérieur"""
        new_display_volume = float(self._alsa_to_display(new_alsa_volume))
        self._client_display_states[client_id] = new_display_volume
        self.logger.debug(f"🔄 SYNC_CLIENT_EXTERNAL: {client_id} → {new_display_volume}%")
    
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
    
    # === CALCUL DE MOYENNE SIMPLE ===
    
    async def _calculate_clients_average_simple(self) -> Optional[int]:
        """Calcule la moyenne SIMPLE depuis les états display purs"""
        try:
            # Assurer l'initialisation une seule fois
            await self._initialize_client_display_states()
            
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                self.logger.debug(f"📊 AVERAGE_CALC: No snapcast clients")
                return None
            
            # Filtrer les clients non-mutés
            active_clients = [c for c in clients if not c.get("muted", False)]
            
            if not active_clients:
                self.logger.debug(f"📊 AVERAGE_CALC: No active clients, returning 0")
                return 0  # Retourner 0 si tous sont mutés
            
            # Calculer la moyenne depuis les états display internes (FLOAT PRECISION)
            total_display_volume = 0.0
            client_volumes = []
            for client in active_clients:
                client_id = client["id"]
                client_name = client.get("name", "Unknown")
                alsa_volume = client.get("volume", 0)  # Pour fallback seulement
                display_volume = self._get_client_display_volume(client_id, alsa_volume)
                total_display_volume += display_volume
                client_volumes.append(f"{client_name}:{display_volume:.1f}%")
            
            # Moyenne en float puis arrondie à l'entier pour affichage
            average_float = total_display_volume / len(active_clients)
            average_int = round(average_float)
            
            self.logger.info(f"📊 AVERAGE_CALC: [{', '.join(client_volumes)}] → {average_float:.2f}% → {average_int}%")
            return average_int
            
        except Exception as e:
            self.logger.error(f"Error calculating clients average: {e}")
            return None
    
    # === API PUBLIQUE SIMPLE - VOLUME AFFICHÉ (0-100%) ===
    
    async def get_display_volume(self) -> int:
        """Volume dynamique selon le mode - VERSION SIMPLE"""
        if self._is_multiroom_enabled():
            # Mode multiroom : calculer la moyenne depuis les états display découplés PURS
            average_volume = await self._calculate_clients_average_simple()
            if average_volume is not None:
                return average_volume
            else:
                # Fallback si pas de clients ou erreur
                return int(round(self._precise_display_volume))
        else:
            # Mode direct : utiliser le volume interne précis
            return int(round(self._precise_display_volume))
    
    async def set_display_volume(self, display_volume: int, show_bar: bool = True) -> bool:
        """Définit le volume affiché (0-100%) avec sauvegarde automatique"""
        async with self._volume_lock:
            try:
                self._is_adjusting = True
                display_clamped = self._clamp_display_volume(float(display_volume))
                
                if self._is_multiroom_enabled():
                    success = await self._set_absolute_volume_multiroom_pure(int(display_clamped))
                else:
                    # Mode direct : logique existante
                    target_alsa_volume = self._display_to_alsa(int(display_clamped))
                    success = await self._apply_alsa_volume_direct(target_alsa_volume)
                    
                    if success:
                        self._precise_display_volume = display_clamped
                
                if success:
                    self._save_last_volume(int(display_clamped))
                    await self._broadcast_volume_change(show_bar)
                
                self._is_adjusting = False
                return success
            except Exception as e:
                self.logger.error(f"Error setting display volume: {e}")
                self._is_adjusting = False
                return False
    
    # === ARCHITECTURE SIMPLE : MÉTHODES D'AJUSTEMENT ===
    
    async def adjust_display_volume(self, delta: int, show_bar: bool = True) -> bool:
        """SIMPLE : Méthode unifiée qui délègue selon le mode"""
        async with self._volume_lock:
            try:
                self.logger.info(f"🎵 ADJUST_DISPLAY_VOLUME: delta={delta}%, multiroom={self._is_multiroom_enabled()}")
                
                # Marquer ajustement
                self._is_adjusting = True
                
                # ARCHITECTURE SIMPLE : Déléguer selon le mode
                if self._is_multiroom_enabled():
                    success = await self._adjust_volume_multiroom_simple(delta)
                else:
                    success = await self._adjust_volume_direct_simple(delta)
                
                if success:
                    # UN SEUL appel à get_display_volume
                    final_display_volume = await self.get_display_volume()
                    self.logger.info(f"🎵 ADJUST_SUCCESS: {final_display_volume}%")
                    self._save_last_volume(final_display_volume)
                    await self._broadcast_volume_change(show_bar)
                else:
                    self.logger.error(f"🎵 ADJUST_FAILED")
                
                # Marquer fin d'ajustement
                asyncio.create_task(self._mark_adjustment_done())
                
                return success
                
            except Exception as e:
                self.logger.error(f"Error in adjust volume: {e}")
                self._is_adjusting = False
                return False
    
    async def _adjust_volume_direct_simple(self, delta: int) -> bool:
        """SIMPLE : Ajustement volume en mode direct (système)"""
        try:
            current_precise = self._precise_display_volume
            new_precise = self._clamp_display_volume(current_precise + delta)
            
            current_display = int(round(current_precise))
            new_display = int(round(new_precise))
            
            # Calculs ALSA
            new_alsa = self._display_to_alsa(new_display)
            
            self.logger.info(f"🔀 DIRECT_ADJUST: {current_display}% + {delta}% = {new_display}% (ALSA: {new_alsa})")
            
            success = await self._apply_alsa_volume_direct(new_alsa)
            
            if success:
                self._precise_display_volume = new_precise
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error in direct volume adjustment: {e}")
            return False
    
    async def _adjust_volume_multiroom_simple(self, delta: int) -> bool:
        """SIMPLE : Ajustement volume en mode multiroom (états display purs)"""
        try:
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                self.logger.warning(f"🔀 MULTIROOM_ADJUST: No clients found")
                return False
            
            # Assurer l'initialisation une seule fois
            await self._initialize_client_display_states()
            
            self.logger.info(f"🔀 MULTIROOM_ADJUST: Applying {delta}% to {len(clients)} clients")
            
            # SIMPLE : Traitement séquentiel pour éviter les conflits
            client_logs = []
            
            for client in clients:
                client_id = client["id"]
                client_name = client.get("name", "Unknown")
                current_alsa_volume = client.get("volume", 0)
                
                # 1. Récupérer l'état display PUR actuel (float)
                current_display_float = self._get_client_display_volume(client_id, current_alsa_volume)
                
                # 2. Appliquer le delta sur l'état display PUR (AUCUNE CONVERSION)
                new_display_float = self._clamp_display_volume(current_display_float + delta)
                
                # 3. Mettre à jour l'état display interne (FLOAT PRECISION)
                self._set_client_display_volume(client_id, new_display_float)
                
                # 4. Conversion vers ALSA SEULEMENT pour envoyer à Snapcast
                new_alsa_volume = self._display_to_alsa(int(round(new_display_float)))
                new_alsa_volume = self._clamp_alsa_volume(new_alsa_volume)
                
                # 5. Envoyer le volume ALSA à Snapcast
                await self.snapcast_service.set_volume(client_id, new_alsa_volume)
                
                # Log optimisé
                alsa_changed = "✅" if new_alsa_volume != current_alsa_volume else "⚪"
                client_log = f"'{client_name}': {current_display_float:.1f}% + {delta}% = {new_display_float:.1f}% (ALSA: {current_alsa_volume} → {new_alsa_volume}) {alsa_changed}"
                client_logs.append(client_log)
            
            # Invalider le cache clients
            self._snapcast_clients_cache = []
            self._snapcast_cache_time = 0
            
            # Log récapitulatif unique
            self.logger.info(f"🔀 MULTIROOM_SUMMARY: {len(clients)} clients updated:")
            for log in client_logs:
                self.logger.info(f"  {log}")
            
            return True
        except Exception as e:
            self.logger.error(f"Error in multiroom volume adjustment: {e}")
            return False
    
    async def _set_absolute_volume_multiroom_pure(self, target_display_volume: int) -> bool:
        """SIMPLE : Définit un volume absolu en mode multiroom avec états display PURS"""
        try:
            clients = await self._get_snapcast_clients_cached()
            if not clients:
                return False
            
            # Assurer l'initialisation une seule fois
            await self._initialize_client_display_states()
            
            # Convertir le volume display cible vers ALSA  
            target_alsa_volume = self._display_to_alsa(target_display_volume)
            
            self.logger.info(f"🔧 SET_ABS_MULTIROOM: {target_display_volume}% (ALSA: {target_alsa_volume}) for {len(clients)} clients")
            
            # SIMPLE : Traitement séquentiel
            for client in clients:
                client_id = client["id"]
                
                # Mettre à jour l'état display interne (FLOAT)
                self._set_client_display_volume(client_id, float(target_display_volume))
                
                # Appliquer le volume ALSA
                await self.snapcast_service.set_volume(client_id, target_alsa_volume)
            
            return True
        except Exception as e:
            self.logger.error(f"Error setting absolute multiroom volume: {e}")
            return False
    
    # === BROADCAST WEBSOCKET SIMPLE ===
    
    async def _broadcast_volume_change(self, show_bar: bool = True) -> None:
        """Broadcast SIMPLE et direct"""
        try:
            # UN SEUL appel à get_display_volume 
            current_volume = await self.get_display_volume()
            
            # UN SEUL broadcast WebSocket
            await self.state_machine.broadcast_event("volume", "volume_changed", {
                "volume": current_volume,
                "multiroom_mode": self._is_multiroom_enabled(),
                "show_bar": show_bar,
                "source": "volume_service",
                "mobile_steps": self._mobile_volume_steps
            })
            
        except Exception as e:
            self.logger.error(f"Error in volume broadcast: {e}")
    
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
            self.logger.debug(f"🔄 SYNC_CLIENT_EXTERNAL: Skipping sync for {client_id} - adjustment in progress")
            return
            
        self._sync_client_display_from_external(client_id, new_alsa_volume)
    
    # === MÉTHODES UTILITAIRES ===
    
    async def _mark_adjustment_done(self):
        """Marque la fin d'ajustement après un délai"""
        await asyncio.sleep(0.1)
        self._is_adjusting = False
    
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
        """Force tous les clients Snapcast au volume ALSA et initialise les états display PURS"""
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
                
                # Initialiser l'état display découplé (FLOAT)
                self._set_client_display_volume(client_id, float(display_volume))
            
            self._client_states_initialized = True
            self.logger.info(f"Startup: {len(clients)} clients set to {alsa_volume} ({display_volume}% display)")
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
                    "pure_display_states_count": len(self._client_display_states)
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
                "display_volume": True,
                "config": {
                    "alsa_min": self._alsa_min_volume,
                    "alsa_max": self._alsa_max_volume,
                    "startup_volume": self._default_startup_display_volume,
                    "restore_last_volume": self._restore_last_volume,
                    "mobile_steps": self._mobile_volume_steps
                }
            }