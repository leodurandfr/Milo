# backend/api/qobuz_account.py
"""Backend relay for the Qobuz account (login/status/logout).

Unlike Spotify Connect (zeroconf, no login), qobuz-proxy requires a one-time
Qobuz account login before it advertises "Milō" in the Qobuz app. The login is a
browser OAuth flow served by qobuz-proxy itself on :8689; the callback lands back
on qobuz-proxy, which caches the token and starts the speaker.

This router lets the frontend drive that account without ever talking to :8689
directly (cross-origin): it hands out the OAuth login URL for the browser to
open, and relays logout.

Login status is read from qobuz-proxy's own token cache
(sources/qobuz/account.py), NOT from its HTTP API: the sidecar only runs while
Qobuz is the active source, so an API read would report "not connected" from the
settings screen every other time.

No Milo-Mac coupling (Qobuz is Milō-only).
"""
import asyncio
import logging
from urllib.parse import urlencode

import aiohttp
from typing import TYPE_CHECKING, Awaitable, Callable
from fastapi import APIRouter, HTTPException, Request

from backend.sources.qobuz.account import clear_credentials, is_connected, read_credentials

if TYPE_CHECKING:
    from backend.core.systemd import SystemdServiceManager


logger = logging.getLogger(__name__)

# qobuz-proxy's local HTTP API. Logout uses loopback; the browser-facing login
# URL is built from the request host so it resolves from the same device.
QOBUZ_PROXY_INTERNAL = "http://127.0.0.1:8689"
QOBUZ_PROXY_PORT = 8689
QOBUZ_SERVICE = "milo-qobuz.service"

def create_qobuz_account_router(
    systemd_manager: "SystemdServiceManager",
    on_account_changed: Callable[[], Awaitable[None]],
) -> APIRouter:
    """Create the Qobuz account relay router. `on_account_changed` is the
    Qobuz source's: a logout moves its `no_account` availability."""
    router = APIRouter(prefix="/api/qobuz/account", tags=["qobuz"])

    @router.get("")
    async def get_account():
        """Return Qobuz login status from the sidecar's cached token.

        A cached user_id + token is what the sidecar auto-authenticates from at
        start, so its presence is the account being connected — readable whether
        or not milo-qobuz.service is currently running. Live token validity is
        reported by the source itself (`availability.qobuz` in the state) while
        Qobuz is the active source.
        """
        creds = await read_credentials()
        return {
            "status": "success",
            "data": {
                "authenticated": is_connected(creds),
                "email": creds.get("email") or None,
            },
        }

    @router.get("/login-url")
    async def get_login_url(request: Request):
        """Return the qobuz-proxy OAuth login URL for the browser to open.

        The URL points at qobuz-proxy (:8689) on the same host the client used
        to reach Milō, so the flow — and its /auth/callback, which exchanges the
        code and starts the speaker — stays on the proxy. `origin` must point
        back at qobuz-proxy for the callback to land there.

        Requires Qobuz to already be the active source, because that is what
        runs the sidecar the browser is about to reach on :8689. This route used
        to start the unit itself, which left a Qobuz Connect speaker named after
        the house advertising for as long as the backend ran — raised from a
        settings screen, outside the state machine, with nothing watching it and
        nothing to stop it but the lifespan's lingering-unit sweep at the next
        boot. Selecting the source is the one gesture that starts a daemon here,
        and an unauthenticated Qobuz source is a supported state: it sits idle
        and its poll already broadcasts the login landing.

        Reading the account and logging out stay available from any source —
        both go through the credentials cache, not the sidecar.
        """
        # probe_active, not is_active: this acts on the answer rather than
        # rendering it, and an unreadable probe must not be reported as a fact
        # the backend established. Both unknown and down refuse — handing out a
        # URL for a proxy that may be down only buys a connection-refused.
        if await systemd_manager.probe_active(QOBUZ_SERVICE) is not True:
            logger.warning("Refusing login-url: qobuz-proxy is not confirmed running")
            raise HTTPException(
                status_code=409,
                detail="Select the Qobuz source before connecting the account",
            )

        host = request.url.hostname or "milo.local"
        base = f"http://{host}:{QOBUZ_PROXY_PORT}"
        login_url = f"{base}/auth/login?{urlencode({'origin': base})}"
        return {"status": "success", "data": {"login_url": login_url}}

    @router.post("/logout")
    async def logout():
        """Disconnect the account: stop the live session, then clear the token.

        The relay to qobuz-proxy is what tears down the running speaker and drops
        its in-memory token — only meaningful while the sidecar runs, which is
        only while Qobuz is the active source. Clearing the cache is what makes
        the logout stick, and is done in both cases (the sidecar clears the same
        keys on its side; rewriting them out is idempotent). A reachable proxy
        returning a non-2xx is surfaced as a 502.
        """
        if await systemd_manager.is_active(QOBUZ_SERVICE):
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=5.0)
                ) as session:
                    async with session.post(
                        f"{QOBUZ_PROXY_INTERNAL}/api/auth/logout"
                    ) as resp:
                        if resp.status not in (200, 204):
                            logger.error("Qobuz logout -> HTTP %s", resp.status)
                            raise HTTPException(
                                status_code=502,
                                detail=f"qobuz-proxy logout failed (HTTP {resp.status})",
                            )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning("Qobuz logout: proxy unreachable (%s)", e)

        try:
            await clear_credentials()
        except OSError as e:
            logger.error("Could not clear the Qobuz credentials cache: %s", e)
            raise HTTPException(status_code=500, detail="Could not clear the Qobuz token")
        await on_account_changed()
        return {"status": "success"}

    return router
