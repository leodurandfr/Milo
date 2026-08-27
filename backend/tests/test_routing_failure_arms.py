"""What a multiroom transition does after the mode has already switched, and
what the three env writers do when the disk refuses.

What breaks when these fail, in order:

* **the spinner never clears.** `_post_transition_setup_best_effort` is where
  `RoutingMultiroomReady` is broadcast — the event `App.vue` listens for to take
  the UI out of its transition state. B1 measured the whole method at 0 % and
  left it as an accepted finding; B8c is the re-take of `core/multiroom`, so it
  is closed here. Nothing else emits that event, and every step around it is
  best-effort by design: the physical mode has already switched, so a step that
  raised instead of warning would fail a transition that already happened.
* **an env file half-written is a unit that will not start.** The three
  `*.env` files are read by systemd units at `ExecStart`; `routing.env` decides
  `MILO_MODE`, so a truncated one is a source unit pointed at the wrong ALSA
  device. `_write_atomically` is what makes a failed write leave the old file
  intact, and its cleanup arm is what keeps a `.tmp` from accumulating next to it.
* **a toggle that failed must leave the stored flag where it was.** The routing
  flags are read back by the property that decides which mode the appliance is
  in; a flag advanced past a body that raised makes the next boot reconcile to a
  state the hardware never reached.

The env `PATH`s are already redirected to a tmpdir session-wide by the suite's
own conftest, which is why this file can exercise the real writers at all: this
checkout is the appliance, and `RoutingEnv.regenerate` also sets
`os.environ["MILO_MODE"]`.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from backend.core.multiroom.routing import (
    DEFAULT_ROC_CONFIG,
    AudioRoutingService,
    MacEnv,
    RoutingEnv,
    SnapclientEnv,
    _write_atomically,
)
from backend.tests.conftest import events_of


@pytest.fixture
def mock_systemd_manager():
    """Stand in for SystemdServiceManager — it shells `sudo systemctl` on this host."""
    with patch("backend.core.multiroom.routing.SystemdServiceManager") as factory:
        manager = Mock()
        manager.is_active = AsyncMock(return_value=False)
        manager.start = AsyncMock(return_value=True)
        manager.stop = AsyncMock(return_value=True)
        manager.restart = AsyncMock(return_value=True)
        factory.return_value = manager
        yield manager


@pytest.fixture
def service(mock_settings_service, mock_systemd_manager):
    svc = AudioRoutingService(
        settings_service=mock_settings_service, systemd_manager=mock_systemd_manager
    )
    svc._initial_detection_done = True
    lock = asyncio.Lock()

    @asynccontextmanager
    async def _exclusive():
        async with lock:
            yield

    state_machine = Mock()
    state_machine.exclusive_transition = _exclusive
    state_machine.broadcast = AsyncMock()
    state_machine.update_source_state = AsyncMock()
    svc.state_machine = state_machine
    mock_settings_service._storage["routing.multiroom_enabled"] = False
    return svc


class TestThePostTransitionSetup:
    """B1-8, closed. Everything that happens once the mode has already switched."""

    def _ws(self, ready=True):
        ws = MagicMock()
        ws.start_connection = AsyncMock()
        ws.stop_connection = AsyncMock()
        ws.wait_for_ready = AsyncMock(return_value=ready)
        return ws

    async def test_enabling_opens_the_socket_and_waits_for_it(self, service):
        """The wait is not decoration: `start_connection` only spawns the loop,
        and the volume sync that follows reads the registry the connect sweep
        fills. Announcing ready before the sweep would clear the spinner over a
        room list that is still empty."""
        ws = self._ws()
        service.snapcast_websocket_service = ws

        await service._post_transition_setup_best_effort(True)

        ws.start_connection.assert_awaited_once()
        ws.wait_for_ready.assert_awaited_once_with(timeout=15.0)
        ws.stop_connection.assert_not_called()

    async def test_disabling_closes_the_socket(self, service):
        """snapserver is stopped with multiroom, so a socket left open reconnects
        in a loop against a daemon that is down — 5 s to 30 s apart, forever."""
        ws = self._ws()
        service.snapcast_websocket_service = ws

        await service._post_transition_setup_best_effort(False)

        ws.stop_connection.assert_awaited_once()
        ws.start_connection.assert_not_called()

    async def test_a_socket_that_never_becomes_ready_is_a_warning_not_a_failure(
        self, service, caplog
    ):
        """The physical mode has already switched. Raising here would report a
        failed transition for an appliance that is in multiroom — and the volume
        sync heals on its own through the delayed sync and client admissions."""
        service.snapcast_websocket_service = self._ws(ready=False)

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.routing"):
            await service._post_transition_setup_best_effort(True)

        assert any("not ready after 15s" in r.message for r in caplog.records)
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_a_socket_lifecycle_that_raises_does_not_stop_the_rest(
        self, service, caplog
    ):
        """Three independent steps. A WS failure that swallowed the volume mode
        update would leave the global level denoting the old mode."""
        ws = self._ws()
        ws.start_connection = AsyncMock(side_effect=RuntimeError("no loop"))
        service.snapcast_websocket_service = ws
        volume = MagicMock()
        volume.update_volume_mode = AsyncMock()
        volume.volume_control = True
        service.volume_service = volume

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.routing"):
            await service._post_transition_setup_best_effort(True)

        assert any("WS lifecycle failed" in r.message for r in caplog.records)
        volume.update_volume_mode.assert_awaited_once_with(True)

    async def test_switching_mode_never_pushes_a_level_to_anyone(self, service):
        """Switching modes changes what the global figure denotes, not what any
        speaker plays at. A push here would move every room's level at once
        because someone enabled multiroom."""
        volume = MagicMock()
        volume.update_volume_mode = AsyncMock()
        volume.sync_all_clients_from_equalizer = AsyncMock()
        volume.volume_control = True
        service.volume_service = volume

        await service._post_transition_setup_best_effort(True)

        volume.sync_all_clients_from_equalizer.assert_not_called()

    async def test_a_dac_unit_reads_the_clients_levels_instead(self, service):
        """The one direction the ownership rule allows: with `volume_control`
        False an external amp holds the level, so the store learns it from the
        clients rather than telling them."""
        volume = MagicMock()
        volume.update_volume_mode = AsyncMock()
        volume.sync_all_clients_from_equalizer = AsyncMock()
        volume.volume_control = False
        service.volume_service = volume

        await service._post_transition_setup_best_effort(True)

        volume.sync_all_clients_from_equalizer.assert_awaited_once()

    async def test_a_volume_sync_that_raises_still_clears_the_spinner(
        self, service, caplog
    ):
        """The event is what takes the UI out of its transition state. Losing it
        to a non-fatal volume failure leaves the multiroom page spinning with the
        mode already on."""
        volume = MagicMock()
        volume.update_volume_mode = AsyncMock(side_effect=RuntimeError("no store"))
        service.volume_service = volume

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.routing"):
            await service._post_transition_setup_best_effort(True)

        assert any("Volume sync failed" in r.message for r in caplog.records)
        assert len(events_of(service.state_machine.broadcast, "routing", "multiroom_ready")) == 1

    async def test_the_ready_event_fires_on_enable_and_only_on_enable(self, service):
        """Disabling has no spinner to clear — the UI is already back on the
        direct-mode screen — and a ready event there would announce a multiroom
        that was just switched off."""
        await service._post_transition_setup_best_effort(True)
        assert len(events_of(service.state_machine.broadcast, "routing", "multiroom_ready")) == 1

        service.state_machine.broadcast.reset_mock()
        await service._post_transition_setup_best_effort(False)
        assert events_of(service.state_machine.broadcast, "routing", "multiroom_ready") == []

    async def test_a_broadcast_that_fails_is_the_last_thing_that_can_go_wrong(
        self, service, caplog
    ):
        """It is the final step, so an exception escaping would surface as a
        failed transition on an appliance that completed one."""
        service.state_machine.broadcast = AsyncMock(side_effect=RuntimeError("no ws clients"))

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.routing"):
            await service._post_transition_setup_best_effort(True)

        assert any("multiroom_ready broadcast failed" in r.message for r in caplog.records)


class TestTheTransitionBroadcasts:
    """The two events that bracket a transition, and their one guard."""

    async def test_enabling_and_disabling_announce_different_events(self, service):
        """`App.vue` keys its loading copy on which one arrived."""
        with patch.object(asyncio, "sleep", AsyncMock()):
            await service._broadcast_transition_event(True)
            await service._broadcast_transition_event(False)

        assert len(events_of(service.state_machine.broadcast, "routing", "multiroom_enabling")) == 1
        assert len(events_of(service.state_machine.broadcast, "routing", "multiroom_disabling")) == 1

    async def test_without_a_state_machine_nothing_is_announced_and_it_says_so(
        self, service, caplog
    ):
        """Reached before `_apply_transition` raises on the same condition; the
        warning is what names the missing wiring rather than the transition."""
        service.state_machine = None

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.routing"):
            await service._broadcast_transition_event(True)

        assert any("state_machine not available" in r.message for r in caplog.records)

    async def test_a_failed_transition_says_which_direction_failed(self, service):
        """`routing/multiroom_error` is read by Milo-Mac too, which is pinned to
        its presence alone — the reason is what the local UI shows."""
        await service._broadcast_error(True)
        await service._broadcast_error(False)

        reasons = [
            e.reason for e in events_of(service.state_machine.broadcast, "routing", "multiroom_error")
        ]
        assert reasons == ["enable_failed", "disable_failed"]

    async def test_an_error_broadcast_that_fails_is_swallowed_with_a_warning(
        self, service, caplog
    ):
        """It runs inside the failure path of a transition that already went
        wrong; raising here would replace the real cause in the traceback."""
        service.state_machine.broadcast = AsyncMock(side_effect=RuntimeError("no clients"))

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.routing"):
            await service._broadcast_error(True)

        assert any("multiroom_error broadcast failed" in r.message for r in caplog.records)

    async def test_without_a_state_machine_the_error_path_is_silent(self, service, caplog):
        """No line at all here, unlike the pre-transition event: this is already
        the failure path of a transition that went wrong, and a second warning
        about missing wiring buries the real cause. Falling through instead would
        log one — the attribute error on `None` lands in the catch below."""
        service.state_machine = None

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.routing"):
            await service._broadcast_error(True)

        assert caplog.records == []


class TestTheDelayedBootSync:
    """The boot-time catch-up: what re-aligns the fleet when nothing else did."""

    async def test_it_waits_for_snapserver_before_reading_anything(self, service, caplog):
        """It runs at boot while snapserver is still starting. Syncing against a
        daemon that is not up reads an empty client list and does nothing, with
        no second attempt."""
        service.snapcast_service = MagicMock()
        service.snapcast_service.wait_until_available = AsyncMock(return_value=False)
        volume = MagicMock()
        volume.sync_all_clients_from_equalizer = AsyncMock()
        service.volume_service = volume

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.routing"):
            await service._delayed_multiroom_sync()

        volume.sync_all_clients_from_equalizer.assert_not_called()
        assert any("not ready in time" in r.message for r in caplog.records)

    async def test_a_multiroom_switched_off_while_waiting_cancels_the_sync(
        self, service, mock_settings_service
    ):
        """Up to fifteen seconds pass in the wait above. Pushing to snapserver
        after the operator switched back to direct mode would drive a daemon the
        routing service has just stopped."""
        service.snapcast_service = MagicMock()
        service.snapcast_service.wait_until_available = AsyncMock(return_value=True)
        mock_settings_service._storage["routing.multiroom_enabled"] = False
        volume = MagicMock()
        volume.sync_all_clients_from_equalizer = AsyncMock()
        service.volume_service = volume

        await service._delayed_multiroom_sync()

        volume.sync_all_clients_from_equalizer.assert_not_called()

    async def test_with_multiroom_still_on_the_fleet_is_synced(
        self, service, mock_settings_service
    ):
        service.snapcast_service = MagicMock()
        service.snapcast_service.wait_until_available = AsyncMock(return_value=True)
        mock_settings_service._storage["routing.multiroom_enabled"] = True
        volume = MagicMock()
        volume.sync_all_clients_from_equalizer = AsyncMock()
        service.volume_service = volume

        await service._delayed_multiroom_sync()

        volume.sync_all_clients_from_equalizer.assert_awaited_once()

    async def test_no_volume_service_is_reported_not_passed_over(
        self, service, mock_settings_service, caplog
    ):
        """The fleet then boots un-synced with nothing to say why."""
        service.snapcast_service = MagicMock()
        service.snapcast_service.wait_until_available = AsyncMock(return_value=True)
        mock_settings_service._storage["routing.multiroom_enabled"] = True
        service.volume_service = None

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.routing"):
            await service._delayed_multiroom_sync()

        assert any("VolumeService not available" in r.message for r in caplog.records)

    async def test_no_snapcast_service_cannot_confirm_readiness(self, service, caplog):
        """Fail closed: an unconfirmed daemon is not a ready one."""
        service.snapcast_service = None

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.routing"):
            assert await service._wait_snapserver_ready(timeout=1.0) is False

        assert any("cannot confirm snapserver readiness" in r.message for r in caplog.records)


class TestTheGuardedToggle:
    """The shared body of the routing toggles: advance the flag, or put it back."""

    async def test_an_unchanged_state_is_accepted_without_running_the_body(self, service):
        """These endpoints are `PUT`, so a repeated request is expected. Running
        the body again would bypass CamillaDSP's effects or re-toggle a unit."""
        body = AsyncMock(return_value=True)

        assert await service._guarded_simple_toggle(
            AsyncMock(return_value=True), AsyncMock(), True, "equalizer_effects", body
        ) is True

        body.assert_not_called()

    async def test_a_body_that_returns_false_puts_the_flag_back(self, service, caplog):
        """The flag is what the property reports and what the next boot
        reconciles against; leaving it advanced makes the appliance re-derive a
        state its hardware never reached."""
        set_fn = AsyncMock()

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.routing"):
            assert await service._guarded_simple_toggle(
                AsyncMock(return_value=False), set_fn, True, "equalizer_effects",
                AsyncMock(return_value=False),
            ) is False

        assert [c.args[0] for c in set_fn.await_args_list] == [True, False]
        assert any("reverting to False" in r.message for r in caplog.records)

    async def test_a_body_that_raises_puts_the_flag_back_too(self, service, caplog):
        """Same revert, the other failure shape — and the one that reaches it
        when CamillaDSP is unreachable rather than merely refusing."""
        set_fn = AsyncMock()

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.routing"):
            assert await service._guarded_simple_toggle(
                AsyncMock(return_value=False), set_fn, True, "equalizer_effects",
                AsyncMock(side_effect=RuntimeError("camilla is down")),
            ) is False

        assert [c.args[0] for c in set_fn.await_args_list] == [True, False]
        assert any("camilla is down" in r.message for r in caplog.records)


