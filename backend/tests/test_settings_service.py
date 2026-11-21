# backend/tests/test_settings_service.py
"""
Unit tests for SettingsService

A write that fails is failed at the filesystem (`unwritable_dir` below), not by
replacing `_write_locked` with one that returns False: the service's write
boundary is the disk, and the settings file already lives in a directory the
test owns. Replacing the method also replaced its own except/return-False
branch — `_write_locked` could report success on a write that raised and the
whole suite stayed green. How many writes happened is read at the same
boundary, by wrapping `save_versioned_json`.
"""
import pytest
import contextlib
import json
import os
from unittest.mock import patch
from backend.core.settings import SettingsService, SettingsWriteError
from backend.shared.persistence import save_versioned_json


@contextlib.contextmanager
def unwritable_dir(path):
    """Revoke write permission on the directory holding `path`.

    The write boundary of SettingsService is the filesystem, and the settings
    file already lives in a directory the test owns. Taking write permission
    away from it is the failure a read-only or full /var/lib/milo produces, and
    it drives `_write_locked`'s own except/return-False branch instead of
    replacing the method with one that returns False. Reads still work (r-x),
    so a test can check what stayed on disk without restoring first.

    Run as root this grants the write anyway — the strict writes then succeed
    and their `pytest.raises` goes red, which is the safe direction to fail.
    """
    directory = os.path.dirname(path)
    mode = os.stat(directory).st_mode
    os.chmod(directory, 0o500)
    try:
        yield
    finally:
        os.chmod(directory, mode)


