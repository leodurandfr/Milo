# backend/infrastructure/services/settings_service.py
"""
Service de gestion des settings - Version OPTIM avec I/O async
"""
import json
import os
import logging
import aiofiles
import asyncio
from typing import Dict, Any

class SettingsService:
    """Simplified settings manager with support for 0 = disabled"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.settings_file = '/var/lib/milo/settings.json'
        self._cache = None
        self._file_lock = asyncio.Lock()  # Lock async natif au lieu de fcntl.flock
        
        self.defaults = {
            "language": "english",
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
            "podcast": {
                "taddy_user_id": "",
                "taddy_api_key": ""
            },
            "routing": {
                "multiroom_enabled": False,
                "equalizer_enabled": False
            },
            "dock": {
                "enabled_apps": ["spotify", "bluetooth", "mac", "radio", "podcast", "multiroom", "equalizer", "settings"]
            }
        }
    
    async def load_settings(self) -> Dict[str, Any]:
        """Charge et valide les settings avec async lock"""
        try:
            if os.path.exists(self.settings_file):
                async with self._file_lock:
                    async with aiofiles.open(self.settings_file, 'r', encoding='utf-8') as f:
                        content = await f.read()

                    settings = json.loads(content)

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

                    self._cache = validated
                    return validated
            else:
                # Créer le fichier avec les defaults
                self._cache = self.defaults.copy()
                await self.save_settings(self.defaults)
                return self._cache

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON decode error in settings file: {e}")
            # Save le fichier corrompu
            if os.path.exists(self.settings_file):
                backup_corrupted = self.settings_file + '.corrupted'
                async with aiofiles.open(self.settings_file, 'r', encoding='utf-8') as src:
                    content = await src.read()
                async with aiofiles.open(backup_corrupted, 'w', encoding='utf-8') as dst:
                    await dst.write(content)
                self.logger.warning(f"Corrupted JSON saved to: {backup_corrupted}")
            self._cache = self.defaults.copy()
            await self.save_settings(self.defaults)
            return self._cache
        except Exception as e:
            self.logger.error(f"Error loading settings: {e}")
            self._cache = self.defaults.copy()
            return self._cache
    
    async def save_settings(self, settings: Dict[str, Any]) -> bool:
        """Sauvegarde avec async lock et écriture atomique"""
        try:
            validated = self._validate_and_merge(settings)

            async with self._file_lock:
                # Atomic write via fichier temporaire
                temp_file = self.settings_file + '.tmp'

                # Générer le JSON
                json_content = json.dumps(validated, ensure_ascii=False, indent=2)

                # Écrire le contenu
                async with aiofiles.open(temp_file, 'w', encoding='utf-8') as f:
                    await f.write(json_content)
                    await f.write('\n')
                    await f.flush()
                    os.fsync(f.fileno())

                # Renommage atomique
                os.replace(temp_file, self.settings_file)

            self._cache = validated
            self.logger.debug("Settings saved successfully")
            return True

        except Exception as e:
            self.logger.error(f"Error saving settings: {e}")
            # Nettoyer le fichier temporaire si échec
            try:
                if os.path.exists(self.settings_file + '.tmp'):
                    os.remove(self.settings_file + '.tmp')
            except Exception as cleanup_error:
                self.logger.warning(f"Failed to clean up temp file: {cleanup_error}")
            return False
    
    def _validate_and_merge(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Validation et fusion avec defaults - Support 0 = désactivé"""
        validated = {}
        
        # Language
        valid_languages = ['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese', 'italian', 'german']
        validated['language'] = settings.get('language') if settings.get('language') in valid_languages else 'english'
        
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

        # Podcast credentials
        podcast_input = settings.get('podcast', {})
        validated['podcast'] = {
            'taddy_user_id': str(podcast_input.get('taddy_user_id', '')),
            'taddy_api_key': str(podcast_input.get('taddy_api_key', ''))
        }
        # Preserve credentials_validated_at if present
        if 'credentials_validated_at' in podcast_input:
            validated['podcast']['credentials_validated_at'] = int(podcast_input['credentials_validated_at'])

        # Dock avec validation au moins une source audio
        dock_input = settings.get('dock', {})
        all_valid_apps = ["spotify", "bluetooth", "mac", "radio", "podcast", "multiroom", "equalizer", "settings"]
        audio_sources = ["spotify", "bluetooth", "mac", "radio", "podcast"]
        other_apps = ["multiroom", "equalizer", "settings"]

        enabled_apps = dock_input.get('enabled_apps', [])
        filtered_apps = [app for app in enabled_apps if app in all_valid_apps]

        # Vérifier qu'au moins une source audio est activée
        enabled_audio_sources = [app for app in filtered_apps if app in audio_sources]
        if not enabled_audio_sources:
            # Forcer au moins spotify si aucune source audio
            filtered_apps = ['spotify'] + [app for app in filtered_apps if app in other_apps]
        
        validated['dock'] = {
            'enabled_apps': filtered_apps if filtered_apps else self.defaults['dock']['enabled_apps'].copy()
        }

        # Routing (multiroom + equalizer)
        routing_input = settings.get('routing', {})
        validated['routing'] = {
            'multiroom_enabled': bool(routing_input.get('multiroom_enabled', False)),
            'equalizer_enabled': bool(routing_input.get('equalizer_enabled', False))
        }

        # Equalizer (saved_bands) - Préserver la section equalizer sans validation stricte
        equalizer_input = settings.get('equalizer', {})
        if equalizer_input:
            # Préserver la section equalizer telle quelle (pas de validation stricte)
            validated['equalizer'] = equalizer_input

        # Radio (favorites + broken_stations) - Préserver la section radio sans validation stricte
        radio_input = settings.get('radio', {})
        if radio_input:
            # Préserver la section radio telle quelle (pas de validation stricte)
            validated['radio'] = radio_input

        return validated
    
    def get_setting_sync(self, key_path: str) -> Any:
        """Gets a setting by path (SYNCHRONOUS - uses cache or blocking load)"""
        if not self._cache:
            # Charger de manière synchrone si nécessaire (bloquant mais rare)
            try:
                if os.path.exists(self.settings_file):
                    with open(self.settings_file, 'r', encoding='utf-8') as f:
                        self._cache = json.load(f)
                else:
                    self._cache = self.defaults.copy()
            except Exception:
                self._cache = self.defaults.copy()

        try:
            keys = key_path.split('.')
            value = self._cache
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return None

    async def get_setting(self, key_path: str) -> Any:
        """Gets a setting by path (async)"""
        if not self._cache:
            self._cache = await self.load_settings()

        try:
            keys = key_path.split('.')
            value = self._cache
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return None

    def invalidate_cache(self) -> None:
        """Invalide le cache pour forcer un rechargement"""
        self._cache = None

    async def set_setting(self, key_path: str, value: Any) -> bool:
        """Définit une setting et invalide le cache (async)"""
        try:
            settings = await self.load_settings()

            keys = key_path.split('.')
            current = settings
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]

            current[keys[-1]] = value

            success = await self.save_settings(settings)

            # Invalider le cache pour forcer un reload
            if success:
                self._cache = None

            return success

        except Exception as e:
            self.logger.error(f"Error setting {key_path}: {e}")
            return False
    
    def get_volume_config(self) -> Dict[str, Any]:
        """Méthode helper synchrone (utilise cache uniquement)"""
        volume_settings = self._cache.get('volume', {}) if self._cache else {}
        return {
            "alsa_min": volume_settings.get("alsa_min", 0),
            "alsa_max": volume_settings.get("alsa_max", 65),
            "startup_volume": volume_settings.get("startup_volume", 37),
            "restore_last_volume": volume_settings.get("restore_last_volume", False),
            "mobile_volume_steps": volume_settings.get("mobile_volume_steps", 5),
            "rotary_volume_steps": volume_settings.get("rotary_volume_steps", 2)
        }

    async def get_volume_config_async(self) -> Dict[str, Any]:
        """Méthode helper async pour récupérer la config volume"""
        volume_settings = await self.get_setting('volume') or {}
        return {
            "alsa_min": volume_settings.get("alsa_min", 0),
            "alsa_max": volume_settings.get("alsa_max", 65),
            "startup_volume": volume_settings.get("startup_volume", 37),
            "restore_last_volume": volume_settings.get("restore_last_volume", False),
            "mobile_volume_steps": volume_settings.get("mobile_volume_steps", 5),
            "rotary_volume_steps": volume_settings.get("rotary_volume_steps", 2)
        }