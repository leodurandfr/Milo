# Vulture Report — Unused Python Code

**Date:** 2026-03-26
**Tool:** vulture 2.16, min-confidence 60%
**Scope:** `backend/`, `milo-client/app/`

## Summary

| Category | Count | Verdict |
|---|---|---|
| FastAPI route handlers | 137 | **False positive** — registered by decorator |
| Pydantic validators | 24 | **False positive** — called by framework |
| D-Bus agent methods | 10 | **False positive** — called by D-Bus |
| Pydantic model fields | 15 | **False positive** — dataclass-style fields |
| Test-only findings | 51 | Excluded (mock setup, fixtures) |
| **Unused imports** | **1** | **Real** |
| **Unused variables** | **19** | **Verify & fix** |
| **Unused methods/properties** | **41** | **Verify & fix** |
| **Unused constants** | **10** | **Verify & fix** |

---

## Actionable Findings

### 1. Unused Import (90% confidence)

```
backend/core/equalizer/client_proxy.py:19: unused import 'CLIENT_REQUEST_TIMEOUT'
```

### 2. Unused Variables

#### Source code (production)

```
backend/sources/bluetooth/agent.py:127: unused variable 'entered' (100% confidence)
backend/sources/podcast/source.py:514: unused variable 'position_changed' (60%)
backend/sources/podcast/source.py:519: unused variable 'position_changed' (60%)
backend/api/settings.py:855: unused variable 'temp_stderr' (60%)
backend/api/settings.py:859: unused variable 'temp_stderr' (60%)
backend/api/settings.py:861: unused variable 'throttle_stderr' (60%)
backend/api/settings.py:865: unused variable 'throttle_stderr' (60%)
```

#### Constants (may be used outside Python — check shell scripts, systemd units)

```
backend/config/constants.py:19: unused variable 'LAST_VOLUME_FILE' (60%)
backend/config/constants.py:20: unused variable 'RADIO_DATA_FILE' (60%)
backend/config/constants.py:21: unused variable 'PODCAST_DATA_FILE' (60%)
backend/config/constants.py:23: unused variable 'ROUTING_ENV_FILE' (60%)
backend/config/constants.py:29: unused variable 'RADIO_IMAGES_DIR' (60%)
backend/config/constants.py:47: unused variable 'CLIENT_REQUEST_TIMEOUT' (60%)
backend/config/constants.py:53: unused variable 'MAC_RTP_PORT' (60%)
backend/config/constants.py:54: unused variable 'MAC_RS8M_PORT' (60%)
backend/config/constants.py:55: unused variable 'MAC_RTCP_PORT' (60%)
backend/config/constants.py:56: unused variable 'MAC_AUDIO_OUTPUT' (60%)
```

#### Module-level constants

```
backend/core/equalizer/service.py:52: unused variable 'COMMAND_TIMEOUT' (60%)
backend/core/equalizer/sync.py:35: unused variable 'SYNC_CATEGORIES' (60%)
```

### 3. Unused Methods & Properties (need grep verification)

#### Equalizer services

```
backend/core/equalizer/service.py:79: unused attribute '_current_config'
backend/core/equalizer/service.py:859: unused method 'clear_active_preset'
backend/core/equalizer/sync.py:148: unused method 'cleanup_duplicate_clients'
backend/core/equalizer/sync.py:297: unused method 'sync_settings'
backend/core/equalizer/sync.py:429: unused method 'apply_standalone_settings_to_client'
```

#### Multiroom / client registry

```
backend/core/multiroom/client_registry.py:712: unused method 'set_zone_clients'
backend/core/multiroom/client_registry.py:985: unused method 'get_zone_ids'
backend/core/multiroom/client_registry.py:1092: unused method 'get_client_equalizer_settings'
backend/core/multiroom/client_registry.py:1156: unused method 'get_state_dict'
backend/core/multiroom/client_registry.py:1170: unused method 'unsubscribe'
backend/core/multiroom/crossover.py:564: unused method 'on_zone_changed'
backend/core/multiroom/routing.py:694: unused method 'get_available_services'
backend/core/multiroom/snapcast.py:94: unused method 'set_client_name'
backend/core/multiroom/snapcast.py:224: unused method 'get_detailed_clients'
```

#### Volume services

```
backend/core/volume/equalizer_controller.py:54: unused method 'set_router'
backend/core/volume/equalizer_controller.py:217: unused method 'sync_all_from_hardware'
backend/core/volume/equalizer_controller.py:254: unused method 'set_timeout'
backend/core/volume/service.py:411: unused method 'sync_existing_client_from_snapcast'
backend/core/volume/service.py:459: unused method 'sync_client_volume_from_external'
backend/core/volume/service.py:850: unused method 'update_client_availability'
backend/core/volume/state.py:320: unused method 'get_startup_volume'
backend/core/volume/state.py:833: unused method 'get_volume_limits'
```

