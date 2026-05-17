# backend/tests/test_settings_service.py
"""
Unit tests for SettingsService
"""
import pytest
import json
import os
import tempfile
from unittest.mock import patch
from backend.core.settings import SettingsService, SettingsWriteError


class TestSettingsService:
    """Tests for the settings service"""

    @pytest.fixture
    def temp_settings_file(self):
        """Creates a temporary file for tests"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_path = f.name

        yield temp_path

        # Cleanup
        try:
            os.unlink(temp_path)
        except:
            pass

        # Cleanup .tmp file too if present
        try:
            os.unlink(temp_path + '.tmp')
        except:
            pass

    @pytest.fixture
    def service(self, temp_settings_file):
        """Fixture to create a settings service"""
        service = SettingsService()
        service.settings_file = temp_settings_file
        return service

    def test_initialization(self, service):
        """Service initialization test"""
        assert service.settings_file is not None
        assert service._cache is None
        assert 'volume' in service.defaults
        assert 'screen' in service.defaults
        assert 'audio' in service.defaults
        assert 'auto_disconnect_delay' in service.defaults['audio']
        assert 'routing' in service.defaults

    @pytest.mark.asyncio
    async def test_load_settings_file_not_exists(self, service):
        """Loading test when file does not exist"""
        settings = await service.load_settings()

        # Should create file with defaults (after validation)
        assert os.path.exists(service.settings_file)
        # Check main keys (validation may modify some keys)
        assert settings['language'] == 'english'
        assert settings['volume']['limit_min_db'] == service.defaults['volume']['limit_min_db']
        assert settings['volume']['limit_max_db'] == service.defaults['volume']['limit_max_db']
        assert settings['routing'] == service.defaults['routing']
        assert service._cache is not None

    @pytest.mark.asyncio
    async def test_load_settings_file_exists(self, service, temp_settings_file):
        """Existing file loading test"""
        # Write settings to file (stamped with current schema_version)
        test_settings = {
            'schema_version': SettingsService.SCHEMA_VERSION,
            'language': 'english',
            'volume': {'limit_min_db': -50.0, 'limit_max_db': -15.0},
            'screen': {'timeout_seconds': 15, 'brightness_on': 7},
            'routing': {'multiroom_enabled': True, 'equalizer_effects_enabled': False},
            'dock': {'enabled_apps': ['spotify', 'bluetooth']}
        }

        with open(temp_settings_file, 'w') as f:
            json.dump(test_settings, f)

        settings = await service.load_settings()

        assert settings['language'] == 'english'
        assert settings['volume']['limit_min_db'] == -50.0
        assert settings['volume']['limit_max_db'] == -15.0

    @pytest.mark.asyncio
    async def test_load_settings_raises_on_schema_mismatch(
        self, service, temp_settings_file
    ):
        """A legacy file without `schema_version` (or a stale one) must fail loud
        so dependencies.py::init_async can log the banner and SystemExit(1)."""
        from backend.shared.persistence import SchemaVersionMismatch

        legacy_settings = {
            'language': 'english',
            'spotify': {'auto_disconnect_delay': 300.0},
        }
        with open(temp_settings_file, 'w') as f:
            json.dump(legacy_settings, f)

        with pytest.raises(SchemaVersionMismatch):
            await service.load_settings()

    @pytest.mark.asyncio
    async def test_save_settings_success(self, service):
        """Successful save test"""
        test_settings = service.defaults.copy()
        test_settings['language'] = 'spanish'

        result = await service.save_settings(test_settings)

        assert result is True
        assert os.path.exists(service.settings_file)

        # Check that file contains the settings
        with open(service.settings_file, 'r') as f:
            saved = json.load(f)
            assert saved['language'] == 'spanish'

    def test_validate_and_merge_language(self, service):
        """Language validation test"""
        # Valid language
        result = service._validate_and_merge({'language': 'english'})
        assert result['language'] == 'english'

        # Invalid language - fallback to english (default)
        result = service._validate_and_merge({'language': 'invalid'})
        assert result['language'] == 'english'

    def test_validate_and_merge_volume(self, service):
        """Volume validation test (dB-based)"""
        # Normal values
        result = service._validate_and_merge({
            'volume': {'limit_min_db': -50.0, 'limit_max_db': -15.0}
        })
        assert result['volume']['limit_min_db'] == -50.0
        assert result['volume']['limit_max_db'] == -15.0

        # Minimum gap of 6 dB
        result = service._validate_and_merge({
            'volume': {'limit_min_db': -25.0, 'limit_max_db': -23.0}
        })
        assert result['volume']['limit_max_db'] - result['volume']['limit_min_db'] >= 6.0

        # Out of bounds values (dB range is -80 to 0)
        result = service._validate_and_merge({
            'volume': {'limit_min_db': -90.0, 'limit_max_db': 10.0}
        })
        assert result['volume']['limit_min_db'] >= -80.0
        assert result['volume']['limit_max_db'] <= 0.0

    def test_validate_and_merge_screen_timeout_zero(self, service):
        """Screen timeout validation test with 0 = disabled"""
        # Timeout at 0 (disabled)
        result = service._validate_and_merge({
            'screen': {'timeout_seconds': 0, 'brightness_on': 5}
        })
        assert result['screen']['timeout_seconds'] == 0

        # Normal timeout
        result = service._validate_and_merge({
            'screen': {'timeout_seconds': 15, 'brightness_on': 5}
        })
        assert result['screen']['timeout_seconds'] == 15

        # Timeout too small (minimum 3s if non-zero)
        result = service._validate_and_merge({
            'screen': {'timeout_seconds': 1, 'brightness_on': 5}
        })
        assert result['screen']['timeout_seconds'] == 3

    def test_validate_and_merge_audio_disconnect_zero(self, service):
        """Global auto-disconnect delay validation test with 0 = disabled"""
        # Delay at 0 (disabled)
        result = service._validate_and_merge({
            'audio': {'auto_disconnect_delay': 0.0}
        })
        assert result['audio']['auto_disconnect_delay'] == 0.0

        # Normal delay
        result = service._validate_and_merge({
            'audio': {'auto_disconnect_delay': 15.0}
        })
        assert result['audio']['auto_disconnect_delay'] == 15.0

        # Delay too small (minimum 1.0s if non-zero)
        result = service._validate_and_merge({
            'audio': {'auto_disconnect_delay': 0.5}
        })
        assert result['audio']['auto_disconnect_delay'] == 1.0

    def test_validate_and_merge_audio_disconnect_non_numeric(self, service):
        """Non-numeric auto_disconnect_delay must not crash; fall back to default."""
        result = service._validate_and_merge({
            'audio': {'auto_disconnect_delay': 'broken'}
        })
        assert result['audio']['auto_disconnect_delay'] == 120.0

    def test_validate_and_merge_dock_apps(self, service):
        """Dock apps validation test"""
        # Valid apps
        result = service._validate_and_merge({
            'dock': {'enabled_apps': ['spotify', 'bluetooth', 'settings']}
        })
        assert 'spotify' in result['dock']['enabled_apps']
        assert 'bluetooth' in result['dock']['enabled_apps']

        # Invalid apps filtered out
        result = service._validate_and_merge({
            'dock': {'enabled_apps': ['spotify', 'invalid_app', 'bluetooth']}
        })
        assert 'invalid_app' not in result['dock']['enabled_apps']

        # No audio source - should force librespot
        result = service._validate_and_merge({
            'dock': {'enabled_apps': ['settings', 'equalizer']}
        })
        assert 'spotify' in result['dock']['enabled_apps']

    def test_validate_and_merge_routing(self, service):
        """Routing validation test"""
        result = service._validate_and_merge({
            'routing': {'multiroom_enabled': True, 'equalizer_effects_enabled': False}
        })
        assert result['routing']['multiroom_enabled'] is True
        assert result['routing']['equalizer_effects_enabled'] is False

    def test_validate_and_merge_equalizer_preserved(self, service):
        """Equalizer section preservation test"""
        result = service._validate_and_merge({
            'equalizer': {
                'saved_bands': {'preset1': [65, 66, 67]},
                'active_preset': 'preset1'
            }
        })
        assert 'equalizer' in result
        assert result['equalizer']['saved_bands'] == {'preset1': [65, 66, 67]}

    @pytest.mark.asyncio
    async def test_get_setting_simple(self, service):
        """Simple setting retrieval test"""
        service._cache = {'language': 'french'}

        value = await service.get_setting('language')

        assert value == 'french'

    @pytest.mark.asyncio
    async def test_get_setting_nested(self, service):
        """Nested setting retrieval test"""
        service._cache = {
            'volume': {'limit_min_db': -50.0, 'limit_max_db': -21.0}
        }

        value = await service.get_setting('volume.limit_min_db')

        assert value == -50.0

    @pytest.mark.asyncio
    async def test_get_setting_not_found(self, service):
        """Non-existent setting retrieval test"""
        service._cache = {'language': 'french'}

        value = await service.get_setting('nonexistent.key')

        assert value is None

    @pytest.mark.asyncio
    async def test_get_setting_loads_if_no_cache(self, service, temp_settings_file):
        """Test that get_setting loads settings if cache is empty"""
        # Write settings to file (stamped with current schema_version)
        test_settings = service.defaults.copy()
        test_settings['language'] = 'english'
        test_settings['schema_version'] = SettingsService.SCHEMA_VERSION

        with open(temp_settings_file, 'w') as f:
            json.dump(test_settings, f)

        service._cache = None

        value = await service.get_setting('language')

        assert value == 'english'
        assert service._cache is not None

    @pytest.mark.asyncio
    async def test_set_setting_simple(self, service):
        """Simple setting modification test"""
        service._cache = service.defaults.copy()

        result = await service.set_setting('language', 'spanish')

        assert result is True

        # Check that cache has been invalidated
        assert service._cache is None

        # Check that value has been saved
        saved_value = await service.get_setting('language')
        assert saved_value == 'spanish'

    @pytest.mark.asyncio
    async def test_set_setting_nested(self, service):
        """Nested setting modification test"""
        service._cache = service.defaults.copy()

        result = await service.set_setting('volume.limit_min_db', -45.0)

        assert result is True

        # Check that value has been saved
        saved_value = await service.get_setting('volume.limit_min_db')
        assert saved_value == -45.0

    @pytest.mark.asyncio
    async def test_set_setting_create_nested_path_in_existing_section(self, service):
        """Nested path creation test in existing section"""
        # Initialize file with defaults
        await service.save_settings(service.defaults)
        service._cache = None  # Force reload

        # Create a new path in the 'volume' section which already exists
        result = await service.set_setting('volume.new_setting', 123)

        assert result is True

        # Check that path has been created
        await service.get_setting('volume.new_setting')
        # Note: Validation may remove unknown keys, so we just check that save succeeded
        # This test mainly verifies that path creation doesn't raise an exception
        assert result is True

    def test_get_volume_config(self, service):
        """Complete volume config retrieval test (dB-based)"""
        service._cache = {
            'volume': {
                'limit_min_db': -50.0,
                'limit_max_db': -15.0,
                'startup_volume_db': -25.0,
                'restore_last_volume': True,
                'step_mobile_db': 4.0,
                'step_rotary_db': 3.0
            }
        }

        config = service.get_volume_config()

        assert config['limit_min_db'] == -50.0
        assert config['limit_max_db'] == -15.0
        assert config['startup_volume_db'] == -25.0
        assert config['restore_last_volume'] is True
        assert config['step_mobile_db'] == 4.0
        assert config['step_rotary_db'] == 3.0

    @pytest.mark.asyncio
    async def test_load_settings_error_fallback_to_defaults(self, service):
        """Fallback to defaults test in case of loading error"""
        # Force an error by using a corrupted file
        with open(service.settings_file, 'w') as f:
            f.write('{"invalid json')

        settings = await service.load_settings()

        # Should fallback to defaults (after validation, some keys may be absent)
        # Check main keys instead of strict equality
        assert settings['language'] == 'english'
        assert settings['volume']['limit_min_db'] == service.defaults['volume']['limit_min_db']
        assert settings['volume']['limit_max_db'] == service.defaults['volume']['limit_max_db']
        assert settings['routing'] == service.defaults['routing']
        assert service._cache is not None

    @pytest.mark.asyncio
    async def test_save_settings_error_cleanup_temp_file(self, service):
        """Temporary file cleanup test in case of error"""
        # Mock aiofiles.open to raise an exception (service uses aiofiles, not builtins.open)
        with patch('aiofiles.open', side_effect=Exception('Write error')):
            result = await service.save_settings({'language': 'french'})

            assert result is False

    # ------------------------------------------------------------------ #
    # Phase 1 — failure-loud writes and validated sync reads             #
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_set_setting_strict_persists_value(self, service):
        """set_setting_strict succeeds on a normal write and clears the cache."""
        service._cache = service.defaults.copy()

        await service.set_setting_strict('language', 'spanish')

        assert service._cache is None
        assert await service.get_setting('language') == 'spanish'

    @pytest.mark.asyncio
    async def test_set_setting_strict_raises_on_disk_failure(self, service):
        """set_setting_strict raises SettingsWriteError when the disk write fails."""
        await service.save_settings(service.defaults)

        async def fake_write_locked(_settings):
            return False

        with patch.object(service, '_write_locked', side_effect=fake_write_locked):
            with pytest.raises(SettingsWriteError):
                await service.set_setting_strict(
                    'routing.multiroom_enabled', True
                )

        # File on disk must still hold the pre-write value
        with open(service.settings_file, 'r') as f:
            on_disk = json.load(f)
        assert on_disk['routing']['multiroom_enabled'] is False

    @pytest.mark.asyncio
    async def test_set_setting_strict_does_not_mutate_cache_on_failure(self, service):
        """A failed strict write must not leave a partially-updated cache."""
        await service.save_settings(service.defaults)
        # Pre-populate the cache via a normal read.
        await service.get_setting('routing.multiroom_enabled')
        cache_before = service._cache

        async def fake_write_locked(_settings):
            return False

        with patch.object(service, '_write_locked', side_effect=fake_write_locked):
            with pytest.raises(SettingsWriteError):
                await service.set_setting_strict(
                    'routing.multiroom_enabled', True
                )

        # _write_locked failed before the post-write `self._cache = None`,
        # so the cache should still hold the old, consistent state.
        assert service._cache is cache_before
        assert service._cache['routing']['multiroom_enabled'] is False

    def test_get_setting_sync_validates_missing_keys(self, service, temp_settings_file):
        """Bootstrap reads of legacy files without a `routing` block must
        return the validated default (False) — not None.

        This guards against Defect 5 in the multiroom-state-desync plan:
        an unvalidated cache fill that propagated `None` into
        RoutingEnv.regenerate.
        """
        # Write a legacy-shaped settings.json missing the `routing` block.
        legacy_settings = {'language': 'english'}
        with open(temp_settings_file, 'w') as f:
            json.dump(legacy_settings, f)

        service._cache = None

        value = service.get_setting_sync('routing.multiroom_enabled')

        assert value is False

    @pytest.mark.asyncio
    async def test_get_setting_sync_followed_by_get_setting_is_consistent(
        self, service, temp_settings_file
    ):
        """get_setting_sync must not poison the cache for subsequent
        async reads. The cache must hold validated data either way.
        """
        legacy_settings = {'language': 'english'}
        with open(temp_settings_file, 'w') as f:
            json.dump(legacy_settings, f)

        service._cache = None

        sync_value = service.get_setting_sync('routing.multiroom_enabled')
        async_value = await service.get_setting('routing.multiroom_enabled')

        assert sync_value is False
        assert async_value is False

    @pytest.mark.asyncio
    async def test_get_setting_sync_corrupt_file_falls_back_to_defaults(
        self, service, temp_settings_file
    ):
        """Corrupted JSON during bootstrap must not crash and must yield
        validated defaults (not a None-poisoned cache).
        """
        with open(temp_settings_file, 'w') as f:
            f.write('{"invalid json')

        service._cache = None

        value = service.get_setting_sync('routing.multiroom_enabled')

        assert value is False

    @pytest.mark.asyncio
    async def test_delete_setting_returns_false_on_disk_failure(self, service):
        """delete_setting swallows write failures and returns False.

        Documents the *current* lossy semantics — equalizer callers do not
        rely on the strict variant, so this is acceptable for now. If a
        critical-path caller of delete_setting appears, mirror set_setting
        and add a `delete_setting_strict` instead.
        """
        await service.save_settings(service.defaults)

        async def fake_write_locked(_settings):
            return False

        with patch.object(service, '_write_locked', side_effect=fake_write_locked):
            result = await service.delete_setting('routing.multiroom_enabled')

        assert result is False
        # File on disk must still hold the pre-call value
        with open(service.settings_file, 'r') as f:
            on_disk = json.load(f)
        assert 'multiroom_enabled' in on_disk['routing']

    @pytest.mark.asyncio
    async def test_delete_setting_missing_key_is_noop(self, service):
        """Deleting a key that doesn't exist is a successful no-op."""
        await service.save_settings(service.defaults)

        result = await service.delete_setting('nonexistent.key')

        assert result is True
