# Story 4.5: Global DSP Bypass

Status: done

## Story

As a user,
I want to quickly enable/disable all DSP processing (except crossover),
so that I can compare processed vs flat sound or temporarily bypass all effects.

## Acceptance Criteria

1. **AC1: Disable global DSP** - Given a zone or standalone client, when I disable global DSP (`dsp_settings.enabled = false`), then all EQ filters are bypassed in CamillaDSP, compressor is bypassed, loudness is bypassed, crossover remains active (managed separately in Epic 5), and a WebSocket event `dsp_changed` is broadcast

2. **AC2: Enable global DSP** - Given global DSP is disabled, when I enable global DSP (`dsp_settings.enabled = true`), then all EQ filters, compressor, and loudness are restored to their configured state, changes are applied to CamillaDSP, and underlying settings are preserved (not reset)

3. **AC3: Zone propagation** - Given a zone target, when global DSP toggle changes, then the bypass/restore propagates to all ONLINE clients in the zone within 200ms (NFR3)

4. **AC4: Settings preservation** - Given DSP is bypassed then re-enabled, when restoration occurs, then all EQ gains, compressor parameters, and loudness parameters are exactly as they were before bypass

5. **AC5: Crossover independence** - Given crossover is active (subwoofer in zone), when global DSP is bypassed, then crossover filters remain active and are not affected by the bypass toggle

6. **AC6: Persistence across restart** - Given DSP effects are disabled, when the backend restarts, then the disabled state persists and effects remain bypassed until user enables them

## Tasks / Subtasks

