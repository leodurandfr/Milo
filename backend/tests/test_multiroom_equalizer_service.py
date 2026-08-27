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
import logging

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
# Helpers
# =============================================================================

def batched_records(registry):
    """The {mac: record} mapping a partial update persists in ONE registry write.

    The fan-out writes every member in a single call, because each call rewrites
    the whole of settings.json: asserting on the mapping is asserting that the
    batch happened at all.
    """
    registry.set_clients_equalizer.assert_awaited_once()
    return registry.set_clients_equalizer.await_args.args[0]


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
    registry.set_clients_equalizer = AsyncMock()
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
    cam.schedule_persist = Mock()
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
    registry.set_clients_equalizer = AsyncMock()
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
    resolves from the target's own custom_gains, falling back to the local DAC's
    saved curve for the LOCAL target only, and to a flat default everywhere else
    — the branches the indirect load_*_preset tests never exercise."""

    @pytest.mark.asyncio
    async def test_resolve_builtin_returns_preset_gains(self, multiroom_equalizer_service):
        from backend.core.equalizer.presets import get_preset_by_id

        gains = await multiroom_equalizer_service.resolve_preset_gains(
            "rock", None, "client", "local"
        )
        assert gains == get_preset_by_id("rock")["gains"]

    @pytest.mark.asyncio
    async def test_resolve_unknown_preset_raises(self, multiroom_equalizer_service):
        with pytest.raises(ValueError, match="Preset not found"):
            await multiroom_equalizer_service.resolve_preset_gains(
                "does_not_exist", None, "client", "local"
            )

    @pytest.mark.asyncio
    async def test_resolve_custom_from_settings(self, multiroom_equalizer_service):
        settings = EqualizerSettings(custom_gains=[2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0])
        gains = await multiroom_equalizer_service.resolve_preset_gains(
            "custom", settings, "client", "local"
        )
        assert gains == settings.custom_gains

    @pytest.mark.asyncio
    async def test_resolve_custom_falls_back_to_camilladsp_for_local(self, multiroom_equalizer_service, mock_camilladsp_service):
        # No per-target custom_gains → use the local DAC's saved curve (distinct
        # from the flat DEFAULT so the branch is unambiguous).
        mock_camilladsp_service.get_custom_gains = AsyncMock(return_value=[1.5] * 10)
        gains = await multiroom_equalizer_service.resolve_preset_gains(
            "custom", None, "client", "local"
        )
        assert gains == [1.5] * 10

    @pytest.mark.asyncio
    async def test_resolve_custom_for_a_satellite_never_uses_the_server_curve(self, multiroom_equalizer_service, mock_camilladsp_service):
        """Loading "custom" on a satellite that never saved one must not dress it
        in the SERVER's curve — it would play a tuning nobody chose for it, and
        the read route (GET /target/{target}) already refuses to display that."""
        from backend.core.equalizer.presets import DEFAULT_CUSTOM_GAINS

        mock_camilladsp_service.get_custom_gains = AsyncMock(return_value=[1.5] * 10)
        gains = await multiroom_equalizer_service.resolve_preset_gains(
            "custom", None, "client", "milo-client-1"
        )
        assert gains == DEFAULT_CUSTOM_GAINS
        mock_camilladsp_service.get_custom_gains.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resolve_custom_for_a_zone_never_uses_the_server_curve(self, multiroom_equalizer_service, mock_camilladsp_service):
        """Same for a zone: a zone containing the local member reads its curve
        through the record (get_zone_eq), never through this fallback."""
        from backend.core.equalizer.presets import DEFAULT_CUSTOM_GAINS

        mock_camilladsp_service.get_custom_gains = AsyncMock(return_value=[1.5] * 10)
        gains = await multiroom_equalizer_service.resolve_preset_gains(
            "custom", None, "zone", "zone-123"
        )
        assert gains == DEFAULT_CUSTOM_GAINS
        mock_camilladsp_service.get_custom_gains.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resolve_custom_falls_back_to_default(self, mock_registry):
        from backend.core.equalizer.presets import DEFAULT_CUSTOM_GAINS

        # A service with no CamillaDSP and no per-target gains → the flat default.
        service = MultiroomEqualizerService(client_registry_service=mock_registry)
        gains = await service.resolve_preset_gains("custom", None, "client", "local")
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

class TestZoneFanoutOutcome:
    """A zone write reports what actually happened to its members.

    The fan-out used to return a hardcoded True and log "applied to all members"
    unconditionally: a satellite that refused kept playing the old curve while
    the UI drew the new one, with nothing in the journal and nothing in the
    banner. These tests drive the fan-out through a proxy that fails for one
    member only.
    """

    ZONE_MEMBERS = ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"]
    REFUSING_IP = "192.168.1.11"

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

    @pytest.fixture
    def two_remote_members(self, mock_registry):
        """A zone of two online satellites — no local member, so every write goes
        through the proxy."""
        clients = {
            self.ZONE_MEMBERS[0]: Client(
                mac_id=self.ZONE_MEMBERS[0], name="A", ip="192.168.1.10",
                online=True, zone_id="z",
            ),
            self.ZONE_MEMBERS[1]: Client(
                mac_id=self.ZONE_MEMBERS[1], name="B", ip=self.REFUSING_IP,
                online=True, zone_id="z",
            ),
        }
        mock_registry.get_zone.return_value = Zone(
            id="z", name="Pair", client_ids=list(clients)
        )
        mock_registry.get_client.side_effect = clients.get
        mock_registry.get_online_zone_clients.return_value = list(clients.values())
        return clients

    @pytest.mark.asyncio
    async def test_a_member_that_refused_makes_the_zone_write_fail(
        self, service, proxy_service, two_remote_members, sample_equalizer_settings, caplog
    ):
        proxy_service.apply_record = AsyncMock(
            side_effect=lambda ip, _settings: ip != self.REFUSING_IP
        )

        with caplog.at_level(logging.ERROR):
            result = await service.set_zone_eq("z", sample_equalizer_settings)

        assert result is False
        # The failing member is named: it is the only thing that tells the owner
        # which speaker kept the old curve.
        assert self.ZONE_MEMBERS[1] in caplog.text
        assert self.ZONE_MEMBERS[0] not in caplog.text

    @pytest.mark.asyncio
    async def test_a_zone_write_that_reached_everyone_still_succeeds(
        self, service, two_remote_members, sample_equalizer_settings, caplog
    ):
        with caplog.at_level(logging.INFO):
            result = await service.apply_zone_equalizer("z", sample_equalizer_settings)

        assert result is True
        assert "applied to all members" in caplog.text

    @pytest.mark.asyncio
    async def test_applied_to_all_members_is_not_logged_when_it_is_untrue(
        self, service, proxy_service, two_remote_members, sample_equalizer_settings, caplog
    ):
        proxy_service.apply_record = AsyncMock(return_value=False)

        with caplog.at_level(logging.INFO):
            assert await service.apply_zone_equalizer("z", sample_equalizer_settings) is False

        assert "applied to all members" not in caplog.text

    @pytest.mark.asyncio
    async def test_a_partial_update_surfaces_an_unreachable_member(
        self, service, mock_registry, mock_state_machine, two_remote_members
    ):
        """The zone branch used to absorb member exceptions in its gather while
        the same write to a single client raised through to a 503 — two opposite
        answers for one operation."""
        from backend.core.equalizer.client_proxy import SatelliteUnreachable

        router = Mock()
        router.update_filter = AsyncMock(side_effect=[
            {"status": "success"},
            SatelliteUnreachable(self.REFUSING_IP, "Cannot reach client", 503),
        ])
        service._equalizer_router = router

        with pytest.raises(SatelliteUnreachable):
            await service.update_filter("zone", "z", "eq_band_00", gain=5.0)

        # Everything that did succeed is still committed: the records are stored
        # (they sync to the absent member on reconnection) and the broadcast fired.
        assert set(batched_records(mock_registry)) == set(self.ZONE_MEMBERS)
        mock_state_machine.broadcast.assert_called_once()

    @pytest.fixture
    def mixed_enabled_members(self, mock_registry):
        """One member applying effects, one bypassed — so the zone's derived
        `enabled` is False while a member's own is True."""
        def record(enabled):
            settings = EqualizerSettings.default()
            settings.enabled = enabled
            return settings

        stored = {
            self.ZONE_MEMBERS[0]: record(True),
            self.ZONE_MEMBERS[1]: record(False),
        }
        mock_registry.get_client_equalizer.side_effect = stored.get
        return stored

    @pytest.mark.asyncio
    async def test_a_zone_fanout_keeps_each_members_own_enabled(
        self, service, mock_registry, proxy_service, two_remote_members, mixed_enabled_members
    ):
        """`enabled` is derived on read — the conjunction of the members' own
        flags — so writing it back into a member bypasses a satellite because
        ANOTHER member is bypassed. On a fresh unit the local member's flag is
        False by default, which makes the conjunction False for every zone.
        """
        zone_record = await service.get_zone_eq("z")
        assert zone_record.enabled is False  # the conjunction, as reported

        await service.set_zone_eq("z", zone_record)

        persisted = {
            call.args[0]: call.args[1]
            for call in mock_registry.set_client_equalizer.call_args_list
        }
        assert persisted[self.ZONE_MEMBERS[0]].enabled is True
        assert persisted[self.ZONE_MEMBERS[1]].enabled is False
        # And each satellite is sent its own flag, not the zone's conjunction.
        pushed = {
            call.args[0]: call.args[1] for call in proxy_service.apply_record.await_args_list
        }
        assert pushed["192.168.1.10"].enabled is True
        assert pushed[self.REFUSING_IP].enabled is False

    @pytest.mark.asyncio
    async def test_a_partial_update_keeps_each_members_own_enabled(
        self, service, mock_registry, two_remote_members, mixed_enabled_members
    ):
        """Same on the targeted path — dragging one EQ band is what runs it."""
        service._equalizer_router = None

        await service.update_filter("zone", "z", "eq_band_00", gain=5.0)

        persisted = batched_records(mock_registry)
        assert persisted[self.ZONE_MEMBERS[0]].filters[0].gain == 5.0
        assert persisted[self.ZONE_MEMBERS[0]].enabled is True
        assert persisted[self.ZONE_MEMBERS[1]].enabled is False


