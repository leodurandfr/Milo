# backend/tests/test_music_library_hotplug.py
"""The USB hotplug path — `StorageManager`'s boot and its udev bridge.

`test_music_library_storage.py` covers everything below this: the mount and
unmount flows, the helpers, the rescan. What it says of the layer above is *"the
pyudev monitor thread itself needs real udev events and is exercised on the Pi,
not here"* — and the measurement agrees: `initialize` (23 lines),
`_mount_present_devices` (8) and `_on_udev_event` (13) had never executed.

But `_on_udev_event` needs no udev at all. It is a pure bridge: it reads three
primitives off a Device object and hands a coroutine to the loop. What it does
need is its `except`, and that is why this file exists — **a raised exception
kills the pyudev monitor thread**, and after that no key is ever detected again
for the rest of the session, with nothing in any log but one debug line. The
same goes for `initialize`'s three fail-open arms: a dev host with no libudev
must boot, and the whole appliance must boot even when the monitor refuses to
start.

Nothing here touches real udev or spawns anything. `pyudev` is replaced in
`sys.modules` for every test, because the real one would enumerate *this Pi's*
block devices and `_mount_present_devices` would then run `sudo -n milo-mount`
against them; and `create_subprocess_exec` is a recorder that never reaches the
operating system.
"""
import asyncio
import sys

import pytest
from unittest.mock import AsyncMock

from backend.sources.music_library import storage as storage_mod
from backend.sources.music_library.storage import StorageManager

USB = {"ID_BUS": "usb", "DEVTYPE": "partition", "ID_FS_TYPE": "vfat"}


class _Dev(dict):
    """A pyudev Device, as far as this module ever looks at one."""

    def __init__(self, node="/dev/sda1", action=None, **props):
        super().__init__(props)
        self.device_node = node
        self.action = action

    def get(self, key, default=None):
        return dict.get(self, key, default)


class _Observer:
    """pyudev.MonitorObserver, recording that it was started and stopped."""

    instances = []

    def __init__(self, monitor, callback=None, name=None):
        self.monitor = monitor
        self.callback = callback
        self.name = name
        self.daemon = None
        self.started = False
        self.stopped = False
        self.stop_raises = None
        _Observer.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        if self.stop_raises is not None:
            raise self.stop_raises
        self.stopped = True


class _Monitor:
    def __init__(self, log):
        self.filters = []
        self._log = log

    def filter_by(self, subsystem):
        self.filters.append(subsystem)
        self._log.append(("filter_by", subsystem))


class _Context:
    def __init__(self, devices, log, enumerate_raises=None):
        self._devices = devices
        self._log = log
        self._raises = enumerate_raises

    def list_devices(self, **query):
        self._log.append(("list_devices", query))
        if self._raises is not None:
            raise self._raises
        return list(self._devices)


class _Pyudev:
    """Enough of the pyudev module for `initialize`, and a log of the order."""

    def __init__(self, devices=(), *, context_raises=None, monitor_raises=None,
                 enumerate_raises=None):
        self.log = []
        self._devices = devices
        self._context_raises = context_raises
        self._monitor_raises = monitor_raises
        self._enumerate_raises = enumerate_raises
        self.Monitor = self._monitor_factory()
        self.MonitorObserver = self._observer_factory()

    def Context(self):
        if self._context_raises is not None:
            raise self._context_raises
        return _Context(self._devices, self.log, self._enumerate_raises)

    def _monitor_factory(self):
        outer = self

        class Monitor:
            @staticmethod
            def from_netlink(context):
                if outer._monitor_raises is not None:
                    raise outer._monitor_raises
                outer.log.append(("from_netlink", None))
                return _Monitor(outer.log)

        return Monitor

    def _observer_factory(self):
        outer = self

        def make(monitor, callback=None, name=None):
            outer.log.append(("observer", name))
            return _Observer(monitor, callback=callback, name=name)

        return make


@pytest.fixture(autouse=True)
def no_real_udev_and_no_real_spawn(monkeypatch):
    """Neither the appliance's udev nor its mount helper is reachable by default.

    The real `pyudev.Context().list_devices()` here returns this Pi's own block
    devices, and the very next thing `initialize` does with one is `sudo -n
    milo-mount /dev/…`. A test that forgets its double must fail, not mount the
    boot medium.
    """
    _Observer.instances.clear()

    def _never(*args, **kwargs):
        raise AssertionError(f"a subprocess was spawned: {args}")

    monkeypatch.setattr(storage_mod.asyncio, "create_subprocess_exec", _never)
    monkeypatch.delitem(sys.modules, "pyudev", raising=False)
    monkeypatch.setitem(sys.modules, "pyudev", object())


@pytest.fixture
def changed():
    """The hook the layer above uses to reconcile Navidrome's libraries."""
    return AsyncMock()


@pytest.fixture
def manager(changed):
    return StorageManager(AsyncMock(return_value=None), changed)


def use(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "pyudev", fake)
    return fake


# =============================================================================
# Boot
# =============================================================================

