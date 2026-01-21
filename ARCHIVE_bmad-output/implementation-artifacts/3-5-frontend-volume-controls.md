# Story 3.5: Frontend Volume Controls

Status: completed

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user**,
I want **volume sliders in the multiroom interface**,
So that **I can easily adjust volume for individual clients and zones**.

## Acceptance Criteria

1. **AC1: Client volume slider display**
   - **Given** MultiroomControl.vue displays clients and zones
   - **When** I view a client in MultiroomItem.vue
   - **Then** I see a volume slider showing current volume_db
   - **And** I see a mute toggle button

2. **AC2: Client volume slider interaction**
   - **Given** I drag a client volume slider
   - **When** I release the slider
   - **Then** the new volume is sent via PATCH API
   - **And** the slider reflects the confirmed value

3. **AC3: Zone volume slider display and delta**
   - **Given** I view a zone in MultiroomItem.vue
   - **When** the zone has ONLINE clients
   - **Then** I see a zone volume slider showing average volume
   - **And** the slider applies delta on change

4. **AC4: Real-time WebSocket updates**
   - **Given** volume changes occur (local or remote)
   - **When** a WebSocket `client_state_changed` event is received
   - **Then** all volume sliders update immediately to reflect new values

5. **AC5: Mute toggle functionality**
   - **Given** I toggle mute on a client
   - **When** I click the mute button
   - **Then** the mute state is toggled via API and reflected in UI

## Tasks / Subtasks

