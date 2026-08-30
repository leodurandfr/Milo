"""The refusals and fail-open arms of the remaining `api/` routers.

What breaks when these fail, grouped by what the arm is for:

* **the three `/status`-style reads that must answer 200 with an error.** The
  settings screen and the multiroom page render from them unconditionally, so a
  500 is a blank panel rather than one that says the data is unavailable — and
  the repo's envelope rule makes `{"status": "error"}` the shape, never a bare
  boolean. What the arms must NOT do is stay quiet: the failure still belongs in
  the journal and the operator banner, which is the half that was lost once
  already (a satellite discovery that threw showed "no satellite" with nothing
  anywhere to say why).
* **the pushes to a satellite whose failure nothing retries.** The snapclient
  buffer push runs *before* the snapserver restart on purpose — afterwards every
  client is briefly disconnected and the registry answers an empty list — so a
  refusal there is a room left on the old buffer with the rest of the fleet
  moved. It is fire-and-forget across `asyncio.gather`, so the log line is the
  only trace.
* **the 404s.** Every mac_id and zone id here arrives from a URL path segment.

Measured 2026-08-27: these are what `api/` had left after B2 and after this
unit's dock/hardware pass — 60-odd lines, almost all of them `except` bodies.
`api/network.py` is deliberately excluded: its ten routes are strict
pass-throughs and the repo's own rule forbids asserting on one (B2-12).
"""
import logging
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import aiohttp
import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.testclient import TestClient

from backend.api.errors import create_errors_router
from backend.api.multiroom import create_multiroom_router
from backend.api.models import ProgramUpdateRequest
from backend.api.programs import create_programs_router
from backend.api.routing import create_routing_router
from backend.api.volume import create_volume_router
from backend.core.models.audio_state import AudioSource
from backend.core.multiroom.models import Client


# The payload every one of these calls carries: they exercise the in-flight
# claim, not the choice of release, so they all ask for the version the
# manifest declares.
VALIDATED = ProgramUpdateRequest(target="validated")


def _endpoint(router, path: str, method: str = "GET"):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"no {method} {path} in the router")


class TestTheProgramsReads:
    """Three GETs the settings screen renders from, all fail-open, all must log."""

    @pytest.fixture
    def router(self):
        update_service = MagicMock()
        satellite_service = MagicMock()
        state_machine = MagicMock()
        state_machine.broadcast = AsyncMock()
        r = create_programs_router(update_service, satellite_service, state_machine)
        r.update_service = update_service
        r.satellite_service = satellite_service
        r.state_machine = state_machine
        return r

    async def test_a_github_timeout_still_answers_the_program_list_shape(
        self, router, caplog
    ):
        """`programs`, `count` and `active_updates` are read unconditionally by
        the updates panel. A 500 collapses it; answering without the keys is the
        same crash one layer later."""
        router.update_service.get_all_program_status = AsyncMock(
            side_effect=RuntimeError("api.github.com timed out")
        )

        with caplog.at_level(logging.ERROR, logger="backend.api.programs"):
            body = await _endpoint(router, "/api/programs")()

        assert body["status"] == "error"
        assert body["programs"] == {} and body["count"] == 0
        assert "active_updates" in body
        assert any("Error listing program status" in r.message for r in caplog.records)

    async def test_an_update_in_flight_survives_a_failed_read(self, router):
        """The in-flight keys are what lets a freshly loaded client reconstruct
        the "updating" state it never saw the WS deltas for — after a reload, on
        a second device, or when the backend restarted mid-update. Dropping them
        because GitHub was unreachable puts an Update button back on a program
        that is being written to disk."""
        router.update_service.get_all_program_status = AsyncMock(
            side_effect=RuntimeError("api.github.com timed out")
        )
        # The route reads the module-level in-flight set through its closure.
        endpoint = _endpoint(router, "/api/programs")
        from backend.api import programs as programs_module

        with patch.object(programs_module, "logger", logging.getLogger("backend.api.programs")):
            claim = _endpoint(router, "/api/programs/{program_key}/update", "POST")
            router.update_service.can_update_program = AsyncMock(
                return_value={"can_update": True, "available_version": "1.2.3"}
            )
            router.update_service.update_program = AsyncMock(return_value={"success": True})
            await claim("go-librespot", VALIDATED, BackgroundTasks())

            body = await endpoint()

        assert "go-librespot" in body["active_updates"]

    async def test_a_satellite_discovery_that_throws_says_so(self, router, caplog):
        """The list is built from four awaits against the fleet. A discovery that
        threw used to leave the screen showing "no satellite" with nothing
        anywhere to say why — the reason this arm logs at all.

        The three version lookups are stubbed to succeed so the discovery is the
        only thing that can fail: they share one `asyncio.gather`, and any
        un-stubbed collaborator raises there too — which is how this test used to
        pass while the failure it names was never injected at all.
        """
        router.satellite_service.discover_satellites = AsyncMock(
            side_effect=RuntimeError("mDNS browse failed")
        )
        router.update_service.get_latest_github_version = AsyncMock(
            return_value={"status": "success", "version": "0.27.0"}
        )
        router.satellite_service.get_client_payload_version = AsyncMock(
            return_value="v0.1.0-1749-gc6247d94"
        )

        with caplog.at_level(logging.ERROR, logger="backend.api.programs"):
            body = await _endpoint(router, "/api/programs/satellites")()

        assert body["status"] == "error"
        assert body["satellites"] == [] and body["count"] == 0
        assert any(
            "Error listing satellites" in r.message and "mDNS browse failed" in r.message
            for r in caplog.records
        )

    async def test_an_unreadable_installed_version_is_null_not_a_failure(
        self, router, caplog
    ):
        """It is polled per program; a 500 for one would blank the whole row."""
        router.update_service.get_installed_version = AsyncMock(
            side_effect=FileNotFoundError("binary is gone")
        )

        with caplog.at_level(logging.ERROR, logger="backend.api.programs"):
            body = await _endpoint(router, "/api/programs/{program_key}/installed")("go-librespot")

        assert body["status"] == "error"
        assert body["installed"] is None
        assert any("Error reading installed version" in r.message for r in caplog.records)


