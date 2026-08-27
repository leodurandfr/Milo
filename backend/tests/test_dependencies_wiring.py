"""`initialize_services()` executed, not read — the boot's four steps.

`dependencies.py` sat at 25.6 %, the worst rate in the backend: 64 of
`initialize_services`'s lines and 18 of `init_async`'s had never run. What lives
only there is the wiring itself — every A↔B cycle the constructors cannot close,
the subscription ORDER the registry bus depends on, the env files that must
reach disk before any source unit starts, and the two fail-loud paths that decide
whether a drifted `settings.json` stops the boot or is silently derived from.

None of that is visible to a guardrail: `test_service_wiring` reads the source
and runs nothing, so it can prove a setter is *written* here and never that it is
*reached*.

**Why this file can exist at all.** Building the real graph on this machine means
running the real `initialize()` of every service: pyudev enumerating the block
devices of this Pi and chaining into `sudo -n milo-mount`, D-Bus sessions against
the live BlueZ and NetworkManager, an mpv IPC socket in `/run/milo`, a Navidrome
login, and `CamillaClient` on 127.0.0.1:1234 — the daemon driving the room. The
`registry` fixture below makes that impossible rather than unlikely: the lazy
cache is pre-seeded, so `_create_service` is never entered, and the fixture
asserts afterwards that it was not.
"""
import asyncio
import logging
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

import backend.dependencies as deps
from backend.shared.persistence import SchemaVersionMismatch


class BuiltARealService(BaseException):
    """Raised if the real service factory is reached.

    `BaseException`, not `Exception`: `initialize_services` is called under
    `lifespan`'s `except Exception` and `init_async`'s `gather(...,
    return_exceptions=True)`, both of which would swallow an ordinary error and
    leave the run green with a real pyudev context, D-Bus session or CamillaDSP
    client already built.
    """


def _double(name):
    """One stand-in service.

    A plain `MagicMock` rather than an `AsyncMock`: every wiring call here is
    synchronous (`set_state_machine`, `attach_registry`, `subscribe`), and an
    AsyncMock would answer each of them with a coroutine nobody awaits. Only the
    three awaited entry points are async.
    """
    svc = MagicMock(name=f"service:{name}")
    svc.initialize = AsyncMock(return_value=True)
    svc.check = AsyncMock(return_value=None)
    svc.regenerate_env_files = AsyncMock(return_value=None)
    return svc


class _SeededRegistry(dict):
    """Stands in for `dependencies._services`, doubling on first request.

    `get_service` asks `if name not in _services` before creating, so answering
    True to every membership test is what keeps `_create_service` out of reach.
    `__missing__` covers the test's own indexing, so a test can arm a service
    before the boot has asked for it and still be arming the same object.
    """

    def __contains__(self, name):
        if not dict.__contains__(self, name):
            self[name] = _double(name)
        return True

    def __missing__(self, name):
        self[name] = _double(name)
        return self[name]


@pytest.fixture
def registry(monkeypatch):
    """Seed the lazy cache and prove the real factory was never entered."""
    seeded = _SeededRegistry()
    monkeypatch.setattr(deps, "_services", seeded)
    monkeypatch.setattr(deps, "_init_task", None)

    def _refuse(name):
        raise BuiltARealService(
            f"the real factory built {name!r}: its initialize() reaches this "
            "machine's own hardware, D-Bus and CamillaDSP"
        )

    monkeypatch.setattr(deps, "_create_service", _refuse)
    yield seeded
    task = deps.get_init_task()
    if task and not task.done():
        task.cancel()


async def _run_init(registry):
    """Run the whole boot and join its async half."""
    await deps.initialize_services()
    task = deps.get_init_task()
    await task
    return task


