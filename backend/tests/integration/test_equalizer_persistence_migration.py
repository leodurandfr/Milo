# backend/tests/integration/test_equalizer_persistence_migration.py
"""
Integration tests for the equalizer.json persistence split + one-time migration.

Covers:
- Cold start with neither settings.json equalizer block nor equalizer.json:
  in-memory state is the 10 default flat bands and nothing is written.
- Cold start with an existing equalizer.json: state is restored from the file.
- One-time migration: legacy equalizer.* keys in settings.json get written to
  /var/lib/milo/equalizer.json, and the equalizer block is then removed from
  settings.json (and stays removed across subsequent boots).
- Defensive cleanup: when equalizer.json already exists and a stale equalizer
  block lingers in settings.json (e.g. from a partial migration on a previous
  boot), the block is dropped on the next load without touching the file.

These tests intentionally use a real SettingsService against a temp file
(no mocks) so we exercise the read-modify-write under `_file_lock` end-to-end.
"""
import json
import os
import tempfile
from pathlib import Path

import pytest

from backend.core.equalizer import CamillaDSPService
from backend.core.settings import SettingsService


@pytest.fixture
def tmp_settings_file():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        path = f.name
    yield path
    for p in (path, path + '.tmp'):
        try:
            os.unlink(p)
        except FileNotFoundError:
            pass


@pytest.fixture
def tmp_equalizer_file(tmp_path):
    return tmp_path / "equalizer.json"


@pytest.fixture
def settings_service(tmp_settings_file):
    svc = SettingsService()
    svc.settings_file = tmp_settings_file
    return svc


def _write_settings(path: str, data: dict) -> None:
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f)


def _read_settings(path: str) -> dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _legacy_settings_blob() -> dict:
    """Realistic pre-split settings.json with the legacy equalizer block."""
    return {
        "setup_completed": True,
        "routing": {
            "multiroom_enabled": False,
            "equalizer_effects_enabled": False,
        },
        "equalizer": {
            "filters": [
                {"id": f"eq_band_{i:02d}", "type": "Peaking",
                 "freq": float(freq), "gain": gain, "q": 1.41, "enabled": True}
                for i, (freq, gain) in enumerate(zip(
                    [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000],
                    [5, 4, 3, 1, 2, 2, 3, 4, 3, 2],
                ))
            ],
            "compressor": {
                "enabled": True, "threshold": -25.0, "ratio": 6.0,
                "attack": 15.0, "release": 150.0, "makeup_gain": 5.0,
            },
            "loudness": {"enabled": True, "high_boost": 8.0, "low_boost": 11.0},
            "mono": True,
            "active_preset": "acoustic",
            "custom_gains": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "effects_enabled": True,  # legacy toggle location
        },
    }


@pytest.mark.asyncio
async def test_cold_start_no_file_no_legacy_keys(
    settings_service, tmp_equalizer_file, monkeypatch
):
    """Fresh install: in-memory defaults, equalizer.json not created."""
    monkeypatch.setattr(CamillaDSPService, "STORAGE_PATH", tmp_equalizer_file)
    _write_settings(settings_service.settings_file, {
        "routing": {"multiroom_enabled": False, "equalizer_effects_enabled": False},
    })

    service = CamillaDSPService(settings_service=settings_service)
    await service._load_saved_config()

    assert not tmp_equalizer_file.exists(), \
        "Should not create equalizer.json when there's nothing to migrate"
    # 10 default flat bands from __init__
    assert len(service._filters) == 10
    assert all(f["gain"] == 0.0 for f in service._filters)
    assert service._active_preset is None
    assert service._effects_enabled is False


@pytest.mark.asyncio
async def test_cold_start_loads_existing_equalizer_json(
    settings_service, tmp_equalizer_file, monkeypatch
):
    """Existing equalizer.json: in-memory state matches file, no migration runs."""
    monkeypatch.setattr(CamillaDSPService, "STORAGE_PATH", tmp_equalizer_file)
    _write_settings(settings_service.settings_file, {
        "routing": {"multiroom_enabled": False, "equalizer_effects_enabled": True},
    })
    tmp_equalizer_file.write_text(json.dumps({
        "timestamp": "2026-05-13T00:00:00+00:00",
        "active_preset": "rock",
        "custom_gains": [0.0] * 10,
        "mono": False,
        "filters": [
            {"id": f"eq_band_{i:02d}", "type": "Peaking",
             "freq": float(f), "gain": float(g), "q": 1.41, "enabled": True}
            for i, (f, g) in enumerate(zip(
                [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000],
                [5, 4, 3, 2, 0, -1, 1, 3, 4, 5],  # rock gains
            ))
        ],
        "compressor": {"enabled": False, "threshold": -20.0, "ratio": 4.0,
                       "attack": 10.0, "release": 100.0, "makeup_gain": 0.0},
        "loudness": {"enabled": False, "high_boost": 5.0, "low_boost": 8.0},
    }))

    service = CamillaDSPService(settings_service=settings_service)
    await service._load_saved_config()

    assert service._active_preset == "rock"
    assert [f["gain"] for f in service._filters] == [5, 4, 3, 2, 0, -1, 1, 3, 4, 5]
    assert service._effects_enabled is True


