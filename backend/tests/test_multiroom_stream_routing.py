"""Which snapserver stream each room plays, across a source switch and a toggle.

What breaks when these fail: in multiroom every source writes its own Loopback
substream and snapserver captures each as a named stream. Left to the `Multiroom`
meta stream, the switch was decided by snapserver alone, in the meta's order: a
source further down the list stayed muted in every room until the previous one
had been silent for `idle_threshold` (5 s) — measured 2.98 s of Radio lost after
Spotify, with the screen showing it playing. The backend now points every group
at the stream of the source it starts, before the start, so the first samples
reach the rooms.

The outside world is the fake snapserver of `tests/snapserver_world.py`, on an
ephemeral loopback port and knowing exactly the streams `provisioning/snapcast.sh`
declares. Everything else is the real thing: `SnapcastService`, the control
WebSocket every request rides (`SnapcastWebSocketService`),
`AudioRoutingService`, `AudioStateMachine`.
"""
import logging
from unittest.mock import AsyncMock, Mock

import pytest

from backend.core.models.audio_state import AudioSource, NetworkRequirement
from backend.core.models.audio_wire import SourceView
from backend.core.multiroom.routing import AudioRoutingService
from backend.core.multiroom.snapcast import SnapcastService
from backend.core.multiroom.websocket import SnapcastWebSocketService
from backend.core.state import AudioStateMachine
from backend.tests.conftest import free_mailbox
from backend.tests.snapserver_world import FakeSnapserver, serving


@pytest.fixture
async def snapserver():
    async with serving(FakeSnapserver()) as fake:
        yield fake


def _source(name, journal):
    """A source whose start and reacquire are written to the snapserver's journal."""
    source = Mock()
    source.is_initialized = True

    async def start():
        journal.append(("start", name))
        return True

    async def acquire():
        journal.append(("acquire", name))
        return True

    source.start = AsyncMock(side_effect=start)
    source.stop = AsyncMock(return_value=True)
    source.release_for_reroute = AsyncMock(return_value=True)
    source.acquire_after_reroute = AsyncMock(side_effect=acquire)
    source.hold_mailbox = free_mailbox()
    source.view = SourceView()
    source.availability = Mock(return_value=None)
    source.NETWORK_REQUIREMENT = NetworkRequirement.NONE
    return source


@pytest.fixture
async def appliance(snapserver, mock_settings_service):
    systemd = Mock()
    systemd.start = AsyncMock(return_value=True)
    systemd.stop = AsyncMock(return_value=True)
    systemd.is_active = AsyncMock(return_value=True)
    snapcast = SnapcastService(systemd_manager=Mock(), host="127.0.0.1", port=snapserver.port)
    routing = AudioRoutingService(
        settings_service=mock_settings_service, systemd_manager=systemd,
        snapcast_service=snapcast,
    )
    routing._initial_detection_done = True
    mock_settings_service._storage["routing.multiroom_enabled"] = True

    state_machine = AudioStateMachine()
    state_machine.ALSA_RELEASE_SETTLE_S = 0
    state_machine.routing_service = routing
    routing.set_state_machine(state_machine)
    for name in (AudioSource.SPOTIFY, AudioSource.RADIO, AudioSource.PODCAST):
        state_machine.register_source(name, _source(name, snapserver.journal))

    control = SnapcastWebSocketService(
        state_machine=state_machine, routing_service=routing,
        host="127.0.0.1", port=snapserver.port, snapcast_service=snapcast,
    )
    snapcast.set_control_channel(control)
    routing.set_snapcast_websocket_service(control)
    await control.initialize()
    try:
        assert await control.wait_for_ready(timeout=5.0)
        snapserver.journal.clear()  # the connect sweep's reads
        yield state_machine, routing, mock_settings_service
    finally:
        await control.cleanup()


def _set_streams(journal):
    return [(i, e[2]["stream_id"]) for i, e in enumerate(journal) if e[:2] == ("rpc", "Group.SetStream")]


async def test_a_multiroom_switch_points_every_group_at_the_target_stream(snapserver, appliance):
    """Spotify then Radio: every room ends on Radio's stream, moved before Radio starts."""
    state_machine, _, _ = appliance

    assert await state_machine.transition_to_source(AudioSource.SPOTIFY)
    assert await state_machine.transition_to_source(AudioSource.RADIO)

    assert [g["stream_id"] for g in snapserver.groups] == ["Radio"] * 3
    radio_started = snapserver.journal.index(("start", AudioSource.RADIO))
    moves = [i for i, stream in _set_streams(snapserver.journal) if stream == "Radio"]
    assert len(moves) == 3
    assert all(i < radio_started for i in moves)


