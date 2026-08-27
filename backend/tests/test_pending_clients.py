"""PendingClientsService — the wizard's staging area and the reboot shield.

What breaks when these fail:

* The identity the user typed in the wizard lives *only* here until the
  snapclient connects and `SnapcastWebSocketService._register_snapclient`
  transfers it into the registry. A satellite re-POSTs the same registration
  every 15 s, so an upsert that stopped preserving `name`/`speaker_type` would
  erase that choice on the next heartbeat, and the client would come back named
  after its Snapcast host — `register_client` in the registry preserves a
  non-empty name, so no later notification can repair it.
* `CONFIGURING_GRACE` shields the entry through a configure+reboot cycle, which
  routinely outlasts `STALE_TIMEOUT`. A wrongly expired entry is visible in the
  room: the backend broadcasts `removed`, the next heartbeat re-registers, and
  `App.vue` reads that as a brand-new speaker — it wakes the screen and opens
  Settings on its own.

Consumers: `api/multiroom.py` (register-client, pending-clients, configure) and
`core/multiroom/websocket.py` (`get_client` + `remove_client`, the transfer).

Why this file exists: measured 2026-08-23, the module ran at 24 % of its lines
under the whole suite — the `def` lines and `__init__`, nothing else. The only
tests naming this service replace it with a MagicMock.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from backend.core.multiroom import pending_clients as pending_clients_module
from backend.core.multiroom.pending_clients import (
    CONFIGURING_GRACE,
    STALE_TIMEOUT,
    PendingClientsService,
)


REBOOTING = "dc:a6:32:7e:d3:43"
SILENT = "d8:3a:dd:68:e7:e4"
FRESH = "b8:27:eb:11:22:33"


class _Clock:
    """The wall clock, as the service's collaborator rather than the runner's."""

    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(pending_clients_module, "time", c)
    return c


@pytest.fixture
def storage(monkeypatch, tmp_path):
    """`PENDING_CLIENTS_FILE` is a module global and `_persist` writes it for
    real — on this checkout that is the appliance's own file."""
    path = tmp_path / "pending_clients.json"
    monkeypatch.setattr(pending_clients_module, "PENDING_CLIENTS_FILE", path)
    return path


def _service() -> PendingClientsService:
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    return PendingClientsService(state_machine=state_machine)


async def _reload_after_sweep() -> dict:
    """Boot a second service on the same file.

    `initialize()` loads the persisted entries and runs the stale sweep before
    returning — the only way in that is not a private method.
    """
    service = _service()
    await service.initialize()
    try:
        return service.get_all_clients()
    finally:
        await service.shutdown()


async def test_a_heartbeat_keeps_the_name_the_user_chose(clock, storage):
    service = _service()
    await service.register_client(
        mac_id=REBOOTING, ip="192.168.1.60", hardware_configured=False, audio_id="none"
    )
    await service.update_client(REBOOTING, name="Bureau", speaker_type="floorstanding")

    clock.now += 15  # the satellite's next heartbeat, same POST body but configured
    await service.register_client(
        mac_id=REBOOTING, ip="192.168.1.61", hardware_configured=True,
        audio_id="hifiberry-dacplus", volume_control=False,
    )

    client = service.get_client(REBOOTING)
    # The heartbeat landed: connection facts are the ones it just carried...
    assert client["ip"] == "192.168.1.61"
    assert client["audio_id"] == "hifiberry-dacplus"
    assert client["volume_control"] is False
    # ...and it did not take the identity down with it.
    assert client["name"] == "Bureau"
    assert client["speaker_type"] == "floorstanding"


async def test_the_sweep_expires_the_silent_client_but_spares_the_one_rebooting(clock, storage):
    service = _service()
    for mac in (SILENT, REBOOTING):
        await service.register_client(
            mac_id=mac, ip="192.168.1.60", hardware_configured=False, audio_id="none"
        )
    await service.update_client(REBOOTING, name="Canapé")
    assert await service.mark_configuring(REBOOTING) is True

    clock.now += STALE_TIMEOUT + 1

    clients = await _reload_after_sweep()

    assert REBOOTING in clients
    assert clients[REBOOTING]["name"] == "Canapé"
    assert SILENT not in clients


