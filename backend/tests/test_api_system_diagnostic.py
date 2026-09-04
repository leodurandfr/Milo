# backend/tests/test_api_system_diagnostic.py
"""`POST /api/system/diagnostic` — the button behind Settings › Device › Diagnostic.

The route is the whole reason the report can leave a stranger's unit: there is
no SSH on it, no log surface in the UI, and no user who will ever open
journalctl. So what this file pins is the shape the frontend reads and the two
failure modes that would leave that user with nothing.

What breaks when these fail:

* **the envelope** is what `apiCall` unwraps; the section list under the
  buttons is `data.unavailable`, and the preview and both output buttons read
  `data.report`. A rename here is a page that generates and then shows nothing;
* **the error path** must reach `errors.log` — a generation that fails silently
  is the one failure the user cannot report, because reporting is the feature;
* **a half-wired backend** is the state the report is asked for in. If the route
  needed a healthy process it would answer only when it was not needed.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock

from backend.api.system import create_system_router
from backend.core.system.diagnostic import DiagnosticService


def _client(diagnostic_service):
    app = FastAPI()
    app.include_router(
        create_system_router(Mock(), diagnostic_service=diagnostic_service),
        prefix="/api/system",
    )
    return TestClient(app)


def test_the_route_returns_the_report_and_the_missing_sections():
    """The two keys the settings page reads, under the success envelope."""
    service = Mock()
    service.generate = AsyncMock(return_value={
        "report": "===== MILO DIAGNOSTIC REPORT =====\n",
        "unavailable": [{"section": "satellite client-2", "reason": "powered off"}],
    })

    response = _client(service).post("/api/system/diagnostic")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["data"]["report"].startswith("===== MILO DIAGNOSTIC REPORT")
    assert body["data"]["unavailable"][0]["section"] == "satellite client-2"


def test_a_generation_failure_is_logged_and_answered_with_a_500(caplog):
    """`api_error_handler` logs before it raises, so the failure lands in
    errors.log — the only trace of an export that never produced one."""
    service = Mock()
    service.generate = AsyncMock(side_effect=RuntimeError("the disk went away"))

    with caplog.at_level("ERROR"):
        response = _client(service).post("/api/system/diagnostic")

    assert response.status_code == 500
    assert "the disk went away" in response.json()["detail"]
    assert any("the disk went away" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_a_report_is_produced_with_no_service_wired_at_all():
    """The state the report exists for: a backend that is partly dead.

    Every collector's dependency is optional, so a service that failed to come
    up costs its own section and nothing else. A route that needed a healthy
    process would answer only when it was not needed.
    """
    result = await DiagnosticService().generate()

    assert result["report"].startswith("===== MILO DIAGNOSTIC REPORT")
    # The sections that need a service say so by name...
    assert "===== MULTIROOM =====" in result["report"]
    assert any(item["section"] == "MULTIROOM" for item in result["unavailable"])
    # ...and the ones that read the machine itself still answered.
    assert "===== AUDIO PATH =====" in result["report"]
    assert "NOT COLLECTED" not in result["report"].split("===== AUDIO PATH =====")[1][:200]