async def test_a_group_already_on_the_target_is_not_moved(snapserver, appliance):
    """A room another path already pointed (its admission) costs no request, and
    snapserver re-sends nothing to it."""
    state_machine, _, _ = appliance
    snapserver.groups[1]["stream_id"] = "Radio"

    assert await state_machine.transition_to_source(AudioSource.RADIO)

    assert [g["stream_id"] for g in snapserver.groups] == ["Radio"] * 3
    assert len(_set_streams(snapserver.journal)) == 2


async def test_direct_mode_sends_no_set_stream(snapserver, appliance):
    """In direct mode snapserver is not the output (and is stopped): not one request."""
    state_machine, _, settings = appliance
    settings._storage["routing.multiroom_enabled"] = False

    assert await state_machine.transition_to_source(AudioSource.SPOTIFY)
    assert await state_machine.transition_to_source(AudioSource.RADIO)

    assert [e for e in snapserver.journal if e[0] == "rpc"] == []
    assert ("start", AudioSource.RADIO) in snapserver.journal


async def test_selecting_none_leaves_the_groups_alone(snapserver, appliance):
    """No source has no stream: the rooms stay where the last one put them."""
    state_machine, _, _ = appliance
    assert await state_machine.transition_to_source(AudioSource.RADIO)
    before = len(snapserver.journal)

    assert await state_machine.transition_to_source(AudioSource.NONE)

    assert [e for e in snapserver.journal[before:] if e[0] == "rpc"] == []
    assert [g["stream_id"] for g in snapserver.groups] == ["Radio"] * 3


async def test_a_refused_set_stream_does_not_fail_the_switch(snapserver, appliance, caplog):
    """The source still starts and the state says so: refusing the selection
    over a stream snapserver kept would be a source the user cannot pick. The
    refusal is in the journal, and the next admission or switch repoints."""
    state_machine, _, _ = appliance
    snapserver.refuse["Group.SetStream"] = None

    with caplog.at_level(logging.WARNING):
        assert await state_machine.transition_to_source(AudioSource.RADIO)

    assert ("start", AudioSource.RADIO) in snapserver.journal
    assert state_machine.system_state.service_error is None
    assert any("Radio" in r.message and r.levelno == logging.WARNING for r in caplog.records)


async def test_a_wedged_snapserver_costs_the_switch_its_bound_only(snapserver, appliance):
    """Each JSON-RPC call carries 3 s, and a transition has 15 s for both
    sources' stop and start: the hook is bounded as a whole, and past it the
    source starts anyway."""
    state_machine, routing, _ = appliance
    routing.STREAM_POINT_BUDGET_S = 0.05
    snapserver.hang = True

    assert await state_machine.transition_to_source(AudioSource.RADIO)

    assert ("start", AudioSource.RADIO) in snapserver.journal


async def test_enabling_multiroom_points_the_groups_at_the_carried_source_before_acquire(
    snapserver, appliance
):
    """The groups come back from snapserver's own state on whatever stream they
    last had; the source carried across the toggle must be what they play once
    it is reacquired. The persisted mode is still direct while the output
    switches, so this pointing cannot go through the direct-mode gate."""
    state_machine, routing, settings = appliance
    settings._storage["routing.multiroom_enabled"] = False
    await routing.snapcast_websocket_service.stop_connection()
    state_machine.system_state.active_source = AudioSource.RADIO
    snapserver.set_streams("Spotify")

    assert await routing.set_multiroom_enabled(True)

    assert [g["stream_id"] for g in snapserver.groups] == ["Radio"] * 3
    acquired = snapserver.journal.index(("acquire", AudioSource.RADIO))
    moves = [i for i, _ in _set_streams(snapserver.journal)]
    assert len(moves) == 3
    assert all(i < acquired for i in moves)


async def test_a_stream_snapserver_does_not_know_is_a_refusal(snapserver):
    """snapserver answers a JSON-RPC error; the verdict carries it."""
    snapcast = SnapcastService(systemd_manager=Mock())
    snapcast.set_control_channel(snapserver.control_channel())

    assert await snapcast.point_groups_at("DLNA") is False
    assert [g["stream_id"] for g in snapserver.groups] == ["Multiroom"] * 3
