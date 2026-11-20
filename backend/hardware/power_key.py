# backend/hardware/power_key.py
"""The Raspberry Pi 5's own power button, read as a proof of presence.

The board's button is a `gpio-keys` input named `pwr_button` (bcm2712 device
tree) that sends KEY_POWER, which logind turns into a power-off. It is read
here only while a challenge waits on it, with logind's handling of the key
held off by `milo-presence-inhibit.service` (core/system/presence.py), and
nowhere else.

The device is matched by its name *and* by sitting on the host bus: BlueZ's
AVRCP uinput device maps a phone's Power command to KEY_POWER too (see
rootfs/etc/udev/rules.d/71-milo-bt-no-power-switch.rules), and a Bluetooth
sender is exactly the remote party a proof of presence has to keep out.
"""
import asyncio
import logging
from typing import Optional

try:
    import evdev
    EVDEV_AVAILABLE = True
except ImportError:
    EVDEV_AVAILABLE = False

logger = logging.getLogger(__name__)

POWER_KEY_NAME = "pwr_button"


def is_the_boards_power_key(device) -> bool:
    """Whether an evdev device is the board's own button, not a key that only says so."""
    ecodes = evdev.ecodes
    return (
        device.name == POWER_KEY_NAME
        and device.info.bustype == ecodes.BUS_HOST
        and ecodes.KEY_POWER in device.capabilities().get(ecodes.EV_KEY, [])
    )


def _find_power_key():
    for path in evdev.list_devices():
        try:
            device = evdev.InputDevice(path)
        except OSError:
            continue
        if is_the_boards_power_key(device):
            return device
        device.close()
    return None


async def open_power_key() -> Optional["evdev.InputDevice"]:
    """The board's power button, open, or None: no evdev, no such input, or
    /dev/input unreadable (a dev host has neither)."""
    if not EVDEV_AVAILABLE:
        return None
    try:
        return await asyncio.to_thread(_find_power_key)
    except OSError as e:
        logger.warning("The power button could not be looked for in /dev/input: %s", e)
        return None


async def wait_for_press(device) -> None:
    """Return at the next press of the key. A release or an autorepeat is not one."""
    ecodes = evdev.ecodes
    async for event in device.async_read_loop():
        if event.type == ecodes.EV_KEY and event.code == ecodes.KEY_POWER and event.value == 1:
            return
