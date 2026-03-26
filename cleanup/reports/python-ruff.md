# Ruff Report — Python Lint Issues

**Date:** 2026-03-26
**Tool:** ruff 0.15.7
**Rules:** F401, F841, F811, F821, E501, E711, E712, E721, E741, W291-W293
**Scope:** `backend/`, `milo-client/app/`

## Summary

| Rule | Description | Source | Tests | Total |
|---|---|---|---|---|
| **F401** | Unused imports | 21 | 98 | 119 |
| **F841** | Unused local variables | 6 | 22 | 28 |
| **F821** | Undefined names | 14 | 0 | 14 |
| **F811** | Redefined unused name | 0 | 1 | 1 |
| **E741** | Ambiguous variable name | 1 | 0 | 1 |
| **W293** | Blank line with whitespace | 98 | 0 | 98 |
| **W292** | No newline at end of file | 7 | 0 | 7 |
| **W291** | Trailing whitespace | 2 | 0 | 2 |
| **E501** | Line too long (>88) | ~2427 | — | ~2427 |

**Auto-fixable:** 226 issues (mostly whitespace + unused imports)

---

## Actionable Findings — Source Code

### F401: Unused Imports (21 in source, auto-fixable)

```
backend/core/equalizer/client_proxy.py:11: `asyncio` imported but unused
backend/core/equalizer/client_proxy.py:22: `backend.config.constants.CLIENT_REQUEST_TIMEOUT` imported but unused
backend/core/equalizer/multiroom_service.py:24: `typing.Any` imported but unused
backend/core/equalizer/multiroom_service.py:24: `typing.Dict` imported but unused
backend/core/equalizer/multiroom_service.py:29: `backend.core.multiroom.models.CompressorSettings` imported but unused
backend/core/equalizer/multiroom_service.py:30: `backend.core.multiroom.models.LoudnessSettings` imported but unused
backend/core/multiroom/crossover.py:17: `typing.List` imported but unused
backend/core/multiroom/equalizer_router.py:14: `typing.Optional` imported but unused
backend/core/multiroom/routes.py:7: `fastapi.HTTPException` imported but unused
backend/core/multiroom/routing.py:11: `backend.core.systemd.SystemdServiceManager` imported but unused
backend/core/multiroom/snapcast.py:11: `typing.Optional` imported but unused
backend/core/updates/version.py:11: `typing.Optional` imported but unused
backend/core/updates/version.py:12: `pathlib.Path` imported but unused
backend/core/volume/service.py:15: `typing.Any` imported but unused
backend/core/volume/state.py:23: `time` imported but unused
backend/hardware/screen.py:8: `os` imported but unused
backend/hardware/service.py:14: `pathlib.Path` imported but unused
backend/hardware/service.py:18: `backend.hardware.registry.SCREENS` imported but unused
backend/shared/mpv_audio_source.py:15: `typing.Dict` imported but unused
backend/shared/mpv_audio_source.py:15: `typing.Any` imported but unused
backend/sources/podcast/taddy_api.py:5: `asyncio` imported but unused
```

### F841: Unused Local Variables (6 in source)

```
backend/api/settings.py:859: Local variable `temp_stderr` is assigned to but never used
backend/api/settings.py:865: Local variable `throttle_stderr` is assigned to but never used
backend/core/multiroom/websocket.py:602: Local variable `client_id` is assigned to but never used
backend/core/multiroom/websocket.py:603: Local variable `client_name` is assigned to but never used
backend/sources/podcast/source.py:519: Local variable `position_changed` is assigned to but never used
backend/sources/radio/data.py:705: Local variable `success` is assigned to but never used
```

### E741: Ambiguous Variable Name (1 hit)

```
backend/sources/podcast/routes.py:216: Ambiguous variable name: `l`
```

### F821: Undefined Names (14 hits — ALL FALSE POSITIVES)

All in `backend/sources/bluetooth/agent.py`. These are **D-Bus type signature annotations**
(`'o'` = object path, `'s'` = string, `'u'` = uint32, `'q'` = uint16), not Python names.
Ruff parses them as forward references but they are D-Bus wire-format specifiers.

