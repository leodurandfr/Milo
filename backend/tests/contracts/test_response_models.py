# backend/tests/contracts/test_response_models.py
"""Wire-fidelity guard for the Phase 1 response_model rollout.

Each Milo-Mac contract route now declares a `response_model`. FastAPI validates
the handler's dict through that model and re-serializes it, which can silently
DROP keys (not in the model) or ADD keys (`null` defaults). This test pins the
exact serialized key set for every model against the dict its route actually
returns — success AND the resilience error branch — so a future model edit that
would change the wire fails loudly here, next to the offline Milo-Mac contract
test.

`emit()` mirrors FastAPI's response_model serialization: validate the returned
dict through the model, then dump in JSON mode with the same `exclude_none`
flag the route decorator uses.
"""
from backend.api import responses as R
from backend.core.state import AudioStateMachine


def emit(model, data, *, exclude_none=False):
    """Reproduce FastAPI's response_model serialization for `data`."""
    return model.model_validate(data).model_dump(mode="json", exclude_none=exclude_none)


def test_audio_state_preserves_every_key_the_state_machine_emits():
    """AudioStateResponse must carry the whole of get_current_state().

    Derived from the state machine rather than from a dict written here, which
    is the difference between a guardrail and a copy of the thing it guards:
    this test used to hand-type the seven keys it expected, so when
    `network_unavailable` was added to get_current_state() the model, the test
    and each other stayed in perfect agreement while the route quietly stopped
    serving it. The frontend feeds this response through the same
    `updateSystemState` as a WS full_state, so a filtered key is read as absent
    — an offline status card reverting to a normal one on every resync.

    A bare machine is enough: with no services wired, get_current_state() still
    emits every key, which is exactly the set the model has to declare.
    """
    produced = AudioStateMachine().get_current_state()
    # The producer has to have produced something, or the comparison is vacuous.
    assert "active_source" in produced
    assert "network_unavailable" in produced

    data = {**produced, "metadata": {"title": "x", "artist": None}}  # opaque; sub-null kept
    out = emit(R.AudioStateResponse, data)
    assert set(out) == set(produced)
    # metadata stays opaque: response_model must not strip its null sub-keys.
    assert out["metadata"] == {"title": "x", "artist": None}
    assert out["error"] is None  # no exclude_none on this route


def test_audio_source_status_only():
    assert set(emit(R.StatusResponse, {"status": "success"})) == {"status"}


def test_multiroom_set_keys():
    data = {"status": "success", "multiroom_enabled": True, "active_source": "none"}
    assert set(emit(R.MultiroomSetResponse, data)) == set(data)


def test_volume_state_success_omits_message():
    inner = {
        "mode": "multiroom",
        "global_volume_db": -45.0,
        "global_mute": False,
        "volume_control": True,
        "any_volume_control": True,
        "clients": {
            "dc:a6:32:7e:d3:43": {
                "volume_db": -40.0, "offset_db": 0.0, "mute": False, "available": True,
            }
        },
        "zones": {
            "z1": {
                "id": "z1", "name": "Kitchen", "client_ids": ["dc:a6:32:7e:d3:43"],
                "average_volume_db": -40.0, "all_muted": False,
            }
        },
    }
    out = emit(R.VolumeStateEnvelope, {"status": "success", "data": inner}, exclude_none=True)
    assert set(out) == {"status", "data"}  # message dropped
    # Milo-Mac hard-requires data.global_volume_db; inner keys must survive.
    assert set(out["data"]) == set(inner)
    assert out["data"]["global_volume_db"] == -45.0


def test_volume_state_error_omits_data():
    out = emit(
        R.VolumeStateEnvelope,
        {"status": "error", "message": "boom"},
        exclude_none=True,
    )
    assert set(out) == {"status", "message"}  # data dropped


def test_volume_adjust_keys():
    data = {"status": "success", "volume_db": -45.0, "delta_db": 2.0}
    assert set(emit(R.VolumeAdjustResponse, data)) == set(data)


def test_equalizer_enabled_keys():
    data = {"status": "success", "target": "local", "enabled": True}
    assert set(emit(R.EqualizerEnabledResponse, data)) == set(data)


def test_radio_stations_success_omits_api_error():
    data = {"stations": [{"id": "1", "name": "FIP", "favorite": True}], "total": 1}
    out = emit(R.RadioStationsResponse, data, exclude_none=True)
    assert set(out) == {"stations", "total"}  # api_error absent on success
    assert out["stations"][0] == {"id": "1", "name": "FIP", "favorite": True}