class TestCycleWiring:
    """The back-references no constructor can close, and what each one buys."""

    async def test_the_state_machine_gets_its_websocket_manager(self, registry):
        await _run_init(registry)

        assert registry["audio_state_machine"].ws_manager is registry["websocket_manager"]

    @pytest.mark.parametrize("attr,service_name", [
        ("routing_service", "audio_routing_service"),
        ("camilladsp_service", "camilladsp_service"),
        ("connectivity_service", "connectivity_service"),
    ])
    async def test_the_three_full_state_back_references_are_wired(
        self, registry, attr, service_name
    ):
        """`get_current_state()` reads multiroom_enabled, equalizer_effects_enabled
        and network_unavailable through these three and falls back to the benign
        value when one is unset — indistinguishable on the wire from "multiroom
        off, effects off, network fine". A wiring regression would ship as a
        silent UI lie rather than a crash, which is why the boot checks them.
        """
        await _run_init(registry)

        assert getattr(registry["audio_state_machine"], attr) is registry[service_name]

    @pytest.mark.parametrize("attr", [
        "routing_service", "camilladsp_service", "connectivity_service",
    ])
    async def test_a_missing_full_state_reference_stops_the_boot(self, registry, attr):
        """The check is the whole point: a None here is invisible on the wire.

        Reported instead of raised, the unit would come up serving a `full_state`
        that claims multiroom is off, effects off and the network fine, to every
        client including Milo-Mac.

        The reference is dropped from `routing_service.set_volume_service` — the
        last wiring call before the check — because every assignment happens
        earlier and sabotaging one of those would simply be overwritten by the
        line that follows it.
        """
        machine = registry["audio_state_machine"]
        registry["audio_routing_service"].set_volume_service = MagicMock(
            side_effect=lambda _v: setattr(machine, attr, None)
        )

        with pytest.raises(RuntimeError, match=f"{attr} not wired"):
            await deps.initialize_services()

    async def test_routing_resolves_its_sources_through_the_state_machine(self, registry):
        """The callback is a lambda, so only calling it proves what it closes over.

        Wired to anything else, `AudioRoutingService` would resolve a source to
        the wrong object and stop the wrong unit on a mode switch.
        """
        await _run_init(registry)

        callback = registry["audio_routing_service"].set_source_callback.call_args.args[0]
        machine = registry["audio_state_machine"]
        callback("radio")

        machine.get_source.assert_called_once_with("radio")

    async def test_camilladsp_calls_volume_back_on_reconnect(self, registry):
        """The single most consequential line in this function.

        CamillaDSP is `PartOf=milo-backend.service` and restarts with the backend
        at its own default gain; this callback is the only thing that puts the
        room's level back. Unwired, a restart leaves the DSP wherever it came up.
        """
        await _run_init(registry)

        registry["camilladsp_service"].set_on_reconnect_callback.assert_called_once_with(
            registry["volume_service"].reapply_current_volume
        )

    async def test_volume_and_snapcast_hold_each_other(self, registry):
        await _run_init(registry)

        registry["volume_service"].set_snapcast_websocket_service.assert_called_once_with(
            registry["snapcast_websocket_service"]
        )
        registry["snapcast_websocket_service"].set_volume_service.assert_called_once_with(
            registry["volume_service"]
        )

    async def test_volume_and_routing_hold_each_other(self, registry):
        await _run_init(registry)

        registry["volume_service"].set_routing_service.assert_called_once_with(
            registry["audio_routing_service"]
        )
        registry["audio_routing_service"].set_volume_service.assert_called_once_with(
            registry["volume_service"]
        )

    async def test_routing_can_start_and_stop_the_control_websocket(self, registry):
        await _run_init(registry)

        registry["audio_routing_service"].set_snapcast_websocket_service.assert_called_once_with(
            registry["snapcast_websocket_service"]
        )