class TestTheBackgroundUpdate:
    """The claim/announce/release cycle behind every update button."""

    @pytest.fixture
    def router(self):
        update_service = MagicMock()
        update_service.can_update_program = AsyncMock(
            return_value={"can_update": True, "available_version": "1.2.3"}
        )
        state_machine = MagicMock()
        state_machine.broadcast = AsyncMock()
        state_machine.transition_to_source = AsyncMock(return_value=True)
        state_machine.system_state.active_source = AudioSource.SPOTIFY
        r = create_programs_router(update_service, MagicMock(), state_machine)
        r.update_service = update_service
        r.state_machine = state_machine
        return r

    async def test_a_failed_update_is_announced_as_failed_and_the_claim_released(
        self, router, caplog
    ):
        """The completion event is what takes the spinner off the button. A claim
        left behind makes every later attempt answer "already updating" for the
        life of the process — with no update running."""
        router.update_service.update_program = AsyncMock(
            return_value={"success": False, "error": "checksum mismatch"}
        )
        endpoint = _endpoint(router, "/api/programs/{program_key}/update", "POST")

        with caplog.at_level(logging.ERROR, logger="backend.api.programs"):
            tasks = BackgroundTasks()
            await endpoint("go-librespot", VALIDATED, tasks)
            await tasks()

        events = [c.args[0] for c in router.state_machine.broadcast.await_args_list]
        assert events[-1].success is False
        assert any("failed" in r.message for r in caplog.records)

        # The claim is gone: a second attempt is accepted.
        router.update_service.update_program = AsyncMock(return_value={"success": True})
        second = await endpoint("go-librespot", VALIDATED, BackgroundTasks())
        assert second["status"] == "success"

    async def test_an_update_that_raises_is_announced_as_failed_too(
        self, router, caplog
    ):
        """It runs from a BackgroundTask: an exception escaping surfaces as an
        unhandled task with no event at all, and the button spins forever."""
        router.update_service.update_program = AsyncMock(
            side_effect=RuntimeError("the tarball was truncated")
        )
        endpoint = _endpoint(router, "/api/programs/{program_key}/update", "POST")

        with caplog.at_level(logging.ERROR, logger="backend.api.programs"):
            tasks = BackgroundTasks()
            await endpoint("go-librespot", VALIDATED, tasks)
            await tasks()

        events = [c.args[0] for c in router.state_machine.broadcast.await_args_list]
        assert events[-1].success is False

        second = await endpoint("go-librespot", VALIDATED, BackgroundTasks())
        assert second["status"] == "success", "the claim outlived the failure"

    async def test_updating_the_program_behind_the_playing_source_stops_it_first(
        self, router
    ):
        """go-librespot holds its ALSA device while it plays; overwriting the
        binary under it is how an update ends with a source that never comes
        back until the unit is restarted."""
        router.update_service.update_program = AsyncMock(return_value={"success": True})
        endpoint = _endpoint(router, "/api/programs/{program_key}/update", "POST")

        tasks = BackgroundTasks()
        await endpoint("go-librespot", VALIDATED, tasks)
        await tasks()

        router.state_machine.transition_to_source.assert_awaited_once_with(AudioSource.NONE)

    async def test_updating_a_program_that_is_not_playing_leaves_the_source_alone(
        self, router
    ):
        """Otherwise every update would silence whatever is playing."""
        router.state_machine.system_state.active_source = AudioSource.RADIO
        router.update_service.update_program = AsyncMock(return_value={"success": True})
        endpoint = _endpoint(router, "/api/programs/{program_key}/update", "POST")

        tasks = BackgroundTasks()
        await endpoint("go-librespot", VALIDATED, tasks)
        await tasks()

        router.state_machine.transition_to_source.assert_not_called()


