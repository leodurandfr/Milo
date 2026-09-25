"""Mac sessions, driven through the roc-recv the unit was measured to run
(tests/mac_world.py; docs: source architecture, phase 3d).

What is asserted is what the wire says, and what Milō asked the journal for,
after a Mac or roc-recv did something. Each gap test was seen red on the code
before phase 3d for the reason its docstring gives.
"""
import asyncio

import pytest

from backend.core.models.audio_state import AudioSource
from backend.core.models.ws_events import SourceErrorReason
from backend.tests.golden.harness import settle
from backend.tests.mac_world import (
    AIR_IP, AIR_NAME, MINI_IP, MINI_NAME, PORT, MacWorld, connect_lines,
)


@pytest.fixture
async def world(monkeypatch):
    w = MacWorld(monkeypatch)
    yield w
    for gate in w.avahi_gates.values():
        gate.set()
    await w.source.shutdown()


# === A Mac streaming is a session; its name is what the card shows ===

async def test_a_mac_picking_milo_is_shown_by_its_bonjour_name(world):
    await world.select()
    await world.mac_streams(MINI_IP)
    assert world.active() and world.names() == [MINI_NAME]


async def test_a_mac_already_streaming_when_the_source_starts_is_shown(world):
    """Measured: a Mac streaming to the unit reattaches to a new roc-recv
    within its first milliseconds — before Milō follows the journal. The
    replay of the running process is what catches it."""
    world.streaming[MINI_IP] = PORT
    await world.select()
    assert world.active() and world.names() == [MINI_NAME]


async def test_the_mac_picking_another_output_ends_the_session(world):
    await world.select()
    await world.mac_streams(MINI_IP)
    await world.mac_leaves(MINI_IP)
    assert not world.active()
    assert world.errors() == []


async def test_a_mac_already_named_is_not_looked_up_again(world):
    """roc-recv can announce the same address again within one run; each
    lookup costs up to 6.8 s of avahi (measured), for a name already known."""
    await world.select()
    await world.mac_streams(MINI_IP)
    lookups = len(world.avahi_calls)
    world._log(connect_lines(MINI_IP))
    await settle()
    assert len(world.avahi_calls) == lookups
    assert world.names() == [MINI_NAME]


async def test_two_macs_are_one_session_until_the_last_leaves(world):
    await world.select()
    await world.mac_streams(MINI_IP)
    await world.mac_streams(AIR_IP)
    assert world.names() == [MINI_NAME, AIR_NAME]
    await world.mac_leaves(MINI_IP)
    assert world.active() and world.names() == [AIR_NAME]
    await world.mac_leaves(AIR_IP)
    assert not world.active()


# === The replay is the running roc-recv's, never an earlier one's (E28) ===

async def test_a_mac_that_left_while_the_source_was_off_is_not_shown(world):
    """E28, reproduced on the unit: roc-recv stopped under a live session
    (Milō switched to another source) writes no goodbye, the Mac then picked
    its own speakers, and the next selection replayed the previous run's
    connect line — "Audio reçu de …" from a Mac sending nothing, forever."""
    await world.select()
    await world.mac_streams(MINI_IP)
    await world.leave()
    world.stops_streaming(MINI_IP)
    await world.select()
    assert not world.active()
    assert world.names() == []


async def test_the_journal_is_read_from_the_running_process_on(world):
    """The bound itself: the feed replays what the running roc-recv logged,
    from its start, and nothing older."""
    await world.select()
    follow = world.follows[-1]
    assert follow.since_usec == world.started_usec


async def test_a_mac_that_left_while_roc_recv_restarted_is_not_shown(world):
    """Measured: killed, roc-recv comes back 5 s later under a new process
    that knows nothing of the old sessions, and a Mac that left meanwhile
    never gets a goodbye. The old source kept it on screen."""
    await world.select()
    await world.mac_streams(MINI_IP)
    await world.kill_roc_recv()
    world.stops_streaming(MINI_IP)
    await world.systemd_restarts_it()
    assert not world.active()