```
agent.py:110  'o', 's'    — RequestPinCode(device: 'o') -> 's'
agent.py:116  'o', 's'    — DisplayPinCode(device: 'o', pincode: 's')
agent.py:121  'o', 'u'    — RequestPasskey(device: 'o') -> 'u'
agent.py:127  'o', 'u', 'q' — DisplayPasskey(device: 'o', passkey: 'u', entered: 'q')
agent.py:132  'o', 'u'    — RequestConfirmation(device: 'o', passkey: 'u')
agent.py:137  'o'         — RequestAuthorization(device: 'o')
agent.py:142  'o', 's'    — AuthorizeService(device: 'o', uuid: 's')
```

---

## Whitespace Issues (107 total, all auto-fixable)

| File | W293 | W292 | W291 |
|---|---|---|---|
| `backend/api/settings.py` | 43 | 1 | 0 |
| `backend/core/multiroom/routing.py` | 21 | 1 | 1 |
| `backend/core/systemd.py` | 18 | 1 | 1 |
| `backend/core/settings.py` | 10 | 1 | 0 |
| `backend/core/models/audio_state.py` | 3 | 0 | 0 |
| `backend/hardware/screen.py` | 10 | 1 | 0 |
| `backend/api/audio.py` | 0 | 1 | 0 |
| `backend/api/programs.py` | 0 | 1 | 0 |

---

## Test Code Issues (121 total)

### F401: Unused Imports in Tests (98 hits)

Common patterns — likely leftover from copy-paste or refactors:
- `asyncio` imported but unused (7 files)
- `unittest.mock.MagicMock` imported but unused (11 files)
- `unittest.mock.patch` imported but unused (8 files)
- `unittest.mock.Mock` imported but unused (5 files)
- Various model/type imports no longer used in tests

### F841: Unused Variables in Tests (22 hits)

Assigned-but-unused results, variables for assertions that were removed, etc.

<details>
<summary>Full test findings</summary>

