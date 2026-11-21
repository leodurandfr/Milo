"""A persisted file that cannot be read is set aside, and its owner starts from its defaults.

The owner's decision this pins: the unit always starts. Before, a version drift
raised `SchemaVersionMismatch` and `initialize_services` answered it with
`SystemExit(1)`, which systemd looped until someone deleted the file over SSH —
and the update guard read that loop as a release that does not hold, so a
release that bumped a schema was rolled back on every unit. A missing top-level
key raised a `RuntimeError` that left the source dead until a `rm`, and a
`profiles.json` the loader could not read kept Spotify from ever starting.

Each case below is run against every owner of a versioned file, through the
owner's own boot entry point: the boot must not raise, the bytes must be kept
exactly under `<name>.<stamp>.bad`, one ERROR — and only one — must say so, and
what the owner then holds must be what a fresh install holds. The owner list is
held equal to `VERSIONED_DATA_FILES`, so a versioned file added later cannot
skip these cases.
"""
import asyncio
import contextlib
import copy
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional
from unittest.mock import AsyncMock, Mock

import pytest

import backend.core.equalizer.service as equalizer_service_module
import backend.core.push.token_registry as push_tokens_module
import backend.sources.music_library.data as music_library_data_module
import backend.sources.podcast.data as podcast_data_module
import backend.sources.radio.data as radio_data_module
import backend.sources.spotify.history as spotify_history_module
import backend.sources.spotify.profiles as spotify_profiles_module
from backend.core.equalizer.service import CamillaDSPService
from backend.core.push.models import PushTokenKind
from backend.core.push.token_registry import PushTokenRegistry
from backend.core.settings import SettingsService
from backend.core.updates.data_snapshot import VERSIONED_DATA_FILES
from backend.core.volume.state import VolumeStateStore
from backend.hardware.service import HardwareService
from backend.shared.persistence import SET_ASIDE_SUFFIX
from backend.sources.music_library.data import MusicLibraryDataService
from backend.sources.podcast.data import PodcastDataService
from backend.sources.radio.data import StationDataService
from backend.sources.spotify.history import SpotifyHistory
from backend.sources.spotify.profiles import SpotifyProfiles


@dataclass
class Owner:
    """One owner of a versioned file, as its boot meets it.

    `build` points a new instance at `tmp_path`; `boot` is what
    `initialize_services` runs; `state` is what the owner then answers through
    its public surface — compared with a fresh install's, never with a value
    written here. `sample` is a valid file at the right version holding
    something a fresh install does not. `required` is the owner's own
    `REQUIRED_TOP_LEVEL_KEYS`, read from its module: a key added there gets
    its missing-key case here, and a sample that lacks it fails the control.
    """

    cls: type
    build: Callable[[Path], Any]
    file: Callable[[Any], Path]
    boot: Callable[[Any], Awaitable[None]]
    state: Callable[[Any], Awaitable[Any]]
    sample: Dict[str, Any]
    required: tuple = ()
    # Content that parses at the right version but holds a value the owner
    # cannot take: what only the owner can see.
    unusable: Optional[Dict[str, Any]] = None


async def _initialize(owner) -> None:
    await owner.initialize()


def _settings(tmp_path):
    svc = SettingsService()
    svc.settings_file = str(tmp_path / "settings.json")
    return svc


def _settings_sample():
    sample = copy.deepcopy(SettingsService().defaults)
    sample["language"] = "french"
    sample["volume"]["startup_volume_db"] = -31.0
    return {"schema_version": SettingsService.SCHEMA_VERSION, **sample}


async def _settings_boot(svc):
    """The boot's two reads, in its order: STEP 3b's sync one, which the env
    files are derived from, then STEP 4's `initialize`."""
    svc.get_setting_sync("routing.multiroom_enabled")
    await svc.initialize()


async def _settings_state(svc):
    return await svc.get_all_settings()


def _hardware(tmp_path):
    svc = HardwareService()
    svc.hardware_file = tmp_path / "hardware.json"
    return svc


async def _hardware_state(svc):
    return (svc.get_screen_type(), svc.get_audio_id(), svc.get_rotary_enabled())


def _push(tmp_path):
    registry = PushTokenRegistry()
    registry.tokens_file = tmp_path / "push_tokens.json"
    return registry


