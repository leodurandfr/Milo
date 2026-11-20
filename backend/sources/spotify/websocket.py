# backend/sources/spotify/websocket.py
"""
WebSocket client for go-librespot events.

Manages WebSocket connection to go-librespot for real-time
event notifications (playback state, metadata updates, etc).
"""
import asyncio
import contextlib
import json
import logging
from typing import Callable, Awaitable, Optional

import aiohttp


# Event callback type
EventCallback = Callable[[dict], Awaitable[None]]
# Called on every (re)connection so the source can reconcile state with the daemon
OnConnectCallback = Callable[[], Awaitable[None]]


class LibrespotWebSocket:
    """
    WebSocket client for go-librespot.

    Connects to go-librespot WebSocket endpoint and dispatches
    events to registered callback.
    """

    # go-librespot can be up but wedged after a restart; the reconnect loop must
    # not park on its handshake, whatever timeout the session was built with.
    CONNECT_TIMEOUT_S = 5

    # The pause between two connections: no backoff, so /events is back within
    # this long of go-librespot answering again.
    RECONNECT_DELAY_S = 2.0

    def __init__(
        self,
        ws_url: str,
        session: aiohttp.ClientSession,
        on_event: EventCallback,
        on_connect: Optional[OnConnectCallback] = None
    ):
        """
        Initialize WebSocket client.

        Args:
            ws_url: WebSocket URL (e.g., ws://localhost:3678/events)
            session: aiohttp ClientSession for connections
            on_event: Callback for received events
            on_connect: Optional callback fired on every (re)connection so the
                source can reconcile its state with the daemon. go-librespot
                emits events only on change, so an idle daemon (e.g. after a
                crash + systemd restart) sends nothing on its own.
        """
        self._logger = logging.getLogger("source.spotify.websocket")
        self._ws_url = ws_url
        self._session = session
        self._on_event = on_event
        self._on_connect = on_connect
        self._task: Optional[asyncio.Task] = None
        self._connected = False
        self._stopping = False
        # An outage is said once per level: the first failed connection after
        # one that opened (or after the start), at info, and the first failure
        # nothing expected, at error. go-librespot is Restart=always, and a
        # daemon that exits at every start used to cost a WARNING every 2 s —
        # 1,800 lines an hour in the journal and in errors.log, which the
        # diagnostic report reads and which rotated every ~7 h down to that one
        # line. "WebSocket connected" ends it.
        self._outage_levels_told: set = set()

    @property
    def connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected

    async def start(self) -> None:
        """Start WebSocket connection."""
        await self.stop()  # Clean up any existing connection

        self._stopping = False
        self._task = asyncio.create_task(self._connection_loop())
        self._logger.info(f"WebSocket starting: {self._ws_url}")

    async def stop(self) -> None:
        """Stop WebSocket connection."""
        self._stopping = True

        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        self._connected = False
        self._logger.info("WebSocket stopped")

    async def _connection_loop(self) -> None:
        """Main connection loop with reconnection."""
        while not self._stopping:
            try:
                await self._run_connection()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._connection_failed(f"WebSocket error: {e}", logging.ERROR)

            if not self._stopping:
                # Wait before reconnecting
                await asyncio.sleep(self.RECONNECT_DELAY_S)

    def _connection_failed(self, reason: str, level: int = logging.INFO) -> None:
        """One line per outage and level, then debug until a connection opens.

        A socket that reconnects is not a dead feed and raises no alarm (as
        Tidal's controller socket): the source's start already reports a daemon
        that never answered, on screen, and systemd's journal carries a daemon
        that keeps exiting. A failure nothing expected (`level=ERROR`: a bug,
        not a daemon down) is said at error, once per outage too — at a retry
        every 2 s, a line per retry is the same flood whatever its cause — and
        apart from the daemon's absence: an outage already told at info still
        reaches errors.log with the first bug, and a bug told first does not
        hide a daemon that then stops answering.
        """
        if level in self._outage_levels_told:
            self._logger.debug(f"go-librespot /events still unreachable: {reason}")
            return
        self._outage_levels_told.add(level)
        self._logger.log(
            level,
            f"go-librespot /events unreachable, retrying every {self.RECONNECT_DELAY_S:.0f} s "
            f"until it answers: {reason}"
        )

    async def _run_connection(self) -> None:
        """Run a single WebSocket connection."""
        try:
            async with contextlib.AsyncExitStack() as stack:
                try:
                    async with asyncio.timeout(self.CONNECT_TIMEOUT_S):
                        ws = await stack.enter_async_context(self._session.ws_connect(self._ws_url))
                except TimeoutError:
                    self._connection_failed("go-librespot did not answer the WebSocket handshake")
                    return
                self._connected = True
                self._outage_levels_told.clear()
                self._logger.info("WebSocket connected")

                if self._on_connect:
                    try:
                        await self._on_connect()
                    except Exception as e:
                        self._logger.error(f"on_connect reconcile failed: {e}", exc_info=True)

                async for msg in ws:
                    if self._stopping:
                        break

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            event = json.loads(msg.data)
                            await self._on_event(event)
                        except Exception as e:
                            self._logger.error(f"Event processing error: {e}")

                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        self._logger.error(f"WebSocket error: {ws.exception()}")
                        break

                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        break

        except aiohttp.ClientError as e:
            # Refused, or answered with something other than the upgrade (a
            # daemon still starting): the daemon is not there yet.
            self._connection_failed(f"Connection failed: {e}")
        except Exception as e:
            self._connection_failed(f"WebSocket error: {e}", logging.ERROR)
        finally:
            self._connected = False
