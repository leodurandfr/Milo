# backend/tests/test_cd_reader.py
"""The CD audio reader: CDROMREADAUDIO ioctl -> named FIFO -> mpv.

This is the CD audio path itself. It had no test file, and no line of it had
ever run: the reader is a thread that talks to `/dev/sr0` and `/run/milo/
cd-audio.pcm` through raw descriptors, so the suite mocked the whole object out
wherever it appeared.

The outside world here is the drive and the kernel FIFO, and it is represented
by `FakeDrive` below. The real `os.open`/`os.write`/`fcntl.ioctl`/`os.mkfifo`
are made to RAISE for the whole module (`never_the_real_drive`) rather than be
spied on afterwards: this host *is* the appliance, the live backend holds
`/run/milo/cd-audio.pcm`, and there is a disc in the tray.

What is pinned, in order of what it costs to get wrong:

  * a thread `stop()` gave up on must stay stopped. `stop()` joins for 3 s and a
    thread parked inside a CDROMREADAUDIO retry cannot be interrupted, so the
    join times out and the reference is dropped while the thread lives.
  * the request struct is the kernel's `cdrom_read_audio` (24 bytes, fields at
    0/4/8/16). A layout that packs differently hands the drive a garbage LBA.
  * the ready handshake fires *before* the blocking FIFO open, which is the only
    reason the caller does not wait for mpv to appear.
  * a partial `os.write` is resumed. A chunk is 47040 bytes against a 64 KiB
    pipe buffer, so short writes are the normal case, not the exceptional one.
"""
import ctypes
import fcntl
import os
import struct
import threading
import time

import pytest

from backend.sources.cd import reader as reader_mod
from backend.sources.cd.reader import (
    CD_FIFO_PATH,
    CDROM_LBA,
    CDROMREADAUDIO,
    READ_CHUNK,
    SECTOR_SIZE,
    CdIoctlReader,
    stat_is_fifo,
)

CD_FD, FIFO_FD = 901, 902


class CdromReadAudio(ctypes.Structure):
    """`struct cdrom_read_audio` as <linux/cdrom.h> declares it.

    This is the oracle: the reader packs its request with a `struct` format
    string, and nothing else in the tree checks that the two agree. A format
    that packs differently hands the drive a well-formed request with the LBA in
    the wrong place — the drive returns the wrong sectors, or EFAULT, and every
    layer above sees a read that simply did not work.
    """
    _fields_ = [
        ("addr_lba", ctypes.c_int),
        ("addr_format", ctypes.c_ubyte),
        ("nframes", ctypes.c_int),
        ("buf", ctypes.c_void_p),
    ]


# `reader.py` does a plain `import os`, so there is no module-local binding to
# replace: patching `reader_mod.os.open` patches it for pytest too, and the run
# dies inside tmpdir cleanup. Every double here is therefore PATH-scoped and
# delegates everything that is not the drive or its FIFO to the real primitive.
DEVICE_PATHS = ("/dev/sr0", CD_FIFO_PATH)
_REAL = {name: getattr(os, name) for name in
         ("open", "close", "write", "mkfifo", "remove")}


def _is_device(target):
    return isinstance(target, (str, bytes)) and str(target) in DEVICE_PATHS


@pytest.fixture(autouse=True)
def never_the_real_drive(monkeypatch):
    """Reaching the real /dev/sr0 or /run/milo/cd-audio.pcm fails loudly.

    This host IS the appliance: the live backend owns that FIFO and there is a
    disc in the tray. A test that forgets to install a FakeDrive must not find
    the real one.
    """
    def guard(name):
        real = _REAL[name]

        def wrapper(target, *args, **kwargs):
            if _is_device(target):
                raise AssertionError(
                    f"a test reached the appliance's real drive: os.{name}({target!r})"
                )
            return real(target, *args, **kwargs)
        return wrapper

    for name in ("open", "mkfifo", "remove"):
        monkeypatch.setattr(os, name, guard(name))

    real_ioctl = fcntl.ioctl

    def ioctl_guard(fd, request, *args, **kwargs):
        if request in (CDROMREADAUDIO,):
            raise AssertionError("a test issued a real CDROMREADAUDIO")
        return real_ioctl(fd, request, *args, **kwargs)

    monkeypatch.setattr(fcntl, "ioctl", ioctl_guard)


