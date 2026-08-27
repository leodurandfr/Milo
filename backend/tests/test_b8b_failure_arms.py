"""The residue of B8b: failure arms that decide whether a fault is visible.

What is left after the lifecycles, the graph writes and the boot wiring is a set
of small `except` branches spread over the volume path, the DSP client, the
satellite proxy and the WebSocket server. They are grouped here because they
share one property rather than one module: each is the difference between a
failure the operator can see and one that reads as success.

The two that matter most:

* `_apply_volume_to_hardware`'s LOCAL arm. A remote client that refuses degrades
  gracefully — it re-syncs on reconnect — but the local one failing means the
  server's own audio may be silent, and it is the only refusal reported at
  error and answered False.
* `broadcast_volume_state` re-raises after logging, alone among the volume
  methods. It is spawned as a background task, and its error callback is what
  turns a broadcast failure into something visible; swallowed, the UI simply
  stops receiving volume updates.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from backend.config.constants import DEFAULT_VOLUME_DB
from backend.core.equalizer.service import CamillaDSPService
from backend.core.volume import VolumeService, VolumeStateStore


@pytest.fixture
def camilladsp():
    dsp = Mock()
    dsp.set_volume = AsyncMock(return_value=True)
    dsp.set_mute = AsyncMock(return_value=True)
    dsp.get_volume = AsyncMock(return_value={"main": -30.0, "mute": False})
    dsp.is_volume_control_available = Mock(return_value=True)
    dsp.wait_for_connection = AsyncMock(return_value=True)
    return dsp


@pytest.fixture
def volume(camilladsp, tmp_path, monkeypatch):
    monkeypatch.setattr(VolumeStateStore, "STORAGE_PATH", tmp_path / "last_volume.json")
    state_machine = Mock()
    state_machine.broadcast = AsyncMock()
    settings = Mock()
    settings.get_setting = AsyncMock(return_value=None)
    settings.set_setting = AsyncMock()
    settings.invalidate_cache = Mock()
    return VolumeService(
        state_machine=state_machine,
        snapcast_service=Mock(),
        settings_service=settings,
        camilladsp_service=camilladsp,
        equalizer_client_proxy_service=Mock(),
    )


class TestVolumeFanOutVerdict:
    """Which refusal is fatal, and which one degrades."""

    @pytest.fixture
    def multiroom(self, volume):
        volume._routing_service = Mock()
        volume._routing_service.get_state = Mock(return_value={"multiroom_enabled": True})
        volume._equalizer_controller = Mock()
        volume._equalizer_controller.apply_volumes_parallel = AsyncMock(return_value={})
        volume.broadcast_volume_state = AsyncMock()
        return volume

    async def test_the_local_client_refusing_is_the_one_fatal_failure(
        self, multiroom, caplog
    ):
        """A satellite that refuses re-syncs on reconnect; the server does not.

        Reported as success, the room the appliance itself drives is silent (or
        stuck at the old level) while the UI shows the new one, and nothing
        anywhere says so.
        """
        multiroom._state_store.ensure_local_client("local:mac", -30.0)
        await multiroom._state_store.register_client("sat:mac", volume_db=-30.0)
        multiroom._equalizer_controller.apply_volumes_parallel = AsyncMock(
            return_value={"local:mac": False, "sat:mac": True}
        )
        multiroom._refused = Mock(side_effect=lambda cid, applied, what: not applied)

        with caplog.at_level(logging.ERROR):
            result = await multiroom._apply_volume_to_hardware(
                -25.0, {"local:mac": -25.0, "sat:mac": -25.0}, ["local:mac", "sat:mac"]
            )

        assert result is False
        assert "LOCAL server volume update failed" in caplog.text

    async def test_a_remote_refusal_alone_degrades_gracefully(self, multiroom, caplog):
        """The control. A speaker that was away comes back through the
        reconnection sync, so failing the whole gesture would make every volume
        change in a house with one sleeping speaker report an error.
        """
        multiroom._state_store.ensure_local_client("local:mac", -30.0)
        await multiroom._state_store.register_client("sat:mac", volume_db=-30.0)
        multiroom._equalizer_controller.apply_volumes_parallel = AsyncMock(
            return_value={"local:mac": True, "sat:mac": False}
        )
        multiroom._refused = Mock(side_effect=lambda cid, applied, what: not applied)

        with caplog.at_level(logging.WARNING):
            result = await multiroom._apply_volume_to_hardware(
                -25.0, {"local:mac": -25.0, "sat:mac": -25.0}, ["local:mac", "sat:mac"]
            )

        assert result is True
        assert "Multiroom volume update failed for 1/2" in caplog.text

    async def test_an_empty_update_set_is_a_success_without_a_fan_out(self, multiroom):
        """Every client is at the target already, or every one is a DAC that owns
        its own level. Answered False, the slider would report a failure for a
        change that had nothing to do."""
        assert await multiroom._apply_volume_to_hardware(-25.0, {}, []) is True

        multiroom._equalizer_controller.apply_volumes_parallel.assert_not_awaited()

    async def test_a_multiroom_shift_with_nothing_online_computes_nothing(
        self, multiroom
    ):
        """The shift is relative, measured against the global average. With no
        client reachable that average is a fabricated default, and applying a
        delta to it would move every speaker to a level derived from nothing.
        """
        assert await multiroom._compute_multiroom_updates(-25.0, []) == {}


class TestVolumeGuardsFailOpen:
    """Reads that must answer rather than raise."""

    async def test_an_unreadable_routing_state_reads_as_direct_mode(
        self, volume, caplog
    ):
        """`_is_multiroom_enabled` is consulted on every volume gesture.

        Raising would 500 the slider; answering True would fan a level out to a
        registry that may hold stale clients. Direct mode is the safe default —
        it touches only the local DSP.
        """
        volume._routing_service = Mock()
        volume._routing_service.get_state = Mock(side_effect=RuntimeError("no state"))

        with caplog.at_level(logging.WARNING):
            assert volume._is_multiroom_enabled() is False

        assert "Failed to check multiroom state" in caplog.text

    async def test_no_camilladsp_service_reads_as_unavailable(self, volume):
        """Guards the direct-mode apply. Answering True would call `set_volume`
        on None and raise inside a volume gesture."""
        volume._camilladsp_service = None

        assert volume._is_equalizer_available() is False

    async def test_an_unresolvable_local_mac_degrades_with_a_warning(
        self, volume, monkeypatch, caplog
    ):
        """A unit with neither eth0 nor wlan0 up at boot. Raising here would take
        the whole volume init down; silence would leave the operator with a
        direct-mode unit whose level is never persisted and no clue why.
        """
        monkeypatch.setattr("backend.core.volume.service.get_local_mac", lambda: None)

        with caplog.at_level(logging.WARNING):
            volume._seed_local_client_if_needed()

        assert "Could not resolve local MAC" in caplog.text
        assert volume._state_store.local_mac_id is None

    async def test_an_unknown_client_reads_as_the_default_level(self, volume):
        """`GET /api/volume/client/{id}` for a client the store has never seen.

        None would render as an empty slider; the default renders a slider the
        user can move, which then registers the client.
        """
        assert await volume.get_client_volume("never:seen") == {
            "main": DEFAULT_VOLUME_DB, "mute": False,
        }

    async def test_a_failed_broadcast_is_logged_and_re_raised(self, volume, caplog):
        """The one volume method that re-raises after logging.

        It is spawned as a background task, and the task's error callback is what
        makes a broken broadcast visible. Swallowed, the UI just stops receiving
        volume updates — with the levels themselves still being applied, so
        nothing looks broken until the user reloads.
        """
        volume._state_store.get_complete_state = AsyncMock(
            side_effect=RuntimeError("store unreadable")
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError, match="store unreadable"):
                await volume.broadcast_volume_state()

        assert "Error broadcasting volume state" in caplog.text

    async def test_an_unmute_that_fails_on_a_mode_switch_is_survivable(
        self, volume, camilladsp, caplog
    ):
        """A mode switch is not an adjustment: nobody asked for a new level, so a
        daemon that refuses the unmute must not fail the switch itself. The
        reconnect restore re-applies both."""
        camilladsp.set_mute = AsyncMock(side_effect=RuntimeError("daemon down"))
        volume.broadcast_volume_state = AsyncMock()

        with caplog.at_level(logging.WARNING):
            await volume.update_volume_mode(multiroom_enabled=False)

        assert "Failed to unmute CamillaDSP" in caplog.text


class TestVolumeStoreGuards:
    """`VolumeStateStore` refusing what it cannot do, out loud."""

    @pytest.fixture
    def store(self, tmp_path, monkeypatch):
        monkeypatch.setattr(VolumeStateStore, "STORAGE_PATH", tmp_path / "last_volume.json")
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return VolumeStateStore(settings_service=settings)

    async def test_an_unknown_mode_is_refused_rather_than_stored(self, store, caplog):
        """The mode decides whether a level is applied locally or fanned out.

        Stored blindly, an unrecognised value makes every later comparison fall
        through and the store silently behaves as neither mode.
        """
        with caplog.at_level(logging.WARNING):
            await store.set_mode("quadraphonic")

        assert "Invalid volume mode" in caplog.text

    async def test_muting_a_client_the_store_does_not_know_is_reported(
        self, store, caplog
    ):
        """Unlike `set_client_volume`, mute does NOT auto-register.

        Silent, a mute sent to a client that has not registered yet is dropped
        and the speaker keeps playing with the UI showing it muted.
        """
        with caplog.at_level(logging.WARNING):
            await store.set_client_mute("never:seen", True)

        assert "Cannot mute unknown client" in caplog.text

    async def test_marking_an_unknown_client_available_is_reported(self, store, caplog):
        """Availability arrives from the registry bus, so an unknown id here
        means the two views of the fleet have diverged."""
        with caplog.at_level(logging.WARNING):
            await store.set_client_availability("never:seen", True)

        assert "Cannot set availability for unknown client" in caplog.text

    async def test_seeding_a_local_client_twice_keeps_the_first_level(self, store):
        """`_seed_local_client_if_needed` runs at every boot and the persisted
        state may already hold the real level. Overwriting it with the startup
        default is the restore silently undoing itself."""
        store.ensure_local_client("aa:bb", -38.0)
        store.ensure_local_client("aa:bb", -45.0)

        assert store.get_client_volume("aa:bb") == -38.0

    async def test_a_persist_that_fails_does_not_propagate(
        self, store, monkeypatch, caplog
    ):
        """Every caller is a volume gesture, most of them debounced background
        writes. A raise here would surface as a failed volume change over a
        durability problem the user cannot act on."""
        async def _boom(*args, **kwargs):
            raise OSError("read-only filesystem")

        monkeypatch.setattr(store, "_write_state", _boom, raising=False)
        monkeypatch.setattr(
            "backend.core.volume.state.aiofiles.open",
            Mock(side_effect=OSError("read-only filesystem")),
        )

        with caplog.at_level(logging.ERROR):
            await store._persist_state_async()

        assert "Error persisting volume state" in caplog.text


class TestCamillaDspReadGuards:
    """Reads on the DSP client that must answer the cache rather than raise."""

    @pytest.fixture
    def service(self, tmp_path, monkeypatch):
        monkeypatch.setattr(CamillaDSPService, "STORAGE_PATH", tmp_path / "equalizer.json")
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        svc = CamillaDSPService(settings_service=settings)
        svc.state_machine = Mock()
        svc.state_machine.broadcast = AsyncMock()
        return svc

    async def test_a_volume_read_that_fails_answers_the_last_known_value(
        self, service
    ):
        """The boot push reads this to seed a client with nothing persisted.

        Raising would abort that push for every client at once; answering a
        default would hand the new speaker a level nobody chose. The cache is
        the last value Milō itself applied, which is the right answer.
        """
        client = MagicMock()
        client.volume.main_volume.side_effect = OSError("socket gone")
        service._client = client
        service._connected = True
        service._volume = {"main": -22.0, "mute": False}

        assert await service.get_volume() == {"main": -22.0, "mute": False}

    async def test_a_disconnected_service_answers_its_cached_volume(self, service):
        service._volume = {"main": -33.0, "mute": True}

        assert await service.get_volume() == {"main": -33.0, "mute": True}

    async def test_levels_are_read_from_both_meters_at_once(self, service):
        """Input and output are two round-trips; gathered they are one wait.

        `LevelsMonitor` calls this at 10 Hz, so serialising them doubles the
        DSP traffic of every open EQ view.
        """
        client = MagicMock()
        client.levels.capture_peak.return_value = [-30.0, -31.0]
        client.levels.playback_peak.return_value = [-25.0, -26.0]
        service._client = client
        service._connected = True

        assert await service.get_levels() == {
            "available": True,
            "input_peak": [-30.0, -31.0],
            "output_peak": [-25.0, -26.0],
        }

    async def test_a_disconnected_service_reports_levels_unavailable(self, service):
        """The meters fall to the floor rather than freezing — a held meter over
        a dead daemon reads as signal present."""
        assert await service.get_levels() == {"available": False}

    async def test_a_batched_apply_on_a_dead_daemon_is_refused(self, service, caplog):
        """`apply_settings` is the 13-round-trips-in-one path the per-client EQ
        uses. Answered True while disconnected, the record is marked applied and
        the reconnect restore has no reason to re-push it."""
        with caplog.at_level(logging.WARNING):
            assert await service.apply_settings(Mock(), persist=False) is False

        assert "Cannot apply settings: not connected" in caplog.text

    async def test_saved_gains_shorter_than_ten_bands_are_refused(self, service):
        """Ten bands, and `_config_apply_*` indexes them positionally. A short
        list from a hand-edited file would raise inside the config write and the
        whole EQ push would fail."""
        service._custom_gains = [0.0] * 10

        service.set_custom_gains([1.0, 2.0])

        assert service._custom_gains == [0.0] * 10

    async def test_saved_gains_are_truncated_to_ten(self, service):
        service.set_custom_gains([float(i) for i in range(15)])

        assert service._custom_gains == [float(i) for i in range(10)]

    async def test_an_empty_custom_preset_reads_as_the_builtin_default(self, service):
        """The custom preset is offered in the UI before it is ever saved."""
        from backend.core.equalizer.presets import DEFAULT_CUSTOM_GAINS

        service._custom_gains = []

        assert await service.get_custom_gains() == list(DEFAULT_CUSTOM_GAINS)

    async def test_the_effects_flag_is_readable_and_writable_as_a_bool(self, service):
        """`AudioStateMachine.broadcast()` reads this when aggregating
        `full_state`, and `AudioRoutingService` owns the persistence. A truthy
        non-bool would reach the wire as-is and a consumer comparing to True
        would disagree with one testing truthiness.
        """
        service.set_effects_enabled("yes")

        assert service.effects_enabled is True

    async def test_the_reconnect_callback_is_invoked_before_the_effects(self, service):
        """Volume first, by design: a restarted daemon is at its own gain until
        something tells it otherwise, and restoring the effects first would put
        the user's EQ on top of a level nobody chose."""
        order = []
        service._on_reconnect_callback = AsyncMock(side_effect=lambda: order.append("volume"))
        service._effects_enabled = True
        service.restore_effects = AsyncMock(side_effect=lambda: order.append("effects") or True)

        await service._restore_after_reconnect()

        assert order == ["volume", "effects"]

    async def test_a_reconnect_that_blows_up_is_contained(self, service, caplog):
        """It runs inside `_connection_loop`; an escape would be caught by the
        loop's generic arm and turned into another reconnect attempt against a
        daemon that is already connected."""
        service._on_reconnect_callback = AsyncMock(side_effect=RuntimeError("callback gone"))

        with caplog.at_level(logging.ERROR):
            await service._restore_after_reconnect()

        assert "Error restoring state after reconnect" in caplog.text

    async def test_the_saved_effects_flag_is_loaded_from_settings(self, service):
        """It lives in `settings.json` under `routing.equalizer_effects_enabled`,
        not in equalizer.json — a unit that booted with the effects off must not
        come back with them on."""
        service.settings_service.get_setting = AsyncMock(return_value=True)

        await service._load_saved_config()

        assert service._effects_enabled is True

    async def test_a_fresh_install_keeps_the_in_memory_defaults_and_says_nothing(
        self, service, caplog
    ):
        """No equalizer.json yet — the ten default bands must survive.

        The early return is inert on the cache: `load_versioned_json` answers
        `{}` for a missing file, so every `.get()` below it is None and every
        `isinstance` check rejects it. What it does buy is the log line: without
        it, a fresh install announces "Loaded equalizer.json: 10 filters" on
        every boot for a file that does not exist, which is the first thing an
        operator reads when the EQ comes up flat.
        """
        before = [dict(f) for f in service._filters]

        with caplog.at_level(logging.INFO, logger="backend.core.equalizer.service"):
            await service._load_saved_config()

        assert service._filters == before
        assert "Loaded equalizer.json" not in caplog.text


