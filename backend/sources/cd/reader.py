# backend/sources/cd/reader.py
"""
Direct CD audio reader using Linux CDROMREADAUDIO ioctl.

Reads raw PCM sectors from the CD drive and writes them to a named FIFO,
bypassing libcdio/cdparanoia TOC parsing (~17s) for instant playback start.
mpv reads the FIFO with --demuxer=rawaudio.

CD audio format: 44100 Hz, 16-bit signed LE, stereo, 2352 bytes/sector, 75 sectors/second.
"""
import ctypes
import fcntl
import logging
import os
import struct
import threading
from typing import Optional

logger = logging.getLogger("source.cd.reader")

# Linux ioctl constants
CDROMREADAUDIO = 0x530E
CDROM_LBA = 0x01

# CD audio constants
SECTOR_SIZE = 2352  # bytes per sector (588 frames x 4 bytes/frame)
SECTORS_PER_SECOND = 75
READ_CHUNK = 20  # sectors per ioctl call (~0.27s of audio)

# FIFO path (RuntimeDirectory=milo already exists)
CD_FIFO_PATH = "/run/milo/cd-audio.pcm"


class CdIoctlReader:
    """Reads CD audio sectors via ioctl and writes PCM to a FIFO.

    Runs in a dedicated thread. The FIFO connects to mpv which decodes
    the raw PCM stream. Supports start/stop and repositioning via LBA.
    """

    def __init__(self, device: str = "/dev/sr0"):
        self._device = device
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._current_lba = 0
        self._lba_lock = threading.Lock()
        self._running = False

    @property
    def current_lba(self) -> int:
        """Current read position (LBA). Thread-safe."""
        with self._lba_lock:
            return self._current_lba

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    def start(self, start_lba: int, end_lba: int) -> None:
        """Start reading from start_lba to end_lba, writing PCM to FIFO.

        The thread blocks on FIFO open until mpv opens the read end.
        Call wait_ready() to synchronize before telling mpv to loadfile.
        """
        self.stop()
        self._stop_event.clear()
        self._ready_event.clear()
        with self._lba_lock:
            self._current_lba = start_lba
        self._running = True
        self._thread = threading.Thread(
            target=self._read_loop,
            args=(start_lba, end_lba),
            daemon=True,
            name="cd-ioctl-reader",
        )
        self._thread.start()

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """Wait until the reader has opened the CD device and is ready to connect FIFO."""
        return self._ready_event.wait(timeout=timeout)

    def stop(self) -> None:
        """Stop the reader thread. Safe to call even if not running."""
        if not self._thread:
            return
        self._stop_event.set()
        self._running = False
        # Unblock writer if stuck waiting for a reader on the FIFO
        try:
            fd = os.open(CD_FIFO_PATH, os.O_RDONLY | os.O_NONBLOCK)
            os.close(fd)
        except OSError:
            pass
        self._thread.join(timeout=3.0)
        if self._thread.is_alive():
            logger.warning("CD reader thread did not stop within timeout")
        self._thread = None

    def _read_loop(self, start_lba: int, end_lba: int) -> None:
        """Main read loop: ioctl read -> FIFO write."""
        cd_fd = -1
        fifo_fd = -1
        try:
            cd_fd = os.open(self._device, os.O_RDONLY | os.O_NONBLOCK)
            self._ensure_fifo()

            # Signal: CD device open, FIFO created, about to block on FIFO open
            self._ready_event.set()

            if self._stop_event.is_set():
                return

            # Blocks until mpv opens the FIFO for reading
            fifo_fd = os.open(CD_FIFO_PATH, os.O_WRONLY)

            if self._stop_event.is_set():
                return

            lba = start_lba
            audio_buf = ctypes.create_string_buffer(READ_CHUNK * SECTOR_SIZE)
            buf_ptr = ctypes.addressof(audio_buf)

            while lba < end_lba and not self._stop_event.is_set():
                nframes = min(READ_CHUNK, end_lba - lba)

                # Pack struct cdrom_read_audio with native alignment:
                # int addr.lba | u8 addr_format | [pad3] | int nframes | [pad] | void* buf
                req = bytearray(struct.pack("@iBiP", lba, CDROM_LBA, nframes, buf_ptr))

                try:
                    fcntl.ioctl(cd_fd, CDROMREADAUDIO, req)
                except OSError as e:
                    if self._stop_event.is_set():
                        break
                    logger.error(f"CDROMREADAUDIO failed at LBA {lba}: {e}")
                    break

                # Write PCM data to FIFO (may block if mpv buffer is full)
                data = audio_buf.raw[: nframes * SECTOR_SIZE]
                try:
                    view = memoryview(data)
                    offset = 0
                    while offset < len(data):
                        if self._stop_event.is_set():
                            return
                        written = os.write(fifo_fd, view[offset:])
                        offset += written
                except BrokenPipeError:
                    # mpv closed the read end (track change or stop)
                    break
                except OSError as e:
                    if self._stop_event.is_set():
                        break
                    logger.error(f"FIFO write error at LBA {lba}: {e}")
                    break

                lba += nframes
                with self._lba_lock:
                    self._current_lba = lba

        except OSError as e:
            if not self._stop_event.is_set():
                logger.error(f"CD reader error: {e}")
        finally:
            self._running = False
            for fd in (fifo_fd, cd_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    @staticmethod
    def _ensure_fifo() -> None:
        """Create the FIFO if it doesn't exist (or recreate if it's a regular file)."""
        if os.path.exists(CD_FIFO_PATH):
            if not stat_is_fifo(CD_FIFO_PATH):
                os.remove(CD_FIFO_PATH)
                os.mkfifo(CD_FIFO_PATH, 0o644)
        else:
            os.mkfifo(CD_FIFO_PATH, 0o644)

    @staticmethod
    def cleanup_fifo() -> None:
        """Remove the FIFO."""
        try:
            if os.path.exists(CD_FIFO_PATH):
                os.remove(CD_FIFO_PATH)
        except OSError:
            pass


def stat_is_fifo(path: str) -> bool:
    """Check if path is a FIFO (named pipe)."""
    import stat
    try:
        return stat.S_ISFIFO(os.stat(path).st_mode)
    except OSError:
        return False
