# backend/sources/tidal/controller_socket.py
"""Controller client for the Tidal Connect daemon's `tisoc` Unix socket.

The daemon (`tidal_connect_application`, a build of Tidal's proprietary Connect
Device SDK) speaks a length-framed JSON protocol on a Unix socket. Nothing
documents it; the framing and vocabulary below were read off a live session
against a phone. Frame layout:

    FF 02 | uint16 big-endian payload length | JSON | FF 03

Two handshakes are mandatory and are the whole reason this class exists rather
than the source talking to the socket directly:

  - `startService` on connect. Until it arrives the daemon's SessionManager
    sits in STARTING and rejects every incoming phone session, logging
    "Illegal State: STARTING" — and once a session has been rejected the
    manager stays wedged, so only a daemon restart recovers it. Connecting
    promptly is therefore part of the contract, not an optimisation.
  - `grantResources` in answer to the daemon's `requestResources`. Without it
    the session opens and then stalls before a single sample is decoded.

Both look identical from the phone: "the speaker won't connect". They are
answered here so the source only ever sees playback events.
"""
import asyncio
import contextlib
import json
import logging
import struct
from typing import Any, Awaitable, Callable, Dict, Optional

# Frame delimiters. A payload cannot contain FF bytes (it is JSON), so a
# mismatched marker means the stream desynchronised — unrecoverable in place,
# handled by dropping the connection and letting the retry loop rebuild it.
FRAME_START = b"\xff\x02"
FRAME_END = b"\xff\x03"
_LENGTH_BYTES = 2
_HEADER_SIZE = len(FRAME_START) + _LENGTH_BYTES

# uint16 length field — the daemon cannot frame a larger payload, so a JSON
# object bigger than this is a protocol violation rather than a long track name.
MAX_PAYLOAD = 0xFFFF

# Delay between reconnection attempts. The socket only disappears when the
# daemon is restarting, which systemd does in ~1s.
RECONNECT_DELAY = 1.0

# How long one frame may take to reach the daemon. The socket is local and a
# frame is a few hundred bytes, so this only ever fires on a daemon that has
# stopped draining its end — which it does without closing the socket.
SEND_TIMEOUT = 2.0

# How long _do_start waits for the daemon to confirm `startService`. Generous
# relative to the observed answer (immediate) because the whole start runs
# under AudioStateMachine.TRANSITION_TIMEOUT and a slow answer is still better
# than declaring the source up while it would reject the first phone.
READY_TIMEOUT = 5.0

EventCallback = Callable[[Dict[str, Any]], Awaitable[None]]


