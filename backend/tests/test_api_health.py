# backend/tests/test_api_health.py
"""Tests for ``GET /api/health``.

The route is a single document assembled from five independent probes, and both
things this file pins are composition faults rather than probe faults:

* a probe that raises must not make a *later* probe report a failure of its own —
  the routing probe's result is read by the snapcast probe, so an unbound name
  there surfaced as a snapcast error and hid the real cause;
* the overall ``status`` is the worst verdict any probe reached, so a later
  ``degraded`` may never overwrite an ``unhealthy`` already posted.

Consumer: any external monitor polling /api/health, plus the operator reading it
by hand on a unit.
"""
import asyncio

import pytest
from unittest.mock import Mock, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.health import create_health_router


def _services(*, routing_raises=False, state_machine_raises=False,
              multiroom_enabled=False, multiroom_available=True,
              ws_connected=True, camilladsp_connected=True,
              snapcast_status_raises=False, camilladsp_status=None,
              camilladsp_raises=False, sources=None):
    """The five collaborators the router probes, each independently breakable."""
    state_machine = Mock()
    if state_machine_raises:
        state_machine.get_current_state = Mock(side_effect=RuntimeError("state machine down"))
    else:
        state_machine.get_current_state = Mock(
            return_value={"active_source": "radio", "transitioning": False}
        )
    state_machine.sources = sources if sources is not None else {}

    routing = Mock()
    if routing_raises:
        routing.get_state = Mock(side_effect=RuntimeError("routing down"))
    else:
        routing.get_state = Mock(return_value={"multiroom_enabled": multiroom_enabled})
    if snapcast_status_raises:
        routing.get_snapcast_status = AsyncMock(side_effect=RuntimeError("snapserver gone"))
    else:
        routing.get_snapcast_status = AsyncMock(return_value={
            "multiroom_available": multiroom_available,
            "server_active": True,
            "client_active": True,
        })

    snapcast_ws = Mock()
    snapcast_ws.connected = ws_connected

    camilladsp = Mock()
    camilladsp.connected = camilladsp_connected
    if camilladsp_raises:
        camilladsp.get_status = AsyncMock(side_effect=asyncio.TimeoutError())
    else:
        camilladsp.get_status = AsyncMock(
            return_value=camilladsp_status or {"available": True, "state": "running"}
        )

    return state_machine, routing, camilladsp, snapcast_ws


def _health(**kwargs):
    state_machine, routing, camilladsp, snapcast_ws = _services(**kwargs)
    app = FastAPI()
    app.include_router(create_health_router(
        state_machine, routing, Mock(), Mock(), camilladsp, snapcast_ws
    ))
    return TestClient(app).get("/api/health").json()


class TestProbeIsolation:
    """A broken probe reports itself and nothing else."""

    def test_routing_failure_is_not_reported_as_a_snapcast_failure(self):
        """The snapcast probe reads the routing probe's result. When routing
        raised, that name was never bound, so the snapcast probe died on a
        NameError and blamed snapcast for an outage that was routing's."""
        checks = _health(routing_raises=True)

        assert checks["services"]["routing"]["healthy"] is False
        assert "error" not in checks["services"]["snapcast"], (
            "snapcast reported a fault of its own while only routing was down: "
            f"{checks['services']['snapcast']}"
        )

    def test_routing_failure_survives_as_the_overall_verdict(self):
        """Routing down is unhealthy. Losing that to the snapcast probe's own
        except clause is how a hard failure got downgraded to degraded."""
        assert _health(routing_raises=True)["status"] == "unhealthy"


class TestStatusEscalatesMonotonically:
    """Within one response, health only ever gets worse."""

    def test_a_later_degraded_does_not_overwrite_an_earlier_unhealthy(self):
        """The state-machine probe posts unhealthy, then the snapcast probe finds
        multiroom unavailable and posts degraded. The worse verdict wins."""
        checks = _health(
            state_machine_raises=True,
            multiroom_enabled=True,
            multiroom_available=False,
        )

        assert checks["services"]["state_machine"]["healthy"] is False
        assert checks["services"]["snapcast"]["healthy"] is False
        assert checks["status"] == "unhealthy"

    def test_a_later_unhealthy_still_overwrites_an_earlier_degraded(self):
        """Escalation must not be mistaken for a first-write-wins rule: the
        camilladsp probe runs after snapcast and is allowed to make it worse."""
        checks = _health(
            multiroom_enabled=True,
            multiroom_available=False,
            camilladsp_connected=False,
        )

        assert checks["status"] == "unhealthy"

    def test_degraded_is_reached_when_nothing_worse_happened(self):
        """The degraded rung is real, not merely unreachable in the other tests."""
        checks = _health(multiroom_enabled=True, ws_connected=False)

        assert checks["status"] == "degraded"

    def test_all_probes_healthy_stays_healthy(self):
        assert _health()["status"] == "healthy"