class TestTheSnapclientConfigPush:
    """It runs before the snapserver restart, and nothing retries it."""

    @pytest.fixture
    def pieces(self):
        routing = MagicMock()
        routing.multiroom_enabled = True
        routing.snapclient_service = "milo-snapclient-multiroom.service"
        routing.service_manager.restart = AsyncMock(return_value=True)
        snapcast = MagicMock()
        snapcast.is_available = AsyncMock(return_value=True)
        snapcast.get_server_config = AsyncMock(return_value={"buffer_ms": 700})
        snapcast.update_server_config = AsyncMock(return_value=True)
        state_machine = MagicMock()
        state_machine.broadcast = AsyncMock()
        registry = MagicMock()
        registry.get_online_clients = Mock(return_value=[])
        settings = MagicMock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock(return_value=True)
        return routing, snapcast, state_machine, registry, settings

    def _client(self, pieces, registry_service=...):
        routing, snapcast, state_machine, registry, settings = pieces
        settings.set_settings_strict = AsyncMock()
        app = FastAPI()
        app.include_router(create_routing_router(
            routing, state_machine, snapcast,
            settings_service=settings,
            client_registry_service=registry if registry_service is ... else registry_service,
        ))
        return TestClient(app)

    @staticmethod
    def _apply(client, **extra):
        """The one request that reaches the push: a preset change."""
        config = {"buffer_ms": 700, "codec": "flac", "chunk_ms": 40,
                  "snapclient_buffer_time": 120}
        config.update(extra)
        return client.put("/api/routing/snapcast/server-config", json={"config": config})

    @staticmethod
    def _snapclient(ip, name="Canapé"):
        return Client(mac_id="aa:bb:cc:dd:ee:07", name=name, ip=ip, online=True)

    async def test_a_fleet_of_one_local_client_pushes_to_nobody(self, pieces, caplog):
        """The local snapclient reads its env from the same file the server
        writes; pushing to 127.0.0.1:8001 would reach a client API that does not
        exist on a server."""
        _, _, _, registry, _ = pieces
        registry.get_online_clients = Mock(return_value=[self._snapclient("127.0.0.1", "Milō")])
        client = self._client(pieces)

        with patch.object(aiohttp, "ClientSession", Mock(side_effect=AssertionError("dialled"))):
            with caplog.at_level(logging.DEBUG, logger="backend.api.routing"):
                assert self._apply(client).status_code == 200

        assert any("No remote clients" in r.message for r in caplog.records)

    async def test_a_satellite_that_refuses_the_push_is_named_with_its_answer(
        self, pieces, caplog
    ):
        """Nothing retries this, and it is gathered with `return_exceptions`. The
        name and the status are the only record that this room is still on the
        old ALSA buffer while the rest of the fleet moved."""
        _, _, _, registry, _ = pieces
        registry.get_online_clients = Mock(return_value=[self._snapclient("192.168.1.153")])
        client = self._client(pieces)

        class _Resp:
            status = 400

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def text(self):
                return "unknown field"

        class _Session:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def put(self, url, **kw):
                return _Resp()

        with patch.object(aiohttp, "ClientSession", _Session):
            with caplog.at_level(logging.WARNING, logger="backend.api.routing"):
                assert self._apply(client).status_code == 200

        assert any("Failed to push config to Canapé" in r.message and "400" in r.message
                   for r in caplog.records)

    async def test_a_satellite_that_cannot_be_reached_does_not_stop_the_others(
        self, pieces, caplog
    ):
        """They are pushed concurrently; a raise that escaped would abandon the
        remaining rooms and then the snapserver restart with them."""
        _, _, _, registry, _ = pieces
        registry.get_online_clients = Mock(return_value=[
            self._snapclient("192.168.1.153"), self._snapclient("192.168.1.60", "Bureau"),
        ])
        client = self._client(pieces)

        with patch.object(aiohttp, "ClientSession", Mock(side_effect=OSError("no route"))):
            with caplog.at_level(logging.WARNING, logger="backend.api.routing"):
                assert self._apply(client).status_code == 200

        reached = [r.message for r in caplog.records if "Could not reach" in r.message]
        assert len(reached) == 2, "one unreachable satellite abandoned the others"
        assert any("Canapé (192.168.1.153)" in m for m in reached)

    async def test_without_a_registry_nothing_is_pushed(self, pieces, caplog):
        """The router is constructible without one — it is optional in the
        signature — and this runs on every preset change."""
        client = self._client(pieces, registry_service=None)

        with caplog.at_level(logging.DEBUG, logger="backend.api.routing"):
            assert self._apply(client).status_code == 200

        assert any("No client_registry_service" in r.message for r in caplog.records)

    async def test_a_broadcast_that_fails_does_not_fail_the_config_write(
        self, pieces, caplog
    ):
        """It is the last step after `/etc` was written and snapserver restarted.
        Raising there would answer 500 for a change that fully landed, and the
        settings page would offer to apply it again — another restart, another
        silence in every room."""
        _, _, state_machine, _, _ = pieces
        state_machine.broadcast = AsyncMock(side_effect=RuntimeError("no ws clients"))
        client = self._client(pieces)

        with caplog.at_level(logging.ERROR, logger="backend.api.routing"):
            response = self._apply(client)

        assert response.status_code == 200
        assert any("Error publishing Snapcast update" in r.message for r in caplog.records)

    async def test_a_server_config_read_that_fails_still_answers_the_capabilities(
        self, pieces, caplog
    ):
        """The settings page builds its codec and preset options from
        `capabilities`; losing them to a snapserver that is briefly down leaves
        the form with empty dropdowns instead of current values it cannot fetch.
        """
        _, snapcast, _, _, _ = pieces
        snapcast.get_server_config = AsyncMock(side_effect=RuntimeError("rpc broke"))
        client = self._client(pieces)

        with caplog.at_level(logging.ERROR, logger="backend.api.routing"):
            body = client.get("/api/routing/snapcast/server-config").json()

        assert body["config"] is None
        assert body["capabilities"], "the capabilities went with the failed read"
        assert "error" in body
        assert any("Error getting server config" in r.message for r in caplog.records)

    async def test_a_local_snapclient_that_will_not_restart_reaches_the_caller(
        self, pieces, caplog
    ):
        """By this point every satellite is on the new buffer_time and the local
        speaker is not. That is a fleet out of step with itself, not a cosmetic
        failure — it must not hide under a "services restarted" answer."""
        routing, _, _, _, _ = pieces
        routing.service_manager.restart = AsyncMock(return_value=False)
        client = self._client(pieces)

        with caplog.at_level(logging.ERROR, logger="backend.api.routing"):
            response = self._apply(client)

        assert response.status_code == 502
        assert "did not restart" in response.json()["detail"]
        assert any("Failed to restart" in r.message for r in caplog.records)


