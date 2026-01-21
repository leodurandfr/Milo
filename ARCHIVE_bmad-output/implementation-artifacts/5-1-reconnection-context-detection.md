# Story 5.1: Reconnection Context Detection

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **system**,
I want **to detect the reconnection context when a client comes back online**,
so that **I can apply the correct volume and DSP settings based on the situation**.

## Acceptance Criteria

1. **AC1: Zone Membership Detection** - Given a client reconnects (Snapcast event), when ClientRegistryService receives the connection event, then the system determines if client is IN_ZONE or STANDALONE (based on zone_id)

2. **AC2: IN_ZONE Context Detection** - Given a client is IN_ZONE, when determining reconnection context, then the system checks if other zone members are ONLINE or all OFFLINE

3. **AC3: STANDALONE Context Detection** - Given a client is STANDALONE, when determining reconnection context, then the system checks if any other clients are ONLINE globally

4. **AC4: Context Enum Implementation** - Given ClientRegistryService, when I implement `_get_reconnection_context(mac_id)`, then the method returns one of 4 contexts:
   - `IN_ZONE_OTHERS_ONLINE` (FR7)
   - `IN_ZONE_ALL_OFFLINE` (FR8)
   - `STANDALONE_OTHERS_ONLINE` (FR9)
   - `STANDALONE_ALONE` (FR10)

5. **AC5: Context Used for Sync Dispatch** - Given the reconnection context is determined, when sync is dispatched, then the appropriate volume/DSP sync strategy is selected (Stories 5.2 and 5.3 will implement the strategies)

## Tasks / Subtasks

