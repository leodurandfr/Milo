# backend/sources/airplay/metadata_reader.py
"""
Async reader for shairport-sync metadata pipe: the session's events.

Shairport-sync outputs metadata as XML-like items to a named pipe.
Each item contains a type, code, and optional base64-encoded data.

Item format:
  <item><type>73736e63</type><code>70626567</code><length>0</length></item>

Type/code are hex-encoded 4-char strings (e.g., 73736e63 = "ssnc", 70626567 = "pbeg").

What the sender is playing does not come through here any more. Since 5.6 an
AirPlay 2 sender sends it as plists (`ssnc`/`copl`, raw), and shairport-sync
publishes them decoded on D-Bus, which is where Milō reads them (remote.py);
the `core` tags, `PICT` and `prgr` this reader used to parse are no longer
sent at all. The pipe still carries the session itself, which D-Bus does not
(no sender name, no goodbye):

  Session control (type "ssnc"), what shairport-sync 5.5.1 was measured to
  send (2026-09-23; docs: source architecture, phase 3a) and 5.6 still sends:
    - conn / disc: a sender connected / left (data: its IP). In 5.6 `conn`
      comes with the second SETUP (the one carrying `streams`), so a player
      that only checks the receiver is there no longer sends it
    - snam: client name (e.g. "Mac mini de Léo")
    - pbeg / pend: a stream began / ended — a paused iPhone ends its stream
      after 29-184 s and stays connected, so `pend` is not a goodbye
    - paus / pres: a Buffered stream (iPhone Music) paused / resumed; a skip or
      a seek is `paus` then `pres` 160 ms later. A Realtime stream (a Mac's
      system audio, Spotify) never sends them.
    - pffr: the stream's first frame arrived; `styp` ("Buffered", "Realtime")
      follows it in the same millisecond
  `pfls` (a flush) is not a pause and is not read: it never appeared in the
  measurements, and treating it as one armed the idle timeout under a sender
  that was playing (E54).
"""
import asyncio
import contextlib
import base64
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from backend.shared.decorators import handle_errors

logger = logging.getLogger("source.airplay.metadata")

def _hex_to_str(hex_str: str) -> str:
    try:
        return bytes.fromhex(hex_str).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return hex_str


@dataclass(frozen=True)
class PipeEvent:
    """One thing shairport-sync announced. `value` depends on `kind`:
    conn/disc: the IP (or None); client_name, stream_type: text; the stream
    events carry nothing."""
    kind: str
    value: Any = None


# ssnc codes that are a state change with no payload, by the event they are.
_STATE_CODES = {
    "pbeg": "stream_begin",
    "pend": "stream_end",
    "paus": "paused",
    "pres": "resumed",
    "pffr": "first_frame",
}


# ssnc codes whose payload is text.
_TEXT_CODES = frozenset({"conn", "disc", "snam", "styp", "snua"})


class MetadataReader:
    """Async reader for shairport-sync metadata pipe."""

    def __init__(self, pipe_path: str, on_event: Callable[[PipeEvent], Any]):
        """
        Args:
            pipe_path: Path to the metadata named pipe
            on_event: Called with every PipeEvent, in pipe order
        """
        self._pipe_path = pipe_path
        self._on_event = on_event
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start reading metadata pipe."""
        self._running = True
        self._task = asyncio.create_task(self._read_loop())
        logger.info(f"MetadataReader started on {self._pipe_path}")

    async def stop(self) -> None:
        """Stop reading."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("MetadataReader stopped")

    async def _read_loop(self) -> None:
        """Main loop: open pipe and read items continuously."""
        while self._running:
            try:
                # Open pipe (blocks until writer connects)
                fd = os.open(self._pipe_path, os.O_RDONLY | os.O_NONBLOCK)
                reader = asyncio.StreamReader()
                transport, _ = await asyncio.get_running_loop().connect_read_pipe(
                    lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(fd, "rb")
                )

                try:
                    buffer = b""
                    while self._running:
                        data = await reader.read(65536)
                        if not data:
                            # Pipe closed by writer, reopen
                            break

                        buffer += data
                        buffer = await self._process_buffer(buffer)
                finally:
                    transport.close()

            except asyncio.CancelledError:
                raise
            except FileNotFoundError:
                logger.info(f"Metadata pipe not found: {self._pipe_path}, retrying...")
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"MetadataReader error: {e}")
                await asyncio.sleep(1)

    async def _process_buffer(self, buffer: bytes) -> bytes:
        """Extract complete XML items from buffer and process them."""
        text = buffer.decode("utf-8", errors="replace")

        # Match complete <item>...</item> blocks
        pattern = re.compile(r"<item>(.*?)</item>", re.DOTALL)

        last_end = 0
        for match in pattern.finditer(text):
            last_end = match.end()
            await self._parse_item(match.group(1))

        # Return unprocessed remainder
        if last_end > 0:
            return text[last_end:].encode("utf-8", errors="replace")
        return buffer

    async def _parse_item(self, item_xml: str) -> None:
        """Parse a single metadata item and dispatch to handlers."""
        type_match = re.search(r"<type>([0-9a-fA-F]+)</type>", item_xml)
        code_match = re.search(r"<code>([0-9a-fA-F]+)</code>", item_xml)
        data_match = re.search(r"<data encoding=\"base64\">(.*?)</data>", item_xml, re.DOTALL)
        length_match = re.search(r"<length>(\d+)</length>", item_xml)

        if not type_match or not code_match:
            return

        item_type = _hex_to_str(type_match.group(1))
        code = _hex_to_str(code_match.group(1))
        data_length = int(length_match.group(1)) if length_match else 0

        # Decode data only where it is read: a `copl` can carry a picture.
        raw_data = None
        if data_match and data_length > 0 and code in _TEXT_CODES:
            try:
                raw_data = base64.b64decode(data_match.group(1).strip())
            except Exception:
                raw_data = None

        await self._handle_item(item_type, code, raw_data)

    @handle_errors(default=None)
    async def _handle_item(self, item_type: str, code: str, data: Optional[bytes]) -> None:
        """Route parsed item to appropriate handler."""
        if item_type == "ssnc":
            await self._handle_ssnc(code, data)

    async def _handle_ssnc(self, code: str, data: Optional[bytes]) -> None:
        """Handle shairport-sync control codes."""
        # Text only where it is text: a `copl` is a binary plist.
        text = data.decode("utf-8", errors="replace") if data and code in _TEXT_CODES else None
        if code in _STATE_CODES:
            await self._on_event(PipeEvent(_STATE_CODES[code]))
        elif code in ("conn", "disc"):
            await self._on_event(PipeEvent(code, text))
        elif code == "snam" and text:
            await self._on_event(PipeEvent("client_name", text))
        elif code == "styp" and text:
            await self._on_event(PipeEvent("stream_type", text))
        elif code == "snua" and text:
            logger.debug(f"AirPlay device: {text}")
