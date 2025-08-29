# backend/infrastructure/services/settings_service.py
"""
Service de gestion des settings unifiés pour Milo
"""
import json
import os
import logging
from typing import Dict, Any, Optional

class SettingsService:
    """Gestionnaire de settings unifié avec validation"""
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.settings_file = os.path.expanduser('~/milo_settings.json')
        self.legacy_language_file = os.path.expanduser('~/milo_language.json')
        self._cache = None
        
        # Settings par défaut
        self.default_settings = {
            "language": "french",
            "volume": {
                "alsa_min": 0,
                "alsa_max": 65,
                "startup_volume": 37,
                "restore_last_volume": False,
                "mobile_volume_steps": 5
            },
            "display": {
                "screen_timeout_seconds": 10
            },
            "spotify": {
                "auto_disconnect_delay": 10.0
            },
            "dock": {
                "enabled_apps": ["spotify", "bluetooth", "roc", "multiroom", "equalizer"]
            }
        }
    
    def _migrate_legacy_language(self) -> None:
        """Migre l'ancienne configuration de langue vers le nouveau système"""
        try:
            if os.path.exists(self.legacy_language_file):
                self.logger.info("Migrating legacy language configuration")
                
                with open(self.legacy_language_file, 'r', encoding='utf-8') as f:
                    legacy_config = json.load(f)
                
                # Charger ou créer les nouvelles settings
                settings = self.load_settings()
                settings["language"] = legacy_config.get("language", "french")
                
                # Sauvegarder
                self.save_settings(settings)
                
                # Supprimer l'ancien fichier
                os.remove(self.legacy_language_file)
                self.logger.info(f"Legacy language file migrated and removed: {legacy_config}")
                
        except Exception as e:
            self.logger.error(f"Error migrating legacy language: {e}")
    
    def load_settings(self) -> Dict[str, Any]:
        """Charge les settings avec migration automatique"""
        try:
            # Migration legacy si nécessaire
            self._migrate_legacy_language()
            
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                
                # Fusionner avec defaults pour nouvelles clés
                merged_settings = self._merge_with_defaults(settings)
                
                # Valider et corriger
                validated_settings = self._validate_settings(merged_settings)
                
                # Sauvegarder si des corrections ont été apportées
                if validated_settings != settings:
                    self.save_settings(validated_settings)
                
                self._cache = validated_settings
                return validated_settings
            else:
                self.logger.info("No settings file found, creating with defaults")
                self.save_settings(self.default_settings)
                self._cache = self.default_settings.copy()
                return self._cache
                
        except Exception as e:
            self.logger.error(f"Error loading settings: {e}")
            self._cache = self.default_settings.copy()
            return self._cache
    
    def save_settings(self, settings: Dict[str, Any]) -> bool:
        """Sauvegarde les settings avec validation"""
        try:
            validated_settings = self._validate_settings(settings)
            
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(validated_settings, f, ensure_ascii=False, indent=2)
            
            self._cache = validated_settings
            self.logger.info(f"Settings saved successfully to {self.settings_file}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving settings: {e}")
            return False
    
    def get_setting(self, key_path: str) -> Any:
        """Récupère une setting spécifique par chemin (ex: 'volume.alsa_min')"""
        if not self._cache:
            self._cache = self.load_settings()
        
        try:
            keys = key_path.split('.')
            value = self._cache
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            self.logger.warning(f"Setting key not found: {key_path}")
            return None
    
    def set_setting(self, key_path: str, value: Any) -> bool:
        """Définit une setting spécifique et sauvegarde"""
        try:
            settings = self.load_settings()
            
            # Navigation dans l'arborescence
            keys = key_path.split('.')
            current = settings
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]
            
            # Définir la valeur
            current[keys[-1]] = value
            
            return self.save_settings(settings)
            
        except Exception as e:
            self.logger.error(f"Error setting {key_path}: {e}")
            return False
    
    def _merge_with_defaults(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Fusionne les settings avec les valeurs par défaut"""
        def deep_merge(default, user):
            result = default.copy()
            for key, value in user.items():
                if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                    result[key] = deep_merge(result[key], value)
                else:
                    result[key] = value
            return result
        
        return deep_merge(self.default_settings, settings)
    
    def _validate_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Valide et corrige les settings"""
        validated = settings.copy()
        
        # Validation volume
        if 'volume' in validated:
            vol = validated['volume']
            
            # Limites ALSA
            vol['alsa_min'] = max(0, min(100, int(vol.get('alsa_min', 0))))
            vol['alsa_max'] = max(0, min(100, int(vol.get('alsa_max', 65))))
            
            # Garantir gap minimum de 10
            if vol['alsa_max'] - vol['alsa_min'] < 10:
                self.logger.warning(f"Volume range too small, adjusting: min={vol['alsa_min']}, max={vol['alsa_max']}")
                vol['alsa_max'] = vol['alsa_min'] + 10
                if vol['alsa_max'] > 100:
                    vol['alsa_max'] = 100
                    vol['alsa_min'] = 90
            
            # Startup volume dans la plage
            startup_vol = int(vol.get('startup_volume', 37))
            vol['startup_volume'] = max(vol['alsa_min'], min(vol['alsa_max'], startup_vol))
            
            # Steps volume mobile
            vol['mobile_volume_steps'] = max(1, min(20, int(vol.get('mobile_volume_steps', 5))))
            
            # Boolean restore_last_volume
            vol['restore_last_volume'] = bool(vol.get('restore_last_volume', False))
        
        # Validation display
        if 'display' in validated:
            disp = validated['display']
            disp['screen_timeout_seconds'] = max(5, min(300, int(disp.get('screen_timeout_seconds', 10))))
        
        # Validation spotify
        if 'spotify' in validated:
            spotify = validated['spotify']
            spotify['auto_disconnect_delay'] = max(5.0, min(300.0, float(spotify.get('auto_disconnect_delay', 10.0))))
        
        # Validation langue
        valid_languages = ['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese']
        if validated.get('language') not in valid_languages:
            validated['language'] = 'french'
        
        return validated
    
    def get_volume_config(self) -> Dict[str, Any]:
        """Récupère la configuration volume complète"""
        settings = self.load_settings()
        return settings.get('volume', self.default_settings['volume'])
    
    def set_volume_limits(self, alsa_min: int, alsa_max: int) -> bool:
        """Définit les limites de volume avec validation"""
        try:
            # Validation
            alsa_min = max(0, min(100, int(alsa_min)))
            alsa_max = max(0, min(100, int(alsa_max)))
            
            if alsa_max - alsa_min < 10:
                self.logger.error(f"Volume range too small: {alsa_min}-{alsa_max}")
                return False
            
            # Sauvegarder
            success = (
                self.set_setting('volume.alsa_min', alsa_min) and
                self.set_setting('volume.alsa_max', alsa_max)
            )
            
            if success:
                self.logger.info(f"Volume limits updated: {alsa_min}-{alsa_max}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error setting volume limits: {e}")
            return False