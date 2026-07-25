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
(`$QOBUZPROXY_DATA_DIR/credentials.json`), NOT from its HTTP API: the sidecar
only runs while Qobuz is the active source, so an API read would report "not
connected" from the settings screen every other time. The cache is the account's
persistent state — the proxy re-authenticates from it on every start — so it
answers the same whether the sidecar is up or down.

No Milo-Mac coupling (Qobuz is Milō-only).
"""
import asyncio
import json
import logging
import os
from urllib.parse import urlencode

import aiofiles
import aiohttp
from fastapi import APIRouter, HTTPException, Request

from backend.config.constants import MILO_DATA_DIR

logger = logging.getLogger(__name__)

# qobuz-proxy's local HTTP API. Logout uses loopback; the browser-facing login
# URL is built from the request host so it resolves from the same device.
QOBUZ_PROXY_INTERNAL = "http://127.0.0.1:8689"
QOBUZ_PROXY_PORT = 8689
QOBUZ_SERVICE = "milo-qobuz.service"

# qobuz-proxy's OAuth token cache, pinned under the D3 data dir by
# milo-qobuz.service (QOBUZPROXY_DATA_DIR). Written by the sidecar on login,
# read here for the account status. Keys: user_id, user_auth_token, email.
QOBUZ_CREDENTIALS_FILE = MILO_DATA_DIR / "qobuz" / "credentials.json"
_TOKEN_KEYS = ("user_id", "user_auth_token", "email")


async def _read_credentials() -> dict:
    """Return the cached token payload, or {} when absent/unreadable."""
    try:
        async with aiofiles.open(QOBUZ_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            return json.loads(await f.read())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("Qobuz credentials cache unreadable (%s)", e)
        return {}


async def _clear_credentials() -> None:
    """Drop the token keys from the cache, preserving any other proxy state."""
    creds = await _read_credentials()
    if not any(key in creds for key in _TOKEN_KEYS):
        return
    for key in _TOKEN_KEYS:
        creds.pop(key, None)
    tmp = QOBUZ_CREDENTIALS_FILE.with_suffix(".tmp")
    try:
        async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
            await f.write(json.dumps(creds, indent=2))
        os.replace(tmp, QOBUZ_CREDENTIALS_FILE)
    except OSError as e:
        logger.error("Could not clear the Qobuz credentials cache: %s", e)
        raise HTTPException(status_code=500, detail="Could not clear the Qobuz token")


def create_qobuz_account_router(systemd_manager) -> APIRouter:
    """Create the Qobuz account relay router."""
    router = APIRouter(prefix="/api/qobuz/account", tags=["qobuz"])

    @router.get("")
    async def get_account():
        """Return Qobuz login status from the sidecar's cached token.

        A cached user_id + token is what the sidecar auto-authenticates from at
        start, so its presence is the account being connected — readable whether
        or not milo-qobuz.service is currently running. Live token validity is
        reported separately by the source itself (account_authenticated in the
        broadcast metadata) while Qobuz is the active source.
        """
        creds = await _read_credentials()
        return {
            "status": "success",
            "data": {
                "authenticated": bool(creds.get("user_id") and creds.get("user_auth_token")),
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

        milo-qobuz.service isn't necessarily running (it only starts when Qobuz
        becomes the active source), so ensure it's up first — a no-op if it's
        already active — or the browser hits connection-refused on :8689.
        """
        if not await systemd_manager.start(QOBUZ_SERVICE):
            logger.error("Could not start milo-qobuz.service for login-url")
            raise HTTPException(status_code=503, detail="qobuz-proxy could not be started")

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

        await _clear_credentials()
        return {"status": "success"}

    return router
