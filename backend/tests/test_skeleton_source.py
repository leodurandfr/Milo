# backend/tests/test_skeleton_source.py
"""A whole source in one file: the shape a new source starts from.

docs/development.md § "Adding a new audio source" points here rather than
sketching one, because this source runs: a sketch that cannot run is a sketch
nothing tells has gone stale. It goes through what every source does — it opens
a session when it starts playing something, it publishes its view when the view
moves, and it takes commands from `POST /api/audio/control/{source}` — against a
real AudioStateMachine, so the assertions read the state every client reads.

`Player` stands for the program that makes the sound (mpv, a daemon): the source
asks it to do things, and only what it announces back moves the phase. The
source borrows the radio's identifier, because the state machine knows sources
by the `AudioSource` enum; a real source adds its own value there first.

What breaks when this fails: the base class's contract with a new source — the
session model, the one publisher, the command boundary — changed under the guide
that tells contributors how to write one.
"""
import json
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import pytest
from pydantic import BaseModel, Field

from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import AudioSource, NetworkRequirement
from backend.core.models.session import (
    CommandScope,
    EndReason,
    IdlePolicy,
    Phase,
    PhaseEvent,
    ResumePolicy,
    ReroutePolicy,
    Session,
)
from backend.core.state import AudioStateMachine
from backend.tests.golden.harness import settle


class PlayItemParams(BaseModel):
    """Params for `play_item`. A real source keeps these in its `models.py`."""
    title: str = Field(min_length=1)


class Player:
    """The outside world: what the source drives, and what talks back.

    `announce` is how a player's callback reaches the source — from another
    task, so it posts and never touches the source itself.
    """

    def __init__(self) -> None:
        self.requests: List[tuple] = []
        self.listener: Optional[Callable[[PhaseEvent], None]] = None

    async def play(self, title: str) -> None:
        self.requests.append(("play", title))

    async def pause(self) -> None:
        self.requests.append(("pause",))

    async def resume(self) -> None:
        self.requests.append(("resume",))

    def announce(self, event: PhaseEvent) -> None:
        self.listener(event)


@dataclass(eq=False)
class SkeletonSession(Session):
    """Everything one listening session owns. It dies with the session."""
    title: str = ""


class SkeletonSource(BaseAudioSource):
    NETWORK_REQUIREMENT = NetworkRequirement.NONE
    IDLE_POLICY = IdlePolicy.AUTO_STOP
    REROUTE = ReroutePolicy.END_SESSION
    # Nothing is kept to resume: every end forgets what played.
    RESUME_POLICY = ResumePolicy(capture_on=frozenset(), forget_on=frozenset(EndReason))
    SESSION_DAEMON = False

    COMMANDS = {"play_item": PlayItemParams, "pause": None, "resume": None}
    COMMAND_SCOPES = {
        "play_item": CommandScope.CONTENT,
        "pause": CommandScope.SESSION,
        "resume": CommandScope.SESSION,
    }

    def __init__(self, config, state_machine, settings_service, systemd_manager):
        super().__init__(
            source_id=AudioSource.RADIO.value,
            service_name="milo-skeleton.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config,
        )
        self.player = Player()
        self.player.listener = self._post_feed

    async def _do_start(self) -> bool:
        # A source with a unit of its own starts it here:
        # `return await self._start_service_and_wait()`.
        return True

    async def _do_stop(self) -> bool:
        # Another source was picked: the state machine publishes what follows.
        await self.end_session(EndReason.SOURCE_SWITCH)
        return True

    async def _do_release(self) -> bool:
        # A multiroom toggle moves the output: REROUTE = END_SESSION says the
        # session ends, and for that reason, not as a switch to another source.
        await self.end_session(EndReason.REROUTE)
        self._publish()
        return True

    async def _do_acquire(self) -> bool:
        return True

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        if cmd == "play_item":
            if self._session is not None:
                await self.end_session(EndReason.USER_STOP)
            self.open_session(SkeletonSession(phase=Phase.LOADING, title=params.title))
            await self.player.play(params.title)
            self._publish()
            return self.success_response(f"Loading {params.title}")
        # The phase moves when the player announces it, never on a command's word.
        if cmd == "pause":
            await self.player.pause()
            return self.success_response()
        if cmd == "resume":
            await self.player.resume()
            return self.success_response()
        return self.error_response(f"Unhandled command: {cmd}")

    async def _handle_feed(self, events: List[PhaseEvent]) -> None:
        for event in events:
            if self._session is not None:
                self._session.advance(event)
        self._publish_changes()

    def _session_fields(self, session: SkeletonSession) -> Dict[str, Any]:
        return {"title": session.title}

    def _controls(self) -> List[str]:
        """The exact commands that would act now, so a client draws only those."""
        session = self._session
        if session is None:
            return []
        return {Phase.PLAYING: ["pause"], Phase.PAUSED: ["resume"]}.get(session.phase, [])


