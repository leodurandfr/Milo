# backend/sources/qobuz/account.py
"""The Qobuz account, as qobuz-proxy's own token cache holds it.

`$QOBUZPROXY_DATA_DIR/credentials.json` (pinned under the data dir by
milo-qobuz.service) is written by the sidecar on login and read here. It is the
account's persistent state — the proxy re-authenticates from it on every start
— so it answers the same whether the sidecar is up or down. Read by the account
routes (api/qobuz_account.py) and by the source, whose `no_account`
availability uses the same predicate while the sidecar is not running.
"""
import json
import logging
import os

import aiofiles

from backend.config.constants import MILO_DATA_DIR

logger = logging.getLogger("source.qobuz.account")

QOBUZ_CREDENTIALS_FILE = MILO_DATA_DIR / "qobuz" / "credentials.json"
_TOKEN_KEYS = ("user_id", "user_auth_token", "email")


async def read_credentials() -> dict:
    """Return the cached token payload, or {} when absent/unreadable."""
    try:
        async with aiofiles.open(QOBUZ_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            return json.loads(await f.read())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("Qobuz credentials cache unreadable (%s)", e)
        return {}


def is_connected(credentials: dict) -> bool:
    """A cached user id + token is what the sidecar authenticates from."""
    return bool(credentials.get("user_id") and credentials.get("user_auth_token"))


async def clear_credentials() -> None:
    """Drop the token keys from the cache, preserving any other proxy state.
    Raises OSError when the cache cannot be rewritten."""
    creds = await read_credentials()
    if not any(key in creds for key in _TOKEN_KEYS):
        return
    for key in _TOKEN_KEYS:
        creds.pop(key, None)
    tmp = QOBUZ_CREDENTIALS_FILE.with_suffix(".tmp")
    async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
        await f.write(json.dumps(creds, indent=2))
    os.replace(tmp, QOBUZ_CREDENTIALS_FILE)
