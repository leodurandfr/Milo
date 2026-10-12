# backend/sources/spotify/jam.py
"""
The Spotify Jam of the signed-in account, followed from Spotify itself, so the
source bar can name who is in it.

go-librespot plays a Jam (since 0.10.2) but says nothing of it: no event,
nothing in /status, and `context_uri` stays the playlist the Jam started from.
Spotify's social-connect service says who is in it. Measured 2026-10-11
(go-librespot 0.10.3, two accounts, the iOS and macOS apps):

- GET social-connect/v2/sessions/current with the daemon's token answers in
  ~45 ms: `session_members` (`id`, `display_name`, `joined_timestamp`),
  `session_owner_id`, `active`, and `hostActiveDeviceId`, go-librespot's device
  id when the Jam plays on Milō. 404 with no Jam. After a Jam ends Spotify keeps
  a dormant session: 200, `active` absent. Asked every 2 s it answered 429 twice
  in 2.5 min, so it is read once per connection and never polled.
- A dealer socket opened with the same token, no device registered on it, is
  pushed `social-connect/v2/session_update`, a JSON payload carrying the whole
  session and a reason (NEW_SESSION, SESSION_ACTIVATED, USER_JOINED, USER_LEFT,
  SESSION_DELETED…), as members join and leave, ~5 s before the read above
  changes. One stayed up for 2 h on its token, past the token's expiry. The
  dealer answers each `{"type":"ping"}` with a pong; go-librespot's own dealer
  client gives up on a socket whose pong is 40 s late.
- The members come in the order of whoever asks; the source sorts them.
- A Jam can outlive its device: one stayed active, hosted on Milō, while Milō
  played another account (and was ended later). The source shows only the
  signed-in account's.
"""
import asyncio
import contextlib
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

import aiohttp

from backend.shared.network import describe_network_error
from backend.sources.spotify.library import SpotifyLibraryError, SpotifyUnavailable

logger = logging.getLogger("source.spotify.jam")

JamSession = Optional[Dict[str, Any]]

SESSION_UPDATE = "social-connect/v2/session_update"


def session_update(frame: Any) -> Tuple[bool, JamSession]:
    """Whether a dealer frame tells of the Jam, and the session it carries
    (None: the Jam ended). A Jam update in any other form than the measured
    one raises ValueError: it is skipped with a line, never mistaken for an
    end."""
    if not isinstance(frame, dict):
        return False, None
    if frame.get("type") != "message" or frame.get("uri") != SESSION_UPDATE:
        return False, None
    payloads = frame.get("payloads")
    payload = payloads[0] if isinstance(payloads, list) and payloads else None
    if not isinstance(payload, dict):
        raise ValueError("a Jam update without a JSON payload")
    if payload.get("reason") == "SESSION_DELETED":
        return True, None
    session = payload.get("session")
    if not isinstance(session, dict):
        raise ValueError("a Jam update without a session")
    return True, session


def _reason(error: BaseException) -> str:
    """What went wrong, safe to log: an aiohttp error's text names the URL it
    failed on, and the dealer's carries the access token."""
    if isinstance(error, aiohttp.ClientResponseError):
        return f"{type(error).__name__} {error.status}"
    if isinstance(error, aiohttp.ClientError):
        return type(error).__name__
    return describe_network_error(error)


