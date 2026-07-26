# backend/tests/test_routes_audio.py
"""
Tests for /api/audio — the single transport every source command rides.

The generic control endpoint replaced five per-source wrapper routes, and the
whole point of that consolidation was that one failure contract replaces two:
a command that fails is an HTTP 400, not a 200 carrying `status: "error"`. If
that regresses, every caller silently reads a failure as a success — which is
exactly what the old dedicated/generic split allowed.

`POST /source/{name}` is the deliberate exception and is pinned here too: it
answers 200 with `status: "error"` because Milo-Mac reads that key, and a future
"make it consistent" pass would break a client with no versioning.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, Mock

from backend.api.audio import create_router
from backend.core.models.audio_state import AudioSource


@pytest.fixture
def source():
    """A registered source whose command() outcome each test decides."""
    src = Mock()
    src.command = AsyncMock(return_value={"success": True, "message": "ok"})
    return src


@pytest.fixture
def client(source):
    state_machine = Mock()
    state_machine.sources = {AudioSource.PODCAST: source}
    state_machine.transition_to_source = AsyncMock(return_value=True)
    state_machine.refresh_active_metadata = AsyncMock()
    state_machine.get_current_state = Mock(return_value={})

    app = FastAPI()
    app.include_router(create_router(state_machine))
    test_client = TestClient(app)
    test_client._state_machine = state_machine
    return test_client


def _control(client, command="pause", data=None):
    return client.post(
        "/api/audio/control/podcast",
        json={"command": command, "data": data or {}},
    )


class TestGenericControlEndpoint:
    """POST /api/audio/control/{source} — the one transport for commands."""

    def test_successful_command_returns_the_status_envelope(self, client, source):
        response = _control(client, "pause")

        assert response.status_code == 200
        assert response.json() == {
            "status": "success",
            "result": {"success": True, "message": "ok"},
        }
        source.command.assert_awaited_once_with("pause", {})

    def test_command_params_reach_the_source_verbatim(self, client, source):
        """`data` is opaque here on purpose — the source validates it against its
        own COMMANDS map, which is why a dedicated typed route adds nothing."""
        _control(client, "set_speed", {"speed": 1.5})

        source.command.assert_awaited_once_with("set_speed", {"speed": 1.5})

    def test_failed_command_is_a_400_not_a_200(self, client, source):
        """The contract the five wrapper routes were removed in favour of.

        Before consolidation this endpoint answered 200 + {"status": "error"}
        while the dedicated routes raised 400, so the same failure looked
        different depending on which path the caller happened to use.
        """
        source.command.return_value = {"success": False, "error": "mpv not running"}

        response = _control(client)

        assert response.status_code == 400
        assert response.json()["detail"] == "mpv not running"

    def test_a_raising_command_is_a_500(self, client, source):
        source.command.side_effect = RuntimeError("socket gone")

        response = _control(client)

        assert response.status_code == 500

    def test_unknown_source_name_is_a_400(self, client):
        """parse_audio_source guards untrusted path input."""
        response = client.post(
            "/api/audio/control/not-a-source", json={"command": "pause", "data": {}}
        )

        assert response.status_code == 400

    def test_valid_but_unregistered_source_is_a_404(self, client):
        """A source in the enum that dependencies.py never instantiated: the
        name is legal, the instance is absent — distinct failures, distinct codes.
        """
        response = client.post(
            "/api/audio/control/radio", json={"command": "pause", "data": {}}
        )

        assert response.status_code == 404


class TestSourceTransitionEndpoint:
    """POST /api/audio/source/{source} — pinned by the Milo-Mac manifest."""

    def test_failed_transition_stays_a_200_with_status_error(self, client):
        """Deliberately NOT aligned on the 400 contract above.

        Milo-Mac reads the `status` key of this response and has no API
        versioning, so switching it to an HTTPException would break the app
        silently at runtime. The asymmetry is intentional — don't "fix" it.
        """
        client._state_machine.transition_to_source = AsyncMock(return_value=False)

        response = client.post("/api/audio/source/podcast")

        assert response.status_code == 200
        assert response.json() == {"status": "error"}

    def test_successful_transition(self, client):
        response = client.post("/api/audio/source/podcast")

        assert response.status_code == 200
        assert response.json() == {"status": "success"}
