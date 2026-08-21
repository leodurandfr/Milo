# backend/tests/test_core_equalizer.py
"""
Unit tests for core/equalizer module.

Tests cover:
- CamillaDSPService
- EqualizerClientProxyService
- Presets
"""
import asyncio
import logging

import aiohttp
import pytest
from unittest.mock import Mock, AsyncMock

from backend.config.constants import CLIENT_API_PORT
from backend.core.equalizer import (
    CamillaDSPService,
    CamillaDspState,
    EqualizerClientProxyService,
    get_builtin_presets,
    get_preset_by_id,
    DEFAULT_CUSTOM_GAINS,
    BUILTIN_PRESETS,
)
from backend.core.equalizer.client_proxy import SatelliteUnreachable, is_ip_address
from backend.core.equalizer.presets import DEFAULT_EQ_FREQS
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


# =============================================================================
# The satellite transport: request() / try_request()
# =============================================================================

class _FakeResponse:
    """One satellite answer: an async context manager with a status and a body.

    `error` is raised on entry rather than at call time, which is where aiohttp
    raises it too — `session.get(...)` only builds the context manager.
    """

    def __init__(self, status=200, payload=None, error=None, body_error=None):
        self.status = status
        self._payload = payload if payload is not None else {}
        self._error = error
        self._body_error = body_error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        if self._body_error is not None:
            raise self._body_error
        return self._payload