```
backend/tests/conftest.py:7: F401 `backend.core.models.audio_state.AudioSource` imported but unused
backend/tests/integration/test_audio_transitions.py:17: F401 `unittest.mock.AsyncMock` imported but unused
backend/tests/integration/test_audio_transitions.py:293: F841 `original_spotify_start` assigned but unused
backend/tests/integration/test_audio_transitions.py:432: F841 `results` assigned but unused
backend/tests/integration/test_audio_transitions.py:497: F841 `buffered_events` assigned but unused
backend/tests/integration/test_compressor_loudness_control.py:18: F401 `asyncio` imported but unused
backend/tests/integration/test_compressor_loudness_control.py:442: F401 `create_equalizer_router` imported but unused
backend/tests/integration/test_compressor_loudness_control.py:574: F401 `pydantic.ValidationError` imported but unused
backend/tests/integration/test_crossover_scenarios.py:14: F401 `asyncio` imported but unused
backend/tests/integration/test_crossover_scenarios.py:16: F401 `unittest.mock.MagicMock` imported but unused
backend/tests/integration/test_crossover_scenarios.py:16: F401 `unittest.mock.patch` imported but unused
backend/tests/integration/test_crossover_scenarios.py:19: F401 `backend.core.multiroom.models.Client` imported but unused
backend/tests/integration/test_crossover_scenarios.py:20: F401 `backend.core.multiroom.models.Zone` imported but unused
backend/tests/integration/test_crossover_scenarios.py:21: F401 `backend.core.multiroom.models.EqualizerSettings` imported but unused
backend/tests/integration/test_crossover_scenarios.py:23: F401 `backend.core.multiroom.models.DEFAULT_SPEAKER_TYPE` imported but unused
backend/tests/integration/test_crossover_scenarios.py:24: F401 `backend.core.multiroom.models.DEFAULT_CROSSOVER_FREQUENCIES` imported but unused
backend/tests/integration/test_crossover_scenarios.py:26: F401 `backend.config.constants.DEFAULT_VOLUME_DB` imported but unused
backend/tests/integration/test_crossover_scenarios.py:498: F841 `zone` assigned but unused
backend/tests/integration/test_crossover_scenarios.py:850: F841 `zone` assigned but unused
backend/tests/integration/test_crossover_scenarios.py:885: F841 `crossover_enabled_before` assigned but unused
backend/tests/integration/test_eq_filter_management.py:15: F401 `unittest.mock.MagicMock` imported but unused
backend/tests/integration/test_eq_filter_management.py:16: F401 `asyncio` imported but unused
backend/tests/integration/test_eq_filter_management.py:21: F401 `backend.core.equalizer.FilterType` imported but unused
backend/tests/integration/test_eq_filter_management.py:22: F401 `backend.core.equalizer.get_builtin_presets` imported but unused
backend/tests/integration/test_eq_filter_management.py:87: F841 `mock_set` assigned but unused
backend/tests/integration/test_eq_filter_management.py:305: F811 Redefinition of unused `FilterType`
backend/tests/integration/test_equalizer_zone_endpoints.py:19: F401 `unittest.mock.patch` imported but unused
backend/tests/integration/test_equalizer_zone_endpoints.py:19: F401 `unittest.mock.MagicMock` imported but unused
backend/tests/integration/test_equalizer_zone_endpoints.py:445: F841 `result` assigned but unused
backend/tests/integration/test_equalizer_zone_endpoints.py:477: F841 `result` assigned but unused
backend/tests/integration/test_global_equalizer_bypass.py:17: F401 `unittest.mock.MagicMock` imported but unused
backend/tests/integration/test_global_equalizer_bypass.py:18: F401 `asyncio` imported but unused
backend/tests/integration/test_global_equalizer_bypass.py:338: F401 `fastapi.testclient.TestClient` imported but unused
backend/tests/integration/test_multiroom_sync.py:13: F401 `unittest.mock.call` imported but unused
backend/tests/integration/test_multiroom_zones.py:15: F401 models (Client, Zone, RegistryEventType) imported but unused
backend/tests/integration/test_multiroom_zones.py:16: F401 `VolumeState` imported but unused
backend/tests/integration/test_reconnection_scenarios.py:13: F401 `unittest.mock.Mock` imported but unused
backend/tests/integration/test_reconnection_scenarios.py:13: F401 `unittest.mock.patch` imported but unused
backend/tests/integration/test_reconnection_scenarios.py:14: F401 `dataclasses.dataclass` imported but unused
backend/tests/integration/test_reconnection_scenarios.py:15: F401 typing (Dict, List, Optional, Any) imported but unused
backend/tests/integration/test_reconnection_scenarios.py:17: F401 `VolumeState` imported but unused
backend/tests/integration/test_reconnection_scenarios.py:544: F401 `SnapcastWebSocketService` imported but unused
backend/tests/integration/test_reconnection_scenarios.py:1056: F841 `sync_status` assigned but unused
backend/tests/integration/test_settings_persistence.py:18: F401 `os` imported but unused
backend/tests/integration/test_settings_persistence.py:22: F401 typing (Dict, Any, List) imported but unused
backend/tests/integration/test_settings_persistence.py:26: F401 `WebSocketEventCollector` imported but unused
backend/tests/integration/test_snapcast_detection.py:14: F401 `asyncio` imported but unused
backend/tests/integration/test_snapcast_detection.py:16: F401 `unittest.mock.patch` imported but unused
backend/tests/integration/test_snapcast_detection.py:21: F401 `RegistryEventType` imported but unused
backend/tests/integration/test_volume_control.py:18: F401 `tempfile` imported but unused
backend/tests/integration/test_volume_control.py:19: F401 `pathlib.Path` imported but unused
backend/tests/integration/test_websocket_events.py:19: F401 `unittest.mock.patch` imported but unused
backend/tests/integration/test_websocket_events.py:22: F401 `fastapi.WebSocket` imported but unused
backend/tests/integration/test_websocket_events.py:28: F401 `WebSocketEventCollector` imported but unused
backend/tests/test_audio_state_machine.py:12: F401 `unittest.mock.patch` imported but unused
backend/tests/test_bluetooth_source.py:13: F401 `asyncio` imported but unused
backend/tests/test_bluetooth_source.py:14: F401 `unittest.mock.MagicMock` imported but unused
backend/tests/test_core_equalizer.py:11: F401 `asyncio` imported but unused
backend/tests/test_core_multiroom.py:15: F401 `datetime.datetime` imported but unused
backend/tests/test_core_multiroom.py:24: F401 `SpeakerType` imported but unused
backend/tests/test_core_multiroom.py:38-39: F401 `get_online_clients`, `get_online_client_ids` imported but unused
backend/tests/test_core_multiroom.py:880,2901,3128,3167: F841 `zone`/`result` assigned but unused
backend/tests/test_core_volume.py:9: F401 `unittest.mock.patch`, `MagicMock` imported but unused
backend/tests/test_core_volume.py:572,611: F841 `client_id` assigned but unused
backend/tests/test_crossover_service.py:21-24,27: F401 models/constants imported but unused
backend/tests/test_equalizer_models.py:12: F401 `pytest` imported but unused
backend/tests/test_mac_source.py:13: F401 `unittest.mock.MagicMock` imported but unused
backend/tests/test_multiroom_equalizer_service.py:14: F401 `unittest.mock.MagicMock` imported but unused
backend/tests/test_podcast_source.py:13: F401 `asyncio` imported but unused
backend/tests/test_radio_source.py:13: F401 `asyncio` imported but unused
backend/tests/test_radio_source.py:14: F401 `unittest.mock.MagicMock` imported but unused
backend/tests/test_routing_service.py:7: F401 `os` imported but unused
backend/tests/test_routing_service.py:8: F401 `unittest.mock.MagicMock` imported but unused
backend/tests/test_settings_service.py:9: F401 `Mock`, `mock_open`, `AsyncMock` imported but unused
backend/tests/test_settings_service.py:304: F841 `saved_value` assigned but unused
backend/tests/test_spotify_source.py:14: F401 `asyncio` imported but unused
backend/tests/test_spotify_source.py:16: F401 `os` imported but unused
backend/tests/test_state_machine.py:7: F401 `Mock`, `patch` imported but unused
backend/tests/test_state_machine.py:9: F401 `SystemAudioState` imported but unused
backend/tests/test_update_service.py:6: F401 `os` imported but unused
backend/tests/test_update_service.py:7: F401 `pathlib.Path` imported but unused
backend/tests/test_update_service.py:10: F401 `Mock`, `MagicMock` imported but unused
backend/tests/test_update_service.py:81,92,103: F841 `result` assigned but unused
backend/tests/test_version_helpers.py:5: F401 `pytest` imported but unused
backend/tests/test_version_service.py:9: F401 `Mock` imported but unused
backend/tests/test_volume_api.py:11: F401 `unittest.mock.patch` imported but unused
backend/tests/test_volume_service.py:6: F401 `unittest.mock.patch` imported but unused
backend/tests/test_websocket_server.py:8: F401 `unittest.mock.MagicMock` imported but unused
milo-client/app/tests/test_routes.py:5: F401 `patch`, `MagicMock` imported but unused
milo-client/app/tests/test_services_equalizer.py:5: F401 `Mock`, `AsyncMock`, `MagicMock` imported but unused
milo-client/app/tests/test_services_equalizer.py:6: F401 `asyncio` imported but unused
milo-client/app/tests/test_services_snapclient.py:5: F401 `Mock`, `MagicMock` imported but unused
milo-client/app/tests/test_services_snapclient.py:6: F401 `asyncio` imported but unused
milo-client/app/tests/test_services_snapclient.py:34: F841 `version` assigned but unused
milo-client/app/tests/test_services_snapclient.py:110: F841 `result` assigned but unused
```

</details>

---

## E501: Line Too Long (~2427 hits)

Not listed individually. Current default limit is 88 chars.
Most violations are in:
- `backend/api/equalizer.py` — long route definitions and error messages
- `backend/api/settings.py` — long route definitions
- `backend/core/multiroom/` — complex method signatures
- `backend/tests/` — long mock chains and assertions

Consider raising line length to 100 or 120 in a `ruff.toml` / `pyproject.toml`.

---

## Quick Fix Commands

```bash
# Fix all auto-fixable issues (unused imports, whitespace)
ruff check backend/ milo-client/app/ --select F401,W291,W292,W293 --fix

# Preview fixes without applying
ruff check backend/ milo-client/app/ --select F401,W291,W292,W293 --fix --diff
```