- [x] **Task 1: Update API calls to use new MAC-based volume endpoints** (AC: #2, #5)
  - [x] 1.1: Replace `PUT /api/dsp/client/{hostname}/volume` with `PATCH /api/volume/client/mac/{mac_url}` in dspStore.js
  - [x] 1.2: Replace `PUT /api/dsp/client/{hostname}/mute` with `PATCH /api/volume/client/mac/{mac_url}/mute` in dspStore.js
  - [x] 1.3: Replace `POST /api/volume/zone/{zoneId}/delta` with `PATCH /api/volume/zone/{zone_id}` for zone delta
  - [x] 1.4: Add MAC URL conversion helper (remove colons from MAC for URL path)
  - [x] 1.5: Update response handling to match new API response format

- [x] **Task 2: Verify volume slider rendering** (AC: #1, #3)
  - [x] 2.1: Confirm RangeSlider shows current volume_db from unifiedAudioStore.volumeState.clients
  - [x] 2.2: Confirm zone average volume displays from unifiedAudioStore.volumeState.zones[zoneId].average_volume_db
  - [x] 2.3: Verify slider min/max from settingsStore.volumeLimits (min_db, max_db)
  - [x] 2.4: Test offline client displays "Hors ligne" instead of slider

- [x] **Task 3: Verify mute toggle functionality** (AC: #5)
  - [x] 3.1: Confirm Toggle component shows inverted state (!client.dspMuted = enabled)
  - [x] 3.2: Test zone mute toggles ALL online clients in zone
  - [x] 3.3: Test standalone client mute toggles single client
  - [x] 3.4: Verify visual feedback (muted opacity, dimmed text)

- [x] **Task 4: Verify WebSocket event handling** (AC: #4)
  - [x] 4.1: Confirm `volume_changed` event updates unifiedStore.volumeState
  - [x] 4.2: Test displayVolume computed updates reactively after WebSocket event
  - [x] 4.3: Verify round-trip closure (slider → API → WebSocket → store → UI)
  - [x] 4.4: Test remote volume changes from another client/interface update UI

- [x] **Task 5: Integration test for volume flow** (AC: all)
  - [x] 5.1: Test client volume adjustment end-to-end
  - [x] 5.2: Test zone volume delta applies correctly to all online clients
  - [x] 5.3: Test mute/unmute persists and broadcasts
  - [x] 5.4: Test offline clients excluded from zone operations

## Dev Notes

### CURRENT IMPLEMENTATION STATUS: MOSTLY COMPLETE

**Good news:** The frontend volume controls are already substantially implemented in:
- `frontend/src/components/multiroom/MultiroomControl.vue` (main orchestrator)
- `frontend/src/components/multiroom/MultiroomItem.vue` (individual row with slider + mute)
- `frontend/src/stores/dspStore.js` (API call methods)
- `frontend/src/stores/unifiedAudioStore.js` (volume state)
- `frontend/src/components/ui/RangeSlider.vue` (slider component)

**Primary work needed:** Update API endpoints to use the new MAC-based endpoints from Story 3.4.

---

### Key File Locations and Line References

| Component | File | Key Lines | Purpose |
|-----------|------|-----------|---------|
| Volume slider in zone header | `MultiroomItem.vue` | 78-89 | RangeSlider with throttled input |
| Volume slider for zone clients | `MultiroomItem.vue` | 138-151 | Individual client sliders |
| Mute toggle | `MultiroomItem.vue` | 106-110 | Toggle component |
| Volume API - single client | `dspStore.js` | 362-385 | `updateClientDspVolume()` |
| Volume API - zone delta | `dspStore.js` | 398-416 | `applyZoneDelta()` |
| Mute API | `dspStore.js` | 451-495 | `updateClientDspMute()` |
| Volume state | `unifiedAudioStore.js` | 20-28 | `volumeState` structure |
| WebSocket handler | `MultiroomControl.vue` | 494-496 | `volume_changed` subscription |
| Zone average calc | `MultiroomControl.vue` | 92-110 | `getZoneAverageVolume()` |
| Throttling | `useVolumeThrottle.js` | 31-108 | Throttle presets |

---

### API Endpoint Migration (CRITICAL)

**Current endpoints (to migrate FROM):**
```javascript
// Single client volume
PUT /api/dsp/client/{hostname}/volume
Body: { volume: volumeDb }

// Single client mute
PUT /api/dsp/client/{hostname}/mute
Body: { muted: boolean }

// Zone delta
POST /api/volume/zone/{zoneId}/delta
Body: { delta_db: number }
```

**New endpoints per Story 3.4 (to migrate TO):**
```javascript
// Single client volume (MAC in URL without colons)
PATCH /api/volume/client/mac/{mac_url}
Body: { "volume_db": number }
Response: { "status": "success", "mac_id": "dc:a6:32:7e:d3:43", "volume_db": -25.0 }

// Single client mute (MAC in URL without colons)
PATCH /api/volume/client/mac/{mac_url}/mute
Body: { "mute": boolean }
Response: { "status": "success", "mac_id": "dc:a6:32:7e:d3:43", "mute": true }

// Zone delta
PATCH /api/volume/zone/{zone_id}
Body: { "delta_db": number }
Response: { "status": "success", "zone_id": "uuid-...", "new_average_db": -35.0, "delta_db": 5.0, "applied_to": [...], "offline_clients": [...] }
```

**MAC URL conversion helper:**
```javascript
// Add to dspStore.js
function macToUrlFormat(macId) {
  // Convert "dc:a6:32:7e:d3:43" to "dca6327ed343"
  return macId.replace(/:/g, '');
}
```

---

### Volume State Structure (unifiedAudioStore)

```javascript
// frontend/src/stores/unifiedAudioStore.js:20-28
volumeState: ref({
  mode: 'direct',                  // 'direct' or 'multiroom'
  global_volume_db: -30.0,         // Global volume (average)
  global_mute: false,              // Global mute state
  clients: {                       // Keyed by hostname or mac_id
    "dc:a6:32:7e:d3:43": {
      volume_db: -30.0,
      offset_db: 0,
      mute: false,
      available: true
    }
  },
  zones: {                         // Keyed by zone_id (UUID)
    "uuid-...": {
      id: "uuid-...",
      name: "Salon",
      client_ids: ["mac1", "mac2"],
      average_volume_db: -35.0,    // Pre-calculated by backend
      all_muted: false             // True if ALL clients muted
    }
  },
  step_mobile_db: 3.0              // Volume step for mobile buttons
})
```

---

### Throttling Implementation

**Already implemented in `useVolumeThrottle.js`:**
- Zone header slider: MEDIUM preset (80ms throttle, 300ms final delay)
- Individual client slider: FAST preset (50ms throttle, 150ms final delay)

**Usage in MultiroomItem.vue:228-243:**
```javascript
const { throttledFn: throttledZoneVolume, flush: flushZoneVolume } = useVolumeThrottle(
  (volumeDb) => emit('volume-change', props.client.id, volumeDb),
  'MEDIUM'
);

const { getThrottledFn: getClientThrottledFn } = useVolumeThrottleMap(
  (clientMacId) => (value) => emit('client-volume-change', clientMacId, value),
  'FAST'
);
```

---

### Mute Toggle Logic

**IMPORTANT:** Toggle component uses "enabled" state (inverse of muted).

```javascript
// MultiroomItem.vue:107
<Toggle :model-value="!client.dspMuted" />

// Handler at line 310-315
function handleMuteToggle(enabled) {
  const newMuted = !enabled;  // INVERT: enabled → muted
  emit('mute-toggle', props.client.id, newMuted);
}
```

**Zone mute behavior (MultiroomControl.vue:368-389):**
- Zone mute: Toggles ALL ONLINE clients in zone (parallel API calls)
- Standalone: Toggles single client

---

### Offline Client Handling

**Current implementation (MultiroomItem.vue:152-156):**
- Offline clients show "Hors ligne" text instead of volume slider
- Mute toggle disabled for offline clients
- Offline clients excluded from zone delta calculations

```vue
<template v-if="zoneClient.online">
  <RangeSlider ... />
</template>
<div v-else class="client-offline text-mono">
  Hors ligne
</div>
```

---

### WebSocket Event Flow

**Current subscription (MultiroomControl.vue:494-496):**
```javascript
on('volume', 'volume_changed', (event) => {
  unifiedStore.handleVolumeEvent(event);
});
```

**Expected event format from backend:**
```json
{
  "category": "multiroom",
  "type": "client_state_changed",
  "data": {
    "mac_id": "dc:a6:32:7e:d3:43",
    "client": {
      "mac_id": "dc:a6:32:7e:d3:43",
      "volume_db": -25.0,
      "mute": false
    }
  }
}
```

---

### Project Structure Notes

**Files to modify:**
- `frontend/src/stores/dspStore.js` - Update API endpoint URLs and request bodies

**Files to verify (already implemented correctly):**
- `frontend/src/components/multiroom/MultiroomControl.vue` - Main control
- `frontend/src/components/multiroom/MultiroomItem.vue` - Volume slider UI
- `frontend/src/components/ui/RangeSlider.vue` - Slider component
- `frontend/src/composables/useVolumeThrottle.js` - Throttling
- `frontend/src/stores/unifiedAudioStore.js` - State management
- `frontend/src/services/websocket.js` - Event handling

---

### Dependencies from Story 3.4

**Backend endpoints available:**
- `PATCH /api/volume/client/mac/{mac_url}` - Client volume by MAC
- `PATCH /api/volume/client/mac/{mac_url}/mute` - Client mute by MAC
- `PATCH /api/volume/zone/{zone_id}` - Zone delta
- `GET /api/volume/settings` - Volume settings
- `PATCH /api/volume/settings` - Update volume settings

**MAC format rules:**
| Context | Format | Example |
|---------|--------|---------|
| URL path parameter | No colons | `dca6327ed343` |
| Storage/state | With colons | `dc:a6:32:7e:d3:43` |
| API response body | With colons | `dc:a6:32:7e:d3:43` |
| WebSocket events | With colons | `dc:a6:32:7e:d3:43` |

---

### Volume Limits

From settingsStore via `settingsStore.volumeLimits`:
- `min_db`: Minimum volume (-80 dB typical)
- `max_db`: Maximum volume (0 dB or user-configured limit like -21 dB)
- Default volume: -30 dB (fallback in getters)

**Used in MultiroomItem.vue:250-251:**
```javascript
const sliderMin = computed(() => settingsStore.volumeLimits.min_db);
const sliderMax = computed(() => settingsStore.volumeLimits.max_db);
```

---

### Visual States (CSS)

**Muted state styling (MultiroomItem.vue):**
- `.client-name.muted` - Reduced text contrast
- `.volume-control.muted` - Slider value dimmed
- `.range-slider.muted` - opacity: 0.5

**Offline state styling:**
- `.client-row-name.offline` - Reduced text contrast
- `.client-offline` - Gray background, uppercase "HORS LIGNE"

---

### Previous Story Intelligence

**From Story 3.4:**
- MAC URL conversion: Remove colons for URL path
- Backend validates MAC format (12 hex chars without colons)
- Zone delta only affects ONLINE clients
- Response includes `offline_clients` list (not updated)
- Settings endpoint exists for startup volume config

**Recent commits:**
- `9a31e2f` - fix(volume): sync _local_volume_db in multiroom mode
- `f9967a6` - feat(volume): change default to -60dB and sync volumes on mode switch
- `14c47ed` - refactor(frontend): consolidate Pinia stores and eliminate state duplication

---

### References

- [Source: frontend/src/components/multiroom/MultiroomItem.vue:78-89 - Zone volume slider]
- [Source: frontend/src/components/multiroom/MultiroomItem.vue:138-151 - Client volume sliders]
- [Source: frontend/src/components/multiroom/MultiroomItem.vue:106-110 - Mute toggle]
- [Source: frontend/src/stores/dspStore.js:362-385 - updateClientDspVolume()]
- [Source: frontend/src/stores/dspStore.js:398-416 - applyZoneDelta()]
- [Source: frontend/src/stores/dspStore.js:451-495 - updateClientDspMute()]
- [Source: frontend/src/stores/unifiedAudioStore.js:20-28 - volumeState structure]
- [Source: frontend/src/composables/useVolumeThrottle.js:18-22 - Throttle presets]
- [Source: backend/api/volume.py - Backend volume endpoints]
- [Source: _bmad-output/implementation-artifacts/3-4-api-endpoints-for-volume.md - Story 3.4]
- [Source: _bmad-output/planning-artifacts/epics.md#Story 3.5]

## Technical Requirements

### NFR Compliance

- **NFR1:** Volume changes applied within 100ms - Throttled via useVolumeThrottle, API is async
- **NFR2:** WebSocket updates within 100ms - Event handler in MultiroomControl.vue
- **NFR30:** Updates immediate without polling - WebSocket subscription established

### Architecture Compliance

- **Single Source of Truth:** Backend via unifiedAudioStore.volumeState
- **MAC Address Format:** URL = no colons, state/display = with colons
- **Composition API:** All components use Vue 3 Composition API
- **Pinia Stores:** State in unifiedAudioStore, actions in dspStore

### FR Coverage

- **FR5:** User can adjust volume independently for each client - Client volume slider
- **FR6:** User can adjust zone volume (delta applied to ONLINE clients) - Zone slider with delta

### Testing Standards

- Use Vitest for frontend tests
- Component tests with @vue/test-utils
- Mock API calls with axios mock adapter
- Test WebSocket event handling

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

N/A - all tests passed without debug issues

### Completion Notes List

1. **Task 1 - API Migration Complete:**
   - Added `macToUrlFormat()` helper function to convert MAC addresses to URL format (remove colons)
   - Added `isMacAddress()` helper to detect MAC address format
   - Updated `updateClientDspVolume()` to use `PATCH /api/volume/client/mac/{mac_url}` for MAC addresses
   - Updated `updateClientDspMute()` to use `PATCH /api/volume/client/mac/{mac_url}/mute` for MAC addresses
   - Updated `applyZoneDelta()` to use `PATCH /api/volume/zone/{zone_id}` instead of POST
   - Request body now uses `volume_db` and `mute` keys matching Story 3.4 API spec

2. **Task 2-4 - Verification Complete:**
   - Volume slider rendering verified via component tests
   - Mute toggle functionality verified with inverted state logic
   - WebSocket event handling verified with zone support

3. **Task 5 - Integration Tests:**
   - 50 backend integration tests pass in `test_volume_control.py`
   - 132 frontend tests pass including new tests for dspStore, MultiroomItem, MultiroomControl

### File List

**Modified:**
- `frontend/src/stores/dspStore.js` - Updated volume/mute API endpoints to use MAC-based URLs

**Created (Tests):**
- `frontend/tests/stores/dspStore.test.js` - 16 tests for volume API migration
- `frontend/tests/components/MultiroomItem.test.js` - 9 tests for volume slider and mute toggle
- `frontend/tests/components/MultiroomControl.test.js` - 2 tests for zone mute functionality

**Updated (Tests):**
- `frontend/tests/stores/unifiedAudioStore.test.js` - 4 additional tests for WebSocket zone handling

**Story File:**
- `_bmad-output/implementation-artifacts/3-5-frontend-volume-controls.md` - Updated status to completed

