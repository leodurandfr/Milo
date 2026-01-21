# Story 5.2: IN_ZONE Reconnection Sync

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user**,
I want **my zone clients to automatically sync with the correct volume and DSP on reconnection**,
so that **they seamlessly rejoin the zone audio experience**.

## Acceptance Criteria

1. **AC1: IN_ZONE_OTHERS_ONLINE Volume Sync (FR7)** - Given a client reconnects with context `IN_ZONE_OTHERS_ONLINE`, when `syncClientOnReconnect(mac_id)` is called, then volume is set to the average of other ONLINE zone members.

2. **AC2: IN_ZONE_ALL_OFFLINE Volume Sync (FR8)** - Given a client reconnects with context `IN_ZONE_ALL_OFFLINE`, when `syncClientOnReconnect(mac_id)` is called, then volume is set to `startup_volume_db` (first client of the day).

3. **AC3: Zone DSP Settings Applied** - Given a client reconnects in either IN_ZONE context, when `syncClientOnReconnect(mac_id)` is called, then DSP settings are loaded from `zone.dsp_settings` and applied to CamillaDSP.

4. **AC4: Sync Time Compliance (NFR4)** - Given sync is triggered, when volume and DSP are applied, then the entire sync process completes within 1 second.

5. **AC5: WebSocket Event Broadcast** - Given sync is complete, when settings are applied, then a WebSocket event `client_state_changed` is broadcast with updated volume and `dsp_ready` status.

6. **AC6: Pending Settings Handling** - Given DSP sync fails for some filters, when sync completes, then failed settings are queued via `crossover_service.queue_pending_settings()` for later retry.

## Tasks / Subtasks

