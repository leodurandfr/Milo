"""
Versioned JSON persistence with fail-loud schema mismatch.

The owning service declares a ``SCHEMA_VERSION`` class constant, loads via
``load_versioned_json``, and saves via ``save_versioned_json``. When a file's
``schema_version`` doesn't match what the service expects, ``SchemaVersionMismatch``
is raised so main.py can log a clear reset command and exit. See CLAUDE.md
§"Persistence & schema-version protocol".
"""
import asyncio
import contextlib
import itertools
import json
import os
from pathlib import Path
from typing import Any, Dict

import aiofiles

# Monotonic counter making each in-flight temp file unique (see _unique_temp).
_temp_counter = itertools.count()


class SchemaVersionMismatch(RuntimeError):
    """Raised when a persisted file's schema_version doesn't match what the owning service expects."""

    def __init__(self, file: Path, expected: int, found: Any) -> None:
        self.file = file
        self.expected = expected
        self.found = found
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        found_str = (
            f"schema_version={self.found}" if self.found is not None else "schema_version missing"
        )
        return (
            "\n*** SCHEMA MISMATCH ***\n"
            f"  File:     {self.file}\n"
            f"  Expected: schema_version={self.expected}\n"
            f"  Found:    {found_str}\n"
            f"  Fix:      delete the file to reset its state: rm {self.file}\n"
            f"            then restart the service: sudo systemctl restart milo-backend\n"
        )


def check_schema_version(file: Path, data: Any, expected_version: int) -> None:
    """Raise SchemaVersionMismatch unless ``data`` carries ``expected_version``.

    Exposed on its own for the reader that must do its own file I/O:
    ``SettingsService._read_locked`` keeps the raw text so it can snapshot a
    corrupt file before falling back, and still has to refuse a version it did
    not verify — otherwise its ``_write_locked`` re-stamps the file at the
    current version, which is a silent migration.
    """
    found = data.get("schema_version") if isinstance(data, dict) else None
    if found != expected_version:
        raise SchemaVersionMismatch(file, expected_version, found)


async def load_versioned_json(file: Path, expected_version: int) -> Dict[str, Any]:
    """Load a versioned JSON file, raising SchemaVersionMismatch on version drift.

    Returns ``{}`` if the file doesn't exist (fresh install — caller should
    apply defaults and persist via ``save_versioned_json``). The returned dict
    keeps the ``schema_version`` field; callers may pop it if they want a
    clean payload.
    """
    if not file.exists():
        return {}

    async with aiofiles.open(file, "r", encoding="utf-8") as f:
        content = await f.read()

    data = json.loads(content)
    check_schema_version(file, data, expected_version)
    return data


def load_versioned_json_sync(file: Path, expected_version: int) -> Dict[str, Any]:
    """Blocking twin of ``load_versioned_json``, for the pre-loop bootstrap read.

    ``SettingsService.get_setting_sync`` runs before the event loop exists
    (``dependencies.py`` STEP 3b derives routing.env / mac.env / snapclient.env
    from it), so it cannot await the async reader — and it is the *first* reader
    of settings.json on every boot. Same contract: ``{}`` when the file is
    absent, ``SchemaVersionMismatch`` on drift.
    """
    if not file.exists():
        return {}

    data = json.loads(file.read_text(encoding="utf-8"))
    check_schema_version(file, data, expected_version)
    return data


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
    missing field would raise SchemaVersionMismatch on the next load).
    ``private``: written 0600 (see ``_write_bytes_atomically``).
    """
    payload = dict(data)
    payload["schema_version"] = version

    # Concurrent writes are real: the same record reaches this primitive from
    # several uncoordinated paths (e.g. the EQ debounced persist plus the access
    # layer's persist_state/update_cache). os.replace stays atomic, so the final
    # file is always a complete payload (last writer wins).
    await asyncio.to_thread(_write_json_atomically, file, payload, _unique_temp(file), private)
