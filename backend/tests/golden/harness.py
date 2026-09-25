"""Golden harness: what each source puts on the wire, recorded and reviewed.

Each scenario drives a source through the outside world only — lifecycle and
commands through the public API, daemon/mpv/D-Bus stimuli through a
per-source adapter — and compares every envelope a real AudioStateMachine
broadcasts, plus the REST answer at the end, with a recording.

History: recorded on the old wire before the source refactor (docs: source
architecture, phases 0b-4), which then rewrote every source's internals under
it and re-recorded a source only where its phase changed that source's wire on
purpose. Phase 5b switched the wire itself (docs: "Développeurs : le fil"):
every recording was re-recorded then, each scenario reviewed against its old
recording through a condensed side-by-side (the sequence of source state and
playing flags before, of service, phase, controls and availability after).

Rules that keep the recording meaningful:
- A change to a source's internals rewrites its adapter (how a stimulus
  reaches the source), never a scenario and never a recording.
- Re-recording (MILO_RECORD_WIRE=1) is for adding a scenario, on a tree whose
  source code has not moved since the last green run, or for a change of the
  wire made on purpose — then only the files it touches, after every differing
  envelope was reviewed and listed in the commit, with the MILO_DUMP_WIRE output
  of the reviewed run byte-identical to what is recorded. Re-recording to make
  a red run green erases the only witness of the drift.
- Session ids are random: the recorder renames them `session-N` in order of
  appearance; anchors are stamped on the golden wall clock (conftest.py), which
  moves one second per TickGate tick, unless a world puts it on its own
  VirtualClock.
"""
import asyncio
import heapq
import itertools
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, Mock

import pytest

from backend.config.constants import DEFAULT_DOCK_APPS
from backend.core.state import AudioStateMachine

RECORDINGS = Path(__file__).parent / "wire"
RECORD = os.environ.get("MILO_RECORD_WIRE") == "1"
# A directory to write what each scenario produced now, for diffing a red run
# against the recordings without touching them.
DUMP = os.environ.get("MILO_DUMP_WIRE")


# The wall clock position anchors are stamped with, in every scenario and world
# (`audio_source.wall_time`): frozen here by the golden conftest, advanced with
# a world's VirtualClock by `use_virtual_wall`.
EPOCH = 1790270000.0

# The golden wall clock (conftest.py reads it): it starts at EPOCH and moves one
# second with each TickGate tick — a tick is one second of the mpv sources'
# playhead loop, so an anchor ages exactly as the playhead the tick reads.
# Frozen instead, every reading of a playing track was a "discontinuity" no
# real clock would produce.
GOLDEN_WALL = [EPOCH]


def use_virtual_wall(monkeypatch, clock: "VirtualClock") -> None:
    """Stamp anchors on `clock`, so an anchor ages as the scenario lets time pass."""
    from backend.core import audio_source
    monkeypatch.setattr(audio_source, "wall_time", lambda: EPOCH + clock.now)


class WireRecorder:
    """Stands in for WebSocketManager: keeps every envelope, minus its clock.

    Session ids are random (uuid4): each one is renamed `session-N` in the
    order it first appears, so a recording compares byte for byte and still
    says which envelopes are about the same session.
    """

    def __init__(self) -> None:
        self.envelopes: List[Dict[str, Any]] = []
        self._session_names: Dict[str, str] = {}

    def stable(self, payload: Any) -> Any:
        """`payload` (an envelope's data, or a REST state) with its session ids renamed."""
        payload = json.loads(json.dumps(payload))
        if not isinstance(payload, dict):
            return payload
        session = payload.get("session")
        if isinstance(session, dict) and "id" in session:
            session["id"] = self._name(session["id"])
        if "session_id" in payload:
            payload["session_id"] = self._name(payload["session_id"])
        return payload

    def _name(self, session_id: str) -> str:
        return self._session_names.setdefault(session_id, f"session-{len(self._session_names) + 1}")

    async def broadcast_dict(self, envelope: Dict[str, Any]) -> None:
        # Serialized now: a payload is a live dict the state machine may
        # mutate after broadcasting it.
        kept = {k: v for k, v in envelope.items() if k != "timestamp"}
        kept["data"] = self.stable(kept.get("data"))
        self.envelopes.append(json.loads(json.dumps(kept)))