async def _push_state(registry):
    return sorted(t.token for kind in PushTokenKind for t in registry.tokens_for(kind))


_TOKEN_RECORD = {
    "kind": "widget", "environment": "sandbox", "device_id": "phone-1",
    "session_id": None, "registered_at": 1_700_000_000.0, "last_push_at": None,
}


def _refuse_the_live_daemon(*args, **kwargs):
    raise AssertionError("a test built the real CamillaDspClient")


def _equalizer(tmp_path):
    settings = Mock()
    settings.get_setting = AsyncMock(return_value=None)
    svc = CamillaDSPService(settings_service=settings)
    svc.STORAGE_PATH = tmp_path / "equalizer.json"
    return svc


async def _equalizer_boot(svc):
    """The boot's entry point, minus the connection loop it spawns: cancelled
    before it first runs, so nothing here reaches a daemon."""
    await svc.initialize()
    svc._reconnect_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await svc._reconnect_task


async def _equalizer_state(svc):
    return svc.get_equalizer_settings()


_EQ_SAMPLE = {
    "schema_version": CamillaDSPService.SCHEMA_VERSION,
    "active_preset": "rock",
    "custom_gains": [2.0] * 10,
    "mono": True,
}


def _radio(tmp_path):
    svc = StationDataService()
    svc._data_file = tmp_path / "radio_data.json"
    return svc


async def _radio_state(svc):
    return svc.favorite_ids


def _podcast(tmp_path):
    svc = PodcastDataService()
    svc._data_file = tmp_path / "podcast_data.json"
    return svc


def _music_library(tmp_path):
    svc = MusicLibraryDataService()
    svc._data_file = tmp_path / "music_library_data.json"
    return svc


async def _load_data(svc):
    return await svc.load_data()


async def _profiles_state(profiles):
    return profiles.list()


async def _history_state(history):
    return history.tracks("alice")


OWNERS = [
    Owner(
        SettingsService, _settings, lambda s: Path(s.settings_file), _settings_boot,
        _settings_state, _settings_sample(),
        unusable={**_settings_sample(), "volume": None},
    ),
    Owner(
        HardwareService, _hardware, lambda s: s.hardware_file, _initialize, _hardware_state,
        {"schema_version": HardwareService.SCHEMA_VERSION,
         "audio": {"id": "hifiberry_amp2"}, "screen": {"type": "waveshare_8_dsi"},
         "rotary_encoder": {"enabled": False}},
    ),
    Owner(
        PushTokenRegistry, _push, lambda r: r.tokens_file, _initialize, _push_state,
        {"schema_version": PushTokenRegistry.SCHEMA_VERSION, "tokens": {"tok-w": _TOKEN_RECORD}},
        required=push_tokens_module.REQUIRED_TOP_LEVEL_KEYS,
        unusable={"schema_version": PushTokenRegistry.SCHEMA_VERSION,
                  "tokens": {"tok-w": {k: v for k, v in _TOKEN_RECORD.items()
                                       if k != "registered_at"}}},
    ),
    Owner(
        CamillaDSPService, _equalizer, lambda s: s.STORAGE_PATH, _equalizer_boot,
        _equalizer_state, _EQ_SAMPLE,
        unusable={**_EQ_SAMPLE, "custom_gains": [None] * 10},
    ),
    Owner(
        StationDataService, _radio, lambda s: s._data_file, _initialize, _radio_state,
        {"schema_version": StationDataService.SCHEMA_VERSION, "favorites": ["s1"],
         "modified_metadata": {}, "manual_stations": {}, "favorites_cache": {}},
        required=radio_data_module.REQUIRED_TOP_LEVEL_KEYS,
    ),
    Owner(
        PodcastDataService, _podcast, lambda s: s._data_file, _initialize, _load_data,
        {"schema_version": PodcastDataService.SCHEMA_VERSION,
         "subscriptions": [{"itunes_id": "1"}], "playback_progress": {}},
        required=podcast_data_module.REQUIRED_TOP_LEVEL_KEYS,
    ),
    Owner(
        MusicLibraryDataService, _music_library, lambda s: s._data_file, _initialize,
        _load_data,
        {"schema_version": MusicLibraryDataService.SCHEMA_VERSION,
         "shares": [{"id": "nas"}], "known_usb": {}, "playlist_storages": {}},
        required=music_library_data_module.REQUIRED_TOP_LEVEL_KEYS,
    ),
    Owner(
        SpotifyProfiles, lambda tmp: SpotifyProfiles(tmp / "spotify" / "profiles.json"),
        lambda p: p._file, _initialize, _profiles_state,
        {"schema_version": SpotifyProfiles.SCHEMA_VERSION, "profiles": {"alice": {
            "spotify_name": "Alice", "avatar_url": None, "added_at": 1.0,
            "signed_in_at": 2.0, "credentials": "blob"}}},
        required=spotify_profiles_module.REQUIRED_TOP_LEVEL_KEYS,
    ),
    Owner(
        SpotifyHistory, lambda tmp: SpotifyHistory(tmp / "spotify" / "history.json"),
        lambda h: h._file, _initialize, _history_state,
        {"schema_version": SpotifyHistory.SCHEMA_VERSION,
         "accounts": {"alice": [{"uri": "spotify:track:1"}]}},
        required=spotify_history_module.REQUIRED_TOP_LEVEL_KEYS,
    ),
]


