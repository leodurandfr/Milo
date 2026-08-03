"""
Unit tests for ConnectivityService's startup connectivity read.

The level (NONE / PORTAL / LIMITED / FULL) is published whole, never flattened:
the state machine crosses it with the active source's NETWORK_REQUIREMENT, and
a boolean cannot say "LAN reachable, no internet" — which is the difference
between a broken Spotify and a working AirPlay.

initialize() must stay fast (cached property, no blocking network probe)
since it runs inside the backend's startup gather — but the cached value can
still be UNKNOWN right after boot, before NM's own periodic/interface-triggered
check has run, which produced a false "offline" banner on every reboot. A
background re-check forces one fresh NM probe and corrects + broadcasts if it
disagrees, without holding up startup.
"""
import asyncio

from unittest.mock import AsyncMock, MagicMock, patch

from backend.core.connectivity.service import ConnectivityService
from backend.core.models.audio_state import ConnectivityLevel

NM_FULL = 4
NM_NONE = 1
NM_LIMITED = 3


def make_nm_iface(check_connectivity=None, get_connectivity=None) -> MagicMock:
    iface = MagicMock()
    iface.call_check_connectivity = check_connectivity or AsyncMock(return_value=NM_FULL)
    iface.get_connectivity = get_connectivity or AsyncMock(return_value=NM_FULL)
    return iface


def make_service() -> ConnectivityService:
    service = ConnectivityService()
    service._state_machine = MagicMock()
    service._state_machine.broadcast = AsyncMock()
    return service


def _patch_dbus(nm_iface: MagicMock):
    """Patch MessageBus/introspect/proxy plumbing so initialize() reaches
    the connectivity read with our fake NM + properties interfaces."""
    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    bus.introspect = AsyncMock(return_value=MagicMock())

    proxy = MagicMock()
    properties_iface = MagicMock()
    properties_iface.on_properties_changed = MagicMock()

    def get_interface(name):
        return nm_iface if name == "org.freedesktop.NetworkManager" else properties_iface

    proxy.get_interface = MagicMock(side_effect=get_interface)
    bus.get_proxy_object = MagicMock(return_value=proxy)

    return patch("backend.core.connectivity.service.MessageBus", return_value=bus), properties_iface


async def test_initialize_reads_cached_property_without_forcing_a_probe():
    """initialize() must not block the startup gather on a network probe."""
    nm_iface = make_nm_iface(get_connectivity=AsyncMock(return_value=NM_FULL))
    message_bus_patch, properties_iface = _patch_dbus(nm_iface)

    service = make_service()
    with patch.object(service._bg, "spawn") as spawn_mock:
        spawn_mock.side_effect = lambda coro, label: coro.close()  # avoid "never awaited"
        with message_bus_patch:
            ok = await service.initialize()

    assert ok is True
    assert service.level is ConnectivityLevel.FULL
    nm_iface.get_connectivity.assert_awaited_once()
    nm_iface.call_check_connectivity.assert_not_awaited()
    properties_iface.on_properties_changed.assert_called_once()
    spawn_mock.assert_called_once()


async def test_initialize_keeps_the_level_nm_reports():
    """The level is what the source's NETWORK_REQUIREMENT is crossed with, so
    flattening it back to a boolean here would erase the LAN-up/no-internet
    distinction the whole feature turns on."""
    nm_iface = make_nm_iface(get_connectivity=AsyncMock(return_value=NM_NONE))
    message_bus_patch, _properties_iface = _patch_dbus(nm_iface)

    service = make_service()
    with patch.object(service._bg, "spawn"):
        with message_bus_patch:
            ok = await service.initialize()

    assert ok is True
    assert service.level is ConnectivityLevel.NONE


async def test_initialize_fails_open_when_dbus_unavailable():
    with patch(
        "backend.core.connectivity.service.MessageBus",
        side_effect=RuntimeError("no system bus"),
    ):
        service = make_service()
        ok = await service.initialize()

    assert ok is False
    assert service.level is ConnectivityLevel.UNKNOWN


async def test_recheck_fresh_corrects_stale_offline_and_broadcasts():
    """Cached read said offline (e.g. still UNKNOWN at boot); the forced
    re-check finds FULL and must flip state + broadcast the correction."""
    service = make_service()
    service._level = ConnectivityLevel.NONE
    nm_iface = make_nm_iface(check_connectivity=AsyncMock(return_value=NM_FULL))

    await service._recheck_fresh(nm_iface)

    assert service.level is ConnectivityLevel.FULL
    service._state_machine.broadcast.assert_awaited_once()
    event = service._state_machine.broadcast.call_args.args[0]
    assert event.connectivity == "full"


async def test_recheck_fresh_no_broadcast_when_unchanged():
    service = make_service()
    service._level = ConnectivityLevel.FULL
    nm_iface = make_nm_iface(check_connectivity=AsyncMock(return_value=NM_FULL))

    await service._recheck_fresh(nm_iface)

    assert service.level is ConnectivityLevel.FULL
    service._state_machine.broadcast.assert_not_awaited()


async def test_recheck_fresh_leaves_state_unchanged_on_probe_failure():
    service = make_service()
    service._level = ConnectivityLevel.NONE
    nm_iface = make_nm_iface(check_connectivity=AsyncMock(side_effect=RuntimeError("denied")))

    await service._recheck_fresh(nm_iface)

    assert service.level is ConnectivityLevel.NONE
    service._state_machine.broadcast.assert_not_awaited()


async def test_recheck_fresh_bounded_by_timeout():
    async def hang(*_args, **_kwargs):
        await asyncio.sleep(10)
        return NM_FULL

    service = make_service()
    service._level = ConnectivityLevel.NONE
    nm_iface = make_nm_iface(check_connectivity=AsyncMock(side_effect=hang))

    with patch("backend.core.connectivity.service.NM_CHECK_CONNECTIVITY_TIMEOUT", 0.01):
        await service._recheck_fresh(nm_iface)

    assert service.level is ConnectivityLevel.NONE
    service._state_machine.broadcast.assert_not_awaited()


async def test_limited_is_kept_distinct_from_none():
    """LIMITED must not collapse into NONE: AirPlay/DLNA/Mac keep working on a
    router with no route out, and only the level says so."""
    nm_iface = make_nm_iface(get_connectivity=AsyncMock(return_value=NM_LIMITED))
    message_bus_patch, _properties_iface = _patch_dbus(nm_iface)

    service = make_service()
    with patch.object(service._bg, "spawn"):
        with message_bus_patch:
            await service.initialize()

    assert service.level is ConnectivityLevel.LIMITED
    assert service.get_state() == {"connectivity": "limited"}


async def test_unknown_nm_value_fails_open():
    """A level NM gained after this was written must read as UNKNOWN, which
    every consumer treats as FULL — never report a problem we cannot name."""
    nm_iface = make_nm_iface(get_connectivity=AsyncMock(return_value=99))
    message_bus_patch, _properties_iface = _patch_dbus(nm_iface)

    service = make_service()
    with patch.object(service._bg, "spawn"):
        with message_bus_patch:
            await service.initialize()

    assert service.level is ConnectivityLevel.UNKNOWN
