"""Unit tests for load_versioned_json / save_versioned_json."""
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