def _cases():
    """(owner, case, bytes) for every owner: what a card or a release can leave."""
    for owner in OWNERS:
        valid = json.dumps(owner.sample, indent=2)
        version = owner.sample["schema_version"]
        yield owner, "truncated", valid[: len(valid) // 2].encode()
        yield owner, "empty", b""
        yield owner, "not_utf8", b"\xff\xfe" + valid.encode("utf-16-le")
        yield owner, "not_an_object", json.dumps([owner.sample]).encode()
        yield owner, "too_deep", b"[" * 200_000  # json.loads raises RecursionError
        yield owner, "newer_version", json.dumps({**owner.sample, "schema_version": version + 1}).encode()
        yield owner, "no_version", json.dumps(
            {k: v for k, v in owner.sample.items() if k != "schema_version"}).encode()
        for key in owner.required:
            yield owner, f"no_{key}", json.dumps(
                {k: v for k, v in owner.sample.items() if k != key}).encode()
        if owner.unusable is not None:
            yield owner, "unusable_value", json.dumps(owner.unusable).encode()


CASES = list(_cases())


def _set_aside(path: Path):
    return sorted(path.parent.glob(f"{path.name}.*{SET_ASIDE_SUFFIX}"))


@pytest.fixture(autouse=True)
def no_live_daemon(monkeypatch):
    monkeypatch.setattr(equalizer_service_module, "CamillaDspClient", _refuse_the_live_daemon)


def test_every_versioned_file_is_covered():
    """`VERSIONED_DATA_FILES` is held equal to the classes declaring a
    `SCHEMA_VERSION` (test_update_data_snapshot.py), so a new owner lands in
    it — and must land here too, or its unreadable file is untested."""
    assert len(OWNERS) >= 9
    covered = {f"{o.cls.__module__}.{o.cls.__qualname__}" for o in OWNERS}
    assert covered == set(VERSIONED_DATA_FILES)


@pytest.mark.parametrize("owner", OWNERS, ids=lambda o: o.cls.__name__)
async def test_the_sample_is_read_and_is_not_a_fresh_install(owner, tmp_path):
    """The control the cases below stand on: a valid file is read as it is,
    and what it holds is visibly not the defaults — otherwise "back to the
    defaults" would pass on an owner that read nothing at all."""
    fresh = owner.build(tmp_path / "fresh")
    await owner.boot(fresh)

    loaded = owner.build(tmp_path / "loaded")
    path = owner.file(loaded)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(owner.sample))
    await owner.boot(loaded)

    assert await owner.state(loaded) != await owner.state(fresh)
    assert _set_aside(path) == []