class TestTheEnvWriters:
    """Three plain-text files systemd units read at ExecStart."""

    async def test_a_failed_routing_write_is_raised_not_reported_as_written(
        self, monkeypatch, caplog
    ):
        """`routing.env` carries `MILO_MODE`. Reporting a write that did not
        happen leaves the source unit pointed at the previous mode's ALSA device
        while every layer above believes the switch landed."""
        monkeypatch.setattr(
            "backend.core.multiroom.routing._atomic_write",
            AsyncMock(side_effect=OSError("read-only filesystem")),
        )

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.routing"):
            with pytest.raises(RuntimeError, match="routing.env"):
                await RoutingEnv.regenerate(True)

        assert any("read-only filesystem" in r.message for r in caplog.records)

    async def test_the_process_env_is_only_updated_once_the_file_landed(
        self, monkeypatch
    ):
        """`os.environ["MILO_MODE"]` is what this process reads back; setting it
        past a failed write makes the backend and the units disagree about the
        mode for as long as it runs."""
        monkeypatch.setattr(
            "backend.core.multiroom.routing._atomic_write",
            AsyncMock(side_effect=OSError("read-only filesystem")),
        )
        before = os.environ.get("MILO_MODE")

        with pytest.raises(RuntimeError):
            await RoutingEnv.regenerate(True)

        assert os.environ.get("MILO_MODE") == before

    async def test_a_failed_mac_write_is_raised(self, monkeypatch):
        """`mac.env` holds the ROC latency the Mac stream is negotiated at."""
        monkeypatch.setattr(
            "backend.core.multiroom.routing._atomic_write",
            AsyncMock(side_effect=OSError("no space left")),
        )

        with pytest.raises(RuntimeError, match="mac.env"):
            await MacEnv.regenerate({})

    async def test_a_failed_snapclient_write_is_raised(self, monkeypatch):
        """`snapclient.env` holds the ALSA buffer the local client plays at."""
        monkeypatch.setattr(
            "backend.core.multiroom.routing._atomic_write",
            AsyncMock(side_effect=OSError("no space left")),
        )

        with pytest.raises(RuntimeError, match="snapclient.env"):
            await SnapclientEnv.regenerate(120, 4)

    async def test_a_non_numeric_roc_latency_falls_back_to_the_declared_default(
        self, tmp_path, monkeypatch
    ):
        """The stored value reaches here straight from `settings.json`, which is
        a file on disk an operator can edit. A string in `ROC_TARGET_LATENCY`
        makes the ROC receiver refuse to start, and Mac streaming stops working
        with the failure in a unit's journal rather than Milō's."""
        monkeypatch.setattr(MacEnv, "PATH", str(tmp_path / "mac.env"))

        await MacEnv.regenerate({"target_latency_ms": "loud", "frame_length_ms": None})

        written = (tmp_path / "mac.env").read_text()
        assert f"ROC_TARGET_LATENCY={DEFAULT_ROC_CONFIG['target_latency_ms']}ms" in written
        assert f"ROC_FRAME_LENGTH={DEFAULT_ROC_CONFIG['frame_length_ms']}ms" in written

    def test_a_write_that_fails_leaves_no_temp_file_behind(self, tmp_path):
        """The temp sits next to the real file in `/var/lib/milo`. One per failed
        write accumulates in the directory the schema-version protocol asks the
        operator to inspect by hand."""
        target = tmp_path / "routing.env"
        temp = tmp_path / "routing.env.tmp"

        with patch("backend.core.multiroom.routing.os.replace",
                   side_effect=OSError("cross-device link")):
            with pytest.raises(OSError):
                _write_atomically(str(target), "MILO_MODE=multiroom\n", str(temp))

        assert not temp.exists()
        assert not target.exists()

    def test_the_original_file_survives_a_failed_write(self, tmp_path):
        """That is the whole point of writing a temp first: a unit reading the
        file mid-write must never see a truncated one."""
        target = tmp_path / "routing.env"
        target.write_text("MILO_MODE=direct\n")
        temp = tmp_path / "routing.env.tmp"

        with patch("backend.core.multiroom.routing.os.replace",
                   side_effect=OSError("cross-device link")):
            with pytest.raises(OSError):
                _write_atomically(str(target), "MILO_MODE=multiroom\n", str(temp))

        assert target.read_text() == "MILO_MODE=direct\n"


class TestTheSnapcastStatusRead:
    """The health read behind `GET /api/routing/snapcast/status`."""

    async def test_both_units_must_be_up_for_multiroom_to_be_available(self, service):
        """The client is what plays on this appliance's own DAC; a server with no
        local client is a fleet where the main unit is silent."""
        service.service_manager.is_active = AsyncMock(side_effect=[True, False])

        assert await service.get_snapcast_status() == {
            "server_active": True, "client_active": False, "multiroom_available": False,
        }

    async def test_a_systemd_failure_reports_unavailable_rather_than_raising(
        self, service, caplog
    ):
        """It feeds a `/status`-style endpoint, which answers HTTP 200 with an
        error rather than a 500 — and "unknown" must read as unavailable, not as
        available, or the UI offers multiroom controls over nothing."""
        service.service_manager.is_active = AsyncMock(side_effect=RuntimeError("dbus gone"))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.routing"):
            status = await service.get_snapcast_status()

        assert status == {
            "server_active": False, "client_active": False, "multiroom_available": False,
        }
        assert any("Error getting snapcast status" in r.message for r in caplog.records)