class Wire:
    """Stands in for the WebSocket manager: every envelope the machine sends."""

    def __init__(self) -> None:
        self.envelopes: List[Dict[str, Any]] = []

    async def broadcast_dict(self, envelope: Dict[str, Any]) -> None:
        self.envelopes.append(json.loads(json.dumps(envelope)))


@pytest.fixture
async def world():
    machine = AudioStateMachine()
    machine.ws_manager = Wire()
    source = SkeletonSource({}, machine, None, None)
    machine.register_source(AudioSource.RADIO, source)
    assert await machine.transition_to_source(AudioSource.RADIO) is True
    await settle()
    yield machine, source
    await source.shutdown()


def _sent(machine, category, type_):
    return [e["data"] for e in machine.ws_manager.envelopes
            if (e["category"], e["type"]) == (category, type_)]


async def test_a_started_source_offers_nothing_before_it_plays(world):
    machine, _ = world
    state = machine.get_current_state()

    assert (state["source"], state["service"], state["session"], state["controls"]) == (
        "radio", "running", None, [],
    )


async def test_a_command_opens_a_session_that_loads_until_the_player_says_it_plays(world):
    machine, source = world

    result = await source.command("play_item", {"title": "Blue in Green"})
    await settle()

    assert result["success"] is True
    assert source.player.requests == [("play", "Blue in Green")]
    session = machine.get_current_state()["session"]
    assert (session["phase"], session["title"]) == ("loading", "Blue in Green")

    source.player.announce(PhaseEvent.SOUND_STARTED)
    await settle()

    state = machine.get_current_state()
    assert (state["session"]["phase"], state["controls"]) == ("playing", ["pause"])
    assert _sent(machine, "source", "state")[-1] == state


async def test_pause_is_asked_of_the_player_and_the_phase_waits_for_its_answer(world):
    machine, source = world
    await source.command("play_item", {"title": "Blue in Green"})
    source.player.announce(PhaseEvent.SOUND_STARTED)
    await settle()

    await source.command("pause", None)
    await settle()
    assert source.player.requests[-1] == ("pause",)
    assert machine.get_current_state()["session"]["phase"] == "playing"

    source.player.announce(PhaseEvent.PAUSED)
    await settle()
    state = machine.get_current_state()
    assert (state["session"]["phase"], state["controls"]) == ("paused", ["resume"])


async def test_the_command_boundary_refuses_what_cannot_run(world):
    """Before `_handle_command`: a body that does not validate, and a session
    command with no session — both a 400 on the control route."""
    _, source = world

    assert (await source.command("play_item", {"title": ""}))["success"] is False
    assert (await source.command("pause", None))["success"] is False
    assert source.player.requests == []


async def test_a_multiroom_toggle_ends_the_session_as_a_reroute(world):
    machine, source = world
    await source.command("play_item", {"title": "Blue in Green"})
    await settle()

    async def switch_output() -> None:
        pass

    await machine.reroute_active_source(switch_output)
    await settle()

    assert _sent(machine, "source", "session_ended")[-1]["reason"] == "reroute"
    assert machine.get_current_state()["session"] is None


async def test_a_stop_announces_the_end_before_the_state_it_leads_to(world):
    machine, source = world
    await source.command("play_item", {"title": "Blue in Green"})
    await settle()
    session_id = machine.get_current_state()["session"]["id"]

    await machine.transition_to_source(AudioSource.NONE)
    await settle()

    kinds = [(e["category"], e["type"]) for e in machine.ws_manager.envelopes]
    ended = kinds.index(("source", "session_ended"))
    assert machine.ws_manager.envelopes[ended]["data"] == {
        "source": "radio", "session_id": session_id, "reason": "source_switch",
    }
    assert ("source", "state") in kinds[ended + 1:]
    assert machine.get_current_state()["session"] is None
