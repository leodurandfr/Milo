"""The crossover service's retry machine, and the arms that decide whether a
zone's filters ever reach a speaker.

What breaks when these fail: a crossover filter that never lands is a subwoofer
playing the full range next to satellites that are also playing it — audible in
the room, invisible everywhere else. The push is fire-and-forget from a registry
event, so nothing above notices, and the only recovery paths are the ones here.

Measured 2026-08-27, three things had never run:

* **the retry machine.** `_create_retry_task` cancels a client's previous retry
  before starting a new one, and `_delayed_retry_pending` gives up after a bounded
  number of attempts. Two retries running for the same client push the same
  filters twice; one that never gives up holds a task per client for the life of
  the process.
* **every failure arm of the registry-event dispatch.** This service is one of
  the three subscribers to the internal registry bus, and its handler is a
  single `if/elif` chain over event types. An exception in one arm that escaped
  would take down the notification for every other subscriber behind it in the
  subscription order.
* **the refusals of the proxy path.** A client with no address, no proxy service
  or an offline registry entry must be *queued*, not reported as pushed — the
  queue is what the reconnection replays.
"""
import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.core.multiroom.crossover import CrossoverService
from backend.core.multiroom.models import (
    DEFAULT_CROSSOVER_FREQUENCY,
    Client,
    RegistryEventType,
    Zone,
)

MAC = "aa:bb:cc:dd:ee:07"
IP = "192.168.1.153"
_REAL_SLEEP = asyncio.sleep


class RetriedPastItsBound(BaseException):
    """The retry loop ran past the number of attempts it declares.

    BaseException-derived: `_delayed_retry_pending` is decorated with
    `@handle_errors(default=None, level='debug')`, so an ordinary error would be
    swallowed at DEBUG and an unbounded loop would read as a hung suite instead
    of a red test.
    """


@pytest.fixture
def registry():
    reg = MagicMock()
    reg._clients = {}
    reg._zones = {}
    reg.get_client = MagicMock(side_effect=lambda m: reg._clients.get(m))
    reg.get_zone = MagicMock(side_effect=lambda z: reg._zones.get(z))
    reg.get_zone_for_client = MagicMock(side_effect=lambda m: next(
        (z for z in reg._zones.values() if m in z.client_ids), None
    ))
    reg.is_client_online = MagicMock(
        side_effect=lambda m: bool(reg._clients.get(m) and reg._clients[m].online)
    )
    reg.zone_to_enriched_dict = MagicMock(side_effect=lambda z: z.to_dict())
    reg.auto_crossover_frequency = MagicMock(return_value=80)
    reg.subscribe = MagicMock()
    reg.update_zone = AsyncMock()
    return reg


@pytest.fixture
def service(registry):
    proxy = MagicMock()
    proxy.try_request = AsyncMock(return_value=200)
    proxy.apply_record = AsyncMock(return_value=True)
    svc = CrossoverService(
        settings_service=AsyncMock(),
        camilladsp_service=AsyncMock(),
        proxy_service=proxy,
    )
    svc.state_machine = MagicMock()
    svc.state_machine.broadcast = AsyncMock()
    svc.set_registry(registry)
    return svc


def _client(*, mac=MAC, ip=IP, online=True, speaker_type="bookshelf"):
    return Client(mac_id=mac, name="Canapé", ip=ip, online=online, speaker_type=speaker_type)


