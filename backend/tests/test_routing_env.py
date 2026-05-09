# backend/tests/test_routing_env.py
"""
Unit tests for RoutingEnv / MacEnv / SnapclientEnv regenerate functions.

These tests cover the bug-fixing core of the routing refactor:
- regenerate() never reads os.environ as truth
- regenerate() reads settings.json each call (no class-level caches)
- ROC and snapclient saves never touch routing.env (and vice versa)
- Validation clamps out-of-bounds values
"""
import os
import pytest
from unittest.mock import Mock

from backend.core.multiroom.routing import (
    RoutingEnv,
    MacEnv,
    SnapclientEnv,
    DEFAULT_ROC_CONFIG,
    DEFAULT_SNAPCLIENT_CONFIG,
)


def _read(path):
    with open(path, "r") as f:
        return f.read()


@pytest.fixture
def env_paths(tmp_path, monkeypatch):
    """Redirect the three env files to a temp directory for isolated writes."""
    routing_path = tmp_path / "routing.env"
    mac_path = tmp_path / "mac.env"
    snapclient_path = tmp_path / "snapclient.env"
    monkeypatch.setattr(RoutingEnv, "PATH", str(routing_path))
    monkeypatch.setattr(MacEnv, "PATH", str(mac_path))
    monkeypatch.setattr(SnapclientEnv, "PATH", str(snapclient_path))
    return {"routing": routing_path, "mac": mac_path, "snapclient": snapclient_path}


@pytest.fixture
def fake_settings():
    """Sync-only mock settings service with a backing dict."""
    store = {}

    def get(key):
        cur = store
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return None
            cur = cur[part]
        return cur

    svc = Mock()
    svc.get_setting_sync = Mock(side_effect=get)
    svc._store = store
    return svc


# =============================================================================
# RoutingEnv
# =============================================================================

class TestRoutingEnv:
    def test_regenerate_writes_multiroom_mode(self, env_paths):
        RoutingEnv.regenerate(True)
        content = _read(env_paths["routing"])
        assert "MILO_MODE=multiroom" in content

    def test_regenerate_writes_direct_mode(self, env_paths):
        RoutingEnv.regenerate(False)
        content = _read(env_paths["routing"])
        assert "MILO_MODE=direct" in content

    def test_regenerate_sets_os_environ(self, env_paths, monkeypatch):
        monkeypatch.setenv("MILO_MODE", "stale_value")
        RoutingEnv.regenerate(True)
        assert os.environ["MILO_MODE"] == "multiroom"

    def test_regenerate_does_not_read_os_environ_as_truth(self, env_paths, monkeypatch):
        """Regression for the original Spotify bug: regenerate must derive
        MILO_MODE from its argument alone, never from os.environ."""
        monkeypatch.setenv("MILO_MODE", "direct")
        RoutingEnv.regenerate(True)
        content = _read(env_paths["routing"])
        assert "MILO_MODE=multiroom" in content
        assert os.environ["MILO_MODE"] == "multiroom"

    def test_regenerate_idempotent(self, env_paths):
        RoutingEnv.regenerate(True)
        first = _read(env_paths["routing"])
        RoutingEnv.regenerate(True)
        second = _read(env_paths["routing"])
        assert first == second

    def test_regenerate_only_writes_milo_mode(self, env_paths):
        """routing.env must hold only MILO_MODE — ROC/snapclient vars belong elsewhere."""
        RoutingEnv.regenerate(True)
        content = _read(env_paths["routing"])
        assert "ROC_TARGET_LATENCY" not in content
        assert "ROC_LATENCY_PROFILE" not in content
        assert "ROC_FRAME_LENGTH" not in content
        assert "MILO_SNAPCLIENT_BUFFER_TIME" not in content
        assert "MILO_SNAPCLIENT_FRAGMENTS" not in content
        assert "MILO_SNAPCLIENT_SOUNDCARD" not in content


# =============================================================================
# MacEnv
# =============================================================================

