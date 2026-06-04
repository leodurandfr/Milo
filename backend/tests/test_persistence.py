"""Unit tests for load_versioned_json / save_versioned_json."""
import asyncio
import json
from pathlib import Path

import pytest

from backend.shared.persistence import (
    SchemaVersionMismatch,
    load_versioned_json,
    save_versioned_json,
)


@pytest.mark.asyncio
async def test_load_missing_file_returns_empty(tmp_path: Path):
    result = await load_versioned_json(tmp_path / "absent.json", expected_version=1)
    assert result == {}


@pytest.mark.asyncio
async def test_load_missing_schema_version_raises(tmp_path: Path):
    file = tmp_path / "no_version.json"
    file.write_text(json.dumps({"some": "data"}), encoding="utf-8")

    with pytest.raises(SchemaVersionMismatch) as excinfo:
        await load_versioned_json(file, expected_version=1)

    assert excinfo.value.found is None
    assert excinfo.value.expected == 1
    assert str(file) in str(excinfo.value)
    assert f"rm {file}" in str(excinfo.value)


@pytest.mark.asyncio
async def test_load_wrong_schema_version_raises(tmp_path: Path):
    file = tmp_path / "old.json"
    file.write_text(json.dumps({"schema_version": 1, "x": 42}), encoding="utf-8")

    with pytest.raises(SchemaVersionMismatch) as excinfo:
        await load_versioned_json(file, expected_version=2)

    assert excinfo.value.found == 1
    assert excinfo.value.expected == 2


@pytest.mark.asyncio
async def test_load_matching_schema_version_returns_data(tmp_path: Path):
    file = tmp_path / "ok.json"
    file.write_text(json.dumps({"schema_version": 2, "x": 42}), encoding="utf-8")

    data = await load_versioned_json(file, expected_version=2)
    assert data == {"schema_version": 2, "x": 42}


@pytest.mark.asyncio
async def test_save_stamps_version_and_roundtrip(tmp_path: Path):
    file = tmp_path / "roundtrip.json"
    await save_versioned_json(file, {"x": 42, "y": "hello"}, version=3)

    loaded = await load_versioned_json(file, expected_version=3)
    assert loaded == {"schema_version": 3, "x": 42, "y": "hello"}


@pytest.mark.asyncio
async def test_save_overrides_caller_schema_version(tmp_path: Path):
    """save_versioned_json always stamps its `version` arg, even if caller set one in the dict."""
    file = tmp_path / "override.json"
    await save_versioned_json(file, {"schema_version": 99, "x": 1}, version=2)

    loaded = await load_versioned_json(file, expected_version=2)
    assert loaded["schema_version"] == 2


@pytest.mark.asyncio
async def test_save_concurrent_writes_do_not_race_on_tempfile(tmp_path: Path):
    """Overlapping writes to the same path must not collide on a shared temp file.

    Regression guard for the EQ persist crash: a fixed ``<file>.tmp`` name lets the
    first writer's ``os.replace`` rename it away, so a concurrent writer's
    ``os.replace`` then raises ``FileNotFoundError [Errno 2] '<file>.tmp' -> '<file>'``.
    The local EQ record reaches this primitive from several uncoordinated paths
    (debounced persist + the access layer's ``persist_state``/``update_cache``), so
    concurrent writes are real. A unique temp name per write makes them collision-free.
    """
    file = tmp_path / "concurrent.json"

    # Many overlapping writers maximize the chance of hitting the race window on a
    # shared-tempfile implementation; a unique-tempfile implementation never collides.
    await asyncio.gather(*[
        save_versioned_json(file, {"writer": i}, version=1)
        for i in range(25)
    ])

    # The final file is a complete payload from exactly one writer (last wins).
    loaded = await load_versioned_json(file, expected_version=1)
    assert loaded["schema_version"] == 1
    assert "writer" in loaded

    # No stray temp files left behind.
    assert list(tmp_path.glob("*.tmp")) == []
