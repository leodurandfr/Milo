# backend/tests/test_calibration_probe.py
"""Guard how the fleet is measured.

The model is only as good as these readings, and the failure that matters is
not a crash: it is a client quietly left out. One buffer is sized for the whole
house from the worst link in it, so a speaker that was never weighed is a link
that might have been the weak one.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.core.multiroom import calibration_probe as probe_module
from backend.core.multiroom.calibration_probe import (
    ASSUMED_LINK,
    CalibrationProbeError,
    CalibrationProbeService,
    NoRemoteClientError,
)


def _client(name, ip, is_local=False):
    return SimpleNamespace(mac_id=f"mac-{name}", name=name, ip=ip, is_local=is_local)


class _Registry:
    def __init__(self, clients):
        self._clients = clients

    def get_online_clients(self):
        return self._clients


def _probe_payload(kind="ethernet", speed=1000, sched_max=0.4):
    return {
        "hostname": "milo-client",
        "link": {"interface": "eth0", "kind": kind, "speed_mbps": speed, "signal_dbm": None},
        "scheduling": {"p50_ms": 0.05, "p99_ms": 0.1, "max_ms": sched_max, "samples": 400},
        "unavailable": [],
    }


@pytest.fixture
def fast_local_sample():
    with patch.object(probe_module, "_sample_local_scheduling", new=AsyncMock(return_value=0.2)):
        yield


@pytest.fixture
def healthy_ping():
    with patch.object(probe_module, "_ping", new=AsyncMock(return_value=(0.3, 1.2, 0.0))):
        yield


async def test_a_fleet_that_answers_is_read_without_assumptions(fast_local_sample, healthy_ping):
    """The nominal path: every field comes from a measurement, so `assumed` is
    empty and the UI has nothing to qualify."""
    service = CalibrationProbeService(_Registry([
        _client("Canapé", "10.0.0.2"), _client("Milō", "127.0.0.1", is_local=True),
    ]))

    with patch.object(probe_module, "_fetch_probe", new=AsyncMock(return_value=_probe_payload())):
        readings, assumed = await service.measure()

    assert assumed == []
    assert {r.name for r in readings} == {"Canapé", "Milō"}
    assert [r.is_local for r in readings].count(True) == 1


async def test_one_unreachable_speaker_fails_the_whole_analysis(fast_local_sample, healthy_ping):
    """The rule this module exists for.

    Reporting the other speakers and quietly dropping this one would produce a
    confident proposal sized against links that were never the constraint. The
    failure names the client so the user can act on it.
    """
    service = CalibrationProbeService(_Registry([
        _client("Canapé", "10.0.0.2"), _client("Bureau", "10.0.0.3"),
    ]))

    async def one_fails(session, ip):
        if ip == "10.0.0.3":
            raise CalibrationProbeError("connection refused")
        return _probe_payload()

    with patch.object(probe_module, "_fetch_probe", new=AsyncMock(side_effect=one_fails)):
        with pytest.raises(CalibrationProbeError, match="Bureau"):
            await service.measure()


async def test_a_satellite_that_cannot_time_itself_is_refused_not_defaulted(
    fast_local_sample, healthy_ping
):
    """Scheduling decides the client's ALSA buffer, which is the largest single
    term in the budget. Substituting a default would hand the model a number
    nothing measured, on the one machine that failed to measure itself."""
    service = CalibrationProbeService(_Registry([_client("Canapé", "10.0.0.2")]))
    payload = _probe_payload()
    payload["scheduling"] = {}

    with patch.object(probe_module, "_fetch_probe", new=AsyncMock(return_value=payload)):
        with pytest.raises(CalibrationProbeError, match="Canapé"):
            await service.measure()


async def test_an_unnamed_link_is_assumed_wireless_and_declared(fast_local_sample, healthy_ping):
    """An unreadable link must default to the pessimistic reading and say so.

    Guessing Ethernet would give the optimistic answer on precisely the unit the
    server knows least about, and the optimistic direction is the one that costs
    audio rather than latency.
    """
    service = CalibrationProbeService(_Registry([_client("Canapé", "10.0.0.2")]))

    with patch.object(probe_module, "_fetch_probe",
                      new=AsyncMock(return_value=_probe_payload(kind=None, speed=None))):
        readings, assumed = await service.measure()

    assert readings[0].link == ASSUMED_LINK
    assert "Canapé.link" in assumed and "Canapé.link_speed_mbps" in assumed


async def test_a_house_with_only_the_local_speaker_raises_its_own_error(fast_local_sample):
    """"Nothing here to tune" and "a speaker did not answer" are opposite facts.

    Both used to raise CalibrationProbeError, so a local-only system was told a
    speaker could not be measured -- and the string written for this case could
    never appear.
    """
    service = CalibrationProbeService(_Registry([_client("Milō", "127.0.0.1", is_local=True)]))

    with pytest.raises(NoRemoteClientError):
        await service.measure()

    assert not issubclass(NoRemoteClientError, CalibrationProbeError)


async def test_a_host_that_answers_no_probe_is_an_error_not_a_hundred_percent_loss():
    """100% loss and "never measured" are different facts.

    Fed to the model the first is a very bad link, which it would dutifully size
    a three-second buffer for; the second means the reading is absent and no
    configuration should be offered at all.
    """
    with patch.object(probe_module.asyncio, "create_subprocess_exec", new=AsyncMock(
            return_value=SimpleNamespace(
                communicate=AsyncMock(return_value=(b"0 packets received", b"")),
                kill=lambda: None))):
        with pytest.raises(CalibrationProbeError, match="no ICMP reply"):
            await probe_module._ping("10.0.0.9")


async def test_a_fast_failure_cancels_the_slow_probe_beside_it():
    """`asyncio.gather` leaves the sibling running when one coroutine raises.

    Here that sibling is a 30-second ping. Abandoned, it finished long after the
    analysis had reported its failure, with nobody left to read its exception --
    which asyncio logs at ERROR, and the WS log handler turns into a second,
    unexplained banner on the user's screen.
    """
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(30)
        return "never"

    async def fails_fast():
        await started.wait()
        raise CalibrationProbeError("refused")

    slow_coro = slow()
    with pytest.raises(CalibrationProbeError):
        await probe_module._first_failure_cancels(slow_coro, fails_fast())

    # Nothing is left pending: a survivor would raise its own exception later,
    # into a run that has already ended.
    assert [task for task in asyncio.all_tasks() if not task.done()
            and task is not asyncio.current_task()] == []


class TestTheServiceLifecycle:
    """What a client that joins mid-run is told.

    Progress and the result arrive as WS deltas and deltas are never replayed,
    so everything a late client needs has to survive in the refetch.
    """

    def _service(self):
        from backend.core.multiroom.calibration_service import CalibrationService
        return CalibrationService(state_machine=None, probe_service=None)

    def test_a_run_drops_the_previous_proposal_before_it_starts(self):
        """Held through the run, a refetch answered `running: true` beside the
        *previous* proposal — and the panel staged that stale configuration,
        spending the one staging the fresh result was waiting for."""
        service = self._service()
        service._last_result = {"config": {"buffer_ms": 700}}

        service.start()

        assert service.running is True
        assert service.last_result is None

    def test_a_proposal_nobody_applied_can_be_forgotten(self):
        """The result is evidence for a configuration that was never written.
        Held here, it comes back on the refetch every panel open performs — a
        table of measurements over a setting the unit does not run, which the
        panel had already dropped on its own side."""
        service = self._service()
        service._last_result = {"config": {"buffer_ms": 700}}

        service.forget()

        assert service.last_result is None

    def test_forgetting_does_not_stop_a_run(self):
        """One client walking away is not a reason to stop measuring for the
        others — and the run's own result lands as usual afterwards."""
        service = self._service()
        service.start()

        service.forget()

        assert service.running is True

    def test_an_idle_service_reports_no_elapsed_time(self):
        """A bar drawn from a stale elapsed would start part-filled over an
        analysis that has not begun."""
        assert self._service().progress["elapsed_seconds"] == 0.0

    def test_a_running_service_reports_what_the_bar_needs(self):
        """The duration only ever travelled on the progress event, which a tab
        loading mid-run has already missed."""
        service = self._service()
        service.start()

        progress = service.progress

        assert progress["expected_seconds"] > 0
        assert progress["elapsed_seconds"] >= 0
