# backend/tests/test_ws_events.py
"""
Envelope tests for the typed WS event layer.

Every WsEvent subclass must serialize to {category, type, origin, data,
timestamp}, and the frontend and Milo-Mac both key on the exact contents.
Each case below pins one representative event per family (state machine,
audio source, volume, settings, programs, equalizer, multiroom, routing,
network, hardware) against its full expected envelope, timestamp excluded —
including which categories carry full_state and which fields are dropped
rather than sent as null.
"""
import pytest

from backend.core.state import AudioStateMachine
from backend.core.models.ws_events import (
    DockAppsChanged,
    DockAppsConfig,
    EqualizerFilterChanged,
    EqualizerLevels,
    MultiroomPendingClientChanged,
    MultiroomZoneChanged,
    NetworkStatusChanged,
    ProgramUpdateComplete,
    RadioFavoriteAdded,
    RoutingMultiroomError,
    SatelliteUpdateProgress,
    ScreenSleepChanged,
    SourceErrorCleared,
    SourcePositionUpdate,
    SourceStateChanged,
    SystemCdDriveStatus,
    SystemConnectivityChanged,
    SystemErrorEvent,
    SystemStateChanged,
    SystemTransitionStart,
    VolumeChanged,
    VolumeLimitsChanged,
    VolumeLimitsConfig,
    VolumeStartupChanged,
    VolumeStartupConfig,
)


@pytest.fixture
def state_machine(mock_ws_manager):
    sm = AudioStateMachine()
    sm.ws_manager = mock_ws_manager
    return sm