def test_radio_stations_degraded_keeps_api_error():
    data = {"stations": [], "total": 0, "api_error": True}
    out = emit(R.RadioStationsResponse, data, exclude_none=True)
    assert out["api_error"] is True


def test_network_status_envelope_preserves_nested_nulls():
    # backend/api/network.py GET /status -> NetworkStatus.model_dump().
    # Disconnected wifi carries explicit nulls the frontend reads — no exclude_none.
    data = {
        "status": "success",
        "data": {
            "wifi_enabled": True,
            "ethernet": {"connected": True, "ip_address": "192.168.1.2"},
            "wifi": {
                "connected": False, "ssid": None, "ip_address": None,
                "signal": None, "saved_ssid": "HomeNet",
            },
        },
    }
    out = emit(R.NetworkStatusEnvelope, data)
    assert out == data  # nulls preserved, reused domain model = identical dump


def test_wifi_networks_and_saved_envelopes():
    scan = {"status": "success", "data": [
        {"ssid": "A", "signal": 80, "security": "WPA2", "in_use": True},
    ]}
    assert emit(R.WifiNetworksEnvelope, scan) == scan
    saved = {"status": "success", "data": [{"ssid": "A"}, {"ssid": "B"}]}
    assert emit(R.WifiSavedEnvelope, saved) == saved


def test_wifi_small_envelopes():
    assert set(emit(R.WifiSaveEnvelope, {"status": "success", "data": {"ssid": "X"}})) == {"status", "data"}
    cc = {"status": "success", "data": {"country_code": "FR"}}
    assert emit(R.WifiCountryEnvelope, cc) == cc
    assert set(emit(R.StatusResponse, {"status": "success"})) == {"status"}


def test_volume_zone_delta_keys():
    data = {
        "status": "success", "zone_id": "z1", "new_average_db": -40.0, "delta_db": 2.0,
        "applied_to": ["dc:a6:32:7e:d3:43"], "offline_clients": [],
    }
    assert set(emit(R.ZoneVolumeDeltaResponse, data)) == set(data)


def test_volume_client_and_control_keys():
    assert set(emit(R.ClientVolumeSetResponse, {"status": "success", "mac_id": "m", "volume_db": -40.0})) == {"status", "mac_id", "volume_db"}
    assert set(emit(R.ClientMuteSetResponse, {"status": "success", "mac_id": "m", "mute": True})) == {"status", "mac_id", "mute"}
    assert set(emit(R.VolumeControlResponse, {"status": "success", "volume_control": False})) == {"status", "volume_control"}


def test_multiroom_state_and_pending_preserve_opaque_maps():
    # backend/api/multiroom.py GET /state — client/zone values stay opaque.
    data = {
        "clients": {"dc:a6:32:7e:d3:43": {"name": "Kitchen", "online": True, "zone_id": None}},
        "zones": {"z1": {"id": "z1", "online_client_count": 1, "crossover_frequency": None}},
    }
    out = emit(R.MultiroomStateResponse, data)
    assert out == data  # computed/null sub-keys preserved
    pend = {"clients": {"aa:bb": {"ip": "10.0.0.9", "audio_id": "hifiberry"}}}
    assert emit(R.MultiroomPendingClientsResponse, pend) == pend


def test_multiroom_client_and_zone_mutations():
    c = {"status": "success", "client": {"mac_id": "m", "online": False, "name": "X"}}
    assert emit(R.ClientMutationResponse, c) == c
    z = {"status": "success", "zone": {"id": "z1", "client_ids": ["a", "b"]}}
    assert emit(R.ZoneMutationResponse, z) == z
    m = {"status": "success", "message": "Zone 'z1' deleted"}
    assert set(emit(R.MultiroomMessageResponse, m)) == {"status", "message"}


def test_multiroom_zone_or_message_branches():
    # Zone survived → only {status, zone}; deleted → only {status, message}.
    zone_branch = {"status": "success", "zone": {"id": "z1"}}
    out = emit(R.ZoneOrMessageResponse, zone_branch, exclude_none=True)
    assert set(out) == {"status", "zone"}
    msg_branch = {"status": "success", "message": "Client removed, zone deleted"}
    out = emit(R.ZoneOrMessageResponse, msg_branch, exclude_none=True)
    assert set(out) == {"status", "message"}


