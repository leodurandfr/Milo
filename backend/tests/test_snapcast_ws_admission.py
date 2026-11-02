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
import time
from collections import Counter
from unittest.mock import AsyncMock, MagicMock, call

import aiohttp
import pytest

from backend.core.models.audio_state import AudioSource
from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.models import EqFilter, EqualizerSettings
from backend.core.multiroom.websocket import SnapcastWebSocketService
from backend.core.multiroom.snapcast import SnapcastService
from backend.tests.conftest import drain_background_tasks
from backend.tests.snapserver_world import FakeSnapserver

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
    service = MagicMock(spec=SnapcastService)
    service.set_volume = AsyncMock(return_value=True)
    service.set_latency = AsyncMock(return_value=True)
    service.get_server_status = AsyncMock(return_value={"server": {"groups": []}})
    service.extract_clients = MagicMock(return_value=[])
    service.group_of = MagicMock(return_value={
        "id": f"group-{SNAPCAST_ID}", "muted": False, "stream_id": "Multiroom",
        "clients": [{"id": SNAPCAST_ID}],
    })
    service.set_group_mute = AsyncMock(return_value=True)
    service.set_group_stream = AsyncMock(return_value=True)
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


@pytest.fixture
def store(tmp_path, registry):
    """The real VolumeStateStore, subscribed to the real registry."""
    from backend.core.models.volume import VolumeConfig
    from backend.core.volume.state import VolumeStateStore

    settings = AsyncMock()
    settings.get_setting = AsyncMock(return_value=None)
    store = VolumeStateStore()
    store.STORAGE_PATH = tmp_path / "last_volume.json"
    config = VolumeConfig()
    config.startup_volume_db = -20.0
    config.restore_last_volume = True
    store.set_volume_config(config)
    store.set_registry(registry)
    return store


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
        attenuation stage (invariant 7). Its volume no longer reaches the audio
        (`--mixer none`), but its mute still does, and snapserver keeps a mute
        across restarts: one set by any other JSON-RPC client would silence the
        room for good while Milō shows it unmuted (its group's mute: see
        TestTheGroupSnapserverKeeps). The per-client delay is the mirror: it is
        native Snapcast latency Milō owns, so a delay changed while the client
        was away has reached nobody until this call.
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

    async def test_a_refused_latency_is_retried_before_the_client_is_shown_online(
        self, service, registry, snapcast
    ):
        """`set_latency` answers a refusal with False, not an exception. Read
        before the attempts and never checked, it let the room come online
        without its delay — out of step with the others until its next
        admission, while every screen showed the delay in place."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_online(MAC, False)
        await registry.set_client_delay(MAC, 40)
        shown_online_at_each_push = []
        answers = iter([False, True])

        async def set_latency(client_id, ms):
            shown_online_at_each_push.append(registry.get_client(MAC).online)
            return next(answers)

        snapcast.set_latency = AsyncMock(side_effect=set_latency)

        ok = await service._sync_reconnecting_client_volume(
            MAC, set_online_after=True, max_retries=1, retry_delay=0.0,
            snapcast_id=SNAPCAST_ID,
        )

        assert snapcast.set_latency.await_args_list == [call(SNAPCAST_ID, 40)] * 2
        assert shown_online_at_each_push == [False, False]
        assert ok is True
        assert registry.get_client(MAC).online is True

    async def test_a_refused_passthrough_keeps_the_client_offline(
        self, service, registry, snapcast
    ):
        """`--mixer none` still obeys snapserver's mute, and snapserver keeps
        it across restarts: an admission that could not lift it and showed the
        room online anyway offered controls for a speaker that stays silent."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_online(MAC, False)
        snapcast.set_volume = AsyncMock(return_value=False)

        ok = await service._sync_reconnecting_client_volume(
            MAC, set_online_after=True, max_retries=0, retry_delay=0.0,
            snapcast_id=SNAPCAST_ID,
        )

        snapcast.set_volume.assert_awaited_once_with(SNAPCAST_ID, 100)
        assert ok is False
        assert registry.get_client(MAC).online is False

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


OTHER_ID, OTHER_IP = "aa:bb:cc:dd:ee:08", "192.168.1.60"


@pytest.fixture
def snapserver():
    return FakeSnapserver(clients={SNAPCAST_ID: IP, OTHER_ID: OTHER_IP})


@pytest.fixture
def admitted(service, registry, snapserver, monkeypatch):
    """The service on the real SnapcastService, which the fake snapserver answers,
    and every change of a client's online flag written to the snapserver's journal.

    Taken after `service`, whose `no_satellite_network` replaces
    `aiohttp.ClientSession`: snapserver's URL is answered by the fake, every
    other request by that stand-in.
    """
    snapcast = SnapcastService(systemd_manager=MagicMock(), host="snapserver.test")
    monkeypatch.setattr(
        aiohttp, "ClientSession", snapserver.client_session(snapcast.base_url, aiohttp.ClientSession)
    )
    service._snapcast_service = snapcast
    set_online = registry.set_client_online

    async def recorded(mac_id, online):
        snapserver.journal.append(("online", mac_id, online))
        return await set_online(mac_id, online)

    registry.set_client_online = recorded
    return service