class TestSubscriptionOrder:
    """The one thing in STEP 2 that does NOT commute.

    Registry subscribers are notified in subscription order. The volume state
    store must be current before the snapcast broadcaster fires a multiroom
    event, or the frame that reaches the UI carries the level from before the
    change that caused it.
    """

    async def test_volume_subscribes_before_the_broadcaster(self, registry):
        order = []
        registry["volume_service"].attach_registry = MagicMock(
            side_effect=lambda _r: order.append("volume")
        )
        registry["crossover_service"].set_registry = MagicMock(
            side_effect=lambda _r: order.append("crossover")
        )
        registry["snapcast_websocket_service"].set_registry = MagicMock(
            side_effect=lambda _r: order.append("snapcast_ws")
        )

        await _run_init(registry)

        assert order.index("volume") < order.index("snapcast_ws")

    async def test_all_three_subscribers_are_attached_to_the_one_registry(self, registry):
        """Three services read this bus; one wired to a different registry
        instance would receive nothing at all, silently."""
        await _run_init(registry)

        reg = registry["client_registry_service"]
        registry["volume_service"].attach_registry.assert_called_once_with(reg)
        registry["crossover_service"].set_registry.assert_called_once_with(reg)
        registry["snapcast_websocket_service"].set_registry.assert_called_once_with(reg)


class TestSourceRegistration:
    """STEP 3 — before the async init, and covering every enum member."""

    async def test_every_audio_source_is_registered(self, registry):
        """A source missing here is unreachable: `state_machine.get_source()`
        answers None, the transition is refused, and the button does nothing with
        no error anywhere. `AudioSource.NONE` is the absence sentinel and is the
        one member that must NOT be registered.
        """
        from backend.core.models.audio_state import AudioSource

        await _run_init(registry)

        registered = {
            call.args[0]
            for call in registry["audio_state_machine"].register_source.call_args_list
        }
        expected = set(AudioSource) - {AudioSource.NONE}
        assert registered == expected

    async def test_sources_are_registered_before_the_async_init_runs(self, registry):
        """Stated by the function's own docstring as one of only three ordering
        constraints. `init_async` starts the sources' own `initialize()`, and a
        source that publishes a state before the machine knows it exists has that
        state dropped."""
        order = []
        registry["audio_state_machine"].register_source = MagicMock(
            side_effect=lambda *_: order.append("register")
        )
        registry["radio_source"].initialize = AsyncMock(
            side_effect=lambda: order.append("init")
        )

        await _run_init(registry)

        assert order.index("register") < order.index("init")


class TestEnvFilesBeforeAnything:
    """STEP 3b — the second ordering constraint, and the boot's first schema read."""

    async def test_the_env_files_are_written_before_any_service_initialises(
        self, registry
    ):
        """Source units read their `EnvironmentFile` at start. Written after the
        async init, a unit started by `routing_service.initialize()` reads the
        PREVIOUS routing mode, and a direct/multiroom switch made while the
        backend was down never takes effect.
        """
        order = []
        registry["audio_routing_service"].regenerate_env_files = AsyncMock(
            side_effect=lambda: order.append("env")
        )
        registry["audio_routing_service"].initialize = AsyncMock(
            side_effect=lambda: order.append("routing_init")
        )
        registry["camilladsp_service"].initialize = AsyncMock(
            side_effect=lambda: order.append("camilla_init")
        )

        await _run_init(registry)

        assert order[0] == "env"

    async def test_a_drifted_settings_file_stops_the_boot_before_deriving_from_it(
        self, registry, caplog
    ):
        """Fail loud + reset, never migrate.

        This is the first read of `settings.json`, so a schema drift is caught
        here — before `routing.env` is derived from a shape nobody validated and
        written over the file the running units read.
        """
        registry["audio_routing_service"].regenerate_env_files = AsyncMock(
            side_effect=SchemaVersionMismatch("settings.json", 1, 2)
        )

        with caplog.at_level(logging.ERROR):
            with pytest.raises(SystemExit) as exit_info:
                await deps.initialize_services()

        assert exit_info.value.code == 1
        assert "Schema version mismatch while deriving the env files" in caplog.text

    async def test_the_bailing_boot_flushes_stderr_so_systemd_keeps_the_banner(
        self, registry, monkeypatch
    ):
        """systemd loops the unit on SystemExit(1); the operator only ever sees
        the journal. An unflushed stderr on a process about to exit is a banner
        that never reaches it, and the loop then looks like a silent crash."""
        flushed = []
        monkeypatch.setattr(sys.stderr, "flush", lambda: flushed.append(1))
        registry["audio_routing_service"].regenerate_env_files = AsyncMock(
            side_effect=SchemaVersionMismatch("settings.json", 1, 2)
        )

        with pytest.raises(SystemExit):
            await deps.initialize_services()

        assert flushed


