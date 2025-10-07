# backend/infrastructure/services/settings_service.py
"""
Service de gestion des settings - Version OPTIM avec support valeur 0 pour désactivation
"""
import json
import os
import fcntl
import logging
from typing import Dict, Any

class SettingsService:
    """Gestionnaire de settings simplifié avec support 0 = désactivé"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.settings_file = os.path.expanduser('~/milo/milo_settings.json')
        self._cache = None
        
        self.defaults = {
            "language": "french",
            "volume": {
                "limits_enabled": True,
                "alsa_min": 0,
                "alsa_max": 65,
                "restore_last_volume": False,
                "startup_volume": 24,
                "mobile_volume_steps": 5,
                "rotary_volume_steps": 2
            },
            "screen": {
                "timeout_enabled": True,
                "timeout_seconds": 10,
                "brightness_on": 5
            },
            "spotify": {
                "auto_disconnect_delay": 10.0
            },
            "routing": {
                "multiroom_enabled": False,
                "equalizer_enabled": False
            },
            "dock": {
                "enabled_apps": ["librespot", "bluetooth", "roc", "multiroom", "equalizer", "settings"]
            }
        }
    
    def load_settings(self) -> Dict[str, Any]:
        """Charge et valide les settings avec file locking"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    # Verrouiller en lecture pour éviter lecture partielle pendant écriture
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    try:
                        settings = json.load(f)
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                
                # Migration display → screen
                if 'display' in settings:
                    display_config = settings.pop('display')
                    if 'screen' not in settings:
                        settings['screen'] = {
                            'timeout_seconds': display_config.get('screen_timeout_seconds', 10),
                            'brightness_on': display_config.get('brightness_on', 5)
                        }
                
                # Fusion avec defaults et validation
                validated = self._validate_and_merge(settings)
                
                # Sauver si changements
                if validated != settings:
                    self.save_settings(validated)
                
                self._cache = validated
                return validated
            else:
                self.save_settings(self.defaults)
                self._cache = self.defaults.copy()
                return self._cache
                
        except Exception as e:
            self.logger.error(f"Error loading settings: {e}")
            self._cache = self.defaults.copy()
            return self._cache
    
    def save_settings(self, settings: Dict[str, Any]) -> bool:
        """Sauvegarde avec file locking pour éviter corruption"""
        try:
            validated = self._validate_and_merge(settings)

            # Écriture atomique avec file locking
            temp_file = self.settings_file + '.tmp'
            with open(temp_file, 'w', encoding='utf-8') as f:
                # Verrouiller le fichier pendant l'écriture
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(validated, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            # Renommage atomique
            os.replace(temp_file, self.settings_file)

            self._cache = validated
            return True

        except Exception as e:
            self.logger.error(f"Error saving settings: {e}")
            # Nettoyer le fichier temporaire si échec
            try:
                if os.path.exists(self.settings_file + '.tmp'):
                    os.remove(self.settings_file + '.tmp')
            except:
                pass
            return False
    
    def _validate_and_merge(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Validation et fusion avec defaults - Support 0 = désactivé"""
        validated = {}
        
        # Language
        valid_languages = ['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese']
        validated['language'] = settings.get('language') if settings.get('language') in valid_languages else 'french'
        
        # Volume
        vol_input = settings.get('volume', {})
        vol = {}
        vol['limits_enabled'] = bool(vol_input.get('limits_enabled', True))
        vol['alsa_min'] = max(0, min(100, int(vol_input.get('alsa_min', 0))))
        vol['alsa_max'] = max(0, min(100, int(vol_input.get('alsa_max', 65))))
        
        # Garantir gap minimum
        if vol['alsa_max'] - vol['alsa_min'] < 10:
            vol['alsa_max'] = vol['alsa_min'] + 10
            if vol['alsa_max'] > 100:
                vol['alsa_max'] = 100
                vol['alsa_min'] = 90
        
        vol['restore_last_volume'] = bool(vol_input.get('restore_last_volume', False))
        vol['startup_volume'] = max(vol['alsa_min'], min(vol['alsa_max'], int(vol_input.get('startup_volume', 37))))
        vol['mobile_volume_steps'] = max(1, min(10, int(vol_input.get('mobile_volume_steps', 5))))
        vol['rotary_volume_steps'] = max(1, min(10, int(vol_input.get('rotary_volume_steps', 2))))
        validated['volume'] = vol
        
        # Screen - MODIFIÉ : Accepter 0 pour timeout_seconds (désactivé)
        screen_input = settings.get('screen', {})
        timeout_seconds_raw = int(screen_input.get('timeout_seconds', 10))
        
        validated['screen'] = {
            # 0 = désactivé, sinon minimum 3 secondes
            'timeout_seconds': 0 if timeout_seconds_raw == 0 else max(3, min(9999, timeout_seconds_raw)),
            'brightness_on': max(1, min(10, int(screen_input.get('brightness_on', 5))))
        }
        
        # Spotify - MODIFIÉ : Accepter 0 pour auto_disconnect_delay (désactivé)
        spotify_input = settings.get('spotify', {})
        disconnect_delay_raw = float(spotify_input.get('auto_disconnect_delay', 10.0))
        
        validated['spotify'] = {
            # 0 = désactivé, sinon minimum 1.0 seconde, maximum 1h (3600s)
            'auto_disconnect_delay': 0.0 if disconnect_delay_raw == 0.0 else max(1.0, min(9999.0, disconnect_delay_raw))
        }
        
        # Dock avec validation au moins une source audio
        dock_input = settings.get('dock', {})
        all_valid_apps = ["librespot", "bluetooth", "roc", "multiroom", "equalizer", "settings"]
        audio_sources = ["librespot", "bluetooth", "roc"]
        other_apps = ["multiroom", "equalizer", "settings"]
        
        enabled_apps = dock_input.get('enabled_apps', [])
        filtered_apps = [app for app in enabled_apps if app in all_valid_apps]
        
        # Vérifier qu'au moins une source audio est activée
        enabled_audio_sources = [app for app in filtered_apps if app in audio_sources]
        if not enabled_audio_sources:
            # Forcer au moins librespot si aucune source audio
            filtered_apps = ['librespot'] + [app for app in filtered_apps if app in other_apps]
        
        validated['dock'] = {
            'enabled_apps': filtered_apps if filtered_apps else self.defaults['dock']['enabled_apps'].copy()
        }

        # Routing (multiroom + equalizer)
        routing_input = settings.get('routing', {})
        validated['routing'] = {
            'multiroom_enabled': bool(routing_input.get('multiroom_enabled', False)),
            'equalizer_enabled': bool(routing_input.get('equalizer_enabled', False))
        }

        return validated
    
    def get_setting(self, key_path: str) -> Any:
        """Récupère une setting par chemin"""
        if not self._cache:
            self._cache = self.load_settings()
        
        try:
            keys = key_path.split('.')
            value = self._cache
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return None
    
    def set_setting(self, key_path: str, value: Any) -> bool:
        """Définit une setting et invalide le cache"""
        try:
            settings = self.load_settings()
            
            keys = key_path.split('.')
            current = settings
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]
            
            current[keys[-1]] = value
            success = self.save_settings(settings)
            
            # Invalider le cache pour forcer un reload
            if success:
                self._cache = None
                
            return success
            
        except Exception as e:
            self.logger.error(f"Error setting {key_path}: {e}")
            return False
    
    def get_volume_config(self) -> Dict[str, Any]:
        """Méthode helper pour récupérer la config volume"""
        volume_settings = self.get_setting('volume') or {}
        return {
            "alsa_min": volume_settings.get("alsa_min", 0),
            "alsa_max": volume_settings.get("alsa_max", 65),
            "startup_volume": volume_settings.get("startup_volume", 37),
            "restore_last_volume": volume_settings.get("restore_last_volume", False),
            "mobile_volume_steps": volume_settings.get("mobile_volume_steps", 5),
            "rotary_volume_steps": volume_settings.get("rotary_volume_steps", 2)
        }