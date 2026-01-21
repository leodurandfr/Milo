# Story 5.4: Crossover Service Implementation

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **developer**,
I want **a CrossoverService that calculates crossover filters based on speaker types**,
so that **the system can automatically configure bass management for zones with subwoofers**.

## Acceptance Criteria

1. **AC1: CrossoverService Module Structure** - Given the backend codebase, when I implement CrossoverService in `core/multiroom/crossover.py`, then the service provides methods to calculate and apply crossover filters.

2. **AC2: Crossover Filter Calculation by Speaker Type** - Given a zone with speaker_type configuration, when I call `calculate_crossover_filters(zone_id)`, then the service returns appropriate filters for each client based on speaker_type:
   - `satellite` → highpass (configurable frequency, default 120Hz)
   - `bookshelf` → highpass (configurable frequency, default 80Hz)
   - `tower` → highpass (configurable frequency, default 50Hz)
   - `subwoofer` → lowpass (configurable frequency matching zone crossover)

3. **AC3: Crossover Settings Persistence** - Given CrossoverService, when crossover settings are stored, then they are persisted in zone via ClientRegistryService with:
   - `enabled` (bool) - whether crossover is active
   - `frequency` (Hz) - crossover frequency
   - Per-client filter types tracked internally

4. **AC4: Zone and Client Integration** - Given the Zone model, when crossover settings are needed, then `zone.crossover_frequency` and computed `crossover_enabled` are available via `zone_to_enriched_dict()`.

5. **AC5: Remote Client Support** - Given clients can be remote (satellite devices), when crossover filters are applied, then the service proxies requests to remote clients via HTTP API.

6. **AC6: Pending Settings Queue** - Given a remote client is offline, when crossover changes are requested, then settings are queued in `_pending_settings` and applied when client reconnects.

## Tasks / Subtasks