def _rpcs(journal, method):
    return [(i, e[2]) for i, e in enumerate(journal) if e[:2] == ("rpc", method)]


def _shown_online(journal):
    return [i for i, e in enumerate(journal) if e[0] == "online" and e[2] is True]


async def _admit(service, registry, *, max_retries=0):
    await registry.register_client(MAC, "Canapé", IP)
    await registry.set_client_online(MAC, False)
    return await service._sync_reconnecting_client_volume(
        MAC, set_online_after=True, max_retries=max_retries, retry_delay=0.0,
        snapcast_id=SNAPCAST_ID,
    )


class TestTheGroupSnapserverKeeps:
    """What an admission puts right in the client's snapserver group.

    snapserver sends a client `client.muted || group.muted` and plays it the
    group's stream, and it keeps both in server.json across its restarts. No
    notification Milō handles carries the group (Client.OnConnect names the
    client alone), so the admission reads it from the status, at each attempt.
    """

    async def test_an_admission_lifts_a_group_mute(self, admitted, registry, snapserver):
        """A group muted by any JSON-RPC client (snapweb's group button, a
        home-automation integration) silenced the room for good: `--mixer none`
        obeys it, the admission lifted the client's mute only, and every screen
        showed the room playing. Only the client's own group is touched."""
        snapserver.group_of(SNAPCAST_ID)["muted"] = True
        snapserver.group_of(OTHER_ID)["muted"] = True

        assert await _admit(admitted, registry) is True

        assert snapserver.is_muted(SNAPCAST_ID) is False
        assert snapserver.group_of(OTHER_ID)["muted"] is True
        assert registry.get_client(MAC).online is True

    async def test_a_refused_group_unmute_keeps_the_client_offline(
        self, admitted, registry, snapserver
    ):
        """A room shown online over a mute snapserver kept offers controls for a
        speaker that stays silent: the refusal is a failed attempt, retried, and
        the client is shown online only after the mute was lifted."""
        snapserver.group_of(SNAPCAST_ID)["muted"] = True
        snapserver.refuse["Group.SetMute"] = 1

        assert await _admit(admitted, registry, max_retries=1) is True

        unmutes = _rpcs(snapserver.journal, "Group.SetMute")
        assert [params for _, params in unmutes] == [{"id": f"group-{SNAPCAST_ID}", "mute": False}] * 2
        assert _shown_online(snapserver.journal)[0] > unmutes[1][0]
        assert snapserver.is_muted(SNAPCAST_ID) is False

    async def test_a_status_snapserver_did_not_answer_is_never_read_as_no_mute(
        self, admitted, registry, snapserver
    ):
        """`get_server_status` is fail-open: it flattens a refusal to {}. Read as
        "the group is not muted", the room would be shown online under a mute
        nobody lifted."""
        snapserver.group_of(SNAPCAST_ID)["muted"] = True
        snapserver.refuse["Server.GetStatus"] = None

        assert await _admit(admitted, registry, max_retries=1) is False

        assert len(_rpcs(snapserver.journal, "Server.GetStatus")) == 2
        assert registry.get_client(MAC).online is False

    async def test_the_group_is_read_whatever_milo_counts_live(
        self, admitted, registry, snapserver
    ):
        """The group is snapserver's state for the client, read raw: the
        liveness rule (lastSeen, the stale local id, the MAC dedup) decides
        which speakers are shown, and a client it drops is still one whose room
        snapserver mutes. Read through it, such an admission failed every
        attempt."""
        snapserver.client(SNAPCAST_ID)["lastSeen"]["sec"] -= 60
        snapserver.group_of(SNAPCAST_ID)["muted"] = True

        assert await _admit(admitted, registry) is True

        assert snapserver.is_muted(SNAPCAST_ID) is False

    async def test_an_admission_points_the_clients_group_at_the_active_stream(
        self, admitted, registry, snapserver
    ):
        """A satellite that connects lands in a group snapserver kept on whatever
        stream it last had (or on its default, the meta stream). The source
        playing is the one its room must hear, before the room is shown online;
        the rooms already playing are not touched."""
        admitted.state_machine.system_state.active_source = AudioSource.RADIO

        assert await _admit(admitted, registry) is True

        assert snapserver.stream_of(SNAPCAST_ID) == "Radio"
        assert snapserver.stream_of(OTHER_ID) == "Multiroom"
        (pointed, _), = _rpcs(snapserver.journal, "Group.SetStream")
        assert _shown_online(snapserver.journal)[0] > pointed

    async def test_a_group_already_on_the_stream_is_not_moved(
        self, admitted, registry, snapserver
    ):
        """snapserver re-sends the stream to a group it is asked to move, even
        onto the one it plays: a room that is right costs no request."""
        admitted.state_machine.system_state.active_source = AudioSource.RADIO
        snapserver.group_of(SNAPCAST_ID)["stream_id"] = "Radio"

        assert await _admit(admitted, registry) is True

        assert _rpcs(snapserver.journal, "Group.SetStream") == []

    async def test_a_refused_stream_is_a_failed_attempt_and_retried(
        self, admitted, registry, snapserver
    ):
        """A room left on another stream plays the wrong source, or silence,
        under a screen that shows it in the house: the client is not shown
        online over a refusal."""
        admitted.state_machine.system_state.active_source = AudioSource.RADIO
        snapserver.refuse["Group.SetStream"] = 1

        assert await _admit(admitted, registry, max_retries=1) is True

        moves = _rpcs(snapserver.journal, "Group.SetStream")
        assert len(moves) == 2
        assert _shown_online(snapserver.journal)[0] > moves[1][0]
        assert snapserver.stream_of(SNAPCAST_ID) == "Radio"

    async def test_a_switch_during_the_pointing_is_followed(self, admitted, registry, snapserver):
        """The admission reads the active source, then waits on snapserver; a
        transition meanwhile points every group at its own stream, and the
        admission's answer can land after it. Read again, the new stream wins."""
        state = admitted.state_machine.system_state
        state.active_source = AudioSource.RADIO

        def switch(method, params):
            if method == "Group.SetStream" and params["stream_id"] == "Radio":
                state.active_source = AudioSource.PODCAST

        snapserver.on_request = switch

        assert await _admit(admitted, registry) is True

        assert [p["stream_id"] for _, p in _rpcs(snapserver.journal, "Group.SetStream")] == [
            "Radio", "Podcast",
        ]
        assert snapserver.stream_of(SNAPCAST_ID) == "Podcast"

    async def test_no_source_leaves_the_clients_group_alone(self, admitted, registry, snapserver):
        """Nothing plays, so there is no stream to choose: the room keeps the
        last one, which the next selection moves."""
        admitted.state_machine.system_state.active_source = AudioSource.NONE

        assert await _admit(admitted, registry) is True

        assert _rpcs(snapserver.journal, "Group.SetStream") == []
        assert snapserver.stream_of(SNAPCAST_ID) == "Multiroom"


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
    """Any other JSON-RPC client of snapserver can rename a client (one on the
    unit since the control moved to loopback; snapweb, from the LAN, before)."""

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

    async def test_a_known_client_live_at_connect_gets_the_mute_it_was_given_while_away(
        self, service, registry, snapcast, volume_service
    ):
        """A mute set while a satellite is offline is stored, not sent; its
        admission is what delivers it. A satellite that comes back during a
        multiroom toggle reconnects to the new snapserver before the socket
        opens, so its Client.OnConnect is never heard and the connect sweep is
        the only path that sees it. Readmitting a known client there without the
        sync left the speaker playing while every screen showed it muted."""
        await registry.register_client(MAC, "Canapé", IP)
        assert registry.get_client(MAC).online is False
        volume_service.state_store.get_client_mute = MagicMock(return_value=True)
        snapcast.get_server_status = AsyncMock(return_value={"server": {"groups": []}})
        snapcast.extract_clients = MagicMock(return_value=[_snapcast_client()])
        shown_online_when_muted = []

        async def mute(mac_id, muted, force=False):
            shown_online_when_muted.append(registry.get_client(MAC).online)
            return True

        volume_service.equalizer_controller.set_equalizer_mute = AsyncMock(side_effect=mute)

        await service._initialize_existing_clients()
        await asyncio.gather(*list(service._bg._tasks))

        volume_service.equalizer_controller.set_equalizer_mute.assert_awaited_once_with(
            MAC, True, force=True
        )
        assert shown_online_when_muted == [False], "shown online before its mute landed"
        assert registry.get_client(MAC).online is True

    async def test_a_retry_takes_the_level_a_move_gave_the_room_while_it_waited(
        self, service, registry, volume_service
    ):
        """An away room moves with its zone, and comes back where its zone went.

        The target used to be resolved once, before the attempts: a zone move
        landing between a failed attempt and its retry was overwritten with the
        level the room had when the admission began.
        """
        await registry.register_client(MAC, "Canapé", IP)
        volume_service.volume_config.restore_last_volume = True
        stored = {MAC: -40.0}
        volume_service.state_store.get_client_volume = MagicMock(side_effect=stored.get)
        targets = []

        async def apply(mac_id, target_db):
            targets.append(target_db)
            if len(targets) == 1:
                stored[MAC] = -50.0  # the zone moved while this attempt failed
                return False
            return True

        service._apply_target_volume_to_client = apply
        service._sync_standalone_equalizer_to_client = AsyncMock(return_value=True)
        service._sync_client_gain_to_client = AsyncMock()

        assert await service._do_sync_reconnecting_client_volume(
            MAC, set_online_after=False, max_retries=1, retry_delay=0.0
        ) is True
        assert targets == [-40.0, -50.0]

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
        self, service, snapcast, registry, caplog
    ):
        """It is awaited from `_handle_notification`, inside the frame loop. An
        exception here is caught two levels up by `_handle_message`, which logs
        without naming the notification — this arm is what says which one.

        The parse raises for real when the local client's MAC cannot be read,
        and a pass that could not parse moves nobody: the client list it would
        have reconciled against does not exist."""
        await registry.register_client(MAC, "Canapé", IP, host=HOST)
        await registry.set_client_online(MAC, True)
        snapcast.extract_clients = MagicMock(
            side_effect=RuntimeError("Cannot determine local MAC address")
        )

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await service._handle_server_update({})

        assert any("Error handling Server.OnUpdate" in r.message for r in caplog.records)
        assert registry.get_client(MAC).online is True

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


