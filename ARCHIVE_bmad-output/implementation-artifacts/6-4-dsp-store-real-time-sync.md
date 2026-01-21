# Story 6.4: DSP Store Real-Time Sync

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **frontend application**,
I want **dspStore to automatically update on WebSocket events**,
so that **DSP settings displayed are always current**.

## Acceptance Criteria

1. **AC1: Zone DSP Changed Event Handling** - Given dspStore is initialized, when a `dsp_changed` event is received for a zone via WebSocket, then the zone's dsp_settings in state are updated, and if the zone is currently selected in UI, controls reflect new values.

2. **AC2: Client DSP Changed Event Handling** - Given dspStore is initialized, when a `dsp_changed` event is received for a standalone client via WebSocket, then the client's dsp_settings in state are updated, and if the client is currently selected in UI, controls reflect new values.

3. **AC3: Preset Change to Manual** - Given dspStore tracks active preset, when a `dsp_changed` event includes preset change to "Manual", then the preset selector updates to show "Manual" as active.

4. **AC4: Remote User DSP Changes** - Given another user changes DSP settings, when local UI is displaying the same target, then local UI updates immediately to show the remote changes without conflict or overwrite.

5. **AC5: No Polling for DSP State** - Given dspStore is initialized, when implementing event handlers, then handlers use Vue reactivity and no polling or periodic refresh is used for DSP state.

## Tasks / Subtasks

