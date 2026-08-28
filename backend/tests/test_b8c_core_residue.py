"""The arms left in the core after every unit of Lot B had passed through it.

Four clusters, chosen by what they cost rather than by how many lines they are:

* **the EQ partial updates.** Each knob in the EQ tab sends only the field it
  moved, and the handler writes only that field back into the record it just
  read. A field that stopped being conditional would carry a `None` into the
  stored record — a band with no Q, a compressor with no attack — and the record
  is what a satellite is re-configured from on its next reconnection. The
  `ValueError` above them is the refusal for a zone or client id that no longer
  exists, which is ordinary: both arrive from a URL path segment.
* **the volume lock's two timeouts.** `set_volume_db` and `adjust_volume_db`
  wait at most 2 s for the lock. The rotary encoder emits a command per detent,
  so the contended case is a knob being spun — and an unbounded wait there
  queues every detent behind the first one instead of dropping the ones that no
  longer describe where the knob is.
* **`SettingsService`'s fail-open reads.** `settings.json` is the store every
  service reads its configuration from. A load that raised on a corrupt file
  would take the boot with it — and the schema-version protocol is deliberately
  *not* this path: it fails loud with a banner, while an unreadable file falls
  back to declared defaults.
* **the OTA rollbacks.** They run after an update has already failed. Reporting
  a rollback that did not happen is what leaves a source with no working binary
  and a UI that says it was restored.
"""
import asyncio
import contextlib
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from backend.core.equalizer import MultiroomEqualizerService
from backend.core.multiroom.models import (
    CompressorSettings,
    EqFilter,
    EqualizerSettings,
    LoudnessSettings,
)
from backend.core.settings import SettingsService


@pytest.fixture
def registry():
    reg = Mock()
    reg.get_zone = Mock(return_value=None)
    reg.get_client = Mock(return_value=None)
    reg.get_client_equalizer = Mock(return_value=None)
    reg.set_client_equalizer = AsyncMock()
    reg.set_clients_equalizer = AsyncMock()
    reg.get_online_zone_clients = Mock(return_value=[])
    reg.is_local_client = Mock(side_effect=lambda mac_id: mac_id == "local")
    return reg


@pytest.fixture
def camilladsp():
    cam = Mock()
    cam.connected = True
    cam.set_filter = AsyncMock(return_value=True)
    cam.set_compressor = AsyncMock(return_value=True)
    cam.set_loudness = AsyncMock(return_value=True)
    cam.set_mono = AsyncMock(return_value=True)
    cam.apply_settings = AsyncMock(return_value=True)
    cam.get_equalizer_settings = Mock(side_effect=lambda: EqualizerSettings.default())
    cam.persist_state = AsyncMock()
    cam.schedule_persist = Mock()
    cam.update_cache = AsyncMock()
    cam.settings_service = None
    return cam


@pytest.fixture
def eq_service(registry, camilladsp):
    sm = Mock()
    sm.broadcast = AsyncMock()
    svc = MultiroomEqualizerService(
        client_registry_service=registry,
        camilladsp_service=camilladsp,
        state_machine=sm,
    )
    svc.equalizer_router = Mock()
    svc.equalizer_router.set_filter = AsyncMock(return_value={"status": "success"})
    svc.equalizer_router.set_compressor = AsyncMock(return_value={"status": "success"})
    svc.equalizer_router.set_loudness = AsyncMock(return_value={"status": "success"})
    svc.equalizer_router.set_mono = AsyncMock(return_value={"status": "success"})
    return svc


def _record():
    """A record with every field away from its default, so a lost one is visible."""
    return EqualizerSettings(
        filters=[EqFilter(id="eq_band_00", frequency=250.0, gain=4.0, q=1.41)],
        compressor=CompressorSettings(
            enabled=True, threshold=-25.0, ratio=3.0, attack=12.0,
            release=180.0, makeup_gain=2.0,
        ),
        loudness=LoudnessSettings(enabled=True, high_boost=6.0, low_boost=5.0),
        mono=False,
    )


def _stored(registry):
    registry.set_clients_equalizer.assert_awaited_once()
    return next(iter(registry.set_clients_equalizer.await_args.args[0].values()))


