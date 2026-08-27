"""The registry's refusals, its zone bookkeeping on departure, and the bus arm
that keeps one bad subscriber from silencing the rest.

What breaks when these fail: `ClientRegistryService` is the single source of
truth for who is in the fleet, and `_emit_event` is the only producer on the
one internal event bus Milō has. Three subscribers hang off it — `CrossoverService`,
`VolumeStateStore`, and the WebSocket translator that turns each event into a
`multiroom` broadcast — and they are notified **in subscription order**, which
`dependencies.py` sets deliberately. An exception escaping one subscriber
therefore silences every subscriber behind it for that change: no crossover
recalculation, no volume-store update, no UI event, and nothing anywhere saying
so.

The rest are the refusals. Every mutating method here takes a mac_id or a zone
id that arrived from a URL path segment or a snapserver frame, so "not found"
is the ordinary case, not the exceptional one — and answering as though the
write happened is what puts a speaker in the UI that no registry entry backs.
"""
import logging
from unittest.mock import AsyncMock

import pytest

from backend.core.multiroom.client_registry import ClientRegistryService
from backend.core.multiroom.models import (
    DEFAULT_SPEAKER_TYPE,
    EqFilter,
    EqualizerSettings,
    RegistryEventType,
)

MAC = "aa:bb:cc:dd:ee:07"
OTHER = "aa:bb:cc:dd:ee:08"
THIRD = "aa:bb:cc:dd:ee:09"
IP = "192.168.1.153"


@pytest.fixture
def settings():
    svc = AsyncMock()
    svc.get_setting = AsyncMock(return_value=None)
    svc.set_settings = AsyncMock()
    return svc


@pytest.fixture
async def registry(settings):
    reg = ClientRegistryService(settings_service=settings)
    await reg.initialize()
    return reg


def _eq(gain=3.0):
    return EqualizerSettings(filters=[EqFilter(id="eq_band_00", frequency=1000, gain=gain)])


class TestTheEventBus:
    """`_emit_event` — the only producer on the one internal bus."""

    async def test_one_subscriber_that_raises_does_not_silence_the_others(
        self, registry, caplog
    ):
        """They are awaited in subscription order, and `dependencies.py` sets
        that order on purpose. Without this arm, a crossover recalculation that
        threw would also cost the volume store its update and the UI its event —
        for the same change, with no trace but the traceback nobody sees."""
        seen = []
        registry.subscribe(AsyncMock(side_effect=RuntimeError("crossover is down")))
        registry.subscribe(
            AsyncMock(side_effect=lambda t, d: seen.append(("store", t)))
        )
        registry.subscribe(AsyncMock(side_effect=lambda t, d: seen.append(("ws", t))))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.client_registry"):
            await registry.register_client(MAC, "Canapé", IP)

        assert [s for s, _ in seen] == ["store", "ws"]
        assert any("Error in registry subscriber" in r.message for r in caplog.records)