# === Selecting the source never waits on a name (E29) ===

async def test_selecting_the_source_never_waits_for_a_senders_name(world):
    """E29: the old start replayed the journal and resolved every sender it
    found before the transition could end — 6.3 s per Mac that no longer
    answers (measured: avahi-resolve gives up after 5 s), 7.45 s measured for
    one, and three over the 15 s budget. The start now starts roc-recv and
    nothing else."""
    world.streaming[MINI_IP] = PORT
    gate = world.name_is_slow(MINI_IP)
    selecting = asyncio.ensure_future(world.machine.transition_to_source(AudioSource.MAC))
    await settle()
    try:
        assert selecting.done()
        assert not world.state()["switching"]
    finally:
        gate.set()
        await selecting
    await settle()
    assert world.active() and world.names() == [MINI_NAME]


async def test_a_name_being_looked_up_never_delays_another_macs_departure(world):
    """The old monitor handled the journal one line at a time, name lookups
    included: the Mac mini leaving was not seen until the Air's name came
    back, 6.8 s on the owner's LAN."""
    await world.select()
    await world.mac_streams(MINI_IP)
    gate = world.name_is_slow(AIR_IP)
    await world.mac_streams(AIR_IP)
    await world.mac_leaves(MINI_IP)
    assert not world.active()
    gate.set()
    await settle()
    assert world.active() and world.names() == [AIR_NAME]


async def test_a_mac_that_leaves_while_being_named_is_never_shown(world):
    """The old source published the Mac once its name came back, then the idle
    state at its goodbye: a card for a Mac already gone."""
    await world.select()
    gate = world.name_is_slow(MINI_IP)
    await world.mac_streams(MINI_IP)
    await world.mac_leaves(MINI_IP)
    gate.set()
    await settle()
    assert not world.active()
    assert world.published(), "nothing was published — the assertion below would be empty"
    assert all(p["session"] is None for p in world.published())


# === roc-recv's death (SESSION_DAEMON) ===

async def test_roc_recv_dying_ends_the_session_with_a_banner(world):
    """Measured on the old code: SIGKILL mid-stream changed nothing at all —
    no log, no banner, ACTIVE throughout. The session belongs to the process
    holding it."""
    await world.select()
    await world.mac_streams(MINI_IP)
    await world.kill_roc_recv()
    assert not world.active()
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]


async def test_the_mac_reattaching_to_the_new_roc_recv_clears_the_banner(world):
    """Measured: the Mac reattaches to the restarted process by itself; the
    banner goes when it is back on screen — after the state that shows it,
    never before."""
    await world.select()
    await world.mac_streams(MINI_IP)
    await world.kill_roc_recv()
    await world.systemd_restarts_it()
    assert world.active() and world.names() == [MINI_NAME]
    assert world.cleared() == 1
    kinds = [(e["type"], e["data"]) for e in world.recorder.envelopes if e["category"] == "source"]
    at = [i for i, (kind, _) in enumerate(kinds) if kind == "error_cleared"][0]
    before = [data for kind, data in kinds[:at] if kind == "state"]
    assert before and before[-1]["session"]["senders"] == [MINI_NAME]


async def test_a_stop_systemd_was_asked_for_is_not_a_death(world):
    """A backend restart stops the source units first, while the backend
    runs: the exit is heard, and it is not a failure."""
    await world.select()
    await world.mac_streams(MINI_IP)
    world.unit_state = ("inactive", "success")
    world._die()
    await settle()
    assert not world.active()
    assert world.errors() == []


# === A multiroom toggle ===

async def test_a_multiroom_toggle_ends_the_session_and_the_mac_comes_back(world):
    """roc-recv writes to the output it was started on, so a toggle restarts
    it; the Mac, still streaming, reattaches to the new one by itself."""
    await world.select()
    await world.mac_streams(MINI_IP)
    await world.reroute()
    assert world.active() and world.names() == [MINI_NAME]
    assert world.errors() == []