class TestTheEqualizerPartialUpdates:
    """One knob moved, one field written, and everything else left alone."""

    @pytest.fixture(autouse=True)
    def one_remote_client(self, registry):
        registry.get_client_equalizer = Mock(return_value=_record())
        registry.get_client = Mock(return_value=Mock(ip="192.168.1.153", is_local=False))
        registry.is_local_client = Mock(return_value=False)

    BAND = {"frequency": 250.0, "gain": 4.0, "q": 1.41, "enabled": True}
    COMP = {"enabled": True, "threshold": -25.0, "ratio": 3.0, "attack": 12.0,
            "release": 180.0, "makeup_gain": 2.0}
    LOUD = {"enabled": True, "high_boost": 6.0, "low_boost": 5.0}

    @staticmethod
    def _one_moved(obj, baseline, field, value):
        """The moved field took the new value; every other one is untouched."""
        assert getattr(obj, field) == value
        for other, held in baseline.items():
            if other != field:
                assert getattr(obj, other) == held, f"{other} was rewritten"

    @pytest.mark.parametrize("field,value", [
        ("frequency", 500.0), ("gain", -3.0), ("q", 0.7), ("enabled", False),
    ])
    async def test_one_band_field_moves_and_the_others_hold(
        self, eq_service, registry, field, value
    ):
        """The EQ tab sends one field per drag. A `None` written into the record
        is what a satellite is reconfigured from on its next reconnection — a
        band with no Q is a band CamillaDSP refuses."""
        assert await eq_service.update_filter(
            "client", "aa:bb:cc:dd:ee:07", "eq_band_00", **{field: value}
        ) is True

        self._one_moved(_stored(registry).filters[0], self.BAND, field, value)

    @pytest.mark.parametrize("field,value", [
        ("enabled", False), ("threshold", -30.0), ("ratio", 6.0),
        ("attack", 30.0), ("release", 400.0), ("makeup_gain", 5.0),
    ])
    async def test_one_compressor_field_moves_and_the_others_hold(
        self, eq_service, registry, field, value
    ):
        """Attack and release are the two the ear notices; a release reset to a
        default while the threshold moved is a compressor that pumps."""
        assert await eq_service.update_compressor(
            "client", "aa:bb:cc:dd:ee:07", **{field: value}
        ) is True

        self._one_moved(_stored(registry).compressor, self.COMP, field, value)

    @pytest.mark.parametrize("field,value", [
        ("enabled", False), ("high_boost", 9.0), ("low_boost", 1.0),
    ])
    async def test_one_loudness_field_moves_and_the_others_hold(
        self, eq_service, registry, field, value
    ):
        assert await eq_service.update_loudness(
            "client", "aa:bb:cc:dd:ee:07", **{field: value}
        ) is True

        self._one_moved(_stored(registry).loudness, self.LOUD, field, value)

    async def test_mono_is_written_without_touching_the_filters(
        self, eq_service, registry
    ):
        """Mono is a mixer change; reapplying the filters with it is what the
        targeted path exists to avoid — every band rewritten for a toggle."""
        assert await eq_service.update_mono("client", "aa:bb:cc:dd:ee:07", True) is True

        stored = _stored(registry)
        assert stored.mono is True
        assert stored.filters[0].gain == 4.0
        assert stored.compressor.threshold == -25.0

    @pytest.mark.parametrize("call", [
        lambda s: s.update_filter("zone", "gone", "eq_band_00", gain=1.0),
        lambda s: s.update_compressor("zone", "gone", ratio=2.0),
        lambda s: s.update_loudness("zone", "gone", high_boost=1.0),
        lambda s: s.update_mono("zone", "gone", True),
    ], ids=["filter", "compressor", "loudness", "mono"])
    async def test_an_unknown_target_is_refused_rather_than_created(
        self, eq_service, registry, call
    ):
        """The target is a URL path segment: `zone:<id>` for a zone the user just
        deleted in another tab. Writing would create an EQ record keyed on an id
        no client and no zone answers to, which nothing ever reads back."""
        registry.get_client_equalizer = Mock(return_value=None)
        registry.get_zone = Mock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await call(eq_service)

        registry.set_clients_equalizer.assert_not_called()

    async def test_a_band_id_that_is_not_in_the_record_is_refused(
        self, eq_service, registry
    ):
        """The band count comes from the loaded preset; an id past its end would
        otherwise be a silent no-op reported as success."""
        with pytest.raises(ValueError, match="Filter not found"):
            await eq_service.update_filter(
                "client", "aa:bb:cc:dd:ee:07", "eq_band_99", gain=1.0
            )

    async def test_without_a_registry_a_client_apply_is_refused_loudly(
        self, eq_service, caplog
    ):
        """The registry holds every remote record; without it the only honest
        answer is that nothing was applied."""
        eq_service._registry = None

        with caplog.at_level(logging.ERROR, logger="backend.core.equalizer.multiroom_service"):
            assert await eq_service.apply_client_equalizer("aa:bb:cc:dd:ee:07", _record()) is False

        assert any("not available" in r.message for r in caplog.records)


