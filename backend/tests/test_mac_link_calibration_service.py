# backend/tests/test_mac_link_calibration_service.py
"""The Mac link analysis against a faked roc-recv journal and a faked ping.

What breaks when these fail: the analysis sizes the link from the wrong stream
(a previous roc-recv, a session that already ended), blames the wrong party for
a failure, or writes something — it must only ever propose.
"""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.core.mac_link import calibration_service as service_module
from backend.core.mac_link.calibration_service import MacLinkCalibrationService
from backend.core.multiroom.calibration_probe import CalibrationProbeError, PingReading

PID = 501123
OLD_PID = 499620
MAC = {"192.168.1.173": "Mac mini de Léo"}
STORED = {
    "target_latency_ms": 70, "latency_profile": "gradual", "frame_length_ms": 6,
    "packet_length_ms": 3, "fec_block_source": 10, "fec_block_repair": 5,
    "packet_interleaving": False,
}


def tuner(niq, stale=3.0):
    return (f"latency tuner: e2e_latency=3423(71.313ms) niq_latency=3000({niq}ms) "
            f"target_latency=3360(70.000ms) jitter=0(0.000ms) stale=100({stale}ms) fe=1.000027 eff_fe=1.000027")


SESSION_START = "session group: creating session: src_addr=192.168.1.173:57193 dst_addr=0.0.0.0:10001"
SESSION_END = "session group: removing session"
PAYLOAD = "fec reader: update payload size: next_esi=0 cur_size=0 new_size=540"
BLOCK = "fec reader: update source block size: cur_sblen=0 cur_rblen=0 new_sblen=10"
OUT_OF_BOUNDS = "latency tuner: latency out of bounds: latency=150 target=70 min=-3 max=143 stale=2"
NO_PLAYBACK = "watchdog: no_playback timeout reached: every frame was blank during timeout"


def session(n=12, niq=70.0):
    return [SESSION_START, PAYLOAD, BLOCK] + [tuner(niq + (i % 3) - 1) for i in range(n)]


def journal(lines, pid=PID):
    return [json.dumps({"MESSAGE": line, "_PID": str(pid)}) for line in lines]


@pytest.fixture
def world(monkeypatch):
    """A roc-recv journal that replays `entries` then stays open, as `-f` does."""
    state = SimpleNamespace(entries=journal(session()), feed_dies=False)

    async def fake_follow(unit, **kwargs):
        for entry in state.entries:
            yield entry
        if not state.feed_dies:
            await asyncio.sleep(3600)

    monkeypatch.setattr(service_module, "follow_unit", fake_follow)
    monkeypatch.setattr(service_module, "MEASURE_SECONDS", 0.05)
    state.ping = AsyncMock(return_value=PingReading(0.29, 0.37, 0.0, 0))
    monkeypatch.setattr(service_module, "ping", state.ping)
    monkeypatch.setattr(service_module, "sample_local_scheduling", AsyncMock(return_value=1.42))

    state.broadcast = AsyncMock()
    state.settings = SimpleNamespace(get_setting=AsyncMock(return_value=dict(STORED)), set_setting=AsyncMock())
    state.systemd = SimpleNamespace(main_pid=AsyncMock(return_value=PID),
                                    main_start_usec=AsyncMock(return_value=1_790_000_000_000_000),
                                    restart=AsyncMock())
    state.mac_source = SimpleNamespace(streaming_macs=dict(MAC), stream_count=1)
    state.service = MacLinkCalibrationService(
        state_machine=SimpleNamespace(broadcast=state.broadcast),
        settings_service=state.settings, systemd_manager=state.systemd, mac_source=state.mac_source,
    )
    return state


def last_event(world):
    return world.broadcast.await_args_list[-1].args[0]


async def test_a_measured_link_is_proposed_and_nothing_is_written(world):
    """The analysis only proposes: Apply is the panel's, through PUT /mac-roc.
    Writing here would restart roc-recv and rebuild the Mac's device mid-song."""
    await world.service._run()

    event = last_event(world)
    assert event.TYPE == "mac_calibration_result"
    assert event.config["target_latency_ms"] == 30
    assert event.measurements["samples"] == 12
    assert world.service.last_result["config"] == event.config
    world.settings.set_setting.assert_not_awaited()
    world.systemd.restart.assert_not_awaited()


async def test_no_mac_streaming_is_refused_before_anything_is_measured(world):
    world.mac_source.streaming_macs = {}
    await world.service._run()

    assert last_event(world).reason == "no_mac"
    world.ping.assert_not_awaited()


async def test_two_macs_are_refused_and_named(world):
    """roc-recv's statistics carry no session: two Macs' samples interleave."""
    world.mac_source.streaming_macs = {**MAC, "192.168.1.60": "MacBook Pro"}
    await world.service._run()

    event = last_event(world)
    assert event.reason == "several_macs"
    assert "MacBook Pro" in event.detail and "Mac mini de Léo" in event.detail