class TestTheVolumeReads:
    """`GET /api/volume` — polled by every client, and by Milo-Mac."""

    @pytest.fixture
    def client(self):
        service = Mock()
        service.get_volume_state = AsyncMock()
        app = FastAPI()
        app.include_router(create_volume_router(service))
        c = TestClient(app)
        c._service = service
        return c

    def test_a_volume_state_that_cannot_be_read_answers_an_error_envelope(self, client):
        """It is a `/status`-style read: 200 with `status: error`, never a bare
        boolean and never a 500 — the volume bar is drawn from it on every page
        load, including Milo-Mac's."""
        client._service.get_volume_state = AsyncMock(side_effect=RuntimeError("no dsp"))

        response = client.get("/api/volume/state")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "error"
        assert "success" not in body


class TestTheFrontendErrorSink:
    """`POST /api/errors` — how a browser failure reaches the operator journal."""

    def test_a_reported_error_is_logged_at_error_with_its_source(self, caplog):
        """It is the only path from a Vue error handler into `errors.log` and the
        operator banner; anything below ERROR is invisible there, because the
        journal carries the level in the text rather than the priority."""
        app = FastAPI()
        app.include_router(create_errors_router())

        with caplog.at_level(logging.ERROR, logger="frontend"):
            response = TestClient(app).post(
                "/api/errors",
                json={"source": "unifiedAudioStore", "error": "TypeError: x is undefined"},
            )

        assert response.status_code == 200
        assert response.json() == {"status": "success"}
        assert any("[unifiedAudioStore]" in r.message and "TypeError" in r.message
                   for r in caplog.records)

    def test_the_optional_stack_is_appended_when_it_is_there(self, caplog):
        """Without it the journal has a message and no frame to place it in."""
        app = FastAPI()
        app.include_router(create_errors_router())

        with caplog.at_level(logging.ERROR, logger="frontend"):
            TestClient(app).post(
                "/api/errors",
                json={"source": "App.vue", "error": "boom", "info": "at setup (App.vue:12)"},
            )

        assert any("at setup (App.vue:12)" in r.message for r in caplog.records)


