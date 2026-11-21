"""The server's secret: what a satellite's paths to root require of a request.

`milo-client` is root by construction (CLAUDE.md, invariant 1): `POST /app/update`
hands the tarball it receives to a helper that installs it as root, sudoers
included, and until this module any host on the LAN could send one. Owner
decision, 2026-11-13: a satellite takes its paths to root from its own server
only. Reading and settings (volume, EQ, the WiFi and timezone pushes…) stay
open to the LAN, as on the server.

The server hands its secret over at every admission (`PUT /api/system/server-secret`,
from the server's one admission recipe, and from the heartbeats that call for
it: a configured speaker's ahead of its admission, and those described under
"Following a new server" below), and the satellite pins the first one it is
given — on that route or on any route below, whichever reaches it first:
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
identity.

**Following a new server.** A server that was reflashed holds a new key, so
every satellite that pinned the old one refuses it. Owner decision: the gesture
that moves a speaker is to unplug it and plug it back in. A process that holds
a pin and starts on a boot no clean stop came before — no `CLEAN_STOP_FILE`,
which the app writes when systemd stops it, and the first start since
`BOOT_ID_FILE` changed — opens a window of `READOPTION_WINDOW_S`, during which
the hand-over (and the hand-over alone) replaces the pin with the secret it
carries. The window closes at the first secret it takes, at the first request
that carries the pinned one (its own server found it: nothing to follow), or
at its end; a crash of the app restarts it on the same boot and opens none.
A unit that holds no pin opens none either: it has nothing to replace, and its
first pin stays the first contact above. The heartbeat says while the window
is open (`accepts_new_server`), and the server hands its secret over at every
heartbeat that says so — how a speaker that answered another server comes to
answer this one, with or without an admission to carry it. Outside it, an
unknown server stays refused.

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
import time
from pathlib import Path
from typing import Optional

from fastapi import Header, HTTPException, Request

from atomic_file import write_atomic

logger = logging.getLogger(__name__)

SECRET_FILE = Path("/var/lib/milo-client/server-secret")
SECRET_HEADER = "X-Milo-Server-Secret"
SECRET_FIELD = "server_secret"

# The window in which a satellite follows a new server (above). Beside the pin,
# outside `app/`, so an update's trial keeps them: the marker a clean stop
# leaves, consumed by the next start, and the boot the last start ran on.
CLEAN_STOP_FILE = Path("/var/lib/milo-client/clean-stop")
BOOT_FILE = Path("/var/lib/milo-client/started-boot")
BOOT_ID_FILE = Path("/proc/sys/kernel/random/boot_id")
# A speaker's boot, its snapclient's admission and its first heartbeat take
# well under a minute; the rest is room for a server that is booting too.
READOPTION_WINDOW_S = 300

# What the server hands over: a SHA-256 HMAC, in hex. Anything else is not
# written: the pin is a file this process trusts on every request after it.
_SECRET_SHAPE = re.compile(r"^[0-9a-f]{64}$")


class ServerSecretRefused(Exception):
    """A request that does not carry this satellite's server's secret (HTTP 403)."""


# When the window closes, on the monotonic clock; None while it is closed.
_window_ends: Optional[float] = None
# Two new secrets in one window: the first replaces the pin, the second meets it.
_follow_lock = asyncio.Lock()


def accepts_new_server() -> bool:
    """Whether the hand-over would replace the pin with another server's secret now."""
    return _window_ends is not None and time.monotonic() < _window_ends


def _close_window(why: str) -> None:
    global _window_ends
    if _window_ends is not None:
        _window_ends = None
        logger.info("No new server is followed any more: %s", why)


def _read_line(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="ascii").strip() or None
    except FileNotFoundError:
        return None


def _write_durably(path: Path, text: str) -> None:
    """`text` in `path`, replaced whole and flushed, its directory too (0600). Blocking."""
    write_atomic(path, f"{text}\n".encode("ascii"), mode=0o600)