#### Systemd manager

```
backend/core/systemd.py:28: unused method 'enable'
backend/core/systemd.py:32: unused method 'disable'
backend/core/systemd.py:127: unused method 'set_hostname'
```

#### Hardware

```
backend/hardware/service.py:263: unused method 'reload'
```

#### Audio sources

```
backend/sources/airplay/source.py:338: unused property 'device_connected'
backend/sources/cd/source.py:57: unused attribute '_disc_watcher_task'
backend/sources/cd/source.py:104: unused attribute '_disc_watcher_task'
backend/sources/podcast/source.py:595: unused property 'mpv'
backend/sources/podcast/taddy_api.py:158: unused attribute '_itunes_lookup_cache'
backend/sources/podcast/taddy_api.py:777: unused method 'get_multiple_podcast_series'
backend/sources/podcast/taddy_api.py:823: unused method 'get_multiple_episodes'
backend/sources/podcast/taddy_api.py:955: unused method 'clean_expired_cache'
backend/sources/radio/data.py:288: unused method 'get_favorites'
backend/sources/radio/shazam.py:83: unused method 'clear_track'
backend/sources/radio/source.py:504: unused property 'mpv'
backend/sources/radio/source.py:519: unused property 'current_station'
backend/sources/spotify/source.py:521: unused property 'api_url'
backend/sources/spotify/source.py:526: unused property 'device_connected'
backend/sources/spotify/source.py:531: unused property 'has_active_session'
```

#### Milo-client

```
backend/dependencies.py:32: unused function 'reset_services'
milo-client/app/routes/hardware.py:37: unused method 'validate_overlay' — likely Pydantic FP
milo-client/app/routes/hardware.py:44: unused method 'validate_overlay_required' — likely Pydantic FP
```

---

## Excluded: False Positives

### FastAPI route handlers (137 hits)

All functions decorated with `@router.get/post/put/delete/patch` are registered by FastAPI
and invoked via HTTP. Vulture cannot trace decorator-based registration.

Files: `backend/api/*.py`, `backend/sources/*/routes.py`, `backend/core/multiroom/routes.py`,
`milo-client/app/routes/*.py`

### Pydantic validators (24 hits)

Methods decorated with `@field_validator` / `@model_validator` in `backend/api/models.py`,
`backend/core/wifi/models.py`, `milo-client/app/routes/hardware.py`.

### D-Bus agent methods (10 hits)

`backend/sources/bluetooth/agent.py` — methods like `Release`, `RequestPinCode`,
`DisplayPasskey`, etc. are called by the D-Bus bluetooth agent interface.

### Pydantic model fields (15 hits)

Fields in `backend/sources/*/models.py` — declared as class attributes, used by serialization.

### Test code (51 hits)

Mock attributes (`side_effect`, `__aexit__`), unused fixture returns, test setup variables.
Separate cleanup pass recommended for tests.

---

## Raw Output

<details>
<summary>Full vulture output (333 lines)</summary>