class TestTheRefusals:
    """A mac or a zone id that names nothing. The ordinary case, not the rare one."""

    async def test_updating_an_unknown_client_answers_none(self, registry, caplog):
        """The mac comes from `PATCH /api/multiroom/clients/{mac}`; creating one
        here would put a speaker in the room list that no snapclient backs."""
        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.client_registry"):
            assert await registry.update_client(MAC, name="Salon") is None

        assert any("Cannot update" in r.message for r in caplog.records)

    async def test_bringing_an_unknown_client_online_is_refused(self, registry, caplog):
        """This is the last step of every admission. A silent create here would
        announce a client the admission sync never actually configured."""
        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.client_registry"):
            await registry.set_client_online(MAC, True)

        assert registry.get_client(MAC) is None
        assert any("Cannot set online" in r.message for r in caplog.records)

    async def test_setting_a_delay_on_an_unknown_client_answers_none(
        self, registry, caplog
    ):
        """The delay is native Snapcast latency Milō owns; the caller pushes the
        answer to snapserver, so a fabricated one would set a latency against an
        id snapserver does not know."""
        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.client_registry"):
            assert await registry.set_client_delay(MAC, 40) is None

        assert any("Cannot set delay" in r.message for r in caplog.records)

    async def test_an_unknown_client_is_not_local_and_is_the_default_speaker(
        self, registry
    ):
        """Both are read on paths that run before admission completes — the EQ
        proxy's local-vs-remote fork and the crossover's speaker-type read. The
        safe answers are "remote" and "bookshelf": neither pins the local
        client's equalizer.json to a satellite, nor a lowpass to a full-range
        speaker."""
        assert registry.is_local_client(MAC) is False
        assert registry.get_client_speaker_type(MAC) == DEFAULT_SPEAKER_TYPE

    async def test_only_the_clients_that_are_up_are_listed_online(self, registry):
        """It is what the EQ fan-out iterates; including offline clients would
        make every zone write wait on a TCP timeout per absent satellite."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.register_client(OTHER, "Bureau", "192.168.1.60")
        await registry.set_client_online(MAC, True)

        assert registry.get_online_client_ids() == [MAC]

    async def test_updating_an_unknown_zone_answers_none(self, registry):
        """The zone id is a URL path segment."""
        assert await registry.update_zone("nope", name="Salon") is None

    async def test_the_clients_of_an_unknown_zone_are_an_empty_list(self, registry):
        """Both readers feed loops. A None here is a crash in the zone fan-out,
        not an empty pass."""
        assert registry.get_zone_clients("nope") == []
        assert registry.get_online_zone_clients("nope") == []

    async def test_adding_a_client_to_an_unknown_zone_is_refused(self, registry, caplog):
        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.client_registry"):
            assert await registry.add_client_to_zone("nope", MAC) is False

        assert any("zone nope not found" in r.message for r in caplog.records)

    async def test_adding_an_unknown_client_to_a_zone_is_refused(self, registry, caplog):
        await registry.register_client(MAC, "Canapé", IP)
        await registry.register_client(OTHER, "Bureau", "192.168.1.60")
        zone = await registry.create_zone("z1", "Salon", [MAC, OTHER])

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.client_registry"):
            assert await registry.add_client_to_zone(zone.id, THIRD) is False

        assert any(f"Cannot add client: {THIRD} not found" in r.message
                   for r in caplog.records)

    async def test_adding_a_client_already_in_the_zone_changes_nothing(self, registry):
        """The multiroom page lets a member be dragged onto its own zone. Taking
        the write would run the leave-and-rejoin path against the zone it is in —
        which can drop it below two members and delete the zone."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.register_client(OTHER, "Bureau", "192.168.1.60")
        zone = await registry.create_zone("z1", "Salon", [MAC, OTHER])

        assert await registry.add_client_to_zone(zone.id, MAC) is False

        assert set(registry.get_zone(zone.id).client_ids) == {MAC, OTHER}

    async def test_removing_from_an_unknown_zone_is_refused(self, registry):
        assert await registry.remove_client_from_zone("nope", MAC) is False

    async def test_removing_a_client_that_is_not_in_the_zone_is_refused(self, registry):
        """Otherwise the removal path runs its zone bookkeeping — including the
        delete-below-two rule — for a client that was never a member."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.register_client(OTHER, "Bureau", "192.168.1.60")
        zone = await registry.create_zone("z1", "Salon", [MAC, OTHER])

        assert await registry.remove_client_from_zone(zone.id, THIRD) is False

        assert set(registry.get_zone(zone.id).client_ids) == {MAC, OTHER}


class TestTheRegistrationUpsert:
    """A satellite re-registers on every reconnection; what survives that."""

    async def test_a_name_the_user_chose_survives_a_re_registration(self, registry):
        """The admission paths pass the Snapcast host name as a fallback. Letting
        it overwrite would rename every speaker to `milo-client-<something>` on
        each reconnection, and nothing repairs it afterwards."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.register_client(MAC, "milo-client-2", "192.168.1.200")

        assert registry.get_client(MAC).name == "Canapé"
        assert registry.get_client(MAC).ip == "192.168.1.200"

    async def test_an_empty_name_is_filled_in_rather_than_left_blank(self, registry):
        """A client admitted from a frame carrying no name has none; the next
        registration that has one must supply it, or the speaker stays unnamed
        in the UI forever."""
        await registry.register_client(MAC, "", IP)

        await registry.register_client(MAC, "Canapé", IP)

        assert registry.get_client(MAC).name == "Canapé"

    async def test_volume_control_is_only_written_when_it_is_stated(self, registry):
        """`None` means "preserve" — the admission paths pass it for a remote
        client with no pending entry. Coercing it would flip a DAC client back to
        managed on its next reconnection, and CamillaDSP would attenuate a signal
        the external amp re-amplifies."""
        await registry.register_client(MAC, "Canapé", IP)
        assert registry.get_client(MAC).volume_control is True, "a new client is managed"

        # The re-registration every reconnection sends, carrying no opinion.
        await registry.register_client(MAC, "Canapé", IP)
        assert registry.get_client(MAC).volume_control is True

        await registry.register_client(MAC, "Canapé", IP, volume_control=False)
        assert registry.get_client(MAC).volume_control is False

        await registry.register_client(MAC, "Canapé", IP)
        assert registry.get_client(MAC).volume_control is False