async def test_the_reboot_shield_expires_with_its_own_grace(clock, storage):
    service = _service()
    await service.register_client(
        mac_id=REBOOTING, ip="192.168.1.60", hardware_configured=False, audio_id="none"
    )
    await service.mark_configuring(REBOOTING)

    # Longer than any reboot: the client is not coming back, and the shield is
    # not a permanent exemption from the sweep.
    clock.now += CONFIGURING_GRACE + 1
    await service.register_client(
        mac_id=FRESH, ip="192.168.1.61", hardware_configured=False, audio_id="none"
    )

    clients = await _reload_after_sweep()

    assert REBOOTING not in clients
    assert FRESH in clients


async def test_reporting_the_card_ends_the_reboot_shield(clock, storage):
    service = _service()
    await service.register_client(
        mac_id=REBOOTING, ip="192.168.1.60", hardware_configured=False, audio_id="none"
    )
    await service.mark_configuring(REBOOTING)

    clock.now += 60  # it rebooted and came back with the card the user picked
    await service.register_client(
        mac_id=REBOOTING, ip="192.168.1.60", hardware_configured=True,
        audio_id="hifiberry-dacplus",
    )

    # From here it ages like any other entry, well inside what is left of the grace.
    clock.now += STALE_TIMEOUT + 1
    await service.register_client(
        mac_id=FRESH, ip="192.168.1.61", hardware_configured=False, audio_id="none"
    )

    clients = await _reload_after_sweep()

    assert REBOOTING not in clients
    assert FRESH in clients