class TestAsyncInit:
    """STEP 4 — the parallel gather, and what it does with what comes back."""

    async def test_the_whole_service_set_is_initialised(self, registry):
        await _run_init(registry)

        for name in (
            "settings_service", "hardware_service", "client_registry_service",
            "audio_routing_service", "volume_service", "screen_controller",
            "bt_remote_controller", "ir_remote_controller", "fan_controller",
            "snapcast_websocket_service", "camilladsp_service", "crossover_service",
            "pending_clients_service", "radio_source", "cd_source",
            "podcast_source", "music_library_source", "connectivity_service",
            "network_service",
        ):
            registry[name].initialize.assert_awaited_once()

    async def test_the_hostname_check_runs_its_own_entry_point(self, registry):
        """`hostname_conflict_service` is initialised by `check()`, not
        `initialize()` — it is a probe, not a service start. Called on the wrong
        name it would answer a MagicMock here and nothing at all on the unit."""
        await _run_init(registry)

        registry["hostname_conflict_service"].check.assert_awaited_once()

    async def test_one_failed_service_does_not_stop_the_others(self, registry, caplog):
        """`return_exceptions=True` is what makes the appliance boot degraded.

        A dev host has no IR receiver and no rotary encoder; a Pi with a missing
        DAC still has to serve its UI. Without it, one raise takes the whole
        gather and the backend never comes up.
        """
        registry["ir_remote_controller"].initialize = AsyncMock(
            side_effect=RuntimeError("no /dev/lirc0")
        )

        with caplog.at_level(logging.ERROR):
            await _run_init(registry)

        registry["volume_service"].initialize.assert_awaited_once()
        assert "ir_remote_controller initialization failed" in caplog.text

    @pytest.mark.parametrize("critical", ["routing_service", "volume_service"])
    async def test_a_critical_service_failure_is_re_raised(self, registry, critical):
        """Routing and volume are the two without which the box makes no sound.

        Degraded like the rest, the unit would come up serving a UI over an audio
        path that was never wired — the worst failure to diagnose, because
        everything answers.
        """
        name = {"routing_service": "audio_routing_service",
                "volume_service": "volume_service"}[critical]
        registry[name].initialize = AsyncMock(side_effect=RuntimeError("boom"))

        await deps.initialize_services()

        with pytest.raises(RuntimeError, match="boom"):
            await deps.get_init_task()

    def test_a_schema_mismatch_during_init_bails_and_names_the_service(
        self, registry, caplog
    ):
        """The banner has to say WHICH file, since the operator's next action is
        an `rm`. Reported as an ordinary failure instead, the boot would carry on
        and the next persist would overwrite the evidence.

        Driven from a synchronous test through its own `asyncio.run`: a
        `SystemExit` raised inside a Task is re-raised into the event loop by
        `Task.__step` as well as being stored on the future, so it escapes
        `run_until_complete` and never reaches an `await` inside the test
        coroutine. That is also exactly why it stops the boot on the unit.
        """
        async def _boot():
            registry["podcast_source"].initialize = AsyncMock(
                side_effect=SchemaVersionMismatch("podcast_data.json", 1, 2)
            )
            await deps.initialize_services()
            await deps.get_init_task()

        with caplog.at_level(logging.ERROR):
            with pytest.raises(SystemExit) as exit_info:
                asyncio.run(_boot())

        assert exit_info.value.code == 1
        assert "during podcast_source init" in caplog.text

    def test_the_schema_check_beats_the_critical_check(self, registry):
        """Both loops walk the same results. A `volume_service` that failed on a
        schema mismatch must exit(1) with the banner, not re-raise the mismatch
        as an ordinary critical failure — one tells the operator what to delete,
        the other does not.
        """
        async def _boot():
            registry["volume_service"].initialize = AsyncMock(
                side_effect=SchemaVersionMismatch("last_volume.json", 1, 2)
            )
            await deps.initialize_services()
            await deps.get_init_task()

        with pytest.raises(SystemExit):
            asyncio.run(_boot())

    async def test_the_periodic_hostname_recheck_starts_after_the_boot_check(
        self, registry
    ):
        """Started before, the periodic sweep races the boot probe and both hit
        avahi at once during the busiest second of the boot."""
        order = []
        registry["hostname_conflict_service"].check = AsyncMock(
            side_effect=lambda: order.append("check")
        )
        registry["hostname_conflict_service"].start_periodic = MagicMock(
            side_effect=lambda: order.append("periodic")
        )

        await _run_init(registry)

        assert order == ["check", "periodic"]

    async def test_a_failed_boot_never_starts_the_periodic_recheck(self, registry):
        """It is the last statement of `init_async`; a critical failure raises
        before it. A sweep left running against a process that is about to be
        restarted by systemd is a timer nobody cancels."""
        registry["volume_service"].initialize = AsyncMock(side_effect=RuntimeError("boom"))

        await deps.initialize_services()
        with pytest.raises(RuntimeError):
            await deps.get_init_task()

        registry["hostname_conflict_service"].start_periodic.assert_not_called()

    async def test_the_init_task_is_exposed_for_the_lifespan_to_await(self, registry):
        """`main.py::lifespan` awaits it before declaring startup complete.

        Unexposed, the lifespan would yield while the services were still coming
        up and the first HTTP request would be served over a half-wired graph.
        """
        assert deps.get_init_task() is None

        await deps.initialize_services()

        task = deps.get_init_task()
        assert isinstance(task, asyncio.Task)
        await task


