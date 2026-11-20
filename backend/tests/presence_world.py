# backend/tests/presence_world.py
"""The world a proof of presence lives in, for the tests that drive one.

What is faked is what the unit has and the suite does not: the board (whether
it is a Pi 5), the encoder (whether one runs), systemd (the inhibitor unit, and
the backend unit's root `ExecStopPost=` that clears a recorded challenge at
every stop), the root helper that records a challenge awaiting a power cut
(`milo-presence-record`, whose own behaviour `tests/architecture/
test_presence_record.py` runs), the kernel's boot id, and evdev's input devices
— the board's power button among keys that only say they are one. Everything
else is the real `PresenceService`.
"""
import asyncio
import json
import types
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from backend.config.constants import PRESENCE_RECORD_CMD
from backend.core.system import presence as presence_module
from backend.core.system.presence import PresenceService
from backend.hardware import power_key

# The evdev constants power_key reads, as the kernel numbers them.
EV_KEY = 0x01
KEY_POWER = 116
BUS_HOST = 0x19
BUS_BLUETOOTH = 0x05


class FakeInput:
    """One /dev/input node: what evdev reports of it, and its event queue."""

    def __init__(self, path, name, bustype, keys=(KEY_POWER,)):
        self.path = path
        self.name = name
        self.info = types.SimpleNamespace(bustype=bustype)
        self._keys = list(keys)
        self.events: asyncio.Queue = asyncio.Queue()
        self.closed = False

    def capabilities(self):
        return {EV_KEY: self._keys}

    async def async_read_loop(self):
        while True:
            yield await self.events.get()

    def press(self):
        """The key going down then up, as gpio-keys reports it."""
        self.events.put_nowait(types.SimpleNamespace(type=EV_KEY, code=KEY_POWER, value=1))
        self.events.put_nowait(types.SimpleNamespace(type=EV_KEY, code=KEY_POWER, value=0))

    def close(self):
        self.closed = True


class FakeEvdev:
    """The slice of python-evdev power_key uses, over a list of FakeInput."""

    ecodes = types.SimpleNamespace(EV_KEY=EV_KEY, KEY_POWER=KEY_POWER, BUS_HOST=BUS_HOST)

    def __init__(self, inputs):
        self.inputs = {device.path: device for device in inputs}

    def list_devices(self):
        return list(self.inputs)

    def InputDevice(self, path):  # noqa: N802 -- evdev's own name
        device = self.inputs[path]
        device.closed = False
        return device


class FakeRotary:
    """An encoder that runs: all PresenceService asks of it."""
    running = True


def install_inputs(monkeypatch, *inputs) -> FakeEvdev:
    fake = FakeEvdev(inputs)
    monkeypatch.setattr(power_key, "evdev", fake, raising=False)
    monkeypatch.setattr(power_key, "EVDEV_AVAILABLE", True)
    return fake


def board_button(path="/dev/input/event0") -> FakeInput:
    return FakeInput(path, power_key.POWER_KEY_NAME, BUS_HOST)


class _HelperRun:
    """One `sudo -n milo-presence-record <verb>`, as the helper answers it."""

    def __init__(self, world: "World", verb: str):
        self._world = world
        self._verb = verb
        self.returncode = None

    async def communicate(self, data=None):
        world = self._world
        world.helper_calls.append(self._verb)
        if world.helper_refuses:
            self.returncode = 1
            return b"", b"ERROR: refused"
        if self._verb == "arm":
            challenge_id, action, digest = data.decode().split()
            world.record.parent.mkdir(parents=True, exist_ok=True)
            # The helper reads the boot id itself: the caller never names it.
            world.record.write_text(json.dumps({
                "id": challenge_id, "action": action, "token_sha256": digest,
                "boot_id": world.boot_id.read_text().strip(),
            }))
        elif self._verb == "clear":
            world.record.unlink(missing_ok=True)
        self.returncode = 0
        return b"", b""

    def kill(self):
        self.returncode = -9


class World:
    """A unit: the record a power cut would prove, the boot it is in, its board."""

    def __init__(self, root: Path, monkeypatch=None, *, pi5=False, rotary=False):
        self.root = root
        self.pi5 = pi5
        self.rotary = rotary
        self.boot_id = root / "boot_id"
        self.boot_id.write_text("boot-1\n")
        # Where only root writes, on the unit: here, only the helper below does.
        self.record = root / "milo-presence" / "challenge.json"
        self.helper_calls: list[str] = []
        self.helper_refuses = False
        self.systemd = Mock()
        self.systemd.start = AsyncMock(return_value=True)
        self.systemd.stop = AsyncMock(return_value=True)
        self.systemd.unit_state = AsyncMock(return_value=("inactive", "success"))
        self.state_machine = Mock()
        self.state_machine.broadcast = AsyncMock()
        if monkeypatch is not None:
            spawn = presence_module.asyncio.create_subprocess_exec

            async def _spawn(*argv, **kwargs):
                if argv[:3] == ("sudo", "-n", PRESENCE_RECORD_CMD):
                    return _HelperRun(self, argv[3])
                return await spawn(*argv, **kwargs)

            monkeypatch.setattr(presence_module.asyncio, "create_subprocess_exec", _spawn)

    # --- what happens to the unit between two processes of the backend ---- #

    def stop_hook(self):
        """milo-backend.service's ExecStopPost: at every stop, whatever ended it."""
        self.record.unlink(missing_ok=True)

    def restart(self):
        """The backend stopped — asked for, crashed or killed — and started
        again, in the same boot."""
        self.stop_hook()

    def reboot(self):
        """A reboot: the backend's stop runs its hook, then a new boot."""
        self.stop_hook()
        self.power_cut()

    def power_cut(self, boot="boot-2"):
        """The plug pulled and put back: a new boot, and no stop before it."""
        self.boot_id.write_text(f"{boot}\n")

    # ---------------------------------------------------------------------- #

    def service(self) -> PresenceService:
        """A process of the backend on this unit (initialize it before use)."""
        hardware = Mock()
        hardware.power_button_supported = Mock(return_value=self.pi5)
        service = PresenceService(
            hardware, self.systemd, self.state_machine,
            record_file=self.record, boot_id_file=self.boot_id,
        )
        if self.rotary:
            service.attach_rotary(FakeRotary())
        return service

    def published(self):
        """Every challenge the service broadcast, in order (None for an end)."""
        return [call.args[0].challenge for call in self.state_machine.broadcast.await_args_list]