class TestTheTransferAndTheRefusals:
    """What clears the wizard entry, and what happens when nothing is there.

    `remove_client` ran at 0 % until 2026-08-27: it is the last step of an
    adoption — `_register_snapclient` calls it once the registry owns the
    identity. An entry that survives the transfer keeps the speaker in the
    wizard's "waiting to be configured" list while it is already playing, and
    every later heartbeat re-broadcasts it.
    """

    async def test_a_successful_adoption_clears_the_wizard_entry(self, clock, storage):
        service = _service()
        await service.register_client(
            mac_id=REBOOTING, ip="192.168.1.60", hardware_configured=False, audio_id="none"
        )
        try:
            assert await service.remove_client(REBOOTING) is True

            assert service.get_client(REBOOTING) is None
            assert await _reload_after_sweep() == {}, "the removal did not reach the disk"
        finally:
            await service.shutdown()

    async def test_the_removal_is_announced_so_the_wizard_list_updates(
        self, clock, storage
    ):
        """The multiroom page holds the pending list from a WS delta; without the
        event the adopted speaker stays in it until the tab is reloaded."""
        service = _service()
        await service.register_client(
            mac_id=REBOOTING, ip="192.168.1.60", hardware_configured=False, audio_id="none"
        )
        service._state_machine.broadcast.reset_mock()
        try:
            await service.remove_client(REBOOTING)

            events = [c.args[0] for c in service._state_machine.broadcast.await_args_list]
            assert [(e.action, e.mac_id) for e in events] == [("removed", REBOOTING)]
        finally:
            await service.shutdown()

    async def test_removing_an_unknown_client_changes_nothing(self, clock, storage):
        """`_register_snapclient` calls it for every admission, pending or not."""
        service = _service()
        try:
            assert await service.remove_client(FRESH) is False
            service._state_machine.broadcast.assert_not_called()
        finally:
            await service.shutdown()

    async def test_updating_an_unknown_client_answers_none(self, clock, storage):
        """The mac comes from a URL path segment; creating an entry from a
        `PATCH` would put a speaker in the wizard that never registered."""
        service = _service()
        try:
            assert await service.update_client(FRESH, name="Salon") is None
        finally:
            await service.shutdown()

    async def test_an_update_touches_only_the_fields_it_was_given(self, clock, storage):
        """The wizard PATCHes one field at a time as the user fills the form."""
        service = _service()
        await service.register_client(
            mac_id=REBOOTING, ip="192.168.1.60", hardware_configured=False, audio_id="none"
        )
        try:
            await service.update_client(REBOOTING, name="Salon", speaker_type="subwoofer")
            updated = await service.update_client(
                REBOOTING, audio_id="hifiberry-dac", volume_control=False
            )

            assert updated["name"] == "Salon"
            assert updated["speaker_type"] == "subwoofer"
            assert updated["audio_id"] == "hifiberry-dac"
            assert updated["volume_control"] is False
        finally:
            await service.shutdown()

    async def test_marking_an_unknown_client_as_configuring_is_refused(
        self, clock, storage
    ):
        """The grace period it grants is what shields an entry through a reboot;
        granting it to an absent mac would write a record with nothing else in it.
        """
        service = _service()
        try:
            assert await service.mark_configuring(FRESH) is False
        finally:
            await service.shutdown()

    async def test_a_sweep_with_nothing_stale_writes_nothing(self, clock, storage):
        """It runs every cleanup interval for the life of the process. Persisting
        each time is a write to the SD card per pass, forever."""
        service = _service()
        await service.register_client(
            mac_id=FRESH, ip="192.168.1.70", hardware_configured=False, audio_id="none"
        )
        try:
            storage.unlink()

            await service._purge_stale()

            assert not storage.exists(), "an idle sweep rewrote the file"
        finally:
            await service.shutdown()

    async def test_a_corrupt_store_starts_empty_rather_than_stopping_the_boot(
        self, clock, storage, caplog
    ):
        """This file carries no `schema_version` — it is a staging area, not
        persisted state — so the fail-loud protocol does not apply and a
        half-written file must not keep the backend from starting. The wizard
        entries are rebuilt by the next heartbeat, 15 s later.
        """
        import logging

        storage.write_text("{not json")
        service = _service()

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.pending_clients"):
            assert await service.initialize() is True
        try:
            assert service.get_all_clients() == {}
            assert any("Failed to load pending clients" in r.message for r in caplog.records)
        finally:
            await service.shutdown()

    async def test_an_empty_file_is_not_a_corrupt_one(self, clock, storage, caplog):
        """`_persist` writes `{}` when the last entry is adopted, and an empty
        read must not log a fault on every boot after that."""
        import logging

        storage.write_text("")
        service = _service()

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.pending_clients"):
            await service.initialize()
        try:
            assert service.get_all_clients() == {}
            assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        finally:
            await service.shutdown()

    async def test_a_disk_that_refuses_the_write_does_not_lose_the_entry(
        self, clock, storage, monkeypatch, caplog
    ):
        """The in-memory copy is what the wizard reads and what the transfer
        consumes; a persistence failure must cost the reboot shield, not the
        registration that just arrived."""
        import logging

        monkeypatch.setattr(
            pending_clients_module.os, "replace",
            MagicMock(side_effect=OSError("read-only filesystem")),
        )
        service = _service()

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.pending_clients"):
            await service.register_client(
                mac_id=FRESH, ip="192.168.1.70", hardware_configured=False, audio_id="none"
            )
        try:
            assert service.get_client(FRESH) is not None
            assert any("Failed to persist pending clients" in r.message for r in caplog.records)
        finally:
            await service.shutdown()

    async def test_a_broadcast_that_fails_does_not_undo_the_registration(
        self, clock, storage, caplog
    ):
        """It is awaited after the lock is released and after the write landed;
        raising would surface a failed `POST /api/multiroom/register-client` to a
        satellite that is registered — and the satellite retries the POST, which
        would re-broadcast forever."""
        import logging

        service = _service()
        service._state_machine.broadcast = AsyncMock(side_effect=RuntimeError("no ws"))

        with caplog.at_level(logging.ERROR, logger="backend.core.multiroom.pending_clients"):
            await service.register_client(
                mac_id=FRESH, ip="192.168.1.70", hardware_configured=False, audio_id="none"
            )
        try:
            assert service.get_client(FRESH) is not None
            assert any("Failed to broadcast pending client event" in r.message
                       for r in caplog.records)
        finally:
            await service.shutdown()
