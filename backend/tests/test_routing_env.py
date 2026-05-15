# backend/tests/test_routing_env.py
"""
Unit tests for RoutingEnv / MacEnv / SnapclientEnv regenerate functions.

These tests cover the bug-fixing core of the routing refactor:
- regenerate() never reads os.environ as truth
- regenerate() is a pure function over its arguments (Phase 4: no settings
  service dependency — the caller passes dicts/values)
- ROC and snapclient saves never touch routing.env (and vice versa)
- Validation clamps out-of-bounds values
"""
import os
import pytest

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
    def test_regenerate_writes_settings_values(self, env_paths):
        MacEnv.regenerate({
            "target_latency_ms": 100,
            "latency_profile": "gradual",
            "frame_length_ms": 4,
        })
        content = _read(env_paths["mac"])
        assert "ROC_TARGET_LATENCY=100ms" in content
        assert "ROC_LATENCY_PROFILE=gradual" in content
        assert "ROC_FRAME_LENGTH=4ms" in content

    def test_regenerate_falls_back_to_defaults_when_none(self, env_paths):
        MacEnv.regenerate(None)
        content = _read(env_paths["mac"])
        assert f"ROC_TARGET_LATENCY={DEFAULT_ROC_CONFIG['target_latency_ms']}ms" in content
        assert f"ROC_LATENCY_PROFILE={DEFAULT_ROC_CONFIG['latency_profile']}" in content
        assert f"ROC_FRAME_LENGTH={DEFAULT_ROC_CONFIG['frame_length_ms']}ms" in content

    def test_regenerate_falls_back_to_defaults_when_empty(self, env_paths):
        MacEnv.regenerate({})
        content = _read(env_paths["mac"])
        assert f"ROC_TARGET_LATENCY={DEFAULT_ROC_CONFIG['target_latency_ms']}ms" in content
        assert f"ROC_LATENCY_PROFILE={DEFAULT_ROC_CONFIG['latency_profile']}" in content
        assert f"ROC_FRAME_LENGTH={DEFAULT_ROC_CONFIG['frame_length_ms']}ms" in content

    def test_regenerate_ignores_os_environ(self, env_paths, monkeypatch):
        """Even with MILO_MODE=direct in env, MacEnv is a pure function over its arg."""
        monkeypatch.setenv("MILO_MODE", "direct")
        MacEnv.regenerate({
            "target_latency_ms": 250,
            "latency_profile": "intact",
            "frame_length_ms": 8,
        })
        content = _read(env_paths["mac"])
        assert "ROC_TARGET_LATENCY=250ms" in content
        assert "ROC_LATENCY_PROFILE=intact" in content
        assert "ROC_FRAME_LENGTH=8ms" in content

    def test_regenerate_clamps_target_latency_high(self, env_paths):
        MacEnv.regenerate({"target_latency_ms": 9999})
        content = _read(env_paths["mac"])
        assert "ROC_TARGET_LATENCY=500ms" in content

    def test_regenerate_clamps_target_latency_low(self, env_paths):
        MacEnv.regenerate({"target_latency_ms": 1})
        content = _read(env_paths["mac"])
        assert "ROC_TARGET_LATENCY=20ms" in content

    def test_regenerate_rejects_invalid_profile(self, env_paths):
        MacEnv.regenerate({"latency_profile": "bogus"})
        content = _read(env_paths["mac"])
        assert f"ROC_LATENCY_PROFILE={DEFAULT_ROC_CONFIG['latency_profile']}" in content

    def test_regenerate_rejects_invalid_frame_length(self, env_paths):
        MacEnv.regenerate({"frame_length_ms": 99})
        content = _read(env_paths["mac"])
        assert f"ROC_FRAME_LENGTH={DEFAULT_ROC_CONFIG['frame_length_ms']}ms" in content

    def test_regenerate_does_not_touch_routing_env(self, env_paths):
        """Regression for the original Spotify bug: changing ROC settings must
        NOT rewrite routing.env / flip MILO_MODE."""
        RoutingEnv.regenerate(True)  # routing.env exists, MILO_MODE=multiroom
        before = _read(env_paths["routing"])

        MacEnv.regenerate({"target_latency_ms": 123})

        after = _read(env_paths["routing"])
        assert before == after
        assert "MILO_MODE=multiroom" in after

    def test_regenerate_idempotent(self, env_paths):
        mac_config = {
            "target_latency_ms": 150,
            "latency_profile": "responsive",
            "frame_length_ms": 6,
        }
        MacEnv.regenerate(mac_config)
        first = _read(env_paths["mac"])
        MacEnv.regenerate(mac_config)
        second = _read(env_paths["mac"])
        assert first == second


# =============================================================================
# SnapclientEnv
# =============================================================================