SNAPWEB_ID = "3f1c2a8e-7b4d-4e2a-9c1f-5d6e7f8a9b0c"


def _connect_frame(*, client_id, host_name, mac, arch="aarch64", name="", ip="192.168.1.42"):
    """A `Client.OnConnect` frame as snapserver 0.35 sends it."""
    client = {
        "id": client_id,
        "connected": True,
        "config": {"name": name, "volume": {"percent": 100, "muted": False}},
        "host": {"name": host_name, "ip": f"::ffff:{ip}", "mac": mac, "arch": arch},
        "lastSeen": {"sec": int(time.time()), "usec": 0},
    }
    return {"jsonrpc": "2.0", "method": "Client.OnConnect",
            "params": {"id": client_id, "client": client}}


class TestSnapwebIsNotASpeaker:
    """snapserver's in-browser player is one of its clients, and never a room.

    snapweb's player says hello as a snapclient (`hostname` "Snapweb client",
    `mac` all zeros, `arch` "web", a UUID it keeps in the browser as its id), so
    snapserver lists it and announces it like any speaker. Nothing behind its
    address answers Milō: admitted, it is a room that is offline forever, written
    to settings.json, listed by every multiroom screen and by Milo-Mac's
    `GET /api/multiroom/state`, and back after every deletion. The status walk
    refused it; `Client.OnConnect`, the fourth admission path, did not.
    """

    async def test_a_snapweb_on_connect_is_never_admitted(
        self, service, volume_service, no_satellite_network, caplog
    ):
        settings = AsyncMock()
        settings.get_setting = AsyncMock(return_value=None)
        reg = ClientRegistryService(settings_service=settings)
        await reg.initialize()
        service.set_registry(reg)

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await service._handle_message(_connect_frame(
                client_id=SNAPWEB_ID, host_name="Snapweb client",
                mac="00:00:00:00:00:00", arch="web",
            ))
            await drain_background_tasks()

            assert reg.get_all_clients() == {}
            settings.set_setting.assert_not_awaited()
            settings.set_settings.assert_not_awaited()
            assert no_satellite_network == []
            volume_service.equalizer_controller.set_equalizer_volume.assert_not_awaited()

            # The same frame from a speaker is admitted: `_handle_message` turns
            # any exception into a log line, so a frame the handler could not
            # read would pass the half above on nothing.
            await service._handle_message(_connect_frame(
                client_id=MAC, host_name=HOST, mac=MAC, ip=IP,
            ))
            await drain_background_tasks()

        assert set(reg.get_all_clients()) == {MAC}
        settings.set_settings.assert_awaited()
        volume_service.equalizer_controller.set_equalizer_volume.assert_awaited()
        assert not any("Error handling message" in r.message for r in caplog.records)

    @pytest.mark.parametrize("name, host_name", [
        ("", "Snapweb client"),
        ("snapweb", HOST),
        ("Kitchen", HOST),
        ("", HOST),
    ])
    async def test_on_connect_and_the_status_walk_judge_a_name_alike(
        self, service, registry, name, host_name
    ):
        """"One client, one admission": whichever notification sees a client
        first, what its name decides is the same. An entry one path admits and
        another refuses is a client whose presence in the room list depends on
        a boot-order race. Only the name varies here, on a live speaker entry."""
        frame = _connect_frame(client_id=MAC, host_name=host_name, mac=MAC, name=name, ip=IP)
        status = {"server": {"groups": [{"clients": [frame["params"]["client"]]}]}}

        by_status_walk = [
            c["mac_id"]
            for c in SnapcastService(systemd_manager=MagicMock()).extract_clients(status)
        ]
        await service._handle_client_connect(frame["params"])
        await drain_background_tasks()

        assert list(registry.get_all_clients()) == by_status_walk


