# backend/tests/test_core_equalizer.py
"""
Unit tests for core/equalizer module.

Tests cover:
- CamillaDSPService
- EqualizerClientProxyService
- Presets
"""
import pytest
from unittest.mock import Mock, AsyncMock, MagicMock

from backend.core.equalizer import (
    CamillaDSPService,
    CamillaDspState,
    EqualizerClientProxyService,
    get_builtin_presets,
    get_preset_by_id,
    DEFAULT_CUSTOM_GAINS,
    BUILTIN_PRESETS,
    is_ip_address,
)
from backend.core.equalizer.client_proxy import SatelliteUnreachable
from backend.core.multiroom.models import EqFilter, EqualizerSettings, FilterType


# =============================================================================
# Presets Tests
# =============================================================================

class TestPresets:
    """Test preset functions"""

    def test_get_builtin_presets_returns_list(self):
        """Should return list of presets"""
        presets = get_builtin_presets()
        assert isinstance(presets, list)
        assert len(presets) > 0

    def test_builtin_presets_have_required_keys(self):
        """Each preset should have id and gains"""
        for preset in BUILTIN_PRESETS:
            assert "id" in preset
            assert "gains" in preset
            assert len(preset["gains"]) == 10

    def test_get_preset_by_id_found(self):
        """Should find preset by ID"""
        preset = get_preset_by_id("acoustic")
        assert preset is not None
        assert preset["id"] == "acoustic"

    def test_get_preset_by_id_not_found(self):
        """Should return None for unknown preset"""
        preset = get_preset_by_id("nonexistent")
        assert preset is None

    def test_default_custom_gains_flat(self):
        """Default custom gains should be flat (all zeros)"""
        assert DEFAULT_CUSTOM_GAINS == [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    def test_preset_gains_within_range(self):
        """All preset gains should be within -15 to +15 dB"""
        for preset in BUILTIN_PRESETS:
            for gain in preset["gains"]:
                assert -15 <= gain <= 15, f"Preset {preset['id']} has out-of-range gain: {gain}"


# =============================================================================
# is_ip_address Tests
# =============================================================================

class TestIsIpAddress:
    """Test IP address detection"""

    def test_ipv4_address(self):
        """Should detect IPv4 addresses"""
        assert is_ip_address("192.168.1.1") is True
        assert is_ip_address("10.0.0.1") is True
        assert is_ip_address("127.0.0.1") is True

    def test_ipv6_address(self):
        """Should detect IPv6 addresses"""
        assert is_ip_address("::1") is True
        assert is_ip_address("2001:db8::1") is True

    def test_hostname_not_ip(self):
        """Should return False for hostnames"""
        assert is_ip_address("milo") is False
        assert is_ip_address("milo-client-1") is False
        assert is_ip_address("localhost") is False


# =============================================================================
# EqualizerClientProxyService Tests
# =============================================================================

class TestEqualizerClientProxyService:
    """Test Equalizer client proxy service"""

    @pytest.fixture
    def proxy_service(self):
        """Create proxy service instance"""
        return EqualizerClientProxyService()

    def test_get_host_with_ip(self, proxy_service):
        """Should return IP address as-is"""
        assert proxy_service._get_host("192.168.1.100") == "192.168.1.100"

    def test_get_host_with_hostname(self, proxy_service):
        """Should add .local suffix to hostname"""
        assert proxy_service._get_host("milo-client-1") == "milo-client-1.local"


class TestApplyRecord:
    """The wire shape of a whole EQ record reaching a satellite.

    Every path that pushes a full record — the live write, the reconnection sync,
    the pending replay — goes through apply_record, so these are the invariants
    for all three at once. When they held only on some paths, the satellite and
    the server's record disagreed until the next reconnect: the failure behind
    six `fix(equalizer)` commits.
    """

    @pytest.fixture
    def proxy_service(self):
        proxy = EqualizerClientProxyService()
        proxy.request = AsyncMock(return_value={"status": "success"})
        return proxy

    @pytest.fixture
    def record(self):
        return EqualizerSettings(
            enabled=False,
            filters=[
                EqFilter(id="eq_band_00", frequency=100, gain=3.0, q=1.41,
                         filter_type=FilterType.PEAKING, enabled=True),
            ],
            mono=True,
        )

    def _sent(self, proxy_service):
        """[(path, body)] in the order they were pushed."""
        return [(c.args[2], c.args[3]) for c in proxy_service.request.await_args_list]

    @pytest.mark.asyncio
    async def test_master_bypass_lands_last(self, proxy_service, record):
        """On the satellite the bypass and per-band pipeline membership are the
        same mechanism, so the gate has to be applied after what it gates."""
        assert await proxy_service.apply_record("192.168.1.100", record) is True

        paths = [path for path, _ in self._sent(proxy_service)]
        assert paths[-1] == "/equalizer/enabled"
        assert set(paths[:-1]) == {
            "/equalizer/filters", "/equalizer/compressor",
            "/equalizer/loudness", "/equalizer/mono",
        }

    @pytest.mark.asyncio
    async def test_the_whole_record_travels(self, proxy_service, record):
        assert await proxy_service.apply_record("192.168.1.100", record) is True
        sent = dict(self._sent(proxy_service))

        assert sent["/equalizer/enabled"] == {"enabled": False}
        assert sent["/equalizer/mono"] == {"enabled": True}
        assert sent["/equalizer/compressor"] == record.compressor.to_dict()
        assert sent["/equalizer/loudness"] == record.loudness.to_dict()

    @pytest.mark.asyncio
    async def test_bands_carry_tuning_only(self, proxy_service, record):
        """A band carrying `enabled` would re-pipe it on a bypassed client."""
        await proxy_service.apply_record("192.168.1.100", record)

        bands = dict(self._sent(proxy_service))["/equalizer/filters"]["filters"]
        assert bands, "no bands pushed — the assertion below would be vacuous"
        assert all(set(b) == {"id", "gain", "freq", "q", "filter_type"} for b in bands)

    @pytest.mark.asyncio
    async def test_an_unreachable_satellite_is_a_failure_not_a_raise(self, proxy_service, record):
        """Callers own the retry policy (requeue as pending), so this reports."""
        proxy_service.request.side_effect = SatelliteUnreachable("192.168.1.100", "down")

        assert await proxy_service.apply_record("192.168.1.100", record) is False


# =============================================================================
# CamillaDSPService Tests
# =============================================================================

class TestCamillaDSPService:
    """Test CamillaDSP service"""

    @pytest.fixture
    def mock_settings_service(self):
        """Create mock settings service"""
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        return settings

    @pytest.fixture
    def camilladsp_service(self, mock_settings_service):
        """Create Equalizer service instance"""
        return CamillaDSPService(
            settings_service=mock_settings_service
        )

    def test_initial_state_disconnected(self, camilladsp_service):
        """Should start in disconnected state"""
        assert camilladsp_service.state == CamillaDspState.DISCONNECTED
        assert camilladsp_service.connected is False

    def test_default_host_port(self, camilladsp_service):
        """Should use default host and port"""
        assert camilladsp_service.host == "127.0.0.1"
        assert camilladsp_service.port == 1234

    def test_custom_host_port(self, mock_settings_service):
        """Should accept custom host and port"""
        service = CamillaDSPService(
            settings_service=mock_settings_service,
            host="192.168.1.100",
            port=5678
        )
        assert service.host == "192.168.1.100"
        assert service.port == 5678

    def test_set_state_machine(self, camilladsp_service):
        """Should set state machine reference"""
        mock_state_machine = Mock()
        camilladsp_service.set_state_machine(mock_state_machine)
        assert camilladsp_service.state_machine == mock_state_machine

    def test_is_volume_control_available_disconnected(self, camilladsp_service):
        """Should return False when disconnected"""
        assert camilladsp_service.is_volume_control_available() is False

    def test_initial_compressor_settings(self, camilladsp_service):
        """Should have default compressor settings"""
        assert camilladsp_service._compressor["enabled"] is False
        assert camilladsp_service._compressor["threshold"] == -20.0
        assert camilladsp_service._compressor["ratio"] == 4.0

    def test_initial_loudness_settings(self, camilladsp_service):
        """Should have default loudness settings"""
        assert camilladsp_service._loudness["enabled"] is False
        assert camilladsp_service._loudness["high_boost"] == 5.0
        assert camilladsp_service._loudness["low_boost"] == 8.0

    def test_initial_volume_settings(self, camilladsp_service):
        """Should have default volume settings"""
        assert camilladsp_service._volume["main"] == 0.0
        assert camilladsp_service._volume["mute"] is False

    def test_get_equalizer_settings_snapshots_local_cache(self, camilladsp_service):
        """get_equalizer_settings() returns the local client's full EQ record from cache."""
        from backend.core.multiroom.models import EqualizerSettings, EqFilter

        camilladsp_service._effects_enabled = True
        camilladsp_service._mono = True
        camilladsp_service._active_preset = "rock"
        camilladsp_service._filters[0].update({"gain": 4.5, "freq": 31, "type": "Peaking", "q": 1.41})

        eq = camilladsp_service.get_equalizer_settings()

        assert isinstance(eq, EqualizerSettings)
        assert eq.enabled is True  # mirrors _effects_enabled
        assert eq.mono is True
        assert eq.active_preset == "rock"
        assert len(eq.filters) == 10
        assert all(isinstance(f, EqFilter) for f in eq.filters)
        assert eq.filters[0].gain == 4.5
        assert eq.filters[0].frequency == 31

    @pytest.mark.asyncio
    async def test_persist_state_flushes_immediately(self, camilladsp_service, monkeypatch):
        """persist_state() writes the full state now (cancels the debounce, no wait)."""
        calls = []

        async def fake_async():
            calls.append(True)

        monkeypatch.setattr(camilladsp_service, "_persist_state_async", fake_async)
        await camilladsp_service.persist_state()
        assert calls == [True]

    @pytest.mark.asyncio
    async def test_update_cache_overwrites_cache_and_persists_while_disconnected(
        self, camilladsp_service, monkeypatch
    ):
        """update_cache() unconditionally overwrites the in-memory cache from an
        EqualizerSettings record and persists — even while DISCONNECTED. Used on the
        disconnected local path so the intent survives to reconnect, where
        restore_effects() re-pushes it. It must NOT touch _effects_enabled (the
        master toggle owned by routing/settings.json)."""
        from backend.core.multiroom.models import (
            EqualizerSettings, EqFilter, CompressorSettings, LoudnessSettings,
            FilterType, DEFAULT_EQ_FREQUENCIES,
        )
        persisted = []

        async def fake_async():
            persisted.append(True)

        monkeypatch.setattr(camilladsp_service, "_persist_state_async", fake_async)

        assert camilladsp_service.connected is False  # disconnected by default
        camilladsp_service._effects_enabled = True  # sentinel: must stay untouched

        settings = EqualizerSettings(
            enabled=False,
            filters=[
                EqFilter(id=f"eq_band_{i:02d}", frequency=DEFAULT_EQ_FREQUENCIES[i],
                         gain=float(i), q=1.41, filter_type=FilterType.PEAKING)
                for i in range(10)
            ],
            compressor=CompressorSettings(enabled=True, threshold=-25.0),
            loudness=LoudnessSettings(enabled=True, low_boost=10.0),
            active_preset="rock",
            mono=True,
            custom_gains=[1.0] * 10,
        )

        await camilladsp_service.update_cache(settings)

        assert camilladsp_service._mono is True
        assert camilladsp_service._active_preset == "rock"
        assert camilladsp_service._compressor["enabled"] is True
        assert camilladsp_service._compressor["threshold"] == -25.0
        assert camilladsp_service._loudness["enabled"] is True
        assert camilladsp_service._loudness["low_boost"] == 10.0
        assert camilladsp_service._custom_gains == [1.0] * 10
        assert camilladsp_service._filters[5]["gain"] == 5.0
        assert camilladsp_service._filters[0]["id"] == "eq_band_00"
        assert camilladsp_service._filters[0]["freq"] == DEFAULT_EQ_FREQUENCIES[0]
        # Master toggle is owned elsewhere — update_cache must not clobber it.
        assert camilladsp_service._effects_enabled is True
        # Intent persisted to equalizer.json.
        assert persisted == [True]

    @pytest.mark.asyncio
    async def test_load_saved_config_restores_local_eq_at_boot(self, camilladsp_service, tmp_path):
        """At boot the local client's EQ is restored from equalizer.json into the
        in-memory cache (the DAC source of truth). This is the LOCAL client's boot
        restoration — independent of multiroom and the websocket re-sync, so a
        restart never reverts the local EQ. (Regression lock for the design's core
        guarantee.)"""
        from backend.shared.persistence import save_versioned_json
        path = tmp_path / "equalizer.json"
        await save_versioned_json(path, {
            "active_preset": "rock",
            "mono": True,
            "custom_gains": [2.0] * 10,
            "filters": [{"id": "eq_band_00", "type": "Peaking", "freq": 31, "gain": 3.5, "q": 1.41, "enabled": True}],
            "compressor": {"enabled": True, "threshold": -25.0, "ratio": 4.0, "attack": 10.0, "release": 100.0, "makeup_gain": 0.0},
            "loudness": {"enabled": True, "high_boost": 6.0, "low_boost": 9.0},
        }, camilladsp_service.SCHEMA_VERSION)
        camilladsp_service.STORAGE_PATH = path

        await camilladsp_service._load_saved_config()

        assert camilladsp_service._active_preset == "rock"
        assert camilladsp_service._mono is True
        assert camilladsp_service._custom_gains == [2.0] * 10
        assert camilladsp_service._filters[0]["gain"] == 3.5
        assert camilladsp_service._compressor["enabled"] is True
        assert camilladsp_service._compressor["threshold"] == -25.0
        assert camilladsp_service._loudness["high_boost"] == 6.0

    @pytest.mark.asyncio
    async def test_disconnected_set_then_reconnect_restores_eq(self, camilladsp_service, monkeypatch):
        """End-to-end of the disconnected→reconnect window: update_cache captures the
        intent while CamillaDSP is DISCONNECTED; on reconnect restore_effects() pushes
        those exact cache values to the daemon. Proves equalizer.json never drifts from
        the live DSP across the boot/reconnect window (carried-over Phase 1 fix)."""
        from backend.core.multiroom.models import (
            EqualizerSettings, EqFilter, FilterType, DEFAULT_EQ_FREQUENCIES,
        )
        monkeypatch.setattr(camilladsp_service, "_persist_state_async", AsyncMock())

        settings = EqualizerSettings(
            filters=[
                EqFilter(id=f"eq_band_{i:02d}", frequency=DEFAULT_EQ_FREQUENCIES[i],
                         gain=4.0 if i == 0 else 0.0, q=1.41, filter_type=FilterType.PEAKING)
                for i in range(10)
            ],
            active_preset="custom",
        )

        # 1. Disconnected: capture the intent into the cache + equalizer.json.
        assert camilladsp_service.connected is False
        await camilladsp_service.update_cache(settings)

        # 2. Reconnect: restore_effects() re-pushes the cache to the daemon.
        camilladsp_service._connected = True
        camilladsp_service._client = MagicMock()
        captured = {}

        async def fake_get_config():
            return {"filters": {}, "pipeline": [], "processors": {}}

        async def fake_set_config(cfg):
            captured["cfg"] = cfg

        monkeypatch.setattr(camilladsp_service, "_get_config", fake_get_config)
        monkeypatch.setattr(camilladsp_service, "_set_config", fake_set_config)

        ok = await camilladsp_service.restore_effects()

        assert ok is True
        assert captured["cfg"]["filters"]["eq_band_00"]["parameters"]["gain"] == 4.0

    @pytest.mark.asyncio
    async def test_get_volume_disconnected(self, camilladsp_service):
        """Should return cached volume when disconnected"""
        volume = await camilladsp_service.get_volume()
        assert volume == {"main": 0.0, "mute": False}

    @pytest.mark.asyncio
    async def test_get_filters_disconnected(self, camilladsp_service):
        """Should return the in-memory default bands when disconnected."""
        filters = await camilladsp_service.get_filters()
        # 10 default flat bands are initialized in __init__ so the API never
        # returns an empty list during the post-restart sync window.
        assert len(filters) == 10
        assert all(f["gain"] == 0.0 for f in filters)
        assert [f["id"] for f in filters] == [f"eq_band_{i:02d}" for i in range(10)]

    @pytest.mark.asyncio
    async def test_set_filter_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.set_filter("eq_band_00", 100, 0, 1.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_volume_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.set_volume(-20)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_mute_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.set_mute(True)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_compressor_disconnected(self, camilladsp_service):
        """Should fail when disconnected without updating cache"""
        result = await camilladsp_service.set_compressor(enabled=True, threshold=-30)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_loudness_disconnected(self, camilladsp_service):
        """Should fail when disconnected without updating cache"""
        result = await camilladsp_service.set_loudness(enabled=True, low_boost=10.0)
        assert result is False

    @pytest.mark.asyncio
    async def test_get_status_disconnected(self, camilladsp_service):
        """Should return disconnected status"""
        status = await camilladsp_service.get_status()
        assert status["available"] is False
        assert status["state"] == CamillaDspState.DISCONNECTED.value

    @pytest.mark.asyncio
    async def test_get_levels_disconnected(self, camilladsp_service):
        """Should return unavailable when disconnected"""
        levels = await camilladsp_service.get_levels()
        assert levels["available"] is False

    @pytest.mark.asyncio
    async def test_set_crossover_filter_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.set_crossover_filter(enabled=True)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_lowpass_filter_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.set_lowpass_filter(enabled=True)
        assert result is False

    def test_get_presets(self, camilladsp_service):
        """Should return builtin presets"""
        presets = camilladsp_service.get_presets()
        assert len(presets) == len(BUILTIN_PRESETS)

    @pytest.mark.asyncio
    async def test_get_active_preset_none(self, camilladsp_service):
        """Should return None when no preset active"""
        preset = await camilladsp_service.get_active_preset()
        assert preset is None

    @pytest.mark.asyncio
    async def test_get_custom_gains_default(self, camilladsp_service):
        """Should return default gains when none saved"""
        gains = await camilladsp_service.get_custom_gains()
        assert gains == DEFAULT_CUSTOM_GAINS

    @pytest.mark.asyncio
    async def test_bypass_effects_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.bypass_effects()
        assert result is False

    @pytest.mark.asyncio
    async def test_restore_effects_disconnected(self, camilladsp_service):
        """Should fail when disconnected"""
        result = await camilladsp_service.restore_effects()
        assert result is False

    @pytest.mark.asyncio
    async def test_wait_for_connection_timeout(self, camilladsp_service):
        """Should timeout when not connected"""
        result = await camilladsp_service.wait_for_connection(timeout=0.1)
        assert result is False

    @pytest.mark.asyncio
    async def test_concurrent_rmw_does_not_clobber(self, camilladsp_service, monkeypatch):
        """A filter drag and a compressor toggle issued concurrently must BOTH land.

        The daemon graph is read-modify-written per caller; without serialization the
        two interleave (each reads the pre-change graph, the second write clobbers the
        first → last-writer-wins). `_config_lock` makes each RMW atomic so both survive.
        The fake config I/O yields between read and write to force the interleave that
        would clobber if the lock were absent.
        """
        import asyncio
        import copy

        camilladsp_service._connected = True
        camilladsp_service._client = MagicMock()
        monkeypatch.setattr(camilladsp_service, "_schedule_persist", lambda: None)
        monkeypatch.setattr(camilladsp_service, "_broadcast", AsyncMock())

        daemon = {
            "filters": {"eq_band_00": {"type": "Biquad", "parameters": {
                "type": "Peaking", "freq": 31.0, "gain": 0.0, "q": 1.41}}},
            "pipeline": [],
            "processors": {},
        }

        async def fake_get_config():
            await asyncio.sleep(0)  # yield between read and write
            return copy.deepcopy(daemon)

        async def fake_set_config(cfg):
            await asyncio.sleep(0)
            daemon.clear()
            daemon.update(copy.deepcopy(cfg))

        monkeypatch.setattr(camilladsp_service, "_get_config", fake_get_config)
        monkeypatch.setattr(camilladsp_service, "_set_config", fake_set_config)

        await asyncio.gather(
            camilladsp_service.set_filter("eq_band_00", 31.0, 6.0, 1.41),
            camilladsp_service.set_compressor(enabled=True),
        )

        assert daemon["filters"]["eq_band_00"]["parameters"]["gain"] == 6.0  # filter change kept
        assert "compressor" in daemon["processors"]  # compressor change kept

    @pytest.mark.asyncio
    async def test_apply_settings_single_round_trip(self, camilladsp_service, monkeypatch):
        """A full 10-band record applies in ONE set_config, not 13 sequential RMWs."""
        import asyncio  # noqa: F401 -- parity with sibling tests; kept for clarity
        from backend.core.multiroom.models import (
            EqualizerSettings, EqFilter, FilterType, DEFAULT_EQ_FREQUENCIES,
        )

        camilladsp_service._connected = True
        camilladsp_service._client = MagicMock()
        monkeypatch.setattr(camilladsp_service, "_schedule_persist", lambda: None)
        monkeypatch.setattr(camilladsp_service, "_broadcast", AsyncMock())

        captured = {}
        set_calls = 0

        async def fake_get_config():
            return {"filters": {}, "pipeline": [], "processors": {}}

        async def fake_set_config(cfg):
            nonlocal set_calls
            set_calls += 1
            captured["cfg"] = cfg

        monkeypatch.setattr(camilladsp_service, "_get_config", fake_get_config)
        monkeypatch.setattr(camilladsp_service, "_set_config", fake_set_config)

        settings = EqualizerSettings(
            filters=[
                EqFilter(id=f"eq_band_{i:02d}", frequency=DEFAULT_EQ_FREQUENCIES[i],
                         gain=float(i), q=1.41, filter_type=FilterType.PEAKING)
                for i in range(10)
            ],
            active_preset="custom",
        )
        settings.compressor.enabled = True
        settings.loudness.enabled = True

        ok = await camilladsp_service.apply_settings(settings, persist=False)

        assert ok is True
        assert set_calls == 1  # one graph write for the whole record (was 13)
        cfg = captured["cfg"]
        assert cfg["filters"]["eq_band_09"]["parameters"]["gain"] == 9.0
        assert "compressor" in cfg["processors"]
        assert "loudness_low" in cfg["filters"]
        assert camilladsp_service._active_preset == "custom"

    @pytest.mark.asyncio
    async def test_apply_settings_rolls_back_caches_on_daemon_failure(self, camilladsp_service, monkeypatch):
        """A failed daemon write must leave the caches unchanged, not ahead of the DSP.

        Otherwise the in-memory cache (and the next persist to equalizer.json,
        plus the zone WS broadcast) would report the new EQ while the daemon
        keeps playing the old one.
        """
        from backend.core.multiroom.models import (
            EqualizerSettings, EqFilter, FilterType, DEFAULT_EQ_FREQUENCIES,
        )

        camilladsp_service._connected = True
        camilladsp_service._client = MagicMock()
        monkeypatch.setattr(camilladsp_service, "_schedule_persist", lambda: None)

        before = (
            camilladsp_service._filters,
            camilladsp_service._compressor,
            camilladsp_service._loudness,
            camilladsp_service._mono,
            camilladsp_service._active_preset,
        )

        async def fake_get_config():
            return {"filters": {}, "pipeline": [], "processors": {}}

        async def failing_set_config(cfg):
            raise RuntimeError("daemon connection dropped")

        monkeypatch.setattr(camilladsp_service, "_get_config", fake_get_config)
        monkeypatch.setattr(camilladsp_service, "_set_config", failing_set_config)

        settings = EqualizerSettings(
            filters=[
                EqFilter(id=f"eq_band_{i:02d}", frequency=DEFAULT_EQ_FREQUENCIES[i],
                         gain=99.0, q=1.41, filter_type=FilterType.PEAKING)
                for i in range(10)
            ],
            active_preset="custom",
        )
        settings.compressor.enabled = True
        settings.mono = True

        ok = await camilladsp_service.apply_settings(settings, persist=False)

        assert ok is False  # @handle_errors(default=False) swallows the re-raise
        # Caches restored to exactly the pre-apply objects — no drift from the daemon.
        assert camilladsp_service._filters is before[0]
        assert camilladsp_service._compressor is before[1]
        assert camilladsp_service._loudness is before[2]
        assert camilladsp_service._mono == before[3]
        assert camilladsp_service._active_preset == before[4]


# =============================================================================
# Equalizer State Tests
# =============================================================================

class TestCamillaDspState:
    """Test Equalizer state enum"""

    def test_equalizer_states_exist(self):
        """Should have all expected states"""
        assert CamillaDspState.DISCONNECTED.value == "disconnected"
        assert CamillaDspState.INACTIVE.value == "inactive"
        assert CamillaDspState.RUNNING.value == "running"
        assert CamillaDspState.PAUSED.value == "paused"