async def test_a_mac_that_left_during_a_multiroom_toggle_is_not_shown(world):
    await world.select()
    await world.mac_streams(MINI_IP)
    world.stops_streaming(MINI_IP)
    await world.reroute()
    assert not world.active()


# === The journal follow and the start, when things go wrong ===

async def test_leaving_the_source_stops_reading_the_journal(world):
    """Left behind, the follow would keep reading the journal of a source
    nobody selected and publish its Macs into a state machine that moved on."""
    await world.select()
    await world.leave()
    assert world.follows and all(f.returncode is not None for f in world.follows)
    await world.mac_streams(MINI_IP)
    assert world.state()["source"] == "none" and world.session() is None


async def test_a_line_that_cannot_be_read_costs_only_that_line(world, monkeypatch, caplog):
    """Background-loop doctrine: one bad line must not end the follow, or no
    Mac is seen arriving or leaving again until the source is restarted."""
    from backend.sources.mac import source as mac_module
    real = mac_module.classify_line

    def classify(line):
        if "creating route" in line:
            raise ValueError("unreadable")
        return real(line)

    monkeypatch.setattr(mac_module, "classify_line", classify)
    await world.select()
    with caplog.at_level("ERROR", logger="source.mac"):
        await world.mac_streams(MINI_IP)
    assert world.active() and world.names() == [MINI_NAME]
    assert "unreadable" in caplog.text


async def test_a_journal_that_cannot_be_followed_is_reported(world, caplog):
    world.journal_refuses = True
    with caplog.at_level("ERROR", logger="source.mac"):
        await world.select()
    assert "cannot exec" in caplog.text


async def test_an_ordinary_switch_away_logs_no_error(world, caplog):
    """The follow is cancelled on every switch away from Mac; an error there
    would raise the UI banner on an ordinary switch."""
    await world.select()
    await world.mac_streams(MINI_IP)
    with caplog.at_level("ERROR", logger="source.mac"):
        await world.leave()
    assert [r for r in caplog.records if r.levelname == "ERROR"] == []


async def test_a_unit_that_will_not_start_fails_the_selection(world):
    world.systemd.start.side_effect = None
    world.systemd.start.return_value = False
    await world.select()
    assert world.state()["service"] == "failed"


async def test_a_unit_that_died_during_its_settle_fails_the_selection(world, caplog):
    """systemd acknowledges the start, then roc-recv exits in its first second
    (a port already bound): reporting started gives the Mac a receiver that is
    not listening."""
    world.systemd.is_active.side_effect = lambda *_: False
    with caplog.at_level("ERROR", logger="source.mac"):
        await world.select()
    assert world.state()["service"] == "failed"
    assert "not active after start" in caplog.text


async def test_without_a_start_time_the_journal_is_followed_from_now(world, caplog):
    """Fail open: systemd cannot say when roc-recv started, so nothing is
    replayed (an unbounded replay is E28) — and the source still works for
    every Mac arriving from now on."""
    world.systemd.main_start_usec.side_effect = RuntimeError("no system bus")
    with caplog.at_level("WARNING", logger="source.mac"):
        await world.select()
    assert world.follows[-1].since_usec is None
    await world.mac_streams(MINI_IP)
    assert world.active() and world.names() == [MINI_NAME]
    assert "following its journal from now" in caplog.text


async def test_stopping_a_source_that_never_started_still_stops_the_unit(world):
    assert await world.source.stop() is True
    world.systemd.stop.assert_awaited()


async def test_a_mac_being_named_when_roc_recv_died_is_not_shown_once_gone(world):
    """Its name was being looked up for a roc-recv that has since died; if
    it did not reattach to the new one, nothing may bring it on screen."""
    await world.select()
    await world.mac_streams(MINI_IP)
    gate = world.name_is_slow(AIR_IP)
    await world.mac_streams(AIR_IP)
    await world.kill_roc_recv()
    world.stops_streaming(AIR_IP)
    await world.systemd_restarts_it()
    gate.set()
    await settle()
    assert world.names() == [MINI_NAME]


