# backend/api/qobuz_account.py
"""Backend relay for the Qobuz account (login/status/logout).

Unlike Spotify Connect (zeroconf, no login), qobuz-proxy requires a one-time
Qobuz account login before it advertises "Milō" in the Qobuz app. The login is a
browser OAuth flow served by qobuz-proxy itself on :8689; the callback lands back
on qobuz-proxy, which caches the token and starts the speaker.

This router is a thin relay so the frontend never talks to :8689 directly
(cross-origin): it reads the login status from qobuz-proxy's GET /api/status,
hands out the OAuth login URL for the browser to open, and relays logout. All
reads fail open — an unreachable proxy is reported as "not authenticated" rather
than a 500. No Milo-Mac coupling (Qobuz is Milō-only).
"""
import asyncio
import logging
from urllib.parse import urlencode

import aiohttp
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

# qobuz-proxy's local HTTP API. Reads/logout use loopback; the browser-facing
# login URL is built from the request host so it resolves from the same device.
QOBUZ_PROXY_INTERNAL = "http://127.0.0.1:8689"
QOBUZ_PROXY_PORT = 8689


def create_qobuz_account_router(systemd_manager) -> APIRouter:
    """Create the Qobuz account relay router."""
    router = APIRouter(prefix="/api/qobuz/account", tags=["qobuz"])

    @router.get("")
    async def get_account():
        """Return Qobuz login status read from qobuz-proxy's /api/status → auth.

        Fail-open: an unreachable/erroring proxy is reported as not
        authenticated (HTTP 200) so the settings screen degrades gracefully.
        """
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=3.0)
            ) as session:
                async with session.get(f"{QOBUZ_PROXY_INTERNAL}/api/status") as resp:
                    if resp.status != 200:
                        logger.warning("Qobuz /api/status -> HTTP %s", resp.status)
                        return {"status": "success", "data": {"authenticated": False}}
                    payload = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.warning("Qobuz account status: proxy unreachable (%s)", e)
            return {"status": "success", "data": {"authenticated": False}}

        auth = payload.get("auth") or {}
        return {
            "status": "success",
            "data": {
                "authenticated": bool(auth.get("authenticated")),
                "name": auth.get("name"),
                "email": auth.get("email"),
                "avatar": auth.get("avatar"),
            },
        }

    @router.get("/login-url")
    async def get_login_url(request: Request):
        """Return the qobuz-proxy OAuth login URL for the browser to open.

        The URL points at qobuz-proxy (:8689) on the same host the client used
        to reach Milō, so the flow — and its /auth/callback, which exchanges the
        code and starts the speaker — stays on the proxy. `origin` must point
        back at qobuz-proxy for the callback to land there.

        milo-qobuz.service isn't necessarily running (it only starts when Qobuz
        becomes the active source), so ensure it's up first — a no-op if it's
        already active — or the browser hits connection-refused on :8689.
        """
        if not await systemd_manager.start("milo-qobuz.service"):
            logger.error("Could not start milo-qobuz.service for login-url")
            raise HTTPException(status_code=503, detail="qobuz-proxy could not be started")

        host = request.url.hostname or "milo.local"
        base = f"http://{host}:{QOBUZ_PROXY_PORT}"
        login_url = f"{base}/auth/login?{urlencode({'origin': base})}"
        return {"status": "success", "data": {"login_url": login_url}}

    @router.post("/logout")
    async def logout():
        """Relay POST /api/auth/logout to qobuz-proxy (clears token, stops speaker).

        Fail-open on an unreachable proxy: with the proxy down there is no live
        session to tear down, so report success. A reachable proxy returning a
        non-2xx is surfaced as a 502.
        """
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

        return {"status": "success"}

    return router