class TestTheSettingsFailOpenReads:
    """`settings.json` is where every service reads its configuration."""

    @pytest.fixture
    def service(self, tmp_path, monkeypatch):
        svc = SettingsService()
        monkeypatch.setattr(svc, "settings_file", str(tmp_path / "settings.json"))
        svc._path = tmp_path / "settings.json"
        return svc

    async def test_an_unreadable_store_falls_back_to_the_declared_defaults(
        self, service, caplog
    ):
        """Not the schema-version path: that one fails loud with a banner and
        stops the boot on purpose. A file that is merely unreadable must not,
        because every service reads through here — the appliance would refuse to
        start over a truncated write."""
        with patch("backend.core.settings.load_versioned_json",
                   AsyncMock(side_effect=OSError("input/output error"))):
            with caplog.at_level(logging.ERROR, logger="backend.core.settings"):
                loaded = await service.load_settings()

        assert loaded["volume"]["startup_volume_db"] == service.defaults["volume"]["startup_volume_db"]
        assert any("Error loading settings" in r.message for r in caplog.records)
        assert not service._path.exists(), (
            "the fallback rewrote the store it could not read"
        )

    async def test_the_fallback_is_cached_so_the_failure_is_read_once(self, service):
        """Every `get_setting` goes through the cache; re-reading a broken file
        per lookup would put an ERROR in the journal on every settings access."""
        with patch("backend.core.settings.load_versioned_json",
                   AsyncMock(side_effect=OSError("input/output error"))):
            first = await service.load_settings()

        second = await service.get_all_settings()

        assert second == first
        assert second is not first, "the cache handed out its own dict"

    def test_a_sync_read_of_a_key_that_is_not_there_answers_none(self, service):
        """`get_setting_sync` is read from properties on hot paths — the routing
        service's `multiroom_enabled` among them. A `KeyError` there is an
        exception inside a property, which reads as the service being broken."""
        service._cache = {"routing": {"multiroom_enabled": True}}

        assert service.get_setting_sync("routing.multiroom_enabled") is True
        assert service.get_setting_sync("routing.nothing_here") is None
        assert service.get_setting_sync("nothing.at.all") is None
        assert service.get_setting_sync("routing.multiroom_enabled.deeper") is None

    async def test_invalidating_the_cache_forces_the_next_read_off_the_disk(
        self, service
    ):
        """The dock handler writes and re-reads within one request; a stale cache
        there answers with the list it just replaced."""
        service._path.write_text(json.dumps(
            {"schema_version": SettingsService.SCHEMA_VERSION, "dock": {"enabled_apps": ["radio"]}}
        ))
        await service.load_settings()

        service._path.write_text(json.dumps(
            {"schema_version": SettingsService.SCHEMA_VERSION, "dock": {"enabled_apps": ["spotify"]}}
        ))
        service.invalidate_cache()

        assert (await service.get_all_settings())["dock"]["enabled_apps"] == ["spotify"]

    async def test_an_empty_strict_write_is_a_no_op(self, service):
        """`set_settings_strict` is called with whatever the caller collected;
        an empty batch must not rewrite the file for nothing — every write is an
        SD-card write on this appliance."""
        await service.load_settings()
        before = service._path.read_bytes()

        await service.set_settings_strict({})

        assert service._path.read_bytes() == before


@contextlib.asynccontextmanager
async def _expires_at_once(delay):
    """Stand in for `asyncio.timeout(2.0)`, expired.

    Carried on the primitive the code actually uses: holding the lock and
    letting the real two seconds elapse would be a wall-clock budget in the
    suite, and the bound is what is under test, not how long it takes.
    """
    assert delay == 2.0, "the volume lock's bound moved"
    raise asyncio.TimeoutError
    yield  # pragma: no cover -- unreachable, keeps this an async generator