# === A Mac belongs to the roc-recv that announced it (code review, phase 3d) ===

async def test_the_first_mac_named_after_roc_recv_died_is_not_shown_once_gone(world):
    """No session yet, so nothing watched roc-recv: the Mac's lookup outlived
    the process that announced it, the Mac left while roc-recv was down, and
    its name opened a session nobody would ever end."""
    await world.select()
    gate = world.name_is_slow(MINI_IP)
    await world.mac_streams(MINI_IP)
    await world.kill_roc_recv()
    world.stops_streaming(MINI_IP)
    await world.systemd_restarts_it()
    gate.set()
    await settle()
    assert not world.active()


async def test_a_line_received_after_its_roc_recv_exited_is_not_replayed(world):
    """journald stamps a stdout line when it receives it (no source time,
    measured), so under a flood a line the previous roc-recv wrote can land
    after the next one started — inside the time bound, from a process that
    no longer holds anything."""
    await world.select()
    old = world.pid
    await world.leave()
    await world.select()
    world.received_late(old, connect_lines(MINI_IP))
    await settle()
    assert not world.active()
    assert world.errors() == []
    await world.mac_streams(AIR_IP)
    assert world.names() == [AIR_NAME]


async def test_a_session_opened_while_systemd_is_slow_is_still_watched(world):
    """MainPID unreadable when the first Mac is named (systemctl timing out
    on a busy card): the session must still end with the roc-recv holding it."""
    await world.select()
    world.pid_unreadable = True
    await world.mac_streams(MINI_IP)
    world.pid_unreadable = False
    await world.kill_roc_recv()
    world.stops_streaming(MINI_IP)
    await world.systemd_restarts_it()
    assert not world.active()


async def test_a_mac_reopening_its_stream_is_not_dropped_by_the_old_ones_end(world):
    """roc-recv keys a session by address, port included (measured: 53721,
    then 58825 for the same Mac): a new stream can open before the watchdog
    ends the old one, and the old one's end must not take the Mac away."""
    await world.select()
    await world.mac_streams(MINI_IP, port=53721)
    lookups = len(world.avahi_calls)
    await world.mac_streams(MINI_IP, port=58825)
    assert world.names() == [MINI_NAME]
    assert len(world.avahi_calls) == lookups
    await world.mac_leaves(MINI_IP, port=53721)
    assert world.active() and world.names() == [MINI_NAME]
    await world.mac_leaves(MINI_IP, port=58825)
    assert not world.active()


async def test_a_name_asked_for_a_roc_recv_since_replaced_is_dropped(world):
    """No session yet, so no watch: the new roc-recv's first line is what says
    the old one is gone, and what it announced goes with it."""
    await world.select()
    gate = world.name_is_slow(MINI_IP)
    await world.mac_streams(MINI_IP)
    await world.kill_roc_recv()
    world.stops_streaming(MINI_IP)
    await world.systemd_restarts_it()
    await world.mac_streams(AIR_IP)
    gate.set()
    await settle()
    assert world.names() == [AIR_NAME]


async def test_a_session_whose_death_is_heard_late_ends_at_the_next_roc_recv(world):
    """Without pidfd the watch checks /proc every 10 s, and systemd brings a
    new roc-recv up in 5: its first line is then the first news of the
    death."""
    world.watch_is_late = True
    await world.select()
    await world.mac_streams(MINI_IP)
    await world.mac_streams(AIR_IP)
    await world.kill_roc_recv()
    world.stops_streaming(MINI_IP)
    await world.systemd_restarts_it()
    assert world.names() == [AIR_NAME]
    assert world.errors() == [SourceErrorReason.STREAM_DISCONNECTED]
