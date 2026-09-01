"""The arms that decide whether a satellite is seen — and whether it is pushed twice.

What breaks when these fail: `SnapcastWebSocketService` is where CLAUDE.md's
"one client, one admission" is enforced. Four snapserver notifications can be
first to see a client arrive and which one wins is a boot-order race, so they all
funnel through one registration and one sync, guarded by `_syncing_mac_ids`. A
duplicate sync is not a duplicate log line: it re-pushes the snapclient buffer
config and restarts the client's snapclient, which cuts the sound in that room.
That is the founding incident of the suite's network guard.

Measured 2026-08-27, none of these arms had run. They fall in three groups:

* **the connect sweep's refusals** — it is the only notification path that will
  ever see a client that was already up when the backend started, i.e. the whole
  fleet after a power cut. When it declines to act it says so at WARNING, which
  is the only thing separating "snapserver answered nothing" from "no speaker is
  connected" in the operator log.
* **the pushes that nothing retries** — the snapclient buffer config and the EQ
  record. Both are fire-and-forget from a background task, so a refusal that is
  not logged is a satellite left on stale settings with no trace anywhere.
* **the registry bus's own drop arm** — `_broadcast_registry_event` is the sole
  translator from the internal bus to the `multiroom` WS category. An event type
  with no class in `REGISTRY_EVENT_CLASSES` is what a renamed identifier looks
  like from here, and it must be loud.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.models import EqFilter, EqualizerSettings
from backend.core.multiroom.websocket import SnapcastWebSocketService

MAC = "aa:bb:cc:dd:ee:07"
IP = "192.168.1.153"
HOST = "milo-client-canape"
SNAPCAST_ID = "aa:bb:cc:dd:ee:07"

_REAL_SLEEP = asyncio.sleep


class TheGuardWasBypassed(BaseException):
    """A duplicate admission reached the sync body.

    Derived from BaseException because the production paths around here carry
    `except Exception`, and a swallowed bound is a hung suite rather than a red.
    """


@pytest.fixture
async def registry():
    settings = AsyncMock()
    settings.get_setting = AsyncMock(return_value=None)
    reg = ClientRegistryService(settings_service=settings)
    await reg.initialize()
    return reg


@pytest.fixture
def snapcast():
    service = MagicMock()
    service.set_volume = AsyncMock(return_value=True)
    service.set_latency = AsyncMock(return_value=True)
    service.get_clients = AsyncMock(return_value=[])
    service.get_server_status = AsyncMock(return_value={"server": {"groups": []}})
    service.extract_clients = MagicMock(return_value=[])
    return service


@pytest.fixture
def volume_service():
    service = MagicMock()
    service.state_store.get_client_volume = MagicMock(return_value=None)
    service.state_store.get_client_mute = MagicMock(return_value=False)
    service.state_store.set_client_volume = AsyncMock()
    service.equalizer_controller.set_equalizer_volume = AsyncMock(return_value=True)
    service.equalizer_controller.set_equalizer_mute = AsyncMock(return_value=True)
    service.broadcast_volume_state = AsyncMock()
    service.volume_config.restore_last_volume = False
    service.volume_config.startup_volume_db = -20.0
    return service


@pytest.fixture
def service(registry, snapcast, volume_service, no_satellite_network):
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    svc = SnapcastWebSocketService(
        state_machine=state_machine,
        routing_service=MagicMock(),
        snapcast_service=snapcast,
    )
    svc._registry = registry
    svc._volume_service = volume_service
    return svc


def _eq_record() -> EqualizerSettings:
    """A record with something in it, so a push that drops it is distinguishable."""
    return EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=1000, gain=3.0)])


def _snapcast_client(*, mac_id=MAC, ip=IP, name="Canapé", client_id=SNAPCAST_ID):
    """The shape `SnapcastService.extract_clients` returns, as the callers read it."""
    return {"mac_id": mac_id, "id": client_id, "ip": ip, "name": name, "host": HOST}


class TestTheConnectSweepRefusals:
    """The sweep that admits everything already connected when the socket opens."""

    async def test_no_snapcast_service_is_reported_not_passed_over(self, service, caplog):
        """Without it there is no way to enumerate clients at all: every satellite
        that was up before the backend stays invisible until it happens to
        reconnect. Silence here reads exactly like an empty fleet."""
        service._snapcast_service = None

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            await service._initialize_existing_clients()

        assert any("SnapcastService not available" in r.message for r in caplog.records)

    async def test_a_snapserver_that_answers_nothing_is_reported_not_read_as_empty(
        self, service, snapcast, caplog
    ):
        """`get_server_status` is fail-open: it flattens an RPC failure to {}.
        Parsing that would say "no client is connected" about a fleet that is
        playing, and the sweep would then admit nobody, silently."""
        snapcast.get_server_status = AsyncMock(return_value={})

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            await service._initialize_existing_clients()

        assert any("Could not get Snapcast status" in r.message for r in caplog.records)
        snapcast.extract_clients.assert_not_called()

    async def test_a_failure_inside_the_sweep_does_not_take_the_message_loop_down(
        self, service, snapcast, caplog
    ):
        """It runs inside `_connect_and_listen`, before the frame loop starts.
        An exception escaping here would drop the connection and every
        notification after it — the sweep failing must cost the sweep only."""
        snapcast.extract_clients = MagicMock(side_effect=RuntimeError("malformed status"))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await service._initialize_existing_clients()

        assert any("Error initializing existing clients" in r.message for r in caplog.records)

    async def test_a_client_snapserver_claims_but_cannot_prove_is_counted_out_loud(
        self, service, snapcast, caplog
    ):
        """snapserver's `connected` flag outlives a satellite that vanished without
        a TCP FIN. The gap between what it claims and what the freshness rule
        admits is the only diagnostic for "the UI shows fewer speakers than the
        server does"."""
        snapcast.get_server_status = AsyncMock(return_value={
            "server": {"groups": [{"clients": [
                {"id": SNAPCAST_ID, "connected": True},
                {"id": "ghost", "connected": True},
            ]}]}
        })
        snapcast.extract_clients = MagicMock(return_value=[_snapcast_client()])

        with caplog.at_level(logging.INFO, logger="backend.core.multiroom.websocket"):
            await service._initialize_existing_clients()

        assert any(
            "snapserver claims 2 connected, 1 are live" in r.message for r in caplog.records
        )


