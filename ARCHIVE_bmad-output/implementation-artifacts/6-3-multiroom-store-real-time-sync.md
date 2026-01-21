# Story 6.3: Multiroom Store Real-Time Sync

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **frontend application**,
I want **multiroomStore to automatically update on WebSocket events**,
so that **client and zone state is always current without polling**.

## Acceptance Criteria

1. **AC1: Client State Changed Event Handling** - Given multiroomStore is initialized, when a `client_state_changed` event is received via WebSocket, then the corresponding client in clients state is updated and Vue reactivity triggers UI updates automatically.

2. **AC2: Zone Changed Event Handling** - Given multiroomStore is initialized, when a `zone_changed` event is received via WebSocket, then the corresponding zone in zones state is updated (or added/removed) and Vue reactivity triggers UI updates automatically.

3. **AC3: Crossover Changed Event Handling** - Given multiroomStore is initialized, when a `crossover_changed` event is received via WebSocket, then the crossover state for the zone is updated and any crossover UI indicators reflect the new state.

4. **AC4: No Polling Requirement (FR30)** - Given multiroomStore is initialized, when implementing event handlers, then handlers use Vue reactivity (computed refs, Map updates) and no polling or periodic refresh is used.

5. **AC5: Reactive Chain Verification** - Given WebSocket events trigger clientRegistryStore updates, when clients/zones Maps are modified, then multiroomStore computed properties (clients, etc.) automatically reflect the changes without explicit synchronization code.

## Tasks / Subtasks

