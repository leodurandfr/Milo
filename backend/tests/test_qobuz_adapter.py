# backend/tests/test_qobuz_adapter.py
"""The gate that decides whether a qobuz-proxy release can be shipped.

`rootfs/usr/local/bin/milo-qobuz` adds position/duration to the sidecar's status
and holds its stream at unity gain — two things upstream offers no setting for.
Both bind to names, and `--check` is what refuses a release that moved one,
before `_update_qobuz_proxy` restarts the service onto it.

What breaks when this fails: the check answers "moved" for a release that is
fine (every qobuz-proxy update refuses, which is the bug that replaced the
source patches it succeeded), or answers "fine" for one that is not (the sidecar
starts, plays, and reports a progress bar that never moves — no error anywhere).
"""
import types
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-qobuz"


@pytest.fixture(scope="module")
def adapter():
    """The launcher, executed into a module namespace.

    Compiled here rather than imported through a file loader: a loader writes
    `__pycache__` beside the script, inside the tree `sync-system-files` copies
    to the fleet — which is how a .pyc came to be deployed to every unit until
    `test_rootfs_deployment` refused it.
    """
    module = types.ModuleType("milo_qobuz")
    module.__file__ = str(ADAPTER)
    exec(compile(ADAPTER.read_text(encoding="utf-8"), str(ADAPTER), "exec"), module.__dict__)
    return module


def test_the_adapter_is_the_one_the_unit_runs(adapter):
    """Guards the rules below: a stub with no bindings would pass them all."""
    assert ADAPTER.is_file(), "the launcher moved — the unit's ExecStart points at this path"
    assert callable(adapter._mentions_key)
    assert callable(adapter.check)


def test_a_key_parked_in_a_const_tuple_is_found(adapter):
    """Constant dict keys compile to one tuple in `co_consts`, not to loose strings.

    A flat membership test misses every one of them, so the check reports the
    key as renamed on a release that never touched it — measured on 1.5.0, which
    builds `now_playing` and was reported broken.
    """
    def builds_status():
        return {"id": 1, "now_playing": None, "config": {}}

    assert adapter._mentions_key(builds_status, "now_playing") is True
    assert adapter._mentions_key(builds_status, "position_ms") is False


def test_a_key_assembled_at_runtime_is_not_seen(adapter):
    """The limit of the reading, stated: only literals are constants.

    A release that computed its keys would read as having dropped every one of
    them — the check would refuse it, which is the safe direction, but this is
    why it says "re-derive" rather than naming a rename.
    """
    def builds_from_argument(key):
        return {key: None}

    assert adapter._mentions_key(builds_from_argument, "now_playing") is False


@pytest.mark.parametrize("flag,expected", [("1", True), ("0", False), ("", False), (None, False)])
def test_the_app_slider_is_honoured_only_behind_the_flag(adapter, tmp_path, monkeypatch, flag, expected):
    """CamillaDSP is the only attenuation stage unless the setting says otherwise.

    A missing flag file is the normal state — the setting defaults to off — so
    it must read as "not allowed" rather than raise into the audio callback.
    """
    monkeypatch.setenv("QOBUZPROXY_DATA_DIR", str(tmp_path))
    if flag is not None:
        (tmp_path / adapter.VOLUME_FLAG).write_text(flag)

    assert adapter._app_volume_allowed() is expected
