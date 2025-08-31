# backend/infrastructure/services/settings_service.py
"""
Service de gestion des settings - Version OPTIM simplifiée
"""
import json
import os
import logging
from typing import Dict, Any

class SettingsService:
    """Gestionnaire de settings simplifié"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.settings_file = os.path.expanduser('~/milo_settings.json')
        self._cache = None
        
        self.defaults = {
            "language": "french",
            "volume": {
                "limits_enabled": True,
                "alsa_min": 0,
                "alsa_max": 65,
                "restore_last_volume": False,
                "startup_volume": 37,
                "mobile_volume_steps": 5
            },
            "screen": {
                "timeout_seconds": 10,
                "brightness_on": 5
            },
            "spotify": {
                "auto_disconnect_delay": 10.0
            },
            "dock": {
                "enabled_apps": ["spotify", "bluetooth", "roc", "multiroom", "equalizer"]
            }
        }
    
    def load_settings(self) -> Dict[str, Any]:
        """Charge et valide les settings"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                
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
        """Sauvegarde simple"""
        try:
            validated = self._validate_and_merge(settings)
            
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(validated, f, ensure_ascii=False, indent=2)
            
            self._cache = validated
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving settings: {e}")
            return False
    
    def _validate_and_merge(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Validation et fusion avec defaults en une seule passe"""
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
        vol['mobile_volume_steps'] = max(1, min(20, int(vol_input.get('mobile_volume_steps', 5))))
        validated['volume'] = vol
        
        # Screen (sans brightness_off)
        screen_input = settings.get('screen', {})
        validated['screen'] = {
            'timeout_seconds': max(3, min(3600, int(screen_input.get('timeout_seconds', 10)))),
            'brightness_on': max(1, min(10, int(screen_input.get('brightness_on', 5))))
        }
        
        # Spotify
        spotify_input = settings.get('spotify', {})
        validated['spotify'] = {
            'auto_disconnect_delay': max(1.0, min(300.0, float(spotify_input.get('auto_disconnect_delay', 10.0))))
        }
        
        # Dock
        dock_input = settings.get('dock', {})
        valid_apps = ["spotify", "bluetooth", "roc", "multiroom", "equalizer"]
        enabled_apps = dock_input.get('enabled_apps', [])
        filtered_apps = [app for app in enabled_apps if app in valid_apps]
        validated['dock'] = {
            'enabled_apps': filtered_apps if filtered_apps else self.defaults['dock']['enabled_apps'].copy()
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
        """Définit une setting"""
        try:
            settings = self.load_settings()
            
            keys = key_path.split('.')
            current = settings
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]
            
            current[keys[-1]] = value
            return self.save_settings(settings)
            
        except Exception as e:
            self.logger.error(f"Error setting {key_path}: {e}")
            return False