class TestTheMultiroomNotFounds:
    """Every id here is a URL path segment."""

    @pytest.fixture
    def pieces(self):
        registry = MagicMock()
        registry.get_client = Mock(return_value=None)
        registry.get_zone = Mock(return_value=None)
        registry.update_zone = AsyncMock(return_value=None)
        registry.add_client_to_zone = AsyncMock(return_value=False)
        registry.remove_client_from_zone = AsyncMock(return_value=False)
        registry.create_zone = AsyncMock()
        state_machine = MagicMock()
        state_machine.broadcast = AsyncMock()
        pending = MagicMock()
        pending.get_client = Mock(return_value=None)
        return registry, state_machine, pending

    @pytest.fixture
    def client(self, pieces):
        registry, state_machine, pending = pieces
        app = FastAPI()
        app.include_router(create_multiroom_router(
            registry_service=registry,
            pending_clients_service=pending,
        ))
        c = TestClient(app)
        c._registry = registry
        c._pending = pending
        return c

    def test_reading_the_hardware_of_an_unknown_client_is_a_404(self, client):
        response = client.get("/api/multiroom/clients/aa:bb:cc:dd:ee:07/hardware")

        assert response.status_code == 404

    def test_configuring_the_audio_of_an_unknown_client_is_a_404(self, client):
        """`PUT /clients/{mac}/audio` reboots the satellite it names. Falling
        through would build the payload from a `None` client and answer 500 for
        what is a plainly-known condition."""
        response = client.put(
            "/api/multiroom/clients/aa:bb:cc:dd:ee:07/audio",
            json={"audio_id": "hifiberry_amp2"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_configuring_an_unknown_pending_client_is_a_404(self, client):
        """The wizard's own path: a mac that expired from the staging area
        between the form opening and the submit."""
        response = client.post(
            "/api/multiroom/pending-clients/aa:bb:cc:dd:ee:07/configure",
            json={"name": "Salon", "speaker_type": "bookshelf",
                  "audio_id": "hifiberry_amp2", "volume_control": True},
        )

        assert response.status_code == 404
