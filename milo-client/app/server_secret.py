"""The server's secret: what a satellite's paths to root require of a request.

`milo-client` is root by construction (CLAUDE.md, invariant 1): `POST /app/update`
hands the tarball it receives to a helper that installs it as root, sudoers
included, and until this module any host on the LAN could send one. Owner
decision, 2026-11-13: a satellite takes its paths to root from its own server
only. Reading and settings (volume, EQ, the WiFi and timezone pushes…) stay
open to the LAN, as on the server.

The server hands its secret over at every admission (`PUT /api/system/server-secret`,
from the server's one admission recipe), and the satellite pins the first one
it is given — on that route or on any route below, whichever reaches it first:
a speaker still waiting to be configured has no snapclient, so it is never
admitted, and its first contact is the reboot that configures it. The pin
lives in `SECRET_FILE`, outside `app/`, so an update's trial, which swaps
`app/` and may put the previous one back, keeps it. From then on a request
carries that secret or is refused with 403, before it does anything:

- `POST /app/update`, in the form field `SECRET_FIELD` — optional on the form,
  like any field added to that frozen set, and refused here when absent;
- `PUT /api/system/ssh`, `POST /api/system/password`, `POST /api/hardware/reboot`
  and the hand-over itself, in the header `SECRET_HEADER`.

Those are the decision's list, not every route that reaches root: the two
dependency installs, `POST /update` (snapclient) and `POST /camilladsp/update`,
stay open to the LAN — each installs as root a release upstream published, at
the version the caller names.

A satellite that holds none refuses a request that carries none: there is no
window in which a bare request reaches root. What is left is trust on first
use — a host that reaches a fresh satellite with a secret before its server
does is the server it pins — and the server's admission, which hands a
speaker's secret to whatever snapclient it admits under that speaker's
identity. Following a server that was reflashed, whose secret is new, is not
done here: until then the satellite refuses it.

The names are restated in `backend/core/multiroom/satellite_http.py`, which a
satellite cannot import (CLAUDE.md, invariant 2); `backend/tests/architecture/
test_satellite_secret.py` holds the two spellings equal, and the routes that
require the secret equal to the ones the server signs.
"""
import asyncio
import hmac
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)

SECRET_FILE = Path("/var/lib/milo-client/server-secret")
SECRET_HEADER = "X-Milo-Server-Secret"
SECRET_FIELD = "server_secret"

# What the server hands over: a SHA-256 HMAC, in hex. Anything else is not
# written: the pin is a file this process trusts on every request after it.
_SECRET_SHAPE = re.compile(r"^[0-9a-f]{64}$")


class ServerSecretRefused(Exception):
    """A request that does not carry this satellite's server's secret (HTTP 403)."""


def _read_pinned() -> Optional[str]:
    """The pinned secret, None while none is pinned. Blocking.

    Raises `ServerSecretRefused` when the pin cannot be read: read as "none
    pinned", it would let the next secret replace it."""
    try:
        return SECRET_FILE.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as e:
        logger.error("Cannot read the pinned server secret (%s): every path to root is refused", e)
        raise ServerSecretRefused("this unit cannot read the secret it pinned") from None


def _pin(secret: str) -> bool:
    """Pin `secret` unless one already is: True when this call pinned it. Blocking.

    Written beside the pin, under a name of its own (0600, as mkstemp makes
    it), made durable, then linked into place: a link fails when the name
    exists, so two first contacts cannot both pin, and no reader ever sees
    half a secret. Raises `ServerSecretRefused` when it cannot be written.
    """
    try:
        fd, name = tempfile.mkstemp(dir=SECRET_FILE.parent, prefix=f".{SECRET_FILE.name}.")
        tmp = Path(name)
        try:
            os.write(fd, f"{secret}\n".encode("ascii"))
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(tmp, SECRET_FILE)
        except FileExistsError:
            return False
        finally:
            tmp.unlink(missing_ok=True)
        directory = os.open(SECRET_FILE.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError as e:
        # A full or read-only /var/lib/milo-client: said, and refused like any
        # other request this unit cannot hold to a pin — never a bare 500.
        logger.error("Cannot pin the server's secret (%s): every path to root is refused", e)
        raise ServerSecretRefused("this unit cannot write its pin") from None
    return True


async def admit(presented: Optional[str]) -> bool:
    """Return once `presented` is this satellite's server's secret: True when
    this request pinned it, False when it matched the pin.

    Raises `ServerSecretRefused`, saying why, otherwise.
    """
    if not presented:
        raise ServerSecretRefused("it carries no server secret")
    if not _SECRET_SHAPE.match(presented):
        raise ServerSecretRefused("what it carries is not a server secret")
    pinned = await asyncio.to_thread(_read_pinned)
    if pinned is None:
        if await asyncio.to_thread(_pin, presented):
            logger.info(
                "Pinned the server's secret: from now on only that server reaches "
                "this unit's update, remote access and reboot"
            )
            return True
        # Another first contact pinned one meanwhile: compared like any other.
        pinned = await asyncio.to_thread(_read_pinned)
    if not pinned or not hmac.compare_digest(pinned, presented):
        raise ServerSecretRefused("it carries another server's secret")
    return False


async def guard(presented: Optional[str], what: str) -> bool:
    """`admit`, its refusal as the 403 a route answers. `what` names the
    request in the log line, which never carries the secret."""
    try:
        return await admit(presented)
    except ServerSecretRefused as refused:
        logger.warning("Refused %s: %s", what, refused)
        raise HTTPException(
            status_code=403,
            detail=f"Refused: {refused}. Only this speaker's own server may ask for it",
        ) from None


async def require_server_secret(
    request: Request,
    secret: Optional[str] = Header(default=None, alias=SECRET_HEADER),
) -> bool:
    """The `Depends()` of a route that reaches root: True when this request pinned the secret."""
    return await guard(secret, f"{request.method} {request.url.path}")