class WireReader:
    """What a world reads off the wire (mixin): `self.machine` and
    `self.recorder` are the world's."""

    def state(self) -> Dict[str, Any]:
        return self.machine.get_current_state()

    def session(self) -> Optional[Dict[str, Any]]:
        return self.state()["session"]

    def phase(self) -> Optional[str]:
        session = self.session()
        return session["phase"] if session else None

    def active(self) -> bool:
        return self.session() is not None

    def playing(self) -> bool:
        return self.phase() == "playing"

    def buffering(self) -> bool:
        return self.phase() == "loading"

    def position_ms(self) -> Optional[int]:
        """Where the session's anchor puts the playhead now — the one formula
        every client uses (docs: "le fil", §2)."""
        from backend.core import audio_source
        session = self.session()
        anchor = session["position"] if session else None
        if anchor is None:
            return None
        ms = anchor["ms"]
        if session["phase"] == "playing":
            ms += (audio_source.wall_time() - anchor["at"]) * 1000 * anchor["rate"]
        if session["duration_ms"] is not None:
            ms = min(ms, session["duration_ms"])
        return max(0, int(ms))

    def envelopes(self, category: str, type_: str) -> List[Dict[str, Any]]:
        return [
            e for e in self.recorder.envelopes
            if e["category"] == category and e["type"] == type_
        ]

    def published(self) -> List[Dict[str, Any]]:
        """Every state broadcast, in order."""
        return [e["data"] for e in self.envelopes("source", "state")]

    def positions(self) -> List[Dict[str, Any]]:
        """Every playhead discontinuity broadcast alone, in order."""
        return [e["data"] for e in self.envelopes("source", "position")]

    def session_ends(self) -> List[str]:
        return [e["data"]["reason"] for e in self.envelopes("source", "session_ended")]

    def errors(self) -> List[str]:
        return [e["data"]["reason"] for e in self.envelopes("source", "error")]


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


class LiveProcessWatch:
    """The pidfd watch a daemon-held session opens (SESSION_DAEMON), on a
    daemon the scenario never kills: it never fires. The harness's systemd
    names pid 4242, which the real watch would find already gone."""

    def __init__(self, pid: int, on_exit: Any, *a: Any, **k: Any) -> None:
        self.pid = pid

    def close(self) -> None:
        return None


def make_settings(values: Optional[Dict[str, Any]] = None) -> Mock:
    """SettingsService reads answered from `values`, None for anything else.

    `dock.enabled_apps` is one the validator guarantees, so it carries the real
    default rather than None: the music library reads it at initialize.
    """
    store = {
        "audio.auto_stop_delay": 120,
        "dock.enabled_apps": list(DEFAULT_DOCK_APPS),
        **(values or {}),
    }
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
        # What else one second moves (an mpv double's playhead: EventMpv.elapse).
        self.clocks: List[Any] = []

    async def sleep(self, delay: float, *a: Any, **k: Any) -> None:
        await self._event.wait()
        self._event.clear()

    async def tick(self, times: int = 1) -> None:
        for _ in range(times):
            # The second passes, the loop reads the playhead it ends on, and
            # the playhead moves on for the next one: a reading a scenario
            # sets is the one the next tick reads.
            GOLDEN_WALL[0] += 1.0
            self._event.set()
            await settle()
            for clock in self.clocks:
                clock(1.0)