class TestPartialUpdateMethods:
    @pytest.mark.asyncio
    async def test_update_filter_persists_to_remote_member_and_local(self, multiroom_equalizer_service, mock_registry, mock_camilladsp_service, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.update_filter("zone", "zone-123", "eq_band_00", gain=5.0)
        assert result is True
        (persisted,) = batched_records(mock_registry).values()
        assert persisted.filters[0].gain == 5.0
        # Local member snapshotted — debounced, not written on the spot: this is
        # the drag path, 20 requests a second.
        mock_camilladsp_service.schedule_persist.assert_called()
        mock_camilladsp_service.persist_state.assert_not_called()

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
        mock_camilladsp_service.schedule_persist.assert_not_called()

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
        (persisted,) = batched_records(mock_registry).values()
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
        (persisted,) = batched_records(mock_registry).values()
        assert persisted.compressor.enabled is True
        assert persisted.compressor.threshold == -30.0
        assert persisted.compressor.ratio == 4.0  # preserved

    @pytest.mark.asyncio
    async def test_update_loudness(self, multiroom_equalizer_service, mock_registry, sample_zone):
        mock_registry.get_zone.return_value = sample_zone
        result = await multiroom_equalizer_service.update_loudness("zone", "zone-123", enabled=True, low_boost=10.0)
        assert result is True
        (persisted,) = batched_records(mock_registry).values()
        assert persisted.loudness.enabled is True
        assert persisted.loudness.low_boost == 10.0
        assert persisted.loudness.high_boost == 5.0  # preserved

    @pytest.mark.asyncio
    async def test_update_mono_client(self, multiroom_equalizer_service, mock_registry, remote_client):
        mock_registry.get_client.return_value = remote_client
        mock_registry.get_client_equalizer.return_value = EqualizerSettings.default()
        result = await multiroom_equalizer_service.update_mono("client", "milo-client-1", enabled=True)
        assert result is True
        (persisted,) = batched_records(mock_registry).values()
        assert persisted.mono is True

    @pytest.mark.asyncio
    async def test_update_filter_unknown_client_raises(self, multiroom_equalizer_service, mock_registry):
        """A partial update to a MAC the registry has never seen fails loud (→ 404),
        rather than silently materializing a phantom per-client record."""
        mock_registry.get_client.return_value = None
        with pytest.raises(ValueError, match="Client not found"):
            await multiroom_equalizer_service.update_filter("client", "unknown-mac", "eq_band_00", gain=5.0)
        mock_registry.set_clients_equalizer.assert_not_called()

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
        offline_registry.set_clients_equalizer.assert_not_called()
        mock_camilladsp_service.schedule_persist.assert_called()

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

        assert list(batched_records(mock_registry)) == ["milo-client-2"]


class TestRemotePushRefusals:
    """`_apply_to_remote`'s four refusal arms, and why two of them answer True.

    Every one was at zero. The distinction they encode is the whole reason the
    reconnection sync exists: "the satellite is asleep" is not the same failure
    as "the satellite is here and would not take it". The first is normal — a
    speaker in an empty room — and reported as a failure it would put an error
    banner in front of the user every time they touch a zone. The second must
    surface, because nothing retries it.
    """

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
    async def test_no_proxy_service_is_not_a_failure(
        self, mock_registry, mock_camilladsp_service, mock_state_machine,
        sample_equalizer_settings, caplog
    ):
        """The record is already persisted by the time this runs; the reconnection
        sync replays it. Answered False, a unit built without the proxy would
        report every zone write as failed while the state on disk is correct."""
        service = MultiroomEqualizerService(
            client_registry_service=mock_registry,
            camilladsp_service=mock_camilladsp_service,
            proxy_service=None,
            state_machine=mock_state_machine,
        )

        with caplog.at_level(logging.DEBUG):
            assert await service._apply_to_remote("aa:bb", sample_equalizer_settings) is True

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    @pytest.mark.asyncio
    async def test_no_registry_is_a_failure(
        self, service, sample_equalizer_settings, caplog
    ):
        """Without the registry there is no address to push to and no record to
        replay later — nothing will ever sync this client. That is a real
        failure, not a deferral."""
        service._registry = None

        with caplog.at_level(logging.WARNING):
            assert await service._apply_to_remote("aa:bb", sample_equalizer_settings) is False

        assert "Registry not available" in caplog.text

    @pytest.mark.asyncio
    async def test_an_unknown_client_is_deferred_not_failed(
        self, service, mock_registry, sample_equalizer_settings, caplog
    ):
        """A zone can name a client the registry has not admitted yet — the
        admission race the four snapserver notifications share. Deferred, the
        record is applied when it arrives."""
        mock_registry.get_client.return_value = None

        with caplog.at_level(logging.DEBUG):
            assert await service._apply_to_remote("aa:bb", sample_equalizer_settings) is True

        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    @pytest.mark.asyncio
    async def test_an_offline_client_is_deferred_not_failed(
        self, service, mock_registry, sample_equalizer_settings, proxy_service, caplog
    ):
        """A speaker switched off is the ordinary state of a multiroom house.

        Reported as a failure it would raise a banner on every EQ change; and
        the push must not even be attempted, or each one costs a TCP connect
        that has to time out.
        """
        offline = Client(
            mac_id="aa:bb", name="Bureau", ip="192.168.1.60",
            host="milo-client-2", online=False,
        )
        mock_registry.get_client.return_value = offline

        with caplog.at_level(logging.DEBUG):
            assert await service._apply_to_remote("aa:bb", sample_equalizer_settings) is True

        proxy_service.apply_record.assert_not_awaited()
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    @pytest.mark.asyncio
    async def test_an_online_client_with_no_address_is_a_failure(
        self, service, mock_registry, sample_equalizer_settings, proxy_service, caplog
    ):
        """Online but unaddressable is a registry inconsistency, not a sleeping
        speaker: nothing will resolve it on its own. Deferred silently, that
        client's EQ would never be applied and nothing would ever say so."""
        addressless = Client(
            mac_id="aa:bb", name="Bureau", ip=None, host="milo-client-2", online=True,
        )
        mock_registry.get_client.return_value = addressless

        with caplog.at_level(logging.WARNING):
            assert await service._apply_to_remote("aa:bb", sample_equalizer_settings) is False

        proxy_service.apply_record.assert_not_awaited()
        assert "has no IP" in caplog.text


class TestZoneEnabledFanout:
    """`set_zone_equalizer_effects_enabled` — the master toggle over a whole zone.

    It is the one setting applied *last*, after the effects it gates, and it
    reaches the local client and the satellites by two entirely different
    mechanisms: the routing service's DSP bypass locally, an HTTP PUT remotely.
    Both arms were at zero.
    """

    @pytest.fixture
    def proxy_service(self):
        proxy = Mock()
        proxy.request = AsyncMock(return_value={"status": "success"})
        proxy.apply_record = AsyncMock(return_value=True)
        return proxy

    @pytest.fixture
    def service(self, mock_registry, mock_camilladsp_service, mock_state_machine, proxy_service):
        svc = MultiroomEqualizerService(
            client_registry_service=mock_registry,
            camilladsp_service=mock_camilladsp_service,
            proxy_service=proxy_service,
            state_machine=mock_state_machine,
        )
        svc._routing_service = Mock()
        svc._routing_service.set_equalizer_effects_enabled = AsyncMock(return_value=True)
        return svc

    @staticmethod
    def _zone(registry, members):
        registry.get_zone = Mock(return_value=Zone(
            id="zone-1", name="Salon", client_ids=list(members)
        ))
        registry.get_client = Mock(side_effect=lambda mac: Client(
            mac_id=mac, name=mac, ip=f"192.168.1.{mac[-1]}", host=mac, online=True,
        ))

    @pytest.mark.asyncio
    async def test_a_missing_zone_raises_rather_than_reporting_success(
        self, service, mock_registry
    ):
        """The route turns this into a 404. Answered False, the UI would show a
        generic failure for a zone the user just deleted in another tab."""
        mock_registry.get_zone = Mock(return_value=None)

        with pytest.raises(ValueError, match="Zone not found"):
            await service.set_zone_equalizer_effects_enabled("gone", True)

    @pytest.mark.asyncio
    async def test_no_registry_is_reported_not_raised(self, service, caplog):
        """Distinct from the missing zone above: there is no registry to ask, so
        the answer is "could not", not "does not exist"."""
        service._registry = None

        with caplog.at_level(logging.ERROR):
            assert await service.set_zone_equalizer_effects_enabled("zone-1", True) is False

        assert "ClientRegistryService not available" in caplog.text

    @pytest.mark.asyncio
    async def test_the_local_member_goes_through_the_routing_service(
        self, service, mock_registry, proxy_service
    ):
        """Locally the toggle is a DSP bypass, not an HTTP call — the routing
        service owns it because it also persists `routing.equalizer_effects_enabled`
        and re-broadcasts `full_state`. Pushed over HTTP instead, the local
        client would be asked to proxy to itself."""
        self._zone(mock_registry, ["local", "aa:bb:cc:dd:ee:02"])

        assert await service.set_zone_equalizer_effects_enabled("zone-1", False) is True

        service._routing_service.set_equalizer_effects_enabled.assert_awaited_once_with(False)
        assert proxy_service.request.await_count == 1

    @pytest.mark.asyncio
    async def test_a_local_failure_does_not_stop_the_satellites(
        self, service, mock_registry, proxy_service, caplog
    ):
        """A DSP that refuses its bypass must not leave the rest of the zone at
        the old setting — half a room with effects on is worse than none."""
        self._zone(mock_registry, ["local", "aa:bb:cc:dd:ee:02"])
        service._routing_service.set_equalizer_effects_enabled = AsyncMock(
            side_effect=RuntimeError("daemon down")
        )

        with caplog.at_level(logging.WARNING):
            assert await service.set_zone_equalizer_effects_enabled("zone-1", False) is True

        proxy_service.request.assert_awaited_once()
        assert "Failed to set equalizer enabled for local" in caplog.text

    @pytest.mark.asyncio
    async def test_a_zone_whose_members_all_refuse_reports_failure(
        self, service, mock_registry, proxy_service
    ):
        """"At least one client updated" is the contract. All refused is the one
        case the UI must not draw as applied."""
        self._zone(mock_registry, ["aa:bb:cc:dd:ee:01", "aa:bb:cc:dd:ee:02"])
        proxy_service.request = AsyncMock(return_value={"status": "error"})

        assert await service.set_zone_equalizer_effects_enabled("zone-1", True) is False

    @pytest.mark.asyncio
    async def test_a_zone_with_no_routing_service_reports_nothing_wrong(
        self, service, mock_registry, proxy_service, caplog
    ):
        """The guard's only effect is the absence of a warning, and that is the
        whole assertion.

        `dependencies.py` always constructs this service with the routing
        service, so None is a dev-host shape. And the `except Exception` inside
        the guard would catch the `AttributeError` an unguarded call raises, log
        it, and let the satellites through anyway — so the fan-out's OUTCOME
        cannot separate the two versions. What separates them is a WARNING per
        zone toggle in `errors.log`, on a unit where nothing is actually wrong.
        Same family as `_load_config_from_settings`'s inert guard in B5, except
        this one is worth keeping for that reason.
        """
        self._zone(mock_registry, ["local", "aa:bb:cc:dd:ee:02"])
        service._routing_service = None

        with caplog.at_level(logging.WARNING):
            assert await service.set_zone_equalizer_effects_enabled("zone-1", True) is True

        proxy_service.request.assert_awaited_once()
        assert caplog.text == "", \
            "a unit with no routing service was reported as a failed local toggle"


class TestRemoteEnabledPush:
    """`_set_remote_client_enabled` — the master toggle for one satellite."""

    @pytest.fixture
    def proxy_service(self):
        proxy = Mock()
        proxy.request = AsyncMock(return_value={"status": "success"})
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
    async def test_a_push_that_raises_is_reported_and_the_record_still_persists(
        self, service, mock_registry, proxy_service, caplog
    ):
        """The persist is what the reconnection sync replays. Skipped because the
        push raised, a satellite that was unreachable for one second keeps the
        old master state for ever.
        """
        mock_registry.get_client = Mock(return_value=Client(
            mac_id="aa:bb", name="Bureau", ip="192.168.1.60",
            host="milo-client-2", online=True,
        ))
        proxy_service.request = AsyncMock(side_effect=RuntimeError("unreachable"))

        with caplog.at_level(logging.WARNING):
            result = await service._set_remote_client_enabled(
                "aa:bb", False, fallback=EqualizerSettings.default_for_zone
            )

        assert result is False
        assert "Failed to push equalizer enabled" in caplog.text
        mock_registry.set_client_equalizer.assert_awaited_once()
        assert mock_registry.set_client_equalizer.await_args.args[1].enabled is False

    @pytest.mark.asyncio
    async def test_an_offline_client_persists_and_reports_success(
        self, service, mock_registry, proxy_service
    ):
        """A speaker that is off cannot refuse. Reported as a failure, turning
        the effects off for a zone with one sleeping member would always look
        broken."""
        mock_registry.get_client = Mock(return_value=Client(
            mac_id="aa:bb", name="Bureau", ip="192.168.1.60",
            host="milo-client-2", online=False,
        ))

        result = await service._set_remote_client_enabled(
            "aa:bb", True, fallback=EqualizerSettings.default_for_zone
        )

        assert result is True
        proxy_service.request.assert_not_awaited()
        mock_registry.set_client_equalizer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_existing_record_keeps_its_tuning_when_only_the_gate_moves(
        self, service, mock_registry, proxy_service, sample_equalizer_settings
    ):
        """Bands carry tuning only; the master toggle is pipeline membership.

        Replacing the record with the fallback default would silently flatten
        the user's curve every time they switched the effects off and on.
        """
        mock_registry.get_client = Mock(return_value=Client(
            mac_id="aa:bb", name="Bureau", ip="192.168.1.60",
            host="milo-client-2", online=True,
        ))
        mock_registry.get_client_equalizer = Mock(return_value=sample_equalizer_settings)

        await service._set_remote_client_enabled(
            "aa:bb", False, fallback=EqualizerSettings.default_for_zone
        )

        stored = mock_registry.set_client_equalizer.await_args.args[1]
        assert stored.enabled is False
        assert stored.filters[0].gain == sample_equalizer_settings.filters[0].gain

    @pytest.mark.asyncio
    async def test_a_client_with_no_record_yet_starts_from_the_fallback(
        self, service, mock_registry, proxy_service
    ):
        """A satellite adopted since the last write has nothing stored. Without
        the fallback the persist would write None and the reconnection sync
        would have nothing to replay."""
        mock_registry.get_client = Mock(return_value=Client(
            mac_id="aa:bb", name="Bureau", ip="192.168.1.60",
            host="milo-client-2", online=True,
        ))
        mock_registry.get_client_equalizer = Mock(return_value=None)

        await service._set_remote_client_enabled(
            "aa:bb", True, fallback=EqualizerSettings.default_for_zone
        )

        stored = mock_registry.set_client_equalizer.await_args.args[1]
        assert isinstance(stored, EqualizerSettings)
        assert stored.enabled is True