class TestSnapclientEnv:
    def test_regenerate_writes_provided_values(self, env_paths):
        SnapclientEnv.regenerate(120, 6)
        content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_BUFFER_TIME=120" in content
        assert "MILO_SNAPCLIENT_FRAGMENTS=6" in content

    def test_regenerate_falls_back_to_defaults_when_none(self, env_paths):
        SnapclientEnv.regenerate(None, None)
        content = _read(env_paths["snapclient"])
        assert f"MILO_SNAPCLIENT_BUFFER_TIME={DEFAULT_SNAPCLIENT_CONFIG['buffer_time']}" in content
        assert f"MILO_SNAPCLIENT_FRAGMENTS={DEFAULT_SNAPCLIENT_CONFIG['fragments']}" in content

    def test_regenerate_ignores_os_environ(self, env_paths, monkeypatch):
        monkeypatch.setenv("MILO_MODE", "direct")
        SnapclientEnv.regenerate(90, 3)
        content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_BUFFER_TIME=90" in content
        assert "MILO_SNAPCLIENT_FRAGMENTS=3" in content

    def test_regenerate_clamps_buffer_high(self, env_paths):
        SnapclientEnv.regenerate(9999, None)
        content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_BUFFER_TIME=200" in content

    def test_regenerate_clamps_buffer_low(self, env_paths):
        SnapclientEnv.regenerate(1, None)
        content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_BUFFER_TIME=10" in content

    def test_regenerate_clamps_fragments(self, env_paths):
        SnapclientEnv.regenerate(None, 99)
        content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_FRAGMENTS=8" in content

    def test_regenerate_does_not_touch_routing_env(self, env_paths):
        RoutingEnv.regenerate(True)
        before = _read(env_paths["routing"])

        SnapclientEnv.regenerate(50, None)

        after = _read(env_paths["routing"])
        assert before == after
        assert "MILO_MODE=multiroom" in after

    def test_regenerate_idempotent(self, env_paths):
        SnapclientEnv.regenerate(100, 5)
        first = _read(env_paths["snapclient"])
        SnapclientEnv.regenerate(100, 5)
        second = _read(env_paths["snapclient"])
        assert first == second


# =============================================================================
# Cross-class isolation
# =============================================================================

class TestEnvIsolation:
    def test_each_regenerate_writes_only_its_file(self, env_paths):
        """Each regenerate touches one and only one file."""
        # Initial: write all three
        RoutingEnv.regenerate(False)
        MacEnv.regenerate({})
        SnapclientEnv.regenerate(None, None)

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
        MacEnv.regenerate({"target_latency_ms": 250})
        assert _read(env_paths["routing"]) == routing_after_toggle
        assert _read(env_paths["mac"]) != mac_initial
        assert _read(env_paths["snapclient"]) == snapclient_initial

        # Changing snapclient settings only changes snapclient.env
        mac_after_change = _read(env_paths["mac"])
        SnapclientEnv.regenerate(150, None)
        assert _read(env_paths["routing"]) == routing_after_toggle
        assert _read(env_paths["mac"]) == mac_after_change
        assert _read(env_paths["snapclient"]) != snapclient_initial


# =============================================================================
# AudioRoutingService.regenerate_env_files (Phase 4 bootstrap helper)
# =============================================================================

class TestRegenerateEnvFilesBootstrap:
    """Tests for the consolidated bootstrap helper that derives all three
    env files from settings.json in a single sync pass."""

    def _make_settings(self, storage):
        """Build a Mock settings service whose get_setting_sync reads from `storage`."""
        from unittest.mock import Mock

        def get(key):
            return storage.get(key)

        svc = Mock()
        svc.get_setting_sync = Mock(side_effect=get)
        return svc

    def test_writes_all_three_files_from_settings(self, env_paths):
        """Single call regenerates routing.env / mac.env / snapclient.env."""
        from backend.core.multiroom import AudioRoutingService

        settings = self._make_settings({
            'routing.multiroom_enabled': True,
            'mac': {
                "target_latency_ms": 150,
                "latency_profile": "gradual",
                "frame_length_ms": 4,
            },
            'multiroom.snapclient_buffer_time': 90,
            'multiroom.snapclient_fragments': 5,
        })
        service = AudioRoutingService(settings_service=settings)

        service.regenerate_env_files()

        assert "MILO_MODE=multiroom" in _read(env_paths["routing"])
        mac_content = _read(env_paths["mac"])
        assert "ROC_TARGET_LATENCY=150ms" in mac_content
        assert "ROC_LATENCY_PROFILE=gradual" in mac_content
        snap_content = _read(env_paths["snapclient"])
        assert "MILO_SNAPCLIENT_BUFFER_TIME=90" in snap_content
        assert "MILO_SNAPCLIENT_FRAGMENTS=5" in snap_content

    def test_writes_defaults_when_settings_missing_keys(self, env_paths):
        """Empty settings (no `routing`/`mac`/`multiroom` blocks) yields validated defaults.

        Regression for Defect 5 in the desync plan: older installs without
        the `routing` block must resolve to `MILO_MODE=direct`, not crash.
        """
        from backend.core.multiroom import AudioRoutingService

        settings = self._make_settings({})  # all reads return None
        service = AudioRoutingService(settings_service=settings)

        service.regenerate_env_files()

        assert "MILO_MODE=direct" in _read(env_paths["routing"])
        mac_content = _read(env_paths["mac"])
        assert f"ROC_TARGET_LATENCY={DEFAULT_ROC_CONFIG['target_latency_ms']}ms" in mac_content
        assert f"ROC_LATENCY_PROFILE={DEFAULT_ROC_CONFIG['latency_profile']}" in mac_content
        snap_content = _read(env_paths["snapclient"])
        assert f"MILO_SNAPCLIENT_BUFFER_TIME={DEFAULT_SNAPCLIENT_CONFIG['buffer_time']}" in snap_content
        assert f"MILO_SNAPCLIENT_FRAGMENTS={DEFAULT_SNAPCLIENT_CONFIG['fragments']}" in snap_content

    def test_writes_defaults_when_no_settings_service(self, env_paths):
        """No settings service injected — helper still writes safe defaults."""
        from backend.core.multiroom import AudioRoutingService

        service = AudioRoutingService(settings_service=None)

        service.regenerate_env_files()

        assert "MILO_MODE=direct" in _read(env_paths["routing"])
        assert os.path.exists(env_paths["mac"])
        assert os.path.exists(env_paths["snapclient"])