class TestBoot:

    async def test_a_key_left_plugged_in_is_mounted_before_the_monitor_starts(
        self, manager, monkeypatch
    ):
        """Booting with a key in is the case nothing announces: udev has already
        fired its `add` before the backend existed, so the enumeration is the
        only thing that finds it. It must run before the observer, or an event
        arriving mid-enumeration has no loop-side state to land on."""
        fake = use(monkeypatch, _Pyudev([_Dev("/dev/sda1", **USB)]))
        mounted = []
        monkeypatch.setattr(manager, "_mount", AsyncMock(
            side_effect=lambda node, *a: mounted.append(node) or fake.log.append(("mount", node))
        ))

        assert await manager.initialize() is True

        steps = [name for name, _ in fake.log]
        assert mounted == ["/dev/sda1"]
        assert steps.index("mount") < steps.index("observer")

    async def test_the_key_is_mounted_with_the_identity_read_on_the_udev_thread(
        self, manager, monkeypatch
    ):
        """The Device object belongs to the monitor thread, so its properties are
        read here and passed as primitives — the uuid is what a user-given name
        is filed under, and it has to survive the hand-off."""
        use(monkeypatch, _Pyudev([
            _Dev("/dev/sda1", ID_FS_UUID="1234-ABCD", ID_FS_LABEL="IPOD", **USB),
        ]))
        mount = AsyncMock()
        monkeypatch.setattr(manager, "_mount", mount)

        await manager.initialize()

        mount.assert_awaited_once_with("/dev/sda1", "1234-ABCD", "IPOD")

    async def test_a_non_usb_partition_present_at_boot_is_left_alone(
        self, manager, monkeypatch
    ):
        """The boot medium is a partition on this very machine."""
        use(monkeypatch, _Pyudev([
            _Dev("/dev/mmcblk0p2", ID_BUS=None, DEVTYPE="partition", ID_FS_TYPE="ext4"),
        ]))
        mount = AsyncMock()
        monkeypatch.setattr(manager, "_mount", mount)

        await manager.initialize()

        mount.assert_not_awaited()

    async def test_the_monitor_only_ever_sees_block_events(self, manager, monkeypatch):
        """Unfiltered, every udev event on the machine crosses the thread bridge
        and lands in `_on_udev_event`'s try block."""
        fake = use(monkeypatch, _Pyudev())

        await manager.initialize()

        assert ("filter_by", "block") in fake.log

    async def test_the_monitor_thread_never_holds_up_a_backend_restart(
        self, manager, monkeypatch
    ):
        """A non-daemon monitor thread makes `systemctl restart milo-backend`
        wait for a thread that is blocked reading a netlink socket."""
        use(monkeypatch, _Pyudev())

        await manager.initialize()

        assert _Observer.instances[-1].daemon is True
        assert _Observer.instances[-1].started is True

    async def test_a_host_without_libudev_boots_without_usb(self, manager):
        """The documented dev-host case: no pyudev, no auto-mount, and a backend
        that still starts. `sys.modules["pyudev"]` is an object with no
        `Context`, which is what an unusable install looks like from here."""
        assert await manager.initialize() is False

    async def test_a_udev_context_that_cannot_be_built_disables_usb_only(
        self, manager, monkeypatch
    ):
        use(monkeypatch, _Pyudev(context_raises=OSError("no udev socket")))

        assert await manager.initialize() is False

    async def test_a_monitor_that_refuses_to_start_still_leaves_the_key_mounted(
        self, manager, monkeypatch
    ):
        """The two halves are independent: what was already plugged in is
        mounted and indexed even though hotplug will not work this session."""
        use(monkeypatch, _Pyudev(
            [_Dev("/dev/sda1", **USB)], monitor_raises=OSError("netlink refused"),
        ))
        mount = AsyncMock()
        monkeypatch.setattr(manager, "_mount", mount)

        assert await manager.initialize() is False
        mount.assert_awaited_once()

    async def test_an_enumeration_that_fails_does_not_stop_the_monitor(
        self, manager, monkeypatch
    ):
        """Hotplug is the half that matters going forward; losing the boot scan
        must not cost the key the user plugs in next."""
        use(monkeypatch, _Pyudev(enumerate_raises=OSError("udev db unreadable")))

        assert await manager.initialize() is True
        assert _Observer.instances[-1].started is True


class TestTeardown:

    async def test_cleanup_stops_the_monitor_thread(self, manager, monkeypatch):
        use(monkeypatch, _Pyudev())
        await manager.initialize()
        observer = _Observer.instances[-1]

        await manager.cleanup()

        assert observer.stopped is True

    async def test_a_monitor_that_refuses_to_stop_does_not_break_shutdown(
        self, manager, monkeypatch
    ):
        """cleanup() runs from the lifespan teardown; raising here would abort
        the rest of it."""
        use(monkeypatch, _Pyudev())
        await manager.initialize()
        _Observer.instances[-1].stop_raises = RuntimeError("thread already gone")

        await manager.cleanup()

        assert manager._observer is None


# =============================================================================
# The udev bridge — it runs on the monitor thread
# =============================================================================