ZERO_ID = "00:00:00:00:00:00"


def _disconnect_frame(**kwargs):
    """A `Client.OnDisconnect` frame: the same client object as the connect."""
    frame = _connect_frame(**kwargs)
    frame["method"] = "Client.OnDisconnect"
    frame["params"]["client"]["connected"] = False
    return frame


class TestAnEntryWithNoUsableId:
    """A remote snapclient started without Milō's `--hostID` announces an
    all-zero id, which no identity can be derived from.

    The status walk skips such an entry with a warning; the two notifications
    that carry one entry each must answer the same. Before, they let
    compute_mac_id's ValueError reach `_handle_message`, which logs it at ERROR
    — a UI banner at every connect and every disconnect of that client.
    """

    @pytest.mark.parametrize("client_id", [MAC, ZERO_ID, ""])
    async def test_on_connect_and_the_status_walk_judge_an_identity_alike(
        self, service, registry, client_id
    ):
        """"One client, one admission", for the identity as for the name: an
        entry the status walk skips is not registered by Client.OnConnect
        either, and one it keys is keyed the same."""
        frame = _connect_frame(client_id=client_id, host_name=HOST, mac=MAC, ip=IP)
        status = {"server": {"groups": [{"clients": [frame["params"]["client"]]}]}}

        by_status_walk = [
            c["mac_id"]
            for c in SnapcastService(systemd_manager=MagicMock()).extract_clients(status)
        ]
        await service._handle_client_connect(frame["params"])
        await drain_background_tasks()

        assert list(registry.get_all_clients()) == by_status_walk

    async def test_its_connect_and_disconnect_are_warnings_that_change_nothing(
        self, service, registry, caplog
    ):
        await registry.register_client(MAC, "Bureau", "192.168.1.60", host="milo-client")
        await registry.set_client_online(MAC, True)

        with caplog.at_level(logging.WARNING):
            await service._handle_message(_connect_frame(client_id=ZERO_ID, host_name=HOST,
                                                         mac=ZERO_ID, ip=IP))
            await drain_background_tasks()
            await service._handle_message(_disconnect_frame(client_id=ZERO_ID, host_name=HOST,
                                                            mac=ZERO_ID, ip=IP))
            await drain_background_tasks()

        assert [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR] == []
        named = [r for r in caplog.records
                 if r.levelno == logging.WARNING and IP in r.getMessage()]
        assert len(named) == 2, [r.getMessage() for r in caplog.records]
        assert set(registry.get_all_clients()) == {MAC}
        assert registry.get_client(MAC).online is True

    async def test_the_local_entry_whose_mac_cannot_be_read_still_fails_loudly(
        self, service, registry, monkeypatch, caplog
    ):
        """The skip is for a remote entry. /sys unreadable on this unit is a
        fault of the unit, and the status walk fails its whole parse on it
        rather than skip the appliance's own speaker."""
        monkeypatch.setattr("backend.core.multiroom.identity.get_local_mac", lambda: None)

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.websocket"):
            await service._handle_message(_connect_frame(client_id="aa:bb:cc:dd:ee:99",
                                                         host_name="milo", mac=MAC,
                                                         ip="127.0.0.1"))
            await drain_background_tasks()

        assert registry.get_all_clients() == {}
        assert any("Cannot determine local MAC" in r.getMessage() for r in caplog.records)