class FakeDrive:
    """The drive and the FIFO, as the reader is entitled to see them.

    Records every ioctl request it was handed and every byte written, and can be
    told to park inside the ioctl (a scratched sector retrying in the kernel) or
    to fail a read or a write.
    """

    def __init__(self, *, fifo_exists=True, write_limit=None,
                 ioctl_error=None, write_error=None, open_error=None):
        self.requests = []          # decoded (lba, addr_format, nframes)
        self.written = []           # (thread ident, bytes)
        self.closed = []
        self.opened = []
        self.mkfifo_calls = []
        self.removed = []
        self._fifo_exists = fifo_exists
        self._write_limit = write_limit
        self._ioctl_error = ioctl_error
        self._write_error = write_error
        self._open_error = open_error
        self.park_in_ioctl = None   # threading.Event -> block until it is set
        self.entered_ioctl = threading.Event()
        self.fifo_open_blocks = None

    # --- the primitives the reader calls -------------------------------------
    def open(self, path, flags, *a):
        self.opened.append((path, flags))
        if self._open_error and path != CD_FIFO_PATH:
            raise self._open_error
        if path == CD_FIFO_PATH and flags & 1:      # O_WRONLY: blocks on mpv
            if self.fifo_open_blocks is not None:
                self.fifo_open_blocks.wait(10)
            return FIFO_FD
        return CD_FD if path != CD_FIFO_PATH else FIFO_FD

    def close(self, fd):
        self.closed.append(fd)

    def ioctl(self, fd, request, buf):
        assert fd == CD_FD, "the audio read must go to the CD device, not the FIFO"
        assert request == CDROMREADAUDIO
        raw = bytes(buf)
        assert len(raw) == ctypes.sizeof(CdromReadAudio), (
            f"the request is {len(raw)} bytes; the kernel reads "
            f"{ctypes.sizeof(CdromReadAudio)}"
        )
        req = CdromReadAudio.from_buffer_copy(raw)
        assert req.buf, "the audio buffer pointer is null"
        self.requests.append((req.addr_lba, req.addr_format, req.nframes))
        self.entered_ioctl.set()
        if self.park_in_ioctl is not None:
            self.park_in_ioctl.wait(10)
        if self._ioctl_error is not None:
            raise self._ioctl_error
        return 0

    def write(self, fd, view):
        assert fd == FIFO_FD, "PCM must go to the FIFO, not the CD device"
        if self._write_error is not None:
            raise self._write_error
        n = len(view) if self._write_limit is None else min(self._write_limit, len(view))
        self.written.append((threading.get_ident(), bytes(view[:n])))
        return n

    def exists(self, path):
        return self._fifo_exists

    def mkfifo(self, path, mode=0o644):
        self.mkfifo_calls.append((path, mode))
        self._fifo_exists = True

    def remove(self, path):
        self.removed.append(path)

    # --- helpers -------------------------------------------------------------
    def install(self, monkeypatch, *, is_fifo=True):
        """Take over only the drive's paths and the two fds it hands out."""
        fake = {"open": self.open, "mkfifo": self.mkfifo, "remove": self.remove}
        for name, mine in fake.items():
            real = _REAL[name]

            def route(target, *a, _mine=mine, _real=real, **k):
                return _mine(target, *a, **k) if _is_device(target) else _real(target, *a, **k)
            monkeypatch.setattr(os, name, route)

        real_close, real_write = _REAL["close"], _REAL["write"]
        monkeypatch.setattr(os, "close",
                            lambda fd: self.close(fd) if fd in (CD_FD, FIFO_FD) else real_close(fd))
        monkeypatch.setattr(os, "write",
                            lambda fd, b: self.write(fd, b) if fd in (CD_FD, FIFO_FD) else real_write(fd, b))

        real_exists = os.path.exists
        monkeypatch.setattr(os.path, "exists",
                            lambda p: self.exists(p) if _is_device(p) else real_exists(p))

        real_ioctl = fcntl.ioctl
        monkeypatch.setattr(fcntl, "ioctl",
                            lambda fd, req, *a: self.ioctl(fd, req, *a)
                            if fd in (CD_FD, FIFO_FD) else real_ioctl(fd, req, *a))
        monkeypatch.setattr(reader_mod, "stat_is_fifo", lambda _p: is_fifo)
        return self

    @property
    def total_pcm(self):
        return b"".join(b for _ident, b in self.written)

    @property
    def writer_threads(self):
        return {ident for ident, _b in self.written}


