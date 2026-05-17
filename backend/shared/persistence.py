"""
Versioned JSON persistence with fail-loud schema mismatch.

The owning service declares a ``SCHEMA_VERSION`` class constant, loads via
``load_versioned_json``, and saves via ``save_versioned_json``. When a file's
``schema_version`` doesn't match what the service expects, ``SchemaVersionMismatch``
is raised so main.py can log a clear reset command and exit. See CLAUDE.md
§"Development & Coding Guidelines §2" and ``BREAKING_CHANGES.md`` at the repo root.
"""
import json
import os
from pathlib import Path
from typing import Any, Dict

import aiofiles


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
            "            see BREAKING_CHANGES.md at the repo root for context\n"
        )


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
    found = data.get("schema_version") if isinstance(data, dict) else None
    if found != expected_version:
        raise SchemaVersionMismatch(file, expected_version, found)

    return data


async def save_versioned_json(file: Path, data: Dict[str, Any], version: int) -> None:
    """Atomically write a versioned JSON file, stamping ``schema_version`` into the payload.

    Stamps ``schema_version`` automatically so callers can't forget it (a
    missing field would raise SchemaVersionMismatch on the next load).
    """
    payload = dict(data)
    payload["schema_version"] = version

    file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = file.with_suffix(file.suffix + ".tmp")

    async with aiofiles.open(temp_file, "w", encoding="utf-8") as f:
        await f.write(json.dumps(payload, ensure_ascii=False, indent=2))
        await f.write("\n")
        await f.flush()
        os.fsync(f.fileno())

    os.replace(temp_file, file)
