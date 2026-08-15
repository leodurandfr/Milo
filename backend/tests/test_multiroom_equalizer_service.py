# backend/tests/test_multiroom_equalizer_service.py
"""
Unit tests for MultiroomEqualizerService — the per-client EQ access layer.

One EQ record per client is the source of truth; a zone holds no EQ of its own
(its EQ is the identical EQ of its members). These tests cover:
- the access-layer primitives (get/set_client_eq, get/set_zone_eq)
- the route-facing wrappers (apply_/get_ zone/client + target-agnostic dispatch)
- preset loading and custom-preset save
- partial updates (per-member persistence + targeted broadcast)
- local DSP application + the active-preset name sync
"""
import pytest
from unittest.mock import Mock, AsyncMock

from backend.core.equalizer import MultiroomEqualizerService
from backend.core.multiroom.models import (
    EqualizerSettings,
    EqFilter,
    CompressorSettings,
    LoudnessSettings,
    FilterType,
    Client,
    Zone,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_registry():
    """Mock ClientRegistryService. 'local' is the local client; others are remote."""
    registry = Mock()
    registry.get_zone = Mock(return_value=None)
    registry.get_client = Mock(return_value=None)
    registry.get_client_equalizer = Mock(return_value=None)
    registry.set_client_equalizer = AsyncMock()
    registry.get_online_zone_clients = Mock(return_value=[])
    registry.is_local_client = Mock(side_effect=lambda mac_id: mac_id == "local")
    return registry


@pytest.fixture
def mock_camilladsp_service():
    """Mock CamillaDSPService — the local client's record store."""
    cam = Mock()
    cam.connected = True
    cam.set_filter = AsyncMock(return_value=True)
    cam.set_compressor = AsyncMock(return_value=True)
    cam.set_loudness = AsyncMock(return_value=True)
    cam.set_mono = AsyncMock(return_value=True)
    # Batched full-record apply: the local path drives the DSP through this one
    # call now (filters + compressor + loudness + mono + preset name in a single
    # graph write), not the per-parameter set_* loop.
    cam.apply_settings = AsyncMock(return_value=True)
    # Fresh snapshot each call so partial-update tests don't alias across calls.
    cam.get_equalizer_settings = Mock(side_effect=lambda: EqualizerSettings.default())
    cam.persist_state = AsyncMock()
    cam.update_cache = AsyncMock()
    cam.set_custom_gains = Mock()
    cam.get_custom_gains = AsyncMock(return_value=[0.0] * 10)
    cam.settings_service = None  # prevent Mock auto-creation for await
    return cam


@pytest.fixture
def mock_state_machine():
    sm = Mock()
    sm.broadcast = AsyncMock()
    return sm


@pytest.fixture
def multiroom_equalizer_service(mock_registry, mock_camilladsp_service, mock_state_machine):
    return MultiroomEqualizerService(
        client_registry_service=mock_registry,
        camilladsp_service=mock_camilladsp_service,
        state_machine=mock_state_machine,
    )


@pytest.fixture
def sample_equalizer_settings():
    return EqualizerSettings(
        enabled=True,
        filters=[
            EqFilter(id="eq_band_00", frequency=100, gain=3.0, q=1.41, filter_type=FilterType.PEAKING, enabled=True),
            EqFilter(id="eq_band_01", frequency=1000, gain=-2.0, q=1.0, filter_type=FilterType.PEAKING, enabled=True),
        ],
        compressor=CompressorSettings(enabled=True, threshold=-20.0, ratio=4.0, attack=10.0, release=100.0, makeup_gain=2.0),
        loudness=LoudnessSettings(enabled=True, high_boost=5.0, low_boost=8.0),
    )


@pytest.fixture
def sample_zone():
    """A zone with a local + remote member (a zone holds no EQ of its own)."""
    return Zone(id="zone-123", name="Living Room", client_ids=["local", "milo-client-1"])


@pytest.fixture
def local_client():
    return Client(mac_id="local", name="Main Speaker", ip="127.0.0.1", online=True, zone_id=None)


@pytest.fixture
def remote_client():
    return Client(mac_id="milo-client-1", name="Bedroom", ip="192.168.1.100", online=True, zone_id=None)


@pytest.fixture
def zoned_remote_client():
    return Client(mac_id="milo-client-1", name="Kitchen", ip="192.168.1.100", online=True, zone_id="zone-123")


@pytest.fixture
def offline_registry():
    """Registry as in multiroom-OFF: it knows no clients (Snapcast populates it).

    Crucially it does NOT recognize "local" as the local client, so only the
    LOCAL_TARGET sentinel can route the local device to CamillaDSP here.
    """
    registry = Mock()
    registry.get_zone = Mock(return_value=None)
    registry.get_client = Mock(return_value=None)
    registry.get_client_equalizer = Mock(return_value=None)
    registry.set_client_equalizer = AsyncMock()
    registry.get_online_zone_clients = Mock(return_value=[])
    registry.is_local_client = Mock(return_value=False)  # empty registry → nothing is "local"
    return registry


@pytest.fixture
def offline_service(offline_registry, mock_camilladsp_service, mock_state_machine):
    return MultiroomEqualizerService(
        client_registry_service=offline_registry,
        camilladsp_service=mock_camilladsp_service,
        state_machine=mock_state_machine,
    )


# =============================================================================
# Initialization
# =============================================================================

class TestMultiroomEqualizerServiceInit:
    def test_create_service(self, mock_registry, mock_camilladsp_service):
        service = MultiroomEqualizerService(
            client_registry_service=mock_registry,
            camilladsp_service=mock_camilladsp_service,
        )
        assert service._registry == mock_registry
        assert service._camilladsp_service == mock_camilladsp_service

    def test_create_service_no_deps(self):
        service = MultiroomEqualizerService()
        assert service._registry is None
        assert service._camilladsp_service is None


# =============================================================================
# Per-client access layer — the unified EQ source of truth
# =============================================================================

class TestPerClientAccessLayer:
    @pytest.mark.asyncio
    async def test_get_client_eq_local_reads_camilladsp(self, multiroom_equalizer_service, mock_camilladsp_service):
        snap = EqualizerSettings.default()
        mock_camilladsp_service.get_equalizer_settings = Mock(return_value=snap)
        result = await multiroom_equalizer_service.get_client_eq("local")
        assert result is snap

    @pytest.mark.asyncio
    async def test_get_client_eq_remote_reads_registry_copy(self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings):
        mock_registry.get_client_equalizer.return_value = sample_equalizer_settings
        result = await multiroom_equalizer_service.get_client_eq("milo-client-1")
        assert result == sample_equalizer_settings
        assert result is not sample_equalizer_settings  # a copy, never the stored object

    @pytest.mark.asyncio
    async def test_get_client_eq_remote_unsaved_returns_default(self, multiroom_equalizer_service, mock_registry):
        mock_registry.get_client_equalizer.return_value = None
        result = await multiroom_equalizer_service.get_client_eq("milo-client-1")
        assert isinstance(result, EqualizerSettings)
        assert result.active_preset == "flat"

    @pytest.mark.asyncio
    async def test_set_client_eq_local_applies_and_persists(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_equalizer_settings):
        result = await multiroom_equalizer_service.set_client_eq("local", sample_equalizer_settings)
        assert result is True
        mock_camilladsp_service.apply_settings.assert_awaited_once()
        mock_camilladsp_service.persist_state.assert_awaited_once()
        mock_registry.set_client_equalizer.assert_not_called()  # local never hits the registry

    @pytest.mark.asyncio
    async def test_set_client_eq_local_persists_custom_gains(self, multiroom_equalizer_service, mock_camilladsp_service):
        eq = EqualizerSettings.default()
        eq.custom_gains = [1.0] * 10
        await multiroom_equalizer_service.set_client_eq("local", eq)
        mock_camilladsp_service.set_custom_gains.assert_called_once_with([1.0] * 10)

    @pytest.mark.asyncio
    async def test_set_client_eq_remote_writes_registry(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_equalizer_settings):
        result = await multiroom_equalizer_service.set_client_eq("milo-client-1", sample_equalizer_settings)
        assert result is True
        mock_registry.set_client_equalizer.assert_called_once_with("milo-client-1", sample_equalizer_settings)
        mock_camilladsp_service.apply_settings.assert_not_called()  # remote DSP is not driven locally

    @pytest.mark.asyncio
    async def test_get_zone_eq_reads_local_member(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone):
        snap = EqualizerSettings.default()
        snap.active_preset = "rock"
        mock_camilladsp_service.get_equalizer_settings = Mock(return_value=snap)
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.get_zone_eq("zone-123")
        assert result.active_preset == "rock"  # read from the local member

    @pytest.mark.asyncio
    async def test_get_zone_eq_no_members_returns_none(self, multiroom_equalizer_service, mock_registry):
        mock_registry.get_zone.return_value = Zone(id="z", name="Empty", client_ids=[])
        assert await multiroom_equalizer_service.get_zone_eq("z") is None

    @pytest.mark.asyncio
    async def test_get_zone_eq_reports_enabled_off_when_a_satellite_is_bypassed(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone
    ):
        """A zone must not claim effects its satellites are not applying.

        ``enabled`` lives in two domains — settings.json locally, the per-client
        record remotely — so reading the local member alone let a zone report
        True while a satellite played bypassed (and the reverse). Breaking this
        makes the Equalizer page lie about a speaker nobody can hear correctly.
        """
        local_snap = EqualizerSettings.default()
        local_snap.enabled = True
        mock_camilladsp_service.get_equalizer_settings = Mock(return_value=local_snap)
        remote_record = EqualizerSettings.default()
        remote_record.enabled = False
        mock_registry.get_client_equalizer = Mock(return_value=remote_record)
        mock_registry.get_zone.return_value = sample_zone

        result = await multiroom_equalizer_service.get_zone_eq("zone-123")

        assert result.enabled is False

    @pytest.mark.asyncio
    async def test_get_zone_eq_reports_enabled_on_when_every_member_agrees(
        self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone
    ):
        """The other half: the conjunction must still be able to say True, or the
        test above passes for the wrong reason."""
        local_snap = EqualizerSettings.default()
        local_snap.enabled = True
        mock_camilladsp_service.get_equalizer_settings = Mock(return_value=local_snap)
        remote_record = EqualizerSettings.default()
        remote_record.enabled = True
        mock_registry.get_client_equalizer = Mock(return_value=remote_record)
        mock_registry.get_zone.return_value = sample_zone

        result = await multiroom_equalizer_service.get_zone_eq("zone-123")

        assert result.enabled is True

    @pytest.mark.asyncio
    async def test_local_master_toggle_fans_out_when_the_local_client_is_zoned(
        self, multiroom_equalizer_service, mock_registry, sample_zone
    ):
        """The dock's Equalizer switch must move the whole zone, not just the DAC.

        Writing the local domain alone leaves every satellite playing under its
        own flag with nothing to repair it — the state the unit was found in on
        2026-07-27. Breaking this re-opens that divergence in one tap.
        """
        mock_registry.get_zone_for_client = Mock(return_value=sample_zone)
        mock_registry.get_all_clients = Mock(
            return_value={"local": Client(mac_id="local", name="Milo", ip="127.0.0.1")}
        )
        multiroom_equalizer_service.set_zone_equalizer_effects_enabled = AsyncMock(return_value=True)

        result = await multiroom_equalizer_service.set_local_equalizer_effects_enabled(False)

        assert result is True
        multiroom_equalizer_service.set_zone_equalizer_effects_enabled.assert_called_once_with(
            "zone-123", False
        )

    @pytest.mark.asyncio
    async def test_local_master_toggle_stays_local_when_the_local_client_is_not_zoned(
        self, multiroom_equalizer_service, mock_registry
    ):
        """The standalone half — no zone means the plain local write, so the
        fan-out above cannot be a blanket redirect."""
        mock_registry.get_zone_for_client = Mock(return_value=None)
        mock_registry.get_all_clients = Mock(
            return_value={"local": Client(mac_id="local", name="Milo", ip="127.0.0.1")}
        )
        routing = Mock()
        routing.set_equalizer_effects_enabled = AsyncMock(return_value=True)
        multiroom_equalizer_service._routing_service = routing

        result = await multiroom_equalizer_service.set_local_equalizer_effects_enabled(True)

        assert result is True
        routing.set_equalizer_effects_enabled.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_set_zone_eq_fans_out_to_all_members(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone, sample_equalizer_settings):
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.set_zone_eq("zone-123", sample_equalizer_settings)
        assert result is True
        # local member applied to the DAC + persisted; remote member written to the registry
        mock_camilladsp_service.apply_settings.assert_awaited_once()
        mock_camilladsp_service.persist_state.assert_awaited()
        mock_registry.set_client_equalizer.assert_called_once()
        assert mock_registry.set_client_equalizer.call_args.args[0] == "milo-client-1"

    @pytest.mark.asyncio
    async def test_set_zone_eq_members_get_independent_copies(self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings):
        mock_registry.get_zone.return_value = Zone(id="z", name="Pair", client_ids=["milo-client-1", "milo-client-2"])
        await multiroom_equalizer_service.set_zone_eq("z", sample_equalizer_settings)
        records = [c.args[1] for c in mock_registry.set_client_equalizer.call_args_list]
        assert len(records) == 2
        assert records[0] is not records[1]  # independent copies, no aliasing

    @pytest.mark.asyncio
    async def test_set_zone_eq_member_copies_are_deep(self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings):
        """Each member receives a DEEP copy: editing one member's nested filter must
        not bleed into a sibling (nor into the caller's source record). Guards the
        from_dict(to_dict()) fan-out against shared EqFilter/list aliasing — the
        property that lets a later per-member edit stay local."""
        mock_registry.get_zone.return_value = Zone(id="z", name="Pair", client_ids=["milo-client-1", "milo-client-2"])
        original_gain = sample_equalizer_settings.filters[0].gain
        await multiroom_equalizer_service.set_zone_eq("z", sample_equalizer_settings)
        records = [c.args[1] for c in mock_registry.set_client_equalizer.call_args_list]
        assert len(records) == 2
        # Mutate member-0's first filter; member-1 and the source must be untouched.
        records[0].filters[0].gain = 99.0
        assert records[1].filters[0].gain == original_gain
        assert sample_equalizer_settings.filters[0].gain == original_gain

    @pytest.mark.asyncio
    async def test_set_zone_eq_zone_not_found_raises(self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings):
        mock_registry.get_zone.return_value = None
        with pytest.raises(ValueError, match="Zone not found"):
            await multiroom_equalizer_service.set_zone_eq("nope", sample_equalizer_settings)


# =============================================================================
# Zone / client route-facing wrappers
# =============================================================================

class TestZoneClientWrappers:
    @pytest.mark.asyncio
    async def test_apply_zone_equalizer_fans_out(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone, sample_equalizer_settings):
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.apply_zone_equalizer("zone-123", sample_equalizer_settings)
        assert result is True
        mock_camilladsp_service.apply_settings.assert_awaited_once()
        mock_registry.set_client_equalizer.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_zone_equalizer_zone_not_found(self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings):
        mock_registry.get_zone.return_value = None
        with pytest.raises(ValueError, match="Zone not found"):
            await multiroom_equalizer_service.apply_zone_equalizer("nonexistent", sample_equalizer_settings)

    @pytest.mark.asyncio
    async def test_apply_zone_equalizer_no_registry(self, sample_equalizer_settings):
        service = MultiroomEqualizerService()
        assert await service.apply_zone_equalizer("zone-123", sample_equalizer_settings) is False

    @pytest.mark.asyncio
    async def test_get_zone_eq_found(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone):
        snap = EqualizerSettings.default()
        snap.active_preset = "jazz"
        mock_camilladsp_service.get_equalizer_settings = Mock(return_value=snap)
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.get_zone_eq("zone-123")
        assert result.active_preset == "jazz"

    @pytest.mark.asyncio
    async def test_get_zone_eq_not_found(self, multiroom_equalizer_service, mock_registry):
        mock_registry.get_zone.return_value = None
        assert await multiroom_equalizer_service.get_zone_eq("nonexistent") is None

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_local(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, local_client, sample_equalizer_settings):
        mock_registry.get_client.return_value = local_client
        result = await multiroom_equalizer_service.apply_client_equalizer("local", sample_equalizer_settings)
        assert result is True
        mock_camilladsp_service.apply_settings.assert_awaited_once()
        mock_camilladsp_service.persist_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_remote(self, multiroom_equalizer_service, mock_registry, remote_client, sample_equalizer_settings):
        mock_registry.get_client.return_value = remote_client
        result = await multiroom_equalizer_service.apply_client_equalizer("milo-client-1", sample_equalizer_settings)
        assert result is True
        mock_registry.set_client_equalizer.assert_called_once_with("milo-client-1", sample_equalizer_settings)

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_not_found(self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings):
        mock_registry.get_client.return_value = None
        with pytest.raises(ValueError, match="Client not found"):
            await multiroom_equalizer_service.apply_client_equalizer("nope", sample_equalizer_settings)

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_in_zone(self, multiroom_equalizer_service, mock_registry, zoned_remote_client, sample_equalizer_settings):
        mock_registry.get_client.return_value = zoned_remote_client
        with pytest.raises(ValueError, match="is in zone"):
            await multiroom_equalizer_service.apply_client_equalizer("milo-client-1", sample_equalizer_settings)

    @pytest.mark.asyncio
    async def test_get_client_eq_remote(self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings):
        mock_registry.get_client_equalizer.return_value = sample_equalizer_settings
        result = await multiroom_equalizer_service.get_client_eq("milo-client-1")
        assert result == sample_equalizer_settings

    @pytest.mark.asyncio
    async def test_get_client_eq_unsaved_returns_default(self, multiroom_equalizer_service, mock_registry):
        mock_registry.get_client_equalizer.return_value = None
        result = await multiroom_equalizer_service.get_client_eq("milo-client-1")
        assert isinstance(result, EqualizerSettings)
        assert result.active_preset == "flat"


# =============================================================================
# Target-agnostic dispatch
# =============================================================================

class TestTargetAgnosticEqualizerMethods:
    @pytest.mark.asyncio
    async def test_apply_equalizer_to_zone(self, multiroom_equalizer_service, mock_registry, sample_zone, sample_equalizer_settings):
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.apply_equalizer("zone", "zone-123", sample_equalizer_settings)
        assert result is True
        mock_registry.get_zone.assert_any_call("zone-123")

    @pytest.mark.asyncio
    async def test_apply_equalizer_to_client(self, multiroom_equalizer_service, mock_registry, remote_client, sample_equalizer_settings):
        mock_registry.get_client.return_value = remote_client
        result = await multiroom_equalizer_service.apply_equalizer("client", "milo-client-1", sample_equalizer_settings)
        assert result is True
        mock_registry.set_client_equalizer.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_equalizer_invalid_target_type(self, multiroom_equalizer_service, sample_equalizer_settings):
        with pytest.raises(ValueError, match="Invalid target_type"):
            await multiroom_equalizer_service.apply_equalizer("invalid", "id", sample_equalizer_settings)

    @pytest.mark.asyncio
    async def test_get_equalizer_zone(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone):
        snap = EqualizerSettings.default()
        snap.active_preset = "pop"
        mock_camilladsp_service.get_equalizer_settings = Mock(return_value=snap)
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.get_equalizer("zone", "zone-123")
        assert result.active_preset == "pop"

    @pytest.mark.asyncio
    async def test_get_equalizer_client(self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings):
        mock_registry.get_client_equalizer.return_value = sample_equalizer_settings
        result = await multiroom_equalizer_service.get_equalizer("client", "milo-client-1")
        assert result == sample_equalizer_settings

    @pytest.mark.asyncio
    async def test_get_equalizer_invalid_target_type(self, multiroom_equalizer_service):
        with pytest.raises(ValueError, match="Invalid target_type"):
            await multiroom_equalizer_service.get_equalizer("invalid", "id")


# =============================================================================
# Preset loading + custom-preset save
# =============================================================================

class TestPresetLoading:
    @pytest.fixture
    def fresh_remote(self):
        """A registered, online, standalone remote client with NO saved EQ entry."""
        return Client(mac_id="dc:a6:32:aa:bb:cc", name="Bedroom", ip="192.168.1.100", online=True, zone_id=None)

    @pytest.mark.asyncio
    async def test_load_preset_client_persists_name_and_gains(self, multiroom_equalizer_service, mock_registry, fresh_remote):
        """Picking a preset on a fresh remote client persists the preset NAME and gains."""
        mock_registry.get_client_equalizer.return_value = None
        mock_registry.get_client.return_value = fresh_remote

        result, gains = await multiroom_equalizer_service.load_preset("client", "dc:a6:32:aa:bb:cc", "bass_boost")

        assert result is True
        mock_registry.set_client_equalizer.assert_called_once()
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.active_preset == "bass_boost"
        assert [round(f.gain) for f in persisted.filters] == [6, 5, 4, 2, 0, 0, 0, 0, 0, 0]

    @pytest.mark.asyncio
    async def test_load_preset_client_unknown_raises(self, multiroom_equalizer_service, mock_registry):
        mock_registry.get_client_equalizer.return_value = None
        mock_registry.get_client.return_value = None
        with pytest.raises(ValueError, match="Client not found"):
            await multiroom_equalizer_service.load_preset("client", "nope", "bass_boost")

    @pytest.mark.asyncio
    async def test_load_preset_client_zoned_raises(self, multiroom_equalizer_service, mock_registry, zoned_remote_client):
        mock_registry.get_client.return_value = zoned_remote_client
        with pytest.raises(ValueError, match="is in zone"):
            await multiroom_equalizer_service.load_preset("client", "milo-client-1", "bass_boost")

    @pytest.mark.asyncio
    async def test_load_preset_zone(self, multiroom_equalizer_service, mock_registry, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        result, gains = await multiroom_equalizer_service.load_preset("zone", "zone-123", "rock")
        assert result is True
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.active_preset == "rock"

    @pytest.mark.asyncio
    async def test_load_preset_zone_zone_not_found(self, multiroom_equalizer_service, mock_registry):
        mock_registry.get_zone.return_value = None
        with pytest.raises(ValueError, match="Zone not found"):
            await multiroom_equalizer_service.load_preset("zone", "nope", "rock")

    @pytest.mark.asyncio
    async def test_save_custom_preset_client(self, multiroom_equalizer_service, mock_registry, fresh_remote):
        mock_registry.get_client_equalizer.return_value = None
        mock_registry.get_client.return_value = fresh_remote
        await multiroom_equalizer_service.save_custom_preset("client", "dc:a6:32:aa:bb:cc")
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.active_preset == "custom"

    @pytest.mark.asyncio
    async def test_save_custom_preset_zone(self, multiroom_equalizer_service, mock_registry, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        await multiroom_equalizer_service.save_custom_preset("zone", "zone-123")
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.active_preset == "custom"

    @pytest.mark.asyncio
    async def test_save_custom_preset_missing_zone_raises(self, multiroom_equalizer_service, mock_registry):
        mock_registry.get_zone.return_value = None
        with pytest.raises(ValueError, match="not found"):
            await multiroom_equalizer_service.save_custom_preset("zone", "nonexistent")


# =============================================================================
# resolve_preset_gains — builtin vs "custom" resolution (name → gains)
# =============================================================================

class TestResolvePresetGains:
    """resolve_preset_gains maps a preset id to its gain values. The "custom" id
    resolves from the target's own custom_gains, falling back to the global
    CamillaDSP gains, then to a flat default — the branches the indirect
    load_*_preset tests never exercise."""

    @pytest.mark.asyncio
    async def test_resolve_builtin_returns_preset_gains(self, multiroom_equalizer_service):
        from backend.core.equalizer.presets import get_preset_by_id

        gains = await multiroom_equalizer_service.resolve_preset_gains("rock")
        assert gains == get_preset_by_id("rock")["gains"]

    @pytest.mark.asyncio
    async def test_resolve_unknown_preset_raises(self, multiroom_equalizer_service):
        with pytest.raises(ValueError, match="Preset not found"):
            await multiroom_equalizer_service.resolve_preset_gains("does_not_exist")

    @pytest.mark.asyncio
    async def test_resolve_custom_from_settings(self, multiroom_equalizer_service):
        settings = EqualizerSettings(custom_gains=[2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0])
        gains = await multiroom_equalizer_service.resolve_preset_gains("custom", settings)
        assert gains == settings.custom_gains

    @pytest.mark.asyncio
    async def test_resolve_custom_falls_back_to_camilladsp(self, multiroom_equalizer_service, mock_camilladsp_service):
        # No per-target custom_gains → use the global CamillaDSP gains (distinct from
        # the flat DEFAULT so the branch is unambiguous).
        mock_camilladsp_service.get_custom_gains = AsyncMock(return_value=[1.5] * 10)
        gains = await multiroom_equalizer_service.resolve_preset_gains("custom", None)
        assert gains == [1.5] * 10

    @pytest.mark.asyncio
    async def test_resolve_custom_falls_back_to_default(self, mock_registry):
        from backend.core.equalizer.presets import DEFAULT_CUSTOM_GAINS

        # A service with no CamillaDSP and no per-target gains → the flat default.
        service = MultiroomEqualizerService(client_registry_service=mock_registry)
        gains = await service.resolve_preset_gains("custom", None)
        assert gains == DEFAULT_CUSTOM_GAINS


# =============================================================================
# Local active-preset NAME sync (the "wrong name / right gains" bug)
# =============================================================================

class TestLocalActivePresetSync:
    @pytest.mark.asyncio
    async def test_apply_to_local_syncs_active_preset_name(self, multiroom_equalizer_service, mock_camilladsp_service, sample_equalizer_settings):
        # The preset NAME now travels inside the batched record; apply_settings
        # writes it into CamillaDSPService._active_preset (no separate call).
        sample_equalizer_settings.active_preset = "vocal_boost"
        result = await multiroom_equalizer_service._apply_to_local(sample_equalizer_settings)
        assert result is True
        mock_camilladsp_service.apply_settings.assert_awaited_once()
        assert mock_camilladsp_service.apply_settings.await_args.args[0].active_preset == "vocal_boost"

    @pytest.mark.asyncio
    async def test_set_client_eq_local_syncs_name_and_persists(self, multiroom_equalizer_service, mock_camilladsp_service, sample_equalizer_settings):
        sample_equalizer_settings.active_preset = "vocal_boost"
        await multiroom_equalizer_service.set_client_eq("local", sample_equalizer_settings)
        mock_camilladsp_service.apply_settings.assert_awaited_once()
        assert mock_camilladsp_service.apply_settings.await_args.args[0].active_preset == "vocal_boost"
        mock_camilladsp_service.persist_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_custom_zone_with_local_member_syncs_name(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        await multiroom_equalizer_service.save_custom_preset("zone", "zone-123")
        mock_camilladsp_service.apply_settings.assert_awaited_once()
        assert mock_camilladsp_service.apply_settings.await_args.args[0].active_preset == "custom"

    @pytest.mark.asyncio
    async def test_save_custom_zone_without_local_skips_local(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service):
        mock_registry.get_zone.return_value = Zone(id="z", name="Pair", client_ids=["milo-client-1", "milo-client-2"])
        await multiroom_equalizer_service.save_custom_preset("zone", "z")
        mock_camilladsp_service.apply_settings.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_custom_remote_client_skips_local(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, remote_client):
        mock_registry.get_client_equalizer.return_value = None
        mock_registry.get_client.return_value = remote_client
        await multiroom_equalizer_service.save_custom_preset("client", "milo-client-1")
        mock_camilladsp_service.apply_settings.assert_not_called()


# =============================================================================
# Local DSP application (the DSP half of set_client_eq)
# =============================================================================

class TestLocalDspApplication:
    @pytest.mark.asyncio
    async def test_set_client_eq_local_applies_all(self, multiroom_equalizer_service, mock_camilladsp_service, sample_equalizer_settings):
        result = await multiroom_equalizer_service.set_client_eq("local", sample_equalizer_settings)
        assert result is True
        # One batched graph write carries filters + compressor + loudness + mono.
        mock_camilladsp_service.apply_settings.assert_awaited_once_with(
            sample_equalizer_settings, persist=False
        )

    @pytest.mark.asyncio
    async def test_set_client_eq_local_disconnected_captures_intent(self, multiroom_equalizer_service, mock_camilladsp_service, sample_equalizer_settings):
        """While CamillaDSP is disconnected the live apply no-ops (set_* guards on
        `connected`), so the intent is captured into the cache + equalizer.json via
        update_cache — restore_effects() re-pushes it on reconnect. persist_state is
        NOT used here (it snapshots the live cache, which was never touched)."""
        mock_camilladsp_service.connected = False
        result = await multiroom_equalizer_service.set_client_eq("local", sample_equalizer_settings)
        assert result is False
        mock_camilladsp_service.apply_settings.assert_not_called()
        mock_camilladsp_service.update_cache.assert_awaited_once_with(sample_equalizer_settings)
        mock_camilladsp_service.persist_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_client_eq_local_no_camilladsp(self, mock_registry, sample_equalizer_settings):
        service = MultiroomEqualizerService(client_registry_service=mock_registry)
        result = await service.set_client_eq("local", sample_equalizer_settings)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_client_eq_remote_no_proxy_is_not_failure(self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings):
        result = await multiroom_equalizer_service.set_client_eq("milo-client-1", sample_equalizer_settings)
        assert result is True  # no proxy → will sync on reconnection
        mock_registry.set_client_equalizer.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_client_eq_local_apply_failure_returns_false(self, multiroom_equalizer_service, mock_camilladsp_service, sample_equalizer_settings):
        # The batched apply is atomic: a failed graph write means nothing was
        # applied, so the whole call reports failure and does not persist.
        mock_camilladsp_service.apply_settings.return_value = False
        result = await multiroom_equalizer_service.set_client_eq("local", sample_equalizer_settings)
        assert result is False
        mock_camilladsp_service.persist_state.assert_not_called()


class TestRemoteRecordPush:
    """A remote write reaches the satellite through the one canonical push."""

    @pytest.fixture
    def proxy_service(self):
        proxy = Mock()
        proxy.apply_record = AsyncMock(return_value=True)
        return proxy

    @pytest.fixture
    def service(self, mock_registry, mock_camilladsp_service, mock_state_machine, proxy_service):
        return MultiroomEqualizerService(
            client_registry_service=mock_registry,
            camilladsp_service=mock_camilladsp_service,
            proxy_service=proxy_service,
            state_machine=mock_state_machine,
        )

    @pytest.mark.asyncio
    async def test_remote_write_delegates_the_whole_record(
        self, service, mock_registry, proxy_service, remote_client, sample_equalizer_settings
    ):
        """The record travels intact — including `enabled`, which this path used
        to drop, leaving a client adopting a bypassed zone's record still playing
        the effects the rest of the zone has off."""
        mock_registry.get_client.return_value = remote_client

        assert await service.set_client_eq("milo-client-1", sample_equalizer_settings) is True
        proxy_service.apply_record.assert_awaited_once_with(
            "192.168.1.100", sample_equalizer_settings
        )

    @pytest.mark.asyncio
    async def test_remote_write_reports_a_failed_push(
        self, service, mock_registry, proxy_service, remote_client, sample_equalizer_settings
    ):
        mock_registry.get_client.return_value = remote_client
        proxy_service.apply_record.return_value = False

        assert await service.set_client_eq("milo-client-1", sample_equalizer_settings) is False

    @pytest.mark.asyncio
    async def test_targeted_band_update_carries_no_enabled_flag(
        self, service, mock_registry, remote_client
    ):
        """Per-band `enabled` must stay off the wire on the targeted path too.

        The satellite implements it as pipeline membership — the very mechanism
        the master bypass uses — so a band carrying the record's default
        enabled=True would re-pipe that band on a bypassed client and make it
        audible on its own. Dragging one band in the UI takes this path.
        """
        router = Mock()
        router.update_filter = AsyncMock(return_value={"status": "success"})
        service._equalizer_router = router
        mock_registry.get_client.return_value = remote_client

        await service.update_filter("client", "milo-client-1", "eq_band_00", gain=5.0)

        assert "enabled" not in router.update_filter.await_args.kwargs["filter_data"]


# =============================================================================
# Partial update methods (per-member persistence + targeted broadcast)
# =============================================================================

class TestPartialUpdateMethods:
    @pytest.mark.asyncio
    async def test_update_filter_persists_to_remote_member_and_local(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.update_filter("zone", "zone-123", "eq_band_00", gain=5.0)
        assert result is True
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.filters[0].gain == 5.0
        mock_camilladsp_service.persist_state.assert_awaited()  # local member snapshotted

    @pytest.mark.asyncio
    async def test_partial_update_local_disconnected_captures_intent(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone):
        """A partial update touching the local member while CamillaDSP is disconnected
        captures the intended record via update_cache (the router no-op'd on the live
        cache), so the local member's equalizer.json doesn't drift from the zone's
        remote members. persist_state is NOT used (the live cache was never updated)."""
        mock_registry.get_zone.return_value = sample_zone
        mock_camilladsp_service.connected = False
        result = await multiroom_equalizer_service.update_filter("zone", "zone-123", "eq_band_00", gain=5.0)
        assert result is True
        mock_camilladsp_service.update_cache.assert_awaited_once()
        captured = mock_camilladsp_service.update_cache.call_args.args[0]
        assert captured.filters[0].gain == 5.0
        mock_camilladsp_service.persist_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_filter_broadcasts(self, multiroom_equalizer_service, mock_registry, mock_state_machine, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        await multiroom_equalizer_service.update_filter("zone", "zone-123", "eq_band_00", gain=5.0)
        mock_state_machine.broadcast.assert_called_once()
        event = mock_state_machine.broadcast.call_args.args[0]
        assert (event.CATEGORY, event.TYPE) == ("multiroom", "equalizer_changed")
        assert event.target_id == "zone-123"
        # The broadcast filter MUST be the frontend wire shape (freq/type), not the
        # model's persistence shape (frequency/filter_type) — the store reads freq/type.
        flt = event.equalizer_settings["filters"][0]
        assert "freq" in flt and "type" in flt
        assert "frequency" not in flt and "filter_type" not in flt
        assert flt["gain"] == 5.0

    @pytest.mark.asyncio
    async def test_update_filter_type(self, multiroom_equalizer_service, mock_registry, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.update_filter("zone", "zone-123", "eq_band_00", filter_type="Lowshelf")
        assert result is True
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.filters[0].filter_type == FilterType.LOWSHELF
        assert persisted.filters[0].gain == 0.0

    @pytest.mark.asyncio
    async def test_update_filter_not_found(self, multiroom_equalizer_service, mock_registry, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        with pytest.raises(ValueError, match="Filter not found"):
            await multiroom_equalizer_service.update_filter("zone", "zone-123", "nonexistent", gain=5.0)

    @pytest.mark.asyncio
    async def test_update_compressor(self, multiroom_equalizer_service, mock_registry, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.update_compressor("zone", "zone-123", enabled=True, threshold=-30.0)
        assert result is True
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.compressor.enabled is True
        assert persisted.compressor.threshold == -30.0
        assert persisted.compressor.ratio == 4.0  # preserved

    @pytest.mark.asyncio
    async def test_update_loudness(self, multiroom_equalizer_service, mock_registry, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.update_loudness("zone", "zone-123", enabled=True, low_boost=10.0)
        assert result is True
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.loudness.enabled is True
        assert persisted.loudness.low_boost == 10.0
        assert persisted.loudness.high_boost == 5.0  # preserved

    @pytest.mark.asyncio
    async def test_update_mono_client(self, multiroom_equalizer_service, mock_registry, remote_client):
        mock_registry.get_client.return_value = remote_client
        mock_registry.get_client_equalizer.return_value = EqualizerSettings.default()
        result = await multiroom_equalizer_service.update_mono("client", "milo-client-1", enabled=True)
        assert result is True
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.mono is True

    @pytest.mark.asyncio
    async def test_update_filter_unknown_client_raises(self, multiroom_equalizer_service, mock_registry):
        """A partial update to a MAC the registry has never seen fails loud (→ 404),
        rather than silently materializing a phantom per-client record."""
        mock_registry.get_client.return_value = None
        with pytest.raises(ValueError, match="Client not found"):
            await multiroom_equalizer_service.update_filter("client", "unknown-mac", "eq_band_00", gain=5.0)
        mock_registry.set_client_equalizer.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_compressor_unknown_client_raises(self, multiroom_equalizer_service, mock_registry):
        mock_registry.get_client.return_value = None
        with pytest.raises(ValueError, match="Client not found"):
            await multiroom_equalizer_service.update_compressor("client", "unknown-mac", enabled=True)


# =============================================================================
# Event broadcasting
# =============================================================================

class TestEventBroadcasting:
    @pytest.mark.asyncio
    async def test_apply_zone_equalizer_does_not_broadcast_directly(self, multiroom_equalizer_service, mock_registry, mock_state_machine, sample_zone, sample_equalizer_settings):
        """Full-record applies don't broadcast; partial updates and the registry do."""
        mock_registry.get_zone.return_value = sample_zone
        await multiroom_equalizer_service.apply_zone_equalizer("zone-123", sample_equalizer_settings)
        mock_state_machine.broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_does_not_broadcast_directly(self, multiroom_equalizer_service, mock_registry, mock_state_machine, local_client, sample_equalizer_settings):
        mock_registry.get_client.return_value = local_client
        await multiroom_equalizer_service.apply_client_equalizer("local", sample_equalizer_settings)
        mock_state_machine.broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_without_state_machine(self, mock_registry, mock_camilladsp_service, sample_zone, sample_equalizer_settings):
        service = MultiroomEqualizerService(
            client_registry_service=mock_registry,
            camilladsp_service=mock_camilladsp_service,
        )
        mock_registry.get_zone.return_value = sample_zone
        result = await service.apply_zone_equalizer("zone-123", sample_equalizer_settings)
        assert result is True


# =============================================================================
# Per-client effects-enabled toggle (Phase 3 — Option A)
#
# The /client/{mac}/enabled route routes through set_client_equalizer_effects_enabled
# instead of the old equalizer_router + _persist_remote path; the zone enabled
# fan-out shares the same per-member primitive (_set_remote_client_enabled).
# =============================================================================

class TestClientEffectsEnabled:
    @pytest.fixture
    def routing_service(self):
        routing = Mock()
        routing.set_equalizer_effects_enabled = AsyncMock(return_value=True)
        return routing

    @pytest.fixture
    def proxy_service(self):
        proxy = Mock()
        proxy.request = AsyncMock(return_value={"status": "success"})
        return proxy

    @pytest.fixture
    def multiroom_equalizer_service(
        self, mock_registry, mock_camilladsp_service, mock_state_machine,
        routing_service, proxy_service,
    ):
        """Fully-wired service — every dep is constructor-injected in production."""
        return MultiroomEqualizerService(
            client_registry_service=mock_registry,
            camilladsp_service=mock_camilladsp_service,
            proxy_service=proxy_service,
            routing_service=routing_service,
            state_machine=mock_state_machine,
        )

    @pytest.mark.asyncio
    async def test_local_uses_routing_service(self, multiroom_equalizer_service, mock_registry, routing_service):
        """Local client → routing bypass/restore; never persisted to the registry
        (its EQ lives in equalizer.json)."""
        result = await multiroom_equalizer_service.set_client_equalizer_effects_enabled("local", False)
        assert result is True
        routing_service.set_equalizer_effects_enabled.assert_awaited_once_with(False)
        mock_registry.set_client_equalizer.assert_not_called()

    @pytest.mark.asyncio
    async def test_local_no_routing_returns_false(self, mock_registry, mock_camilladsp_service):
        service = MultiroomEqualizerService(
            client_registry_service=mock_registry,
            camilladsp_service=mock_camilladsp_service,
        )
        result = await service.set_client_equalizer_effects_enabled("local", False)
        assert result is False

    @pytest.mark.asyncio
    async def test_remote_online_pushes_and_persists(self, multiroom_equalizer_service, mock_registry, proxy_service, remote_client):
        mock_registry.get_client.return_value = remote_client  # online, 192.168.1.100
        mock_registry.get_client_equalizer.return_value = None  # fresh → default fallback
        result = await multiroom_equalizer_service.set_client_equalizer_effects_enabled("milo-client-1", False)
        assert result is True
        proxy_service.request.assert_awaited_once_with(
            "192.168.1.100", "PUT", "/equalizer/enabled", {"enabled": False}
        )
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.enabled is False

    @pytest.mark.asyncio
    async def test_remote_offline_persists_only(self, multiroom_equalizer_service, mock_registry, proxy_service):
        """Offline remote → no push, but the flag is persisted so it syncs on reconnect."""
        offline = Client(mac_id="milo-client-1", name="Bedroom", ip="192.168.1.100", online=False, zone_id=None)
        mock_registry.get_client.return_value = offline
        mock_registry.get_client_equalizer.return_value = None
        result = await multiroom_equalizer_service.set_client_equalizer_effects_enabled("milo-client-1", True)
        assert result is True
        proxy_service.request.assert_not_called()
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.enabled is True

    @pytest.mark.asyncio
    async def test_remote_preserves_existing_record(self, multiroom_equalizer_service, mock_registry, proxy_service, remote_client, sample_equalizer_settings):
        """Flipping enabled keeps the rest of the record intact (and writes a copy)."""
        mock_registry.get_client.return_value = remote_client
        mock_registry.get_client_equalizer.return_value = sample_equalizer_settings
        await multiroom_equalizer_service.set_client_equalizer_effects_enabled("milo-client-1", False)
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.enabled is False
        assert persisted.compressor.enabled is True  # preserved from existing record
        assert persisted is not sample_equalizer_settings  # a copy, never the stored object

    @pytest.mark.asyncio
    async def test_remote_unknown_client_raises(self, multiroom_equalizer_service, mock_registry, proxy_service):
        """An enabled toggle for a MAC the registry has never seen fails loud (→ 404)."""
        mock_registry.get_client.return_value = None
        with pytest.raises(ValueError, match="Client not found"):
            await multiroom_equalizer_service.set_client_equalizer_effects_enabled("unknown-mac", False)
        mock_registry.set_client_equalizer.assert_not_called()


class TestZoneEffectsEnabled:
    @pytest.fixture
    def routing_service(self):
        routing = Mock()
        routing.set_equalizer_effects_enabled = AsyncMock(return_value=True)
        return routing

    @pytest.fixture
    def proxy_service(self):
        proxy = Mock()
        proxy.request = AsyncMock(return_value={"status": "success"})
        return proxy

    @pytest.fixture
    def multiroom_equalizer_service(
        self, mock_registry, mock_camilladsp_service, mock_state_machine,
        routing_service, proxy_service,
    ):
        """Fully-wired service — every dep is constructor-injected in production."""
        return MultiroomEqualizerService(
            client_registry_service=mock_registry,
            camilladsp_service=mock_camilladsp_service,
            proxy_service=proxy_service,
            routing_service=routing_service,
            state_machine=mock_state_machine,
        )

    @pytest.mark.asyncio
    async def test_zone_fans_out_local_and_remote_then_broadcasts(self, multiroom_equalizer_service, mock_registry, mock_state_machine, routing_service, proxy_service, sample_zone, remote_client):
        mock_registry.get_zone.return_value = sample_zone  # ["local", "milo-client-1"]
        mock_registry.get_client.side_effect = lambda mac: remote_client if mac == "milo-client-1" else None
        mock_registry.get_client_equalizer.return_value = None

        result = await multiroom_equalizer_service.set_zone_equalizer_effects_enabled("zone-123", False)

        assert result is True
        routing_service.set_equalizer_effects_enabled.assert_awaited_once_with(False)  # local member
        proxy_service.request.assert_awaited_once_with(
            "192.168.1.100", "PUT", "/equalizer/enabled", {"enabled": False}
        )  # remote member pushed
        persisted = mock_registry.set_client_equalizer.call_args.args[1]
        assert persisted.enabled is False  # remote member's flag persisted
        event = mock_state_machine.broadcast.call_args.args[0]
        assert (event.CATEGORY, event.TYPE) == ("equalizer", "zone_enabled_changed")
        assert event.wire_data() == {"zone_id": "zone-123", "enabled": False}

    @pytest.mark.asyncio
    async def test_zone_not_found_raises(self, multiroom_equalizer_service, mock_registry):
        mock_registry.get_zone.return_value = None
        with pytest.raises(ValueError, match="Zone not found"):
            await multiroom_equalizer_service.set_zone_equalizer_effects_enabled("nope", True)


# =============================================================================
# LOCAL_TARGET sentinel — the local device is addressable as "local" without a
# registry entry (multiroom OFF: the registry is empty / unaware of "local").
# This lets the uniform per-target API address the local client uniformly.
# =============================================================================

class TestLocalTargetSentinel:
    @pytest.mark.asyncio
    async def test_get_client_eq_local_sentinel_reads_camilladsp(self, offline_service, mock_camilladsp_service):
        snap = EqualizerSettings.default()
        snap.active_preset = "rock"
        mock_camilladsp_service.get_equalizer_settings = Mock(return_value=snap)
        result = await offline_service.get_client_eq("local")
        assert result is snap  # sentinel → CamillaDSP even though the registry is empty

    @pytest.mark.asyncio
    async def test_set_client_eq_local_sentinel_applies_no_registry_write(
        self, offline_service, offline_registry, mock_camilladsp_service, sample_equalizer_settings
    ):
        result = await offline_service.set_client_eq("local", sample_equalizer_settings)
        assert result is True
        mock_camilladsp_service.apply_settings.assert_awaited_once()  # applied to the DAC
        mock_camilladsp_service.persist_state.assert_awaited_once()  # persisted to equalizer.json
        offline_registry.set_client_equalizer.assert_not_called()  # no phantom registry record

    @pytest.mark.asyncio
    async def test_update_filter_local_sentinel_no_phantom_record(
        self, offline_service, offline_registry, mock_camilladsp_service
    ):
        # Must NOT raise "Client not found: local" and must NOT write a registry record;
        # the local member is persisted from its live DSP cache instead.
        result = await offline_service.update_filter("client", "local", "eq_band_00", gain=4.0)
        assert result is True
        offline_registry.set_client_equalizer.assert_not_called()
        mock_camilladsp_service.persist_state.assert_awaited()

    @pytest.mark.asyncio
    async def test_set_client_equalizer_effects_enabled_local_sentinel_routes_to_routing(
        self, offline_service
    ):
        routing = Mock()
        routing.set_equalizer_effects_enabled = AsyncMock(return_value=True)
        result = await offline_service.set_client_equalizer_effects_enabled("local", False, routing)
        assert result is True
        routing.set_equalizer_effects_enabled.assert_awaited_once_with(False)

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_local_sentinel_skips_registry_validation(
        self, offline_service, mock_camilladsp_service, sample_equalizer_settings
    ):
        # get_client("local") is None in multiroom-off; must not raise "Client not found".
        result = await offline_service.apply_client_equalizer("local", sample_equalizer_settings)
        assert result is True
        mock_camilladsp_service.persist_state.assert_awaited()


# =============================================================================
# EQ-independent zone members
# =============================================================================

class TestEqIndependentMembers:
    """A zone member can detach its EQ (eq_independent): it stays in the zone for
    synchronized playback, but every zone-EQ operation skips it and it is edited
    directly as a client. Complements the zone fan-out / raise tests above, which
    cover the default (no member independent) because mock_registry.get_client
    returns None there — so nothing is independent unless a test says so."""

    @staticmethod
    def _member(mac_id, *, eq_independent=False, zone_id="z"):
        return Client(
            mac_id=mac_id, name=mac_id, ip="192.168.1.9",
            online=True, zone_id=zone_id, eq_independent=eq_independent,
        )

    @staticmethod
    def _rec(enabled=True):
        rec = EqualizerSettings.default()
        rec.enabled = enabled
        return rec

    def _wire(self, mock_registry, macs):
        """Point the registry at a set of members and a shared record for each."""
        zone = Zone(id="z", name="Pair", client_ids=list(macs))
        mock_registry.get_zone.return_value = zone
        mock_registry.get_client = Mock(side_effect=macs.get)
        mock_registry.get_client_equalizer = Mock(side_effect=lambda m: self._rec())
        return zone

    @pytest.mark.asyncio
    async def test_set_zone_eq_skips_an_independent_member(
        self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings
    ):
        self._wire(mock_registry, {
            "milo-client-1": self._member("milo-client-1", eq_independent=True),
            "milo-client-2": self._member("milo-client-2", eq_independent=False),
        })

        await multiroom_equalizer_service.set_zone_eq("z", sample_equalizer_settings)

        # Only the shared member receives the zone EQ.
        macs = [c.args[0] for c in mock_registry.set_client_equalizer.call_args_list]
        assert macs == ["milo-client-2"]

    @pytest.mark.asyncio
    async def test_get_zone_eq_ignores_an_independent_member(
        self, multiroom_equalizer_service, mock_registry
    ):
        self._wire(mock_registry, {
            "milo-client-1": self._member("milo-client-1", eq_independent=True),
            "milo-client-2": self._member("milo-client-2", eq_independent=False),
        })
        # The independent member is bypassed; it must NOT drag the zone to enabled=False.
        records = {"milo-client-1": self._rec(enabled=False), "milo-client-2": self._rec(enabled=True)}
        mock_registry.get_client_equalizer = Mock(side_effect=records.get)

        result = await multiroom_equalizer_service.get_zone_eq("z")

        assert result.enabled is True

    @pytest.mark.asyncio
    async def test_get_zone_eq_none_when_every_member_is_independent(
        self, multiroom_equalizer_service, mock_registry
    ):
        self._wire(mock_registry, {
            "milo-client-1": self._member("milo-client-1", eq_independent=True),
            "milo-client-2": self._member("milo-client-2", eq_independent=True),
        })

        assert await multiroom_equalizer_service.get_zone_eq("z") is None

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_allows_an_independent_member(
        self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings
    ):
        """A zoned member that detached its EQ is addressed directly — no raise."""
        mock_registry.get_client = Mock(
            return_value=self._member("milo-client-1", eq_independent=True)
        )

        result = await multiroom_equalizer_service.apply_client_equalizer(
            "milo-client-1", sample_equalizer_settings
        )

        assert result is True
        mock_registry.set_client_equalizer.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_client_equalizer_still_raises_for_a_shared_member(
        self, multiroom_equalizer_service, mock_registry, sample_equalizer_settings
    ):
        """The other half: a shared (non-independent) zone member still routes
        through the zone, so the guard must still fire."""
        mock_registry.get_client = Mock(
            return_value=self._member("milo-client-1", eq_independent=False)
        )

        with pytest.raises(ValueError, match="is in zone"):
            await multiroom_equalizer_service.apply_client_equalizer(
                "milo-client-1", sample_equalizer_settings
            )

    @pytest.mark.asyncio
    async def test_partial_update_skips_an_independent_member(
        self, multiroom_equalizer_service, mock_registry
    ):
        self._wire(mock_registry, {
            "milo-client-1": self._member("milo-client-1", eq_independent=True),
            "milo-client-2": self._member("milo-client-2", eq_independent=False),
        })

        await multiroom_equalizer_service.update_mono("zone", "z", enabled=True)

        persisted = [c.args[0] for c in mock_registry.set_client_equalizer.call_args_list]
        assert persisted == ["milo-client-2"]