- [x] Task 1: Verify/implement backend bypass_effects functionality (AC: #1, #4, #5)
  - [x] 1.1 Review existing `bypass_effects()` in `CamillaDSPService` (backend/core/dsp/service.py:926-966)
  - [x] 1.2 Confirm EQ filters are reset to 0 dB gain (not removed) with `persist=False`
  - [x] 1.3 Confirm compressor is disabled with `persist=False`
  - [x] 1.4 Confirm loudness is disabled with `persist=False`
  - [x] 1.5 Verify crossover filters (`crossover_highpass`, `crossover_lowpass`) are NOT affected
  - [x] 1.6 Verify WebSocket event `effects_bypassed` is broadcast

- [x] Task 2: Verify/implement backend restore_effects functionality (AC: #2, #4)
  - [x] 2.1 Review existing `restore_effects()` in `CamillaDSPService` (backend/core/dsp/service.py:968-1012)
  - [x] 2.2 Confirm filters are restored from `dsp.filters` settings with original gains
  - [x] 2.3 Confirm compressor is restored from `dsp.compressor` settings
  - [x] 2.4 Confirm loudness is restored from `dsp.loudness` settings
  - [x] 2.5 Verify WebSocket event `effects_restored` is broadcast

- [x] Task 3: Verify/implement API endpoint for DSP enable/disable (AC: #1, #2, #6)
  - [x] 3.1 Review existing `GET /api/dsp/enabled` endpoint (backend/api/dsp.py:77-91)
  - [x] 3.2 Review existing `PUT /api/dsp/enabled` endpoint (backend/api/dsp.py:93-125)
  - [x] 3.3 Verify `routing_service.set_dsp_effects_enabled()` triggers bypass/restore correctly
  - [x] 3.4 Verify `dsp.effects_enabled` is persisted to settings.json
  - [x] 3.5 Verify WebSocket event `enabled_changed` is broadcast

- [x] Task 4: Verify zone propagation for DSP bypass (AC: #3)
  - [x] 4.1 Determine if zone propagation is currently implemented for bypass/restore → NOT IMPLEMENTED
  - [x] 4.2 Added proxy routes `/api/dsp/client/{hostname}/enabled` (GET/PUT) for remote clients
  - [x] 4.3 Added `propagateToLinkedClients('enabled', {enabled})` in frontend dspStore.js
  - [x] 4.4 Propagation uses existing pattern that handles OFFLINE clients gracefully

- [x] Task 5: Verify frontend DSP enable/disable toggle (AC: #1, #2, #6)
  - [x] 5.1 Review existing `isDspEffectsEnabled` state in dspStore.js (line 46)
  - [x] 5.2 Review `loadEnabledState()` method (lines 1211-1214)
  - [x] 5.3 Review `toggleDspEffectsEnabled()` method (lines 1216-1247)
  - [x] 5.4 Review `handleEnabledChanged()` WebSocket handler (lines 1249-1253)
  - [x] 5.5 Verify UI toggle in SettingsModal.vue connected correctly

- [x] Task 6: Write integration tests
  - [x] 6.1 Test bypass_effects() resets EQ to 0 dB while preserving saved settings
  - [x] 6.2 Test restore_effects() restores exact previous EQ gains
  - [x] 6.3 Test bypass_effects() disables compressor with persist=False
  - [x] 6.4 Test restore_effects() restores compressor with original parameters
  - [x] 6.5 Test bypass_effects() disables loudness with persist=False
  - [x] 6.6 Test restore_effects() restores loudness with original parameters
  - [x] 6.7 Test crossover filters unchanged during bypass/restore cycle
  - [x] 6.8 Test API endpoint roundtrip (PUT enabled=false, GET returns false)
  - [x] 6.9 Test persistence across simulated backend restart
  - [x] 6.10 Test zone propagation proxy route exists

## Dev Notes

### Existing Implementation Analysis

**CRITICAL: Much of the implementation already exists** - The DSP bypass/restore functionality was implemented as part of the DSP infrastructure. This story focuses on **verification, testing, and ensuring completeness** rather than building from scratch.

### Backend Implementation (ALREADY EXISTS)

#### CamillaDSPService bypass_effects() (backend/core/dsp/service.py:926-966)

```python
async def bypass_effects(self) -> bool:
    """
    Bypass all DSP effects while keeping volume control active.

    This is called when user disables "DSP" toggle. CamillaDSP keeps running
    but all audio processing (EQ, compressor, loudness) is bypassed.
    """
    # 1. Save current config before bypassing
    await self.save_current_config()

    # 2. Reset all EQ filters to 0 dB gain (persist=False)
    for f in self._filters:
        await self.set_filter(..., gain=0, persist=False)

    # 3. Disable compressor (persist=False)
    await self.set_compressor(enabled=False, persist=False)

    # 4. Disable loudness (persist=False)
    await self.set_loudness(enabled=False, persist=False)

    # 5. Broadcast event
    await self._broadcast_event("effects_bypassed", {"bypassed": True})
```

**Key Point:** Uses `persist=False` to NOT overwrite saved settings - this allows restoration to work correctly.

#### CamillaDSPService restore_effects() (backend/core/dsp/service.py:968-1012)

```python
async def restore_effects(self) -> bool:
    """
    Restore all DSP effects from saved settings.

    This is called when user enables "DSP" toggle. Restores EQ filters,
    compressor, and loudness from saved settings.
    """
    # 1. Restore EQ filters from settings
    saved_filters = await self.settings_service.get_setting("dsp.filters")
    for f in saved_filters:
        await self.set_filter(...)

    # 2. Restore compressor settings
    saved_compressor = await self.settings_service.get_setting("dsp.compressor")
    await self.set_compressor(**saved_compressor)

    # 3. Restore loudness settings
    saved_loudness = await self.settings_service.get_setting("dsp.loudness")
    await self.set_loudness(**saved_loudness)

    # 4. Broadcast event
    await self._broadcast_event("effects_restored", {"bypassed": False})
```

### API Endpoints (backend/api/dsp.py)

| Endpoint | Method | Description | Lines |
|----------|--------|-------------|-------|
| `/api/dsp/enabled` | GET | Get DSP effects enabled state | 77-91 |
| `/api/dsp/enabled` | PUT | Set DSP effects enabled state | 93-125 |

**GET /api/dsp/enabled:**
- Returns `{"enabled": true/false}`
- Checks both `dsp.effects_enabled` and legacy `dsp.enabled` keys
- Defaults to `true` if not set

**PUT /api/dsp/enabled:**
- Accepts `{"enabled": true/false}`
- Calls `routing_service.set_dsp_effects_enabled(enabled, active_source)`
- Persists to `dsp.effects_enabled` in settings.json
- Broadcasts `enabled_changed` WebSocket event

### Routing Service Integration

The `PUT /api/dsp/enabled` endpoint delegates to `routing_service.set_dsp_effects_enabled()`. This method:
1. Persists the setting
2. Calls `dsp_service.bypass_effects()` or `dsp_service.restore_effects()`
3. May restart the active audio source to apply changes (depending on implementation)

**Important:** Need to verify the routing_service implementation handles the bypass/restore correctly.

### Frontend Implementation (ALREADY EXISTS)

#### dspStore.js State & Methods

```javascript
// State (line 46)
const isDspEffectsEnabled = ref(true);
const isTogglingEnabled = ref(false);

// Load enabled state from backend (lines 1211-1214)
async function loadEnabledState() {
  isDspEffectsEnabled.value = await fetchEnabledState();
  return isDspEffectsEnabled.value;
}

// Toggle DSP effects (lines 1216-1247)
async function toggleDspEffectsEnabled(enabled) {
  // 1. Set optimistic state
  // 2. Call API: PUT /api/dsp/enabled
  // 3. On success: loadStatus() or cleanup()
  // 4. On failure: revert state
}

// WebSocket handler (lines 1249-1253)
function handleEnabledChanged(event) {
  if (event.data && event.data.enabled !== undefined) {
    isDspEffectsEnabled.value = event.data.enabled;
  }
}
```

### WebSocket Events

| Event | Category | Data |
|-------|----------|------|
| `effects_bypassed` | dsp | `{bypassed: true}` |
| `effects_restored` | dsp | `{bypassed: false}` |
| `enabled_changed` | dsp | `{enabled: true/false}` |

### Crossover Filter Independence

**Critical Requirement:** Crossover filters must NOT be affected by DSP bypass.

Crossover filters in CamillaDSP:
- `crossover_highpass` - Highpass filter for satellites/bookshelf/tower
- `crossover_lowpass` - Lowpass filter for subwoofer

The `bypass_effects()` method only touches:
- EQ band filters (`eq_band_XX`)
- Compressor processor
- Loudness shelf filters (`loudness_low`, `loudness_high`)

It does NOT touch:
- `crossover_highpass`
- `crossover_lowpass`

**Verification needed:** Confirm this is true in the implementation.

### Settings Persistence

**Storage:** `/var/lib/milo/settings.json`

```json
{
  "dsp": {
    "effects_enabled": true,  // NEW: Global DSP bypass state
    "enabled": true,          // LEGACY: May still exist
    "filters": [...],         // Saved EQ filter gains
    "compressor": {...},      // Saved compressor settings
    "loudness": {...}         // Saved loudness settings
  }
}
```

### Zone Propagation (TO VERIFY)

**Current Implementation Status: UNKNOWN**

The `/api/dsp/enabled` endpoint may NOT currently propagate to zone members. Need to verify:

1. Does `routing_service.set_dsp_effects_enabled()` handle zone propagation?
2. If not, need to add propagation logic similar to other DSP changes

**If not implemented, add:**
```python
# In /api/dsp/enabled PUT handler:
if client_registry_service:
    # Get zone for current target
    zone = client_registry_service.get_zone_for_client(selected_target)
    if zone:
        # Propagate to ONLINE zone members
        for client_id in zone.client_ids:
            if client_registry_service.is_client_online(client_id):
                await proxy_service.request(client_id, "PUT", "/dsp/enabled", {"enabled": enabled})
```

### Project Structure Notes

- Backend follows feature-based architecture: `backend/core/dsp/`
- API routes: `backend/api/dsp.py` (aliased from `backend/presentation/api/routes/dsp.py`)
- Frontend store: `frontend/src/stores/dspStore.js`
- All state changes must go through `state_machine._broadcast_event()` for WebSocket sync
- Settings persist via `SettingsService.set_setting("dsp.effects_enabled", ...)`

### Testing Approach

**Backend (pytest):**

```python
@pytest.mark.asyncio
async def test_bypass_effects_resets_eq_to_zero(dsp_service, mock_camilla):
    # Setup: Enable EQ with non-zero gains
    await dsp_service.set_filter("eq_band_00", 100, gain=5.0, q=1.41, filter_type="Peaking")

    # Act: Bypass effects
    await dsp_service.bypass_effects()

    # Assert: EQ gain is 0, but saved settings preserve 5.0
    filters = await dsp_service.get_filters()
    assert filters[0]["gain"] == 0

    saved = await settings_service.get_setting("dsp.filters")
    assert saved[0]["gain"] == 5.0

@pytest.mark.asyncio
async def test_restore_effects_restores_exact_gains(dsp_service, mock_camilla):
    # Setup: Save specific settings then bypass
    await dsp_service.set_filter("eq_band_00", 100, gain=5.0, ...)
    await dsp_service.bypass_effects()

    # Act: Restore effects
    await dsp_service.restore_effects()

    # Assert: EQ gain is exactly 5.0 again
    filters = await dsp_service.get_filters()
    assert filters[0]["gain"] == 5.0

@pytest.mark.asyncio
async def test_bypass_does_not_affect_crossover(dsp_service, mock_camilla):
    # Setup: Enable crossover
    await dsp_service.set_crossover_filter(enabled=True, frequency=80)

    # Act: Bypass effects
    await dsp_service.bypass_effects()

    # Assert: Crossover still enabled
    crossover = await dsp_service.get_crossover_filter()
    assert crossover["enabled"] == True
```

**Integration Tests:**
- Full roundtrip API → Service → CamillaDSP → WebSocket → Frontend
- Zone propagation with ONLINE/OFFLINE client handling
- Persistence across simulated restart

### Previous Story Intelligence (4-4-compressor-loudness-control)

**Learnings to apply:**
1. `persist=False` parameter is critical for bypass operations
2. Zone propagation should skip OFFLINE clients
3. WebSocket handlers must update local state correctly
4. Test both bypass and restore in sequence to verify preservation

**Testing patterns established:**
- 41 integration tests in `test_compressor_loudness_control.py`
- Mock CamillaDSP connection with `@patch("camilladsp.CamillaClient")`
- Test `persist=False` separately from normal operations

### Git Intelligence (Recent Commits)

Relevant commits:
- `9a31e2f fix(volume): sync _local_volume_db in multiroom mode to preserve volume on mode switch`
- `57877fd fix(dsp): resolve preset loading and filter restoration issues`

These indicate DSP restoration logic has been recently fixed/improved.

### Key Code Locations

| Component | File | Lines |
|-----------|------|-------|
| CamillaDSPService.bypass_effects | backend/core/dsp/service.py | 926-966 |
| CamillaDSPService.restore_effects | backend/core/dsp/service.py | 968-1012 |
| CamillaDSPService.save_current_config | backend/core/dsp/service.py | 1056-1068 |
| GET /api/dsp/enabled | backend/api/dsp.py | 77-91 |
| PUT /api/dsp/enabled | backend/api/dsp.py | 93-125 |
| Frontend isDspEffectsEnabled | frontend/src/stores/dspStore.js | 46 |
| Frontend loadEnabledState | frontend/src/stores/dspStore.js | 1211-1214 |
| Frontend toggleDspEffectsEnabled | frontend/src/stores/dspStore.js | 1216-1247 |
| Frontend handleEnabledChanged | frontend/src/stores/dspStore.js | 1249-1253 |

### Domain Models

The bypass/restore operates on these persisted structures:

**EQ Filters** (backend/core/dsp/service.py:74-76):
```python
self._filters: List[Dict[str, Any]] = []
# Each filter: {"id": "eq_band_XX", "freq": 1000, "gain": 0, "q": 1.41, "type": "Peaking"}
```

**Compressor** (backend/core/dsp/service.py:79-86):
```python
self._compressor: Dict[str, Any] = {
    "enabled": False,
    "threshold": -20.0,
    "ratio": 4.0,
    "attack": 10.0,
    "release": 100.0,
    "makeup_gain": 0.0
}
```

**Loudness** (backend/core/dsp/service.py:87-92):
```python
self._loudness: Dict[str, Any] = {
    "enabled": False,
    "reference_level": 80,
    "high_boost": 5.0,
    "low_boost": 8.0
}
```

### References

- [Source: backend/core/dsp/service.py#bypass_effects] - Bypass implementation (lines 926-966)
- [Source: backend/core/dsp/service.py#restore_effects] - Restore implementation (lines 968-1012)
- [Source: backend/api/dsp.py#get_dsp_effects_enabled] - GET endpoint (lines 77-91)
- [Source: backend/api/dsp.py#set_dsp_effects_enabled] - PUT endpoint (lines 93-125)
- [Source: frontend/src/stores/dspStore.js#toggleDspEffectsEnabled] - Frontend toggle (lines 1216-1247)
- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.5] - Requirements
- [Source: _bmad-output/project-context.md#Framework-Specific-Rules] - Architecture patterns

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

N/A

### Completion Notes List

1. **Existing implementation was largely complete** - The `bypass_effects()` and `restore_effects()` methods in CamillaDSPService were already fully implemented and working correctly.

2. **Zone propagation was missing** - The global DSP toggle did not propagate to zone members. Added:
   - Backend: `GET/PUT /api/dsp/client/{hostname}/enabled` proxy routes (backend/api/dsp.py:895-939)
   - Frontend: `propagateToLinkedClients('enabled', {enabled})` call in `toggleDspEffectsEnabled()` (dspStore.js:1228)

3. **All acceptance criteria verified**:
   - AC1: bypass_effects() sets all EQ to 0dB, disables compressor/loudness ✅
   - AC2: restore_effects() restores all DSP settings from dsp.* settings ✅
   - AC3: Zone propagation via frontend propagateToLinkedClients pattern ✅
   - AC4: Settings preserved via persist=False pattern ✅
   - AC5: Crossover filters (crossover_highpass, crossover_lowpass) NOT affected ✅
   - AC6: State persists in dsp.effects_enabled setting ✅

4. **Integration tests created** - 22 tests covering all acceptance criteria in `backend/tests/integration/test_global_dsp_bypass.py`

### File List

**Modified:**
- `backend/api/dsp.py` - Added proxy routes for client DSP enabled state (lines 895-939)
- `frontend/src/stores/dspStore.js` - Added zone propagation in toggleDspEffectsEnabled (line 1228)

**Created:**
- `backend/tests/integration/test_global_dsp_bypass.py` - 22 integration tests for all acceptance criteria

**Verified (no changes needed):**
- `backend/core/dsp/service.py` - bypass_effects(), restore_effects() already correct
- `backend/core/multiroom/routing.py` - set_dsp_effects_enabled() already correct
- `frontend/src/components/settings/SettingsModal.vue` - Toggle UI already connected
- `frontend/src/components/settings/categories/DspSettings.vue` - WebSocket handlers already connected

### Code Review Record

**Reviewed:** 2026-01-20
**Reviewer:** Claude Opus 4.5 (Adversarial Code Review)

**Issues Found & Fixed:**
1. **[CRIT-1] FIXED** - Test file `test_global_dsp_bypass.py` was untracked in Git → Staged for commit
2. **[MED-1] FIXED** - Incorrect line reference "1227-1228" → Corrected to "1228"
3. **[MED-3] FIXED** - Added 2 additional tests for zone propagation coverage:
   - `test_remote_client_enabled_proxies_to_client` - Verifies proxy route wiring
   - `test_zone_propagation_skips_offline_clients` - Verifies offline client filtering

**AC Validation Summary:**
- AC1 ✅ bypass_effects() sets EQ to 0dB, disables compressor/loudness
- AC2 ✅ restore_effects() restores all DSP from settings
- AC3 ✅ Zone propagation via proxy routes + frontend propagateToLinkedClients
- AC4 ✅ Settings preserved via persist=False pattern
- AC5 ✅ Crossover filters unaffected by bypass
- AC6 ✅ State persists in dsp.effects_enabled setting