class TestTheDuplicateSyncGuard:
    """"One client, one admission" — the part that keeps a room from being cut twice."""

    async def test_a_second_sync_for_the_same_client_is_refused_while_one_is_in_flight(
        self, service, registry, caplog
    ):
        """Two of the four admission notifications arriving together is the boot
        race the guard exists for. Letting both through re-pushes the snapclient
        buffer config and restarts snapclient on a speaker that is playing."""
        await registry.register_client(MAC, "Canapé", IP)
        parked = asyncio.Event()
        entered = asyncio.Event()
        entries = {"n": 0}

        async def _slow(*args, **kwargs):
            # Bounded on the path the mutation opens, not only the green one:
            # without the guard the second call takes this same branch, and an
            # unbounded park there turns a red into a hung suite.
            entries["n"] += 1
            if entries["n"] > 1:
                raise TheGuardWasBypassed("a second sync entered while one was in flight")
            entered.set()
            await parked.wait()
            return True

        service._do_sync_reconnecting_client_volume = _slow

        first = asyncio.create_task(service._sync_reconnecting_client_volume(MAC))
        await asyncio.wait_for(entered.wait(), timeout=2)

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.websocket"):
            second = await service._sync_reconnecting_client_volume(MAC)

        assert second is False
        assert any("already syncing" in r.message for r in caplog.records)

        parked.set()
        assert await asyncio.wait_for(first, timeout=2) is True

    async def test_the_guard_is_released_so_the_client_can_be_synced_again_later(
        self, service, registry
    ):
        """A guard that leaked would make the *first* reconnection of a session
        the only one that ever works."""
        await registry.register_client(MAC, "Canapé", IP)
        service._do_sync_reconnecting_client_volume = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await service._sync_reconnecting_client_volume(MAC)

        assert MAC not in service._syncing_mac_ids

    async def test_a_reconcile_sweep_skips_a_client_whose_sync_is_already_running(
        self, service, registry, caplog
    ):
        """Same guard, the other entry point: `Server.OnUpdate` sees the client
        before the in-flight `Client.OnConnect` sync has finished."""
        service._syncing_mac_ids.add(MAC)
        service._register_snapclient = AsyncMock()

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.websocket"):
            await service._process_new_clients([_snapcast_client()], known_mac_ids=set())

        service._register_snapclient.assert_not_called()
        assert any("sync already in flight" in r.message for r in caplog.records)

    async def test_a_hardware_apply_that_raises_is_retried_not_abandoned(
        self, service, registry, monkeypatch, caplog
    ):
        """A satellite's API is often still booting when its snapclient is already
        connected, so the first attempts legitimately fail. Giving up on the first
        exception leaves the client muted — CamillaDSP starts with `-m`."""
        await registry.register_client(MAC, "Canapé", IP)
        attempts = []

        async def _apply(mac_id, target):
            attempts.append(target)
            if len(attempts) < 3:
                raise ConnectionError("satellite still booting")
            return True

        service._apply_target_volume_to_client = _apply
        service._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)
        waits = []

        async def _sleep(delay, *a, **k):
            waits.append(delay)
            await _REAL_SLEEP(0)

        monkeypatch.setattr(asyncio, "sleep", _sleep)

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            ok = await service._do_sync_reconnecting_client_volume(
                MAC, set_online_after=False, max_retries=5, retry_delay=3.0
            )

        assert ok is True
        assert len(attempts) == 3
        # One wait per failed attempt and no more: the arm that skips the wait
        # after the last attempt is what keeps a give-up from costing an extra
        # retry_delay with nothing left to retry.
        assert waits == [3.0, 3.0]
        assert sum("attempt 1 failed" in r.message for r in caplog.records) == 1

    async def test_every_admission_repins_snapserver_to_passthrough_and_the_delay(
        self, service, registry, snapcast
    ):
        """Two things only the paths holding a snapcast id can restore.

        Snapserver must stay a passthrough — CamillaDSP on the client is the only
        attenuation stage (invariant 7) — so an admission that left snapserver at
        whatever level it held would attenuate twice, and the same client ended up
        differently loud depending on which of the four notifications announced
        it. The per-client delay is the mirror: it is native Snapcast latency
        Milō owns, so a delay changed while the client was away has reached
        nobody until this call.
        """
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_delay(MAC, 40)
        service._apply_target_volume_to_client = AsyncMock(return_value=True)
        service._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)

        await service._do_sync_reconnecting_client_volume(
            MAC, set_online_after=False, max_retries=0, retry_delay=0.0,
            snapcast_id=SNAPCAST_ID,
        )

        snapcast.set_volume.assert_awaited_once_with(SNAPCAST_ID, 100)
        snapcast.set_latency.assert_awaited_once_with(SNAPCAST_ID, 40)

    async def test_a_caller_without_a_snapcast_id_leaves_snapserver_alone(
        self, service, registry, snapcast
    ):
        """Half the callers do not have one — snapserver keys clients by its own
        id, not by mac_id, and `set_volume(None, 100)` would be a JSON-RPC call
        naming no client. The guard is what makes the id optional at all."""
        await registry.register_client(MAC, "Canapé", IP)
        service._apply_target_volume_to_client = AsyncMock(return_value=True)
        service._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)

        await service._do_sync_reconnecting_client_volume(
            MAC, set_online_after=False, max_retries=0, retry_delay=0.0, snapcast_id=None
        )

        snapcast.set_volume.assert_not_called()
        snapcast.set_latency.assert_not_called()

    async def test_a_sync_that_is_not_an_admission_does_not_change_what_is_shown(
        self, service, registry
    ):
        """Only the admission paths pass `set_online_after=True`. A volume resync
        of a client the operator deliberately left offline would put it back in
        the UI — and the flag is also what makes the *admission* wait for the
        hardware, so flipping it unconditionally undoes both halves at once."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_online(MAC, False)
        service._apply_target_volume_to_client = AsyncMock(return_value=True)
        service._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)

        ok = await service._do_sync_reconnecting_client_volume(
            MAC, set_online_after=False, max_retries=0, retry_delay=0.0
        )

        assert ok is True
        assert registry.get_client(MAC).online is False

    async def test_the_last_attempt_is_not_followed_by_a_pointless_wait(
        self, service, registry, monkeypatch, caplog
    ):
        """`_sync_reconnecting_client_volume` is spawned from the admission paths
        and holds the `_syncing_mac_ids` guard for as long as it runs; a wait
        after the final attempt keeps a client un-admittable for retry_delay more
        seconds than the retries themselves need."""
        await registry.register_client(MAC, "Canapé", IP)
        service._apply_target_volume_to_client = AsyncMock(return_value=False)
        waits = []

        async def _sleep(delay, *a, **k):
            waits.append(delay)
            await _REAL_SLEEP(0)

        monkeypatch.setattr(asyncio, "sleep", _sleep)

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            ok = await service._do_sync_reconnecting_client_volume(
                MAC, set_online_after=False, max_retries=2, retry_delay=3.0
            )

        assert ok is False
        assert service._apply_target_volume_to_client.await_count == 3
        assert waits == [3.0, 3.0]
        assert any("GAVE UP after 3 attempts" in r.message for r in caplog.records)


class TestThePushesNothingRetries:
    """Fire-and-forget pushes to a satellite: the only trace is the log line."""

    async def test_a_satellite_that_refuses_the_buffer_config_is_reported_with_its_answer(
        self, service, monkeypatch, caplog
    ):
        """Nothing retries this. Without the status and the body, a satellite left
        on the wrong ALSA buffer is indistinguishable from one that took it —
        and the buffer is what decides whether that room drifts."""

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

            def put(self, url, **kwargs):
                return _Resp()

        monkeypatch.setattr(aiohttp, "ClientSession", _Session)

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            await service._push_snapclient_config(IP)

        assert any(
            "Failed to sync snapclient config" in r.message and "400" in r.message
            and "unknown field" in r.message
            for r in caplog.records
        )

    async def test_a_satellite_that_cannot_be_reached_is_reported_not_swallowed(
        self, service, monkeypatch, caplog
    ):
        """It runs from a background task, so an exception escaping would surface
        only as a `BG task failed` line naming no client."""

        def _boom(*a, **k):
            raise OSError("no route to host")

        monkeypatch.setattr(aiohttp, "ClientSession", _boom)

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            await service._push_snapclient_config(IP)

        assert any(
            "Could not push snapclient config" in r.message and IP in r.message
            for r in caplog.records
        )

    async def test_the_local_client_is_never_driven_through_the_remote_eq_path(
        self, service, registry
    ):
        """It owns equalizer.json and is applied to the DAC by CamillaDSPService.
        Pushing to it over HTTP would target this host's own client API, which
        does not exist on a server."""
        await registry.register_client(MAC, "Milō", "127.0.0.1")
        # A stored record is what separates the two answers: without one the
        # method returns True from the "no saved settings" arm either way.
        await registry.set_client_equalizer(MAC, _eq_record())
        service._equalizer_client_proxy_service = MagicMock()
        service._equalizer_client_proxy_service.apply_record = AsyncMock()

        assert await service._sync_standalone_equalizer_to_client(MAC) is True
        service._equalizer_client_proxy_service.apply_record.assert_not_called()

    async def test_a_client_without_an_address_cannot_be_synced_and_says_so(
        self, service, registry, caplog
    ):
        """A registry entry with no IP is a client admitted from a frame that
        carried none; pushing to an empty host would build `http://:8001/`."""
        await registry.register_client(MAC, "Canapé", "")

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            assert await service._sync_standalone_equalizer_to_client(MAC) is False

        assert any("no IP address" in r.message for r in caplog.records)

    async def test_no_proxy_service_means_the_record_was_not_applied(
        self, service, registry
    ):
        """Returning True here would mark a satellite configured that never
        received a byte, and the caller shows it online on that answer."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_equalizer(MAC, _eq_record())
        service._equalizer_client_proxy_service = None

        assert await service._sync_standalone_equalizer_to_client(MAC) is False

    async def test_a_refused_record_is_requeued_whole_not_per_setting(
        self, service, registry, caplog
    ):
        """The record is the unit of truth: replaying it converges the client in
        one shot, where per-setting retries leave a satellite half-applied."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_equalizer(MAC, _eq_record())
        service._equalizer_client_proxy_service = MagicMock()
        service._equalizer_client_proxy_service.apply_record = AsyncMock(return_value=False)
        crossover = MagicMock()
        crossover.queue_pending_settings = AsyncMock()
        service._crossover_service = crossover

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            assert await service._sync_standalone_equalizer_to_client(MAC) is False

        crossover.queue_pending_settings.assert_awaited_once()
        assert crossover.queue_pending_settings.await_args.args[1] == "record"

    async def test_a_failure_while_syncing_the_record_is_an_error_and_a_refusal(
        self, service, registry, caplog
    ):
        """It is awaited inside the admission retry loop; a raise would be caught
        there as a whole-attempt failure and re-run the volume apply with it."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_equalizer(MAC, _eq_record())
        service._equalizer_client_proxy_service = MagicMock()
        service._equalizer_client_proxy_service.apply_record = AsyncMock(
            side_effect=RuntimeError("satellite refused")
        )

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            assert await service._sync_standalone_equalizer_to_client(MAC) is False

        assert any("Error syncing equalizer" in r.message for r in caplog.records)


