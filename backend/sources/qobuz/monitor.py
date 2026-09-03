# backend/sources/qobuz/monitor.py
"""Polling monitor for the qobuz-proxy local HTTP API.

qobuz-proxy exposes no local control channel and no push/WebSocket — its own web
UI just polls GET /api/status every few seconds. This monitor mirrors that: it
polls /api/status ~1s, extracts our speaker and the account's login state, and
hands both to a single async callback. The source turns that into playback
state. Playback control belongs to the Qobuz app (Family B), so there is nothing
to send back — only status to read.

The speaker is matched by its ALSA output device, NOT the slugified id:
qobuz-proxy hard-couples id = slugify(name), and the display name "Milō"
slugifies to "mil" (non-ASCII dropped). Matching on audio_device keeps the
pretty display name.
"""
import asyncio
import contextlib
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

import aiohttp

logger = logging.getLogger("source.qobuz.monitor")


class QobuzMonitor:
    """Async poll loop over qobuz-proxy's GET /api/status."""

    def __init__(
        self,
        status_url: str,
        audio_device: str,
        on_status: Callable[[Optional[Dict[str, Any]], bool], Awaitable[None]],
        poll_interval: float = 1.0,
    ):
        self._status_url = status_url
        self._audio_device = audio_device
        self._on_status = on_status
        self._poll_interval = poll_interval

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        """Open the HTTP session and spawn the poll loop."""
        if self._task:
            return
        self._running = True
        # Bounded per-request timeout so an unresponsive proxy can't wedge the
        # loop; a slow tick just retries next interval.
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=3.0)
        )
        self._task = asyncio.create_task(self._loop())
        logger.info("QobuzMonitor started (%s)", self._status_url)

    async def stop(self) -> None:
        """Cancel the poll loop and close the HTTP session."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("QobuzMonitor stopped")

    async def _loop(self) -> None:
        """Poll /api/status until stopped, dispatching each speaker snapshot.

        Background-loop doctrine: the body is wrapped so a transient poll error
        (proxy starting, network blip) is logged and skipped — fail open, keep
        the last state, retry next tick — instead of killing the task.
        """
        while self._running:
            try:
                status = await self._fetch_status()
                if status is not None:
                    await self._on_status(*status)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Qobuz status poll failed: %s", e)
            await asyncio.sleep(self._poll_interval)

    async def _fetch_status(
        self,
    ) -> Optional[tuple[Optional[Dict[str, Any]], bool]]:
        """Return (our speaker dict | None, account authenticated), or None.

        None is "the sidecar did not answer", and the tick is skipped on it
        exactly as the loop above skips a tick that raised. The two are one
        failure — a status that could not be read — and only the transport
        tells them apart: a sidecar that is down refuses the connection and
        raises, one that is up and broken answers 5xx. Answering an absent
        speaker instead, the second one read as "no session": three ticks of
        the source's idle grace and the full-screen player was replaced by the
        idle card, over audio that was still playing. It also flashed the
        "connect your account" CTA at someone who is logged in, once per
        hiccup, at ~1 Hz.

        The speaker inside the tuple is still optional, and means what it says:
        the status was read and names no speaker of ours.
        """
        async with self._session.get(self._status_url) as resp:
            if resp.status != 200:
                logger.warning("Qobuz /api/status -> HTTP %s", resp.status)
                return None
            payload = await resp.json()

        authenticated = bool((payload.get("auth") or {}).get("authenticated"))
        speakers = payload.get("speakers") or []
        for speaker in speakers:
            config = speaker.get("config") or {}
            if config.get("audio_device") == self._audio_device:
                return speaker, authenticated
        # Single-speaker proxy: fall back to the first speaker if present.
        return (speakers[0] if speakers else None), authenticated