class TestTheVolumeLockTimeouts:
    """Two seconds, then the command is dropped rather than queued."""

    @pytest.fixture
    def service(self):
        from backend.core.volume.service import VolumeService

        from backend.core.models.volume import VolumeConfig

        svc = VolumeService.__new__(VolumeService)
        svc._volume_lock = asyncio.Lock()
        svc.logger = logging.getLogger("backend.core.volume.service")
        svc._volume_control = True
        svc._volume_config = VolumeConfig()
        svc._state_store = Mock()
        svc._is_multiroom_enabled = Mock(return_value=True)
        svc._get_controllable_client_ids = Mock(return_value=["aa:bb:cc:dd:ee:07"])
        svc._compute_multiroom_updates = AsyncMock(return_value={})
        svc._state_store.get_complete_state = AsyncMock(return_value={})
        return svc

    async def test_a_set_that_cannot_take_the_lock_in_time_is_dropped(
        self, service, caplog
    ):
        """The rotary encoder emits one command per detent. Queueing behind a
        held lock replays a knob position the user already left — the level
        walks after the hand stops."""
        with caplog.at_level(logging.WARNING, logger="backend.core.volume.service"):
            with patch.object(asyncio, "timeout", _expires_at_once):
                assert await service.set_volume_db(-20.0) is False

        assert any("Timeout waiting for volume lock" in r.message for r in caplog.records)
        service._compute_multiroom_updates.assert_not_called()

    async def test_an_adjust_that_cannot_take_the_lock_in_time_is_dropped(
        self, service, caplog
    ):
        """Same bound on the relative path, which is the one the encoder and the
        IR remote both use."""
        with caplog.at_level(logging.WARNING, logger="backend.core.volume.service"):
            with patch.object(asyncio, "timeout", _expires_at_once):
                assert await service.adjust_volume_db(2.0) is False

        assert any("Timeout waiting for volume lock" in r.message for r in caplog.records)
        # `get_complete_state` is the first thing the guarded body reaches, so it
        # is what says the command was dropped rather than queued.
        service._state_store.get_complete_state.assert_not_called()


class TestTheAutoStopReload:
    """`BaseAudioSource`'s pause timer, which every source inherits."""

    @pytest.fixture
    def source(self):
        from backend.core.audio_source import BaseAudioSource

        class _Source(BaseAudioSource):
            async def _do_start(self):
                return True

        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        state_machine = Mock()
        state_machine.broadcast = AsyncMock()
        state_machine.update_source_state = AsyncMock()
        return _Source(
            source_id="probe",
            service_name="milo-probe.service",
            state_machine=state_machine,
            systemd_manager=Mock(),
            settings_service=settings,
        )

    async def test_a_shorter_delay_restarts_a_running_timer(self, source):
        """The setting is changed while a source is paused. Leaving the old timer
        running means the change takes effect one auto-stop later — i.e. after
        the very stop it was meant to retime."""
        source.auto_stop_enabled = True
        source.auto_stop_delay = 600.0
        source._start_pause_timer()
        first = source._pause_timer
        assert first is not None

        source.auto_stop_delay = 10.0
        await source.reload_auto_stop_config()

        assert source._pause_timer is not None, "the timer was cancelled and not restarted"
        assert source._pause_timer is not first
        await asyncio.gather(first, return_exceptions=True)
        assert first.cancelled()
        source._cancel_pause_timer()

    async def test_disabling_auto_stop_cancels_the_running_timer(self, source):
        """Otherwise turning it off in Settings still stops the music once."""
        source.auto_stop_enabled = True
        source.auto_stop_delay = 600.0
        source._start_pause_timer()
        timer = source._pause_timer

        source.auto_stop_enabled = False
        await source.reload_auto_stop_config()

        assert source._pause_timer is None
        await asyncio.gather(timer, return_exceptions=True)
        assert timer.cancelled()

    async def test_an_unreadable_auto_stop_setting_leaves_the_declared_default(
        self, source, caplog
    ):
        """It runs at every source start; raising would fail the start of a
        source over a settings read."""
        source._settings_service.get_setting = AsyncMock(
            side_effect=RuntimeError("settings.json is gone")
        )

        with caplog.at_level(logging.ERROR, logger=source._logger.name):
            await source._load_auto_stop_config()

        assert any("Auto-stop settings load failed" in r.message for r in caplog.records)

    async def test_a_failing_auto_stop_does_not_kill_the_timer_task_silently(
        self, source, caplog
    ):
        """The stop it triggers reaches the state machine and the source's own
        `_do_stop`; an exception there would surface as a task that vanished,
        with the source left paused forever and nothing said."""
        source.auto_stop_enabled = True
        source.auto_stop_delay = 0.0
        source._on_auto_stop = AsyncMock(side_effect=RuntimeError("mpv is gone"))

        with caplog.at_level(logging.ERROR, logger=source._logger.name):
            source._start_pause_timer()
            await asyncio.gather(source._pause_timer, return_exceptions=True)

        assert any("Auto-stop failed" in r.message for r in caplog.records)

    async def test_an_unhandled_command_is_refused_by_name(self, source):
        """The ABC's default arm. `command()` validates against `COMMANDS`
        first, so this is what a source that declared a command and forgot to
        dispatch it answers — the name is the only thing that says which."""
        result = await source._handle_command("teleport", {})

        assert result["success"] is False
        assert "teleport" in str(result)

    async def test_a_source_that_cannot_refresh_says_so(self, source):
        """The ABC default: a source with no metadata feed answers False rather
        than pretending it republished."""
        assert await source.refresh_metadata() is False