class TestMacEnv:
    def test_regenerate_writes_settings_values(self, env_paths, fake_settings):
        fake_settings._store["mac"] = {
            "target_latency_ms": 100,
            "latency_profile": "gradual",
            "frame_length_ms": 4,
        }
        MacEnv.regenerate(fake_settings)
        content = _read(env_paths["mac"])
        assert "ROC_TARGET_LATENCY=100ms" in content
        assert "ROC_LATENCY_PROFILE=gradual" in content
        assert "ROC_FRAME_LENGTH=4ms" in content

    def test_regenerate_falls_back_to_defaults_when_missing(self, env_paths, fake_settings):
        MacEnv.regenerate(fake_settings)
        content = _read(env_paths["mac"])
        assert f"ROC_TARGET_LATENCY={DEFAULT_ROC_CONFIG['target_latency_ms']}ms" in content
        assert f"ROC_LATENCY_PROFILE={DEFAULT_ROC_CONFIG['latency_profile']}" in content
        assert f"ROC_FRAME_LENGTH={DEFAULT_ROC_CONFIG['frame_length_ms']}ms" in content

    def test_regenerate_ignores_os_environ(self, env_paths, fake_settings, monkeypatch):
        """Even with MILO_MODE=direct in env, MacEnv reads settings, not env."""
        monkeypatch.setenv("MILO_MODE", "direct")
        fake_settings._store["mac"] = {
            "target_latency_ms": 250,
            "latency_profile": "intact",
            "frame_length_ms": 8,
        }
        MacEnv.regenerate(fake_settings)
        content = _read(env_paths["mac"])
        assert "ROC_TARGET_LATENCY=250ms" in content
        assert "ROC_LATENCY_PROFILE=intact" in content
        assert "ROC_FRAME_LENGTH=8ms" in content

    def test_regenerate_clamps_target_latency_high(self, env_paths, fake_settings):
        fake_settings._store["mac"] = {"target_latency_ms": 9999}
        MacEnv.regenerate(fake_settings)
        content = _read(env_paths["mac"])
        assert "ROC_TARGET_LATENCY=500ms" in content

    def test_regenerate_clamps_target_latency_low(self, env_paths, fake_settings):
        fake_settings._store["mac"] = {"target_latency_ms": 1}
        MacEnv.regenerate(fake_settings)
        content = _read(env_paths["mac"])
        assert "ROC_TARGET_LATENCY=5ms" in content

    def test_regenerate_rejects_invalid_profile(self, env_paths, fake_settings):
        fake_settings._store["mac"] = {"latency_profile": "bogus"}
        MacEnv.regenerate(fake_settings)
        content = _read(env_paths["mac"])
        assert f"ROC_LATENCY_PROFILE={DEFAULT_ROC_CONFIG['latency_profile']}" in content

    def test_regenerate_rejects_invalid_frame_length(self, env_paths, fake_settings):
        fake_settings._store["mac"] = {"frame_length_ms": 99}
        MacEnv.regenerate(fake_settings)
        content = _read(env_paths["mac"])
        assert f"ROC_FRAME_LENGTH={DEFAULT_ROC_CONFIG['frame_length_ms']}ms" in content

    def test_regenerate_does_not_touch_routing_env(self, env_paths, fake_settings):
        """Regression for the original Spotify bug: changing ROC settings must
        NOT rewrite routing.env / flip MILO_MODE."""
        RoutingEnv.regenerate(True)  # routing.env exists, MILO_MODE=multiroom
        before = _read(env_paths["routing"])

        fake_settings._store["mac"] = {"target_latency_ms": 123}
        MacEnv.regenerate(fake_settings)

        after = _read(env_paths["routing"])
        assert before == after
        assert "MILO_MODE=multiroom" in after

    def test_regenerate_idempotent(self, env_paths, fake_settings):
        fake_settings._store["mac"] = {
            "target_latency_ms": 150,
            "latency_profile": "responsive",
            "frame_length_ms": 7,
        }
        MacEnv.regenerate(fake_settings)
        first = _read(env_paths["mac"])
        MacEnv.regenerate(fake_settings)
        second = _read(env_paths["mac"])
        assert first == second


# =============================================================================
# SnapclientEnv
# =============================================================================

