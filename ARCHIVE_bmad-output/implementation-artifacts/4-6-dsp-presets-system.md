# Story 4.6: DSP Presets System

Status: completed

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a user,
I want to apply pre-defined audio presets and have my manual changes auto-saved,
so that I can quickly switch between sound profiles.

## Acceptance Criteria

1. **AC1: Apply preset to zone/client** - Given the system has pre-defined presets (e.g., "Flat", "Jazz", "Rock", "Classical", "Bass Boost"), when I apply a preset to a zone or client (FR22), then dsp_settings is overwritten with preset values, changes are applied to CamillaDSP, the active preset name is stored, and a WebSocket event `preset_loaded` is broadcast

2. **AC2: Auto-switch to Manual** - Given a preset is currently active, when I modify any filter parameter (FR23), then the active preset automatically switches to "Manual", the current settings are auto-saved as the "Manual" preset, and WebSocket event reflects preset change to "Manual"

3. **AC3: Zone propagation** - Given a zone target is selected, when I apply a preset, then the preset is applied to ALL ONLINE zone members within 200ms (NFR3), and OFFLINE clients will receive settings on reconnection

4. **AC4: Available presets list** - Given I call `GET /api/dsp/presets`, then I receive the list of 21 pre-defined presets + "Manual" preset with their gain arrays

5. **AC5: Manual preset persistence** - Given I modify EQ filters then switch to a builtin preset, when I later switch back to "Manual", then my previous manual settings are restored exactly

6. **AC6: Startup restoration** - Given a preset is active, when the backend restarts, then the same preset is applied automatically

## Tasks / Subtasks