class TestTheRetryTask:
    """One retry per client, and it must end."""

    async def test_a_new_retry_cancels_the_one_it_replaces(self, service):
        """`CLIENT_CONNECTED` can fire twice in a row for a flapping satellite.
        Two live retries push the same filters at the same speaker, and the queue
        each replays from is popped by whichever wins."""
        parked = asyncio.Event()

        async def _first():
            await parked.wait()

        first = service._create_retry_task(MAC, _first())
        second = service._create_retry_task(MAC, _first())
        await _REAL_SLEEP(0)

        assert first.cancelled() or first.done()
        assert service._retry_tasks[MAC] is second
        second.cancel()
        await asyncio.gather(second, return_exceptions=True)
        parked.set()

    async def test_two_clients_keep_their_own_retries(self, service):
        """The cancellation is keyed by client; cancelling across clients would
        drop a whole room's pending filters because another one reconnected."""
        parked = asyncio.Event()

        async def _park():
            await parked.wait()

        a = service._create_retry_task(MAC, _park())
        b = service._create_retry_task("aa:bb:cc:dd:ee:08", _park())
        await _REAL_SLEEP(0)

        assert not a.done() and not b.done()
        for t in (a, b):
            t.cancel()
        await asyncio.gather(a, b, return_exceptions=True)
        parked.set()

    async def test_the_retry_waits_before_each_attempt_and_then_gives_up(
        self, service, monkeypatch, caplog
    ):
        """The reason it exists is CamillaDSP not being ready yet after an audio
        card change; retrying without a wait would just re-fail immediately.
        Giving up is what stops one unreachable satellite from holding a task
        forever — and the warning is the only record that it was never applied.
        """
        waits = []
        attempts = {"n": 0}

        async def _sleep(delay, *a, **k):
            attempts["n"] += 1
            if attempts["n"] > 5:
                raise RetriedPastItsBound("the retry loop never terminated")
            waits.append(delay)
            await _REAL_SLEEP(0)

        monkeypatch.setattr(asyncio, "sleep", _sleep)
        await service.queue_pending_settings(MAC, "crossover", {"enabled": True, "frequency": 80})
        service.apply_pending_settings = AsyncMock(return_value=False)

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.crossover"):
            await service._delayed_retry_pending(MAC, max_retries=3, retry_delay=5.0)

        assert waits == [5.0, 5.0, 5.0]
        assert service.apply_pending_settings.await_count == 3
        assert any("still not applied after 3 retries" in r.message for r in caplog.records)

    async def test_a_queue_emptied_by_another_path_ends_the_retry_early(
        self, service, monkeypatch
    ):
        """A fresh `CLIENT_CONNECTED` applies the same queue. Continuing would
        re-push filters the client already took, and each push is an HTTP call to
        a satellite that is busy coming back."""
        attempts = {"n": 0}

        async def _sleep(delay, *a, **k):
            attempts["n"] += 1
            if attempts["n"] > 5:
                raise RetriedPastItsBound("the retry loop never terminated")
            await _REAL_SLEEP(0)

        monkeypatch.setattr(asyncio, "sleep", _sleep)
        service.apply_pending_settings = AsyncMock(return_value=True)

        await service._delayed_retry_pending(MAC, max_retries=3, retry_delay=5.0)

        service.apply_pending_settings.assert_not_called()
        assert attempts["n"] == 1, "it did not stop as soon as the queue was empty"

    async def test_a_reconnection_that_still_cannot_apply_schedules_a_retry(
        self, service, registry
    ):
        """The link between the two: recalculation queues the settings again, and
        this is what notices and arms the delayed replay."""
        registry._clients[MAC] = _client()
        await service.queue_pending_settings(MAC, "crossover", {"enabled": True, "frequency": 80})
        service.apply_pending_settings = AsyncMock(return_value=False)

        async def _requeue(mac_id):
            # What the real recalculation does when the push fails: the settings
            # go back on the queue, which is exactly the state the retry is for.
            await service.queue_pending_settings(
                mac_id, "crossover", {"enabled": True, "frequency": 80}
            )

        service._recalculate_zones_for_client = _requeue

        await service._handle_registry_event(
            RegistryEventType.CLIENT_CONNECTED, {"mac_id": MAC}
        )

        assert MAC in service._retry_tasks
        task = service._retry_tasks[MAC]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class TestTheRegistryEventArms:
    """One `if/elif` chain over the internal bus. A raise here reaches the producer."""

    async def test_a_zone_creation_that_fails_does_not_escape(self, service, caplog):
        """`_emit_event` awaits its subscribers in order; an exception escaping
        one stops the ones behind it — here, `VolumeStateStore` and the WS
        translator, i.e. the whole `multiroom` category for that change."""
        service.apply_zone_crossover = AsyncMock(side_effect=RuntimeError("camilla down"))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.crossover"):
            await service._handle_registry_event(
                RegistryEventType.ZONE_CREATED, {"zone_id": "z1"}
            )

        assert any("Error applying crossover for new zone z1" in r.message
                   for r in caplog.records)

    async def test_a_zone_update_that_fails_does_not_escape(self, service, caplog):
        service.apply_zone_crossover = AsyncMock(side_effect=RuntimeError("camilla down"))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.crossover"):
            await service._handle_registry_event(
                RegistryEventType.ZONE_UPDATED, {"zone_id": "z1"}
            )

        assert any("Error recalculating crossover for zone z1" in r.message
                   for r in caplog.records)

    async def test_deleting_a_zone_disables_both_filters_on_every_member(self, service):
        """A deleted zone leaves its members standalone. Keeping the lowpass on a
        satellite that is no longer crossed over with a sub means that room loses
        everything above the crossover point."""
        service._set_client_filter = AsyncMock(return_value=True)

        await service._handle_registry_event(
            RegistryEventType.ZONE_DELETED,
            {"zone": {"id": "z1", "client_ids": [MAC, "aa:bb:cc:dd:ee:08"]}},
        )

        pushed = {(c.args[0], c.args[1], c.args[2]) for c in service._set_client_filter.await_args_list}
        assert pushed == {
            (MAC, "crossover", False), (MAC, "lowpass", False),
            ("aa:bb:cc:dd:ee:08", "crossover", False), ("aa:bb:cc:dd:ee:08", "lowpass", False),
        }

    async def test_a_deletion_that_fails_partway_does_not_escape(self, service, caplog):
        service._set_client_filter = AsyncMock(side_effect=RuntimeError("unreachable"))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.crossover"):
            await service._handle_registry_event(
                RegistryEventType.ZONE_DELETED, {"zone": {"id": "z1", "client_ids": [MAC]}}
            )

        assert any("Error disabling filters after zone z1 deletion" in r.message
                   for r in caplog.records)

    async def test_removing_one_client_clears_its_filters_and_recalculates_the_zone(
        self, service
    ):
        """Both halves are due: the client keeps its filters unless told, and the
        zone it left may have lost its only subwoofer."""
        service._set_client_filter = AsyncMock(return_value=True)
        service.apply_zone_crossover = AsyncMock()

        await service._handle_registry_event(
            "zone_client_removed", {"zone_id": "z1", "mac_id": MAC}
        )

        assert [c.args[1] for c in service._set_client_filter.await_args_list] == [
            "crossover", "lowpass"
        ]
        service.apply_zone_crossover.assert_awaited_once_with("z1")

    async def test_a_removal_that_fails_does_not_escape(self, service, caplog):
        service._set_client_filter = AsyncMock(side_effect=RuntimeError("unreachable"))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.crossover"):
            await service._handle_registry_event(
                "zone_client_removed", {"zone_id": "z1", "mac_id": MAC}
            )

        assert any(f"Error handling client {MAC} removal from zone z1" in r.message
                   for r in caplog.records)


