"""Old-wire golden harness: what each source puts on the wire, recorded once.

The source refactor (docs: source architecture, phases 0b-4) rewrites every
source's internals while the wire must stay byte-identical until the one step
that changes it on purpose. These tests are the proof. Each scenario drives a
source through the outside world only — lifecycle and commands through the
public API, daemon/mpv/D-Bus stimuli through a per-source adapter — and
compares every envelope a real AudioStateMachine broadcasts, full_state
included, plus the REST answer at the end, with a recording.

Rules that keep the recording meaningful:
- The recordings were taken on the code *before* the refactor. A phase that
  rewrites a source rewrites its adapter (how a stimulus reaches the source),
  never a scenario and never a recording.
- Re-recording (MILO_RECORD_OLD_WIRE=1) is for adding a scenario, on a tree
  whose source code has not moved since the last green run. Re-recording to
  make a red run green erases the only witness of the drift.
"""
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.state import AudioStateMachine

RECORDINGS = Path(__file__).parent / "old_wire"
RECORD = os.environ.get("MILO_RECORD_OLD_WIRE") == "1"
# A directory to write what each scenario produced now, for diffing a red run
# against the recordings without touching them.
DUMP = os.environ.get("MILO_DUMP_OLD_WIRE")


class WireRecorder:
    """Stands in for WebSocketManager: keeps every envelope, minus its clock."""

    def __init__(self) -> None:
        self.envelopes: List[Dict[str, Any]] = []

    async def broadcast_dict(self, envelope: Dict[str, Any]) -> None:
        kept = {k: v for k, v in envelope.items() if k != "timestamp"}
        # Serialized now: a payload is a live dict the state machine may
        # mutate after broadcasting it (update_position_metadata writes into
        # system_state.metadata in place).
        self.envelopes.append(json.loads(json.dumps(kept)))


def make_state_machine() -> tuple[AudioStateMachine, WireRecorder]:
    recorder = WireRecorder()
    machine = AudioStateMachine()
    machine.ws_manager = recorder
    return machine, recorder


def make_systemd() -> Mock:
    """The systemd side of every source: every unit starts, stops and answers."""
    manager = Mock()
    for verb in ("start", "stop", "restart", "is_active"):
        setattr(manager, verb, AsyncMock(return_value=True))
    manager.probe_active = AsyncMock(return_value=False)
    manager.main_pid = AsyncMock(return_value=4242)
    return manager


def make_settings(values: Optional[Dict[str, Any]] = None) -> Mock:
    """SettingsService reads answered from `values`, None for anything else."""
    store = {"audio.auto_stop_delay": 120, **(values or {})}
    settings = Mock()
    settings.get_setting = AsyncMock(side_effect=lambda key, *a, **k: store.get(key))
    settings.set_setting = AsyncMock(return_value=True)
    return settings


async def settle() -> None:
    """Run the loop until nothing is ready to run.

    Deterministic, unlike a sleep budget: a task parked on a timer or on an
    event (a 120 s pause timer, an actor waiting for mail) is not ready and does
    not hold this up, while everything a step set in motion runs to its next
    real wait. Reads the loop's ready queue, which asyncio does not expose —
    acceptable in a harness, and loud if it ever disappears.
    """
    loop = asyncio.get_running_loop()
    for _ in range(10_000):
        await asyncio.sleep(0)
        if not loop._ready:  # noqa: SLF001 — see docstring
            await asyncio.sleep(0)
            if not loop._ready:
                return
    raise AssertionError("the loop never went quiet — a task is spinning")


_real_sleep = asyncio.sleep


class AsyncioProxy:
    """A module's `asyncio`, with `sleep` replaced and everything else real.

    Patched into one module at a time (`monkeypatch.setattr(module, "asyncio",
    AsyncioProxy(...))`), so a clock that module waits on becomes a step the
    scenario takes, without touching asyncio for anyone else.
    """

    def __init__(self, sleep) -> None:
        self.sleep = sleep

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)


async def instant_short_sleep(delay: float, *a: Any, **k: Any) -> Any:
    """Settle delays (a unit given 0.5 s to come up) pass at once; a real wait —
    a 120 s pause timer — still waits, so it only fires if a scenario asks."""
    return await _real_sleep(0 if delay <= 1.0 else delay, *a, **k)