class TestWhatANewClientIsAdmittedAt:
    """The level a client nobody has ever set comes back at.

    Driven through the *real* VolumeStateStore, wired to the real registry in the
    order `dependencies.py` wires them (the store subscribes before the WS
    broadcaster). A mocked store cannot see this: the fault is that registering
    the client seeds a level in the store, so the resolver's second step —
    `startup_volume_db` — is answered before it is ever reached.
    """

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


class TestAClientDeletedDuringItsAdmission:
    """A satellite deleted while its admission waits: between two attempts, or on snapserver's pin.

    A satellite whose snapclient is up but whose API refuses (an app update gone
    wrong, a reboot before the API answers) keeps its admission retrying, and the
    offline client screen offers only Delete, so a delete landing in a retry wait
    is the ordinary way to meet this. Driven through the real registry, store,
    controller and router over a mocked CamillaDSP and a refusing satellite: the
    fault is the store recreating the level the delete removed, and the router
    reading the unknown mac as direct mode.
    """

    async def test_a_client_deleted_while_its_admission_retries_never_reaches_the_local_dsp(
        self, service, registry, store, monkeypatch
    ):
        """What it cost: the satellite's startup level and an unmute went to the
        server's CamillaDSP, so a muted main room came back at -20 dB, while the
        store and every screen still showed it muted and the admission answered
        True."""
        from backend.core.multiroom.equalizer_router import EqualizerRouter
        from backend.core.volume.equalizer_controller import EqualizerController

        monkeypatch.setattr(
            "backend.core.multiroom.equalizer_router.get_local_mac",
            lambda: "aa:bb:cc:dd:ee:ff",
        )
        dsp = MagicMock()
        dsp.set_volume = AsyncMock(return_value=True)
        dsp.set_mute = AsyncMock(return_value=True)
        proxy = MagicMock()
        proxy.request = AsyncMock(return_value={"status": "error"})
        service._volume_service.state_store = store
        service._volume_service.equalizer_controller = EqualizerController(
            equalizer_router=EqualizerRouter(registry, dsp, proxy),
            client_registry=registry,
        )
        await registry.register_client(MAC, "Canapé", IP, host=HOST)
        asked_before_the_delete = []

        async def _sleep(delay, *a, **k):
            if delay == 3.0 and registry.get_client(MAC):
                asked_before_the_delete.append(proxy.request.await_count)
                await registry.unregister_client(MAC)
            await _REAL_SLEEP(0)

        monkeypatch.setattr(asyncio, "sleep", _sleep)

        ok = await service._do_sync_reconnecting_client_volume(
            MAC, set_online_after=True, max_retries=5, retry_delay=3.0
        )

        # Non-triviality first: the satellite was asked and refused, so the
        # delete landed in a retry wait and not before the first attempt.
        assert asked_before_the_delete and asked_before_the_delete[0] > 0
        assert ok is False
        dsp.set_volume.assert_not_awaited()
        dsp.set_mute.assert_not_awaited()
        assert store.has_client(MAC) is False
        assert proxy.request.await_count == asked_before_the_delete[0]

    async def test_a_client_deleted_while_snapserver_is_pinned_leaves_no_level_behind(
        self, service, registry, store, snapcast, monkeypatch
    ):
        """What it cost: the pin awaits snapserver after the attempt's registry
        check, so a delete landing there was followed by the apply, which wrote
        the startup level back into the store, unmuted: a client nobody holds,
        moved by every global move, published in volume_changed, and the level
        a re-added satellite came back at instead of the startup one."""
        from backend.core.multiroom.equalizer_router import EqualizerRouter
        from backend.core.volume.equalizer_controller import EqualizerController

        monkeypatch.setattr(
            "backend.core.multiroom.equalizer_router.get_local_mac",
            lambda: "aa:bb:cc:dd:ee:ff",
        )
        dsp = MagicMock()
        dsp.set_volume = AsyncMock(return_value=True)
        dsp.set_mute = AsyncMock(return_value=True)
        proxy = MagicMock()
        proxy.request = AsyncMock(return_value={"status": "success"})
        service._volume_service.state_store = store
        service._volume_service.equalizer_controller = EqualizerController(
            equalizer_router=EqualizerRouter(registry, dsp, proxy),
            client_registry=registry,
        )
        await registry.register_client(MAC, "Canapé", IP, host=HOST)
        held_before_the_delete = []

        async def _lift_the_mute(snapcast_id, percent):
            held_before_the_delete.append(store.has_client(MAC))
            await registry.unregister_client(MAC)
            return True

        snapcast.set_volume = AsyncMock(side_effect=_lift_the_mute)

        ok = await service._do_sync_reconnecting_client_volume(
            MAC, set_online_after=True, max_retries=5, retry_delay=0,
            snapcast_id=SNAPCAST_ID,
        )

        # Non-triviality first: the delete landed inside the pin, on a client
        # the store held a level for, after the attempt's own check passed.
        assert held_before_the_delete == [True]
        assert ok is False
        assert store.has_client(MAC) is False
        dsp.set_volume.assert_not_awaited()
        dsp.set_mute.assert_not_awaited()
        proxy.request.assert_not_awaited()