def _drain(reader, drive, *, sectors, timeout=5.0):
    """Wait until the drive has served `sectors` worth of ioctl requests."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sum(n for _l, _f, n in drive.requests) >= sectors:
            return True
        time.sleep(0.005)
    return False


class TestTheRequestStruct:
    """The request the reader hands the kernel.

    `FakeDrive.ioctl` decodes every request with `CdromReadAudio` above, so the
    whole file is the check: any packing that disagrees with <linux/cdrom.h>
    turns the read-loop tests red. This class only adds what a single run cannot
    show — that the values survive a full 32-bit LBA and the buffer pointer is
    the one the loop actually reads out of.
    """

    def test_the_drive_receives_the_lba_and_length_the_loop_intended(self, monkeypatch):
        drive = FakeDrive().install(monkeypatch)
        reader = CdIoctlReader()

        # A leadout past 2^16 sectors: a short field would truncate it, and an
        # audio CD runs to ~360 000 sectors, so every disc is past that.
        reader.start(333_000, 333_000 + READ_CHUNK)
        reader._thread.join(timeout=5)

        assert drive.requests == [(333_000, CDROM_LBA, READ_CHUNK)]

    def test_the_buffer_pointer_is_where_the_pcm_is_read_back_from(self, monkeypatch):
        """The kernel writes into `buf`; the loop then copies out of its own
        ctypes buffer. If the pointer it sends is not that buffer's address, the
        FIFO is fed whatever was in it before — silence, or the previous chunk."""
        drive = FakeDrive().install(monkeypatch)
        pointers = []

        real_ioctl = drive.ioctl
        installed = fcntl.ioctl

        def capture(fd, request, buf):
            if fd not in (CD_FD, FIFO_FD):
                return installed(fd, request, buf)
            ptr = CdromReadAudio.from_buffer_copy(bytes(buf)).buf
            pointers.append(ptr)
            # Write a recognisable pattern where the reader said the buffer is,
            # which is what the kernel does with it.
            ctypes.memmove(ptr, b"\xa5" * SECTOR_SIZE, SECTOR_SIZE)
            return real_ioctl(fd, request, buf)

        monkeypatch.setattr(fcntl, "ioctl", capture)

        reader = CdIoctlReader()
        reader.start(0, 1)
        reader._thread.join(timeout=5)

        assert len(pointers) == 1 and pointers[0]
        assert drive.total_pcm[:SECTOR_SIZE] == b"\xa5" * SECTOR_SIZE, (
            "the PCM handed to the FIFO did not come from the buffer whose "
            "address was given to the kernel"
        )


class TestTheReadLoop:
    def test_it_reads_from_the_start_lba_and_stops_at_the_end(self, monkeypatch):
        """The end LBA is the leadout: reading past it returns the next track's
        audio, or the drive's error for a sector that is not there."""
        drive = FakeDrive().install(monkeypatch)
        reader = CdIoctlReader()

        reader.start(1000, 1000 + READ_CHUNK * 3)
        reader._thread.join(timeout=5)

        assert not reader._thread.is_alive()
        assert [lba for lba, _f, _n in drive.requests] == [
            1000, 1000 + READ_CHUNK, 1000 + READ_CHUNK * 2
        ]
        assert all(fmt == CDROM_LBA for _l, fmt, _n in drive.requests)

    def test_the_last_read_is_clamped_to_what_is_left(self, monkeypatch):
        """`min(READ_CHUNK, end_lba - lba)`. Without the clamp the final ioctl
        asks for a full chunk and overruns the leadout."""
        drive = FakeDrive().install(monkeypatch)
        reader = CdIoctlReader()

        reader.start(0, READ_CHUNK + 3)
        reader._thread.join(timeout=5)

        assert [n for _l, _f, n in drive.requests] == [READ_CHUNK, 3]
        assert len(drive.total_pcm) == (READ_CHUNK + 3) * SECTOR_SIZE

    def test_a_short_write_is_resumed_from_where_it_stopped(self, monkeypatch):
        """A chunk is 47040 bytes against a 64 KiB pipe buffer, so `os.write`
        returning less than it was given is the normal case. Dropping the
        remainder would silently punch holes in the audio."""
        drive = FakeDrive(write_limit=4096).install(monkeypatch)
        reader = CdIoctlReader()

        reader.start(0, READ_CHUNK)
        reader._thread.join(timeout=5)

        expected = READ_CHUNK * SECTOR_SIZE
        assert len(drive.written) > 1, "the fixture did not force a partial write"
        assert len(drive.total_pcm) == expected
        assert all(len(b) <= 4096 for _i, b in drive.written)