async def test_only_the_running_roc_recv_and_its_current_session_are_read(world):
    """A previous roc-recv's lines (journald can deliver them late) and a
    session that already ended describe streams that are gone: sized from them,
    the proposal would answer for a link nobody is playing on."""
    ended_session = journal([SESSION_START] + [tuner(20.0)] * 12 + [NO_PLAYBACK, SESSION_END])
    current = journal(session(niq=70.0))
    late_from_the_previous_process = journal([tuner(10.0)] * 4, pid=OLD_PID)
    world.entries = ended_session + current[:4] + late_from_the_previous_process + current[4:]

    await world.service._run()

    measured = last_event(world).measurements
    assert measured["samples"] == 12
    assert measured["queue_min_ms"] == 69.0


async def test_an_out_of_bounds_end_is_counted_and_an_ordinary_departure_is_not(world):
    """`no_playback` also ends every session whose Mac simply left; only the
    ends that mean "too tight" say something about the running configuration."""
    world.entries = journal(
        [SESSION_START, tuner(70.0), OUT_OF_BOUNDS, SESSION_END]
        + [SESSION_START, tuner(70.0), NO_PLAYBACK, SESSION_END]
        + session()
    )
    await world.service._run()

    assert last_event(world).measurements["unstable_ends"] == 1


async def test_a_session_too_young_to_size_is_refused(world):
    world.entries = journal(session(n=5))
    await world.service._run()

    event = last_event(world)
    assert event.reason == "too_few_samples"
    assert event.detail == "Mac mini de Léo"


async def test_a_mac_that_answers_no_ping_is_named_for_its_owner(world):
    """macOS's stealth mode drops every probe: the panel names the Mac and says
    what to switch off, rather than printing an address."""
    world.ping.side_effect = CalibrationProbeError("no ICMP reply from 192.168.1.173")
    await world.service._run()

    event = last_event(world)
    assert (event.reason, event.detail) == ("no_ping_reply", "Mac mini de Léo")


async def test_one_analysis_at_a_time_and_forget_drops_the_proposal(world):
    assert world.service.start() is True
    assert world.service.start() is False
    assert world.service.progress["expected_seconds"] > 0
    await world.service.cleanup()

    await world.service._run()
    assert world.service.last_result is not None
    world.service.forget()
    assert world.service.last_result is None


async def test_an_apply_during_the_minute_fails_the_run_instead_of_sizing_a_dead_stream(world):
    """roc-recv restarted meanwhile (an Apply from another device): its new
    lines carry another pid and are skipped, so the reading would describe the
    stream that ended — and be compared with the settings that replaced it."""
    world.systemd.main_pid = AsyncMock(side_effect=[PID, PID + 1])
    await world.service._run()

    assert last_event(world).reason == "receiver_restarted"


async def test_a_journal_that_stops_is_a_failed_read_not_a_short_session(world):
    """Reported as too few samples, it told the user the Mac had not streamed
    long enough — and every retry failed the same way."""
    world.feed_dies = True
    await world.service._run()

    assert last_event(world).reason == "journal_unreadable"


async def test_a_mac_with_two_streams_open_is_measured_later(world):
    """A Mac reopening its stream holds two for a moment; both sessions'
    samples would land in one reading."""
    world.mac_source.stream_count = 2
    await world.service._run()

    assert last_event(world).reason == "two_streams"
    world.ping.assert_not_awaited()


async def test_only_a_post_fec_loss_during_the_last_minutes_counts(world):
    """A loss hours ago leaves the cumulative ratio above zero for the rest of
    the session; sized from it, a clean link took a whole FEC block of latency."""
    loss = lambda ratio: f"depacketizer: ts=1 loss_ratio={ratio:.5f}"
    world.entries = journal(session() + [loss(0.00045)] * 3)
    await world.service._run()
    steady = last_event(world).config["target_latency_ms"]

    world.entries = journal(session() + [loss(0.00045), loss(0.00045), loss(0.00060)])
    await world.service._run()
    assert last_event(world).config["target_latency_ms"] > steady


async def test_a_second_mac_arriving_during_the_minute_is_caught(world):
    """Checked only before the minute, a Mac joining meanwhile sent its queue
    samples into the reading of the first."""
    class ASecondMacArrives:
        stream_count = 1

        def __init__(self):
            self._answers = iter([dict(MAC), {**MAC, "192.168.1.60": "MacBook Pro"}])

        @property
        def streaming_macs(self):
            return next(self._answers)

    service = MacLinkCalibrationService(
        state_machine=SimpleNamespace(broadcast=world.broadcast), settings_service=world.settings,
        systemd_manager=world.systemd, mac_source=ASecondMacArrives(),
    )
    await service._run()
    assert last_event(world).reason == "several_macs"


async def test_the_stretch_where_two_sessions_overlap_is_dropped(world):
    """Between a second session's start and the first one's end, both streams'
    samples arrive together; the reading keeps only what follows."""
    overlap = [SESSION_START] + [tuner(10.0)] * 4 + [NO_PLAYBACK, SESSION_END]
    world.entries = journal(session(n=4) + overlap + [tuner(70.0 + (i % 3) - 1) for i in range(12)])
    await world.service._run()

    measured = last_event(world).measurements
    assert measured["samples"] == 12
    assert measured["queue_min_ms"] == 69.0