@pytest.mark.asyncio
async def test_one_time_migration_from_legacy_settings(
    settings_service, tmp_equalizer_file, monkeypatch
):
    """Legacy block in settings.json migrates to equalizer.json on first boot."""
    monkeypatch.setattr(CamillaDSPService, "STORAGE_PATH", tmp_equalizer_file)
    _write_settings(settings_service.settings_file, _legacy_settings_blob())

    service = CamillaDSPService(settings_service=settings_service)
    await service._load_saved_config()

    # In-memory state populated from the legacy block.
    assert [f["gain"] for f in service._filters] == [5, 4, 3, 1, 2, 2, 3, 4, 3, 2]
    assert service._compressor["enabled"] is True
    assert service._compressor["threshold"] == -25.0
    assert service._loudness["enabled"] is True
    assert service._loudness["low_boost"] == 11.0
    assert service._mono is True
    assert service._active_preset == "acoustic"
    assert service._custom_gains == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    # Legacy effects_enabled lived in the equalizer block — relocated to routing.
    assert service._effects_enabled is True

    # equalizer.json was created with the migrated state.
    assert tmp_equalizer_file.exists()
    saved = json.loads(tmp_equalizer_file.read_text())
    assert [f["gain"] for f in saved["filters"]] == [5, 4, 3, 1, 2, 2, 3, 4, 3, 2]
    assert saved["active_preset"] == "acoustic"
    assert saved["mono"] is True

    # Legacy block removed from settings.json. Effects-enabled relocated.
    settings_after = _read_settings(settings_service.settings_file)
    assert "equalizer" not in settings_after
    assert settings_after["routing"]["equalizer_effects_enabled"] is True


@pytest.mark.asyncio
async def test_migration_is_idempotent_on_second_boot(
    settings_service, tmp_equalizer_file, monkeypatch
):
    """A second boot after migration is a no-op: file state wins, settings untouched."""
    monkeypatch.setattr(CamillaDSPService, "STORAGE_PATH", tmp_equalizer_file)
    _write_settings(settings_service.settings_file, _legacy_settings_blob())

    # First boot: migrates.
    svc1 = CamillaDSPService(settings_service=settings_service)
    await svc1._load_saved_config()

    file_after_first = tmp_equalizer_file.read_text()
    settings_after_first = _read_settings(settings_service.settings_file)

    # Second boot: should just load from equalizer.json.
    svc2 = CamillaDSPService(settings_service=settings_service)
    await svc2._load_saved_config()

    assert [f["gain"] for f in svc2._filters] == [5, 4, 3, 1, 2, 2, 3, 4, 3, 2]
    assert svc2._active_preset == "acoustic"
    assert tmp_equalizer_file.read_text() == file_after_first
    assert _read_settings(settings_service.settings_file) == settings_after_first


@pytest.mark.asyncio
async def test_defensive_cleanup_drops_stale_legacy_block(
    settings_service, tmp_equalizer_file, monkeypatch
):
    """If a previous partial migration left a stale equalizer block, drop it.

    Source of truth (equalizer.json) is preserved untouched.
    """
    monkeypatch.setattr(CamillaDSPService, "STORAGE_PATH", tmp_equalizer_file)

    # equalizer.json has the real state.
    tmp_equalizer_file.write_text(json.dumps({
        "timestamp": "2026-05-13T00:00:00+00:00",
        "active_preset": "acoustic",
        "custom_gains": [0.0] * 10,
        "mono": False,
        "filters": [
            {"id": f"eq_band_{i:02d}", "type": "Peaking",
             "freq": float(f), "gain": float(g), "q": 1.41, "enabled": True}
            for i, (f, g) in enumerate(zip(
                [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000],
                [5, 4, 3, 1, 2, 2, 3, 4, 3, 2],
            ))
        ],
        "compressor": {"enabled": False, "threshold": -20.0, "ratio": 4.0,
                       "attack": 10.0, "release": 100.0, "makeup_gain": 0.0},
        "loudness": {"enabled": False, "high_boost": 5.0, "low_boost": 8.0},
    }))
    file_before = tmp_equalizer_file.read_text()

    # settings.json still has a stale equalizer block (e.g. from a previous boot
    # that crashed before the delete completed).
    _write_settings(settings_service.settings_file, {
        "routing": {"multiroom_enabled": False, "equalizer_effects_enabled": True},
        "equalizer": {"filters": [{"id": "eq_band_00", "gain": 99}], "stale": "garbage"},
    })

    service = CamillaDSPService(settings_service=settings_service)
    await service._load_saved_config()

    # State comes from the file, not the stale block.
    assert [f["gain"] for f in service._filters] == [5, 4, 3, 1, 2, 2, 3, 4, 3, 2]
    # Stale block dropped.
    assert "equalizer" not in _read_settings(settings_service.settings_file)
    # File untouched.
    assert tmp_equalizer_file.read_text() == file_before