class TestRotaryCreator:
    """`_create_rotary_controller` — the one creator that can answer None."""

    @pytest.fixture
    def hardware(self, monkeypatch):
        seeded = _SeededRegistry()
        monkeypatch.setattr(deps, "_services", seeded)
        return seeded["hardware_service"]

    def test_a_disabled_encoder_produces_no_controller(self, hardware, caplog):
        """None is a value the boot handles: `init_async` skips its `initialize()`
        and `main.py` skips its cleanup. Built anyway, `rotary.initialize()` opens
        gpiochip0 and claims three pins the live backend already holds.
        """
        hardware.get_rotary_enabled.return_value = False

        with caplog.at_level(logging.INFO):
            assert deps._create_rotary_controller() is None

        assert "Rotary encoder disabled in hardware config" in caplog.text

    def test_dac_mode_disables_the_encoder_whatever_the_hardware_flag_says(
        self, hardware, caplog
    ):
        """The knob turns a volume Milō does not own. Kept alive it would move
        the state store and broadcast a level the amplifier ignores, so the UI
        slider and the room would disagree permanently.
        """
        hardware.get_rotary_enabled.return_value = True
        hardware.get_volume_control.return_value = False

        with caplog.at_level(logging.INFO):
            assert deps._create_rotary_controller() is None

        assert "DAC mode: rotary encoder disabled" in caplog.text


class TestConstantLookup:
    """`_const` — the indirection the Mac source's config is built from."""

    def test_a_constant_is_read_from_the_shared_module(self):
        """The three ROC ports and the ALSA output are declared once, in
        `config/constants.py`, and the Mac unit's env file is derived from the
        same names. A creator that inlined them would drift from the unit."""
        from backend.config import constants

        assert deps._const("MAC_RTP_PORT") == constants.MAC_RTP_PORT
        assert deps._const("MAC_AUDIO_OUTPUT") == constants.MAC_AUDIO_OUTPUT