def test_register_client_branches():
    # Reconnect path → {status, message}; staged path → {status, client}.
    out = emit(R.RegisterClientResponse, {"status": "success", "message": "reconnect"}, exclude_none=True)
    assert set(out) == {"status", "message"}
    out = emit(R.RegisterClientResponse, {"status": "success", "client": {"mac_id": "m", "ip": "10.0.0.9"}}, exclude_none=True)
    assert set(out) == {"status", "client"}


def test_equalizer_record_preserves_all_ten_keys_and_opaque_subobjects():
    # backend/api/equalizer.py GET /target/{target} — all 10 keys always present.
    data = {
        "enabled": True,
        "active_preset": None,
        "mono": False,
        "compressor": {"enabled": False, "threshold": -20.0, "ratio": 2.0,
                       "attack": 0.01, "release": 0.1, "makeup_gain": 0.0},
        "loudness": {"enabled": True, "low_boost": 5.0, "high_boost": 3.0},
        "custom_gains": [0.0, -2.5, 1.0],
        "filters": [{"id": "f1", "freq": 100.0, "gain": -3.0, "type": "peaking", "enabled": True}],
        "state": "running",
        "sample_rate": None,
        "available": True,
    }
    out = emit(R.EqualizerRecordResponse, data)
    assert out == data  # opaque sub-objects + null active_preset/sample_rate preserved


def test_equalizer_presets_success_omits_error():
    data = {"presets": [{"id": "flat", "gains": [0.0] * 10}], "custom_gains": [0.0] * 10, "active_preset": "flat"}
    out = emit(R.EqualizerPresetsResponse, data, exclude_none=True)
    assert set(out) == {"presets", "custom_gains", "active_preset"}  # no error key
    err = {"presets": [], "custom_gains": [0.0] * 10, "active_preset": None, "error": "boom"}
    out = emit(R.EqualizerPresetsResponse, err, exclude_none=True)
    assert set(out) == {"presets", "custom_gains", "error"}  # active_preset null dropped, error kept


def test_equalizer_target_mutation_responses():
    assert set(emit(R.TargetStatusResponse, {"status": "success", "target": "local"})) == {"status", "target"}
    f = {"status": "success", "target": "local", "filter_id": "f1"}
    assert set(emit(R.TargetFilterResponse, f)) == set(f)
    m = {"status": "success", "target": "local", "mono": True}
    assert set(emit(R.TargetMonoResponse, m)) == set(m)
    p = {"status": "success", "target": "local", "preset_id": "rock", "gains": [0.0, 1.5]}
    assert set(emit(R.TargetPresetResponse, p)) == set(p)
    sc = {"status": "success", "target": "local", "preset_id": "custom"}
    assert set(emit(R.TargetSaveCustomResponse, sc)) == set(sc)


def test_bulk_settings_full_key_set():
    # Mirrors backend/api/settings.py GET /bulk with realistic stored types.
    data = {
        "status": "success",
        "language": "french",
        "volume_limits": {"min_db": -76.0, "max_db": -12.0},
        "volume_startup": {"startup_volume_db": -53.99, "restore_last_volume": True},
        "rotary_steps": {"step_rotary_db": 1.0},
        "bt_remote_steps": {"step_bt_remote_db": 1.0},
        "ir_remote_steps": {"step_ir_remote_db": 3.0},
        "dock_apps": {"enabled_apps": ["spotify", "radio"]},
        "audio_stop": {"auto_stop_delay": 120.0},
        "screen_timeout": {"screen_timeout_enabled": True, "screen_timeout_seconds": 30},
        "screen_brightness": {"brightness_on": 6},
        "screen_ui_scale": {"ui_scale": 1.15},
        "screen_screensaver": {"screensaver_enabled": True, "screensaver_delay_seconds": 120},
        "screen_color_filter": {"enabled": True, "warmth": 72},
        "radio_settings": {"shazam_enabled": True},
        "music_library_settings": {"separate_storages": True},
        "qobuz_settings": {"allow_app_volume": False},
        "spotify_settings": {"crossfade_duration": 6000},
        "mac_roc": {"target_latency_ms": 130, "latency_profile": "responsive", "frame_length_ms": 10},
    }
    out = emit(R.BulkSettingsResponse, data)
    assert set(out) == set(data)
    for key, group in data.items():
        if isinstance(group, dict):
            assert set(out[key]) == set(group), key
    # int fields stay int (no float coercion), db fields stay float.
    assert out["screen_brightness"]["brightness_on"] == 6
    assert isinstance(out["mac_roc"]["target_latency_ms"], int)
    assert isinstance(out["volume_limits"]["min_db"], float)