class TestTheDeparture:
    """`unregister_client`: the zone bookkeeping and the EQ record it takes with it."""

    async def test_a_departure_that_leaves_two_members_only_updates_the_zone(
        self, registry
    ):
        """The zone survives; the event is what redraws its member list."""
        for mac in (MAC, OTHER, THIRD):
            await registry.register_client(mac, mac, IP)
        zone = await registry.create_zone("z1", "Salon", [MAC, OTHER, THIRD])
        seen = []
        registry.subscribe(AsyncMock(side_effect=lambda t, d: seen.append((t, d))))

        await registry.unregister_client(THIRD)

        assert registry.get_zone(zone.id) is not None
        assert set(registry.get_zone(zone.id).client_ids) == {MAC, OTHER}
        assert [t for t, _ in seen][0] == RegistryEventType.ZONE_UPDATED

    async def test_a_departure_that_drops_the_zone_below_two_deletes_it(self, registry):
        """A zone of one is a standalone client with extra bookkeeping, and its
        crossover would keep applying a lowpass with no subwoofer to cross to."""
        for mac in (MAC, OTHER):
            await registry.register_client(mac, mac, IP)
        zone = await registry.create_zone("z1", "Salon", [MAC, OTHER])

        await registry.unregister_client(OTHER)

        assert registry.get_zone(zone.id) is None
        assert registry.get_client(MAC).zone_id is None

    async def test_a_departing_client_takes_its_equalizer_record_with_it(self, registry):
        """The record is keyed by mac. Leaving it behind means a different
        speaker plugged in at the same address inherits the previous one's EQ —
        and `_persist_state` writes it back on the next mutation, so it survives
        a reboot too."""
        await registry.register_client(MAC, "Canapé", IP)
        await registry.set_client_equalizer(MAC, _eq())
        assert registry.get_client_equalizer(MAC) is not None

        await registry.unregister_client(MAC)

        assert registry.get_client_equalizer(MAC) is None