class VirtualClock:
    """Seconds that pass only when a scenario says so, for one module's sleep.

    Patched in as `AsyncioProxy(clock.sleep)`: a sleeper wakes when `advance()`
    carries the clock past its own due time, so a timer armed for 600 s and
    one re-armed for 10 s expire in the order and at the moment their delays
    say, and one disarmed meanwhile (its sleep cancelled) never does.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self._sleepers: List[tuple] = []
        self._seq = itertools.count()

    async def sleep(self, delay: float, *a: Any, **k: Any) -> None:
        if delay <= 0:
            await _real_sleep(0)
            return
        future = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (self.now + delay, next(self._seq), future))
        await future

    async def advance(self, seconds: float) -> None:
        """Let `seconds` pass, waking each sleeper at its due time and letting
        what it set in motion run before the next one wakes."""
        target = self.now + seconds
        await settle()
        while True:
            while self._sleepers and self._sleepers[0][2].done():
                heapq.heappop(self._sleepers)
            if not self._sleepers or self._sleepers[0][0] > target:
                break
            due, _, future = heapq.heappop(self._sleepers)
            self.now = due
            future.set_result(None)
            await settle()
        self.now = target
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


class EventMpv:
    """MpvSim (tests/mpv_sim.py) under the scenarios' old vocabulary.

    The scenarios speak the polled world the old code read — `props["pause"] =
    True`, `props["idle-active"] = True` before a load, `is_connected = False`
    — and are never rewritten. This adapter says what mpv announces in each of
    those worlds: a pause event, loads that end in `end-file reason=error`, a
    link lost. A file opens when time passes (`tick`), not at the load, which
    is what the old code saw too: nothing played before its next poll.
    """

    def __new__(cls, **props):
        from backend.tests.mpv_sim import MpvSim

        class _EventMpv(MpvSim):
            def __init__(self) -> None:
                super().__init__(auto_open=False)
                self.props = _WatchedProps(self)
                self.props.update(props)

            @property
            def is_connected(self) -> bool:
                return self._link is not None

            @is_connected.setter
            def is_connected(self, value: bool) -> None:
                if not value and self._link is not None:
                    link, self._link = self._link, None
                    self.playlist, self.current, self.opened = [], None, False
                    from backend.shared.mpv import LINK_LOST
                    self._dispatch({"event": LINK_LOST}, link)

            async def time_passes(self) -> None:
                if self.current is not None and not self.opened:
                    await self.opens()

            def elapse(self, seconds: float) -> None:
                """Time passing: a file that plays moves its playhead with
                it, up to its end (what comes after is the scenario's)."""
                if (
                    self.current is not None and self.opened and not self.paused
                    and not self.stalled and self.position is not None
                ):
                    self.position += seconds * self.speed
                    length = self._duration()
                    if length:
                        self.position = min(self.position, length)

        return _EventMpv()


class _WatchedProps(dict):
    """The scenarios' `props` dict, turned into what mpv would announce."""

    def __init__(self, mpv) -> None:
        super().__init__()
        self._mpv = mpv

    def __setitem__(self, name, value) -> None:
        super().__setitem__(name, value)
        if name == "pause":
            self._mpv._set_pause(bool(value))
        elif name == "idle-active" and value:
            # mpv idle through the load: every file fails to open.
            self._mpv.broken[""] = "loading failed"
        elif name in ("time-pos", "playback-time") and value is not None:
            self._mpv.position = value
        elif name == "duration":
            self._mpv.default_duration = value

    def update(self, *a, **k) -> None:
        for key, value in dict(*a, **k).items():
            self[key] = value


class Wire:
    """One scenario's record: the envelopes, and REST answers taken on demand."""

    def __init__(self, machine: AudioStateMachine, recorder: WireRecorder) -> None:
        self.machine = machine
        self.recorder = recorder
        self.rest: List[Dict[str, Any]] = []

    async def snapshot_rest(self) -> None:
        """What GET /api/audio/state answers right now."""
        await self.machine.refresh_active_view()
        await settle()
        self.rest.append(self.recorder.stable(self.machine.get_current_state()))

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
