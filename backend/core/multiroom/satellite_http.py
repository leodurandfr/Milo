# backend/core/multiroom/satellite_http.py
"""The server's secret on the requests that reach root on a satellite.

`milo-client` is root by construction (CLAUDE.md, invariant 1): `POST /app/update`
installs what it is handed as root, sudoers included, and the SSH switch with
the `milo` password is a login with sudo. Until this module any host on the LAN
could send either. Owner decision, 2026-11-13: a satellite takes its app
update, SSH switch, password and reboot from its own server only; reading and
settings stay open to the LAN, and so do its two dependency installs (snapclient,
CamillaDSP), which install as root a release upstream published.

This module is the server's half, and the one place the secret is made or put
on a request:

- **the key**: 32 random bytes in `SATELLITE_KEY_FILE` (0600), created at the
  first start. Each satellite is handed its own secret, an HMAC of its identity
  (the registry's `mac_id`) under that key, so one satellite taken over learns
  nothing that opens another;
- `headers(mac_id)`: the header carrying it, on `PUT /api/system/ssh`,
  `POST /api/system/password` and `POST /api/hardware/reboot`
  (`api/multiroom.py`), and on the hand-over below;
- `sign(form, mac_id)`: the form field carrying it on `POST /app/update`
  (`core/updates/satellite.py`). That form's names are frozen and append-only
  (`tests/contracts/test_milo_client_contract.py`), so the field is added,
  optional on the satellite's form, and refused there when absent — a 403
  that says why, not a 422;
- `probe(mac_id, ip)`: `PUT /api/system/server-secret`, which pins this
  server's secret on a satellite that holds none and only compares it on one
  that does, so it is safe to send at any time — and says whose secret the
  satellite holds (`Holding`). `hand_over` sends it at every admission
  (`SnapcastWebSocketService`'s one recipe, once the client counts as online),
  best effort; the relays ask it first (`api/multiroom.py::_signed`), so a
  satellite that pinned another server is refused before a trim, a card or a
  proof of presence is spent on a request it would then refuse.

The names on the wire and the derivation are compared by every satellite that
pinned a secret, across releases — the push that carries a new release reaches
a satellite still on the old one — so they are frozen like the update form's
fields (`tests/contracts/test_milo_client_contract.py`).

The satellite pins the first secret it is given (`milo-client/app/server_secret.py`)
— at an admission, or at the reboot that configures a speaker no admission has
reached yet, since one that has no card runs no snapclient — and refuses those
routes to any other secret from then on. The names on the wire are restated there,
since a satellite cannot import this tree, and held equal by
`tests/architecture/test_satellite_secret.py`, which also holds that every call
the server makes to one of those routes is signed here.

What the secret proves is that the request comes from the server that reached
the satellite first, nothing more. It is handed to whatever snapclient this
server admits under a speaker's identity, and readable by every process running
as `milo` (the same uid as the backend, as for the rest of `/var/lib/milo`). A
new key — a reflashed server, a deleted file — is a secret no satellite pinned:
every one of them refuses this server's update, remote access and reboot until
it is adopted again, which nothing here does.
"""
import asyncio
import hashlib
import hmac
import logging
import os
import re
import secrets
from enum import Enum
from pathlib import Path
from typing import Dict, Optional

import aiohttp

from backend.config.constants import CLIENT_API_PORT, SATELLITE_KEY_FILE

logger = logging.getLogger(__name__)

# The satellite's spellings, which it cannot import: held equal to
# milo-client/app/server_secret.py by tests/architecture/test_satellite_secret.py.
SATELLITE_SECRET_HEADER = "X-Milo-Server-Secret"
SATELLITE_SECRET_FIELD = "server_secret"

# A pin is two file reads and at most one fsynced write on the satellite.
HAND_OVER_TIMEOUT_S = 10

_KEY_SHAPE = re.compile(r"^[0-9a-f]{64}$")


class SatelliteSecretUnavailable(Exception):
    """This server holds no key to derive a satellite's secret from."""


class Holding(Enum):
    """Whose secret a satellite holds, as it answered the hand-over."""
    # It holds this server's: pinned by this hand-over, or before it.
    THIS_SERVER = "this_server"
    # It refuses this server's (403): it pinned another server's, or cannot
    # read or write its own pin. Every path to root is lost to this server.
    REFUSED = "refused"
    # Its app predates the secret (404): it requires none, and takes a
    # request to root bare.
    NONE_REQUIRED = "none_required"
    # An answer this server cannot read: what the request after it meets decides.
    UNKNOWN = "unknown"