OTHER = "aa:bb:cc:dd:ee:08"
OTHER_IP = "192.168.1.60"
ZONE = "z1"


class _Satellite:
    """A satellite's HTTP surface: what each address last took on each path.

    `hold(path)` keeps the first call to that path — or to "record", the whole
    EQ record — waiting until `release()`, which is how a write lands while an
    admission is still pushing. A held call lands when it is released, so
    `last` is in arrival order. While `refuses_records` is set, a record is
    answered False and never lands, as a satellite whose CamillaDSP is down.
    """

    def __init__(self):
        self.last = {}
        self.records = []
        self.calls = Counter()
        self.reached = asyncio.Event()
        self.refuses_records = False
        self._held = None
        self._released = asyncio.Event()

    def hold(self, path):
        self._held = path

    def release(self):
        self._released.set()

    async def _gate(self, path):
        if path == self._held and not self.reached.is_set():
            self.reached.set()
            await self._released.wait()

    async def request(self, hostname, method, path, body=None, skip_multiroom_check=False):
        self.calls[(hostname, path)] += 1
        await self._gate(path)
        self.last[(hostname, path)] = body
        return {"status": "success"}

    async def apply_record(self, hostname, settings):
        self.calls[(hostname, "record")] += 1
        await self._gate("record")
        if self.refuses_records:
            return False
        self.records.append((hostname, settings.to_dict()))
        return True


