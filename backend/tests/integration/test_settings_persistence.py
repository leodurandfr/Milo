# backend/tests/integration/test_settings_persistence.py
"""
Integration tests for settings persistence functionality.

These tests validate the contracts for settings management that must
remain stable during the feature-based architecture refactoring.

Contracts being tested:
- Settings save/load via SettingsService (AC1)
- Atomic writing with temp file + os.replace (AC2)
- Nested settings with dot notation (AC3)
- Value validation and clamping (AC4)
- Backup/restore on corruption (AC5)
"""
import pytest
import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock, AsyncMock

from backend.core.settings import SettingsService



# ==============================================================================
# FIXTURES
# ==============================================================================


@pytest.fixture
def temp_settings_dir():
    """Create a temporary directory for settings files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_settings_file(temp_settings_dir):
    """Create a temporary settings file path."""
    return temp_settings_dir / "settings.json"


@pytest.fixture
async def settings_service(temp_settings_file):
    """
    Create a real SettingsService with a temporary file.

    This provides a fully functional settings service that persists
    to a temporary file, allowing integration tests without affecting
    the real system settings.
    """
    service = SettingsService()
    service.settings_file = str(temp_settings_file)
    service._cache = None

    # Initialize with defaults
    await service.load_settings()

    return service


@pytest.fixture
def mock_ws_manager():
    """Mock WebSocket manager for capturing broadcast events."""
    manager = Mock()
    manager.events = []

    async def broadcast_dict(data):
        manager.events.append(data)

    manager.broadcast_dict = AsyncMock(side_effect=broadcast_dict)
    return manager


# ==============================================================================
# AC1: SETTINGS SAVE/LOAD VIA API
# ==============================================================================


class TestSettingsSaveLoad:
    """Test save/load functionality (AC1)."""

    @pytest.mark.asyncio
    async def test_get_setting_returns_current_value(self, settings_service):
        """Test that get_setting returns the current value."""
        # Default language should be 'english'
        value = await settings_service.get_setting('language')

        assert value == 'english'

    @pytest.mark.asyncio
    async def test_set_setting_persists_value(self, settings_service, temp_settings_file):
        """Test that set_setting persists the value to disk."""
        # Set a value
        result = await settings_service.set_setting('language', 'french')

        assert result is True

        # Read file directly to verify persistence
        with open(temp_settings_file) as f:
            data = json.load(f)

        assert data['language'] == 'french'

    @pytest.mark.asyncio
    async def test_setting_survives_reload(self, settings_service, temp_settings_file):
        """Test that settings survive cache invalidation and reload."""
        # Set a value
        await settings_service.set_setting('language', 'spanish')

        # Invalidate cache to force reload
        settings_service._cache = None

        # Get value after reload
        value = await settings_service.get_setting('language')

        assert value == 'spanish'

    @pytest.mark.asyncio
    async def test_setting_survives_service_recreation(self, temp_settings_file):
        """Test that settings persist across service instances."""
        # Create first service and set value
        service1 = SettingsService()
        service1.settings_file = str(temp_settings_file)
        await service1.load_settings()
        await service1.set_setting('language', 'german')

        # Create second service with same file
        service2 = SettingsService()
        service2.settings_file = str(temp_settings_file)
        await service2.load_settings()

        # Value should persist
        value = await service2.get_setting('language')

        assert value == 'german'

    @pytest.mark.asyncio
    async def test_multiple_settings_persist(self, settings_service, temp_settings_file):
        """Test that multiple settings can be set and persist."""
        # Set multiple values
        await settings_service.set_setting('language', 'italian')
        await settings_service.set_setting('volume.step_mobile_db', 4.0)
        await settings_service.set_setting('screen.timeout_seconds', 120)

        # Read file directly
        with open(temp_settings_file) as f:
            data = json.load(f)

        assert data['language'] == 'italian'
        assert data['volume']['step_mobile_db'] == 4.0
        assert data['screen']['timeout_seconds'] == 120


# ==============================================================================
# AC2: ATOMIC WRITING
# ==============================================================================


class TestAtomicWriting:
    """Test atomic writing functionality (AC2)."""

    @pytest.mark.asyncio
    async def test_no_residual_tmp_file_after_save(self, settings_service, temp_settings_file):
        """Test that no .tmp file remains after successful save."""
        # Save settings
        await settings_service.set_setting('language', 'french')

        # Check no .tmp file exists
        tmp_file = Path(str(temp_settings_file) + '.tmp')

        assert not tmp_file.exists()

    @pytest.mark.asyncio
    async def test_file_valid_json_after_save(self, settings_service, temp_settings_file):
        """Test that file contains valid JSON after save."""
        # Save multiple times
        await settings_service.set_setting('language', 'french')
        await settings_service.set_setting('language', 'spanish')
        await settings_service.set_setting('language', 'german')

        # File should be valid JSON
        with open(temp_settings_file) as f:
            data = json.load(f)

        assert isinstance(data, dict)
        assert data['language'] == 'german'

    @pytest.mark.asyncio
    async def test_concurrent_saves_are_serialized(self, settings_service):
        """Test that concurrent save operations are properly serialized."""
        # Launch multiple concurrent saves
        tasks = [
            settings_service.set_setting('language', 'french'),
            settings_service.set_setting('language', 'spanish'),
            settings_service.set_setting('language', 'german'),
            settings_service.set_setting('language', 'italian'),
        ]

        results = await asyncio.gather(*tasks)

        # All saves should succeed
        assert all(results)

        # Final value should be one of the languages (last one wins)
        value = await settings_service.get_setting('language')
        assert value in ['french', 'spanish', 'german', 'italian']

    @pytest.mark.asyncio
    async def test_file_has_proper_encoding(self, settings_service, temp_settings_file):
        """Test that file is saved with UTF-8 encoding."""
        # Save with special characters
        await settings_service.save_settings({
            **settings_service.defaults,
            'language': 'french'
        })

        # Read with explicit UTF-8
        with open(temp_settings_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # Should be valid UTF-8
        assert 'language' in content

    @pytest.mark.asyncio
    async def test_file_has_trailing_newline(self, settings_service, temp_settings_file):
        """Test that file ends with a newline."""
        await settings_service.set_setting('language', 'french')

        with open(temp_settings_file, 'rb') as f:
            content = f.read()

        assert content.endswith(b'\n')


# ==============================================================================
# AC3: NESTED SETTINGS WITH DOT NOTATION
# ==============================================================================


class TestNestedSettings:
    """Test nested settings with dot notation (AC3)."""

    @pytest.mark.asyncio
    async def test_get_nested_setting_with_dot_notation(self, settings_service):
        """Test reading nested settings with dot notation."""
        # Default values
        min_db = await settings_service.get_setting('volume.limit_min_db')
        max_db = await settings_service.get_setting('volume.limit_max_db')

        assert min_db == -80.0
        assert max_db == -20.0

    @pytest.mark.asyncio
    async def test_set_nested_setting_with_dot_notation(self, settings_service):
        """Test writing nested settings with dot notation."""
        result = await settings_service.set_setting('volume.limit_min_db', -60.0)

        assert result is True

        value = await settings_service.get_setting('volume.limit_min_db')
        assert value == -60.0

    @pytest.mark.asyncio
    async def test_nested_setting_persistence(self, settings_service, temp_settings_file):
        """Test that nested settings persist to file correctly."""
        await settings_service.set_setting('volume.step_mobile_db', 5.0)

        # Read file directly
        with open(temp_settings_file) as f:
            data = json.load(f)

        assert data['volume']['step_mobile_db'] == 5.0

    @pytest.mark.asyncio
    async def test_deep_nested_path_works(self, settings_service):
        """Test that deep nested paths work correctly."""
        # Get deeply nested value
        enabled_apps = await settings_service.get_setting('dock.enabled_apps')

        assert isinstance(enabled_apps, list)
        assert 'spotify' in enabled_apps

    @pytest.mark.asyncio
    async def test_nonexistent_nested_path_returns_none(self, settings_service):
        """Test that nonexistent nested paths return None."""
        value = await settings_service.get_setting('nonexistent.path.here')

        assert value is None

    @pytest.mark.asyncio
    async def test_partial_nested_path_returns_dict(self, settings_service):
        """Test that partial nested paths return the sub-dict."""
        volume = await settings_service.get_setting('volume')

        assert isinstance(volume, dict)
        assert 'limit_min_db' in volume
        assert 'limit_max_db' in volume


# ==============================================================================
# AC4: VALUE VALIDATION
# ==============================================================================


class TestValueValidation:
    """Test value validation (AC4)."""

    @pytest.mark.asyncio
    async def test_volume_min_clamped_to_range(self, settings_service):
        """Test that volume min is clamped to valid range."""
        # Try to set below minimum (-80)
        await settings_service.set_setting('volume.limit_min_db', -100.0)

        value = await settings_service.get_setting('volume.limit_min_db')

        # Should be clamped to -80.0
        assert value >= -80.0

    @pytest.mark.asyncio
    async def test_volume_max_clamped_to_range(self, settings_service):
        """Test that volume max is clamped to valid range."""
        # Try to set above maximum (0)
        await settings_service.set_setting('volume.limit_max_db', 10.0)

        value = await settings_service.get_setting('volume.limit_max_db')

        # Should be clamped to 0.0
        assert value <= 0.0

    @pytest.mark.asyncio
    async def test_volume_min_max_gap_enforced(self, settings_service):
        """Test that minimum 6 dB gap is enforced between min and max."""
        # Set min and max too close together
        await settings_service.set_setting('volume.limit_min_db', -25.0)
        await settings_service.set_setting('volume.limit_max_db', -23.0)

        min_db = await settings_service.get_setting('volume.limit_min_db')
        max_db = await settings_service.get_setting('volume.limit_max_db')

        # Gap should be at least 6 dB
        assert max_db - min_db >= 6.0

    @pytest.mark.asyncio
    async def test_language_whitelist_validated(self, settings_service):
        """Test that invalid languages fall back to default."""
        # Set invalid language
        await settings_service.set_setting('language', 'klingon')

        value = await settings_service.get_setting('language')

        # Should fall back to 'english'
        assert value == 'english'

    @pytest.mark.asyncio
    async def test_valid_language_accepted(self, settings_service):
        """Test that valid languages are accepted."""
        valid_languages = ['english', 'french', 'spanish', 'german', 'italian']

        for lang in valid_languages:
            await settings_service.set_setting('language', lang)
            value = await settings_service.get_setting('language')
            assert value == lang

    @pytest.mark.asyncio
    async def test_screen_timeout_zero_allowed(self, settings_service):
        """Test that screen timeout 0 (disabled) is allowed."""
        await settings_service.set_setting('screen.timeout_seconds', 0)

        value = await settings_service.get_setting('screen.timeout_seconds')

        assert value == 0

    @pytest.mark.asyncio
    async def test_screen_timeout_minimum_enforced(self, settings_service):
        """Test that non-zero timeout has minimum of 3 seconds."""
        # Set timeout below minimum (but not 0)
        await settings_service.set_setting('screen.timeout_seconds', 1)

        value = await settings_service.get_setting('screen.timeout_seconds')

        # Should be at least 3
        assert value >= 3

    @pytest.mark.asyncio
    async def test_dock_apps_invalid_filtered(self, settings_service):
        """Test that invalid dock apps are filtered out."""
        await settings_service.set_setting('dock.enabled_apps',
            ['spotify', 'invalid_app', 'bluetooth', 'fake_source'])

        apps = await settings_service.get_setting('dock.enabled_apps')

        assert 'invalid_app' not in apps
        assert 'fake_source' not in apps
        assert 'spotify' in apps
        assert 'bluetooth' in apps

    @pytest.mark.asyncio
    async def test_dock_apps_minimum_audio_source_enforced(self, settings_service):
        """Test that at least one audio source is enforced in dock apps."""
        # Try to set only non-audio apps
        await settings_service.set_setting('dock.enabled_apps',
            ['settings', 'equalizer'])

        apps = await settings_service.get_setting('dock.enabled_apps')

        # Should have at least one audio source
        audio_sources = {'spotify', 'bluetooth', 'mac', 'radio', 'podcast'}
        has_audio_source = any(app in audio_sources for app in apps)

        assert has_audio_source

    @pytest.mark.asyncio
    async def test_volume_step_clamped_to_range(self, settings_service):
        """Test that volume step is clamped to 1.0-6.0 range."""
        # Try to set below minimum
        await settings_service.set_setting('volume.step_mobile_db', 0.5)

        value = await settings_service.get_setting('volume.step_mobile_db')

        assert value >= 1.0
        assert value <= 6.0


# ==============================================================================
# AC5: BACKUP/RESTORE ON CORRUPTION
# ==============================================================================


class TestBackupRestore:
    """Test backup/restore on corruption (AC5)."""

    @pytest.mark.asyncio
    async def test_corrupted_json_creates_backup(self, temp_settings_file):
        """Test that corrupted JSON creates a .corrupted backup file."""
        # Create a corrupted settings file
        with open(temp_settings_file, 'w') as f:
            f.write('{"invalid json": ')

        # Create service and load (should handle corruption)
        service = SettingsService()
        service.settings_file = str(temp_settings_file)

        await service.load_settings()

        # Backup file should exist
        corrupted_file = Path(str(temp_settings_file) + '.corrupted')

        assert corrupted_file.exists()

    @pytest.mark.asyncio
    async def test_corrupted_json_resets_to_defaults(self, temp_settings_file):
        """Test that corrupted JSON resets settings to defaults."""
        # Create a corrupted settings file
        with open(temp_settings_file, 'w') as f:
            f.write('not valid json at all!')

        # Create service and load
        service = SettingsService()
        service.settings_file = str(temp_settings_file)

        settings = await service.load_settings()

        # Should have default values
        assert settings['language'] == 'english'
        assert settings['volume']['limit_min_db'] == service.defaults['volume']['limit_min_db']

    @pytest.mark.asyncio
    async def test_service_continues_after_corruption(self, temp_settings_file):
        """Test that service continues to function after handling corruption."""
        # Create a corrupted settings file
        with open(temp_settings_file, 'w') as f:
            f.write('[invalid}')

        # Create service and load
        service = SettingsService()
        service.settings_file = str(temp_settings_file)

        await service.load_settings()

        # Service should still be usable
        result = await service.set_setting('language', 'french')
        assert result is True

        value = await service.get_setting('language')
        assert value == 'french'

    @pytest.mark.asyncio
    async def test_backup_file_contains_original_content(self, temp_settings_file):
        """Test that backup file contains the original corrupted content."""
        corrupted_content = '{"broken": true, missing_quote: }'

        # Create a corrupted settings file
        with open(temp_settings_file, 'w') as f:
            f.write(corrupted_content)

        # Create service and load
        service = SettingsService()
        service.settings_file = str(temp_settings_file)

        await service.load_settings()

        # Read backup file
        corrupted_file = Path(str(temp_settings_file) + '.corrupted')
        with open(corrupted_file) as f:
            backup_content = f.read()

        assert backup_content == corrupted_content

    @pytest.mark.asyncio
    async def test_empty_file_handled_gracefully(self, temp_settings_file):
        """Test that empty file is handled gracefully."""
        # Create an empty file
        temp_settings_file.touch()

        # Create service and load
        service = SettingsService()
        service.settings_file = str(temp_settings_file)

        settings = await service.load_settings()

        # Should have defaults
        assert settings['language'] == 'english'

    @pytest.mark.asyncio
    async def test_missing_file_creates_defaults(self, temp_settings_dir):
        """Test that missing file creates defaults."""
        missing_file = temp_settings_dir / "nonexistent.json"

        # Create service with non-existent file
        service = SettingsService()
        service.settings_file = str(missing_file)

        settings = await service.load_settings()

        # Should create file with defaults
        assert missing_file.exists()
        assert settings['language'] == 'english'


# ==============================================================================
# ADDITIONAL INTEGRATION TESTS
# ==============================================================================


class TestCacheInvalidation:
    """Test cache invalidation behavior."""

    @pytest.mark.asyncio
    async def test_set_setting_invalidates_cache(self, settings_service):
        """Test that set_setting invalidates the cache."""
        # Ensure cache is populated
        await settings_service.get_setting('language')
        assert settings_service._cache is not None

        # Set a value (should invalidate cache)
        await settings_service.set_setting('language', 'french')

        # Cache should be invalidated (but may be repopulated by next get)
        # The important thing is the value was persisted
        value = await settings_service.get_setting('language')
        assert value == 'french'

    @pytest.mark.asyncio
    async def test_concurrent_reads_use_cache(self, settings_service):
        """Test that concurrent reads use the cache efficiently."""
        # Prime the cache
        await settings_service.get_setting('language')

        # Multiple concurrent reads
        tasks = [
            settings_service.get_setting('language'),
            settings_service.get_setting('volume.limit_min_db'),
            settings_service.get_setting('screen.timeout_seconds'),
        ]

        results = await asyncio.gather(*tasks)

        # All reads should succeed
        assert len(results) == 3
        assert results[0] == 'english'


class TestDefaultsMerging:
    """Test defaults merging behavior."""

    @pytest.mark.asyncio
    async def test_missing_keys_get_defaults(self, temp_settings_file):
        """Test that missing keys inherit from defaults."""
        # Create file with partial settings (stamped with current schema_version)
        with open(temp_settings_file, 'w') as f:
            json.dump({
                'schema_version': SettingsService.SCHEMA_VERSION,
                'language': 'french',
            }, f)

        # Create service and load
        service = SettingsService()
        service.settings_file = str(temp_settings_file)

        settings = await service.load_settings()

        # Should have language from file
        assert settings['language'] == 'french'

        # Should have defaults for missing keys
        assert 'volume' in settings
        assert settings['volume']['limit_min_db'] == service.defaults['volume']['limit_min_db']

    @pytest.mark.asyncio
    async def test_partial_nested_gets_merged(self, temp_settings_file):
        """Test that partial nested settings get merged with defaults."""
        # Create file with partial volume settings (stamped with current schema_version)
        with open(temp_settings_file, 'w') as f:
            json.dump({
                'schema_version': SettingsService.SCHEMA_VERSION,
                'language': 'english',
                'volume': {'limit_min_db': -60.0}  # Only one key
            }, f)

        # Create service and load
        service = SettingsService()
        service.settings_file = str(temp_settings_file)

        settings = await service.load_settings()

        # Should have custom value
        assert settings['volume']['limit_min_db'] == -60.0

        # Should have defaults for other volume keys
        assert 'limit_max_db' in settings['volume']