class TestTheEqualizerStore:
    """`set_clients_equalizer`: the one write the zone fan-out goes through."""

    async def test_an_empty_mapping_writes_nothing(self, registry, settings):
        """It is called on every zone EQ change, including the ones where the
        zone turns out to hold no online member.

        The early `if not records` guard is inert as far as the *result* goes —
        the `if not stored` check below answers identically once the loop runs
        zero times — so what it buys is skipping the lock on a path the EQ
        fan-out takes per member. Measured 2026-08-27, recorded rather than
        removed.
        """
        settings.set_settings.reset_mock()

        await registry.set_clients_equalizer({})

        settings.set_settings.assert_not_called()

    async def test_a_mapping_of_only_unknown_clients_writes_nothing(
        self, registry, settings, caplog
    ):
        """Storing a record against a mac with no client would make it
        unreachable — nothing iterates the EQ store on its own — while the write
        it triggers rewrites `settings.json` for nothing."""
        settings.set_settings.reset_mock()

        with caplog.at_level(logging.WARNING, logger="backend.core.multiroom.client_registry"):
            await registry.set_clients_equalizer({MAC: _eq()})

        assert registry.get_client_equalizer(MAC) is None
        settings.set_settings.assert_not_called()
        assert any("client aa:bb:cc:dd:ee:07 not found" in r.message for r in caplog.records)

    async def test_the_known_clients_of_a_mixed_mapping_are_still_stored(
        self, registry
    ):
        """A zone whose member list is mid-change reaches here with one stale
        mac; dropping the whole write would lose the change for the members that
        are present."""
        await registry.register_client(MAC, "Canapé", IP)

        await registry.set_clients_equalizer({MAC: _eq(6.0), OTHER: _eq(3.0)})

        assert registry.get_client_equalizer(MAC).filters[0].gain == 6.0
        assert registry.get_client_equalizer(OTHER) is None

    async def test_the_registry_owns_its_copy_of_a_stored_record(self, registry):
        """The caller keeps a reference to the record it passed — the EQ router
        reuses one object across a zone's members. A stored reference would let a
        later edit reach the registry without a write, and `_persist_state`
        would then save a record nothing asked to change."""
        await registry.register_client(MAC, "Canapé", IP)
        record = _eq(3.0)

        await registry.set_clients_equalizer({MAC: record})
        record.filters[0].gain = 99.0

        assert registry.get_client_equalizer(MAC).filters[0].gain == 3.0


class TestThePersistenceArms:
    """The write, and the two ways it can be asked to do nothing."""

    async def test_without_a_settings_service_the_write_is_skipped_quietly(
        self, registry, caplog
    ):
        """The registry is constructible without one — `test_crossover_service`
        and the API tests do it — and every mutation calls this. Falling through
        instead would reach the `except` below and log an ERROR per mutation,
        which is what raises the UI's fault banner."""
        registry._settings_service = None

        with caplog.at_level(logging.DEBUG, logger="backend.core.multiroom.client_registry"):
            await registry.register_client(MAC, "Canapé", IP)

        assert registry.get_client(MAC) is not None
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    async def test_a_settings_write_that_fails_does_not_undo_the_change(
        self, registry, settings, caplog
    ):
        """The in-memory registry is what the UI and every push read; a failed
        write must cost the reboot, not the change that just happened. The error
        line is the only thing that says the two have diverged."""
        settings.set_settings = AsyncMock(side_effect=OSError("read-only filesystem"))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.client_registry"):
            await registry.register_client(MAC, "Canapé", IP)

        assert registry.get_client(MAC) is not None
        assert any("Failed to persist multiroom state" in r.message for r in caplog.records)

    async def test_a_boot_without_a_settings_service_loads_nothing_and_succeeds(
        self, settings
    ):
        """A dev host with no settings file still has to start: the appliance's
        own principle is to fail open when the layer below is unavailable."""
        reg = ClientRegistryService(settings_service=None)

        assert await reg.initialize() is True
        assert reg.get_all_clients() == {}

    async def test_a_settings_read_that_fails_still_boots_with_an_empty_fleet(
        self, settings, caplog
    ):
        """Measured 2026-08-27: `initialize`'s own `except` arm is unreachable.

        `_load_persisted_state` catches every exception in its own body, and it
        is the only thing `initialize` calls that can fail — so a load failure
        logs *there* and this method still answers True. That is the fail-open
        the appliance's principles ask for, but it means what B1-4 recorded is
        the whole story of a bad boot: the three sections share one `try`, a
        section that failed to load leaves the registry empty for it, and
        `_persist_state` then serialises the whole in-memory state — so the
        first mutation that follows *overwrites* what could not be read.

        Inertia recorded, not removed: the arm costs nothing and the day
        `_load_persisted_state` stops swallowing, it is what keeps a corrupted
        boot from reading as a successful one.
        """
        settings.get_setting = AsyncMock(side_effect=RuntimeError("settings.json is gone"))
        reg = ClientRegistryService(settings_service=settings)

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.client_registry"):
            assert await reg.initialize() is True

        assert reg.get_all_clients() == {}
        assert any("Failed to load persisted state" in r.message for r in caplog.records)