class _SatelliteHttp:
    """Stands in for `aiohttp.ClientSession` — the far end of the pipe.

    Callable, so every session the proxy opens is this same recorder: `opened`
    counts the constructions (the keep-alive claim), `calls` keeps what actually
    went out, `close()` counts the closes.
    """

    def __init__(self):
        self.opened = 0
        self.closed = 0
        self.calls = []
        self.response = _FakeResponse()

    def __call__(self, *args, **kwargs):
        self.opened += 1
        return self

    def answers(self, **kwargs):
        self.response = _FakeResponse(**kwargs)

    def get(self, url, **kwargs):
        return self._record("GET", url, kwargs)

    def put(self, url, **kwargs):
        return self._record("PUT", url, kwargs)

    def post(self, url, **kwargs):
        return self._record("POST", url, kwargs)

    def _record(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        return self.response

    async def close(self):
        self.closed += 1

    @property
    def last(self):
        return self.calls[-1]


@pytest.fixture
def satellite(monkeypatch):
    """Every aiohttp session the proxy opens answers as this one satellite."""
    http = _SatelliteHttp()
    monkeypatch.setattr(aiohttp, "ClientSession", http)
    return http


class TestProxyTransport:
    """The pipe between the two halves of the appliance.

    `request` and `try_request` are the only way a server-side decision reaches a
    satellite's DSP. Two static contracts sit on either side of them — what the
    server decides to send (contracts/test_milo_client_contract.py) and what the
    satellite does with what it receives (milo-client/app/tests/) — and neither
    watches the pipe itself: both stayed green with each method gutted to a
    constant. What is pinned here is the distinction the two methods exist for
    (`request` raises on a failure, `try_request` reports it), which is the
    "success on failure" class: a satellite silently ignoring a command.
    """

    @pytest.fixture
    def proxy(self):
        return EqualizerClientProxyService()

    # -- what goes out ------------------------------------------------------

    async def test_the_url_is_the_satellite_api(self, proxy, satellite):
        """An IP is used as-is, a hostname gets the mDNS suffix, and the port is
        the client API's — the satellite serves nothing on any other."""
        await proxy.request("192.168.1.100", "GET", "/equalizer/levels")
        assert satellite.last[1] == f"http://192.168.1.100:{CLIENT_API_PORT}/equalizer/levels"

        await proxy.request("milo-client", "GET", "/equalizer/status")
        assert satellite.last[1] == f"http://milo-client.local:{CLIENT_API_PORT}/equalizer/status"

    async def test_a_write_carries_its_body(self, proxy, satellite):
        """PUT and POST are separate branches; a body dropped on either is a
        command that reached the satellite meaning nothing."""
        await proxy.request("192.168.1.100", "PUT", "/equalizer/mono", {"enabled": True})
        method, _, kwargs = satellite.last
        assert method == "PUT"
        assert kwargs["json"] == {"enabled": True}

        await proxy.request("192.168.1.100", "POST", "/equalizer/reset", {"target": "all"})
        method, _, kwargs = satellite.last
        assert method == "POST"
        assert kwargs["json"] == {"target": "all"}

    async def test_the_caller_timeout_reaches_aiohttp(self, proxy, satellite):
        """try_request's timeout is a caller argument, not a constant: the
        crossover replay picks a short one on purpose."""
        await proxy.try_request("192.168.1.100", "GET", "/equalizer/crossover", timeout=0.25)
        assert satellite.last[2]["timeout"].total == 0.25

    # -- what comes back ----------------------------------------------------

    async def test_a_200_gives_the_decoded_body(self, proxy, satellite):
        satellite.answers(status=200, payload={"volume": 42})
        assert await proxy.request("192.168.1.100", "GET", "/equalizer/volume") == {"volume": 42}

    async def test_a_non_200_raises_carrying_its_status(self, proxy, satellite):
        """The api/ layer maps status_code straight onto its HTTPException, so a
        404 from the satellite must not read as a dead host."""
        satellite.answers(status=404)

        with pytest.raises(SatelliteUnreachable) as raised:
            await proxy.request("192.168.1.100", "PUT", "/equalizer/nope", {})

        assert raised.value.status_code == 404
        assert raised.value.hostname == "192.168.1.100"

    async def test_a_refused_connection_raises_unreachable(self, proxy, satellite):
        satellite.answers(error=aiohttp.ClientConnectorError(Mock(), OSError("refused")))

        with pytest.raises(SatelliteUnreachable) as raised:
            await proxy.request("192.168.1.100", "PUT", "/equalizer/mono", {"enabled": True})

        assert raised.value.status_code == 503

    async def test_a_timeout_raises_unreachable(self, proxy, satellite):
        """A satellite that answers too late has not applied anything."""
        satellite.answers(error=asyncio.TimeoutError())

        with pytest.raises(SatelliteUnreachable):
            await proxy.request("192.168.1.100", "PUT", "/equalizer/mono", {"enabled": True})

    async def test_a_200_whose_body_never_arrives_is_not_a_success(self, proxy, satellite):
        """The status is not the answer — the decode is. A truncated body would
        otherwise return the header's 200 to a caller that asked for values."""
        satellite.answers(status=200, body_error=aiohttp.ClientPayloadError("truncated"))

        with pytest.raises(SatelliteUnreachable):
            await proxy.request("192.168.1.100", "GET", "/equalizer/levels")

    # -- try_request: the non-raising half ----------------------------------

    async def test_try_request_reports_a_refusal_instead_of_raising(self, proxy, satellite):
        """The whole reason the two methods are separate: background callers own
        their retry policy and must see the refusal as a value."""
        satellite.answers(status=500)

        assert await proxy.try_request("192.168.1.100", "PUT", "/equalizer/crossover", {}) == 500

    async def test_try_request_reports_an_unreachable_client_as_zero(self, proxy, satellite):
        satellite.answers(error=aiohttp.ClientConnectorError(Mock(), OSError("refused")))
        assert await proxy.try_request("192.168.1.100", "PUT", "/equalizer/crossover", {}) == 0

        satellite.answers(error=asyncio.TimeoutError())
        assert await proxy.try_request("192.168.1.100", "PUT", "/equalizer/crossover", {}) == 0

    async def test_try_request_never_reports_zero_for_a_reachable_client(self, proxy, satellite):
        """0 is the sentinel for "no answer"; a real HTTP status must never
        collapse into it, or a pending record is replayed forever."""
        satellite.answers(status=200)
        assert await proxy.try_request("192.168.1.100", "GET", "/equalizer/crossover") == 200

    # -- multiroom gate -----------------------------------------------------

    async def test_multiroom_disabled_never_reaches_the_network(self, proxy, satellite):
        """With multiroom off the satellite is not receiving audio at all; the
        request is refused here rather than left to time out."""
        proxy.routing_service = Mock(multiroom_enabled=False)

        with pytest.raises(SatelliteUnreachable) as raised:
            await proxy.request("192.168.1.100", "PUT", "/equalizer/mono", {"enabled": True})

        assert raised.value.status_code == 503
        assert satellite.calls == []

    async def test_skip_multiroom_check_sends_anyway(self, proxy, satellite):
        """The teardown paths push while multiroom is already down."""
        proxy.routing_service = Mock(multiroom_enabled=False)

        await proxy.request("192.168.1.100", "PUT", "/equalizer/mono",
                            {"enabled": True}, skip_multiroom_check=True)

        assert satellite.calls

    # -- the shared session -------------------------------------------------

    async def test_one_session_serves_every_request(self, proxy, satellite):
        """Keep-alive across the fan-out is the reason the session is held; a
        per-request session would pay a TCP handshake per satellite per step."""
        await proxy.request("192.168.1.100", "GET", "/equalizer/status")
        await proxy.try_request("192.168.1.100", "GET", "/equalizer/status")

        assert satellite.opened == 1

    async def test_cleanup_closes_the_session_and_the_next_request_opens_one(
        self, proxy, satellite
    ):
        """Called from the lifespan teardown: a session left open logs an
        unclosed-connector error on shutdown."""
        await proxy.request("192.168.1.100", "GET", "/equalizer/status")
        await proxy.cleanup()
        assert satellite.closed == 1

        await proxy.request("192.168.1.100", "GET", "/equalizer/status")
        assert satellite.opened == 2

    # -- levels polling -----------------------------------------------------

    async def test_levels_come_back_decoded(self, proxy, satellite):
        satellite.answers(status=200, payload={"playback_rms": [-20.0, -21.0]})

        levels = await proxy.get_equalizer_levels("192.168.1.100")

        assert levels == {"playback_rms": [-20.0, -21.0]}
        assert satellite.last[1] == f"http://192.168.1.100:{CLIENT_API_PORT}/equalizer/levels"

    async def test_levels_are_none_when_the_satellite_does_not_answer_them(self, proxy, satellite):
        """The VU meter polls this every frame: a failure is None, never a stale
        or invented reading, and never a raise into the polling loop."""
        satellite.answers(status=503)
        assert await proxy.get_equalizer_levels("192.168.1.100") is None

        satellite.answers(error=asyncio.TimeoutError())
        assert await proxy.get_equalizer_levels("192.168.1.100") is None



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
            FilterType,
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
                EqFilter(id=f"eq_band_{i:02d}", frequency=DEFAULT_EQ_FREQS[i],
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
        assert camilladsp_service._filters[0]["freq"] == DEFAULT_EQ_FREQS[0]
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
    async def test_disconnected_set_then_reconnect_restores_eq(
        self, camilladsp_service, monkeypatch, mock_camilla_client, camilla_daemon
    ):
        """End-to-end of the disconnected→reconnect window: update_cache captures the
        intent while CamillaDSP is DISCONNECTED; on reconnect restore_effects() pushes
        those exact cache values to the daemon. Proves equalizer.json never drifts from
        the live DSP across the boot/reconnect window (carried-over Phase 1 fix)."""
        from backend.core.multiroom.models import (
            EqualizerSettings, EqFilter, FilterType,
        )
        monkeypatch.setattr(camilladsp_service, "_persist_state_async", AsyncMock())

        settings = EqualizerSettings(
            filters=[
                EqFilter(id=f"eq_band_{i:02d}", frequency=DEFAULT_EQ_FREQS[i],
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
        camilladsp_service._client = mock_camilla_client
        camilla_daemon.load({"filters": {}, "pipeline": [], "processors": {}})

        ok = await camilladsp_service.restore_effects()

        assert ok is True
        assert camilla_daemon.last_pushed["filters"]["eq_band_00"]["parameters"]["gain"] == 4.0

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
    async def test_concurrent_rmw_does_not_clobber(
        self, camilladsp_service, monkeypatch, mock_camilla_client, camilla_daemon
    ):
        """A filter drag and a compressor toggle issued concurrently must BOTH land.

        The daemon graph is read-modify-written per caller; without serialization the
        two interleave (each reads the pre-change graph, the second write clobbers the
        first → last-writer-wins). `_config_lock` makes each RMW atomic so both survive.
        The interleave needs no staging: every call to the daemon goes through
        `_run`'s executor, so each read and each write is already a suspension
        point, and the single worker orders them get/get/set/set.
        """
        import asyncio

        camilladsp_service._connected = True
        camilladsp_service._client = mock_camilla_client
        monkeypatch.setattr(camilladsp_service, "_schedule_persist", lambda: None)
        monkeypatch.setattr(camilladsp_service, "_broadcast", AsyncMock())

        camilla_daemon.load({
            "filters": {"eq_band_00": {"type": "Biquad", "parameters": {
                "type": "Peaking", "freq": 31.0, "gain": 0.0, "q": 1.41}}},
            "pipeline": [],
            "processors": {},
        })

        await asyncio.gather(
            camilladsp_service.set_filter("eq_band_00", 31.0, 6.0, 1.41),
            camilladsp_service.set_compressor(enabled=True),
        )

        daemon = camilla_daemon.active_config
        assert daemon["filters"]["eq_band_00"]["parameters"]["gain"] == 6.0  # filter change kept
        assert "compressor" in daemon["processors"]  # compressor change kept

    @pytest.mark.asyncio
    async def test_apply_settings_single_round_trip(
        self, camilladsp_service, monkeypatch, mock_camilla_client, camilla_daemon
    ):
        """A full 10-band record applies in ONE set_config, not 13 sequential RMWs."""
        from backend.core.multiroom.models import (
            EqualizerSettings, EqFilter, FilterType,
        )

        camilladsp_service._connected = True
        camilladsp_service._client = mock_camilla_client
        monkeypatch.setattr(camilladsp_service, "_schedule_persist", lambda: None)
        monkeypatch.setattr(camilladsp_service, "_broadcast", AsyncMock())

        camilla_daemon.load({"filters": {}, "pipeline": [], "processors": {}})

        settings = EqualizerSettings(
            filters=[
                EqFilter(id=f"eq_band_{i:02d}", frequency=DEFAULT_EQ_FREQS[i],
                         gain=float(i), q=1.41, filter_type=FilterType.PEAKING)
                for i in range(10)
            ],
            active_preset="custom",
        )
        settings.compressor.enabled = True
        settings.loudness.enabled = True

        ok = await camilladsp_service.apply_settings(settings, persist=False)

        assert ok is True
        assert len(camilla_daemon.pushed_configs) == 1  # one graph write for the whole record (was 13)
        cfg = camilla_daemon.last_pushed
        assert cfg["filters"]["eq_band_09"]["parameters"]["gain"] == 9.0
        assert "compressor" in cfg["processors"]
        assert "loudness_low" in cfg["filters"]
        assert camilladsp_service._active_preset == "custom"

    @pytest.mark.asyncio
    async def test_apply_settings_rolls_back_caches_on_daemon_failure(
        self, camilladsp_service, monkeypatch, mock_camilla_client, camilla_daemon
    ):
        """A failed daemon write must leave the caches unchanged, not ahead of the DSP.

        Otherwise the in-memory cache (and the next persist to equalizer.json,
        plus the zone WS broadcast) would report the new EQ while the daemon
        keeps playing the old one.
        """
        from backend.core.multiroom.models import (
            EqualizerSettings, EqFilter, FilterType,
        )

        camilladsp_service._connected = True
        camilladsp_service._client = mock_camilla_client
        monkeypatch.setattr(camilladsp_service, "_schedule_persist", lambda: None)

        camilla_daemon.load({"filters": {}, "pipeline": [], "processors": {}})
        mock_camilla_client.config.set_active.side_effect = RuntimeError("daemon connection dropped")

        before = (
            camilladsp_service._filters,
            camilladsp_service._compressor,
            camilladsp_service._loudness,
            camilladsp_service._mono,
            camilladsp_service._active_preset,
        )

        settings = EqualizerSettings(
            filters=[
                EqFilter(id=f"eq_band_{i:02d}", frequency=DEFAULT_EQ_FREQS[i],
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


class TestInactiveDaemonConfigFallback:
    """An EQ write issued while CamillaDSP is inactive must still start from the
    graph the daemon holds on disk.

    `config.active()` answers None whenever the daemon is not processing (between
    streams, right after a restart). `_get_config` falls back to
    `read_and_parse_file(file_path())` for exactly that window. Without the
    fallback the service would start from an empty graph and push it back,
    dropping every filter it did not write itself — crossover included — into the
    config the daemon reloads on its next start. Silent: nothing raises, and the
    loss is audible only in the room whose lowpass disappeared.

    These four cannot be written while `_get_config` is patched out, which is
    what the whole EQ suite used to do.
    """

    @pytest.fixture
    def service(self, mock_camilla_client, tmp_path, monkeypatch):
        """Connected service talking to the mocked daemon.

        STORAGE_PATH is redirected because this checkout is also the appliance:
        `set_filter` schedules a debounced write of the real
        /var/lib/milo/equalizer.json, and a test must never be the thing that
        rewrites the operator's EQ.
        """
        monkeypatch.setattr(CamillaDSPService, "STORAGE_PATH", tmp_path / "equalizer.json")
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        svc = CamillaDSPService(settings_service=settings)
        svc._client = mock_camilla_client
        svc._connected = True
        svc._filters = [
            {"id": "eq_band_00", "type": "Peaking", "freq": 31.0, "gain": 0.0, "q": 1.41, "enabled": True}
        ]
        return svc

    async def test_inactive_daemon_is_read_from_its_config_file(self, service, camilla_daemon):
        """The band lands on top of what the file already declared, not instead of it."""
        camilla_daemon.go_inactive({
            "filters": {
                "crossover_lowpass": {"type": "Biquad", "parameters": {
                    "type": "Lowpass", "freq": 80, "q": 0.707}},
            },
            "pipeline": [{"type": "Filter", "channels": [0], "names": ["crossover_lowpass"]}],
        })

        assert await service.set_filter("eq_band_00", freq=100, gain=4.0, q=1.41) is True

        pushed = camilla_daemon.last_pushed
        assert pushed["filters"]["eq_band_00"]["parameters"]["gain"] == 4.0
        assert "crossover_lowpass" in pushed["filters"], \
            "the graph the daemon holds on disk was replaced instead of amended"
        assert pushed["pipeline"] == [
            {"type": "Filter", "channels": [0], "names": ["crossover_lowpass"]}
        ]

    async def test_the_file_read_uses_the_path_the_daemon_reports(
        self, service, mock_camilla_client, camilla_daemon
    ):
        """The path is asked of the daemon, never assumed — a satellite and the
        server keep their config under different roots."""
        camilla_daemon.go_inactive({"filters": {}, "pipeline": []})

        await service.set_filter("eq_band_00", freq=100, gain=4.0, q=1.41)

        mock_camilla_client.config.read_and_parse_file.assert_called_once_with(
            camilla_daemon.file_path()
        )

    async def test_a_daemon_with_nothing_to_read_still_takes_the_write(self, service, camilla_daemon):
        """No active config and no parsable file: the write still lands on a
        well-formed graph instead of raising on a None."""
        camilla_daemon.go_inactive(None)

        assert await service.set_filter("eq_band_00", freq=100, gain=4.0, q=1.41) is True

        pushed = camilla_daemon.last_pushed
        assert pushed["filters"]["eq_band_00"]["parameters"]["gain"] == 4.0
        assert pushed["pipeline"] == []

    async def test_an_active_daemon_is_never_read_from_disk(
        self, service, mock_camilla_client, camilla_daemon
    ):
        """The control. Without it the three above would also pass on a fallback
        that fires unconditionally, which would answer a stale file while the
        daemon is running."""
        camilla_daemon.load({"filters": {}, "pipeline": []})

        await service.set_filter("eq_band_00", freq=100, gain=4.0, q=1.41)

        mock_camilla_client.config.read_and_parse_file.assert_not_called()


# =============================================================================
# Equalizer State Tests
# =============================================================================

class TestRestoreAfterReconnect:
    """A reconnected daemon that refuses its own state must say so.

    `restore_effects` / `bypass_effects` / `set_mono` all answer False without
    raising, so the enclosing `except` in `_restore_after_reconnect` never sees
    them. Silently dropped, the daemon keeps whatever pipeline it restarted with
    while Milō and the UI still report the user's settings — audible, and the
    only trace was an info line saying the restore had begun.
    """

    @pytest.fixture
    def service(self):
        settings = Mock()
        settings.get_setting = AsyncMock(return_value=None)
        settings.set_setting = AsyncMock()
        svc = CamillaDSPService(settings_service=settings)
        svc._connected = True
        return svc

    async def test_a_refused_restore_is_reported_at_error(self, service, caplog):
        service._effects_enabled = True
        service.restore_effects = AsyncMock(return_value=False)
        service.bypass_effects = AsyncMock()

        with caplog.at_level(logging.ERROR):
            await service._restore_after_reconnect()

        assert "refused to restore equalizer effects" in caplog.text

    async def test_a_refused_bypass_is_reported_at_error(self, service, caplog):
        service._effects_enabled = False
        service.bypass_effects = AsyncMock(return_value=False)
        service.restore_effects = AsyncMock()

        with caplog.at_level(logging.ERROR):
            await service._restore_after_reconnect()

        assert "refused to bypass equalizer effects" in caplog.text

    async def test_a_refused_mono_is_reported_at_error(self, service, caplog):
        service._effects_enabled = True
        service._mono = True
        service.restore_effects = AsyncMock(return_value=True)
        service.set_mono = AsyncMock(return_value=False)

        with caplog.at_level(logging.ERROR):
            await service._restore_after_reconnect()

        assert "refused to restore mono" in caplog.text

    async def test_a_daemon_that_took_everything_logs_no_error(self, service, caplog):
        """The positive control — without it the three above would pass on any
        error line the reconnect path happens to emit."""
        service._effects_enabled = True
        service._mono = True
        service.restore_effects = AsyncMock(return_value=True)
        service.set_mono = AsyncMock(return_value=True)

        with caplog.at_level(logging.ERROR):
            await service._restore_after_reconnect()

        assert caplog.text == ""


class TestCamillaDspState:
    """Test Equalizer state enum"""

    def test_equalizer_states_exist(self):
        """Should have all expected states"""
        assert CamillaDspState.DISCONNECTED.value == "disconnected"
        assert CamillaDspState.INACTIVE.value == "inactive"
        assert CamillaDspState.RUNNING.value == "running"
        assert CamillaDspState.PAUSED.value == "paused"