class TestTheRegistryBusTranslation:
    """The only bridge from the internal registry bus to the `multiroom` WS category."""

    async def test_an_event_type_with_no_ws_class_is_dropped_loudly(self, service, caplog):
        """This is what a renamed registry identifier looks like from here. A
        silent drop is how two dead handlers survived a rename once already."""
        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await service._broadcast_registry_event("client_teleported", {"mac_id": MAC})

        assert any("No WS event class" in r.message for r in caplog.records)
        service.state_machine.broadcast.assert_not_called()


class TestTheReconcileSweepAndTheCloseBound:
    """Two loops that must survive the outside world misbehaving."""

    async def test_the_sweep_skips_a_pass_while_the_socket_is_down(self, service, snapcast):
        """Reading snapserver through a dead socket would mark the whole fleet
        gone. The sweep is the only thing that can do that, so it must not."""
        service.running = True
        service.should_connect = True
        service.websocket = None
        passes = {"n": 0}

        async def _sleep(delay, *a, **k):
            passes["n"] += 1
            if passes["n"] >= 3:
                service.should_connect = False
            await _REAL_SLEEP(0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", _sleep)
            await service._reconcile_loop()

        snapcast.get_server_status.assert_not_called()

    async def test_a_failing_sweep_pass_does_not_kill_the_sweep(
        self, service, snapcast, caplog
    ):
        """It is the only detector of a satellite that vanished without a TCP FIN;
        a task that dies on one bad status leaves that client online forever."""
        service.running = True
        service.should_connect = True
        service.websocket = MagicMock(closed=False)
        snapcast.get_server_status = AsyncMock(side_effect=RuntimeError("rpc broke"))
        passes = {"n": 0}

        async def _sleep(delay, *a, **k):
            passes["n"] += 1
            if passes["n"] >= 3:
                service.should_connect = False
            await _REAL_SLEEP(0)

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(asyncio, "sleep", _sleep)
            with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
                await service._reconcile_loop()

        assert snapcast.get_server_status.await_count == 3
        assert sum("Error in client reconcile sweep" in r.message for r in caplog.records) == 3

    async def test_a_peer_that_never_answers_the_close_frame_is_given_up_on(
        self, service, caplog
    ):
        """aiohttp's close() waits for the peer's CLOSE frame, and the usual reason
        we are here is that snapserver is going down with multiroom. Unbounded,
        this would hold `PUT /api/routing/multiroom` or the lifespan teardown open
        on a peer that will never answer."""

        class _Deaf:
            closed = False

            async def close(self):
                await asyncio.Event().wait()

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            await asyncio.wait_for(service._close_websocket(_Deaf()), timeout=5)

        assert any("did not close cleanly" in r.message for r in caplog.records)

    async def test_an_already_closed_socket_is_not_closed_again(self, service):
        """`cleanup` and `stop_connection` both run on a multiroom disable."""
        closed_twice = MagicMock()
        closed_twice.closed = True
        closed_twice.close = AsyncMock()

        await service._close_websocket(closed_twice)
        await service._close_websocket(None)

        closed_twice.close.assert_not_called()


class TestTheNameChangedResolution:
    """snapweb is served on 0.0.0.0:1780, so anyone on the LAN can rename a client."""

    async def test_the_rename_lands_on_the_same_identity_whether_snapserver_answers_or_not(
        self, service, registry, snapcast
    ):
        """Measured 2026-08-27: the status lookup above the fallback is inert.

        `compute_mac_id` returns `host_id` verbatim for any client that is not at
        127.0.0.1, and `host_id` is the `id` the frame already carried — so the
        walk through groups → clients recomputes the value it started from. For
        the local client the two agree too: `milo-snapclient-multiroom.service`
        passes `--hostID $(cat /sys/class/net/eth0/address)`, which is the same
        read `get_local_mac()` does.

        This is not a claim about today's data, it is the guardrail for the
        identity rule itself: it goes red the day `compute_mac_id` keys a remote
        client on anything other than the id Milō assigned — `host.mac`, for
        instance, which reports the interface the client connected through and
        once split one wifi-only device into two identities.
        """
        await registry.register_client(MAC, "Canapé", IP)
        service.registry.update_client = AsyncMock()

        snapcast.get_server_status = AsyncMock(return_value={
            "server": {"groups": [{"clients": [
                {"id": "other", "host": {"name": "x", "ip": "::ffff:192.168.1.99", "mac": "x"}},
                {"id": SNAPCAST_ID,
                 "host": {"name": HOST, "ip": f"::ffff:{IP}", "mac": "ff:ff:ff:ff:ff:ff"}},
            ]}]}
        })
        await service._handle_client_name_changed({"id": SNAPCAST_ID, "name": "Salon"})
        resolved = service.registry.update_client.await_args.args[0]

        service.registry.update_client.reset_mock()
        service._snapcast_service = None
        await service._handle_client_name_changed({"id": SNAPCAST_ID, "name": "Salon"})
        fallback = service.registry.update_client.await_args.args[0]

        assert resolved == fallback == SNAPCAST_ID

    async def test_a_status_that_cannot_be_read_still_applies_the_rename(
        self, service, snapcast, caplog
    ):
        """Losing the rename entirely is the failure the fallback prevents; the
        warning is the only thing that says the lookup was skipped."""
        snapcast.get_server_status = AsyncMock(side_effect=RuntimeError("rpc broke"))
        service.registry.update_client = AsyncMock()

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            await service._handle_client_name_changed({"id": SNAPCAST_ID, "name": "Salon"})

        assert any("Could not resolve mac_id" in r.message for r in caplog.records)
        assert service.registry.update_client.await_args.args[0] == SNAPCAST_ID
        assert service.registry.update_client.await_args.kwargs["name"] == "Salon"

    async def test_a_frame_with_no_name_changes_nothing(self, service):
        """snapweb can clear a name; overwriting the registry with an empty string
        would leave a speaker with no label anywhere in the UI."""
        service.registry.update_client = AsyncMock()

        await service._handle_client_name_changed({"id": SNAPCAST_ID, "name": ""})

        service.registry.update_client.assert_not_called()


class TestTheRequestPath:
    """Sending on the control socket, and the flag that quiets the admission burst."""

    async def test_a_send_that_fails_is_reported_not_raised(self, service, caplog):
        """`_send_request` is awaited from `_connect_and_listen` right after the
        socket opens; a raise there would be read as a connection failure and
        trigger the whole backoff for a socket that is fine."""
        ws = MagicMock()
        ws.send_str = AsyncMock(side_effect=ConnectionResetError("peer went away"))
        service.websocket = ws

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await service._send_request("Server.GetRPCVersion")

        assert any("Failed to send request" in r.message for r in caplog.records)

    async def test_the_init_phase_marks_its_notifications_apart(self, service, caplog):
        """A whole fleet re-admitting on one reconnection is normal traffic; the
        flag is what keeps it from reading as a burst of new arrivals."""
        service._is_initializing = True

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.websocket"):
            await service._handle_notification({"method": "Client.OnVolumeChanged", "params": {}})

        assert any("(init phase)" in r.message for r in caplog.records)


class TestTheRemainingAdmissionArms:
    """The fourth notification, the DAC read at boot, and the two idempotence guards."""

    async def test_a_known_client_reappearing_is_synced_before_it_is_shown_online(
        self, service, registry
    ):
        """The reconcile sweep's online flip is the fourth of the four paths that
        can be first to see a client. Setting online here instead of letting the
        sync do it opens a window where the UI offers controls at a stale level —
        and a failed sync then has nothing to retry it, because snapserver and
        the registry both already read "online"."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_online(MAC, False)
        synced = []
        service._sync_reconnecting_client_volume = AsyncMock(
            side_effect=lambda mac, **kw: synced.append((mac, kw))
        )

        await service._process_online_status_changes([_snapcast_client()])
        await asyncio.gather(*list(service._bg._tasks))

        assert registry.get_client(MAC).online is False, "shown online before the sync"
        assert synced == [(MAC, {"set_online_after": True, "snapcast_id": SNAPCAST_ID})]

    async def test_a_client_already_online_is_not_re_synced(self, service, registry):
        """This runs on every 30 s sweep. Re-syncing a client that never left
        re-applies its level, EQ and buffer config to a playing speaker."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_online(MAC, True)
        service._sync_reconnecting_client_volume = AsyncMock()

        await service._process_online_status_changes([_snapcast_client()])

        service._sync_reconnecting_client_volume.assert_not_called()

    async def test_the_local_client_takes_its_volume_control_from_the_hardware(
        self, service, registry, volume_service
    ):
        """DAC mode is a whole-appliance configuration: `volume_control=False`
        means an external amp owns the level and CamillaDSP must stay pinned at
        0 dB. Registering the local client without reading it would make a DAC
        unit register as managed — attenuating a signal the amp re-amplifies."""
        volume_service.volume_control = False

        await service._register_snapclient(MAC, "Milō", "127.0.0.1", HOST, is_local=True)

        assert registry.get_client(MAC).volume_control is False

    async def test_a_remote_client_does_not_inherit_the_servers_volume_control(
        self, service, registry, volume_service
    ):
        """The flag is per-client hardware. Passing the server's would flip every
        satellite to DAC mode on a server that runs one."""
        volume_service.volume_control = False

        await service._register_snapclient(MAC, "Canapé", IP, HOST, is_local=False)

        assert registry.get_client(MAC).volume_control is True

    async def test_starting_a_connection_that_is_already_starting_spawns_nothing(
        self, service
    ):
        """`start_connection` is called from the routing transition, which is
        re-entered by a retry. A second pair of loops would double every
        notification and every admission sync with it."""
        service.running = True
        service.should_connect = True

        await service.start_connection()

        assert service.reconnect_task is None
        assert service._bg._tasks == set()

    async def test_stopping_a_connection_that_is_already_stopped_touches_nothing(
        self, service
    ):
        """It runs on every multiroom disable, including the one the lifespan
        teardown triggers after `cleanup` already ran."""
        service.should_connect = False
        service.session = MagicMock()
        service._ready_event.set()

        await service.stop_connection()

        assert service._ready_event.is_set(), "a no-op stop cleared the ready flag"

    async def test_a_server_update_without_snapcast_cannot_reconcile_and_says_so(
        self, service, caplog
    ):
        """Reconciling against nothing would read as "every client disconnected"
        and take the whole fleet offline in the UI."""
        service._snapcast_service = None
        service._reconcile_clients = AsyncMock()

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.websocket"):
            await service._handle_server_update({})

        service._reconcile_clients.assert_not_called()
        assert any("not available for online status" in r.message for r in caplog.records)

    async def test_a_server_update_that_fails_does_not_escape_the_message_loop(
        self, service, snapcast, caplog
    ):
        """It is awaited from `_handle_notification`, inside the frame loop. An
        exception here is caught two levels up by `_handle_message`, which logs
        without naming the notification — this arm is what says which one."""
        snapcast.get_clients = AsyncMock(side_effect=RuntimeError("rpc broke"))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await service._handle_server_update({})

        assert any("Error handling Server.OnUpdate" in r.message for r in caplog.records)

    async def test_a_connection_that_fails_for_any_other_reason_is_an_error(
        self, service, monkeypatch, caplog
    ):
        """"Cannot connect" is normal — snapserver is stopped whenever multiroom
        is off. Anything else is a fault worth the operator banner."""
        session = MagicMock()
        session.ws_connect = AsyncMock(side_effect=RuntimeError("handshake refused"))
        service.session = session

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await service._connect_and_listen()

        assert any("WebSocket connection failed" in r.message for r in caplog.records)
        assert service.websocket is None


class TestWhatANewClientIsAdmittedAt:
    """The level a client nobody has ever set comes back at.

    Driven through the *real* VolumeStateStore, wired to the real registry in the
    order `dependencies.py` wires them (the store subscribes before the WS
    broadcaster). A mocked store cannot see this: the fault is that registering
    the client seeds a level in the store, so the resolver's second step —
    `startup_volume_db` — is answered before it is ever reached.
    """

    @pytest.fixture
    def store(self, tmp_path, registry):
        from backend.core.models.volume import VolumeConfig
        from backend.core.volume.state import VolumeStateStore

        settings = AsyncMock()
        settings.get_setting = AsyncMock(return_value=None)
        store = VolumeStateStore(settings)
        store.STORAGE_PATH = tmp_path / "last_volume.json"
        config = VolumeConfig()
        config.startup_volume_db = -20.0
        config.restore_last_volume = True
        store.set_volume_config(config)
        store.set_registry(registry)
        return store

    @pytest.fixture
    def service_with_store(self, service, store):
        service._volume_service.state_store = store
        service._volume_service.volume_config.restore_last_volume = True
        service._volume_service.volume_config.startup_volume_db = -20.0
        return service

    async def test_a_new_client_is_admitted_at_the_configured_startup_volume(
        self, service_with_store, registry, store
    ):
        """Not at DEFAULT_VOLUME_DB. A unit configured for a fixed startup level
        admitted every fresh satellite at -45 instead, because registering it
        already put -45 in the store and step 1 of the resolver found it there."""
        await registry.register_client(MAC, "Canapé", IP, host=HOST)

        assert service_with_store._resolve_target_volume(MAC) == -20.0

    async def test_registration_alone_does_not_make_a_client_available(
        self, registry, store
    ):
        """A client is registered offline on purpose — the hardware has not
        confirmed. `available` is what get_complete_state() averages over, so
        answering True here counted a speaker mid-admission, at a level nobody
        chose, in the global volume for the whole retry budget."""
        await registry.register_client(MAC, "Canapé", IP, host=HOST)
        assert store.is_client_available(MAC) is False

        await registry.set_client_online(MAC, True)
        assert store.is_client_available(MAC) is True

    async def test_a_client_that_was_set_before_keeps_its_own_level(
        self, service_with_store, registry, store
    ):
        """The seeding must not overwrite what the client owns — that is the
        whole volume-ownership rule, and the seed runs on every reconnection."""
        await store.set_client_volume(MAC, -66.0)
        await registry.register_client(MAC, "Canapé", IP, host=HOST)

        assert service_with_store._resolve_target_volume(MAC) == -66.0