class TidalControllerSocket:
    """Owns the tisoc connection: framing, the two handshakes, reconnection.

    Every decoded frame is forwarded to `on_event`, including the ones answered
    here — the source uses `notifySessionState` to know a phone attached, and
    seeing `releaseResources` is how it learns the session ended.
    """

    def __init__(
        self,
        socket_path: str,
        on_event: EventCallback,
        logger: Optional[logging.Logger] = None,
    ):
        self._socket_path = socket_path
        self._on_event = on_event
        self._logger = logger or logging.getLogger("source.tidal.controller")

        self._writer: Optional[asyncio.StreamWriter] = None
        self._task: Optional[asyncio.Task] = None
        self._send_lock = asyncio.Lock()
        self._ready = asyncio.Event()
        self._stopping = False

    @property
    def connected(self) -> bool:
        """True while a socket is open and writable."""
        return self._writer is not None and not self._writer.is_closing()

    async def start(self) -> None:
        """Begin connecting; returns immediately, the loop retries in background."""
        await self.stop()
        self._stopping = False
        self._ready.clear()
        self._task = asyncio.create_task(self._connection_loop())

    async def stop(self) -> None:
        """Tear the connection down and stop retrying."""
        self._stopping = True

        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

        await self._close_writer()
        self._ready.clear()

    async def wait_ready(self, timeout: float = READY_TIMEOUT) -> bool:
        """Wait until the daemon acknowledged `startService`.

        That acknowledgement is the only proof the daemon will accept a phone
        session; a source that reports itself started without it would advertise
        a speaker that silently refuses every connection.
        """
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            self._logger.error(
                "Tidal Connect daemon did not acknowledge startService within %.0fs",
                timeout,
            )
            return False

    async def send(self, command: str, **fields: Any) -> bool:
        """Send one command frame. False when the socket is down or write failed.

        Answers False rather than raising, and never waits longer than
        SEND_TIMEOUT: `_do_stop` sends through here while a source switch waits
        on it, and the daemon this talks to has a wedged state in which it stops
        reading its own socket without closing it.
        """
        payload = json.dumps({"command": command, **fields}).encode()
        if len(payload) > MAX_PAYLOAD:
            self._logger.error("Frame for '%s' exceeds the protocol's length field", command)
            return False

        frame = FRAME_START + struct.pack(">H", len(payload)) + payload + FRAME_END

        async with self._send_lock:
            # Read the writer under the lock and use that reference throughout:
            # the connection loop drops self._writer on every disconnection, and
            # re-reading it after an await is how a routine reconnection turns
            # into an AttributeError on None.
            writer = self._writer
            if writer is None or writer.is_closing():
                self._logger.warning("Cannot send '%s': controller socket is down", command)
                return False

            try:
                writer.write(frame)
                await asyncio.wait_for(writer.drain(), timeout=SEND_TIMEOUT)
                return True
            except asyncio.TimeoutError:
                self._logger.warning(
                    "Send of '%s' timed out after %.0fs — daemon stopped reading",
                    command, SEND_TIMEOUT,
                )
                return False
            except (ConnectionError, OSError) as e:
                self._logger.warning("Send of '%s' failed: %s", command, e)
                return False

    # === Connection lifecycle ===

    async def _connection_loop(self) -> None:
        """Reconnect until stopped. Loop body is guarded per background-loop doctrine."""
        while not self._stopping:
            try:
                await self._run_connection()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._logger.error("Controller socket error: %s", e)

            await self._close_writer()
            self._ready.clear()

            if not self._stopping:
                await asyncio.sleep(RECONNECT_DELAY)

    async def _run_connection(self) -> None:
        """One connection: open, hand-shake, then pump frames until it drops."""
        try:
            reader, self._writer = await asyncio.open_unix_connection(self._socket_path)
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            self._logger.debug("Controller socket not available yet: %s", e)
            return

        self._logger.info("Controller socket connected: %s", self._socket_path)

        # Lifts the daemon out of STARTING. Sent on every (re)connection: a
        # daemon that restarted underneath us is back in STARTING too.
        if not await self.send("startService"):
            return

        while not self._stopping:
            try:
                message = await self._read_frame(reader)
            except (asyncio.IncompleteReadError, ConnectionError):
                self._logger.info("Controller socket closed by the daemon")
                return
            except ValueError as e:
                # Desynchronised stream: every subsequent read is garbage.
                self._logger.error("Controller framing error, reconnecting: %s", e)
                return

            await self._dispatch(message)

    async def _read_frame(self, reader: asyncio.StreamReader) -> Dict[str, Any]:
        """Read exactly one frame. Raises ValueError on a framing violation."""
        header = await reader.readexactly(_HEADER_SIZE)
        if not header.startswith(FRAME_START):
            raise ValueError(f"bad start marker: {header[:2]!r}")

        (length,) = struct.unpack(">H", header[len(FRAME_START):])
        payload = await reader.readexactly(length)

        trailer = await reader.readexactly(len(FRAME_END))
        if trailer != FRAME_END:
            raise ValueError(f"bad end marker: {trailer!r}")

        try:
            return json.loads(payload)
        except json.JSONDecodeError as e:
            raise ValueError(f"undecodable payload: {e}") from e

    async def _dispatch(self, message: Dict[str, Any]) -> None:
        """Answer the protocol handshakes, then hand the frame to the source."""
        command = message.get("command")

        if command == "notifyServiceStateChanged":
            self._ready.set()
        elif command == "requestResources":
            # The daemon is asking the controller for the audio device. It
            # decodes nothing until this is granted.
            await self.send("grantResources")
        elif command == "releaseResources":
            await self.send("revokeResources")

        try:
            await self._on_event(message)
        except Exception as e:
            self._logger.error("Event handler failed for '%s': %s", command, e)

    async def _close_writer(self) -> None:
        if not self._writer:
            return
        writer, self._writer = self._writer, None
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
