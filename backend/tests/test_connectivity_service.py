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


# ============================================================================
# The signal callback and the teardown — both at zero.
#
# `_on_properties_changed` is the whole point of this service: NM probes on its
# own schedule and on every interface state change, and this callback is the
# only thing that turns a probe into a Milō event. `cleanup` is what stops the
# D-Bus session outliving the process.
#
# The callback is synchronous — dbus-next calls it from the signal dispatcher —
# so it hands the broadcast to the task set rather than awaiting it.
# ============================================================================

async def test_a_property_change_publishes_the_new_level():
    """NM's own probe is the only thing that notices the internet coming back.

    Nothing polls: without this the appliance keeps whatever level it read at
    boot for as long as it runs, so a router rebooted at 3 a.m. leaves the
    offline banner up until the next backend restart.
    """
    from dbus_next.signature import Variant

    service = make_service()
    service._level = ConnectivityLevel.NONE

    service._on_properties_changed(
        "org.freedesktop.NetworkManager",
        {"Connectivity": Variant("u", NM_FULL)},
        [],
    )
    await asyncio.sleep(0)
    await service._bg.cancel_all()

    assert service._level is ConnectivityLevel.FULL
    event = service._state_machine.broadcast.await_args.args[0]
    assert event.CATEGORY == "system"
    assert event.connectivity == ConnectivityLevel.FULL.value


async def test_a_raw_integer_value_is_read_the_same_as_a_variant():
    """Both shapes reach this callback depending on the dbus-next path.

    Unwrapped, a `Variant` is not a key of `NM_LEVELS`, so every signal would
    map to UNKNOWN — which every consumer treats as FULL, i.e. the offline
    banner would never appear again.
    """
    service = make_service()
    service._level = ConnectivityLevel.NONE

    service._on_properties_changed(
        "org.freedesktop.NetworkManager", {"Connectivity": NM_FULL}, []
    )
    await asyncio.sleep(0)
    await service._bg.cancel_all()

    assert service._level is ConnectivityLevel.FULL


async def test_a_signal_from_another_interface_is_ignored():
    """`PropertiesChanged` is a bus-wide signal; the listener is attached to the
    object, not to one interface. Unfiltered, a property of some other NM
    interface named `Connectivity` would drive the banner."""
    service = make_service()
    service._level = ConnectivityLevel.FULL

    service._on_properties_changed(
        "org.freedesktop.NetworkManager.Device", {"Connectivity": NM_NONE}, []
    )
    await asyncio.sleep(0)
    await service._bg.cancel_all()

    assert service._level is ConnectivityLevel.FULL
    service._state_machine.broadcast.assert_not_awaited()


async def test_a_signal_about_another_property_is_ignored():
    """NM emits PropertiesChanged for many of its properties. Reading them all
    would index `NM_LEVELS` with whatever changed and land on UNKNOWN."""
    service = make_service()
    service._level = ConnectivityLevel.FULL

    service._on_properties_changed(
        "org.freedesktop.NetworkManager", {"WirelessEnabled": True}, []
    )
    await asyncio.sleep(0)
    await service._bg.cancel_all()

    assert service._level is ConnectivityLevel.FULL
    service._state_machine.broadcast.assert_not_awaited()


async def test_an_unchanged_level_is_not_re_broadcast():
    """NM re-emits its connectivity property on every interface state change,
    and a wifi card renegotiating emits several a second. Broadcast each time,
    `full_state` is re-aggregated and re-sent to every client for nothing."""
    service = make_service()
    service._level = ConnectivityLevel.FULL

    service._on_properties_changed(
        "org.freedesktop.NetworkManager", {"Connectivity": NM_FULL}, []
    )
    await asyncio.sleep(0)
    await service._bg.cancel_all()

    service._state_machine.broadcast.assert_not_awaited()


async def test_an_unknown_nm_value_arriving_by_signal_fails_open():
    """A value NM gained after this was written must read as UNKNOWN, which
    every consumer treats as FULL — never report a problem we have not
    observed. Mapped to NONE instead, an NM upgrade would black out the UI."""
    service = make_service()
    service._level = ConnectivityLevel.FULL

    service._on_properties_changed(
        "org.freedesktop.NetworkManager", {"Connectivity": 99}, []
    )
    await asyncio.sleep(0)
    await service._bg.cancel_all()

    assert service._level is ConnectivityLevel.UNKNOWN


async def test_the_broadcast_is_skipped_when_no_state_machine_is_wired():
    """The service is constructed before `set_state_machine` runs, and NM can
    signal in that window. Unguarded it is an AttributeError inside a background
    task, which `BackgroundTaskSet` logs and nothing else notices."""
    service = ConnectivityService()
    service._level = ConnectivityLevel.NONE

    await service._broadcast()


class TestCleanup:
    """Teardown — `main.py` lists this service in the shutdown table."""

    def _wired(self):
        service = make_service()
        service._properties_iface = MagicMock()
        service._listener_attached = True
        service._bus = MagicMock()
        return service

    async def test_cleanup_detaches_the_listener_and_drops_the_bus(self):
        """A D-Bus session left open holds a name on the system bus; the
        restarted backend then attaches a second listener to the same signal and
        every NM property change is handled twice."""
        service = self._wired()
        bus = service._bus

        await service.cleanup()

        service._properties_iface.off_properties_changed.assert_called_once_with(
            service._on_properties_changed
        )
        bus.disconnect.assert_called_once()
        assert service._bus is None
        assert service._listener_attached is False

    async def test_cleanup_drains_the_task_set_first(self):
        """The forced-probe correction lives in it and can still be in flight at
        shutdown; it would broadcast through a state machine that is already
        being torn down."""
        service = self._wired()
        drained = []
        service._bg.cancel_all = AsyncMock(side_effect=lambda: drained.append(1))

        await service.cleanup()

        assert drained == [1]

    async def test_a_listener_that_was_never_attached_is_not_detached(self):
        """On a dev host with no NetworkManager, `initialize` fails open and
        leaves `_listener_attached` False. Detaching anyway raises inside a
        teardown entry, which `run_teardown` logs and moves past — so the bus
        below it would never be disconnected."""
        service = make_service()
        service._properties_iface = MagicMock()
        service._listener_attached = False
        service._bus = MagicMock()

        await service.cleanup()

        service._properties_iface.off_properties_changed.assert_not_called()
        assert service._bus is None

    async def test_a_detach_that_raises_still_lets_go_of_the_bus(self):
        """dbus-next raises if the signal was never matched. The bus disconnect
        below is the part that matters, and it must not be denied by it."""
        service = self._wired()
        service._properties_iface.off_properties_changed.side_effect = RuntimeError("no match")
        bus = service._bus

        await service.cleanup()

        bus.disconnect.assert_called_once()
        assert service._bus is None

    async def test_a_disconnect_that_raises_still_drops_the_reference(self):
        """A bus already gone raises here — the normal case when NM restarted.

        Keeping the reference means the next `initialize` finds a truthy `_bus`
        and the service believes it is still subscribed to a session that is dead.
        """
        service = self._wired()
        service._bus.disconnect.side_effect = RuntimeError("already closed")

        await service.cleanup()

        assert service._bus is None

    async def test_cleanup_on_a_service_that_never_initialised_is_a_no_op(self):
        """Fail-open means a dev host reaches shutdown with nothing attached."""
        service = ConnectivityService()

        await service.cleanup()

        assert service._bus is None