class TestTheReadyHandshake:
    def test_ready_fires_before_the_blocking_fifo_open(self, monkeypatch):
        """The caller waits on wait_ready() and only then tells mpv to load the
        FIFO. Setting the event after the O_WRONLY open would deadlock: that
        open does not return until mpv is already reading."""
        drive = FakeDrive()
        drive.fifo_open_blocks = threading.Event()
        drive.install(monkeypatch)
        reader = CdIoctlReader()

        reader.start(0, READ_CHUNK)
        assert reader.wait_ready(timeout=5.0) is True
        assert not drive.requests, "the loop ran before mpv opened the read end"

        drive.fifo_open_blocks.set()          # mpv attaches
        reader._thread.join(timeout=5)
        assert drive.requests

    def test_a_drive_that_will_not_open_never_reports_ready(self, monkeypatch):
        """wait_ready() answers False and the caller aborts, instead of telling
        mpv to load a FIFO nothing will ever feed."""
        drive = FakeDrive(open_error=OSError("No medium found")).install(monkeypatch)
        reader = CdIoctlReader()

        reader.start(0, READ_CHUNK)
        assert reader.wait_ready(timeout=0.5) is False
        reader._thread.join(timeout=5)
        assert drive.requests == []
        assert reader.is_running is False


class TestHowARunEnds:
    def test_a_broken_pipe_ends_the_run_without_an_error(self, monkeypatch, caplog):
        """mpv closing the read end is a track change or a stop, not a fault.
        Logged as an error it would raise the WebSocket error banner every time
        the user skips a track."""
        drive = FakeDrive(write_error=BrokenPipeError(32, "Broken pipe")).install(monkeypatch)
        reader = CdIoctlReader()

        with caplog.at_level("WARNING", logger="source.cd.reader"):
            reader.start(0, READ_CHUNK * 4)
            reader._thread.join(timeout=5)

        assert caplog.records == []
        assert len(drive.requests) == 1, "the loop kept reading after the pipe closed"

    def test_a_drive_read_error_is_reported_and_ends_the_run(self, monkeypatch, caplog):
        drive = FakeDrive(ioctl_error=OSError(5, "Input/output error")).install(monkeypatch)
        reader = CdIoctlReader()

        with caplog.at_level("ERROR", logger="source.cd.reader"):
            reader.start(4242, 4242 + READ_CHUNK * 4)
            reader._thread.join(timeout=5)

        assert len(drive.requests) == 1
        assert any("4242" in r.message for r in caplog.records), \
            "the failing LBA is the only clue to which sector the disc lost"

    def test_both_descriptors_are_closed_however_the_run_ends(self, monkeypatch):
        """The CD fd holds the drive: leaked once per track, the drive is never
        released and the tray will not open."""
        drive = FakeDrive(ioctl_error=OSError(5, "Input/output error")).install(monkeypatch)
        reader = CdIoctlReader()

        reader.start(0, READ_CHUNK)
        reader._thread.join(timeout=5)

        assert sorted(drive.closed) == [CD_FD, FIFO_FD]

    def test_running_is_cleared_when_the_loop_exits(self, monkeypatch):
        """`is_running` is what the monitor tick reads to tell an album that has
        finished from a track still playing (source.py::_on_monitor_tick)."""
        drive = FakeDrive()
        drive.park_in_ioctl = threading.Event()
        drive.install(monkeypatch)
        reader = CdIoctlReader()

        reader.start(0, READ_CHUNK)
        assert drive.entered_ioctl.wait(5)
        assert reader.is_running is True

        drive.park_in_ioctl.set()
        reader._thread.join(timeout=5)
        assert reader.is_running is False


class TestStop:
    def test_stop_opens_the_read_end_to_unblock_a_parked_writer(self, monkeypatch):
        """The thread blocks in `os.open(FIFO, O_WRONLY)` until a reader appears.
        If mpv never came, nothing else would ever release it."""
        drive = FakeDrive()
        drive.fifo_open_blocks = threading.Event()
        # Opening the read end is what releases the writer, exactly as the
        # kernel does it.
        real_open = drive.open

        def open_and_release(path, flags, *a):
            if path == CD_FIFO_PATH and not flags & 1:   # O_RDONLY|O_NONBLOCK
                drive.fifo_open_blocks.set()
            return real_open(path, flags, *a)

        drive.install(monkeypatch)
        monkeypatch.setattr(reader_mod.os, "open", open_and_release)
        reader = CdIoctlReader()

        reader.start(0, READ_CHUNK * 100)
        assert reader.wait_ready(timeout=5.0) is True
        reader.stop()

        assert reader._thread is None
        assert any(p == CD_FIFO_PATH and not f & 1 for p, f in drive.opened), \
            "stop() never opened the read end, so a parked writer stays parked"

    def test_stop_is_safe_before_any_run(self, monkeypatch):
        FakeDrive().install(monkeypatch)
        CdIoctlReader().stop()      # must not raise, and must not touch the drive

    def test_a_stopped_run_does_not_drain_the_rest_of_the_disc(self, monkeypatch):
        drive = FakeDrive().install(monkeypatch)
        reader = CdIoctlReader()

        reader.start(0, READ_CHUNK * 10_000)
        assert _drain(reader, drive, sectors=READ_CHUNK)
        reader.stop()

        served = len(drive.requests)
        time.sleep(0.1)
        assert len(drive.requests) == served
        assert served < 10_000, "the loop ignored the stop and read to the leadout"


