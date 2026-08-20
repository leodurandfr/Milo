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
from unittest.mock import Mock, AsyncMock
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.health import create_health_router


def _services(*, routing_raises=False, state_machine_raises=False,
              multiroom_enabled=False, multiroom_available=True,
              ws_connected=True, camilladsp_connected=True):
    """The five collaborators the router probes, each independently breakable."""
    state_machine = Mock()
    if state_machine_raises:
        state_machine.get_current_state = Mock(side_effect=RuntimeError("state machine down"))
    else:
        state_machine.get_current_state = Mock(
            return_value={"active_source": "radio", "transitioning": False}
        )
    state_machine.sources = {}

    routing = Mock()
    if routing_raises:
        routing.get_state = Mock(side_effect=RuntimeError("routing down"))
    else:
        routing.get_state = Mock(return_value={"multiroom_enabled": multiroom_enabled})
    routing.get_snapcast_status = AsyncMock(return_value={
        "multiroom_available": multiroom_available,
        "server_active": True,
        "client_active": True,
    })

    snapcast_ws = Mock()
    snapcast_ws.connected = ws_connected

    camilladsp = Mock()
    camilladsp.connected = camilladsp_connected
    camilladsp.get_status = AsyncMock(return_value={"available": True, "state": "running"})

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