class TestTheProxyRefusals:
    """Every way a filter can fail to reach a speaker, and what is left behind."""

    async def test_a_client_with_no_address_is_refused_out_loud(self, service, registry):
        """A registry entry with no IP builds `http://:8001/` — the push would
        fail on the wire with no name attached to the failure."""
        registry._clients[MAC] = _client(ip="")

        assert await service._set_client_filter(MAC, "crossover", True, 80) is False

    async def test_an_offline_client_is_queued_rather_than_pushed_to(
        self, service, registry
    ):
        """The queue IS the reconnection path. Pushing to a satellite that is off
        costs a TCP timeout per filter on the event that noticed it left."""
        registry._clients[MAC] = _client(online=False)

        assert await service._set_client_filter(MAC, "crossover", True, 120) is False

        assert service._pending_settings[MAC]["crossover"] == {
            "enabled": True, "frequency": 120
        }

    async def test_without_a_proxy_service_nothing_is_claimed_as_pushed(
        self, service, caplog
    ):
        """Returning True here would let the zone report a crossover that exists
        in no client's DSP."""
        service._proxy_service = None

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.crossover"):
            assert await service._proxy_filter_to_client("crossover", IP, True, 80) is False

        assert any("proxy service not available" in r.message for r in caplog.records)

    async def test_a_proxy_that_raises_is_a_refusal_not_a_crash(
        self, service, caplog
    ):
        """It is reached from a registry event and from the retry task; a raise
        would kill the retry that exists to recover from exactly this."""
        service._proxy_service.try_request = AsyncMock(side_effect=RuntimeError("no route"))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.crossover"):
            assert await service._proxy_filter_to_client(
                "crossover", IP, True, 80, client_id=MAC
            ) is False

        assert any(f"Error proxying crossover to client {MAC}" in r.message
                   for r in caplog.records)

    async def test_recalculating_without_a_registry_is_reported(self, service, caplog):
        """It is the entry point of three of the six event arms; silence here
        makes a whole class of missed recalculations invisible."""
        service._registry = None

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.crossover"):
            await service._recalculate_zones_for_client(MAC)

        assert any("Registry not available" in r.message for r in caplog.records)