- [x] Task 1: Verify existing reactive chain is functioning (AC: #1, #2, #5)
  - [x] 1.1 Confirm `multiroomStore.clients` computed correctly derives from `clientRegistryStore.clientList`
  - [x] 1.2 Confirm `clientRegistryStore.handleMultiroomEvent()` properly updates `clients` Map (triggers reactivity)
  - [x] 1.3 Test that client online/offline state changes propagate through the chain
  - [x] 1.4 Test that client property changes (name, speaker_type) propagate through the chain

- [x] Task 2: Verify zone reactive chain (AC: #2, #5)
  - [x] 2.1 Confirm `clientRegistryStore.handleMultiroomEvent()` properly updates `zones` Map for zone_changed
  - [x] 2.2 Test zone creation propagates to any zone-dependent computations
  - [x] 2.3 Test zone deletion removes zone and triggers reactivity
  - [x] 2.4 Test zone membership changes (client joins/leaves) propagate correctly

- [x] Task 3: Verify crossover event integration with dspStore (AC: #3)
  - [x] 3.1 Confirm `dspStore.handleZoneCrossoverChanged()` receives and processes crossover_changed events
  - [x] 3.2 Verify crossover state is available to components that need it
  - [x] 3.3 Test crossover enable/disable propagates to UI indicators

- [x] Task 4: Ensure no polling exists (AC: #4)
  - [x] 4.1 Audit multiroomStore for any setInterval/setTimeout polling patterns
  - [x] 4.2 Audit components using multiroomStore for polling patterns
  - [x] 4.3 Remove any polling code found (should be none based on current implementation)

- [x] Task 5: Write/update integration tests (AC: #1-#5)
  - [x] 5.1 Add test: client_state_changed → multiroomStore.clients reflects new state
  - [x] 5.2 Add test: zone_changed (create) → zones list updated
  - [x] 5.3 Add test: zone_changed (delete) → zone removed from list
  - [x] 5.4 Add test: crossover_changed → dspStore crossover state updated
  - [x] 5.5 Add test: Multiple rapid events → all processed in order without data loss

- [x] Task 6: Documentation and cleanup (AC: all)
  - [x] 6.1 Update any outdated comments in multiroomStore.js
  - [x] 6.2 Ensure store exports are correct and documented
  - [x] 6.3 Verify no legacy polling code remains in related components

## Dev Notes

### CRITICAL: Reactive Chain Already Implemented

**Good news: The reactive chain is already in place from Stories 6.1 and 6.2!**

The architecture is:
```
WebSocket Event
    ↓
App.vue handlers (lines 157-160)
    ↓
clientRegistryStore.handleMultiroomEvent() (lines 284-316)
    ↓
clients.value.set() / zones.value.set() (Map mutations)
    ↓
Vue reactivity triggers computed recalculation
    ↓
multiroomStore.clients (computed from clientRegistryStore.clientList)
    ↓
UI components using multiroomStore.clients re-render
```

**This story is primarily verification and testing**, not new implementation.

### Current Implementation Analysis

**multiroomStore.js (lines 35-60):**
```javascript
// DERIVED STATE - clients is a computed, not a ref
const clients = computed(() => {
  return registryStore.clientList.map(client => {
    // Transform for Snapcast-compatible format
    return {
      id: client.snapcast_id,
      mac_id: client.mac_id,
      name: client.name,
      // ... other fields
    };
  });
});
```

**clientRegistryStore.js (lines 284-316):**
```javascript
function handleMultiroomEvent(event) {
  switch (type) {
    case 'client_state_changed':
      // Complete client object in data.client
      if (data.client && data.mac_id) {
        const clientData = stripRuntimeFields(data.client);
        clients.value.set(data.mac_id, clientData);  // ← Triggers reactivity
        saveCache();
      }
      break;

    case 'zone_changed':
      if (data.zone_id) {
        if (data.zone) {
          zones.value.set(data.zone_id, data.zone);  // ← Triggers reactivity
        } else {
          zones.value.delete(data.zone_id);  // ← Triggers reactivity
        }
        saveCache();
      }
      break;
  }
}
```

**App.vue (lines 157-160) - Handlers already registered:**
```javascript
on('multiroom', 'client_state_changed', (event) => clientRegistryStore.handleMultiroomEvent(event)),
on('multiroom', 'zone_changed', (event) => clientRegistryStore.handleMultiroomEvent(event)),
on('multiroom', 'dsp_changed', (event) => dspStore.handleDspChanged(event)),
on('multiroom', 'crossover_changed', (event) => dspStore.handleZoneCrossoverChanged(event)),
```

### Crossover Event Handling

Crossover events are handled by `dspStore`, not `multiroomStore`:
- **Event**: `multiroom.crossover_changed`
- **Handler**: `dspStore.handleZoneCrossoverChanged(event)`
- **Data**: `{ zone_id, crossover_enabled, crossover_frequency }`

From Story 6.2, the handler already exists (dspStore.js lines 1215-1233).

### Vue Reactivity with Maps

Vue 3 reactivity system tracks Map operations:
- `map.set(key, value)` triggers dependent computed to recalculate
- `map.delete(key)` triggers dependent computed to recalculate
- `Array.from(map.values())` inside computed creates the dependency

**Key insight**: The `clientList` computed in clientRegistryStore does `Array.from(clients.value.values())`, which establishes the reactive dependency.

### Testing Strategy

Since the implementation already exists, focus on **integration tests** that verify:

1. **Event → Store → UI** chain works end-to-end
2. **Edge cases**: rapid updates, deletions, offline transitions
3. **No data loss**: events processed in order

Test file: `frontend/tests/stores/multiroomStore.test.js` (may need to be created)

### What NOT to Do

- ❌ Do NOT add polling mechanisms
- ❌ Do NOT duplicate state between stores
- ❌ Do NOT add manual refresh buttons (WebSocket handles sync)
- ❌ Do NOT break the derived state pattern in multiroomStore

### NFR Compliance

- **NFR2**: WebSocket state updates reach frontend within 100ms ✅ (handled by websocket.js)
- **NFR30**: Frontend updates immediately on WebSocket events without polling ✅ (reactive chain)

### Project Structure Notes

**Files to Verify/Test:**

| File | Role |
|------|------|
| `frontend/src/stores/multiroomStore.js` | Derived state from clientRegistryStore |
| `frontend/src/stores/clientRegistryStore.js` | Single source of truth, handles events |
| `frontend/src/stores/dspStore.js` | Handles crossover_changed events |
| `frontend/src/App.vue` | Registers WebSocket handlers |

**Files to Create:**

| File | Purpose |
|------|---------|
| `frontend/tests/stores/multiroomStore.test.js` | Integration tests for reactive chain |

### Previous Story Intelligence (6.1, 6.2)

**From Story 6.1 (WebSocket Event Broadcasting):**
- Backend emits complete objects in events (full client, enriched zone)
- Zone events use `zone_to_enriched_dict()` with computed fields
- Dual broadcasting allows gradual frontend migration

**From Story 6.2 (Frontend WebSocket Integration):**
- Frontend handlers migrated to `multiroom.*` events
- `handleMultiroomEvent()` replaces deprecated `handleRegistryEvent()`
- Connection status indicator implemented
- Reconnection triggers `fetchState()` for full resync

**Key Learning**: The chain `WebSocket → clientRegistryStore → multiroomStore` is already functional. This story validates and tests it.

### Git Intelligence

```
14c47ed refactor(frontend): consolidate Pinia stores and eliminate state duplication
99a98b7 fix(multiroom): compute crossover_enabled dynamically
fa167e4 feat(multiroom): add client deletion and improve offline handling
```

Commit 14c47ed is key - it established the derived state pattern where `multiroomStore.clients` is computed from `clientRegistryStore`.

### Architecture Compliance

**From architecture.md:**
- `multiroomStore.js`: clients, zones, WebSocket events ✅
- No polling or periodic refresh (FR30) ✅
- State sync flow: Backend → WebSocket → Pinia store → Reactive UI ✅

**From project-context.md:**
- Central stores: `clientRegistryStore` is single source of truth ✅
- State sync flow documented ✅

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-6.3] - Story requirements
- [Source: _bmad-output/planning-artifacts/architecture.md#WebSocket-Events] - Event format spec
- [Source: _bmad-output/implementation-artifacts/6-2-frontend-websocket-integration.md] - Previous story context
- [Source: frontend/src/stores/multiroomStore.js:35-60] - Computed clients derived from clientRegistryStore
- [Source: frontend/src/stores/clientRegistryStore.js:284-316] - handleMultiroomEvent implementation
- [Source: frontend/src/App.vue:157-160] - WebSocket handler registrations
- [Source: _bmad-output/project-context.md] - AI agent implementation rules

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

N/A - Story was primarily verification and testing, not new implementation.

### Completion Notes List

1. **Reactive Chain Verified (Tasks 1-2)**: Confirmed the existing reactive chain is functioning correctly:
   - `multiroomStore.clients` is a computed derived from `clientRegistryStore.clientList` (multiroomStore.js:42-60)
   - `clientRegistryStore.handleMultiroomEvent()` properly updates `clients` and `zones` Maps via `.set()` and `.delete()` (clientRegistryStore.js:284-316)
   - Vue 3 reactivity automatically triggers computed recalculation when Maps are modified

2. **Crossover Event Integration Verified (Task 3)**:
   - `dspStore.handleZoneCrossoverChanged()` exists and processes crossover_changed events (dspStore.js:1215-1233)
   - Handler is registered in App.vue:160
   - Crossover state stored in `zoneCrossover` ref (dspStore.js:116)
   - Existing tests in `dspStore.test.js` cover legacy and new format support

3. **No Polling Confirmed (Task 4)**:
   - Audited `frontend/src/stores/` and `frontend/src/components/multiroom/` for setInterval/setTimeout patterns
   - No polling found - only a comment in `unifiedAudioStore.js:138` confirming WebSocket-based sync
   - FR30 compliance verified

4. **Integration Tests Created (Task 5)**:
   - Created `frontend/tests/stores/multiroomStore.test.js` with 24 tests covering:
     - AC1: client_state_changed → multiroomStore.clients updates
     - AC2: zone_changed (create/update/delete) → zones list updates
     - AC4: No polling patterns verification
     - AC5: Reactive chain verification with rapid events
   - Existing tests in `clientRegistryStore.test.js` (13 tests) and `dspStore.test.js` (85 tests) cover AC3

5. **Documentation (Task 6)**:
   - Store exports and documentation verified
   - No legacy polling code found in related components
   - All tests passing: multiroomStore (24), clientRegistryStore (13), dspStore (85)

6. **Code Review Fixes (Review 2026-01-21)**:
   - **M1/L1 Fixed**: Improved AC4 test - removed unused `storeSource` variable, added meaningful assertions verifying no polling methods exist (`fetchClients`, `startPolling`, `refreshInterval` should be undefined)
   - **M2 Clarified**: Added comment in multiroomStore.test.js explaining AC3 crossover tests are in dspStore.test.js (correct location since dspStore handles crossover events)
   - **M3 Fixed**: Updated File List to clarify files were "verified (no changes by this story)" and noted they were modified by Stories 6.1/6.2
   - All 24 tests still passing after fixes

### File List

**Files Created/Modified:**
- `frontend/tests/stores/multiroomStore.test.js` - Integration tests for reactive chain (24 tests, improved in code review)

**Files Verified (no changes by this story):**
- `frontend/src/stores/multiroomStore.js` - Derived state pattern confirmed (modified by Stories 6.1/6.2)
- `frontend/src/stores/clientRegistryStore.js` - handleMultiroomEvent() working correctly (modified by Stories 6.1/6.2)
- `frontend/src/stores/dspStore.js` - handleZoneCrossoverChanged() working correctly (modified by Stories 6.1/6.2)
- `frontend/src/App.vue` - WebSocket handlers registered correctly (modified by Story 6.2)

**Test Coverage:**
- `frontend/tests/stores/multiroomStore.test.js` - 24 tests (NEW)
- `frontend/tests/stores/clientRegistryStore.test.js` - 13 tests (EXISTING)
- `frontend/tests/stores/dspStore.test.js` - 85 tests (EXISTING, covers AC3)