class SpotifyJamFeed:
    """One dealer socket for one account: a read of its Jam each time the
    socket opens, then every update Spotify pushes.

    It reconnects on its own, so it is no dead feed and reports nothing above
    debug (CLAUDE.md). A socket counts as standing once a ping is answered:
    one closed before that, like any failure, is retried with the backoff,
    never every 2 s (each retry reads the Jam again, and that read is what
    Spotify rate-limits). One whose ping goes unanswered or cannot be sent is
    closed: a half-open socket would otherwise freeze the Jam for TCP's ~15 min.
    """

    APRESOLVE_URL = "https://apresolve.spotify.com/?type=dealer"
    CONNECT_TIMEOUT_S = 10
    # How long closing waits for the dealer's answer: a socket that died says
    # nothing, and the source waits on the close when it stops the feed.
    CLOSE_TIMEOUT_S = 1.0
    # What the Spotify clients send to keep the socket open; each must be
    # answered before the next.
    PING_S = 30.0
    RETRY_MIN_S = 2.0
    RETRY_MAX_S = 60.0

    def __init__(
        self,
        http: aiohttp.ClientSession,
        token: Callable[[], Awaitable[str]],
        read_session: Callable[[str], Awaitable[JamSession]],
        on_session: Callable[["SpotifyJamFeed", JamSession], None],
    ) -> None:
        self._http = http
        self._token = token
        self._read_session = read_session
        self._on_session = on_session
        self._task: Optional[asyncio.Task] = None
        self._pinger: Optional[asyncio.Task] = None
        # The socket open now had a ping answered, and one is outstanding.
        self._stable = False
        self._awaiting_pong = False

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        delay = self.RETRY_MIN_S
        while True:
            try:
                stood = await self._follow()
            except asyncio.CancelledError:
                raise
            except SpotifyUnavailable:
                # go-librespot is down or between accounts: the source tells it.
                stood = False
            except Exception as e:
                stood = self._stable
                logger.debug(f"Spotify Jam socket failed: {_reason(e)}")
            delay = self.RETRY_MIN_S if stood else min(delay * 2, self.RETRY_MAX_S)
            await asyncio.sleep(delay)

    async def _follow(self) -> bool:
        """One socket, until it closes; whether it stood."""
        self._stable = self._awaiting_pong = False
        token = await self._token()
        async with contextlib.AsyncExitStack() as stack:
            async with asyncio.timeout(self.CONNECT_TIMEOUT_S):
                async with self._http.get(self.APRESOLVE_URL) as resp:
                    hosts = (await resp.json(content_type=None) or {}).get("dealer") or []
                if not hosts:
                    raise RuntimeError("no dealer resolved")
                # The token is in the URL: never logged.
                ws = await stack.enter_async_context(self._http.ws_connect(
                    f"wss://{hosts[0]}/?access_token={token}",
                    timeout=aiohttp.ClientWSTimeout(ws_close=self.CLOSE_TIMEOUT_S),
                ))
                # Read after the socket opened, so no update falls in between.
                # A read refused (a 429) keeps the socket, whose pushes still
                # come, and shows no Jam rather than one that may have ended.
                try:
                    session = await self._read_session(token)
                except (SpotifyUnavailable, SpotifyLibraryError) as e:
                    logger.debug(f"Spotify Jam not read at connection: {e}")
                    session = None
                self._on_session(self, session)
            self._pinger = asyncio.create_task(self._ping(ws))
            try:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        # Any frame after a ping answers it.
                        if self._awaiting_pong:
                            self._awaiting_pong = False
                            self._stable = True
                        try:
                            matched, session = session_update(json.loads(msg.data))
                        except ValueError as e:
                            # One frame the parse cannot read costs that frame.
                            logger.info(f"Spotify Jam update skipped: {e}")
                            continue
                        if matched:
                            self._on_session(self, session)
                    elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break
            finally:
                pinger, self._pinger = self._pinger, None
                pinger.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pinger
        return self._stable

    async def _ping(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while True:
            await asyncio.sleep(self.PING_S)
            try:
                if self._awaiting_pong:
                    raise ConnectionError("ping unanswered")
                self._awaiting_pong = True
                await ws.send_str('{"type":"ping"}')
            except (ConnectionError, aiohttp.ClientError, OSError) as e:
                # The socket is gone, whatever TCP says: closing it ends the read.
                logger.debug(f"Spotify Jam socket closed: {_reason(e)}")
                with contextlib.suppress(aiohttp.ClientError, OSError):
                    await ws.close()
                return
