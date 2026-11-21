"""
Versioned JSON persistence: atomic writes, and a file that cannot be read is set aside.

The owning service declares a ``SCHEMA_VERSION`` class constant, loads via
``load_versioned_json`` and saves via ``save_versioned_json``. A file that is
there but cannot be used — not JSON, not an object, another ``schema_version``,
a top-level key its owner requires missing — is renamed beside itself as
``<name>.<UTC stamp>.bad``, reported once (``report_set_aside``), and read as
absent: its owner starts that section again from its defaults, exactly as on a
fresh install, and the unit always starts. Nothing reads the old shape back —
this is not a migration — but its bytes are kept. See CLAUDE.md
§"Persistence & the schema-version protocol".
"""
import asyncio
import contextlib
import itertools
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Monotonic counter making each in-flight temp file unique (see _unique_temp).
_temp_counter = itertools.count()

# What a file set aside is renamed to end with, after its name and a UTC stamp.
SET_ASIDE_SUFFIX = ".bad"


class _Unreadable(Exception):
    """Why a file that is there cannot be used: the reason its report gives."""


def _decode(raw: bytes, expected_version: int, required: Tuple[str, ...]) -> Dict[str, Any]:
    """The file's object, or ``_Unreadable`` saying what is wrong with it."""
    try:
        data = json.loads(raw.decode("utf-8"))
    # ValueError: UnicodeDecodeError and JSONDecodeError alike. RecursionError:
    # nesting deeper than the decoder's stack — bytes the owner cannot use either.
    except (ValueError, RecursionError) as e:
        raise _Unreadable(f"not JSON: {e}") from e
    if not isinstance(data, dict):
        raise _Unreadable(f"a JSON {type(data).__name__}, not an object")
    if "schema_version" not in data:
        raise _Unreadable(f"no schema_version, {expected_version} expected")
    found = data["schema_version"]
    # `type(...) is int`: JSON's `true` equals 1 in Python and would pass for v1.
    if type(found) is not int or found != expected_version:
        raise _Unreadable(f"schema_version {found!r}, {expected_version} expected")
    missing = [key for key in required if key not in data]
    if missing:
        raise _Unreadable(f"no {', '.join(missing)}")
    return data


