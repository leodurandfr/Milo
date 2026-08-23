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