@pytest.mark.parametrize(
    "owner, case, content", CASES,
    ids=[f"{o.cls.__name__}-{case}" for o, case, _ in CASES],
)
async def test_an_unreadable_file_is_set_aside_and_its_owner_starts_from_its_defaults(
    owner, case, content, tmp_path, caplog
):
    fresh = owner.build(tmp_path / "fresh")
    await owner.boot(fresh)

    unit = owner.build(tmp_path / "unit")
    path = owner.file(unit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)

    with caplog.at_level(logging.WARNING):
        await owner.boot(unit)  # never raises: the unit always starts

    kept = _set_aside(path)
    assert len(kept) == 1, "the bytes must be kept, once"
    assert kept[0].read_bytes() == content
    assert await owner.state(unit) == await owner.state(fresh)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1, [r.getMessage() for r in errors]
    assert errors[0].name == "backend.shared.persistence"
    assert str(path) in errors[0].getMessage() and kept[0].name in errors[0].getMessage()


@pytest.mark.parametrize("owner", OWNERS, ids=lambda o: o.cls.__name__)
async def test_the_next_boot_meets_nothing_to_set_aside(owner, tmp_path, caplog):
    """Renamed, not copied: a file met again at every boot would be reported —
    and kept — at every boot. The next one finds the owner's own fresh file, or
    none, and says nothing."""
    unit = owner.build(tmp_path)
    path = owner.file(unit)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"schema_version": ')
    await owner.boot(unit)

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        await owner.boot(owner.build(tmp_path))

    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert len(_set_aside(path)) == 1


async def test_a_private_file_set_aside_stays_private(tmp_path):
    """profiles.json holds go-librespot's reusable credentials, 0600. A copy
    made with the default mode would leave them readable by every local user;
    the rename keeps the very inode, mode included."""
    profiles = SpotifyProfiles(tmp_path / "profiles.json")
    path = tmp_path / "profiles.json"
    path.write_text('{"schema_version": 1, "profiles": {"alice": {"credentials": "blob"}}}')
    path.chmod(0o600)

    await profiles.initialize()

    (kept,) = _set_aside(path)
    assert kept.stat().st_mode & 0o777 == 0o600


class TestLastVolume:
    """`last_volume.json` is outside the versioned API but just as durable: the
    level and the mute of every room. A file it could not read was logged and
    the next persist rewrote it, so a damaged one cost every room's level with
    nothing kept and nothing on screen."""

    @pytest.fixture
    def path(self, tmp_path, monkeypatch):
        path = tmp_path / "last_volume.json"
        monkeypatch.setattr(VolumeStateStore, "STORAGE_PATH", path)
        return path

    def _store(self):
        return VolumeStateStore()

    VALID = {
        "local_mac_id": "aa:aa:aa:aa:aa:01",
        "clients": {"aa:aa:aa:aa:aa:01": {"volume_db": -31.0, "mute": True}},
    }

    async def test_the_sample_is_read(self, path):
        path.write_text(json.dumps(self.VALID))
        store = self._store()

        await store.initialize()

        assert store.has_client("aa:aa:aa:aa:aa:01")
        assert _set_aside(path) == []

    @pytest.mark.parametrize("content", [
        json.dumps(VALID)[:40],
        "",
        json.dumps([VALID]),
        json.dumps({"clients": VALID["clients"]}),
        json.dumps({**VALID, "clients": {"aa:aa:aa:aa:aa:01": {"volume_db": -31.0}}}),
        json.dumps({**VALID, "clients": {"aa:aa:aa:aa:aa:01": {"volume_db": None, "mute": False}}}),
        json.dumps({**VALID, "clients": {"aa:aa:aa:aa:aa:01": {"volume_db": -31.0, "mute": "yes"}}}),
        json.dumps({**VALID, "clients": {"aa:aa:aa:aa:aa:01": {"volume_db": "-31", "mute": False}}}),
        json.dumps({**VALID, "clients": {"aa:aa:aa:aa:aa:01": {"volume_db": True, "mute": False}}}),
        # NaN would clamp to the upper limit: a room sent at its loudest.
        json.dumps({**VALID, "clients": {"aa:aa:aa:aa:aa:01": {"volume_db": float("nan"), "mute": False}}}),
        '{"local_mac_id": null, "clients": {"aa:aa:aa:aa:aa:01": {"volume_db": 1e400, "mute": false}}}',
        # An integer no float can hold: float() raises OverflowError.
        '{"local_mac_id": null, "clients": {"aa:aa:aa:aa:aa:01": {"volume_db": -' + "9" * 400
        + ', "mute": false}}}',
        "[" * 200_000,  # json.loads raises RecursionError
    ], ids=["truncated", "empty", "not_an_object", "no_local_mac_id", "no_mute",
            "null_level", "mute_not_a_bool", "level_a_string", "level_a_bool", "level_nan",
            "level_infinite", "level_overflows", "too_deep"])
    async def test_an_unreadable_file_is_set_aside_not_overwritten(self, path, content, caplog):
        path.write_text(content)
        store = self._store()

        with caplog.at_level(logging.WARNING):
            await store.initialize()
        # A level set now persists over the path: the bytes must already be elsewhere.
        await store.set_client_volume("bb:bb:bb:bb:bb:02", -40.0)
        await store.cleanup()

        (kept,) = _set_aside(path)
        assert kept.read_text() == content
        assert not store.has_client("aa:aa:aa:aa:aa:01"), "nothing read from it is kept"
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert len(errors) == 1 and errors[0].name == "backend.shared.persistence"
        assert str(path) in errors[0].getMessage()

    async def test_a_file_that_cannot_be_moved_aside_still_lets_the_store_start(
        self, path, monkeypatch
    ):
        """The volume service is critical at boot: a store whose load raised
        would stop the unit, which is the one thing a damaged file must not do."""
        path.write_text("{")

        def refused(src, dst):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr("backend.shared.persistence.os.rename", refused)
        store = self._store()

        await store.initialize()

        assert not store.has_client("aa:aa:aa:aa:aa:01")