class TestWhatChangesWhileAClientIsAdmitted:
    """A setting changed between the admission's read and the client going online.

    The admission reads each setting once and pushes it while the client is
    still offline on purpose; a write made meanwhile — a zone mute or move, a
    delay, an EQ edit, a trim — is stored only, since the router and the routes
    leave an offline client to its admission. Measured on the unit, the record
    push alone keeps that window open 185 to 350 ms, and a whole fleet is
    readmitted at once after a power cut. Driven through the real registry,
    volume service, controller, router and EQ service over a simulated
    satellite and snapserver: what breaks is a speaker shown online, and drawn
    muted or moved on every screen, that plays the state from before the write.
    """

    @pytest.fixture
    async def world(self, registry, snapcast, tmp_path, monkeypatch, no_satellite_network):
        from types import SimpleNamespace

        from backend.core.equalizer.multiroom_service import MultiroomEqualizerService
        from backend.core.multiroom.equalizer_router import EqualizerRouter
        from backend.core.volume import VolumeService, VolumeStateStore

        monkeypatch.setattr(VolumeStateStore, "STORAGE_PATH", tmp_path / "last_volume.json")
        satellite = _Satellite()
        dsp = MagicMock()  # no local client here: nothing may reach it
        state_machine = MagicMock()
        state_machine.broadcast = AsyncMock()
        router = EqualizerRouter(
            client_registry=registry, camilladsp_service=dsp, proxy_service=satellite
        )
        volume = VolumeService(
            state_machine=state_machine,
            snapcast_service=MagicMock(),
            settings_service=AsyncMock(),
            camilladsp_service=dsp,
            equalizer_client_proxy_service=satellite,
            equalizer_router=router,
        )
        router.set_local_volume_control_source(lambda: volume.volume_control)
        volume.set_routing_service(
            MagicMock(get_state=MagicMock(return_value={"multiroom_enabled": True}))
        )
        volume.attach_registry(registry)
        equalizer = MultiroomEqualizerService(
            client_registry_service=registry, camilladsp_service=dsp,
            proxy_service=satellite, equalizer_router=router,
        )
        service = SnapcastWebSocketService(
            state_machine=state_machine,
            routing_service=MagicMock(),
            snapcast_service=snapcast,
            equalizer_client_proxy_service=satellite,
        )
        service.set_registry(registry)
        service.set_volume_service(volume)

        await registry.register_client(MAC, "Canapé", IP, host=HOST)
        await registry.register_client(OTHER, "Bureau", OTHER_IP)
        await registry.set_client_equalizer(MAC, _eq_record())
        await registry.create_zone(ZONE, "Salon", [MAC, OTHER])

        async def admit(write, hold="record"):
            """Admit MAC, making `write` while the push of `hold` is in flight."""
            satellite.hold(hold)
            admission = asyncio.create_task(service._sync_reconnecting_client_volume(
                MAC, set_online_after=True, retry_delay=0.0, snapcast_id=SNAPCAST_ID,
            ))
            reached = asyncio.create_task(satellite.reached.wait())
            await asyncio.wait({admission, reached}, return_when=asyncio.FIRST_COMPLETED)
            # Non-triviality: the write lands mid-admission, before the online flip.
            assert reached.done(), f"the admission never pushed {hold!r}"
            assert registry.get_client(MAC).online is False
            await write()
            satellite.release()
            assert await admission is True
            assert registry.get_client(MAC).online is True

        return SimpleNamespace(
            service=service, registry=registry, volume=volume, store=volume.state_store,
            equalizer=equalizer, satellite=satellite, snapcast=snapcast, admit=admit,
        )

    async def test_a_zone_mute_made_while_the_record_is_pushed_reaches_the_speaker(self, world):
        """The scenario as reported: the room plays unmuted while the kiosk,
        Milo-Mac and Milo-iOS all draw it muted, until someone touches it."""
        await world.admit(lambda: world.volume.set_zone_mute(ZONE, True))

        assert world.store.get_client_mute(MAC) is True, "the zone mute was not stored"
        assert world.satellite.last[(IP, "/equalizer/mute")] == {
            "muted": world.store.get_client_mute(MAC)
        }

    async def test_a_zone_move_made_while_the_record_is_pushed_reaches_the_speaker(self, world):
        """A room that is away comes back where its room went — and a relative
        step taken later would otherwise jump from a level it never played."""
        moved = []

        async def move():
            await world.volume.apply_zone_volume_delta(ZONE, -10.0)
            moved.append(world.store.get_client_volume(MAC))

        await world.admit(move)

        assert world.store.get_client_volume(MAC) == moved[0]
        assert world.satellite.last[(IP, "/equalizer/volume")] == {
            "volume": world.store.get_client_volume(MAC)
        }

    async def test_a_zone_move_made_while_snapserver_is_pinned_reaches_the_speaker(
        self, world, snapcast
    ):
        """The level is read after the snapserver pin, not before: the apply
        writes the level it is given back to the store, so one read before the
        pin's await put the room back where it stood and lost the move."""
        moved = []
        pinned = asyncio.Event()
        release = asyncio.Event()

        async def set_volume(client_id, percent):
            pinned.set()
            await release.wait()
            return True

        snapcast.set_volume = AsyncMock(side_effect=set_volume)
        admission = asyncio.create_task(world.service._sync_reconnecting_client_volume(
            MAC, set_online_after=True, retry_delay=0.0, snapcast_id=SNAPCAST_ID,
        ))
        await pinned.wait()
        await world.volume.apply_zone_volume_delta(ZONE, -10.0)
        moved.append(world.store.get_client_volume(MAC))
        release.set()
        assert await admission is True

        assert world.store.get_client_volume(MAC) == moved[0]
        assert world.satellite.last[(IP, "/equalizer/volume")] == {"volume": moved[0]}

    async def test_a_delay_set_while_the_record_is_pushed_reaches_snapserver(self, world, snapcast):
        """Out of step with the other rooms until its next admission, while the
        client screen shows the delay in place."""
        await world.admit(lambda: world.registry.set_client_delay(MAC, 40))

        assert snapcast.set_latency.await_args_list[-1] == call(
            SNAPCAST_ID, world.registry.get_client(MAC).delay_ms
        )

    async def test_an_eq_edit_made_during_the_admission_push_reaches_the_satellite(self, world):
        """The record read before the edit is the one the satellite keeps: mono
        switched on mid-admission stayed stereo in the room."""
        async def edit():
            record = await world.equalizer.get_client_eq(MAC)
            record.mono = True
            await world.equalizer.set_client_eq(MAC, record)

        await world.admit(edit)

        assert world.registry.get_client_equalizer(MAC).mono is True, "the edit was not stored"
        assert world.satellite.records[-1] == (
            IP, world.registry.get_client_equalizer(MAC).to_dict()
        )

    async def test_a_trim_changed_during_the_admission_push_reaches_the_satellite(self, world):
        """What the gain route does for a client that is offline: store it and
        leave it to the admission — which had already read it."""
        await world.admit(
            lambda: world.registry.set_client_gain(MAC, -6.0), hold="/equalizer/gain"
        )

        assert world.satellite.last[(IP, "/equalizer/gain")] == {
            "gain_db": world.registry.get_client(MAC).gain_db
        }

    async def test_an_admission_nobody_touched_sends_each_setting_once(self, world, snapcast):
        """The pass after the online flip sends only what moved: a second push
        of an unchanged record is five PUTs to a speaker that is playing."""
        async def nothing():
            return None

        await world.admit(nothing)

        assert snapcast.set_latency.await_count == 1
        for setting in ("/equalizer/volume", "/equalizer/mute", "/equalizer/gain", "record"):
            assert world.satellite.calls[(IP, setting)] == 1, setting


