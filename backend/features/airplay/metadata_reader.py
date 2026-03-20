# backend/features/airplay/metadata_reader.py
"""
Async reader for shairport-sync metadata pipe.

Shairport-sync outputs metadata as XML-like items to a named pipe.
Each item contains a type, code, and optional base64-encoded data.

Item format:
  <item><type>73736e63</type><code>70626567</code><length>0</length></item>

Type/code are hex-encoded 4-char strings (e.g., 73736e63 = "ssnc", 70626567 = "pbeg").

Important codes:
  Core metadata (type "core"):
    - asar: artist
    - minm: title (track name)
    - asal: album name
    - asgn: genre

  Session control (type "ssnc"):
    - pbeg: play begin
    - pend: play end
    - pfls: play flush (pause)
    - prsm: play resume
    - PICT: artwork (data is raw image bytes)
    - pvol: volume info
    - prgr: progress (start/current/end in sample frames at 44100Hz)
    - mdst: metadata start
    - mden: metadata end
    - snua: user agent (device info)
    - snam: client name (X-Apple-Client-Name, e.g. "Mac mini de Léo")
"""
import asyncio
import base64
import logging
import os
import re
from typing import Callable, Dict, Any, Optional

from backend.shared.decorators import handle_errors

logger = logging.getLogger(__name__)

# Hex-decode helper: converts hex string to ASCII
def _hex_to_str(hex_str: str) -> str:
    try:
        return bytes.fromhex(hex_str).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return hex_str


class MetadataReader:
    """Async reader for shairport-sync metadata pipe."""

    def __init__(
        self,
        pipe_path: str,
        on_metadata: Callable[[Dict[str, Any]], Any],
        on_play_state: Callable[[str], Any],
        on_artwork: Callable[[bytes], Any],
        on_progress: Optional[Callable[[int, int, int], Any]] = None,
        on_client_name: Optional[Callable[[str], Any]] = None,
        on_connection: Optional[Callable[[str, Optional[str]], Any]] = None,
    ):
        """
        Args:
            pipe_path: Path to the metadata named pipe
            on_metadata: Callback for metadata updates (dict with title, artist, album)
            on_play_state: Callback for play state changes ("play", "pause", "stop")
            on_artwork: Callback for artwork data (raw image bytes)
            on_progress: Optional callback for progress (start, current, end in frames)
            on_client_name: Optional callback for client name (X-Apple-Client-Name)
            on_connection: Optional callback for AirPlay 2 connection events
                           ("connected"/"disconnected", client_ip)
        """
        self._pipe_path = pipe_path
        self._on_metadata = on_metadata
        self._on_play_state = on_play_state
        self._on_artwork = on_artwork
        self._on_progress = on_progress
        self._on_client_name = on_client_name
        self._on_connection = on_connection
        self._task: Optional[asyncio.Task] = None
        self._running = False

        # Accumulate metadata between mdst/mden boundaries
        self._pending_metadata: Dict[str, str] = {}

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
            try:
                await self._task
            except asyncio.CancelledError:
                pass
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

        # Decode data if present
        raw_data = None
        if data_match and data_length > 0:
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
        elif item_type == "core":
            self._handle_core(code, data)

    async def _handle_ssnc(self, code: str, data: Optional[bytes]) -> None:
        """Handle shairport-sync control codes."""
        if code == "pbeg":
            await self._on_play_state("play")
        elif code == "pend":
            await self._on_play_state("stop")
        elif code == "pfls":
            await self._on_play_state("pause")
        elif code == "prsm":
            await self._on_play_state("play")
        elif code == "PICT" and data:
            await self._on_artwork(data)
        elif code == "prgr" and data:
            await self._handle_progress(data)
        elif code == "mdst":
            self._pending_metadata = {}
        elif code == "mden":
            if self._pending_metadata:
                await self._on_metadata(dict(self._pending_metadata))
        elif code == "snam" and data:
            if self._on_client_name:
                name = data.decode("utf-8", errors="replace")
                await self._on_client_name(name)
        elif code == "conn" and self._on_connection:
            client_ip = data.decode("utf-8", errors="replace") if data else None
            await self._on_connection("connected", client_ip)
        elif code == "disc" and self._on_connection:
            client_ip = data.decode("utf-8", errors="replace") if data else None
            await self._on_connection("disconnected", client_ip)
        elif code == "snua" and data:
            logger.debug(f"AirPlay device: {data.decode('utf-8', errors='replace')}")

    def _handle_core(self, code: str, data: Optional[bytes]) -> None:
        """Handle core metadata codes (track info)."""
        if not data:
            return

        text = data.decode("utf-8", errors="replace")

        if code == "minm":
            self._pending_metadata["title"] = text
        elif code == "asar":
            self._pending_metadata["artist"] = text
        elif code == "asal":
            self._pending_metadata["album"] = text
        elif code == "asgn":
            self._pending_metadata["genre"] = text

    async def _handle_progress(self, data: bytes) -> None:
        """Parse progress data and invoke callback."""
        if not self._on_progress:
            return

        try:
            text = data.decode("utf-8", errors="replace").strip()
            parts = text.split("/")
            if len(parts) == 3:
                start = int(parts[0])
                current = int(parts[1])
                end = int(parts[2])
                await self._on_progress(start, current, end)
        except (ValueError, IndexError):
            pass