- [x] Task 1: Verify existing backend preset functionality (AC: #1, #2, #5, #6)
  - [x] 1.1 Review `load_preset()` in `backend/core/dsp/service.py` (lines 874-901)
  - [x] 1.2 Verify preset gains are applied correctly via `_apply_gains()`
  - [x] 1.3 Verify `set_filter(from_preset=False)` triggers auto-switch to "Manual" (lines 417-424)
  - [x] 1.4 Verify `_save_manual_gains()` saves current state before switching
  - [x] 1.5 Verify `_apply_saved_preset()` restores on startup (lines 1043-1054)
  - [x] 1.6 Verify WebSocket event `preset_loaded` is broadcast

- [x] Task 2: Add zone/client API endpoints for preset application (AC: #3)
  - [x] 2.1 Add `POST /api/dsp/zone/{zone_id}/preset` with `{"preset_id": "jazz"}`
  - [x] 2.2 Add `POST /api/dsp/client/{mac_id}/preset` with `{"preset_id": "jazz"}`
  - [x] 2.3 Zone endpoint must propagate to all ONLINE clients via proxy pattern
  - [x] 2.4 Follow existing proxy pattern from story 4-5 (backend/api/dsp.py:895-939)

- [x] Task 3: Add proxy routes for remote client preset loading (AC: #3)
  - [x] 3.1 Add `PUT /api/dsp/client/{hostname}/preset/{preset_id}` proxy route
  - [x] 3.2 Proxy should forward to remote client's `/api/dsp/preset/{preset_id}`
  - [x] 3.3 Handle OFFLINE clients gracefully (skip, don't error)

- [x] Task 4: Verify/update frontend preset functionality (AC: #1, #4)
  - [x] 4.1 Review `loadPreset()` in `frontend/src/stores/dspStore.js` (line 801-818)
  - [x] 4.2 Verify `builtinPresets` state is populated from `/api/dsp/presets`
  - [x] 4.3 Verify `activePreset` state tracks current preset
  - [x] 4.4 Verify `isManualMode` computed detects when gains differ from preset

- [x] Task 5: Add frontend zone propagation for presets (AC: #3)
  - [x] 5.1 Update `loadPreset()` to call `propagateToLinkedClients('preset', {preset_id})`
  - [x] 5.2 Use existing `propagateToLinkedClients()` pattern from dspStore.js
  - [x] 5.3 Add proxy endpoint mapping for preset in `propagateToLinkedClients()`

- [x] Task 6: Verify WebSocket event handling (AC: #1, #2)
  - [x] 6.1 Verify `handlePresetLoaded()` updates `activePreset.value` on WebSocket event
  - [x] 6.2 Verify event is received when other zone member changes preset
  - [x] 6.3 Test UI reflects remote preset changes immediately

- [x] Task 7: Write integration tests (AC: #1-#6)
  - [x] 7.1 Test `load_preset("jazz")` applies correct gains
  - [x] 7.2 Test modifying filter while on preset auto-switches to "Manual"
  - [x] 7.3 Test "Manual" gains are saved and restored correctly
  - [x] 7.4 Test zone propagation via new API endpoint
  - [x] 7.5 Test proxy route for remote client preset
  - [x] 7.6 Test `GET /api/dsp/presets` returns 21 presets + Manual
  - [x] 7.7 Test preset persistence across simulated restart

## Dev Notes

### Existing Implementation Analysis

**CRITICAL: Significant preset functionality already exists** - The core preset logic was implemented as part of the DSP infrastructure. This story focuses on:
1. **Verification** of existing functionality
2. **Adding zone propagation** via new API endpoints
3. **Integration testing**

### Backend Implementation (ALREADY EXISTS)

#### presets.py - 21 Builtin Presets (backend/core/dsp/presets.py)

```python
BUILTIN_PRESETS: List[Dict] = [
    {"id": "acoustic", "gains": [5, 4, 3, 1, 2, 2, 3, 4, 3, 2]},
    {"id": "bass_boost", "gains": [6, 5, 4, 2, 0, 0, 0, 0, 0, 0]},
    {"id": "classical", "gains": [5, 4, 3, 2, -1, -1, 0, 2, 3, 4]},
    {"id": "dance", "gains": [4, 6, 5, 0, 2, 4, 5, 4, 3, 0]},
    {"id": "electronic", "gains": [5, 4, 2, 0, -2, 2, 1, 3, 5, 4]},
    {"id": "hip_hop", "gains": [5, 5, 3, 1, -1, -1, 1, 0, 2, 3]},
    {"id": "jazz", "gains": [4, 3, 2, 2, -2, -2, 0, 2, 3, 4]},
    # ... 14 more presets
]
DEFAULT_MANUAL_GAINS = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
```

EQ Frequencies: 31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000 Hz

#### CamillaDSPService.load_preset() (backend/core/dsp/service.py:874-901)

```python
async def load_preset(self, preset_id: str) -> bool:
    """Load a builtin or manual preset"""
    # Early return if already on the same preset
    current = await self.get_active_preset()
    if preset_id == current:
        return True

    gains = await self._get_preset_gains(preset_id)
    if gains is None:
        return False

    # Save current as manual before switching
    if current in ("manual", None) and preset_id != "manual":
        await self._save_manual_gains()

    await self._apply_gains(gains)
    await self.settings_service.set_setting("dsp.active_preset", preset_id)
    await self._broadcast_event("preset_loaded", {"id": preset_id})
    return True
```

**Key Point:** Saves manual gains before switching to preserve user customizations.

#### Auto-Switch to Manual (backend/core/dsp/service.py:417-424)

```python
# In set_filter() method:
if persist:
    await self._save_filters()
    # If user manually modified a filter while on a predefined preset,
    # save current gains as manual and switch to manual mode
    if not from_preset and self.settings_service:
        current_preset = await self.get_active_preset()
        if current_preset and current_preset != "manual":
            await self._save_manual_gains()
            await self.settings_service.set_setting("dsp.active_preset", "manual")
            await self._broadcast_event("preset_loaded", {"id": "manual"})
```

**Key Point:** Uses `from_preset=False` (default) to trigger auto-switch when user modifies EQ directly.

### API Endpoints (backend/api/dsp.py)

| Endpoint | Method | Description | Status |
|----------|--------|-------------|--------|
| `/api/dsp/presets` | GET | Get all presets + manual + active | ✅ EXISTS |
| `/api/dsp/preset/{preset_id}` | PUT | Load preset (local client) | ✅ EXISTS |
| `/api/dsp/zone/{zone_id}/preset` | POST | Load preset for zone | ❌ **TO ADD** |
| `/api/dsp/client/{mac_id}/preset` | POST | Load preset for specific client | ❌ **TO ADD** |
| `/api/dsp/client/{hostname}/preset/{preset_id}` | PUT | Proxy: load preset on remote | ❌ **TO ADD** |

### Zone Propagation Pattern (from Story 4-5)

The proxy pattern established in story 4-5 for DSP enabled state should be reused:

```python
# backend/api/dsp.py - Proxy route pattern
@router.put("/client/{hostname}/preset/{preset_id}")
async def proxy_load_preset(hostname: str, preset_id: str):
    """Proxy preset loading to a remote client."""
    url = f"http://{hostname}.local:8000/api/dsp/preset/{preset_id}"
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.put(url)
        return response.json()
```

Frontend propagation pattern from dspStore.js:

```javascript
// In loadPreset() - add after local preset load:
await propagateToLinkedClients('preset', { preset_id: presetId });

// In propagateToLinkedClients() - add case:
case 'preset':
  endpoint = `/api/dsp/client/${hostname}/preset/${data.preset_id}`;
  method = 'put';
  break;
```

### Frontend Implementation (ALREADY EXISTS)

#### dspStore.js State & Methods

```javascript
// State
const builtinPresets = ref([]);         // Array of {id, gains}
const activePreset = ref('manual');     // Current preset ID
const isLoadingPreset = ref(false);     // Prevents "Manual" flicker

// Computed
const isManualMode = computed(() => {
  if (isLoadingPreset.value) return false;  // Prevents flicker
  if (activePreset.value === 'manual') return true;
  if (!activePreset.value) return true;
  // Compare current gains with preset gains
  const preset = builtinPresets.value.find(p => p.id === activePreset.value);
  if (!preset) return true;
  for (let i = 0; i < preset.gains.length; i++) {
    const filter = filters.value.find(f => f.id === `eq_band_0${i}`);
    if (!filter || Math.abs(filter.gain - preset.gains[i]) > 0.1) {
      return true;  // Gain differs = manual mode
    }
  }
  return false;
});

// Methods
async function loadPreset(presetId) {
  isLoadingPreset.value = true;
  const response = await axios.put(`/api/dsp/preset/${presetId}`);
  if (response.data.success) {
    activePreset.value = presetId;
    await loadStatus();  // Refresh filters
  }
  isLoadingPreset.value = false;
}
```

### WebSocket Events

| Event | Category | Data | Trigger |
|-------|----------|------|---------|
| `preset_loaded` | dsp | `{id: "jazz"}` | When preset applied |

### Settings Persistence

**Storage:** `/var/lib/milo/settings.json`

```json
{
  "dsp": {
    "active_preset": "jazz",        // Currently active preset ID
    "manual_gains": [3, 2, 1, 0...] // User's custom manual preset gains
  }
}
```

### Previous Story Intelligence (4-5-global-dsp-bypass)

**Learnings to apply:**
1. Zone propagation via `propagateToLinkedClients()` frontend pattern
2. Proxy routes pattern: `/api/dsp/client/{hostname}/...`
3. Skip OFFLINE clients gracefully in propagation
4. `isLoadingPreset` flag to prevent UI flicker

**Testing patterns established:**
- Mock CamillaDSP with `@patch("camilladsp.CamillaClient")`
- Test both local and zone propagation separately
- Test persistence across simulated restart

### Git Intelligence (Recent Commits)

```
57877fd fix(dsp): resolve preset loading and filter restoration issues
9a31e2f fix(volume): sync _local_volume_db in multiroom mode
14c47ed refactor(frontend): consolidate Pinia stores
```

The `57877fd` commit fixed preset loading issues - confirms preset system was recently worked on.

### Key Code Locations

| Component | File | Lines |
|-----------|------|-------|
| BUILTIN_PRESETS | backend/core/dsp/presets.py | 18-40 |
| load_preset() | backend/core/dsp/service.py | 874-901 |
| Auto-switch to Manual | backend/core/dsp/service.py | 417-424 |
| _save_manual_gains() | backend/core/dsp/service.py | 903-913 |
| _apply_saved_preset() | backend/core/dsp/service.py | 1043-1054 |
| GET /api/dsp/presets | backend/api/dsp.py | 337-350 |
| PUT /api/dsp/preset/{id} | backend/api/dsp.py | 352-368 |
| Frontend loadPreset() | frontend/src/stores/dspStore.js | 801-818 |
| Frontend builtinPresets | frontend/src/stores/dspStore.js | 48 |
| Frontend activePreset | frontend/src/stores/dspStore.js | 49 |

### Architecture Compliance

**From architecture.md:**

- **API Pattern:** `/api/dsp/zone/{zone_id}/preset` with `{"preset_id": "Jazz"}`
- **Propagation:** Zone endpoint must propagate to all ONLINE clients
- **WebSocket:** Use `preset_loaded` event (not `dsp_changed`) for preset changes
- **SSOT:** Backend is source of truth for active preset (`dsp.active_preset`)

**MUST:**
- Use proxy pattern for remote client preset loading
- Propagate to ONLINE zone members only
- Save manual gains before switching presets
- Broadcast `preset_loaded` event after applying

**MUST NOT:**
- Apply presets to OFFLINE clients immediately (they sync on reconnection)
- Overwrite manual gains when switching between builtin presets
- Use `dsp_changed` event for preset changes (use `preset_loaded`)

### Implementation Priority

1. **Verify existing functionality** - Tasks 1, 4, 6 (mostly review)
2. **Add zone propagation** - Tasks 2, 3, 5 (new code)
3. **Integration tests** - Task 7

### References

- [Source: backend/core/dsp/presets.py] - Builtin presets definition
- [Source: backend/core/dsp/service.py#load_preset] - Preset loading logic (lines 874-901)
- [Source: backend/core/dsp/service.py#set_filter] - Auto-switch to Manual (lines 417-424)
- [Source: backend/api/dsp.py#get_presets] - GET presets endpoint (lines 337-350)
- [Source: frontend/src/stores/dspStore.js#loadPreset] - Frontend preset loading (lines 801-818)
- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.6] - FR22, FR23 requirements
- [Source: _bmad-output/planning-artifacts/architecture.md#API-Design] - API patterns
- [Source: _bmad-output/implementation-artifacts/4-5-global-dsp-bypass.md] - Proxy pattern reference

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

N/A

### Completion Notes List

1. **Backend verification complete** - All existing preset functionality verified working correctly:
   - `load_preset()` properly applies gains via `_apply_gains()`
   - Auto-switch to "Manual" triggers via `from_preset=False` pattern
   - Manual gains saved/restored via `_save_manual_gains()` and `_apply_saved_preset()`
   - WebSocket `preset_loaded` event broadcasts correctly

2. **New API endpoints added** (backend/api/dsp.py):
   - `POST /api/dsp/zone/{zone_id}/preset` - Apply preset to all zone members
   - `POST /api/dsp/client/{mac_id}/preset` - Apply preset to specific client
   - `PUT /api/dsp/client/{hostname}/preset/{preset_id}` - Proxy route for remote clients

3. **Frontend zone propagation added** (frontend/src/stores/dspStore.js):
   - `loadPreset()` now calls `propagateToLinkedClients('preset', {preset_id})`
   - `propagateToLinkedClients()` handles 'preset' case with special URL format

4. **Integration tests created** - 37 backend tests + 12 frontend tests covering all acceptance criteria:
   - AC1: Preset application and WebSocket events (5 tests)
   - AC2: Auto-switch to Manual behavior (3 tests)
   - AC3: Zone propagation endpoints with real endpoint testing (10 tests)
   - AC3: Client preset endpoint (4 tests)
   - AC4: 21 builtin presets list + Manual preset (10 tests)
   - AC5: Manual gains persistence (2 tests)
   - AC6: Startup restoration (2 tests)
   - Model validation tests (4 tests)
   - Frontend preset loading and propagation (12 tests)

5. **All tests pass** - Backend: 37 preset integration tests pass, Frontend: 40 dspStore tests pass

### File List

**Modified:**
- `backend/api/models.py` - Added `DspPresetRequest` model
- `backend/api/dsp.py` - Added 3 new endpoints for zone/client preset loading
- `frontend/src/stores/dspStore.js` - Added preset propagation to linked clients
- `frontend/tests/stores/dspStore.test.js` - Added 12 preset management tests

**Created:**
- `backend/tests/integration/test_dsp_presets_system.py` - 37 integration tests