- [x] Task 1: Implement Zone Average Volume Calculation (AC: #1)
  - [x] 1.1 Add method `get_zone_average_volume(zone_id, exclude_mac_id)` to `ClientRegistryService`
  - [x] 1.2 Calculate average from ONLINE clients in zone, excluding reconnecting client
  - [x] 1.3 Return `None` if no other ONLINE clients (triggers FR8 fallback)

- [x] Task 2: Implement IN_ZONE Volume Sync Strategy (AC: #1, #2)
  - [x] 2.1 Add method `_get_inzone_target_volume(mac_id, context)` to `SnapcastWebSocketService`
  - [x] 2.2 For `IN_ZONE_OTHERS_ONLINE`: use zone average volume
  - [x] 2.3 For `IN_ZONE_ALL_OFFLINE`: use `startup_volume_db` from `VolumeConfigService`
  - [x] 2.4 Replace current volume sync with context-aware sync

- [x] Task 3: Refactor Volume Sync in `_sync_existing_client_volume()` (AC: #1, #2, #4)
  - [x] 3.1 Extract target volume calculation based on context
  - [x] 3.2 Apply target volume to client via `VolumeService` or `DSPController`
  - [x] 3.3 Update client's `volume_db` in `VolumeStateStore`
  - [x] 3.4 Ensure total sync time < 1 second

- [x] Task 4: Verify Zone DSP Sync (AC: #3, #6)
  - [x] 4.1 Verify `_sync_zone_dsp_to_client()` correctly applies EQ filters
  - [x] 4.2 Verify compressor settings are applied
  - [x] 4.3 Verify loudness settings are applied
  - [x] 4.4 Verify failed settings are queued in `crossover_service.pending_settings`

- [x] Task 5: Implement WebSocket Broadcast (AC: #5)
  - [x] 5.1 Add volume state broadcast after sync completion
  - [x] 5.2 Include updated volume state for all clients
  - [x] 5.3 Verify event reaches frontend stores

- [x] Task 6: Write Unit Tests (AC: #1-#6)
  - [x] 6.1 Test zone average volume calculation (include/exclude scenarios)
  - [x] 6.2 Test `IN_ZONE_OTHERS_ONLINE` uses zone average
  - [x] 6.3 Test `IN_ZONE_ALL_OFFLINE` uses startup_volume_db
  - [x] 6.4 Test DSP settings applied from zone
  - [x] 6.5 Test WebSocket event broadcast

- [x] Task 7: Write Integration Tests (AC: #1-#5)
  - [x] 7.1 Test E2E reconnection with other zone members online
  - [x] 7.2 Test E2E reconnection as first zone client
  - [x] 7.3 Test DSP settings applied from zone
  - [x] 7.4 Test WebSocket broadcast after sync

## Dev Notes

### CRITICAL: Understanding the IN_ZONE Reconnection Scenarios

This story implements **two specific reconnection scenarios** for clients that are members of a zone:

| Context | FR | Condition | Volume Source | DSP Source |
|---------|-----|-----------|---------------|------------|
| `IN_ZONE_OTHERS_ONLINE` | FR7 | Other zone members are ONLINE | Zone average (ONLINE members) | `zone.dsp_settings` |
| `IN_ZONE_ALL_OFFLINE` | FR8 | All other zone members OFFLINE | `startup_volume_db` | `zone.dsp_settings` |

**Key insight**: DSP source is ALWAYS `zone.dsp_settings` for IN_ZONE contexts. Only volume differs.

### Existing Implementation Analysis

**Current implementation in `websocket.py` (lines 640-732):**

The `_sync_existing_client_volume()` method already:
1. ✅ Detects reconnection context via `registry.get_reconnection_context(mac_id)`
2. ✅ Joins client to multiroom group
3. ✅ Sets Snapcast volume to 100% passthrough
4. ✅ Calls `_sync_zone_dsp_to_client()` for IN_ZONE contexts
5. ❌ **MISSING**: Context-aware volume calculation (currently delegates to VolumeService which doesn't use zone average)

**What needs to change:**
1. Add zone average volume calculation method to `ClientRegistryService`
2. Update volume sync to use zone average for `IN_ZONE_OTHERS_ONLINE`
3. Update volume sync to use `startup_volume_db` for `IN_ZONE_ALL_OFFLINE`
4. Ensure WebSocket broadcast includes sync context

### Architecture Compliance

**From architecture.md:**

```
ClientRegistryService (QUOI)
    ├── VolumeService.set_volume(mac_id, db) → CamillaDSPProxy
    └── DspService.apply_filters(mac_id, settings) → CamillaDSPProxy
```

**Key decisions:**
- Reconnection logic is **centralized** in `ClientRegistryService`
- Volume sync uses `VolumeService` for DSP volume application
- DSP sync uses `dsp_client_proxy_service` for remote clients

### Code Pattern to Follow

**Zone average volume calculation (to add in registry.py):**

```python
def get_zone_average_volume(self, zone_id: str, exclude_mac_id: Optional[str] = None) -> Optional[float]:
    """
    Calculate average volume of ONLINE zone clients.

    Used for IN_ZONE_OTHERS_ONLINE reconnection sync (FR7).

    Args:
        zone_id: The zone ID
        exclude_mac_id: Client to exclude (reconnecting client)

    Returns:
        Average volume in dB, or None if no ONLINE clients
    """
    zone = self._zones.get(zone_id)
    if not zone:
        return None

    online_volumes = []
    for mac_id in zone.client_ids:
        if mac_id == exclude_mac_id:
            continue
        client = self._clients.get(mac_id)
        if client and client.online:
            online_volumes.append(client.volume_db)

    if not online_volumes:
        return None

    return sum(online_volumes) / len(online_volumes)
```

**Target volume calculation (to add in websocket.py):**

```python
async def _get_inzone_target_volume(
    self,
    mac_id: str,
    context: ReconnectionContext
) -> float:
    """
    Get target volume for IN_ZONE reconnection contexts.

    Args:
        mac_id: Reconnecting client's mac_id
        context: IN_ZONE_OTHERS_ONLINE or IN_ZONE_ALL_OFFLINE

    Returns:
        Target volume in dB
    """
    if context == ReconnectionContext.IN_ZONE_OTHERS_ONLINE:
        # FR7: Use zone average
        client = self.registry.get_client(mac_id)
        if client and client.zone_id:
            avg = self.registry.get_zone_average_volume(client.zone_id, exclude_mac_id=mac_id)
            if avg is not None:
                return avg
        # Fallback to startup if zone average unavailable
        self.logger.warning(f"Zone average unavailable for {mac_id}, using startup volume")

    # FR8: IN_ZONE_ALL_OFFLINE or fallback - use startup_volume_db
    volume_service = getattr(self.state_machine, 'volume_service', None)
    if volume_service:
        return volume_service.config.config.startup_volume_db
    return DEFAULT_VOLUME_DB
```

### Previous Story Intelligence (5-1)

**From Story 5.1 Implementation Summary:**

1. **ReconnectionContext Enum** already implemented in `models.py:45-70`
2. **Helper methods** `get_other_online_zone_clients()` and `get_other_online_clients()` already in `registry.py:717-759`
3. **Context detection** `get_reconnection_context()` already in `registry.py:761-823`
4. **DSP sync** `_sync_zone_dsp_to_client()` and `_sync_standalone_dsp_to_client()` already in `websocket.py:756-970`

**Code Review Fixes from 5-1:**
- Fixed type mismatch: use `flt.frequency` not `flt.get('freq')` for EqFilter dataclass
- Fixed dataclass conversion: use `.to_dict()` for CompressorSettings and LoudnessSettings

### Git Intelligence (Recent Commits)

```
9a31e2f fix(volume): sync _local_volume_db in multiroom mode to preserve volume on mode switch
5bd630f feat(install): set ALSA volume to 100% based on HiFiBerry card type
f9967a6 feat(volume): change default to -60dB and sync volumes on mode switch
2ece0e5 fix(eventbus): add missing await to async emit() calls
57877fd fix(dsp): resolve preset loading and filter restoration issues
```

**Key insights from recent commits:**
- Default volume is now -60dB (`DEFAULT_VOLUME_DB` in constants.py)
- `_local_volume_db` must be kept in sync in multiroom mode
- EventBus calls must be awaited
- DSP preset/filter restoration has been fixed

### Existing Services to Use

**VolumeService** (`backend/core/volume/service.py`):
- `config.config.startup_volume_db` - Access startup volume setting
- `sync_existing_client_from_snapcast(mac_id)` - Current sync method (needs context-aware update)
- `update_client_volume_db(client_id, volume_db)` - Set specific client volume

**VolumeStateStore** (`backend/core/volume/state.py`):
- `set_client_volume(hostname, volume_db)` - Update client volume state
- `register_client(client_id, volume_db, available)` - Register client with initial volume

**DSPController** (`backend/core/volume/dsp_controller.py`):
- `set_dsp_volume(client_id, volume_db)` - Apply volume to CamillaDSP
- `set_dsp_mute(client_id, mute)` - Apply mute state

### Files to Modify

| File | Priority | Changes |
|------|----------|---------|
| `backend/core/multiroom/registry.py` | HIGH | Add `get_zone_average_volume()` method |
| `backend/core/multiroom/websocket.py` | HIGH | Add `_get_inzone_target_volume()`, update volume sync logic |
| `backend/core/volume/service.py` | MEDIUM | Potentially add context-aware sync method |
| `backend/tests/test_core_multiroom.py` | MEDIUM | Add unit tests for zone average |
| `backend/tests/integration/test_reconnection_scenarios.py` | MEDIUM | Add integration tests for FR7/FR8 |

### Project Structure Notes

```
backend/core/multiroom/
├── models.py          # ReconnectionContext enum (already done)
├── registry.py        # Add get_zone_average_volume()
├── websocket.py       # Add _get_inzone_target_volume(), update sync
├── crossover.py       # No changes
└── snapcast.py        # No changes

backend/core/volume/
├── service.py         # May need context-aware helpers
├── state.py           # VolumeStateStore (SSOT)
├── dsp_controller.py  # DSP volume application
└── config.py          # startup_volume_db access

backend/tests/
├── test_core_multiroom.py                    # Add unit tests
└── integration/
    └── test_reconnection_scenarios.py        # Add FR7/FR8 E2E tests
```

### Testing Strategy

**Unit tests (`backend/tests/test_core_multiroom.py`):**
1. `TestZoneAverageVolume`
   - Test with multiple online clients → returns average
   - Test with one online client → returns that volume
   - Test with no online clients → returns None
   - Test with exclude_mac_id → excludes that client
   - Test with invalid zone_id → returns None

2. `TestInZoneTargetVolume`
   - Test `IN_ZONE_OTHERS_ONLINE` uses zone average
   - Test `IN_ZONE_ALL_OFFLINE` uses startup_volume_db
   - Test fallback when zone average is None

**Integration tests (`backend/tests/integration/test_reconnection_scenarios.py`):**
1. `TestInZoneReconnectionSync`
   - Test FR7: Reconnect with other zone members online → volume = zone average
   - Test FR8: Reconnect as first zone client → volume = startup_volume_db
   - Test DSP settings applied from zone
   - Test WebSocket event broadcast
   - Test sync completes within 1 second

### Dependencies

- **Depends on**: Story 5.1 (context detection) - ✅ DONE
- **Blocks**: Story 5.4 (crossover service needs sync to work)

### NFR Compliance

- **NFR4**: Client reconnection sync completes within 1 second
  - Zone average calculation: < 5ms (in-memory)
  - Volume application: < 100ms (CamillaDSP)
  - DSP sync: < 500ms (multiple API calls)
  - Total: < 700ms (well within 1s)

### Edge Cases to Handle

1. **Zone exists but has no ONLINE clients**: Return None, use startup_volume_db
2. **Zone deleted while client offline**: Client becomes standalone (handled by unregister flow)
3. **Client's stored volume_db is outside limits**: Clamp to config limits
4. **CamillaDSP unavailable**: Queue settings, set dsp_ready=false
5. **Network timeout during sync**: Graceful degradation, client will retry

### WebSocket Event Format

**client_state_changed event:**
```json
{
  "category": "multiroom",
  "type": "client_state_changed",
  "data": {
    "mac_id": "dc:a6:32:7e:d3:43",
    "client": {
      "mac_id": "dc:a6:32:7e:d3:43",
      "name": "Living Room",
      "ip": "192.168.1.100",
      "online": true,
      "zone_id": "uuid-...",
      "volume_db": -25.0,
      "mute": false,
      "speaker_type": "bookshelf",
      "dsp_ready": true
    },
    "sync_context": "in_zone_others_online"
  }
}
```

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.2] - Story requirements
- [Source: _bmad-output/planning-artifacts/architecture.md#State-Machine-Reconnexion] - Reconnection architecture
- [Source: _bmad-output/implementation-artifacts/5-1-reconnection-context-detection.md] - Previous story with context detection
- [Source: backend/core/multiroom/websocket.py:640-732] - Current sync implementation
- [Source: backend/core/multiroom/registry.py:761-823] - Context detection method
- [Source: backend/core/volume/service.py] - VolumeService for volume application
- [Source: _bmad-output/project-context.md] - AI agent implementation rules

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

N/A

### Completion Notes List

**Implementation Summary:**

1. **`get_zone_average_volume(zone_id, exclude_mac_id)`** - New method in `ClientRegistryService` that calculates the average volume of ONLINE zone clients, excluding the reconnecting client. Returns `None` if no ONLINE clients are available (triggering FR8 fallback).

2. **`_get_inzone_target_volume(mac_id, context)`** - New method in `SnapcastWebSocketService` that determines the target volume based on reconnection context:
   - `IN_ZONE_OTHERS_ONLINE` (FR7): Uses zone average from online members
   - `IN_ZONE_ALL_OFFLINE` (FR8): Uses `startup_volume_db` from settings

3. **`_apply_target_volume_to_client(mac_id, target_volume_db)`** - New method in `SnapcastWebSocketService` that applies a specific volume to a client's DSP and updates state in both VolumeService and ClientRegistry.

4. **Modified `_sync_existing_client_volume()`** - Now uses context-aware volume calculation for IN_ZONE contexts. Calls `_get_inzone_target_volume()` and `_apply_target_volume_to_client()` instead of the generic `_sync_client_volume_and_broadcast()`.

5. **WebSocket Broadcast** - Added broadcast call after volume sync completes to notify frontend of reconnected client state.

**Test Coverage:**
- 7 unit tests for `TestZoneAverageVolume` - All passed
- 5 unit tests for `TestInZoneTargetVolume` - All passed
- 5 unit tests for `TestApplyTargetVolumeToClient` - All passed (2 added by code review)
- 5 integration tests for `TestInZoneReconnectionSyncIntegration` - All passed
- 2 integration tests for `TestAC4SyncTimeCompliance` - All passed (added by code review)
- 4 integration tests for `TestAC6PendingSettingsQueue` - All passed (added by code review)

**Key Design Decisions:**
- Zone average calculation excludes the reconnecting client to prevent self-influence
- DSP sync continues to use existing `_sync_zone_dsp_to_client()` which already handles zone.dsp_settings correctly
- Volume sync uses `broadcast=False` in `_apply_target_volume_to_client()` to allow grouped broadcast after all sync operations complete

### File List

| File | Change Type | Description |
|------|-------------|-------------|
| `backend/core/multiroom/registry.py` | MODIFIED | Added `get_zone_average_volume()` method |
| `backend/core/multiroom/websocket.py` | MODIFIED | Added `_get_inzone_target_volume()`, `_apply_target_volume_to_client()`, updated `_sync_existing_client_volume()` for context-aware sync, fixed AC4→AC5 comment |
| `backend/tests/test_core_multiroom.py` | MODIFIED | Added `TestZoneAverageVolume`, `TestInZoneTargetVolume`, `TestApplyTargetVolumeToClient` (5 tests, 2 from review) |
| `backend/tests/integration/test_reconnection_scenarios.py` | MODIFIED | Added `TestInZoneReconnectionSyncIntegration`, `TestAC4SyncTimeCompliance` (2 tests), `TestAC6PendingSettingsQueue` (4 tests) |

### Senior Developer Review (AI)

**Review Date:** 2026-01-20
**Reviewer:** Claude Opus 4.5 (Code Review Workflow)
**Outcome:** ✅ APPROVED with fixes applied

**Issues Found and Fixed:**

| # | Severity | Issue | Resolution |
|---|----------|-------|------------|
| H1 | HIGH | AC4 (NFR4 < 1s) missing performance tests | Added `TestAC4SyncTimeCompliance` with 2 tests |
| M1 | MEDIUM | AC6 pending settings queue not tested | Added `TestAC6PendingSettingsQueue` with 4 tests |
| M2 | MEDIUM | `_apply_target_volume_to_client` missing exception tests | Added 2 exception handling tests |
| M3 | MEDIUM | Comment said AC4 but should be AC5 | Fixed comment in websocket.py:741 |
| L1 | LOW | Excessive debug timestamps in logs | Not fixed (cosmetic) |
| L2 | LOW | Docstring missing context field docs | Not fixed (cosmetic) |

**Verification:**
- All new tests pass (pytest)
- Existing tests unaffected
- Code follows project-context.md patterns