- [x] Task 1: Define ReconnectionContext Enum (AC: #4)
  - [x] 1.1 Add `ReconnectionContext` enum to `backend/core/multiroom/models.py`
  - [x] 1.2 Define 4 values: `IN_ZONE_OTHERS_ONLINE`, `IN_ZONE_ALL_OFFLINE`, `STANDALONE_OTHERS_ONLINE`, `STANDALONE_ALONE`
  - [x] 1.3 Add docstrings explaining each context with FR reference

- [x] Task 2: Implement `get_reconnection_context()` method (AC: #1, #2, #3, #4)
  - [x] 2.1 Add method to `ClientRegistryService` in `backend/core/multiroom/registry.py`
  - [x] 2.2 Implement zone membership check (`client.zone_id is not None`)
  - [x] 2.3 Implement IN_ZONE context detection (check `get_other_online_zone_clients()` excluding self)
  - [x] 2.4 Implement STANDALONE context detection (check `get_other_online_clients()` excluding self)
  - [x] 2.5 Return appropriate `ReconnectionContext` enum value

- [x] Task 3: Refactor SnapcastWebSocketService to use context detection (AC: #5)
  - [x] 3.1 Modify `_sync_existing_client_volume()` in `backend/core/multiroom/websocket.py`
  - [x] 3.2 Call `registry.get_reconnection_context(mac_id)` before sync
  - [x] 3.3 Log the detected context for debugging
  - [x] 3.4 Pass context in sync_status["context"] (prepare for Stories 5.2/5.3)

- [x] Task 4: Add helper methods for context detection (AC: #2, #3)
  - [x] 4.1 Add `get_other_online_zone_clients(mac_id)` to ClientRegistryService
  - [x] 4.2 Add `get_other_online_clients(mac_id)` to ClientRegistryService
  - [x] 4.3 Ensure methods exclude the reconnecting client itself

- [x] Task 5: Write unit tests for context detection (AC: #1-#5)
  - [x] 5.1 Test IN_ZONE with others ONLINE returns `IN_ZONE_OTHERS_ONLINE`
  - [x] 5.2 Test IN_ZONE with all others OFFLINE returns `IN_ZONE_ALL_OFFLINE`
  - [x] 5.3 Test STANDALONE with others ONLINE returns `STANDALONE_OTHERS_ONLINE`
  - [x] 5.4 Test STANDALONE as first client returns `STANDALONE_ALONE`
  - [x] 5.5 Test edge case: client in zone but zone only has 1 member (edge case)

- [x] Task 6: Write integration tests for reconnection flow (AC: #1-#5)
  - [x] 6.1 Test context detection E2E for each of the 4 contexts
  - [x] 6.2 Test context is included in sync_status response
  - [x] 6.3 Test context changes when client joins/leaves zone

## Dev Notes

### CRITICAL: Understanding the 4 Reconnection Scenarios (FR7-FR10)

This story implements the **detection phase** of client reconnection. The actual sync logic will be implemented in Stories 5.2 (IN_ZONE) and 5.3 (STANDALONE). This story's job is to:

1. **Detect** which of the 4 scenarios applies
2. **Return** the appropriate context enum
3. **Prepare** for the sync strategies in subsequent stories

| Context | FR | Zone State | Other Clients | Volume Source | DSP Source |
|---------|-----|------------|---------------|---------------|------------|
| `IN_ZONE_OTHERS_ONLINE` | FR7 | Client in zone | Zone has other ONLINE clients | Zone avg | zone.dsp_settings |
| `IN_ZONE_ALL_OFFLINE` | FR8 | Client in zone | All zone clients OFFLINE | startup_volume_db | zone.dsp_settings |
| `STANDALONE_OTHERS_ONLINE` | FR9 | Client standalone | Any other client ONLINE | Global avg | standalone_dsp[mac_id] |
| `STANDALONE_ALONE` | FR10 | Client standalone | No other clients ONLINE | startup_volume_db | standalone_dsp[mac_id] |

### Existing Implementation Analysis

**Current state in `backend/core/multiroom/websocket.py`:**

The `_sync_existing_client_volume()` method already contains **implicit** context detection logic scattered across:
- Lines 686-694: Zone membership check via `self.registry.get_zone_for_client(mac_id)`
- The sync logic is partially there but not formalized into a context enum

**What needs to change:**
1. Extract context detection into a dedicated method in `ClientRegistryService`
2. Make the context explicit via an enum
3. Prepare for Stories 5.2/5.3 to implement the actual sync strategies

### Architecture Compliance

**From architecture.md:**

```
ClientRegistryService (QUOI)
    ├── VolumeService.set_volume(mac_id, db) → CamillaDSPProxy
    └── DspService.apply_filters(mac_id, settings) → CamillaDSPProxy
```

**Key decision from architecture:**
- Reconnection logic is **centralized** in `ClientRegistryService`
- Method name: `syncClientOnReconnect(mac_id)` which:
  1. Detects the context (IN_ZONE/STANDALONE × others ONLINE/OFFLINE)
  2. Delegates to appropriate sync strategy

### Code Pattern to Follow

**ReconnectionContext enum (to add in models.py):**

```python
from enum import Enum

class ReconnectionContext(str, Enum):
    """
    Context for client reconnection sync strategy selection.

    Based on FR7-FR10 from PRD:
    - FR7: IN_ZONE client reconnects with others ONLINE
    - FR8: IN_ZONE client reconnects with ALL others OFFLINE
    - FR9: STANDALONE client reconnects with others ONLINE
    - FR10: STANDALONE client reconnects alone (first client)
    """
    IN_ZONE_OTHERS_ONLINE = "in_zone_others_online"      # FR7
    IN_ZONE_ALL_OFFLINE = "in_zone_all_offline"          # FR8
    STANDALONE_OTHERS_ONLINE = "standalone_others_online" # FR9
    STANDALONE_ALONE = "standalone_alone"                # FR10
```

**_get_reconnection_context() method (to add in registry.py):**

```python
def _get_reconnection_context(self, mac_id: str) -> ReconnectionContext:
    """
    Determine the reconnection context for a client.

    This is the first step of the reconnection sync process.
    The context determines which volume and DSP sources to use.

    Args:
        mac_id: The reconnecting client's mac_id

    Returns:
        One of the 4 ReconnectionContext values
    """
    client = self._clients.get(mac_id)
    if not client:
        # Unknown client - treat as standalone alone
        return ReconnectionContext.STANDALONE_ALONE

    if client.zone_id:
        # Client is in a zone
        zone = self._zones.get(client.zone_id)
        if zone:
            # Check if other zone members are online
            other_online = [
                cid for cid in zone.client_ids
                if cid != mac_id and self._clients.get(cid, Client()).online
            ]
            if other_online:
                return ReconnectionContext.IN_ZONE_OTHERS_ONLINE
            else:
                return ReconnectionContext.IN_ZONE_ALL_OFFLINE
        # Zone not found but client has zone_id - edge case
        return ReconnectionContext.IN_ZONE_ALL_OFFLINE

    # Client is standalone
    other_online = [
        c for c in self._clients.values()
        if c.mac_id != mac_id and c.online
    ]
    if other_online:
        return ReconnectionContext.STANDALONE_OTHERS_ONLINE
    else:
        return ReconnectionContext.STANDALONE_ALONE
```

### Previous Story Intelligence (4-8-frontend-dsp-controls)

**Patterns established in Epic 4:**
1. **Zone endpoint pattern**: Backend handles propagation to ONLINE zone clients
2. **WebSocket events**: All state changes broadcast via `_emit_event()`
3. **Test pattern**: Unit tests + integration tests for each feature

**Code patterns from 4-8:**
- Store delegation pattern: `dspStore.js` delegates to `clientRegistryStore.js`
- WebSocket event handlers in stores for real-time updates

### Git Intelligence (Recent Commits)

```
9a31e2f fix(volume): sync _local_volume_db in multiroom mode to preserve volume on mode switch
5bd630f feat(install): set ALSA volume to 100% based on HiFiBerry card type
f7cf915 docs: update documentation to reflect feature-based architecture
f9967a6 feat(volume): change default to -60dB and sync volumes on mode switch
2ece0e5 fix(eventbus): add missing await to async emit() calls
```

**Key insights from recent commits:**
- Volume sync has been improved recently (9a31e2f, f9967a6)
- Default volume is now -60dB (f9967a6)
- EventBus calls must be awaited (2ece0e5)

### Existing Tests to Reference

**From `backend/tests/integration/test_reconnection_scenarios.py`:**

Tests already exist for the 4 FR scenarios:
- `TestReconnectionInZone` class - FR7, FR8 tests
- `TestReconnectionStandalone` class - FR9, FR10 tests
- `TestVolumeStateCalculations` - Volume average calculations

**Add new tests for:**
- `_get_reconnection_context()` method return values
- Edge cases (empty zones, unknown clients)

### Integration Points

**SnapcastWebSocketService → ClientRegistryService:**
```
Client.OnConnect event
    ↓
_handle_client_connect()
    ↓
_sync_existing_client_volume()
    ↓
registry._get_reconnection_context(mac_id)  ← NEW
    ↓
Dispatch to appropriate sync strategy (Stories 5.2/5.3)
```

### Files to Modify

| File | Priority | Changes |
|------|----------|---------|
| `backend/core/multiroom/models.py` | HIGH | Add `ReconnectionContext` enum |
| `backend/core/multiroom/registry.py` | HIGH | Add `_get_reconnection_context()` and helper methods |
| `backend/core/multiroom/websocket.py` | MEDIUM | Refactor to use context detection |
| `backend/tests/test_core_multiroom.py` | MEDIUM | Add unit tests for context detection |
| `backend/tests/integration/test_reconnection_scenarios.py` | MEDIUM | Add integration tests |

### Project Structure Notes

```
backend/core/multiroom/
├── models.py          # Add ReconnectionContext enum
├── registry.py        # Add _get_reconnection_context() method
├── websocket.py       # Refactor to use context detection
├── crossover.py       # No changes (Story 5.5)
└── snapcast.py        # No changes

backend/tests/
├── test_core_multiroom.py                    # Add unit tests
└── integration/
    └── test_reconnection_scenarios.py        # Add integration tests
```

### Testing Strategy

**Unit tests (`backend/tests/test_core_multiroom.py`):**
1. Test `_get_reconnection_context()` with mocked registry state
2. Test all 4 context combinations
3. Test edge cases (unknown client, empty zone, missing zone)

**Integration tests (`backend/tests/integration/test_reconnection_scenarios.py`):**
1. Test context detection via Snapcast event simulation
2. Verify context logged correctly
3. Verify context passed to sync methods

### Dependencies

- **Depends on**: Epic 1-4 completion (client registry, zones, DSP)
- **Blocks**: Stories 5.2, 5.3 (need context detection before implementing sync strategies)

### NFR Compliance

- **NFR4**: Client reconnection sync completes within 1 second
  - Context detection should be < 10ms (in-memory lookups only)
- **NFR17**: Code follows Python async/await patterns throughout
  - Method is sync (no I/O), but callers are async

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic-5] - Epic 5 definition and FR7-FR10
- [Source: _bmad-output/planning-artifacts/architecture.md#State-Machine-Reconnexion] - Reconnection architecture
- [Source: backend/core/multiroom/registry.py] - ClientRegistryService implementation
- [Source: backend/core/multiroom/websocket.py] - SnapcastWebSocketService with current sync logic
- [Source: backend/tests/integration/test_reconnection_scenarios.py] - Existing reconnection tests
- [Source: _bmad-output/project-context.md] - AI agent implementation rules

## Implementation Summary

### Files Modified
- `backend/core/multiroom/models.py` - Added `ReconnectionContext` enum (4 values: FR7-FR10)
- `backend/core/multiroom/registry.py` - Added `get_reconnection_context()` and helper methods
- `backend/core/multiroom/websocket.py` - Refactored `_sync_existing_client_volume()` to use context detection

### Key Implementation Details

1. **ReconnectionContext Enum** (models.py:45-70)
   - `IN_ZONE_OTHERS_ONLINE` - FR7: Zone client, others online → zone average volume
   - `IN_ZONE_ALL_OFFLINE` - FR8: Zone client, all others offline → startup volume
   - `STANDALONE_OTHERS_ONLINE` - FR9: Standalone, others online → global average
   - `STANDALONE_ALONE` - FR10: Standalone, no others online → startup volume

2. **Helper Methods** (registry.py:713-752)
   - `get_other_online_zone_clients(mac_id)` - Returns online zone members excluding self
   - `get_other_online_clients(mac_id)` - Returns all online clients excluding self

3. **Context Detection** (registry.py:754-805)
   - `get_reconnection_context(mac_id)` - Main method that:
     - Checks if client is in zone (zone_id exists)
     - For IN_ZONE: checks if other zone members are online
     - For STANDALONE: checks if any other clients are online
     - Returns appropriate enum value

4. **WebSocket Integration** (websocket.py:185-260)
   - Context detection happens first in `_sync_existing_client_volume()`
   - Context is logged for debugging
   - Context determines IN_ZONE vs STANDALONE sync path
   - Context is returned in `sync_status["context"]` for stories 5.2/5.3

### Test Coverage
- **Unit Tests** (test_core_multiroom.py): 27 tests covering enum, context detection, helpers, DSP sync methods
- **Integration Tests** (test_reconnection_scenarios.py): 6 new E2E tests for context flow
- **Total**: 147 tests pass in test_core_multiroom.py, 25 tests pass in test_reconnection_scenarios.py

### Code Review Fixes (2026-01-20)

**Reviewer:** Claude Opus 4.5 (adversarial code review)

**Issues Fixed:**

1. **🔴 HIGH: Type Mismatch in `_sync_zone_dsp_to_client()`** (websocket.py:791-810)
   - **Problem:** Code used `.get()` on `EqFilter` dataclass objects instead of accessing attributes
   - **Fix:** Changed `flt.get('id')` → `flt.id`, `flt.get('freq')` → `flt.frequency`, etc.
   - **Fix:** Added `flt.to_dict()` when queueing failed filters for pending settings

2. **🔴 HIGH: Dataclass objects passed to proxy instead of dicts** (websocket.py:813-836)
   - **Problem:** `CompressorSettings` and `LoudnessSettings` objects passed directly to `proxy_service.request()`
   - **Fix:** Added `.to_dict()` conversion for compressor and loudness before API calls

3. **🟡 MEDIUM: Missing unit tests for DSP sync methods**
   - **Fix:** Added `TestSyncZoneDspToClient` (6 tests) and `TestSyncStandaloneDspToClient` (4 tests)

**New Tests Added:**
- `test_eq_filter_attributes_accessed_correctly`
- `test_compressor_settings_converted_to_dict`
- `test_loudness_settings_converted_to_dict`
- `test_failed_filter_queued_as_dict`
- `test_sync_returns_true_on_success`
- `test_sync_handles_missing_client`
- `test_no_saved_settings_returns_true`
- `test_saved_settings_applied_via_proxy`
- `test_local_client_uses_dsp_service`
- `test_sync_handles_missing_client` (standalone)

---

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

Tests executed:
- `python -m pytest tests/test_core_multiroom.py -v` → 137 passed
- `python -m pytest tests/integration/test_reconnection_scenarios.py -v` → 25 passed

### Completion Notes List

1. Implemented `ReconnectionContext` enum with 4 values matching FR7-FR10
2. Added helper methods `get_other_online_zone_clients()` and `get_other_online_clients()`
3. Implemented `get_reconnection_context()` method with proper zone/standalone detection
4. Refactored `_sync_existing_client_volume()` to detect and log context before sync
5. Added comprehensive unit tests (17 tests) and integration tests (6 tests)
6. All acceptance criteria (AC1-AC5) are met

### File List

| File | Changes |
|------|---------|
| `backend/core/multiroom/models.py` | Added `ReconnectionContext` enum |
| `backend/core/multiroom/registry.py` | Added `get_reconnection_context()`, `get_other_online_zone_clients()`, `get_other_online_clients()` |
| `backend/core/multiroom/websocket.py` | Updated `_sync_existing_client_volume()` to use context detection; **[Code Review]** Fixed type mismatch bugs in `_sync_zone_dsp_to_client()` |
| `backend/tests/test_core_multiroom.py` | Added `TestReconnectionContextEnum`, `TestReconnectionContextDetection`, `TestReconnectionHelperMethods`; **[Code Review]** Added `TestSyncZoneDspToClient`, `TestSyncStandaloneDspToClient` |
| `backend/tests/integration/test_reconnection_scenarios.py` | Added `TestReconnectionContextDetectionIntegration` |