# (event, expected envelope minus timestamp/full_state, full_state expected?)
CASES = [
    (
        SystemTransitionStart(),
        {"category": "system", "type": "transition_start", "origin": "system",
         "data": {"source": "system"}},
        True,
    ),
    (
        # EXCLUDE_NONE: the unset multiroom_changed discriminator must be
        # absent from the wire, not null (Milo-Mac keys on its presence).
        SystemStateChanged(source="system"),
        {"category": "system", "type": "state_changed", "origin": "system",
         "data": {"source": "system"}},
        True,
    ),
    (
        SystemErrorEvent(source="spotify", error="Transition timeout",
                         message="Transition timeout after 10.0s"),
        {"category": "system", "type": "error", "origin": "spotify",
         "data": {"source": "spotify", "error": "Transition timeout",
                  "message": "Transition timeout after 10.0s"}},
        True,
    ),
    (
        # metadata=None stays on the wire as null — the frontend distinguishes
        # "no metadata this time" from "key absent"
        SourceStateChanged(source="radio", new_state="ready", metadata=None),
        {"category": "source", "type": "state_changed", "origin": "radio",
         "data": {"source": "radio", "new_state": "ready", "metadata": None}},
        True,
    ),
    (
        SourceErrorCleared(source="bluetooth"),
        {"category": "source", "type": "error_cleared", "origin": "bluetooth",
         "data": {"source": "bluetooth"}},
        True,
    ),
    (
        SourcePositionUpdate(source="spotify", position=1000, duration=200000),
        {"category": "source", "type": "position_update", "origin": "spotify",
         "data": {"source": "spotify", "position": 1000, "duration": 200000}},
        False,
    ),
    (
        VolumeChanged(show_bar=True, step_mobile_db=2.0, multiroom_enabled=False,
                      state={"mode": "direct", "global_volume_db": -30.0}),
        {"category": "volume", "type": "volume_changed", "origin": "volume",
         "data": {"show_bar": True, "step_mobile_db": 2.0,
                  "multiroom_enabled": False,
                  "state": {"mode": "direct", "global_volume_db": -30.0}}},
        False,
    ),
    (
        VolumeLimitsChanged(limits=VolumeLimitsConfig(min_db=-80.0, max_db=-20.0)),
        {"category": "settings", "type": "volume_limits_changed", "origin": "settings",
         "data": {"source": "settings",
                  "limits": {"min_db": -80.0, "max_db": -20.0}}},
        False,
    ),
    (
        VolumeStartupChanged(config=VolumeStartupConfig(
            startup_volume_db=-42.0, restore_last_volume=True)),
        {"category": "settings", "type": "volume_startup_changed", "origin": "settings",
         "data": {"source": "settings",
                  "config": {"startup_volume_db": -42.0, "restore_last_volume": True}}},
        False,
    ),
    (
        DockAppsChanged(config=DockAppsConfig(enabled_apps=["spotify", "radio"])),
        {"category": "settings", "type": "dock_apps_changed", "origin": "settings",
         "data": {"source": "settings",
                  "config": {"enabled_apps": ["spotify", "radio"]}}},
        False,
    ),
    (
        SatelliteUpdateProgress(mac_id="aa:bb:cc:dd:ee:ff"),
        {"category": "programs", "type": "satellite_update_progress",
         "origin": "satellite_update",
         "data": {"source": "satellite_update", "mac_id": "aa:bb:cc:dd:ee:ff",
                  "status": "updating"}},
        False,
    ),
    (
        ProgramUpdateComplete(program="go-librespot", success=True),
        {"category": "programs", "type": "program_update_complete",
         "origin": "program_update",
         "data": {"source": "program_update", "program": "go-librespot",
                  "success": True}},
        False,
    ),
    (
        EqualizerFilterChanged(id="eq_band_00", freq=31.0, gain=3.0, q=1.41,
                               type="Peaking", enabled=True),
        {"category": "equalizer", "type": "filter_changed", "origin": "equalizer",
         "data": {"id": "eq_band_00", "freq": 31.0, "gain": 3.0, "q": 1.41,
                  "type": "Peaking", "enabled": True}},
        False,
    ),
    (
        EqualizerLevels(available=True, output_peak=[-12.5, -13.0]),
        {"category": "equalizer", "type": "levels", "origin": "equalizer",
         "data": {"available": True, "output_peak": [-12.5, -13.0]}},
        False,
    ),
    (
        # EXCLUDE_NONE: the zone_client_removed variant carries mac_id, no zone key
        MultiroomZoneChanged(zone_id="z1", mac_id="aa:bb:cc:dd:ee:ff"),
        {"category": "multiroom", "type": "zone_changed", "origin": "multiroom",
         "data": {"zone_id": "z1", "mac_id": "aa:bb:cc:dd:ee:ff"}},
        False,
    ),
    (
        MultiroomPendingClientChanged(action="removed", mac_id="aa:bb:cc:dd:ee:ff"),
        {"category": "multiroom", "type": "pending_client_changed",
         "origin": "multiroom",
         "data": {"action": "removed", "mac_id": "aa:bb:cc:dd:ee:ff"}},
        False,
    ),
    (
        RoutingMultiroomError(reason="enable_failed"),
        {"category": "routing", "type": "multiroom_error", "origin": "routing",
         "data": {"reason": "enable_failed"}},
        False,
    ),
    (
        NetworkStatusChanged(wifi_enabled=True,
                             ethernet={"connected": False, "ip_address": None},
                             wifi={"connected": True, "ssid": "Net",
                                   "ip_address": "192.168.1.2", "signal": 70,
                                   "saved_ssid": "Net"}),
        {"category": "network", "type": "status_changed", "origin": "network",
         "data": {"wifi_enabled": True,
                  "ethernet": {"connected": False, "ip_address": None},
                  "wifi": {"connected": True, "ssid": "Net",
                           "ip_address": "192.168.1.2", "signal": 70,
                           "saved_ssid": "Net"}}},
        False,
    ),
    (
        SystemConnectivityChanged(online=False),
        {"category": "system", "type": "connectivity_changed", "origin": "system",
         "data": {"source": "system", "online": False}},
        False,  # INCLUDE_FULL_STATE=False despite the system category
    ),
    (
        SystemCdDriveStatus(),
        {"category": "system", "type": "cd_drive_status", "origin": "cd",
         "data": {"source": "cd"}},
        True,
    ),
    (
        RadioFavoriteAdded(station_id="abc123"),
        {"category": "source", "type": "favorite_added", "origin": "radio",
         "data": {"source": "radio", "station_id": "abc123"}},
        True,
    ),
    (
        # No source field on purpose — origin falls back to the category
        ScreenSleepChanged(sleeping=True),
        {"category": "settings", "type": "screen_sleep_changed", "origin": "settings",
         "data": {"sleeping": True}},
        False,
    ),
]


@pytest.mark.parametrize(
    "event,expected,has_full_state", CASES,
    ids=[f"{c[0].CATEGORY}.{c[0].TYPE}" for c in CASES],
)
async def test_broadcast_envelope(
    state_machine, event, expected, has_full_state
):
    await state_machine.broadcast(event)

    envelope = state_machine.ws_manager.broadcast_dict.call_args[0][0]
    assert isinstance(envelope.pop("timestamp"), float)

    if has_full_state:
        assert envelope["data"].pop("full_state") == state_machine.get_current_state()
    else:
        assert "full_state" not in envelope["data"]

    assert envelope == expected


async def test_broadcast_without_ws_manager_is_noop():
    sm = AudioStateMachine()
    await sm.broadcast(SystemTransitionStart())  # must not raise