class TestAThreadStopGaveUpOn:
    """`stop()` joins for 3 s. A thread parked inside a CDROMREADAUDIO retry —
    a scratched sector, retried in the kernel — cannot be interrupted, so the
    join times out and the reference is dropped while the thread is alive.

    The stop flag used to be shared and `start()` cleared it, which un-stopped
    that thread: it resumed writing PCM into the same FIFO as its replacement,
    interleaved, until the disc ran out.
    """

    def test_it_never_writes_again_once_a_new_run_has_started(self, monkeypatch):
        drive = FakeDrive()
        drive.park_in_ioctl = threading.Event()
        drive.install(monkeypatch)
        reader = CdIoctlReader()

        reader.start(0, READ_CHUNK * 10_000)
        assert drive.entered_ioctl.wait(5)
        abandoned = reader._thread.ident
        assert drive.written == [], "the parked thread wrote before it was abandoned"

        reader.stop()                       # join times out; still alive
        assert reader._thread is None

        reader.start(50_000, 50_000 + READ_CHUNK * 4)
        drive.park_in_ioctl.set()           # the stuck ioctl finally returns
        reader._thread.join(timeout=5)
        time.sleep(0.2)                     # let the abandoned thread run on

        assert abandoned not in drive.writer_threads, (
            "the abandoned reader was un-stopped by the next start() and wrote "
            "PCM into the FIFO alongside its replacement"
        )
        assert len(drive.writer_threads) == 1

    def test_the_abandoned_run_is_reported(self, monkeypatch, caplog):
        """It is the operator's only sign that the drive is struggling — the
        audio itself just stutters."""
        drive = FakeDrive()
        drive.park_in_ioctl = threading.Event()
        drive.install(monkeypatch)
        reader = CdIoctlReader()

        reader.start(0, READ_CHUNK * 10_000)
        assert drive.entered_ioctl.wait(5)
        with caplog.at_level("WARNING", logger="source.cd.reader"):
            reader.stop()

        assert any("did not stop" in r.message for r in caplog.records)
        drive.park_in_ioctl.set()


class TestTheFifo:
    def test_a_missing_fifo_is_created(self, monkeypatch):
        drive = FakeDrive(fifo_exists=False).install(monkeypatch)
        CdIoctlReader._ensure_fifo()
        assert drive.mkfifo_calls == [(CD_FIFO_PATH, 0o644)]
        assert drive.removed == []

    def test_an_existing_fifo_is_left_alone(self, monkeypatch):
        drive = FakeDrive(fifo_exists=True).install(monkeypatch, is_fifo=True)
        CdIoctlReader._ensure_fifo()
        assert drive.mkfifo_calls == []
        assert drive.removed == []

    def test_a_regular_file_squatting_the_path_is_replaced(self, monkeypatch):
        """`mpv --demuxer=rawaudio` on a regular file reads it once and stops.
        Left in place, CD playback produces a fraction of a second of audio."""
        drive = FakeDrive(fifo_exists=True).install(monkeypatch, is_fifo=False)
        CdIoctlReader._ensure_fifo()
        assert drive.removed == [CD_FIFO_PATH]
        assert drive.mkfifo_calls == [(CD_FIFO_PATH, 0o644)]

    def test_stat_is_fifo_answers_no_for_a_path_that_is_not_there(self, tmp_path):
        assert stat_is_fifo(str(tmp_path / "nothing-here")) is False

    def test_stat_is_fifo_tells_a_fifo_from_a_file(self, tmp_path):

        plain = tmp_path / "plain"
        plain.write_bytes(b"x")
        fifo = tmp_path / "fifo"
        _REAL["mkfifo"](str(fifo))
        assert stat_is_fifo(str(plain)) is False
        assert stat_is_fifo(str(fifo)) is True