class RetriedPastItsBound(BaseException):
    """The crossover's pending retries slept more often than they declare.

    BaseException-derived so `_delayed_retry_pending`'s `@handle_errors` cannot
    swallow it: the unbounded loop ends instead of hanging the drain.
    """


class TestAPendingRecordAcrossAdmissions:
    """The EQ record a refused admission push queued, replayed at a later one.

    CLIENT_CONNECTED replays the pending queue right after the admission pushed
    the record the registry holds, so the replay is the last record to land.
    Driven through the real registry, the crossover service that owns the queue
    and the EQ service, over a simulated satellite: what breaks is a speaker
    playing the EQ from before an edit while the kiosk, Milo-Mac and the
    registry all show the edit, with nothing logged.
    """

    async def test_an_admission_push_is_not_undone_by_an_older_pending_record(
        self, registry, snapcast, volume_service, monkeypatch, no_satellite_network
    ):
        from backend.core.equalizer.multiroom_service import MultiroomEqualizerService
        from backend.core.multiroom.crossover import CrossoverService

        satellite = _Satellite()
        crossover = CrossoverService(proxy_service=satellite)
        crossover.set_registry(registry)
        state_machine = MagicMock()
        state_machine.broadcast = AsyncMock()
        service = SnapcastWebSocketService(
            state_machine=state_machine,
            routing_service=MagicMock(),
            snapcast_service=snapcast,
            crossover_service=crossover,
            equalizer_client_proxy_service=satellite,
        )
        volume_service.equalizer_controller.set_equalizer_gain = AsyncMock(return_value=True)
        service.set_registry(registry)
        service.set_volume_service(volume_service)
        equalizer = MultiroomEqualizerService(client_registry_service=registry, proxy_service=satellite)

        waits = []

        async def _sleep(delay, *args, **kwargs):
            waits.append(delay)
            if len(waits) > 10:
                raise RetriedPastItsBound
            await _REAL_SLEEP(0)

        monkeypatch.setattr(asyncio, "sleep", _sleep)

        async def admit():
            assert await service._sync_reconnecting_client_volume(
                MAC, set_online_after=True, retry_delay=0.0, snapcast_id=SNAPCAST_ID,
            ) is True
            await drain_background_tasks()

        await registry.register_client(MAC, "Canapé", IP, host=HOST)
        await registry.set_client_equalizer(MAC, EqualizerSettings(
            filters=[EqFilter(id="eq_band_00", frequency=1000, gain=-6.0)]
        ))
        queued = registry.get_client_equalizer(MAC).to_dict()

        # Its DSP down: the admission push, the replay on CLIENT_CONNECTED and
        # the delayed retries all fail, and the record stays queued.
        satellite.refuses_records = True
        await admit()
        assert crossover.has_pending_settings(MAC), "the refused record was not queued"

        satellite.refuses_records = False
        record = await equalizer.get_client_eq(MAC)
        record.filters[0].gain = 4.0
        assert await equalizer.set_client_eq(MAC, record) is True

        await registry.set_client_online(MAC, False)
        await admit()

        assert len(waits) <= 10, "the pending retries never ended"
        assert registry.get_client_equalizer(MAC).to_dict() != queued, "the edit was not stored"
        assert satellite.records[-1] == (IP, registry.get_client_equalizer(MAC).to_dict())


class TestAClientSeenAgainAfterTheSocketDropped:
    """A client still there when the socket comes back goes through the sync again."""

    async def test_a_client_seen_before_the_socket_dropped_is_readmitted_through_the_sync(
        self, service, registry, snapcast, volume_service
    ):
        """Between two connections nobody watched it: its CamillaDSP may have
        restarted muted (`-m`), or a re-admission may have given up. Held online
        across the gap, it was skipped by the online flip — which only brings
        back a client the registry holds offline — and never synced again, so a
        silent speaker stayed on every screen as playing."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_online(MAC, True)
        service.should_connect = True

        await service.stop_connection()
        await service._reconcile_clients([_snapcast_client()])
        await drain_background_tasks()

        snapcast.set_latency.assert_awaited()
        volume_service.equalizer_controller.set_equalizer_volume.assert_awaited_with(
            MAC, -20.0, force=True
        )
        assert registry.get_client(MAC).online is True