def _refuse_the_rename(monkeypatch):
    def refused(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("backend.shared.persistence.os.rename", refused)


def _refuse_the_read(path: Path):
    """A file the backend cannot open but may replace: what `sudo nano` leaves
    (root, 0600) — bytes that are fine, in a directory `milo` writes."""
    path.chmod(0o000)


async def _volume_takes_a_change(tmp_path, monkeypatch):
    monkeypatch.setattr(VolumeStateStore, "STORAGE_PATH", tmp_path / "last_volume.json")
    store = VolumeStateStore()
    await store.initialize()
    await store.set_client_volume("bb:bb:bb:bb:bb:02", -40.0)
    await store.cleanup()  # flushes the debounced persist


async def _equalizer_takes_a_change(tmp_path, monkeypatch):
    svc = _equalizer(tmp_path)
    await _equalizer_boot(svc)
    await svc.persist_state()


async def _hardware_takes_a_change(tmp_path, monkeypatch):
    """The DAC-mode toggle, after a boot whose hardware init failed (logged by
    `init_async`, which goes on) and whose getters answered the defaults."""
    svc = _hardware(tmp_path)
    with contextlib.suppress(OSError):
        await svc.initialize()
    svc.get_volume_control()
    with contextlib.suppress(OSError):  # refused, and said so to the route
        await svc.set_volume_control(False)


_WRITERS = {
    "last_volume.json": (_volume_takes_a_change, json.dumps(TestLastVolume.VALID)),
    "equalizer.json": (_equalizer_takes_a_change, json.dumps(_EQ_SAMPLE)),
    "hardware.json": (_hardware_takes_a_change, json.dumps(OWNERS[1].sample)),
}


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a 0000 file")
@pytest.mark.parametrize("name", _WRITERS)
@pytest.mark.parametrize("cause", ["read_refused", "rename_refused"])
async def test_a_file_the_boot_could_not_take_in_is_never_written_over(
    name, cause, tmp_path, monkeypatch
):
    """The owners that persist what they hold in memory — every level change,
    every EQ gesture, the DAC-mode toggle — and that keep running when their
    load fails. The bytes were neither read nor moved aside, so a persist of
    this run would replace them with no copy: the very loss setting a file
    aside exists to prevent. They stay as they were, for the next start to
    read again."""
    takes_a_change, valid = _WRITERS[name]
    path = tmp_path / name
    if cause == "read_refused":
        path.write_text(valid)
        _refuse_the_read(path)
    else:
        path.write_text("{")
        _refuse_the_rename(monkeypatch)
    before = (path.stat().st_ino, path.stat().st_mtime_ns)

    await takes_a_change(tmp_path, monkeypatch)

    assert (path.stat().st_ino, path.stat().st_mtime_ns) == before
    path.chmod(0o600)
    assert path.read_text() == (valid if cause == "read_refused" else "{")