class TickGate:
    """A periodic loop's sleep, opened one pass at a time by the scenario."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    async def sleep(self, delay: float, *a: Any, **k: Any) -> None:
        await self._event.wait()
        self._event.clear()

    async def tick(self, times: int = 1) -> None:
        for _ in range(times):
            self._event.set()
            await settle()


class FakeMpv:
    """mpv as the four mpv sources see it: properties answer from `props`.

    Only the calls the sources make exist; a new call fails loudly instead of
    answering a Mock, which would publish `<Mock ...>` into a recording.
    """

    def __init__(self, **props: Any) -> None:
        self.props: Dict[str, Any] = {
            "pause": False, "idle-active": False, "core-idle": False,
            "time-pos": 0, "playback-time": 0, "duration": None,
            "metadata": {}, "speed": 1.0, **props,
        }
        self.is_connected = True
        self.accept = True
        self.loaded: List[str] = []

    async def connect(self) -> bool:
        self.is_connected = True
        return True

    async def disconnect(self) -> None:
        self.is_connected = False

    async def get_property(self, name: str) -> Any:
        return self.props.get(name)

    async def set_property(self, name: str, value: Any) -> bool:
        self.props[name] = value
        return self.accept

    async def get_metadata(self) -> Dict[str, Any]:
        return dict(self.props.get("metadata") or {})

    async def is_playing(self) -> bool:
        return (
            not self.props["pause"]
            and not self.props["idle-active"]
            and not self.props["core-idle"]
        )

    async def load_stream(self, url: str, *a: Any, **k: Any) -> bool:
        if self.accept:
            self.loaded.append(url)
        return self.accept

    async def load_file(self, url: str, *a: Any, **k: Any) -> bool:
        return await self.load_stream(url)

    async def stop(self) -> bool:
        return True

    async def pause(self) -> bool:
        if self.accept:
            self.props["pause"] = True
        return self.accept

    async def resume(self) -> bool:
        if self.accept:
            self.props["pause"] = False
        return self.accept

    async def seek(self, position: float, *a: Any, **k: Any) -> bool:
        if self.accept:
            self.props["time-pos"] = position
            self.props["playback-time"] = position
        return self.accept

    async def set_speed(self, speed: float) -> bool:
        return await self.set_property("speed", speed)

    async def wait_until_advancing(self, *a: Any, **k: Any) -> bool:
        return self.accept

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(f"FakeMpv has no '{name}' — add it to the harness")


class Wire:
    """One scenario's record: the envelopes, and REST answers taken on demand."""

    def __init__(self, machine: AudioStateMachine, recorder: WireRecorder) -> None:
        self.machine = machine
        self.recorder = recorder
        self.rest: List[Dict[str, Any]] = []

    async def snapshot_rest(self) -> None:
        """What GET /api/audio/state answers right now."""
        await self.machine.refresh_active_metadata()
        await settle()
        self.rest.append(json.loads(json.dumps(self.machine.get_current_state())))

    def record(self) -> Dict[str, Any]:
        return {"ws": self.recorder.envelopes, "rest": self.rest}


def check_recording(source: str, scenario: str, wire: Wire) -> None:
    """Compare (or, when recording, store) one scenario, byte for byte."""
    got = wire.record()
    assert got["ws"], f"{source}/{scenario} put nothing on the wire — the driver is broken"
    if DUMP:
        Path(DUMP, f"{source}.{scenario}.json").write_text(json.dumps(got, ensure_ascii=False))
    path = RECORDINGS / f"{source}.json"
    stored = json.loads(path.read_text()) if path.exists() else {}
    if RECORD:
        stored[scenario] = got
        RECORDINGS.mkdir(exist_ok=True)
        path.write_text(json.dumps(stored, indent=1, ensure_ascii=False) + "\n")
        pytest.skip(f"recorded {source}/{scenario}")
    assert scenario in stored, f"no recording for {source}/{scenario}"
    # Dumped, not compared as dicts: key order is part of the wire.
    if json.dumps(got, ensure_ascii=False) != json.dumps(stored[scenario], ensure_ascii=False):
        pytest.fail(_first_difference(source, scenario, stored[scenario], got), pytrace=False)


def _first_difference(source: str, scenario: str, want: Dict, got: Dict) -> str:
    for part in ("ws", "rest"):
        a, b = want[part], got[part]
        for i in range(max(len(a), len(b))):
            x = a[i] if i < len(a) else None
            y = b[i] if i < len(b) else None
            if json.dumps(x, ensure_ascii=False) != json.dumps(y, ensure_ascii=False):
                return (
                    f"{source}/{scenario}: {part}[{i}] differs "
                    f"({len(a)} recorded, {len(b)} now)\n"
                    f"recorded: {json.dumps(x, ensure_ascii=False)}\n"
                    f"now:      {json.dumps(y, ensure_ascii=False)}"
                )
    return f"{source}/{scenario}: same envelopes, different serialization"