class TestSettingsService:
    """Tests for the settings service"""

    @pytest.fixture
    def temp_settings_file(self, tmp_path):
        """A settings path inside pytest's own temp directory.

        Not `NamedTemporaryFile` in /tmp: the service writes *siblings* of this
        path — `.tmp` while saving, and `.<stamp>.bad` when it sets an
        unreadable file aside — so a teardown unlinking a hand-listed set misses
        whichever suffix it was never told about. It missed `.corrupted`, as
        the copy was named then, and every run of the two corruption tests
        left two files in /tmp. That
        matters here because the dev host *is* the appliance and its /tmp is
        tmpfs. A directory pytest owns takes every sibling with it, whatever
        the service decides to write next.
        """
        return str(tmp_path / "settings.json")

    @pytest.fixture
    def service(self, temp_settings_file):
        """Fixture to create a settings service"""
        service = SettingsService()
        service.settings_file = temp_settings_file
        return service

    @pytest.mark.asyncio
    async def test_write_with_no_settings_file_leaves_defaults_intact(self, service):
        """A write on a unit with no settings.json must not rewrite the defaults.

        `_read_locked` falls back to the defaults when the file is missing and
        hands that dict straight to `_apply_key`. A shallow copy shares every
        section, so the write landed inside `self.defaults` itself and every
        later fallback served the written value instead of the default, for the
        life of the process. The fixture leaves no file, as on a fresh unit.
        """
        await service.set_setting('volume.limit_min_db', -33.0)

        assert service.defaults['volume']['limit_min_db'] == -80.0
        assert SettingsService().defaults == service.defaults

    def test_initialization(self, service):
        """Service initialization test"""
        assert service.settings_file is not None
        assert service._cache is None
        assert 'volume' in service.defaults
        assert 'screen' in service.defaults
        assert 'audio' in service.defaults
        assert 'auto_stop_delay' in service.defaults['audio']
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

    def test_a_stored_fec_block_never_exceeds_what_rs8m_holds(self, service):
        """roc's RS8M block is 255 packets, source and repair together: a stored
        200 + 200 would reach the Mac as a block roc cannot build."""
        mac = service._validate_and_merge({'mac': {'fec_block_source': 200, 'fec_block_repair': 200}})['mac']
        assert mac['fec_block_source'] + mac['fec_block_repair'] <= 255

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

    @pytest.mark.parametrize("stored", ["sepia", "", None, ["dark"]])
    def test_validate_and_merge_an_unknown_theme_falls_back_to_the_default(self, service, stored):
        """A theme no token block answers would leave the kiosk with no
        `data-theme` the CSS knows; a list must not raise on the lookup."""
        result = service._validate_and_merge({'screen': {'theme': stored}})
        assert result['screen']['theme'] == service.defaults['screen']['theme']

    def test_validate_and_merge_keeps_a_known_theme(self, service):
        result = service._validate_and_merge({'screen': {'theme': 'dark'}})
        assert result['screen']['theme'] == 'dark'

    def test_validate_and_merge_audio_stop_zero(self, service):
        """Global auto-stop delay validation test with 0 = disabled"""
        # Delay at 0 (disabled)
        result = service._validate_and_merge({
            'audio': {'auto_stop_delay': 0.0}
        })
        assert result['audio']['auto_stop_delay'] == 0.0

        # Normal delay
        result = service._validate_and_merge({
            'audio': {'auto_stop_delay': 15.0}
        })
        assert result['audio']['auto_stop_delay'] == 15.0

        # Delay too small (minimum 1.0s if non-zero)
        result = service._validate_and_merge({
            'audio': {'auto_stop_delay': 0.5}
        })
        assert result['audio']['auto_stop_delay'] == 1.0

    def test_validate_and_merge_audio_stop_non_numeric(self, service):
        """Non-numeric auto_stop_delay must not crash; fall back to default."""
        result = service._validate_and_merge({
            'audio': {'auto_stop_delay': 'broken'}
        })
        assert result['audio']['auto_stop_delay'] == 120.0

    def test_validate_and_merge_resume_window_keeps_null_as_no_limit(self, service):
        """null is the slider's last stop, "forever": read as a missing value
        it would come back as the default, and the choice would undo itself."""
        result = service._validate_and_merge({'audio': {'resume_window': None}})
        assert result['audio']['resume_window'] is None

    def test_validate_and_merge_resume_window_clamps_like_a_delay(self, service):
        """0 is off, a value under a second is a second, past a day is a day,
        and anything that is not a number is the default."""
        merged = lambda raw: service._validate_and_merge({'audio': {'resume_window': raw}})['audio']['resume_window']
        assert merged(0) == 0.0
        assert merged(0.5) == 1.0
        assert merged(10 ** 6) == 86400.0
        assert merged('broken') == service.defaults['audio']['resume_window']
        assert service._validate_and_merge({'audio': {}})['audio']['resume_window'] == (
            service.defaults['audio']['resume_window']
        )

    def test_validate_and_merge_normalization_keeps_one_switch_per_declared_source(self, service):
        """Every mpv source reads `audio.normalization.<source>` before each
        load: a switch the file lost answers None there, which plays
        unfiltered whatever the default says. A switch nobody declares is
        dropped rather than kept."""
        defaults = service.defaults['audio']['normalization']
        some = next(iter(defaults))
        merged = service._validate_and_merge(
            {'audio': {'normalization': {some: not defaults[some], 'spotify': True}}}
        )['audio']['normalization']
        assert merged == {**defaults, some: not defaults[some]}

        for broken in (None, 'on', ['radio']):
            stored = {'audio': {'normalization': broken}}
            assert service._validate_and_merge(stored)['audio']['normalization'] == defaults

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

    def test_validate_and_merge_keeps_volume_placed(self, service):
        """Volume has no switch in the dock list: dock.volume decides where it
        shows. A list stored without it (an older dock, a client that never
        knew it) would hide it on every screen, whatever those switches say,
        and Milo-Mac reading /bulk would disagree with the web dock."""
        result = service._validate_and_merge({
            'dock': {'enabled_apps': ['radio', 'lyrics', 'settings']}
        })
        assert result['dock']['enabled_apps'] == ['radio', 'lyrics', 'volume', 'settings']

        result = service._validate_and_merge({'dock': {'enabled_apps': ['radio']}})
        assert result['dock']['enabled_apps'] == ['radio', 'volume']

    def test_validate_and_merge_routing(self, service):
        """Routing validation test"""
        result = service._validate_and_merge({
            'routing': {'multiroom_enabled': True, 'equalizer_effects_enabled': False}
        })
        assert result['routing']['multiroom_enabled'] is True
        assert result['routing']['equalizer_effects_enabled'] is False

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
    @pytest.mark.parametrize(
        "key_path", ["volume.new_setting", "screen.timeout_secnds", "brand_new_section.x"]
    )
    async def test_writing_an_undeclared_key_is_refused(self, service, caplog, key_path):
        """A key the validator drops is refused, never reported written.

        Breaks the route that writes a new option or a misspelled key: it would
        answer 200 and broadcast a value that is gone at the next read.
        """
        await service.save_settings(service.defaults)

        with caplog.at_level("ERROR"):
            assert await service.set_setting(key_path, 123) is False

        assert await service.get_setting(key_path) is None
        with open(service.settings_file) as f:
            on_disk = json.load(f)
        section, leaf = key_path.split('.')
        assert leaf not in on_disk.get(section, {})
        assert any(
            r.levelname == "ERROR" and key_path in r.getMessage() for r in caplog.records
        )

    @pytest.mark.asyncio
    async def test_strict_write_of_an_undeclared_key_raises(self, service):
        """The strict variant raises on a key the validator drops (multiroom transition)."""
        await service.save_settings(service.defaults)

        with pytest.raises(SettingsWriteError, match="volume.new_setting"):
            await service.set_setting_strict('volume.new_setting', 1)

    @pytest.mark.asyncio
    async def test_a_batch_with_one_undeclared_key_writes_nothing(self, service):
        """One undeclared key refuses the whole batch: all keys land or none do."""
        await service.save_settings(service.defaults)
        before = await service.get_setting('volume.limit_min_db')

        result = await service.set_settings(
            {'volume.limit_min_db': before + 5.0, 'volume.nope': 1}
        )

        assert result is False
        with open(service.settings_file) as f:
            on_disk = json.load(f)
        assert on_disk['volume']['limit_min_db'] == before

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
    async def test_save_settings_returns_false_when_the_write_fails(self, service):
        """A failed persist is reported, not swallowed into a success.

        Patched at the persistence collaborator rather than at whatever file
        primitive it happens to use: this pinned `aiofiles.open` until
        save_versioned_json moved its whole sequence onto a worker thread, at
        which point the patch stopped intercepting anything and the test
        asserted a write that had actually succeeded. The temp-file cleanup this
        used to be named for belongs to persistence.py and is asserted there.
        """
        with patch(
            'backend.core.settings.save_versioned_json', side_effect=Exception('Write error')
        ):
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

        with unwritable_dir(service.settings_file):
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

        with unwritable_dir(service.settings_file):
            with pytest.raises(SettingsWriteError):
                await service.set_setting_strict(
                    'routing.multiroom_enabled', True
                )

        # _write_locked failed before the post-write `self._cache = None`,
        # so the cache should still hold the old, consistent state.
        assert service._cache is cache_before
        assert service._cache['routing']['multiroom_enabled'] is False

    def test_get_setting_sync_validates_missing_keys(self, service, temp_settings_file):
        """Bootstrap reads of a file with no `routing` block must return the
        validated default (False) — not None.

        This guards against Defect 5 in the multiroom-state-desync plan:
        an unvalidated cache fill that propagated `None` into
        RoutingEnv.regenerate.
        """
        # A current-version file that predates the `routing` block being written.
        partial_settings = {
            'schema_version': SettingsService.SCHEMA_VERSION,
            'language': 'english',
        }
        with open(temp_settings_file, 'w') as f:
            json.dump(partial_settings, f)

        service._cache = None

        value = service.get_setting_sync('routing.multiroom_enabled')

        assert value is False

    @pytest.mark.parametrize("stored", [
        {'schema_version': SettingsService.SCHEMA_VERSION + 1},
        {'schema_version': SettingsService.SCHEMA_VERSION, 'volume': None},
    ], ids=["drifted", "a_value_it_cannot_take"])
    def test_the_bootstrap_reader_sets_a_drifted_file_aside_before_deriving_from_it(
        self, service, temp_settings_file, tmp_path, stored
    ):
        """It is the *first* reader on every boot — `dependencies.py` STEP 3b
        derives routing.env / mac.env / snapclient.env from it before any async
        init runs. Consuming a drifted file here would write the three env
        files from a shape nobody validated; refusing it, as it did, looped
        the boot until someone deleted the file over SSH. A value the
        validator cannot take is set aside here too, not left for STEP 4 to
        find: the env files and the file are then the same defaults.
        """
        drifted = json.dumps({**stored, 'routing': {'multiroom_enabled': True}})
        with open(temp_settings_file, 'w') as f:
            f.write(drifted)
        service._cache = None
        fresh = SettingsService()
        fresh.settings_file = str(tmp_path / "absent.json")

        assert service.get_setting_sync('routing.multiroom_enabled') == (
            fresh.get_setting_sync('routing.multiroom_enabled')
        )
        (kept,) = tmp_path.glob("settings.json.*.bad")
        assert kept.read_text() == drifted

    @pytest.mark.asyncio
    async def test_a_write_over_a_drifted_file_keeps_its_bytes_and_restamps_nothing(
        self, service, temp_settings_file, tmp_path
    ):
        """`_read_locked` must not hand a drifted file to `_write_locked`.

        The write path stamps whatever it reads at the current SCHEMA_VERSION,
        so a version it never verified would be migrated in silence. Set aside
        instead, its bytes are kept as they were, and the write lands on the
        defaults: the language the drifted file held is not carried over.
        """
        drifted = json.dumps({
            'schema_version': SettingsService.SCHEMA_VERSION + 1,
            'language': 'french',
        })
        with open(temp_settings_file, 'w') as f:
            f.write(drifted)

        await service.set_setting_strict('routing.multiroom_enabled', True)

        (kept,) = tmp_path.glob("settings.json.*.bad")
        assert kept.read_text() == drifted
        reread = SettingsService()
        reread.settings_file = temp_settings_file
        assert await reread.get_setting('routing.multiroom_enabled') is True
        assert await reread.get_setting('language') == service.defaults['language']

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored", [
        {"volume": None},
        {"screen": {"timeout_seconds": "never"}},
    ], ids=["section_not_an_object", "number_a_word"])
    async def test_the_setup_wizard_finishes_over_a_value_the_validator_cannot_take(
        self, service, temp_settings_file, tmp_path, stored
    ):
        """A file at the right version holding one value `_validate_and_merge`
        raises on. Left in place, it read as the defaults and refused every
        write, at every boot: the kiosk showed the wizard, and the wizard's
        last step (`api/setup.py`: `set_setting("setup_completed", True)`)
        could never land — the unit stuck until someone deleted the file over
        SSH. Set aside, the write lands on the defaults."""
        content = json.dumps({
            "schema_version": SettingsService.SCHEMA_VERSION,
            **service.defaults, "language": "french", **stored,
        })
        with open(temp_settings_file, 'w') as f:
            f.write(content)

        assert await service.set_setting("setup_completed", True) is True

        (kept,) = tmp_path.glob("settings.json.*.bad")
        assert kept.read_text() == content
        reread = SettingsService()
        reread.settings_file = temp_settings_file
        assert await reread.get_setting("setup_completed") is True
        assert await reread.get_setting("language") == service.defaults["language"]

    @pytest.mark.asyncio
    async def test_get_setting_sync_followed_by_get_setting_is_consistent(
        self, service, temp_settings_file
    ):
        """get_setting_sync must not poison the cache for subsequent
        async reads. The cache must hold validated data either way.
        """
        partial_settings = {
            'schema_version': SettingsService.SCHEMA_VERSION,
            'language': 'english',
        }
        with open(temp_settings_file, 'w') as f:
            json.dump(partial_settings, f)

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

    # ------------------------------------------------------------------ #
    # Atomic multi-key writes (set_settings / set_settings_strict)        #
    # ------------------------------------------------------------------ #

    @pytest.mark.asyncio
    async def test_set_settings_persists_all_keys(self, service):
        """A multi-key write lands every key and invalidates the cache."""
        await service.save_settings(service.defaults)
        service._cache = None

        result = await service.set_settings({
            'volume.limit_min_db': -60.0,
            'volume.limit_max_db': -10.0,
        })

        assert result is True
        assert service._cache is None
        assert await service.get_setting('volume.limit_min_db') == -60.0
        assert await service.get_setting('volume.limit_max_db') == -10.0

    @pytest.mark.asyncio
    async def test_set_settings_single_write(self, service):
        """All keys land in exactly one read-modify-write (one os.replace),
        not one per key — that is what closes the torn-write window."""
        await service.save_settings(service.defaults)
        service._cache = None

        with patch('backend.core.settings.save_versioned_json',
                   wraps=save_versioned_json) as spy:
            await service.set_settings({
                'volume.limit_min_db': -55.0,
                'volume.limit_max_db': -12.0,
                'volume.restore_last_volume': False,
            })

        # One trip to the atomic-write primitive — one os.replace, one payload.
        assert spy.await_count == 1

    @pytest.mark.asyncio
    async def test_set_settings_validation_sees_the_full_pair(self, service):
        """The gap constraint must be enforced against BOTH new values at once.

        With two separate set_setting calls, setting min first validates it
        against the OLD max; the atomic write validates min+max together, so a
        wide new range is preserved instead of being clamped on an intermediate
        state.
        """
        await service.save_settings(service.defaults)  # min=-80, max=-20
        service._cache = None

        # New min above the OLD max: a min-then-max sequence would transiently
        # shove max up to min+6. The atomic write sees the real max (-5).
        await service.set_settings({
            'volume.limit_min_db': -15.0,
            'volume.limit_max_db': -5.0,
        })

        assert await service.get_setting('volume.limit_min_db') == -15.0
        assert await service.get_setting('volume.limit_max_db') == -5.0

    @pytest.mark.asyncio
    async def test_set_settings_strict_raises_on_disk_failure(self, service):
        """The strict variant raises and leaves the file untouched on failure."""
        await service.save_settings(service.defaults)

        with unwritable_dir(service.settings_file):
            with pytest.raises(SettingsWriteError):
                await service.set_settings_strict({
                    'volume.limit_min_db': -60.0,
                    'volume.limit_max_db': -10.0,
                })

        with open(service.settings_file, 'r') as f:
            on_disk = json.load(f)
        assert on_disk['volume']['limit_min_db'] == service.defaults['volume']['limit_min_db']
        assert on_disk['volume']['limit_max_db'] == service.defaults['volume']['limit_max_db']

    @pytest.mark.asyncio
    async def test_set_settings_empty_skips_write(self, service):
        """An empty update set is a true no-op: no disk write, cache untouched."""
        await service.save_settings(service.defaults)
        await service.get_setting('language')  # warm the cache
        cache_before = service._cache

        with patch('backend.core.settings.save_versioned_json',
                   wraps=save_versioned_json) as spy:
            result = await service.set_settings({})

        assert result is True
        spy.assert_not_awaited()
        assert service._cache is cache_before


