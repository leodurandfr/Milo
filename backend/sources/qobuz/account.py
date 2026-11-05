# backend/sources/qobuz/account.py
"""The Qobuz account, as qobuz-proxy's own token cache holds it.

`$QOBUZPROXY_DATA_DIR/credentials.json` (pinned under the data dir by
milo-qobuz.service) is written by the sidecar on login and read here. It is the
account's persistent state — the proxy re-authenticates from it on every start
— so it answers the same whether the sidecar is up or down. Read by the account
routes (api/qobuz_account.py) and by the source, whose `no_account`
availability uses the same predicate while the sidecar is not running.
"""
import asyncio
import contextlib
import json
import logging
import os

import aiofiles

from backend.config.constants import MILO_DATA_DIR
from backend.shared.persistence import write_bytes_atomically

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
    """Drop the token keys from the cache, preserving any other proxy state,
    and leave the file 0600 either way. Raises OSError when the cache cannot
    be rewritten.

    A running sidecar, told of the logout first, has usually dropped the keys
    already, rewriting the file in place with its mode kept; so does the next
    login. A cache left 0644 here hands that login's token to every local
    account, so a cache with nothing to drop is still made private — by its
    mode alone, never by a rewrite of a file that is qobuz-proxy's.
    """
    creds = await read_credentials()
    if any(key in creds for key in _TOKEN_KEYS):
        for key in _TOKEN_KEYS:
            creds.pop(key, None)
        await write_bytes_atomically(
            QOBUZ_CREDENTIALS_FILE, json.dumps(creds, indent=2).encode("utf-8"), private=True
        )
        return
    # No file at all: the sidecar creates the next one under its UMask=0077.
    with contextlib.suppress(FileNotFoundError):
        await asyncio.to_thread(os.chmod, QOBUZ_CREDENTIALS_FILE, 0o600)