class TestUdevBridge:

    @pytest.fixture
    async def bridged(self, manager):
        """A manager whose loop is known — what `initialize` sets on line one."""
        manager._loop = asyncio.get_running_loop()
        return manager

    async def _drain(self):
        """Let the coroutine the callback scheduled actually run."""
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_a_key_plugged_in_is_mounted_with_its_identity(
        self, bridged, monkeypatch
    ):
        mount = AsyncMock()
        monkeypatch.setattr(bridged, "_mount", mount)

        bridged._on_udev_event(
            _Dev("/dev/sdb1", action="add", ID_FS_UUID="AAAA-BBBB",
                 ID_FS_LABEL="MUSIC", **USB)
        )
        await self._drain()

        mount.assert_awaited_once_with("/dev/sdb1", "AAAA-BBBB", "MUSIC")

    async def test_a_key_pulled_out_is_unmounted_without_being_classified(
        self, bridged, monkeypatch
    ):
        """A `remove` event carries none of the ID_FS_* properties — the device
        is gone. Classifying it would reject every removal and leave the
        mountpoint behind, which is what keeps Navidrome indexing a tree that is
        not there. `_unmount` filters on the devnode map instead."""
        unmount = AsyncMock()
        monkeypatch.setattr(bridged, "_unmount", unmount)

        bridged._on_udev_event(_Dev("/dev/sdb1", action="remove"))
        await self._drain()

        unmount.assert_awaited_once_with("/dev/sdb1")

    async def test_a_non_usb_partition_appearing_is_ignored(self, bridged, monkeypatch):
        mount = AsyncMock()
        monkeypatch.setattr(bridged, "_mount", mount)

        bridged._on_udev_event(
            _Dev("/dev/mmcblk0p1", action="add", ID_BUS=None,
                 DEVTYPE="partition", ID_FS_TYPE="ext4")
        )
        await self._drain()

        mount.assert_not_awaited()

    async def test_an_action_that_is_neither_add_nor_remove_does_nothing(
        self, bridged, monkeypatch
    ):
        """udev emits `change` for every media poll on an optical drive and
        `bind`/`unbind` on the USB bus."""
        mount, unmount = AsyncMock(), AsyncMock()
        monkeypatch.setattr(bridged, "_mount", mount)
        monkeypatch.setattr(bridged, "_unmount", unmount)

        bridged._on_udev_event(_Dev("/dev/sdb1", action="change", **USB))
        await self._drain()

        mount.assert_not_awaited()
        unmount.assert_not_awaited()

    async def test_a_device_with_no_node_is_dropped(self, bridged, monkeypatch):
        unmount = AsyncMock()
        monkeypatch.setattr(bridged, "_unmount", unmount)

        bridged._on_udev_event(_Dev(None, action="remove"))
        await self._drain()

        unmount.assert_not_awaited()

    async def test_an_event_arriving_before_the_loop_is_known_is_dropped(
        self, manager, monkeypatch
    ):
        """The observer is started inside `initialize`, after `_loop` is set —
        but a callback that ran without it would raise on the bridge itself, and
        that exception is what kills the monitor thread.

        `assert_not_called`, not `assert_not_awaited`: without the `_loop` half
        of the guard the coroutine *is* built and handed to
        `run_coroutine_threadsafe(coro, None)`, which raises into the except and
        leaves it un-awaited — so an awaited-only assertion passes on the
        regression it is named for."""
        unmount = AsyncMock()
        monkeypatch.setattr(manager, "_unmount", unmount)
        assert manager._loop is None

        manager._on_udev_event(_Dev("/dev/sdb1", action="remove"))

        unmount.assert_not_called()

    async def test_a_broken_event_never_escapes_to_the_monitor_thread(self, bridged):
        """The reason the try/except is there: pyudev calls this from its own
        thread, and an exception that leaves it kills that thread. No key is
        detected again for the rest of the session, and the only trace is one
        debug line."""
        class _Exploding:
            @property
            def action(self):
                raise RuntimeError("udev device went away mid-read")

        bridged._on_udev_event(_Exploding())  # must simply return


# =============================================================================
# Identity — what a user-given name is filed under
# =============================================================================

class TestIdentity:

    def test_the_filesystem_uuid_is_the_identity(self):
        assert StorageManager._identity(
            _Dev(ID_FS_UUID="1234-ABCD", ID_FS_LABEL="IPOD")
        ) == ("1234-ABCD", "IPOD")

    def test_a_key_without_a_uuid_falls_back_to_its_bare_kernel_name(self):
        """Bare, not `/dev/sda1`: the identity travels in a URL path segment, and
        a value with a slash in it matches no route at all — so a key with no
        filesystem UUID could never be renamed or forgotten."""
        uuid, label = StorageManager._identity(
            _Dev(ID_FS_UUID=None, DEVNAME="/dev/sda1", ID_FS_LABEL=None)
        )

        assert uuid == "sda1"
        assert "/" not in uuid
        assert label == ""

    def test_a_key_with_neither_is_still_not_identity_less(self):
        uuid, _ = StorageManager._identity(_Dev(ID_FS_UUID=None, DEVNAME=None))

        assert uuid == ""