- [x] Task 1: Verify CrossoverService Module Structure (AC: #1)
  - [x] 1.1 Verify `CrossoverService` class exists in `backend/core/multiroom/crossover.py`
  - [x] 1.2 Verify service is registered in `backend/dependencies.py` as lazy singleton
  - [x] 1.3 Verify service has `initialize()` method for async initialization
  - [x] 1.4 Verify service subscribes to ClientRegistryService events

- [x] Task 2: Verify Speaker Type Crossover Calculation (AC: #2)
  - [x] 2.1 Verify `DEFAULT_CROSSOVER_FREQUENCIES` dict in models.py with correct defaults
  - [x] 2.2 Verify `get_zone_auto_crossover(zone_id)` method calculates minimum frequency
  - [x] 2.3 Verify highpass applied to satellite/bookshelf/tower speakers
  - [x] 2.4 Verify lowpass applied to subwoofer speakers

- [x] Task 3: Verify Crossover Settings Persistence (AC: #3)
  - [x] 3.1 Verify Zone model uses `crossover_frequency` via ClientRegistryService
  - [x] 3.2 Verify `crossover_enabled` computed dynamically based on subwoofer presence
  - [x] 3.3 Verify `zone_to_enriched_dict()` includes crossover settings

- [x] Task 4: Verify Remote Client Proxy (AC: #5)
  - [x] 4.1 Verify `_proxy_crossover_to_client(hostname, enabled, frequency)` method
  - [x] 4.2 Verify `_proxy_lowpass_to_client(hostname, enabled, frequency)` method
  - [x] 4.3 Verify HTTP timeout handling (5 seconds)

- [x] Task 5: Verify Pending Settings Queue (AC: #6)
  - [x] 5.1 Verify `queue_pending_settings(client_id, setting_type, settings)` method
  - [x] 5.2 Verify `apply_pending_settings(client_id)` method on reconnect
  - [x] 5.3 Verify `has_pending_settings(client_id)` check method

- [x] Task 6: Add Missing Zone Model Fields (AC: #4)
  - [x] 6.1 Add `crossover_frequency: Optional[int]` to Zone dataclass in models.py
  - [x] 6.2 Add `crossover_enabled: Optional[bool]` to Zone dataclass in models.py
  - [x] 6.3 Update `Zone.to_dict()` to include crossover fields
  - [x] 6.4 Update `Zone.from_dict()` to parse crossover fields

- [x] Task 7: Add Client Crossover Frequency Field (AC: #4)
  - [x] 7.1 Add `crossover_frequency: Optional[int]` to Client dataclass in models.py
  - [x] 7.2 Update `Client.to_dict()` to include crossover_frequency
  - [x] 7.3 Update `Client.from_dict()` to parse crossover_frequency

- [x] Task 8: Write Unit Tests (AC: #1-#6)
  - [x] 8.1 Test `calculate_crossover_filters()` returns correct filter types per speaker
  - [x] 8.2 Test `apply_zone_crossover()` applies filters to online clients only
  - [x] 8.3 Test `apply_zone_crossover()` handles subwoofer ONLINE/OFFLINE toggle
  - [x] 8.4 Test pending settings queue and apply on reconnect
  - [x] 8.5 Test remote client proxy with success and failure scenarios

- [x] Task 9: Write Integration Tests (AC: #1-#6)
  - [x] 9.1 Test E2E crossover activation when subwoofer joins zone
  - [x] 9.2 Test E2E crossover deactivation when subwoofer leaves zone
  - [x] 9.3 Test E2E crossover recalculation on speaker_type change
  - [x] 9.4 Test E2E pending settings applied on client reconnect

## Dev Notes

### CRITICAL: Understanding the Crossover Service Architecture

The `CrossoverService` is already **partially implemented** in `backend/core/multiroom/crossover.py` (831 lines). This story's primary goal is to:

1. **Validate** the existing implementation against the acceptance criteria
2. **Complete** missing model fields (`crossover_frequency`, `crossover_enabled` on Zone/Client)
3. **Add comprehensive tests** for the crossover logic

### Existing Implementation Analysis

**`backend/core/multiroom/crossover.py` (already implemented):**

| Method | Status | Description |
|--------|--------|-------------|
| `__init__()` | ✅ Done | Constructor with settings_service, dsp_service, event_bus |
| `set_registry()` | ✅ Done | Sets ClientRegistryService and subscribes to events |
| `_handle_registry_event()` | ✅ Done | Handles CLIENT_CONNECTED, ZONE_CREATED, etc. |
| `initialize()` | ✅ Done | Async initialization |
| `get_client_type()` | ✅ Done | Returns speaker_type and crossover_frequency |
| `set_client_speaker_type()` | ✅ Done | Sets speaker type with crossover recalculation |
| `set_client_crossover_frequency()` | ✅ Done | Sets custom crossover frequency |
| `get_zone_crossover()` | ✅ Done | Returns zone crossover settings |
| `get_zone_auto_crossover()` | ✅ Done | Calculates min frequency from non-subwoofer speakers |
| `set_zone_crossover_frequency()` | ✅ Done | Sets zone crossover frequency |
| `apply_zone_crossover()` | ✅ Done | Applies highpass/lowpass to zone members |
| `_set_client_crossover()` | ✅ Done | Applies highpass to local or remote client |
| `_set_client_lowpass()` | ✅ Done | Applies lowpass to local or remote client |
| `_proxy_crossover_to_client()` | ✅ Done | HTTP proxy for remote crossover |
| `_proxy_lowpass_to_client()` | ✅ Done | HTTP proxy for remote lowpass |
| `queue_pending_settings()` | ✅ Done | Queues settings for offline clients |
| `apply_pending_settings()` | ✅ Done | Applies pending settings on reconnect |

### Missing Model Fields

**Zone model (`models.py:396-446`):**
- ❌ Missing `crossover_frequency: Optional[int]` field
- ❌ Missing `crossover_enabled: Optional[bool]` field
- These are currently computed dynamically in `registry.zone_to_enriched_dict()`

**Client model (`models.py:333-393`):**
- ❌ Missing `crossover_frequency: Optional[int]` field
- Currently accessed via `client.crossover_frequency` in crossover.py but NOT defined in dataclass

### Default Crossover Frequencies (from models.py)

```python
DEFAULT_CROSSOVER_FREQUENCIES = {
    'satellite': 120,   # Small speakers, limited bass (~120 Hz)
    'bookshelf': 80,    # Medium speakers (THX standard)
    'tower': 50,        # Full-range speakers (~40-50 Hz response)
    'subwoofer': None   # No highpass for subwoofer (receives lowpass)
}
```

### Architecture Compliance

**From architecture.md:**

| speaker_type | Filtre | Fréquence |
|--------------|--------|-----------|
| satellite | highpass | configurable (ex: 150Hz) |
| bookshelf | highpass | configurable (ex: 80Hz) |
| tower | highpass | configurable (ex: 60Hz) |
| subwoofer | lowpass | configurable |

**Key decisions:**
- Crossover is recalculated on every ONLINE status change or speaker_type change
- `zone.dsp_settings.crossover` stores crossover state (but we use zone-level fields now)
- CrossoverService subscribes to ClientRegistryService events

### Service Dependencies

```
CrossoverService
    ├── settings_service (SettingsService)
    ├── dsp_service (CamillaDSPService) for local client
    ├── event_bus (EventBus) for broadcasting
    └── _registry (ClientRegistryService) for client/zone data
```

### Event Subscription Flow

```
ClientRegistryService events → CrossoverService._handle_registry_event()
    ├── CLIENT_CONNECTED → apply_pending_settings() + recalculate_zones_for_client()
    ├── CLIENT_DISCONNECTED → recalculate_zones_for_client()
    ├── CLIENT_UPDATED → recalculate_zones_for_client()
    ├── ZONE_CREATED → apply_zone_crossover()
    ├── ZONE_UPDATED → apply_zone_crossover()
    ├── ZONE_DELETED → disable filters for all zone clients
    ├── zone_client_added → apply_zone_crossover()
    └── zone_client_removed → disable filters for removed client + apply_zone_crossover()
```

### Previous Story Intelligence

**From Stories 5-1, 5-2, 5-3:**
- Reconnection context detection works (ReconnectionContext enum)
- DSP sync to clients works (`_sync_zone_dsp_to_client()`, `_sync_standalone_dsp_to_client()`)
- Pending settings queue pattern established in crossover.py
- Code review patterns: use `.to_dict()` for dataclass objects, await EventBus calls

### Git Intelligence (Recent Commits)

```
99a98b7 fix(multiroom): compute crossover_enabled dynamically based on subwoofer availability
fa167e4 feat(multiroom): add client deletion and improve offline handling
4e3aa94 fix(dsp): prevent premature zone deletion when removing client
```

**Key insights:**
- `crossover_enabled` is computed dynamically (not stored)
- Client deletion and offline handling have been improved
- Zone deletion edge cases have been addressed

### Files to Modify

| File | Priority | Changes |
|------|----------|---------|
| `backend/core/multiroom/models.py` | HIGH | Add `crossover_frequency`, `crossover_enabled` to Zone; add `crossover_frequency` to Client |
| `backend/tests/test_crossover_service.py` | HIGH | Create new test file with comprehensive unit tests |
| `backend/tests/integration/test_crossover_scenarios.py` | MEDIUM | Create integration tests for crossover activation/deactivation |

### Project Structure Notes

```
backend/core/multiroom/
├── models.py          # Add crossover fields to Zone and Client
├── registry.py        # Already handles crossover_frequency via update_zone()
├── crossover.py       # ALREADY IMPLEMENTED (831 lines)
├── websocket.py       # No changes needed
└── snapcast.py        # No changes needed

backend/tests/
├── test_crossover_service.py              # NEW: Unit tests
└── integration/
    └── test_crossover_scenarios.py        # NEW: Integration tests
```

### Testing Strategy

**Unit tests (`backend/tests/test_crossover_service.py`):**

1. `TestCrossoverFilterCalculation`
   - Test satellite speaker → highpass 120Hz
   - Test bookshelf speaker → highpass 80Hz
   - Test tower speaker → highpass 50Hz
   - Test subwoofer → lowpass at zone frequency
   - Test custom crossover frequency override

2. `TestZoneCrossoverApplication`
   - Test `apply_zone_crossover()` with subwoofer ONLINE → enables crossover
   - Test `apply_zone_crossover()` with subwoofer OFFLINE → disables crossover
   - Test only ONLINE clients receive filter updates
   - Test OFFLINE clients get queued for later

3. `TestPendingSettingsQueue`
   - Test `queue_pending_settings()` stores correctly
   - Test `apply_pending_settings()` applies crossover
   - Test `apply_pending_settings()` applies lowpass
   - Test `has_pending_settings()` returns correct status
   - Test `clear_pending_settings()` removes entries

4. `TestRemoteClientProxy`
   - Test successful HTTP proxy call
   - Test timeout handling (queue settings)
   - Test offline client handling

**Integration tests (`backend/tests/integration/test_crossover_scenarios.py`):**

1. `TestCrossoverActivation`
   - Test E2E: subwoofer comes ONLINE → crossover activates
   - Test E2E: crossover applies to all zone members
   - Test E2E: WebSocket event `crossover_changed` broadcast

2. `TestCrossoverDeactivation`
   - Test E2E: subwoofer goes OFFLINE → crossover deactivates
   - Test E2E: all zone members return to full-range

3. `TestCrossoverRecalculation`
   - Test E2E: speaker_type change triggers recalculation
   - Test E2E: client join/leave zone triggers recalculation

### NFR Compliance

- **NFR5**: Crossover activation/deactivation completes within 500ms
  - Zone crossover calculation: < 10ms (in-memory)
  - HTTP proxy calls: < 5s timeout per client (parallel if multiple)
  - Total for typical 2-client zone: < 100ms

### Edge Cases to Handle

1. **No subwoofer in zone**: `crossover_enabled = false`
2. **Subwoofer OFFLINE but exists in zone**: `crossover_enabled = false`
3. **All zone clients OFFLINE**: Skip application, queue for later
4. **Remote client unreachable**: Queue pending settings, log warning
5. **Zone deleted**: Disable all filters for ex-members
6. **Client changes speaker_type**: Recalculate zone crossover

### WebSocket Event Format

**crossover_changed event:**
```json
{
  "category": "multiroom",
  "type": "crossover_changed",
  "data": {
    "zone_id": "uuid-...",
    "crossover_enabled": true,
    "crossover_frequency": 80
  }
}
```

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-5.4] - Story requirements
- [Source: _bmad-output/planning-artifacts/architecture.md#Crossover] - Crossover architecture
- [Source: backend/core/multiroom/crossover.py] - Existing CrossoverService implementation
- [Source: backend/core/multiroom/models.py] - Domain models (need crossover fields)
- [Source: backend/core/multiroom/registry.py:896-922] - `zone_to_enriched_dict()` computes crossover_enabled
- [Source: _bmad-output/implementation-artifacts/5-1-reconnection-context-detection.md] - Previous story patterns
- [Source: _bmad-output/project-context.md] - AI agent implementation rules

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

None required - implementation was straightforward.

### Completion Notes List

1. **Task 6 Complete**: Added `crossover_frequency` (default 80Hz) and `crossover_enabled` (default None for auto mode) to Zone dataclass. Updated `to_dict()` and `from_dict()` methods.

2. **Task 7 Complete**: Added `crossover_frequency` (default None - uses speaker_type default) to Client dataclass. Updated `to_dict()` and `from_dict()` methods.

3. **Registry Enhancement**: Updated `update_zone()` method in registry.py to accept `crossover_frequency` and `crossover_enabled` parameters. Updated `update_speaker_type()` to persist `crossover_frequency` to client.

4. **zone_to_enriched_dict() Enhancement**: Modified to respect `zone.crossover_enabled` when not None, while still requiring subwoofer presence for crossover to be effective.

5. **Task 8 Complete**: Created comprehensive unit tests in `test_crossover_service.py` (34 tests):
   - TestCrossoverFilterCalculation (5 tests)
   - TestZoneCrossoverApplication (3 tests)
   - TestSubwooferOnlineOfflineToggle (2 tests)
   - TestPendingSettingsQueue (7 tests)
   - TestRemoteClientProxy (4 tests)
   - TestHelperFunctions (2 tests)
   - TestZoneCrossoverFields (4 tests)
   - TestClientCrossoverFields (4 tests)
   - TestAutoCrossoverCalculation (3 tests)

6. **Task 9 Complete**: Created integration tests in `test_crossover_scenarios.py` (12 tests):
   - TestCrossoverActivation (2 tests)
   - TestCrossoverDeactivation (2 tests)
   - TestCrossoverRecalculation (3 tests)
   - TestPendingSettingsOnReconnect (3 tests)
   - TestRegistryEventIntegration (2 tests)

7. **All Tests Pass**: 46 new tests pass. 177 existing tests in test_core_multiroom.py pass (no regressions).

### Code Review Fixes (2026-01-21)

8. **HIGH-2 Fix**: Fixed `apply_zone_crossover()` auto mode logic - when `zone.crossover_enabled` is None (auto mode), crossover now correctly enables when there's an online subwoofer. Previously, `None and True` evaluated to `None` (falsy), preventing crossover from ever activating in auto mode.

9. **MEDIUM-2 Fix**: Added `crossover_frequency` to `_persist_clients()` in registry.py - client crossover frequency preferences are now persisted to settings.json and survive restarts.

10. **MEDIUM-4 Fix**: Fixed inconsistent frequency validation - `set_zone_crossover_frequency()` now uses 20-200 Hz range (matching `set_client_crossover_frequency()`) instead of 40-200 Hz.

11. **HIGH-1 Fix**: Test files added to git staging (were untracked).

### File List

**Modified:**
- `backend/core/multiroom/models.py` - Added crossover fields to Zone and Client dataclasses
- `backend/core/multiroom/registry.py` - Updated update_zone(), update_speaker_type(), _persist_clients() methods, enhanced zone_to_enriched_dict()
- `backend/core/multiroom/crossover.py` - Fixed apply_zone_crossover() auto mode logic, fixed frequency validation range

**Created:**
- `backend/tests/test_crossover_service.py` - New unit test file (34 tests)
- `backend/tests/integration/test_crossover_scenarios.py` - New integration test file (12 tests)

### Change Log

- 2026-01-21: Code review fixes - auto mode logic, persistence, frequency validation
- 2026-01-21: Implemented Story 5.4 - CrossoverService model fields and comprehensive tests