def open_window_after_unclean_stop() -> bool:
    """At the app's start: open the window if this boot came after a stop that left no trace. Blocking.

    True when it opened, which needs a pin to replace: a fresh card's first
    start follows no clean stop either, and a window there would have its
    heartbeat ask the server for a secret before anyone configured it.
    Fails closed: a boot id it cannot read, or a trace of the last stop it
    cannot read or write, opens nothing. The clean stop's marker is consumed
    only once this boot is recorded, so a start that cannot record it leaves
    the next start on this boot reading a clean stop still.
    """
    global _window_ends
    _window_ends = None
    try:
        clean = CLEAN_STOP_FILE.exists()
        boot = _read_line(BOOT_ID_FILE)
        first_start_of_boot = boot is not None and boot != _read_line(BOOT_FILE)
        if first_start_of_boot:
            _write_durably(BOOT_FILE, boot)
        CLEAN_STOP_FILE.unlink(missing_ok=True)
        holds_a_pin = SECRET_FILE.exists()
    except (OSError, UnicodeDecodeError) as e:
        logger.error("Cannot tell how the last stop went (%s): no new server is followed", e)
        return False
    if boot is None:
        logger.error("Cannot read this boot's id (%s): no new server is followed", BOOT_ID_FILE)
        return False
    if clean or not first_start_of_boot:
        return False
    if not holds_a_pin:
        logger.info("Started after a stop that left no trace, holding no server secret: nothing to follow")
        return False
    _window_ends = time.monotonic() + READOPTION_WINDOW_S
    logger.warning(
        "This unit started after a stop that left no trace (a power cut): for %s s it "
        "follows a server that hands it a new secret",
        READOPTION_WINDOW_S,
    )
    return True


def record_clean_stop() -> None:
    """At the app's stop: the marker the next start reads as a clean stop. Blocking, never raises."""
    try:
        _write_durably(CLEAN_STOP_FILE, "clean")
    except OSError as e:
        # The next start then opens the window: the stop is taken for a power cut.
        logger.error("Cannot record a clean stop (%s): the next start follows a new server", e)


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


async def admit(presented: Optional[str], *, follow: bool = False) -> bool:
    """Return once `presented` is this satellite's server's secret: True when
    this request pinned it, False when it matched the pin.

    `follow` is the hand-over's: inside the window, a secret that is not the
    pinned one replaces it (True), and so does any secret when the pin cannot
    be read. Raises `ServerSecretRefused`, saying why, otherwise.
    """
    if not presented:
        raise ServerSecretRefused("it carries no server secret")
    if not _SECRET_SHAPE.match(presented):
        raise ServerSecretRefused("what it carries is not a server secret")
    try:
        pinned = await asyncio.to_thread(_read_pinned)
    except ServerSecretRefused:
        if not (follow and accepts_new_server()):
            raise
        return await _follow(presented)
    if pinned is None:
        # Nothing to follow on a unit that holds none: its first pin is trust
        # on first use, and a second first contact is compared against it.
        _close_window("this unit holds no secret to replace")
        if await asyncio.to_thread(_pin, presented):
            logger.info(
                "Pinned the server's secret: from now on only that server reaches "
                "this unit's update, remote access and reboot"
            )
            return True
        # Another first contact pinned one meanwhile: compared like any other.
        pinned = await asyncio.to_thread(_read_pinned)
    if pinned and hmac.compare_digest(pinned, presented):
        _close_window("its own server reached it")
        return False
    if follow and accepts_new_server():
        return await _follow(presented)
    if follow:
        raise ServerSecretRefused(
            "it carries another server's secret (unplugged and plugged back in, this "
            "speaker follows a new server for a few minutes)"
        )
    raise ServerSecretRefused("it carries another server's secret")


async def _follow(presented: str) -> bool:
    """Replace the pin with `presented`, inside the window: True when it did."""
    async with _follow_lock:
        # A secret that took the window first closed it: this one meets its pin.
        if not accepts_new_server():
            return await admit(presented)
        try:
            await asyncio.to_thread(_write_durably, SECRET_FILE, presented)
        except OSError as e:
            logger.error("Cannot pin a new server's secret (%s): every path to root is refused", e)
            raise ServerSecretRefused("this unit cannot write its pin") from None
        _close_window("a new server was followed")
        logger.warning(
            "Followed a new server: its secret replaces the one pinned before, and from "
            "now on only that server reaches this unit's update, remote access and reboot"
        )
        return True


async def guard(presented: Optional[str], what: str, *, follow: bool = False) -> bool:
    """`admit`, its refusal as the 403 a route answers. `what` names the
    request in the log line, which never carries the secret."""
    try:
        return await admit(presented, follow=follow)
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
