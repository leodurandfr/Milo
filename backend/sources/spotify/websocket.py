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
                self._logger.error(f"WebSocket error: {e}")

            if not self._stopping:
                # Wait before reconnecting
                await asyncio.sleep(2.0)

    async def _run_connection(self) -> None:
        """Run a single WebSocket connection."""
        try:
            async with self._session.ws_connect(self._ws_url, timeout=aiohttp.ClientTimeout(total=5)) as ws:
                self._connected = True
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

        except aiohttp.ClientConnectorError as e:
            self._logger.warning(f"Connection failed: {e}")
        except Exception as e:
            self._logger.error(f"WebSocket error: {e}")
        finally:
            self._connected = False
