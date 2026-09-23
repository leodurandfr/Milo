# backend/sources/cd/drive.py
"""
The CD drive as udev announces it, and the drive-status ioctl that confirms.

Measured on the unit (Apple USB SuperDrive, a slot drive; docs: source
architecture, phase 2):
- insert: one `change`, with ID_CDROM_MEDIA and the audio track count, once
  the disc is readable (~10 s). The kernel's own 2 s poll relies on the
  drive's event report, which says "new media" only then; an open() makes it
  ask TEST UNIT READY instead, which answers "becoming ready" during the spin
  and yields an earlier `change` with no media (the ioctl: DRIVE_NOT_READY);
- eject: one `change` with no media when the disc is out; an empty slot drive
  answers TRAY_OPEN; pulling the disc out of the slot announces nothing;
- unplug: `remove`; replug: `add`, then a `change` once the drive has read
  whatever is in it (8.7 s with a disc, the ioctl's open blocking meanwhile);
- nothing at all while a disc sits in the drive.

A `change` with no media is an insertion spinning up or an eject: that one
question goes to the ioctl. The one poll left is the source's insertion probe
(an empty drive, CD on screen), and it can only find a disc, never lose one —
a poller that could read a failed probe as a removal was E37. No native push
exists for this drive: SATA drives can notify asynchronously
(/sys/block/sr0/events_async), USB ones cannot (measured: empty).
"""
import asyncio
import fcntl
import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping, Optional

from backend.config.constants import CD_DEVICE

# CDROM_DRIVE_STATUS and its answers (linux/cdrom.h).
CDROM_DRIVE_STATUS = 0x5326
CDS_NO_DISC = 1
CDS_TRAY_OPEN = 2
CDS_DRIVE_NOT_READY = 3
CDS_DISC_OK = 4


class DiscState(str, Enum):
    """The device axis: what is in the drive, independent of any session."""
    NO_DRIVE = "no_drive"
    EMPTY = "empty"
    READING = "reading"          # a disc spins; its TOC is not readable yet
    UNREADABLE = "unreadable"    # a disc is in, and it is not an audio CD
    IDENTIFYING = "identifying"  # TOC read; the disc is being named
    READY = "ready"
    EJECTING = "ejecting"


@dataclass(frozen=True)
class DriveEvent:
    """One udev event about the drive, reduced to what the source reads."""
    action: str                  # "add" | "remove" | "change" | "present"
    media: bool = False          # ID_CDROM_MEDIA: a readable disc is in
    audio_tracks: int = 0        # ID_CDROM_MEDIA_TRACK_COUNT_AUDIO

    @classmethod
    def from_udev(cls, action: str, properties: Mapping[str, Any]) -> "DriveEvent":
        return cls(
            action=action,
            media=properties.get("ID_CDROM_MEDIA") == "1",
            audio_tracks=int(properties.get("ID_CDROM_MEDIA_TRACK_COUNT_AUDIO") or 0),
        )


class CdDrive:
    """udev's monitor on the drive's block device, bridged to the event loop.

    Fail open: with no udev (a dev host without libudev) the drive is reported
    absent and the backend runs on.
    """

    def __init__(self, device: str = CD_DEVICE):
        self._device = device
        self._name = os.path.basename(device)
        self._observer = None
        self._logger = logging.getLogger("source.cd.drive")

    def start(self, on_event: Callable[[DriveEvent], None]) -> Optional[DriveEvent]:
        """Start listening; return the drive as udev knows it now — a
        `present` event — or None when there is no drive (or no udev).

        The monitor starts before the snapshot is taken, so an event landing
        in between is heard rather than lost; `on_event` runs on the loop.
        """
        loop = asyncio.get_running_loop()
        try:
            import pyudev
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by("block")
        except Exception as e:
            self._logger.warning("udev unavailable, CD drive disabled: %s", e)
            return None

        def on_device(device) -> None:
            # On the monitor thread: read the primitives here, hand them over.
            try:
                if device.sys_name != self._name:
                    return
                event = DriveEvent.from_udev(device.action, dict(device.properties))
                loop.call_soon_threadsafe(on_event, event)
            except Exception as e:
                # A raised exception would kill the monitor thread.
                self._logger.error("udev event handling failed: %s", e)

        try:
            self._observer = pyudev.MonitorObserver(monitor, callback=on_device, name="milo-cd-drive")
            self._observer.daemon = True
            self._observer.start()
        except Exception as e:
            self._logger.warning("Failed to start the CD drive monitor: %s", e)
            return None

        try:
            device = pyudev.Devices.from_name(context, "block", self._name)
        except pyudev.DeviceNotFoundError:
            return None                  # no drive plugged in
        except Exception as e:
            self._logger.warning("Could not read the CD drive from udev, taken as absent: %s", e)
            return None
        return DriveEvent.from_udev("present", dict(device.properties))

    def stop(self) -> None:
        if self._observer is not None:
            try:
                self._observer.stop()
            except Exception as e:
                self._logger.warning("CD drive monitor stop error: %s", e)
            self._observer = None

    def status(self) -> int:
        """CDROM_DRIVE_STATUS, or -1 when the drive cannot be asked. Blocking
        (the open waits while a drive spins up): run it in a thread."""
        try:
            fd = os.open(self._device, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            return -1
        try:
            return fcntl.ioctl(fd, CDROM_DRIVE_STATUS)
        except OSError:
            return -1
        finally:
            os.close(fd)