```
backend/api/audio.py:22: unused function 'change_audio_source' (60% confidence)
backend/api/audio.py:32: unused function 'control_source' (60% confidence)
backend/api/equalizer.py:75: unused function 'get_equalizer_effects_enabled' (60% confidence)
backend/api/equalizer.py:108: unused function 'get_equalizer_status' (60% confidence)
backend/api/equalizer.py:130: unused function 'get_zone_levels' (60% confidence)
backend/api/equalizer.py:176: unused function 'get_all_filters' (60% confidence)
backend/api/equalizer.py:222: unused function 'reset_all_filters' (60% confidence)
backend/api/equalizer.py:261: unused function 'save_custom_preset' (60% confidence)
backend/api/equalizer.py:272: unused function 'save_zone_custom_preset' (60% confidence)
backend/api/equalizer.py:287: unused function 'save_client_custom_preset' (60% confidence)
backend/api/equalizer.py:314: unused function 'load_preset_for_zone' (60% confidence)
backend/api/equalizer.py:333: unused function 'update_zone_filter' (60% confidence)
backend/api/equalizer.py:361: unused function 'update_zone_compressor' (60% confidence)
backend/api/equalizer.py:385: unused function 'update_zone_loudness' (60% confidence)
backend/api/equalizer.py:406: unused function 'update_zone_equalizer_enabled' (60% confidence)
backend/api/equalizer.py:432: unused function 'load_preset_for_client' (60% confidence)
backend/api/equalizer.py:585: unused function 'set_zone_crossover' (60% confidence)
backend/api/equalizer.py:602: unused function 'get_local_crossover' (60% confidence)
backend/api/equalizer.py:612: unused function 'set_local_crossover' (60% confidence)
backend/api/equalizer.py:630: unused function 'get_client_equalizer_status' (60% confidence)
backend/api/equalizer.py:646: unused function 'get_client_equalizer_filters' (60% confidence)
backend/api/equalizer.py:651: unused function 'update_client_equalizer_filter' (60% confidence)
backend/api/equalizer.py:669: unused function 'reset_client_equalizer_filters' (60% confidence)
backend/api/equalizer.py:679: unused function 'get_client_compressor' (60% confidence)
backend/api/equalizer.py:684: unused function 'update_client_compressor' (60% confidence)
backend/api/equalizer.py:698: unused function 'get_client_loudness' (60% confidence)
backend/api/equalizer.py:703: unused function 'update_client_loudness' (60% confidence)
backend/api/equalizer.py:717: unused function 'get_client_equalizer_enabled' (60% confidence)
backend/api/equalizer.py:722: unused function 'update_client_equalizer_enabled' (60% confidence)
backend/api/equalizer.py:747: unused function 'update_client_volume' (60% confidence)
backend/api/equalizer.py:770: unused function 'update_client_mute' (60% confidence)
backend/api/equalizer.py:792: unused function 'get_client_saved_settings' (60% confidence)
backend/api/equalizer.py:800: unused function 'restore_client_settings' (60% confidence)
backend/api/errors.py:18: unused function 'report_frontend_error' (60% confidence)
backend/api/health.py:14: unused function 'health_check' (60% confidence)
backend/api/health.py:90: unused function 'ping' (60% confidence)
backend/api/health.py:95: unused function 'get_initial_state' (60% confidence)
backend/api/models.py:18: unused method 'validate_command' (60% confidence)
backend/api/models.py:76: unused method 'validate_range' (60% confidence)
backend/api/models.py:134: unused method 'validate_apps' (60% confidence)
backend/api/models.py:158: unused method 'validate_delay' (60% confidence)
backend/api/models.py:176: unused method 'strip_whitespace' (60% confidence)
backend/api/models.py:191: unused method 'validate_timeout' (60% confidence)
backend/api/models.py:267: unused method 'validate_name' (60% confidence)
backend/api/models.py:275: unused method 'validate_client_ids' (60% confidence)
backend/api/models.py:294: unused method 'validate_name' (60% confidence)
backend/api/models.py:309: unused method 'validate_mac_id' (60% confidence)
backend/api/models.py:340: unused method 'validate_preset_id' (60% confidence)
backend/api/models.py:362: unused method 'validate_timeout' (60% confidence)
backend/api/models.py:385: unused method 'validate_audio_id' (60% confidence)
backend/api/models.py:398: unused method 'validate_screen_type' (60% confidence)
backend/api/models.py:413: unused method 'validate_unique_pins' (60% confidence)
backend/api/models.py:449: unused method 'validate_speaker_type' (60% confidence)
backend/api/models.py:469: unused method 'validate_mac_id' (60% confidence)
backend/api/models.py:485: unused method 'validate_speaker_type' (60% confidence)
backend/api/models.py:507: unused method 'validate_audio_id' (60% confidence)
backend/api/models.py:520: unused method 'validate_speaker_type' (60% confidence)
backend/api/models.py:527: unused method 'validate_audio_id' (60% confidence)
backend/api/multiroom.py:203: unused function 'get_client_hardware' (60% confidence)
backend/api/multiroom.py:222: unused function 'configure_client_audio' (60% confidence)
backend/api/multiroom.py:251: unused function 'get_zones' (60% confidence)
backend/api/multiroom.py:552: unused function 'get_pending_clients' (60% confidence)
backend/api/multiroom.py:558: unused function 'update_pending_client' (60% confidence)
backend/api/multiroom.py:572: unused function 'configure_pending_client' (60% confidence)
backend/api/programs.py:97: unused function 'get_all_programs' (60% confidence)
backend/api/programs.py:159: unused function 'get_satellite_status' (60% confidence)
backend/api/programs.py:232: unused function 'get_satellite_update_status' (60% confidence)
backend/api/programs.py:252: unused function 'get_program_details' (60% confidence)
backend/api/programs.py:268: unused function 'get_program_installed_version' (60% confidence)
backend/api/settings.py:106: unused function 'get_bulk_settings' (60% confidence)
backend/api/settings.py:163: unused function 'get_language' (60% confidence)
backend/api/settings.py:167: unused function 'set_language' (60% confidence)
backend/api/settings.py:178: unused function 'get_volume_limits' (60% confidence)
backend/api/settings.py:189: unused function 'set_volume_limits' (60% confidence)
backend/api/settings.py:207: unused function 'get_volume_startup' (60% confidence)
backend/api/settings.py:218: unused function 'set_volume_startup' (60% confidence)
backend/api/settings.py:236: unused function 'get_volume_steps' (60% confidence)
backend/api/settings.py:244: unused function 'set_volume_steps' (60% confidence)
backend/api/settings.py:256: unused function 'get_rotary_steps' (60% confidence)
backend/api/settings.py:264: unused function 'set_rotary_steps' (60% confidence)
backend/api/settings.py:276: unused function 'get_bt_remote_steps' (60% confidence)
backend/api/settings.py:284: unused function 'set_bt_remote_steps' (60% confidence)
backend/api/settings.py:296: unused function 'get_dock_apps' (60% confidence)
backend/api/settings.py:306: unused function 'set_dock_apps' (60% confidence)
backend/api/settings.py:515: unused function 'get_spotify_disconnect' (60% confidence)
backend/api/settings.py:523: unused function 'set_spotify_disconnect' (60% confidence)
backend/api/settings.py:547: unused function 'get_podcast_credentials' (60% confidence)
backend/api/settings.py:558: unused function 'set_podcast_credentials' (60% confidence)
backend/api/settings.py:593: unused function 'validate_podcast_credentials' (60% confidence)
backend/api/settings.py:643: unused function 'get_podcast_credentials_status' (60% confidence)
backend/api/settings.py:675: unused function 'get_screen_timeout' (60% confidence)
backend/api/settings.py:690: unused function 'set_screen_timeout' (60% confidence)
backend/api/settings.py:702: unused function 'get_screen_brightness' (60% confidence)
backend/api/settings.py:710: unused function 'set_screen_brightness' (60% confidence)
backend/api/settings.py:721: unused function 'apply_brightness_instantly' (60% confidence)
backend/api/settings.py:752: unused function 'get_screen_screensaver' (60% confidence)
backend/api/settings.py:763: unused function 'set_screen_screensaver' (60% confidence)
backend/api/settings.py:788: unused function 'get_screen_ui_scale' (60% confidence)
backend/api/settings.py:796: unused function 'set_screen_ui_scale' (60% confidence)
backend/api/settings.py:806: unused function 'notify_screen_activity' (60% confidence)
backend/api/settings.py:816: unused function 'get_screen_debug' (60% confidence)
backend/api/settings.py:837: unused function 'get_system_temperature' (60% confidence)
backend/api/settings.py:855: unused variable 'temp_stderr' (60% confidence)
backend/api/settings.py:859: unused variable 'temp_stderr' (60% confidence)
backend/api/settings.py:861: unused variable 'throttle_stderr' (60% confidence)
backend/api/settings.py:865: unused variable 'throttle_stderr' (60% confidence)
backend/api/settings.py:933: unused function 'get_network_info' (60% confidence)
backend/api/settings.py:973: unused function 'get_system_resources' (60% confidence)
backend/api/settings.py:1023: unused function 'get_hardware_info' (60% confidence)
backend/api/settings.py:1048: unused function 'get_hardware_config' (60% confidence)
backend/api/settings.py:1077: unused function 'set_hardware_config' (60% confidence)
backend/api/settings.py:1140: unused function 'get_mac_roc_config' (60% confidence)
backend/api/settings.py:1153: unused function 'set_mac_roc_config' (60% confidence)
backend/api/settings.py:1206: unused function 'get_radio_settings' (60% confidence)
backend/api/settings.py:1216: unused function 'set_radio_settings' (60% confidence)
backend/api/settings.py:1242: unused function 'get_inactivity_timeout' (60% confidence)
backend/api/settings.py:1251: unused function 'set_inactivity_timeout' (60% confidence)
backend/api/setup.py:29: unused function 'complete_setup' (60% confidence)
backend/api/system.py:28: unused function 'restart_system' (60% confidence)
backend/api/system.py:35: unused function 'shutdown_system' (60% confidence)
backend/api/volume.py:32: unused function 'adjust_volume' (60% confidence)
backend/api/volume.py:109: unused function 'apply_zone_delta_patch' (60% confidence)
backend/api/volume.py:160: unused function 'get_zone_info' (60% confidence)
backend/api/volume.py:318: unused function 'set_client_volume_by_mac' (60% confidence)
backend/api/volume.py:351: unused function 'set_client_mute_by_mac' (60% confidence)
backend/api/volume.py:384: unused function 'get_volume_settings' (60% confidence)
backend/api/volume.py:399: unused function 'update_volume_settings' (60% confidence)
backend/api/wifi.py:30: unused function 'connect_to_network' (60% confidence)
backend/api/wifi.py:58: unused function 'set_wifi_radio' (60% confidence)
backend/api/wifi.py:66: unused function 'get_hotspot_status' (60% confidence)
backend/api/wifi.py:71: unused function 'get_wifi_country' (60% confidence)
backend/api/wifi.py:78: unused function 'set_wifi_country' (60% confidence)
backend/config/constants.py:19: unused variable 'LAST_VOLUME_FILE' (60% confidence)
backend/config/constants.py:20: unused variable 'RADIO_DATA_FILE' (60% confidence)
backend/config/constants.py:21: unused variable 'PODCAST_DATA_FILE' (60% confidence)
backend/config/constants.py:23: unused variable 'ROUTING_ENV_FILE' (60% confidence)
backend/config/constants.py:29: unused variable 'RADIO_IMAGES_DIR' (60% confidence)
backend/config/constants.py:47: unused variable 'CLIENT_REQUEST_TIMEOUT' (60% confidence)
backend/config/constants.py:53: unused variable 'MAC_RTP_PORT' (60% confidence)
backend/config/constants.py:54: unused variable 'MAC_RS8M_PORT' (60% confidence)
backend/config/constants.py:55: unused variable 'MAC_RTCP_PORT' (60% confidence)
backend/config/constants.py:56: unused variable 'MAC_AUDIO_OUTPUT' (60% confidence)
backend/core/equalizer/client_proxy.py:19: unused import 'CLIENT_REQUEST_TIMEOUT' (90% confidence)
backend/core/equalizer/service.py:52: unused variable 'COMMAND_TIMEOUT' (60% confidence)
backend/core/equalizer/service.py:79: unused attribute '_current_config' (60% confidence)
backend/core/equalizer/service.py:859: unused method 'clear_active_preset' (60% confidence)
backend/core/equalizer/sync.py:35: unused variable 'SYNC_CATEGORIES' (60% confidence)
backend/core/equalizer/sync.py:148: unused method 'cleanup_duplicate_clients' (60% confidence)
backend/core/equalizer/sync.py:297: unused method 'sync_settings' (60% confidence)
backend/core/equalizer/sync.py:429: unused method 'apply_standalone_settings_to_client' (60% confidence)
backend/core/multiroom/client_registry.py:712: unused method 'set_zone_clients' (60% confidence)
backend/core/multiroom/client_registry.py:985: unused method 'get_zone_ids' (60% confidence)
backend/core/multiroom/client_registry.py:1092: unused method 'get_client_equalizer_settings' (60% confidence)
backend/core/multiroom/client_registry.py:1156: unused method 'get_state_dict' (60% confidence)
backend/core/multiroom/client_registry.py:1170: unused method 'unsubscribe' (60% confidence)
backend/core/multiroom/crossover.py:564: unused method 'on_zone_changed' (60% confidence)
backend/core/multiroom/models.py:572: unused variable 'CROSSOVER_CHANGED' (60% confidence)
backend/core/multiroom/routes.py:69: unused function 'get_snapcast_server_config' (60% confidence)
backend/core/multiroom/routing.py:694: unused method 'get_available_services' (60% confidence)
backend/core/multiroom/snapcast.py:94: unused method 'set_client_name' (60% confidence)
backend/core/multiroom/snapcast.py:224: unused method 'get_detailed_clients' (60% confidence)
backend/core/systemd.py:28: unused method 'enable' (60% confidence)
backend/core/systemd.py:32: unused method 'disable' (60% confidence)
backend/core/systemd.py:127: unused method 'set_hostname' (60% confidence)
backend/core/volume/equalizer_controller.py:54: unused method 'set_router' (60% confidence)
backend/core/volume/equalizer_controller.py:217: unused method 'sync_all_from_hardware' (60% confidence)
backend/core/volume/equalizer_controller.py:254: unused method 'set_timeout' (60% confidence)
backend/core/volume/service.py:411: unused method 'sync_existing_client_from_snapcast' (60% confidence)
backend/core/volume/service.py:459: unused method 'sync_client_volume_from_external' (60% confidence)
backend/core/volume/service.py:850: unused method 'update_client_availability' (60% confidence)
backend/core/volume/state.py:320: unused method 'get_startup_volume' (60% confidence)
backend/core/volume/state.py:833: unused method 'get_volume_limits' (60% confidence)
backend/core/wifi/models.py:28: unused variable 'saved_ssid' (60% confidence)
backend/core/wifi/models.py:58: unused method 'must_be_uppercase_alpha' (60% confidence)
backend/dependencies.py:32: unused function 'reset_services' (60% confidence)
backend/hardware/bt_remote_routes.py:29: unused function 'get_battery' (60% confidence)
backend/hardware/screen.py:34: unused attribute 'brightness_min' (60% confidence)
backend/hardware/screen.py:35: unused attribute 'brightness_max' (60% confidence)
backend/hardware/screen.py:37: unused attribute 'brightness_min' (60% confidence)
backend/hardware/screen.py:38: unused attribute 'brightness_max' (60% confidence)
backend/hardware/screen.py:42: unused attribute 'brightness_min' (60% confidence)
backend/hardware/screen.py:43: unused attribute 'brightness_max' (60% confidence)
backend/hardware/service.py:263: unused method 'reload' (60% confidence)
backend/sources/airplay/routes.py:70: unused function 'restart_service' (60% confidence)
backend/sources/airplay/source.py:338: unused property 'device_connected' (60% confidence)
backend/sources/bluetooth/agent.py:104: unused method 'Release' (60% confidence)
backend/sources/bluetooth/agent.py:109: unused method 'RequestPinCode' (60% confidence)
backend/sources/bluetooth/agent.py:115: unused method 'DisplayPinCode' (60% confidence)
backend/sources/bluetooth/agent.py:120: unused method 'RequestPasskey' (60% confidence)
backend/sources/bluetooth/agent.py:126: unused method 'DisplayPasskey' (60% confidence)
backend/sources/bluetooth/agent.py:127: unused variable 'entered' (100% confidence)
backend/sources/bluetooth/agent.py:131: unused method 'RequestConfirmation' (60% confidence)
backend/sources/bluetooth/agent.py:136: unused method 'RequestAuthorization' (60% confidence)
backend/sources/bluetooth/agent.py:141: unused method 'AuthorizeService' (60% confidence)
backend/sources/bluetooth/agent.py:146: unused method 'Cancel' (60% confidence)
backend/sources/bluetooth/routes.py:57: unused function 'disconnect_device' (60% confidence)
backend/sources/cd/models.py:21: unused variable 'number' (60% confidence)
backend/sources/cd/routes.py:62: unused function 'get_drive_status' (60% confidence)
backend/sources/cd/routes.py:80: unused function 'play_track' (60% confidence)
backend/sources/cd/routes.py:102: unused function 'next_track' (60% confidence)
backend/sources/cd/routes.py:108: unused function 'prev_track' (60% confidence)
backend/sources/cd/routes.py:124: unused function 'stop_playback' (60% confidence)
backend/sources/cd/routes.py:130: unused function 'eject' (60% confidence)
backend/sources/cd/routes.py:139: unused function 'get_tracks' (60% confidence)
backend/sources/cd/routes.py:145: unused function 'get_disc_info' (60% confidence)
backend/sources/cd/routes.py:167: unused function 'get_cover' (60% confidence)
backend/sources/cd/source.py:57: unused attribute '_disc_watcher_task' (60% confidence)
backend/sources/cd/source.py:104: unused attribute '_disc_watcher_task' (60% confidence)
backend/sources/podcast/models.py:45: unused variable 'publisher' (60% confidence)
backend/sources/podcast/models.py:46: unused variable 'total_episodes' (60% confidence)
backend/sources/podcast/models.py:60: unused variable 'date_published' (60% confidence)
backend/sources/podcast/models.py:62: unused variable 'playback_progress' (60% confidence)
backend/sources/podcast/models.py:71: unused variable 'added_at' (60% confidence)
backend/sources/podcast/models.py:72: unused variable 'last_checked' (60% confidence)
backend/sources/podcast/models.py:79: unused variable 'last_played' (60% confidence)
backend/sources/podcast/routes.py:74: unused function 'get_top_charts' (60% confidence)
backend/sources/podcast/routes.py:120: unused function 'get_content_by_genre' (60% confidence)
backend/sources/podcast/routes.py:160: unused function 'lookup_podcast_by_itunes_id' (60% confidence)
backend/sources/podcast/routes.py:321: unused function 'play_episode' (60% confidence)
backend/sources/podcast/routes.py:340: unused function 'pause_playback' (60% confidence)
backend/sources/podcast/routes.py:348: unused function 'resume_playback' (60% confidence)
backend/sources/podcast/routes.py:356: unused function 'seek_playback' (60% confidence)
backend/sources/podcast/routes.py:365: unused function 'stop_playback' (60% confidence)
backend/sources/podcast/routes.py:373: unused function 'set_speed' (60% confidence)
backend/sources/podcast/routes.py:430: unused function 'get_latest_episodes_from_subscriptions' (60% confidence)
backend/sources/podcast/routes.py:465: unused function 'get_queue' (60% confidence)
backend/sources/podcast/routes.py:478: unused function 'mark_episode_complete' (60% confidence)
backend/sources/podcast/routes.py:494: unused function 'get_settings' (60% confidence)
backend/sources/podcast/routes.py:507: unused function 'update_settings' (60% confidence)
backend/sources/podcast/source.py:514: unused variable 'position_changed' (60% confidence)
backend/sources/podcast/source.py:519: unused variable 'position_changed' (60% confidence)
backend/sources/podcast/taddy_api.py:158: unused attribute '_itunes_lookup_cache' (60% confidence)
backend/sources/podcast/taddy_api.py:777: unused method 'get_multiple_podcast_series' (60% confidence)
backend/sources/podcast/taddy_api.py:823: unused method 'get_multiple_episodes' (60% confidence)
backend/sources/podcast/taddy_api.py:955: unused method 'clean_expired_cache' (60% confidence)
backend/sources/radio/data.py:288: unused method 'get_favorites' (60% confidence)
backend/sources/radio/models.py:23: unused variable 'votes' (60% confidence)
backend/sources/radio/models.py:24: unused variable 'clickcount' (60% confidence)
backend/sources/radio/models.py:25: unused variable 'score' (60% confidence)
backend/sources/radio/models.py:27: unused variable 'is_custom' (60% confidence)
backend/sources/radio/models.py:30: unused class 'Config' (60% confidence)
backend/sources/radio/models.py:31: unused variable 'extra' (60% confidence)
backend/sources/radio/routes.py:81: unused function 'play_station' (60% confidence)
backend/sources/radio/routes.py:102: unused function 'stop_playback' (60% confidence)
backend/sources/radio/routes.py:190: unused function 'get_countries' (60% confidence)
backend/sources/radio/routes.py:207: unused function 'get_favorites' (60% confidence)
backend/sources/radio/routes.py:346: unused function 'get_custom_stations' (60% confidence)
backend/sources/radio/routes.py:536: unused function 'update_station_image' (60% confidence)
backend/sources/radio/routes.py:589: unused function 'remove_station_image' (60% confidence)
backend/sources/radio/routes.py:628: unused function 'create_custom_from_favorite' (60% confidence)
backend/sources/radio/routes.py:690: unused function 'get_station_image' (60% confidence)
backend/sources/radio/routes.py:736: unused function 'get_favicon_proxy' (60% confidence)
backend/sources/radio/shazam.py:83: unused method 'clear_track' (60% confidence)
backend/sources/radio/source.py:504: unused property 'mpv' (60% confidence)
backend/sources/radio/source.py:519: unused property 'current_station' (60% confidence)
backend/sources/spotify/source.py:521: unused property 'api_url' (60% confidence)
backend/sources/spotify/source.py:526: unused property 'device_connected' (60% confidence)
backend/sources/spotify/source.py:531: unused property 'has_active_session' (60% confidence)
backend/tests/conftest.py:55: unused function 'mock_async_lock' (60% confidence)
backend/tests/conftest.py:60: unused attribute '__aexit__' (60% confidence)
backend/tests/integration/test_audio_transitions.py:293: unused variable 'original_spotify_start' (60% confidence)
backend/tests/integration/test_audio_transitions.py:497: unused variable 'buffered_events' (60% confidence)
backend/tests/integration/test_crossover_scenarios.py:885: unused variable 'crossover_enabled_before' (60% confidence)
backend/tests/integration/test_eq_filter_management.py:87: unused variable 'mock_set' (60% confidence)
backend/tests/integration/test_equalizer_zone_endpoints.py:88: unused attribute 'set_enabled' (60% confidence)
backend/tests/integration/test_reconnection_scenarios.py:722: unused attribute '_sync_client_volume_and_broadcast' (60% confidence)
backend/tests/integration/test_reconnection_scenarios.py:1123: unused attribute 'set_client_group_to_multiroom' (60% confidence)
backend/tests/integration/test_websocket_events.py:56: unused method 'send_json' (60% confidence)
backend/tests/test_bluetooth_source.py:58: unused attribute 'connected_devices' (60% confidence)
backend/tests/test_core_equalizer.py:139: unused attribute '__aexit__' (60% confidence)
backend/tests/test_core_equalizer.py:160: unused attribute '__aexit__' (60% confidence)
backend/tests/test_core_equalizer.py:171: unused attribute 'side_effect' (60% confidence)
backend/tests/test_core_multiroom.py:1974: unused attribute 'side_effect' (60% confidence)
backend/tests/test_core_multiroom.py:1975: unused attribute 'side_effect' (60% confidence)
backend/tests/test_core_multiroom.py:2019: unused attribute 'side_effect' (60% confidence)
backend/tests/test_core_multiroom.py:2020: unused attribute 'side_effect' (60% confidence)
backend/tests/test_core_multiroom.py:2069: unused attribute 'side_effect' (60% confidence)
backend/tests/test_crossover_service.py:503: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:507: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:512: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:526: unused attribute 'side_effect' (60% confidence)
backend/tests/test_crossover_service.py:543: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:547: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:552: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:566: unused attribute 'side_effect' (60% confidence)
backend/tests/test_crossover_service.py:1270: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:1274: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:1279: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:1329: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:1333: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:1338: unused attribute '__aexit__' (60% confidence)
backend/tests/test_crossover_service.py:1630: unused attribute 'side_effect' (60% confidence)
backend/tests/test_crossover_service.py:1652: unused attribute 'side_effect' (60% confidence)
backend/tests/test_multiroom_equalizer_service.py:501: unused attribute 'side_effect' (60% confidence)
backend/tests/test_routes_settings.py:36: unused attribute 'update_equalizer_state' (60% confidence)
backend/tests/test_routes_settings.py:473: unused attribute '_mock_state_machine' (60% confidence)
backend/tests/test_routes_snapcast.py:31: unused attribute 'get_detailed_clients' (60% confidence)
backend/tests/test_routes_snapcast.py:37: unused attribute 'set_client_name' (60% confidence)
backend/tests/test_routes_snapcast.py:70: unused attribute '_mock_routing' (60% confidence)
backend/tests/test_routes_snapcast.py:72: unused attribute '_mock_state_machine' (60% confidence)
backend/tests/test_routes_snapcast.py:73: unused attribute '_mock_camilladsp_service' (60% confidence)
backend/tests/test_spotify_source.py:297: unused attribute 'side_effect' (60% confidence)
backend/tests/test_update_service.py:627: unused variable 'component' (100% confidence)
backend/tests/test_volume_api.py:309: unused attribute 'side_effect' (60% confidence)
backend/tests/test_volume_api.py:321: unused attribute 'side_effect' (60% confidence)
backend/tests/test_volume_api.py:330: unused attribute 'side_effect' (60% confidence)
backend/tests/test_volume_api.py:576: unused attribute 'side_effect' (60% confidence)
milo-client/app/routes/app_update.py:19: unused function 'update_app' (60% confidence)
milo-client/app/routes/app_update.py:48: unused function 'get_update_status' (60% confidence)
milo-client/app/routes/app_update.py:56: unused function 'get_version' (60% confidence)
milo-client/app/routes/equalizer.py:23: unused function 'get_equalizer_status' (60% confidence)
milo-client/app/routes/equalizer.py:53: unused function 'get_equalizer_filters' (60% confidence)
milo-client/app/routes/equalizer.py:70: unused function 'update_equalizer_filter' (60% confidence)
milo-client/app/routes/equalizer.py:90: unused function 'update_equalizer_filters_batch' (60% confidence)
milo-client/app/routes/equalizer.py:105: unused function 'reset_equalizer_filters' (60% confidence)
milo-client/app/routes/equalizer.py:139: unused function 'update_mute' (60% confidence)
milo-client/app/routes/equalizer.py:211: unused function 'get_delay' (60% confidence)
milo-client/app/routes/equalizer.py:216: unused function 'update_delay' (60% confidence)
milo-client/app/routes/equalizer.py:233: unused function 'get_crossover' (60% confidence)
milo-client/app/routes/equalizer.py:238: unused function 'update_crossover' (60% confidence)
milo-client/app/routes/equalizer.py:257: unused function 'get_lowpass' (60% confidence)
milo-client/app/routes/equalizer.py:262: unused function 'update_lowpass' (60% confidence)
milo-client/app/routes/hardware.py:37: unused method 'validate_overlay' (60% confidence)
milo-client/app/routes/hardware.py:44: unused method 'validate_overlay_required' (60% confidence)
milo-client/app/routes/hardware.py:74: unused function 'get_hardware' (60% confidence)
milo-client/app/routes/hardware.py:79: unused function 'set_audio' (60% confidence)
milo-client/app/routes/hardware.py:109: unused function 'reboot' (60% confidence)
milo-client/app/routes/health.py:40: unused function 'health_check' (60% confidence)
milo-client/app/routes/snapclient.py:22: unused function 'get_version' (60% confidence)
milo-client/app/routes/snapclient.py:86: unused function 'get_update_status' (60% confidence)
milo-client/app/tests/conftest.py:68: unused function 'mock_subprocess' (60% confidence)
milo-client/app/tests/test_services_snapclient.py:53: unused attribute 'side_effect' (60% confidence)
```

</details>