- [x] Task 1: Audit existing dsp_changed handler implementation (AC: #1, #2, #4)
  - [x] 1.1 Verify `dspStore.handleDspChanged()` correctly processes zone target_type
  - [x] 1.2 Verify `dspStore.handleDspChanged()` correctly processes client target_type
  - [x] 1.3 Test that filter changes from dsp_changed event update `filters.value`
  - [x] 1.4 Test that compressor/loudness changes from dsp_changed event update local state
  - [x] 1.5 Verify the `filterThrottleMap.size === 0` guard prevents overwrites during local editing

- [x] Task 2: Verify filter_changed handler integration (AC: #1, #2, #4)
  - [x] 2.1 Confirm `handleFilterChanged()` is registered in DspSettings.vue for category 'dsp'
  - [x] 2.2 Verify individual filter updates propagate correctly to UI
  - [x] 2.3 Test that the throttle guard works to prevent conflicts during user edits

- [x] Task 3: Verify preset event handling (AC: #3)
  - [x] 3.1 Confirm `handlePresetLoaded()` is registered in DspSettings.vue
  - [x] 3.2 Verify preset change events update `activePreset.value`
  - [x] 3.3 Test that preset change to 'manual' correctly shows "Manual" in UI
  - [x] 3.4 Verify `isManualMode` computed correctly reflects current state

- [x] Task 4: Verify enabled state change handling (AC: #1, #2)
  - [x] 4.1 Confirm `handleEnabledChanged()` is registered in MultiroomControl.vue and DspSettings.vue
  - [x] 4.2 Verify `isDspEffectsEnabled.value` updates on WebSocket event
  - [x] 4.3 Test that DSP enable/disable toggle reflects remote changes

- [x] Task 5: Ensure no polling exists (AC: #5)
  - [x] 5.1 Audit dspStore.js for any setInterval/setTimeout polling patterns
  - [x] 5.2 Audit DspSettings.vue and related components for polling patterns
  - [x] 5.3 Verify all DSP state updates come from WebSocket events

- [x] Task 6: Write/update integration tests (AC: #1-#5)
  - [x] 6.1 Add test: dsp_changed (zone) → dspStore filters/compressor/loudness updated
  - [x] 6.2 Add test: dsp_changed (client) → dspStore filters updated when target matches
  - [x] 6.3 Add test: preset change via WebSocket → activePreset updated
  - [x] 6.4 Add test: enabled_changed → isDspEffectsEnabled updated
  - [x] 6.5 Add test: filter_changed during throttle → no overwrite
  - [x] 6.6 Add test: Multiple rapid DSP events → all processed without data loss

- [x] Task 7: Documentation and cleanup (AC: all)
  - [x] 7.1 Update any outdated comments in dspStore.js WebSocket handlers
  - [x] 7.2 Verify handler registrations are consistent across App.vue and component-level
  - [x] 7.3 Ensure no legacy event handlers remain

## Dev Notes

### CRITICAL: Handler Architecture Already Established

**Good news: The WebSocket event handlers are already implemented in dspStore!**

The current architecture is:

```
WebSocket Event
    ↓
App.vue / Component handlers
    ↓
dspStore handler methods
    ↓
Reactive state updates (filters.value, compressor.value, etc.)
    ↓
Vue reactivity triggers UI update
```

**This story is primarily verification, testing, and ensuring completeness** - not new implementation.

### Current Handler Implementation Analysis

**dspStore.js handlers (lines 1244-1336):**

1. **`handleDspChanged(event)`** - Primary handler for `multiroom.dsp_changed` events:
   - Checks `target_type` (zone or client) to determine relevance
   - Only updates if `selectedTarget.value` matches target_id or is in target zone
   - Updates `filters.value`, `compressor.value`, `loudness.value`
   - Has throttle guard: `filterThrottleMap.size === 0` to prevent conflicts during editing

2. **`handleFilterChanged(event)`** - Handler for individual `dsp.filter_changed` events:
   - Updates single filter in `filters.value`
   - Has throttle guard to prevent conflicts

3. **`handlePresetLoaded(event)`** - Handler for `dsp.preset_loaded` events:
   - Updates `activePreset.value` from event

4. **`handleEnabledChanged(event)`** - Handler for `dsp.enabled_changed` events:
   - Updates `isDspEffectsEnabled.value`

### Event Registration Points

**App.vue (line 159):**
```javascript
on('multiroom', 'dsp_changed', (event) => dspStore.handleDspChanged(event)),
```

**MultiroomControl.vue (line 491):**
```javascript
on('dsp', 'enabled_changed', (e) => dspStore.handleEnabledChanged(e)),
```

**DspSettings.vue (lines 211-217):**
```javascript
on('dsp', 'filter_changed', handleDspFilterChanged),
on('dsp', 'preset_loaded', handleDspPresetLoaded),
on('dsp', 'enabled_changed', (e) => dspStore.handleEnabledChanged(e))
```

### Key Implementation Details

**Target Matching Logic (dspStore.js:1251-1260):**
```javascript
if (target_type === 'client') {
  isRelevant = target_id === selectedTarget.value;
} else if (target_type === 'zone') {
  const zone = registryStore.getZoneForClient(selectedTarget.value);
  isRelevant = zone && zone.id === target_id;
}
```

**Throttle Guard (dspStore.js:1267):**
```javascript
if (filterThrottleMap.size === 0) {
  // Only update filters if no local edits in progress
  for (const filterData of dsp_settings.filters) { ... }
}
```

### What to Verify

1. **Event Format Compatibility** - Backend events use format:
   ```json
   {
     "category": "multiroom",
     "type": "dsp_changed",
     "data": {
       "target_type": "zone"|"client",
       "target_id": "uuid-or-mac",
       "dsp_settings": { "filters": [...], "compressor": {...}, "loudness": {...} }
     }
   }
   ```

2. **Preset Events** - Both `dsp.preset_loaded` (legacy) and `multiroom.dsp_changed` with preset info need handling.

3. **Filter Updates** - Individual `dsp.filter_changed` events complement the full `dsp_changed` events.

### What NOT to Do

- ❌ Do NOT add polling mechanisms for DSP state
- ❌ Do NOT duplicate handler registrations
- ❌ Do NOT bypass the throttle guard
- ❌ Do NOT overwrite filters during local editing (throttle map check)

### NFR Compliance

- **NFR2**: WebSocket state updates reach frontend within 100ms ✅ (handled by websocket.js)
- **NFR3**: DSP filter changes applied within 200ms (backend-side)
- **FR30**: Updates immediately without polling ✅ (reactive chain)

### Previous Story Intelligence (6.3)

**From Story 6.3 (Multiroom Store Real-Time Sync):**
- The `client_state_changed` and `zone_changed` events are handled by `clientRegistryStore`
- `dspStore` derives `availableTargets` and `linkedGroups` from `clientRegistryStore` as computed
- The reactive chain pattern works well - this story follows the same pattern for DSP-specific events
- Existing tests in `dspStore.test.js` (85 tests) cover many scenarios

**Key Learning**: The pattern established in Story 6.3 applies here - verify the reactive chain is functioning rather than implementing new features.

### Git Intelligence

Recent commits show DSP/multiroom stability:
```
57877fd fix(dsp): resolve preset loading and filter restoration issues
99a98b7 fix(multiroom): compute crossover_enabled dynamically
14c47ed refactor(frontend): consolidate Pinia stores and eliminate state duplication
```

Commit 57877fd fixed preset loading - ensure these fixes are preserved.

### Architecture Compliance

**From architecture.md:**
- WebSocket events with explicit identifiers in `data` field ✅
- DSP changes propagate via `dsp_changed` event ✅
- `dspStore.js` for presets and UI DSP state ✅

**From project-context.md:**
- State sync flow: Backend → WebSocket event → Pinia store → Reactive UI ✅
- `/api/dsp/` endpoints for DSP operations ✅

### Project Structure Notes

**Files to Verify/Test:**

| File | Role |
|------|------|
| `frontend/src/stores/dspStore.js` | DSP state and WebSocket handlers |
| `frontend/src/App.vue` | Registers multiroom.dsp_changed handler |
| `frontend/src/components/multiroom/MultiroomControl.vue` | Registers dsp.enabled_changed |
| `frontend/src/components/settings/categories/DspSettings.vue` | Registers filter_changed, preset_loaded, enabled_changed |

**Test Files:**

| File | Purpose |
|------|---------|
| `frontend/tests/stores/dspStore.test.js` | Existing 85 tests - extend for real-time sync |

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-6.4] - Story requirements
- [Source: _bmad-output/planning-artifacts/architecture.md#WebSocket-Events] - Event format spec
- [Source: _bmad-output/implementation-artifacts/6-3-multiroom-store-real-time-sync.md] - Previous story context
- [Source: frontend/src/stores/dspStore.js:1244-1336] - WebSocket handler implementations
- [Source: frontend/src/App.vue:159] - multiroom.dsp_changed handler registration
- [Source: frontend/src/components/settings/categories/DspSettings.vue:211-217] - Component-level handlers
- [Source: _bmad-output/project-context.md] - AI agent implementation rules

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

No debug issues encountered - story was primarily verification and testing.

### Completion Notes List

1. **Task 1 Completed**: Audited `handleDspChanged()` in dspStore.js:1244-1290. Verified zone target_type matching (line 1256-1259), client target_type matching (line 1253-1255), filter/compressor/loudness updates (lines 1265-1289), and throttle guard (line 1267).

2. **Task 2 Completed**: Verified `handleFilterChanged()` registered in DspSettings.vue:211. Individual filter updates work correctly with throttle guard at dspStore.js:1297.

3. **Task 3 Completed**: Verified `handlePresetLoaded()` registered in DspSettings.vue:214. Updates `activePreset.value` supporting both `id` and `name` formats. `isManualMode` computed (dspStore.js:137-163) correctly detects manual mode.

4. **Task 4 Completed**: Verified `handleEnabledChanged()` registered in both MultiroomControl.vue:491 and DspSettings.vue:217. Updates `isDspEffectsEnabled.value` correctly (dspStore.js:1407-1411).

5. **Task 5 Completed**: No polling patterns found in dspStore.js or DspSettings.vue. LevelMeters.vue has polling for VU-meters which is appropriate (not DSP state). All DSP state updates come via WebSocket events.

6. **Task 6 Completed**: Added comprehensive test suite "dspStore - Real-Time Sync (Story 6.4)" covering all ACs:
   - AC1: Zone DSP changed event handling (2 tests)
   - AC2: Client DSP changed event handling (2 tests)
   - AC3: Preset change to manual (3 tests)
   - AC4: Remote user DSP changes with throttle guard (2 tests)
   - AC5: No polling verification (1 test)
   - Multiple rapid events (2 tests)
   - enabled_changed handling (3 tests)

7. **Task 7 Completed**: Comments in dspStore.js are accurate. Handler registrations are consistent: App.vue handles global multiroom events, DspSettings.vue handles DSP-specific events. No legacy handlers found.

### File List

| File | Action | Description |
|------|--------|-------------|
| `frontend/tests/stores/dspStore.test.js` | Modified | Added 15 new tests for Story 6.4 Real-Time Sync covering all ACs |
| `frontend/src/stores/dspStore.js` | Modified | [Code Review] Added null check to handleFilterChanged for consistency |

## Change Log

| Date | Change |
|------|--------|
| 2026-01-21 | Story completed: Verified existing WebSocket handlers, added comprehensive test coverage for real-time sync scenarios |
| 2026-01-21 | Code review: Fixed L2 (added null check to handleFilterChanged for consistency with other handlers) |
