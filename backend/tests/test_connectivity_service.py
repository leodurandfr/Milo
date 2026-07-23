"""
Unit tests for ConnectivityService's startup connectivity read.

initialize() must stay fast (cached property, no blocking network probe)
since it runs inside the backend's startup gather — but the cached value can
still be UNKNOWN right after boot, before NM's own periodic/interface-triggered
check has run, which produced a false "offline" banner on every reboot. A
background re-check forces one fresh NM probe and corrects + broadcasts if it
disagrees, without holding up startup.
"""
import asyncio

from unittest.mock import AsyncMock, MagicMock, patch

from backend.core.connectivity.service import (
    NM_CONNECTIVITY_FULL,
    ConnectivityService,
)


def make_nm_iface(check_connectivity=None, get_connectivity=None) -> MagicMock:
    iface = MagicMock()
    iface.call_check_connectivity = check_connectivity or AsyncMock(return_value=NM_CONNECTIVITY_FULL)
    iface.get_connectivity = get_connectivity or AsyncMock(return_value=NM_CONNECTIVITY_FULL)
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
    nm_iface = make_nm_iface(get_connectivity=AsyncMock(return_value=NM_CONNECTIVITY_FULL))
    message_bus_patch, properties_iface = _patch_dbus(nm_iface)

    service = make_service()
    with patch.object(service._bg, "spawn") as spawn_mock:
        spawn_mock.side_effect = lambda coro, label: coro.close()  # avoid "never awaited"
        with message_bus_patch:
            ok = await service.initialize()

    assert ok is True
    assert service.online is True
    nm_iface.get_connectivity.assert_awaited_once()
    nm_iface.call_check_connectivity.assert_not_awaited()
    properties_iface.on_properties_changed.assert_called_once()
    spawn_mock.assert_called_once()


async def test_initialize_offline_when_cached_property_is_not_full():
    nm_iface = make_nm_iface(get_connectivity=AsyncMock(return_value=1))  # NONE
    message_bus_patch, _properties_iface = _patch_dbus(nm_iface)

    service = make_service()
    with patch.object(service._bg, "spawn"):
        with message_bus_patch:
            ok = await service.initialize()

    assert ok is True
    assert service.online is False


async def test_initialize_fails_open_when_dbus_unavailable():
    with patch(
        "backend.core.connectivity.service.MessageBus",
        side_effect=RuntimeError("no system bus"),
    ):
        service = make_service()
        ok = await service.initialize()

    assert ok is False
    assert service.online is True


async def test_recheck_fresh_corrects_stale_offline_and_broadcasts():
    """Cached read said offline (e.g. still UNKNOWN at boot); the forced
    re-check finds FULL and must flip state + broadcast the correction."""
    service = make_service()
    service._online = False
    nm_iface = make_nm_iface(check_connectivity=AsyncMock(return_value=NM_CONNECTIVITY_FULL))

    await service._recheck_fresh(nm_iface)

    assert service.online is True
    service._state_machine.broadcast.assert_awaited_once()
    event = service._state_machine.broadcast.call_args.args[0]
    assert event.online is True


async def test_recheck_fresh_no_broadcast_when_unchanged():
    service = make_service()
    service._online = True
    nm_iface = make_nm_iface(check_connectivity=AsyncMock(return_value=NM_CONNECTIVITY_FULL))

    await service._recheck_fresh(nm_iface)

    assert service.online is True
    service._state_machine.broadcast.assert_not_awaited()


async def test_recheck_fresh_leaves_state_unchanged_on_probe_failure():
    service = make_service()
    service._online = False
    nm_iface = make_nm_iface(check_connectivity=AsyncMock(side_effect=RuntimeError("denied")))

    await service._recheck_fresh(nm_iface)

    assert service.online is False
    service._state_machine.broadcast.assert_not_awaited()


async def test_recheck_fresh_bounded_by_timeout():
    async def hang(*_args, **_kwargs):
        await asyncio.sleep(10)
        return NM_CONNECTIVITY_FULL

    service = make_service()
    service._online = False
    nm_iface = make_nm_iface(check_connectivity=AsyncMock(side_effect=hang))

    with patch("backend.core.connectivity.service.NM_CHECK_CONNECTIVITY_TIMEOUT", 0.01):
        await service._recheck_fresh(nm_iface)

    assert service.online is False
    service._state_machine.broadcast.assert_not_awaited()
