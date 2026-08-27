"""The lifespan itself: what the boot does after the services are wired, and the
order the shutdown table encodes.

`run_teardown` was already tested; `lifespan` was not — its 26 lines had never
run. Four things live only there:

* **the join.** Startup is not complete until `init_task` has finished; yielding
  earlier serves the first HTTP request over a half-wired graph.
* **the lingering-unit sweep.** Source units carry `BindsTo=milo-backend`, so
  systemd restarts them *with* the backend — while the state machine comes up at
  `source=NONE`. A unit left running holds `hw:Loopback,0,0`, and the first
  source the user picks cannot open it.
* **the shutdown table's ORDER**, whose reasoning is a paragraph of comment in
  `main.py` and was asserted nowhere: the registry's producer is silenced first,
  then the two entries that WRITE, and only then CamillaDSP — the entry most
  likely to block on a daemon going down with us.
* **the startup failure arm**, which must re-raise so systemd sees a failed unit
  instead of an app answering over a graph that never came up.

Everything the lifespan touches is a module global of `backend.main`, so the
doubles are installed there. `initialize_services` is doubled too: the real one
runs every service's `initialize()`, which on this machine means pyudev, D-Bus,
mpv, Navidrome and CamillaDSP on 127.0.0.1:1234.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.main as main


class RanTheRealBoot(BaseException):
    """Raised if the real `initialize_services` is reached.

    `BaseException` because `lifespan` wraps its whole startup in
    `except Exception`, which would log the breach and re-raise it as an
    ordinary startup failure — green run, real graph half-built.
    """


def _source(service_name, active):
    src = MagicMock()
    src.service_name = service_name
    src._active = active
    return src


@pytest.fixture
def boot(monkeypatch):
    """Replace every module global the lifespan reads, and forbid the real boot.

    Returns the shutdown-order log plus the handful of doubles the tests drive.
    """
    import backend.dependencies as deps

    def _refuse_to_build(name):
        raise RanTheRealBoot(
            f"the real factory built {name!r}: its initialize() reaches this "
            "machine's pyudev, D-Bus, mpv, Navidrome and CamillaDSP"
        )

    # Two nets, and the second is the one that matters. The double below stands
    # in for the boot; this makes the REAL boot unable to build anything if a
    # test ever replaces the double with something that calls through.
    monkeypatch.setattr(deps, "_create_service", _refuse_to_build)
    monkeypatch.setattr(main, "initialize_services", AsyncMock())
    monkeypatch.setattr(main, "get_init_task", lambda: None)

    order: list = []

    def _torn_down(name):
        svc = MagicMock(name=name)

        async def _cleanup():
            order.append(name)

        svc.cleanup = _cleanup
        svc.shutdown = _cleanup
        return svc

    state_machine = MagicMock()
    state_machine.sources = {}
    state_machine.cleanup = MagicMock()
    state_machine.start_inactivity_monitor = MagicMock()

    systemd = MagicMock()
    systemd.is_active = AsyncMock(return_value=False)
    systemd.stop = AsyncMock(return_value=True)

    network = MagicMock()
    network.maybe_start_hotspot = AsyncMock()
    network.cleanup = _torn_down("network").cleanup

    ws_log_handler = MagicMock()

    named = {}
    for name in (
        "snapcast_websocket_service", "client_registry_service", "volume_service",
        "camilladsp_service", "equalizer_proxy_service", "levels_monitor",
        "screen_controller", "bt_remote_controller", "ir_remote_controller",
        "fan_controller", "routing_service", "crossover_service",
        "rotary_controller",
    ):
        named[name] = _torn_down(name)
        monkeypatch.setattr(main, name, named[name])

    monkeypatch.setattr(main, "state_machine", state_machine)
    monkeypatch.setattr(main, "systemd_manager", systemd)
    monkeypatch.setattr(main, "network_service", network)
    monkeypatch.setattr(main, "settings_service", MagicMock())
    monkeypatch.setattr(main, "_ws_log_handler", ws_log_handler)

    by_service_name = {
        "pending_clients_service": _torn_down("pending_clients"),
        "connectivity_service": _torn_down("connectivity"),
        "hostname_conflict_service": _torn_down("hostname_conflict"),
    }
    music_library = MagicMock()
    music_library.shares = _torn_down("music_library_shares")
    by_service_name["music_library_source"] = music_library
    monkeypatch.setattr(main, "get_service", lambda n: by_service_name[n])

    return {
        "order": order,
        "state_machine": state_machine,
        "systemd": systemd,
        "network": network,
        "ws_log_handler": ws_log_handler,
        "named": named,
        "monkeypatch": monkeypatch,
    }


async def _run_lifespan(boot):
    async with main.lifespan(MagicMock()):
        pass


class TestStartup:
    """What has to have happened before the first request is served."""

    async def test_the_services_are_joined_before_the_app_starts_serving(self, boot):
        """`init_async` is a task, not an await inside `initialize_services`.

        Not joined, `lifespan` yields while the sources are still initialising:
        FastAPI starts accepting, and the first `GET /api/audio/state` reads a
        state machine with no sources registered yet.
        """
        joined = []

        async def _init_task_body():
            await asyncio.sleep(0)
            joined.append("init")

        task = asyncio.create_task(_init_task_body())
        boot["monkeypatch"].setattr(main, "get_init_task", lambda: task)

        async with main.lifespan(MagicMock()):
            assert joined == ["init"]

    async def test_a_boot_with_no_init_task_still_starts(self, boot):
        """`get_init_task()` answers None until `initialize_services` has run.

        Awaited unconditionally, a boot that bailed before STEP 4 would raise
        `TypeError: object NoneType can't be used in 'await'` on top of whatever
        actually went wrong, hiding it.
        """
        await _run_lifespan(boot)

        boot["state_machine"].start_inactivity_monitor.assert_called_once()

    async def test_source_units_left_running_by_systemd_are_stopped(self, boot):
        """`BindsTo=milo-backend` restarts them with us; the machine starts at NONE.

        A source unit still running holds `hw:Loopback,0,0`, so the first source
        the user picks cannot open its device — and nothing in the UI explains
        why, because from Milō's side no source is active.
        """
        boot["state_machine"].sources = {
            "radio": _source("milo-radio", active=True),
            "spotify": _source("milo-go-librespot", active=False),
        }
        boot["systemd"].is_active = AsyncMock(side_effect=lambda name: name == "milo-radio")

        await _run_lifespan(boot)

        boot["systemd"].stop.assert_awaited_once_with("milo-radio")

    async def test_a_source_that_owns_no_unit_is_never_probed(self, boot):
        """Bluetooth and DLNA have `service_name = None`.

        Passed through, `systemctl is-active None` is a spawn per boot with a
        nonsense argument, and its answer would decide a stop.
        """
        boot["state_machine"].sources = {"bluetooth": _source(None, active=True)}

        await _run_lifespan(boot)

        boot["systemd"].is_active.assert_not_awaited()
        boot["systemd"].stop.assert_not_awaited()

    async def test_a_missing_source_entry_is_skipped(self, boot):
        """`register_source` can be handed None for a source whose creation
        failed; the sweep runs over the same dict."""
        boot["state_machine"].sources = {"radio": None}

        await _run_lifespan(boot)

        boot["systemd"].is_active.assert_not_awaited()

    async def test_nothing_lingering_means_no_stop_at_all(self, boot):
        """The common case — a cold boot. A stop issued anyway is a privileged
        spawn per source on every single start."""
        boot["state_machine"].sources = {"radio": _source("milo-radio", active=True)}
        boot["systemd"].is_active = AsyncMock(return_value=False)

        await _run_lifespan(boot)

        boot["systemd"].stop.assert_not_awaited()

    async def test_the_error_banner_is_armed_with_the_state_machine(self, boot):
        """`WebSocketLogHandler` is attached to the `backend` logger at import,
        but it can only broadcast once it has the machine. Unarmed, every
        backend ERROR is journalled and none of them reaches the UI banner."""
        await _run_lifespan(boot)

        boot["ws_log_handler"].set_state_machine.assert_called_once_with(
            boot["state_machine"]
        )

    async def test_the_inactivity_monitor_is_started_by_the_boot(self, boot):
        """Nothing else starts it, and it is what returns the appliance to NONE
        after a source is left idle — the state the screen sleeps on."""
        await _run_lifespan(boot)

        boot["state_machine"].start_inactivity_monitor.assert_called_once()

    async def test_the_first_boot_hotspot_is_offered_the_settings(self, boot):
        """A unit flashed and powered on with no network has no other way to be
        configured: the wizard is reached over this hotspot."""
        await _run_lifespan(boot)

        boot["network"].maybe_start_hotspot.assert_awaited_once_with(main.settings_service)

    async def test_a_failed_startup_is_logged_and_re_raised(self, boot, caplog):
        """Swallowed, uvicorn would start serving over a graph that never came up
        and systemd would report the unit healthy — the failure mode with no
        symptom except that nothing works.
        """
        async def _boom():
            raise RuntimeError("settings.json unreadable")

        boot["monkeypatch"].setattr(main, "initialize_services", _boom)

        with caplog.at_level(logging.ERROR):
            with pytest.raises(RuntimeError, match="settings.json unreadable"):
                await _run_lifespan(boot)

        assert "Application startup failed" in caplog.text

    async def test_a_startup_that_failed_runs_no_teardown(self, boot):
        """The context manager never yields, so the shutdown half is unreachable.

        Stated because the alternative is worse than it looks: tearing down
        services that were never initialised would call `cleanup()` on
        half-constructed objects while the operator is reading the startup error.
        """
        async def _boom():
            raise RuntimeError("nope")

        boot["monkeypatch"].setattr(main, "initialize_services", _boom)

        with pytest.raises(RuntimeError):
            await _run_lifespan(boot)

        assert boot["order"] == []


class TestShutdownTable:
    """The order the shutdown comment argues for, asserted.

    Each entry is isolated and bounded by `run_teardown`, so order is no longer
    about one hang denying the rest — it is about *state*: two of these entries
    write to disk, and one of them mutates what the other is about to flush.
    """

    async def test_the_registry_producer_is_silenced_before_the_flushes(self, boot):
        """`snapcast_websocket` is what MUTATES the registry.

        Flushing before its loop is cancelled leaves a debounce armed that
        nothing then gets to fire, so the last multiroom change is written by
        nobody and is gone at the next boot.
        """
        await _run_lifespan(boot)

        order = boot["order"]
        assert order.index("snapcast_websocket_service") < order.index("client_registry_service")
        assert order.index("snapcast_websocket_service") < order.index("volume_service")

    async def test_the_two_writers_flush_before_camilladsp(self, boot):
        """They used to sit behind it — the entry most likely to block on a
        daemon going down with us. `client_registry` and `volume` are the only
        two that WRITE (pending multiroom state, pending volume), and systemd's
        TimeoutStopSec is 10 s for the whole shutdown.
        """
        await _run_lifespan(boot)

        order = boot["order"]
        assert order.index("client_registry_service") < order.index("camilladsp_service")
        assert order.index("volume_service") < order.index("camilladsp_service")

    async def test_the_state_machine_is_cleaned_before_the_table_runs(self, boot):
        """It owns the inactivity monitor task, which can still fire a transition.

        A transition starting while the sources are being torn down would start a
        unit the shutdown has already stopped.
        """
        calls = []
        boot["state_machine"].cleanup = MagicMock(side_effect=lambda: calls.append("machine"))

        await _run_lifespan(boot)

        assert calls == ["machine"]
        assert boot["order"], "the teardown table did not run"

    async def test_every_service_with_a_cleanup_is_in_the_table(self, boot):
        """A `cleanup()` nobody calls is not a cleanup — and the AST guardrail in
        `test_service_wiring` proves the CALL is written here, never that the
        table is reached. This is the executed half of that pair.
        """
        await _run_lifespan(boot)

        assert set(boot["order"]) == {
            "snapcast_websocket_service", "client_registry_service", "volume_service",
            "camilladsp_service", "pending_clients", "equalizer_proxy_service",
            "levels_monitor", "screen_controller", "bt_remote_controller",
            "ir_remote_controller", "fan_controller", "connectivity", "network",
            "routing_service", "crossover_service", "hostname_conflict",
            "music_library_shares", "rotary_controller",
        }

    async def test_a_unit_with_no_encoder_tears_down_without_one(self, boot):
        """`rotary_controller` is None on a unit with no encoder — the creator
        answers None on purpose. Appended unconditionally, every such unit would
        raise `AttributeError` on shutdown, inside a teardown entry that logs and
        moves on, so the failure would be a line in a journal nobody reads.
        """
        boot["monkeypatch"].setattr(main, "rotary_controller", None)

        await _run_lifespan(boot)

        assert "rotary_controller" not in boot["order"]
        assert "fan_controller" in boot["order"]

    async def test_one_cleanup_that_raises_does_not_deny_the_rest(self, boot):
        """The property `run_teardown` exists for, stated end to end rather than
        on the helper alone: a daemon already gone is the normal case for
        CamillaDSP, and the flushes before it must still have happened.
        """
        async def _boom():
            raise RuntimeError("daemon already gone")

        boot["named"]["camilladsp_service"].cleanup = _boom

        await _run_lifespan(boot)

        assert "volume_service" in boot["order"]
        assert "routing_service" in boot["order"]