class TestValidateHardwareSection:
    """The `hardware` branch of `_validate_and_merge`: the two remote controls.

    Both of its sub-sections drive real hardware — the BT remote's pairing
    filter and key map, the IR receiver's scancode table — and the controllers
    read the section directly, so what is validated here is what they run.
    """

    @pytest.fixture
    def service(self, tmp_path):
        service = SettingsService()
        service.settings_file = str(tmp_path / "settings.json")
        return service

    @pytest.mark.parametrize("stored", [{}, {"hardware": {}}, {"hardware": {"unknown": {"x": 1}}}])
    def test_a_file_without_the_remotes_emits_the_declared_section(self, service, stored):
        """Emitted whole whatever is stored: a controller reading a key the
        section lacks would answer a default nobody declared."""
        result = service._validate_and_merge(stored)

        assert result["hardware"] == service.defaults["hardware"]

    def test_the_bt_remote_filter_survives_the_round_trip(self, service):
        """The filter is what the pairing scan matches on. Dropped to its
        default, a remote whose name is not ANTICATER stops being adopted, and
        the scan reports nothing found."""
        result = service._validate_and_merge({"hardware": {"bt_remote": {
            "enabled": True,
            "device_name_filter": "Flirc",
            "key_map": {"KEY_PLAYPAUSE": "toggle_play"},
        }}})

        assert result["hardware"]["bt_remote"] == {
            "enabled": True,
            "device_name_filter": "Flirc",
            "key_map": {"KEY_PLAYPAUSE": "toggle_play"},
        }

    def test_a_bt_remote_name_filter_is_capped(self, service):
        """It goes into a scan comparison on every discovered device; an
        unbounded string from disk is a value nothing else limits."""
        result = service._validate_and_merge({"hardware": {"bt_remote": {
            "device_name_filter": "x" * 200,
        }}})

        assert len(result["hardware"]["bt_remote"]["device_name_filter"]) == 64

    @pytest.mark.parametrize("key_map", [["KEY_PLAY"], "115:volume_up", {}])
    def test_a_bt_remote_key_map_that_is_not_a_usable_map_is_the_declared_one(
        self, service, key_map
    ):
        """The controller indexes it per keypress and adopts a device only if
        its keys meet the map. A list or a string from a hand-edited file would
        raise inside the input-event loop — the loop nothing restarts — and an
        empty map adopts no remote at all."""
        result = service._validate_and_merge({"hardware": {"bt_remote": {
            "enabled": True, "key_map": key_map,
        }}})

        assert result["hardware"]["bt_remote"]["key_map"] == (
            service.defaults["hardware"]["bt_remote"]["key_map"]
        )

    def test_the_ir_device_id_is_kept_when_it_fits_a_byte(self, service):
        """The paired remote's address. Lost, the receiver accepts scancodes from
        every remote in the room; changed, it accepts none."""
        result = service._validate_and_merge({"hardware": {"ir_remote": {
            "enabled": True, "device_id": 0x1F, "paired_at": 1787.5,
        }}})

        assert result["hardware"]["ir_remote"] == {
            "enabled": True, "device_id": 0x1F, "paired_at": 1787.5,
        }

    @pytest.mark.parametrize("device_id", [-1, 256, "0x1F", 31.0, None])
    def test_an_ir_device_id_outside_a_byte_is_dropped_to_none(self, service, device_id):
        """It is written into an `ir-keytable` scancode map by
        `milo-apply-ir-keymap`. A value outside 0..0xFF produces a keymap the
        kernel refuses, and the remote stops working with a failure that only
        shows in the journal of a helper script.
        """
        result = service._validate_and_merge({"hardware": {"ir_remote": {
            "enabled": True, "device_id": device_id,
        }}})

        assert result["hardware"]["ir_remote"]["device_id"] is None

    def test_a_boolean_device_id_passes_both_range_checks(self, service):
        """A constat, asserted so it stays visible: `isinstance(x, int)` accepts
        `True`, and `0 <= True <= 0xFF` holds.

        `keymap_writer._validate_device_id` uses the same test, so a
        hand-edited `"device_id": true` survives both and renders a keymap for
        remote 0x01 — silently, since 0x01 is a perfectly valid remote address.

        Latent and left alone: the only producer is the pairing decode,
        `(scancode >> 8) & 0xFF`, which cannot yield a bool. Tightening it would
        mean touching the validator AND the writer for a value nothing can
        write. This turns red the day a producer can.
        """
        result = service._validate_and_merge({"hardware": {"ir_remote": {
            "enabled": True, "device_id": True,
        }}})

        assert result["hardware"]["ir_remote"]["device_id"] is True

    @pytest.mark.parametrize("paired_at,expected", [
        (1787.5, 1787.5),
        (1787, 1787.0),
        ("yesterday", None),
        (None, None),
    ])
    def test_the_pairing_timestamp_is_coerced_to_a_float_or_dropped(
        self, service, paired_at, expected
    ):
        """The settings screen renders it as a date. A string reaching the UI is
        a render error on a page the user opened to unpair a remote."""
        result = service._validate_and_merge({"hardware": {"ir_remote": {
            "paired_at": paired_at,
        }}})

        assert result["hardware"]["ir_remote"]["paired_at"] == expected

    def test_one_sub_section_stored_leaves_the_other_at_its_defaults(self, service):
        """A unit with an IR receiver and no BT remote is the normal case: the
        BT remote reads as the declared one, disabled, not as one the stored
        IR record says anything about."""
        result = service._validate_and_merge({"hardware": {"ir_remote": {"enabled": True}}})

        assert result["hardware"]["bt_remote"] == service.defaults["hardware"]["bt_remote"]
        assert result["hardware"]["ir_remote"]["enabled"] is True