class TestSnapclientEnv:
    def test_regenerate_writes_settings_values(self, env_paths, fake_settings):
        fake_settings._store["multiroom"] = {
            "snapclient_buffer_time": 120,
            "snapclient_fragments": 6,
        }
        SnapclientEnv.regenerate(fake_settings)
        content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_BUFFER_TIME=120" in content
        assert "MILO_SNAPCLIENT_FRAGMENTS=6" in content

    def test_regenerate_falls_back_to_defaults(self, env_paths, fake_settings):
        SnapclientEnv.regenerate(fake_settings)
        content = _read(env_paths["snapclient"])
        assert f"MILO_SNAPCLIENT_BUFFER_TIME={DEFAULT_SNAPCLIENT_CONFIG['buffer_time']}" in content
        assert f"MILO_SNAPCLIENT_FRAGMENTS={DEFAULT_SNAPCLIENT_CONFIG['fragments']}" in content

    def test_regenerate_ignores_os_environ(self, env_paths, fake_settings, monkeypatch):
        monkeypatch.setenv("MILO_MODE", "direct")
        fake_settings._store["multiroom"] = {
            "snapclient_buffer_time": 90,
            "snapclient_fragments": 3,
        }
        SnapclientEnv.regenerate(fake_settings)
        content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_BUFFER_TIME=90" in content
        assert "MILO_SNAPCLIENT_FRAGMENTS=3" in content

    def test_regenerate_clamps_buffer_high(self, env_paths, fake_settings):
        fake_settings._store["multiroom"] = {"snapclient_buffer_time": 9999}
        SnapclientEnv.regenerate(fake_settings)
        content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_BUFFER_TIME=200" in content

    def test_regenerate_clamps_buffer_low(self, env_paths, fake_settings):
        fake_settings._store["multiroom"] = {"snapclient_buffer_time": 1}
        SnapclientEnv.regenerate(fake_settings)
        content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_BUFFER_TIME=10" in content

    def test_regenerate_clamps_fragments(self, env_paths, fake_settings):
        fake_settings._store["multiroom"] = {"snapclient_fragments": 99}
        SnapclientEnv.regenerate(fake_settings)
        content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_FRAGMENTS=8" in content

    def test_regenerate_does_not_touch_routing_env(self, env_paths, fake_settings):
        RoutingEnv.regenerate(True)
        before = _read(env_paths["routing"])

        fake_settings._store["multiroom"] = {"snapclient_buffer_time": 50}
        SnapclientEnv.regenerate(fake_settings)

        after = _read(env_paths["routing"])
        assert before == after
        assert "MILO_MODE=multiroom" in after

    def test_regenerate_idempotent(self, env_paths, fake_settings):
        fake_settings._store["multiroom"] = {
            "snapclient_buffer_time": 100,
            "snapclient_fragments": 5,
        }
        SnapclientEnv.regenerate(fake_settings)
        first = _read(env_paths["snapclient"])
        SnapclientEnv.regenerate(fake_settings)
        second = _read(env_paths["snapclient"])
        assert first == second


# =============================================================================
# Cross-class isolation
# =============================================================================

class TestEnvIsolation:
    def test_each_regenerate_writes_only_its_file(self, env_paths, fake_settings):
        """Each regenerate touches one and only one file."""
        # Initial: write all three
        RoutingEnv.regenerate(False)
        MacEnv.regenerate(fake_settings)
        SnapclientEnv.regenerate(fake_settings)

        routing_initial = _read(env_paths["routing"])
        mac_initial = _read(env_paths["mac"])
        snapclient_initial = _read(env_paths["snapclient"])

        # Toggling routing only changes routing.env
        RoutingEnv.regenerate(True)
        assert _read(env_paths["routing"]) != routing_initial
        assert _read(env_paths["mac"]) == mac_initial
        assert _read(env_paths["snapclient"]) == snapclient_initial

        # Changing mac settings only changes mac.env
        routing_after_toggle = _read(env_paths["routing"])
        fake_settings._store["mac"] = {"target_latency_ms": 250}
        MacEnv.regenerate(fake_settings)
        assert _read(env_paths["routing"]) == routing_after_toggle
        assert _read(env_paths["mac"]) != mac_initial
        assert _read(env_paths["snapclient"]) == snapclient_initial

        # Changing snapclient settings only changes snapclient.env
        mac_after_change = _read(env_paths["mac"])
        fake_settings._store["multiroom"] = {"snapclient_buffer_time": 150}
        SnapclientEnv.regenerate(fake_settings)
        assert _read(env_paths["routing"]) == routing_after_toggle
        assert _read(env_paths["mac"]) == mac_after_change
        assert _read(env_paths["snapclient"]) != snapclient_initial
