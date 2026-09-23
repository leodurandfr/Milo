"""The CD drive as the source hears it: udev's properties reduced to an event,
and the drive-status ioctl that answers the one question udev cannot.

This host IS the appliance and there is a disc in its drive: every test
installs the drive node it wants, and the real primitives raise.
"""
import fcntl
import os

import pytest

from backend.config.constants import CD_DEVICE
from backend.sources.cd.drive import (
    CDROM_DRIVE_STATUS, CDS_DISC_OK, CDS_DRIVE_NOT_READY, CdDrive, DriveEvent,
)

DRIVE_FD = 771


class FakeDriveNode:
    """`/dev/sr0` as an fd and a status code, plus what was done to it."""

    def __init__(self, status=CDS_DISC_OK, *, open_error=None, ioctl_error=None):
        self.status = status
        self.opened = []
        self.closed = []
        self.ioctls = []
        self._open_error = open_error
        self._ioctl_error = ioctl_error

    def install(self, monkeypatch):
        real_open, real_close, real_ioctl = os.open, os.close, fcntl.ioctl

        def open_(path, flags, *a):
            if str(path) != CD_DEVICE:
                return real_open(path, flags, *a)
            self.opened.append(flags)
            if self._open_error:
                raise self._open_error
            return DRIVE_FD

        def close_(fd):
            if fd != DRIVE_FD:
                return real_close(fd)
            self.closed.append(fd)

        def ioctl_(fd, request, *a):
            if request != CDROM_DRIVE_STATUS:
                return real_ioctl(fd, request, *a)
            assert fd == DRIVE_FD
            self.ioctls.append(request)
            if self._ioctl_error:
                raise self._ioctl_error
            return self.status

        monkeypatch.setattr(os, "open", open_)
        monkeypatch.setattr(os, "close", close_)
        monkeypatch.setattr(fcntl, "ioctl", ioctl_)
        return self


@pytest.fixture
def drive():
    return CdDrive(CD_DEVICE)


class TestTheDriveStatus:
    """Asked only when udev says "no media": a disc spinning up and an empty
    drive look the same to udev (measured), and only this tells them apart."""

    def test_a_spinning_disc_is_told_from_a_ready_one(self, drive, monkeypatch):
        node = FakeDriveNode(CDS_DRIVE_NOT_READY).install(monkeypatch)
        assert drive.status() == CDS_DRIVE_NOT_READY
        assert node.ioctls == [CDROM_DRIVE_STATUS]

    def test_the_device_is_opened_without_blocking(self, drive, monkeypatch):
        """A plain open of a CD device waits for the disc to spin up."""
        node = FakeDriveNode().install(monkeypatch)
        drive.status()
        assert node.opened and node.opened[0] & os.O_NONBLOCK

    def test_the_descriptor_is_released_even_when_the_ioctl_fails(self, drive, monkeypatch):
        """A descriptor leaked holds the drive: `eject` then reports it busy."""
        node = FakeDriveNode(ioctl_error=OSError(5, "Input/output error")).install(monkeypatch)
        assert drive.status() == -1
        assert node.closed == [DRIVE_FD]

    def test_a_drive_that_will_not_open_answers_minus_one(self, drive, monkeypatch):
        node = FakeDriveNode(open_error=OSError(19, "No such device")).install(monkeypatch)
        assert drive.status() == -1
        assert node.closed == []


class TestWhatUdevSays:
    """The properties measured on the unit (a SuperDrive, a 17-track CD)."""

    def test_a_readable_audio_disc(self):
        event = DriveEvent.from_udev("change", {
            "DISK_MEDIA_CHANGE": "1", "ID_CDROM_MEDIA": "1", "ID_CDROM_MEDIA_CD": "1",
            "ID_CDROM_MEDIA_TRACK_COUNT": "17", "ID_CDROM_MEDIA_TRACK_COUNT_AUDIO": "17",
        })
        assert (event.media, event.audio_tracks) == (True, 17)

    def test_a_change_with_no_media_is_an_insertion_or_an_eject(self):
        event = DriveEvent.from_udev("change", {"DISK_MEDIA_CHANGE": "1", "SYSTEMD_READY": "0"})
        assert (event.media, event.audio_tracks) == (False, 0)

    def test_a_disc_with_no_audio_track(self):
        event = DriveEvent.from_udev("change", {
            "ID_CDROM_MEDIA": "1", "ID_CDROM_MEDIA_TRACK_COUNT_DATA": "1",
        })
        assert (event.media, event.audio_tracks) == (True, 0)


def test_no_udev_reports_no_drive_and_runs_on(monkeypatch):
    """Fail open: a dev host without libudev has no drive, and says so once."""
    import builtins
    real_import = builtins.__import__

    def no_pyudev(name, *a, **k):
        if name == "pyudev":
            raise ImportError("no libudev")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pyudev)

    async def start():
        return CdDrive(CD_DEVICE).start(lambda event: None)

    import asyncio
    assert asyncio.run(start()) is None