def _move_aside(file: Path) -> Optional[Path]:
    """Rename ``file`` to the first free ``<name>.<UTC stamp>[_N].bad`` beside it. Blocking.

    A rename, not a copy: the bytes stay exactly as they were (mode and owner
    included — the Spotify files are 0600), the next read finds no file and
    starts from the defaults, and nothing is met again at the next boot to be
    kept a second time. None when the file is already gone: a reader that got
    there first set it aside, and reported it.

    A second set-aside in the same second takes ``_1``, ``_2``…, which sort
    after the first (``_`` follows ``.``), so a listing by name is the order
    the copies were made in.
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    for n in itertools.count():
        kept = file.with_name(f"{file.name}.{stamp}{f'_{n}' if n else ''}{SET_ASIDE_SUFFIX}")
        if kept.exists():
            continue
        try:
            os.rename(file, kept)
        except FileNotFoundError:
            return None
        return kept


@dataclass(frozen=True)
class SetAside:
    """One file set aside: where it was, where its bytes went, why, and when."""

    file: Path
    kept: Path
    reason: str
    at: float


# Who keeps the lasting notice of each set-aside (core/system/notices.py). The
# boot's first set-asides come before any service exists — settings.json is
# read synchronously in STEP 3b — so until a keeper claims them they wait here.
_keeper: Optional[Callable[[SetAside], None]] = None
_unclaimed: List[SetAside] = []


def report_set_aside(file: Path, kept: Path, reason: str) -> None:
    """Say that ``file`` was set aside as ``kept``: one ERROR line, and the only one.

    Every set-aside — the two loaders' and the two ``set_aside``'s — is
    reported here and nowhere else, so its lasting notice hangs from here too:
    the record goes to the keeper ``claim_set_asides`` installed, or waits for
    one. The ERROR alone reaches the screen only through the live banner
    (``WebSocketLogHandler``), which no client hears at boot, when most
    set-asides happen.
    """
    logger.error(
        "%s could not be read (%s): it is kept as %s, and what it held starts "
        "again from its defaults",
        file, reason, kept.name,
    )
    record = SetAside(file=Path(file), kept=Path(kept), reason=reason, at=time.time())
    if _keeper is None:
        _unclaimed.append(record)
    else:
        _keeper(record)


def claim_set_asides(keeper: Callable[[SetAside], None]) -> List[SetAside]:
    """Hand every later set-aside to ``keeper``; return the ones made before it.

    ``keeper`` is called on the thread that reports, synchronously, and must
    not raise: it is handed the record in the middle of a load.
    """
    global _keeper
    _keeper = keeper
    claimed = list(_unclaimed)
    _unclaimed.clear()
    return claimed


def release_set_asides(keeper: Callable[[SetAside], None]) -> None:
    """Stop handing set-asides to ``keeper``, if it is still the one that holds them."""
    global _keeper
    if _keeper == keeper:
        _keeper = None


def _load(
    file: Path, expected_version: int, required: Tuple[str, ...]
) -> Tuple[Dict[str, Any], Optional[Tuple[Path, str]]]:
    """Blocking core of both loaders: the data, and what was set aside, if anything.

    An ``OSError`` other than an absent file is raised as it is: a card that
    cannot be read says nothing about the bytes, which may be fine, so they
    are not set aside. A file that cannot be renamed aside raises too — read
    as absent, it would be overwritten by the owner's defaults.
    """
    try:
        raw = file.read_bytes()
    except FileNotFoundError:
        return {}, None
    try:
        return _decode(raw, expected_version, required), None
    except _Unreadable as unreadable:
        try:
            kept = _move_aside(file)
        except OSError as e:
            # Not read, not moved: the owner fails as on a card it cannot
            # read, and its error names what was wrong with the file.
            raise OSError(e.errno, f"cannot set {file} aside ({unreadable}): {e.strerror}") from e
        return {}, (None if kept is None else (kept, str(unreadable)))


async def load_versioned_json(
    file: Path, expected_version: int, *, required: Iterable[str] = ()
) -> Dict[str, Any]:
    """Load a versioned JSON file; ``{}`` when it is absent or was just set aside.

    ``{}`` is the fresh install either way: the caller applies its defaults
    and persists them via ``save_versioned_json``. ``required`` names the
    top-level keys every file the owner writes holds — one missing is a file
    set aside, never a key filled in. The returned dict keeps the
    ``schema_version`` field; callers may pop it if they want a clean payload.
    The read runs off the event loop; the report on the caller's.
    """
    file = Path(file)
    return await _off_loop_then_report(file, _load, file, expected_version, tuple(required))


def load_versioned_json_sync(
    file: Path, expected_version: int, *, required: Iterable[str] = ()
) -> Dict[str, Any]:
    """Blocking twin of ``load_versioned_json``, for the bootstrap reads.

    ``SettingsService.get_setting_sync`` is the *first* reader of settings.json
    on every boot (``dependencies.py`` STEP 3b derives routing.env / mac.env /
    snapclient.env from it), and ``HardwareService`` answers bootstrap getters
    before its ``initialize()``. Same contract, same implementation.
    """
    file = Path(file)
    data, aside = _load(file, expected_version, tuple(required))
    if aside is not None:
        report_set_aside(file, *aside)
    return data


async def set_aside(file: Path, reason: str) -> Optional[Path]:
    """Set aside a file its owner read but cannot use, and report it.

    For what the loader cannot see: a value the owner cannot take (an
    equalizer gain that is not a number, a token record missing a field), or a
    file kept outside the versioned API (``last_volume.json``). Returns where
    the bytes went, or None when the file was already gone.
    """
    file = Path(file)
    return await _off_loop_then_report(file, _move_aside_for, file, reason)


def set_aside_sync(file: Path, reason: str) -> Optional[Path]:
    """Blocking twin of ``set_aside``, for the bootstrap read that validates
    what it loaded (``SettingsService.get_setting_sync``). Same contract."""
    file = Path(file)
    kept, aside = _move_aside_for(file, reason)
    if aside is not None:
        report_set_aside(file, *aside)
    return kept


def _move_aside_for(
    file: Path, reason: str
) -> Tuple[Optional[Path], Optional[Tuple[Path, str]]]:
    """``_move_aside`` in the shape ``_load`` answers: the result, and what was set aside."""
    kept = _move_aside(file)
    return kept, (None if kept is None else (kept, reason))


async def _off_loop_then_report(
    file: Path, blocking: Callable[..., Tuple[Any, Any]], *args: Any
) -> Any:
    """Run ``blocking`` on a worker thread, then report what it set aside, on the loop.

    A caller cancelled meanwhile (a boot stopped, a source handler a STOP
    cuts) does not stop the worker: a rename under way still lands. So the
    report is then made when the worker ends — a file renamed with no word of
    it would be lost in silence, and the next boot, finding nothing, would
    say nothing either.
    """
    work = asyncio.ensure_future(asyncio.to_thread(blocking, *args))
    try:
        result, aside = await asyncio.shield(work)
    except asyncio.CancelledError:
        def _report_late(done: "asyncio.Future") -> None:
            if not done.cancelled() and done.exception() is None:
                late_aside = done.result()[1]
                if late_aside is not None:
                    report_set_aside(file, *late_aside)
        work.add_done_callback(_report_late)
        raise
    if aside is not None:
        report_set_aside(file, *aside)
    return result


def _unique_temp(file: Path) -> Path:
    """A temp name beside ``file`` that no concurrent writer shares.

    A shared ``<file>.tmp`` lets two writers collide: one truncates the other's
    half-written temp, or the loser's ``os.replace`` finds it already renamed
    away. PID + counter keep every temp distinct.
    """
    return file.with_name(f"{file.name}.{os.getpid()}.{next(_temp_counter)}.tmp")


def _write_bytes_atomically(file: Path, data: bytes, temp_file: Path, private: bool = False) -> None:
    """Write, fsync and rename into place. Blocking — call via ``to_thread``.

    Every syscall of the sequence runs on the same worker thread. Wrapping only
    the write (aiofiles) left mkdir, fsync and replace on the event-loop thread,
    where an fsync on a busy SD card stalls every WS, HTTP and monitor task —
    and this primitive is on the write path of every persisted file.

    ``private`` is for a file holding a secret: the temp is created 0600, so the
    secret is never readable by anyone else even before the rename, and the
    rename carries that mode over whatever mode the file had. A directory the
    call creates is 0700; an existing one is left as it is.
    """
    file.parent.mkdir(mode=0o700 if private else 0o777, parents=True, exist_ok=True)
    try:
        if private:
            fd = os.open(temp_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            stream = os.fdopen(fd, "wb")
        else:
            stream = open(temp_file, "wb")
        with stream as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_file, file)
    finally:
        # On success the temp was renamed away (unlink → FileNotFoundError, suppressed);
        # on any failure/cancellation, drop our unique temp so it can't leak.
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_file)


async def write_bytes_atomically(file: Path, data: bytes, *, private: bool = False) -> None:
    """Replace ``file`` with ``data`` atomically, off the event loop.

    A reader meets the old content or the new one, never a partial file — which
    matters most for caches, where a file's mere existence is the hit.
    ``private``: written 0600 (see ``_write_bytes_atomically``).
    """
    await asyncio.to_thread(_write_bytes_atomically, file, data, _unique_temp(file), private)


def _write_json_atomically(
    file: Path, payload: Dict[str, Any], temp_file: Path, private: bool = False
) -> None:
    """Serialize, then write atomically. Blocking — call via ``to_thread``.

    Serialization belongs on the worker too: `indent=2` over a large payload
    (radio favorites with their cached records, multiroom state) is time the
    event loop would otherwise spend not answering the WebSocket.
    """
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _write_bytes_atomically(file, text.encode("utf-8"), temp_file, private)


async def save_versioned_json(
    file: Path, data: Dict[str, Any], version: int, *, private: bool = False
) -> None:
    """Atomically write a versioned JSON file, stamping ``schema_version`` into the payload.

    Stamps ``schema_version`` automatically so callers can't forget it (a
    missing field would set the file aside on the next load).
    ``private``: written 0600 (see ``_write_bytes_atomically``).
    """
    payload = dict(data)
    payload["schema_version"] = version

    # Concurrent writes are real: the same record reaches this primitive from
    # several uncoordinated paths (e.g. the EQ debounced persist plus the access
    # layer's persist_state/update_cache). os.replace stays atomic, so the final
    # file is always a complete payload (last writer wins).
    await asyncio.to_thread(_write_json_atomically, file, payload, _unique_temp(file), private)