class SatelliteSecret:
    """Makes each satellite's secret and puts it on the requests that need it."""

    def __init__(self, key_file: Path = SATELLITE_KEY_FILE):
        self._key_file = Path(key_file)
        self._key: Optional[bytes] = None
        self._lock = asyncio.Lock()
        # Satellites whose app predates the hand-over, or that hold another
        # server's secret: each said once, until it answers otherwise.
        self._lacking_route: set = set()
        self._refusing: set = set()
        # A key that cannot be made is said once, at error: by the boot, or by
        # the first admission that meets it. Every admission after it meets it too.
        self._unavailable_said = False

    async def initialize(self) -> bool:
        """Read the key, or make it at the first start, so a key that cannot be
        read is said at boot rather than at the first satellite."""
        try:
            await self._load()
        except SatelliteSecretUnavailable as e:
            logger.error(f"No satellite secret can be made: {e}")
            self._unavailable_said = True
            return False
        return True

    # ------------------------------------------------------------------ #
    # The secret
    # ------------------------------------------------------------------ #

    async def _load(self) -> bytes:
        async with self._lock:
            if self._key is None:
                self._key = await asyncio.to_thread(self._read_or_create)
            return self._key

    def _read_or_create(self) -> bytes:
        """The key on disk, made first if there is none. Blocking."""
        try:
            text = self._key_file.read_text(encoding="ascii").strip()
        except FileNotFoundError:
            return self._create()
        except (OSError, UnicodeDecodeError) as e:
            raise SatelliteSecretUnavailable(f"{self._key_file} cannot be read: {e}") from None
        if not _KEY_SHAPE.match(text):
            # Never replaced here: a new key is one no satellite pinned.
            raise SatelliteSecretUnavailable(
                f"{self._key_file} does not hold a key. Removing it makes a new one, "
                "which every satellite already paired refuses until it is adopted again"
            )
        return bytes.fromhex(text)

    def _create(self) -> bytes:
        """A new key, durable before it is used. Blocking.

        Linked into place, so two starts cannot each write their own and a
        reader never sees half of one."""
        key = secrets.token_bytes(32)
        tmp = self._key_file.with_name(f"{self._key_file.name}.{os.getpid()}.tmp")
        try:
            # 0600 from its creation: never a moment readable by another user.
            with open(tmp, "w", encoding="ascii", opener=lambda path, flags: os.open(path, flags, 0o600)) as f:
                f.write(f"{key.hex()}\n")
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp, self._key_file)
            except FileExistsError:
                return self._read_or_create()
            finally:
                tmp.unlink(missing_ok=True)
            directory = os.open(self._key_file.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as e:
            raise SatelliteSecretUnavailable(f"{self._key_file} cannot be written: {e}") from None
        logger.info(f"Made the key every satellite's secret derives from ({self._key_file})")
        return key

    async def _secret_for(self, mac_id: str) -> str:
        key = await self._load()
        return hmac.new(key, f"satellite:{mac_id.lower()}".encode(), hashlib.sha256).hexdigest()

    async def headers(self, mac_id: str) -> Dict[str, str]:
        """The header that proves this server to the satellite `mac_id`.

        Raises `SatelliteSecretUnavailable` when this server has no key."""
        return {SATELLITE_SECRET_HEADER: await self._secret_for(mac_id)}

    async def sign(self, form: aiohttp.FormData, mac_id: str) -> None:
        """Add the field that proves this server to a `POST /app/update` form."""
        form.add_field(SATELLITE_SECRET_FIELD, await self._secret_for(mac_id))

    # ------------------------------------------------------------------ #
    # The hand-over
    # ------------------------------------------------------------------ #

    async def hand_over(self, mac_id: str, client_ip: str) -> None:
        """Hand one satellite its secret, at its admission.

        Best effort, like the admission's other pushes: a satellite that is
        gone is logged and left, and the next admission is the retry. Never
        raises."""
        try:
            await self.probe(mac_id, client_ip)
        except SatelliteSecretUnavailable as e:
            if self._unavailable_said:
                logger.debug(f"Satellite {mac_id} was not handed its secret: {e}")
            else:
                self._unavailable_said = True
                logger.error(f"Satellite {mac_id} was not handed its secret: {e}")
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning(
                f"Could not hand satellite {mac_id} its secret at {client_ip}: "
                f"{exc or type(exc).__name__}"
            )

    async def probe(self, mac_id: str, client_ip: str) -> Holding:
        """Hand one satellite its secret and say whose it holds.

        Pins this server's on a satellite that holds none, compares it on one
        that does: sent again, it changes nothing. Raises
        `SatelliteSecretUnavailable` when this server has no key, and what
        aiohttp raises when the satellite does not answer."""
        headers = await self.headers(mac_id)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=HAND_OVER_TIMEOUT_S)) as session:
            async with session.put(
                f"http://{client_ip}:{CLIENT_API_PORT}/api/system/server-secret", headers=headers,
            ) as resp:
                if resp.status == 200:
                    try:
                        changed = (await resp.json())["data"]["changed"]
                    except (ValueError, KeyError, TypeError, aiohttp.ContentTypeError) as exc:
                        logger.warning(
                            f"Satellite {mac_id} answered its secret's hand-over with an "
                            f"unreadable body: {exc!r}"
                        )
                        return Holding.UNKNOWN
                    self._lacking_route.discard(mac_id)
                    self._refusing.discard(mac_id)
                    if changed:
                        logger.info(
                            f"Satellite {mac_id} now holds this server's secret: its update, "
                            "remote access and reboot answer this server only"
                        )
                    return Holding.THIS_SERVER
                detail = await resp.text()

        if resp.status == 404:
            if mac_id not in self._lacking_route:
                self._lacking_route.add(mac_id)
                logger.info(f"Satellite {mac_id} cannot take its secret yet: its app predates it")
            return Holding.NONE_REQUIRED
        if resp.status == 403:
            if mac_id in self._refusing:
                logger.debug(f"Satellite {mac_id} still refuses this server's secret")
            else:
                self._refusing.add(mac_id)
                logger.error(
                    f"Satellite {mac_id} refuses this server's secret ({detail}): it holds "
                    "another server's, or cannot read or write its own. Its update, remote "
                    "access and reboot are refused to this server until it is adopted again"
                )
            return Holding.REFUSED
        logger.warning(f"Satellite {mac_id} refused its secret (HTTP {resp.status}): {detail}")
        return Holding.UNKNOWN