class TestThePendingQueueReplay:
    """`apply_pending_settings`: what the reconnection actually replays."""

    async def test_a_client_with_nothing_queued_is_a_no_op(self, service):
        """It runs on every `CLIENT_CONNECTED`, which is every admission of every
        satellite — popping an absent key would raise on the common path."""
        assert await service.apply_pending_settings(MAC) is False

    async def test_an_empty_queue_entry_is_a_no_op_too(self, service):
        """`clear_pending_settings` deletes the key, but a replay that emptied the
        dict in place would leave one behind."""
        service._pending_settings[MAC] = {}

        assert await service.apply_pending_settings(MAC) is False

    async def test_one_filter_that_fails_does_not_stop_the_other(
        self, service, registry, caplog
    ):
        """Crossover and lowpass are two halves of one configuration: applying
        the highpass without the lowpass leaves a hole in the response."""
        registry._clients[MAC] = _client()
        await service.queue_pending_settings(MAC, "crossover", {"enabled": True, "frequency": 80})
        await service.queue_pending_settings(MAC, "lowpass", {"enabled": True, "frequency": 80})
        attempts = []

        async def _push(client_id, filter_name, enabled, frequency):
            attempts.append(filter_name)
            return filter_name != "crossover"

        service._set_client_filter = _push

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.crossover"):
            assert await service.apply_pending_settings(MAC) is False

        assert attempts == ["crossover", "lowpass"]
        assert any("Failed to apply pending crossover" in r.message for r in caplog.records)

    async def test_the_queue_is_emptied_even_when_a_replay_fails(self, service, registry):
        """It is popped before the pushes, so a client that cannot take its
        settings does not accumulate a queue that grows on every reconnection —
        the zone recalculation re-queues what still matters."""
        registry._clients[MAC] = _client()
        await service.queue_pending_settings(MAC, "crossover", {"enabled": True, "frequency": 80})
        service._set_client_filter = AsyncMock(return_value=False)

        await service.apply_pending_settings(MAC)

        assert not service.has_pending_settings(MAC)


class TestTheZoneCrossoverReads:
    """`get_zone_crossover` and the frequency pin, both of which answer the UI."""

    async def test_an_unknown_zone_answers_the_neutral_shape_without_a_fault(
        self, service, caplog
    ):
        """Two guards that look redundant with the decorator above them and are
        not: `@handle_errors` returns this same dict, but at ERROR level, and an
        ERROR is what raises the UI's fault banner. Asking about a zone that was
        just deleted is ordinary — a second browser tab does it — so it must
        answer the neutral shape and say nothing.

        The four keys are read unconditionally by the zone card, which is why the
        neutral answer is a full shape rather than None.
        """
        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.crossover"):
            got = await service.get_zone_crossover("nope")

        assert got == {
            "frequency": DEFAULT_CROSSOVER_FREQUENCY,
            "auto": True,
            "enabled": False,
            "has_subwoofer": False,
        }
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_without_a_registry_the_answer_is_neutral_and_still_silent(
        self, service, caplog
    ):
        """Same pair, the other guard: it is reached at boot, before
        `set_registry` has run, on any settings read that arrives first."""
        service._registry = None

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.crossover"):
            got = await service.get_zone_crossover("z1")

        assert got["frequency"] == DEFAULT_CROSSOVER_FREQUENCY
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_pinning_a_frequency_on_an_unknown_zone_is_refused(
        self, service, registry, caplog
    ):
        """The zone id comes from a URL path segment. Writing one that does not
        exist would create a zone record with nothing but a crossover in it."""
        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.crossover"):
            assert await service.set_zone_crossover_frequency("nope", 120) is False

        registry.update_zone.assert_not_called()
        assert any("Zone nope not found" in r.message for r in caplog.records)

    async def test_without_a_registry_the_pin_is_refused_quietly(self, service, caplog):
        """Same shape as the read above: `@handle_errors(default=False)` would
        answer False too, but at ERROR — and this is reached at boot, before
        `set_registry` runs, by any settings write that arrives first."""
        service._registry = None

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.crossover"):
            assert await service.set_zone_crossover_frequency("z1", 120) is False

        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_the_pin_is_clamped_into_the_range_the_ui_offers(self, service, registry):
        """It reaches here from a request body. A 5 Hz crossover sends everything
        to the subwoofer; a 20 kHz one sends nothing."""
        registry._zones["z1"] = Zone(id="z1", name="Salon", client_ids=[MAC])

        assert await service.set_zone_crossover_frequency("z1", 9999) is True

        assert registry.update_zone.await_args.kwargs["crossover_frequency"] == 200

    async def test_none_hands_the_frequency_back_to_the_speaker_types(
        self, service, registry
    ):
        """`None` is not "no change" — it is the request to stop pinning, which
        is the only way back to auto once a zone has been pinned."""
        registry._zones["z1"] = Zone(id="z1", name="Salon", client_ids=[MAC])

        assert await service.set_zone_crossover_frequency("z1", None) is True

        assert registry.update_zone.await_args.kwargs["crossover_frequency"] is None