class TestProbesThatFailRatherThanReport:
    """The three arms that turn a collaborator's exception into a verdict.

    Measured 2026-08-25: `health_check` ran at 74.6 % and every one of these was
    dark. A probe that lets its exception out takes the whole document with it,
    and an external monitor then sees a connection error where the body would
    have named which service went — which is the entire reason the route exists.
    """

    def test_a_snapserver_that_will_not_answer_is_degraded_not_a_dead_route(self):
        checks = _health(multiroom_enabled=True, snapcast_status_raises=True)

        assert checks["status"] == "degraded"
        assert checks["services"]["snapcast"]["healthy"] is False
        assert "snapserver gone" in checks["services"]["snapcast"]["error"]

    def test_multiroom_off_reports_a_healthy_snapcast_rather_than_probing_it(self):
        """Snapserver is deliberately stopped in direct mode — probing it there
        would report the appliance unhealthy for working as configured.
        """
        checks = _health(multiroom_enabled=False)

        assert checks["services"]["snapcast"] == {"healthy": True, "note": "multiroom disabled"}
        assert checks["status"] == "healthy"

    @pytest.mark.parametrize("kwargs,state", [
        ({"camilladsp_connected": False}, "disconnected"),
        ({"camilladsp_status": {"available": False, "state": "stalled"}}, "stalled"),
    ])
    def test_a_camilladsp_that_is_not_running_is_unhealthy_not_degraded(self, kwargs, state):
        """It is the only attenuation stage and it is always in the audio path,
        so a down daemon is silence, not reduced service. Degraded here is a
        monitor that never pages.
        """
        checks = _health(**kwargs)

        assert checks["status"] == "unhealthy"
        assert checks["services"]["camilladsp"]["healthy"] is False

    def test_a_hung_camilladsp_socket_does_not_hold_the_whole_document(self):
        """`get_status` goes down a socket that can stall. Without the bound,
        the health route stalls with it and a monitor times out instead of
        being told which service is wedged.
        """
        checks = _health(camilladsp_raises=True)

        assert checks["status"] == "unhealthy"
        assert checks["services"]["camilladsp"]["healthy"] is False

    def test_registered_sources_are_listed_with_whether_they_came_up(self):
        """A source that registered but never initialised is the shape of a
        boot that half worked — the one thing this document can show that
        nothing else does.
        """
        from backend.core.models.audio_state import AudioSource

        started, stalled = Mock(), Mock()
        started.is_initialized = True
        stalled.is_initialized = False
        checks = _health(sources={
            AudioSource.RADIO: started,
            AudioSource.SPOTIFY: stalled,
            AudioSource.BLUETOOTH: None,
        })

        assert checks["services"]["sources"] == {
            "radio": {"registered": True, "initialized": True},
            "spotify": {"registered": True, "initialized": False},
        }


class TestTheTwoFallbackReads:

    def _client(self, **over):
        state_machine, routing, camilladsp, snapcast_ws = _services()
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=over.get("setup_completed", True))
        network = Mock()
        network.hotspot_active = over.get("hotspot_active", False)
        app = FastAPI()
        app.include_router(create_health_router(
            state_machine, routing, settings, network, camilladsp, snapcast_ws
        ))
        return TestClient(app)

    def test_ping_answers_without_touching_a_service(self):
        """It is what a caller uses to tell "the box is up" from "the box is
        up and broken"; a ping that reads state cannot make that distinction.
        """
        assert self._client().get("/api/ping").json() == {"status": "success", "message": "pong"}

    def test_the_initial_state_fallback_carries_what_decides_the_first_screen(self):
        """The macOS captive-portal browser has no WebSocket. These three fields
        are what App.vue picks between the wizard, the hotspot screen and the
        player — a missing one lands a fresh unit on the player, with no wizard
        and nothing to play.
        """
        body = self._client(setup_completed=False, hotspot_active=True).get(
            "/api/initial-state"
        ).json()

        assert body["setup_completed"] is False
        assert body["hotspot_active"] is True
        assert body["full_state"]["active_source"] == "radio"