class TestWebSocketSendFailure:
    """The broadcast fan-out — one dead client must not cost the others."""

    async def test_a_slow_client_is_closed_rather_than_waited_on(self, caplog):
        """Every state change goes through this fan-out. A client that stopped
        reading would otherwise hold the broadcast for its full send timeout,
        and every viewer's UI would stutter with it. Closing it is also what
        makes the browser notice and reconnect.
        """
        from backend.ws import WebSocketManager

        manager = WebSocketManager()

        async def _slow(*args, **kwargs):
            # Bounded, and the bound is the point: the production `wait_for` is
            # the ONLY thing that ends this send, so a double that sleeps for an
            # hour turns a mutation removing that timeout into a hung suite
            # instead of a red one. Long enough to lose the race against
            # SEND_TIMEOUT below, short enough to fail fast if it is gone.
            await asyncio.sleep(2)

        slow = MagicMock()
        slow.send_text = _slow
        slow.close = AsyncMock()
        healthy = MagicMock()
        healthy.send_text = AsyncMock()
        # A set, like the real one: `broadcast_dict` removes the dead peers with
        # `-=`, which a list cannot do. A double that cannot represent what the
        # unit does to it is not a double (17th blind spot).
        manager.active_connections = {slow, healthy}

        with caplog.at_level(logging.DEBUG, logger="backend.ws.manager"):
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr("backend.ws.manager.SEND_TIMEOUT", 0.05)
                await manager.broadcast_dict({"category": "system", "type": "ping"})

        healthy.send_text.assert_awaited_once()
        slow.close.assert_awaited_once()
        assert "Slow client, closing connection" in caplog.text