class TestTheUpdateRollbacks:
    """They run after an update already failed. A false success is the worst answer."""

    @pytest.fixture
    def service(self):
        from backend.core.systemd import SystemdServiceManager
        from backend.core.updates.update import UpdateService

        with patch.dict("os.environ", {}, clear=True):
            return UpdateService(systemd_manager=SystemdServiceManager(),
                                 satellite_update_service=Mock())

    @pytest.fixture(autouse=True)
    def never_a_real_process(self):
        """This service's argv includes `git -C /home/milo/milo reset --hard`."""
        async def _refuse(program, *args, **kwargs):
            raise AssertionError(f"a real process was spawned: {program} {args}")

        with patch("asyncio.create_subprocess_exec", new=_refuse):
            yield

    @staticmethod
    def _config(tmp_path):
        return {
            "log_name": "go-librespot",
            "binary_path": str(tmp_path / "go-librespot"),
            "backup_path": str(tmp_path / "backups"),
            "service_name": "milo-spotify.service",
        }

    async def test_a_refused_restore_is_reported_with_the_wrappers_answer(
        self, service, tmp_path, caplog
    ):
        """`milo-deploy-update install-binary` refuses a destination outside its
        whitelist. Answering True would leave the caller announcing a restored
        program over a binary that was never written back."""
        config = self._config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        with patch.object(service, "_stop_service", AsyncMock(return_value=True)):
            with patch.object(service, "_run_deploy",
                              AsyncMock(return_value=(False, "destination not allowed"))):
                with caplog.at_level(logging.ERROR):
                    assert await service._rollback_binary_program(config) is False

        assert any("destination not allowed" in r.message for r in caplog.records)

    async def test_the_staging_copy_is_removed_even_when_the_restore_fails(
        self, service, tmp_path
    ):
        """The staging file is a copy of a program binary in `/tmp`; one per
        failed rollback accumulates there for as long as the box stays up."""
        config = self._config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")
        staged = []

        async def _deploy(verb, src, dst):
            staged.append(Path(src))
            return False, "refused"

        with patch.object(service, "_stop_service", AsyncMock(return_value=True)):
            with patch.object(service, "_run_deploy", _deploy):
                await service._rollback_binary_program(config)

        assert staged and not staged[0].exists()

    async def test_a_rollback_that_raises_is_a_failure_not_an_escape(
        self, service, tmp_path, caplog
    ):
        """It is awaited from the failure branch of an update; an exception here
        would replace the original failure in the traceback with its own."""
        config = self._config(tmp_path)
        (tmp_path / "backups").mkdir()
        (tmp_path / "backups" / "go-librespot.backup").write_text("old binary")

        with patch.object(service, "_stop_service",
                          AsyncMock(side_effect=RuntimeError("dbus is gone"))):
            with caplog.at_level(logging.ERROR):
                assert await service._rollback_binary_program(config) is False

        assert any("rollback failed" in r.message for r in caplog.records)

    async def test_a_missing_binary_after_an_update_is_not_a_success(
        self, service, tmp_path
    ):
        """The verify step is what turns an install that wrote nothing into a
        rollback. Without it the update reports success over a source that will
        not start on the next boot."""
        config = self._config(tmp_path)

        result = await service._verify_binary_program(config, expect_service_active=True)

        assert result["success"] is False
        assert "not found" in result["error"]

    async def test_a_binary_in_place_with_a_dead_service_is_not_a_success_either(
        self, service, tmp_path
    ):
        """`install-binary` can land while the unit refuses to come back — a new
        binary the previous config no longer starts."""
        config = self._config(tmp_path)
        Path(config["binary_path"]).write_text("new binary")

        with patch.object(service, "_is_service_active", AsyncMock(return_value=False)):
            result = await service._verify_binary_program(config, expect_service_active=True)

        assert result["success"] is False
        assert "not running" in result["error"]

    async def test_a_verification_that_raises_is_reported_as_a_failure(
        self, service, tmp_path
    ):
        """Fail closed: an unverifiable update is not a verified one, and the
        caller rolls back on this answer."""
        config = self._config(tmp_path)
        Path(config["binary_path"]).write_text("new binary")

        with patch.object(service, "_is_service_active",
                          AsyncMock(side_effect=RuntimeError("dbus is gone"))):
            result = await service._verify_binary_program(config, expect_service_active=True)

        assert result["success"] is False
        assert "Verification failed" in result["error"]