class TestValidateMultiroomSection:
    """`multiroom` is preserved without strict validation, and that is deliberate."""

    @pytest.fixture
    def service(self, tmp_path):
        service = SettingsService()
        service.settings_file = str(tmp_path / "settings.json")
        return service

    def test_the_multiroom_section_survives_untouched(self, service):
        """It holds `client_equalizer[mac]` — one full EQ record per satellite,
        under keys that are MAC addresses. There is no declarable shape to
        validate against, and a validator that projected known keys would drop
        every satellite's curve on the next write.
        """
        stored = {
            "client_types": {"aa:bb:cc:dd:ee:ff": "subwoofer"},
            "client_equalizer": {"aa:bb:cc:dd:ee:ff": {"enabled": True, "mono": False}},
        }

        result = service._validate_and_merge({"multiroom": stored})

        assert result["multiroom"] == stored

    def test_an_absent_multiroom_section_is_not_synthesised(self, service):
        """A direct-mode unit has none. An empty one emitted here would be
        written back and read as "every satellite reset to defaults"."""
        result = service._validate_and_merge({})

        assert "multiroom" not in result

    def test_an_empty_multiroom_section_is_not_carried(self, service):
        result = service._validate_and_merge({"multiroom": {}})

        assert "multiroom" not in result


class TestVolumeLimitGap:
    """The 6 dB floor between the two limits, and the arm that gives up on it."""

    @pytest.fixture
    def service(self, tmp_path):
        service = SettingsService()
        service.settings_file = str(tmp_path / "settings.json")
        return service

    def test_a_gap_narrower_than_six_db_is_widened_upwards(self, service):
        """The slider maps its whole travel onto this range; a 1 dB range makes
        every step of the UI a 0.05 dB change and the control useless."""
        result = service._validate_and_merge({"volume": {
            "limit_min_db": -30.0, "limit_max_db": -29.0,
        }})

        assert result["volume"]["limit_min_db"] == -30.0
        assert result["volume"]["limit_max_db"] == -24.0

    def test_a_range_that_cannot_widen_upwards_is_pinned_to_the_top_six_db(
        self, service
    ):
        """0 dB is the technical ceiling — the DSP cannot amplify — so a
        too-narrow range near the top has nowhere to grow. Left alone, the max
        would exceed unity and every level in the UI would be clipped to a value
        the daemon refuses.
        """
        result = service._validate_and_merge({"volume": {
            "limit_min_db": -1.0, "limit_max_db": 0.0,
        }})

        assert result["volume"]["limit_max_db"] == 0.0
        assert result["volume"]["limit_min_db"] == -6.0

    def test_the_startup_volume_is_clamped_into_the_widened_range(self, service):
        """It is clamped against the limits AFTER they are widened, not the ones
        that came off disk — otherwise a boot could apply a level the running
        limits then refuse to display."""
        result = service._validate_and_merge({"volume": {
            "limit_min_db": -1.0, "limit_max_db": 0.0, "startup_volume_db": -40.0,
        }})

        assert result["volume"]["startup_volume_db"] == -6.0
