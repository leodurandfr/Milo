# Story 4.4: Compressor & Loudness Control

Status: done

## Story

As a user,
I want to enable and configure compressor and loudness compensation,
so that I can have consistent volume levels and enhanced low-volume listening.

## Acceptance Criteria

1. **AC1: Compressor enable/disable** - Given a zone or standalone client, when I enable/disable the compressor, then compressor.enabled is toggled in dsp_settings, changes are applied to CamillaDSP, and a WebSocket event `compressor_changed` is broadcast

2. **AC2: Compressor parameter adjustment** - Given compressor is enabled, when I adjust parameters (threshold, ratio, attack, release), then parameters are validated, applied to CamillaDSP within 200ms (NFR3), persisted, and broadcast

3. **AC3: Loudness enable/disable** - Given a zone or standalone client, when I enable/disable loudness compensation, then loudness.enabled is toggled in dsp_settings, and changes are applied to CamillaDSP

4. **AC4: Loudness reference_level adjustment** - Given loudness is enabled, when I adjust reference_level, then the parameter is validated, applied, persisted, and a WebSocket event `loudness_changed` is broadcast

5. **AC5: Zone propagation** - Given a zone target, when compressor or loudness settings change, then changes propagate to all ONLINE clients in the zone within 200ms (NFR3)

6. **AC6: Preset auto-switch on manual modification** - Given a user on a predefined preset, when they manually modify compressor or loudness, then the system should switch to "Manual" preset (FR23 behavior, consistent with EQ filter changes)

## Tasks / Subtasks

- [x] Task 1: Verify compressor enable/disable functionality (AC: #1)
  - [x] 1.1 Confirm `CamillaDSPService.set_compressor(enabled=True/False)` toggles compressor in CamillaDSP pipeline
  - [x] 1.2 Verify WebSocket event `compressor_changed` broadcasts with complete settings
  - [x] 1.3 Confirm compressor state persists to `dsp.compressor` in settings.json
  - [x] 1.4 Verify frontend `dspStore.updateCompressor()` sends PUT request and updates local state

- [x] Task 2: Verify compressor parameter validation and application (AC: #2)
  - [x] 2.1 Confirm parameter ranges: threshold (-60 to 0 dB), ratio (1 to 20), attack (0.1 to 100 ms), release (10 to 1000 ms), makeup_gain (0 to 30 dB)
  - [x] 2.2 Verify attack/release conversion from ms to seconds for CamillaDSP API (`attack/1000.0`)
  - [x] 2.3 Confirm partial updates work (only changed parameters in request)
  - [x] 2.4 Verify parameters applied within 200ms latency requirement

- [x] Task 3: Verify loudness enable/disable functionality (AC: #3)
  - [x] 3.1 Confirm `CamillaDSPService.set_loudness(enabled=True/False)` adds/removes shelf filters
  - [x] 3.2 Verify `loudness_low` (Lowshelf 100Hz) and `loudness_high` (Highshelf 8000Hz) filters created/removed
  - [x] 3.3 Confirm loudness state persists to `dsp.loudness` in settings.json
  - [x] 3.4 Verify frontend `dspStore.updateLoudness()` sends PUT request and updates local state

- [x] Task 4: Verify loudness parameter adjustment (AC: #4)
  - [x] 4.1 Confirm parameter ranges: reference_level (60 to 100 SPL), high_boost (0 to 15 dB), low_boost (0 to 15 dB)
  - [x] 4.2 Verify WebSocket event `loudness_changed` broadcasts with complete settings
  - [x] 4.3 Confirm partial updates work (only changed parameters in request)
  - [x] 4.4 Verify shelf filter gains updated when boost values change

- [x] Task 5: Verify zone propagation for compressor/loudness (AC: #5)
  - [x] 5.1 Confirm frontend `propagateToLinkedClients()` sends compressor/loudness to ONLINE zone members
  - [x] 5.2 Verify OFFLINE clients are skipped (preventing 503 errors)
  - [x] 5.3 Confirm backend `/api/dsp/client/{hostname}/compressor` and `/api/dsp/client/{hostname}/loudness` proxy routes work
  - [x] 5.4 Verify propagation errors tracked via `propagationErrors` ref

- [x] Task 6: Verify preset auto-switch behavior (AC: #6) - INVESTIGATED
  - [x] 6.1 Determine if compressor/loudness changes should trigger preset → "Manual" switch (like EQ filter changes)
  - [x] 6.2 If yes, implement `from_preset=False` check in `set_compressor()` and `set_loudness()`
  - [x] 6.3 If no (compressor/loudness independent of EQ presets), document this design decision

- [x] Task 7: Write integration tests
  - [x] 7.1 Test compressor enable/disable roundtrip (API → CamillaDSP → WebSocket → frontend)
  - [x] 7.2 Test compressor parameter validation (boundary values, partial updates)
  - [x] 7.3 Test loudness enable/disable roundtrip
  - [x] 7.4 Test loudness parameter validation
  - [x] 7.5 Test zone propagation (primary → ONLINE secondaries)
  - [x] 7.6 Test effects bypass preserves compressor/loudness settings for restore

## Dev Notes

### Existing Implementation Analysis

**CRITICAL: Much of the implementation already exists** - Stories 4.1 and 4.2 established the DSP infrastructure. This story is primarily about **verification, testing, and potentially minor adjustments** rather than building from scratch.

### Backend Implementation (ALREADY EXISTS)

The `CamillaDSPService` in `backend/core/dsp/service.py` implements:

1. **Compressor Methods** (lines 592-686):
   ```python
   async def get_compressor(self) -> Dict[str, Any]
   async def set_compressor(self, enabled: Optional[bool] = None,
                            threshold: Optional[float] = None,
                            ratio: Optional[float] = None,
                            attack: Optional[float] = None,
                            release: Optional[float] = None,
                            makeup_gain: Optional[float] = None,
                            persist: bool = True) -> bool
   ```
   - Creates/removes `Processor` type in CamillaDSP config
   - Converts attack/release: `attack_ms / 1000.0` for CamillaDSP
   - Uses `_add_processor_to_pipeline()` / `_remove_processor_from_pipeline()`
   - Broadcasts `compressor_changed` event

2. **Loudness Methods** (lines 687-770):
   ```python
   async def get_loudness(self) -> Dict[str, Any]
   async def set_loudness(self, enabled: Optional[bool] = None,
                          reference_level: Optional[int] = None,
                          high_boost: Optional[float] = None,
                          low_boost: Optional[float] = None,
                          persist: bool = True) -> bool
   ```
   - Implemented via **shelf filters**, NOT a processor
   - `loudness_low`: Lowshelf at 100 Hz with `low_boost` gain
   - `loudness_high`: Highshelf at 8000 Hz with `high_boost` gain
   - Broadcasts `loudness_changed` event

### CamillaDSP Configuration Format

**Compressor (Processor type):**
```json
{
  "processors": {
    "compressor": {
      "type": "Compressor",
      "parameters": {
        "channels": 2,
        "threshold": -20.0,
        "factor": 4.0,
        "attack": 0.01,
        "release": 0.1,
        "makeup_gain": 0.0
      }
    }
  },
  "pipeline": [
    { "type": "Processor", "name": "compressor" }
  ]
}
```

**Loudness (Shelf Filters):**
```json
{
  "filters": {
    "loudness_low": {
      "type": "Biquad",
      "parameters": {
        "type": "Lowshelf",
        "freq": 100,
        "gain": 8.0,
        "slope": 6.0
      }
    },
    "loudness_high": {
      "type": "Biquad",
      "parameters": {
        "type": "Highshelf",
        "freq": 8000,
        "gain": 5.0,
        "slope": 6.0
      }
    }
  }
}
```

### API Endpoints Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/dsp/compressor` | GET | Get compressor settings |
| `/api/dsp/compressor` | PUT | Update compressor settings |
| `/api/dsp/loudness` | GET | Get loudness settings |
| `/api/dsp/loudness` | PUT | Update loudness settings |
| `/api/dsp/client/{hostname}/compressor` | PUT | Proxy compressor to remote client |
| `/api/dsp/client/{hostname}/loudness` | PUT | Proxy loudness to remote client |

### API Request Models

**DspCompressorRequest** (backend/api/models.py lines 269-277):
```python
class DspCompressorRequest(BaseModel):
    enabled: Optional[bool] = None
    threshold: Optional[float] = Field(None, ge=-60, le=0)
    ratio: Optional[float] = Field(None, ge=1, le=20)
    attack: Optional[float] = Field(None, ge=0.1, le=100)    # ms
    release: Optional[float] = Field(None, ge=10, le=1000)   # ms
    makeup_gain: Optional[float] = Field(None, ge=0, le=30)
```

**DspLoudnessRequest** (backend/api/models.py lines 279-285):
```python
class DspLoudnessRequest(BaseModel):
    enabled: Optional[bool] = None
    reference_level: Optional[int] = Field(None, ge=60, le=100)
    high_boost: Optional[float] = Field(None, ge=0, le=15)
    low_boost: Optional[float] = Field(None, ge=0, le=15)
```

### Frontend Implementation (ALREADY EXISTS)

**dspStore.js** (frontend/src/stores/dspStore.js):

1. **State** (lines 54-68):
   ```javascript
   const compressor = ref({
     enabled: false,
     threshold: -20,
     ratio: 4,
     attack: 10,
     release: 100,
     makeup_gain: 0
   });

   const loudness = ref({
     enabled: false,
     reference_level: 80,
     low_boost: 5,
     high_boost: 5
   });
   ```

2. **Update Methods** (lines 822-852):
   - `updateCompressor(settings)` - PUT to `/api/dsp/compressor`
   - `updateLoudness(settings)` - PUT to `/api/dsp/loudness`

3. **WebSocket Handlers** (lines 1188-1194):
   - `handleCompressorChanged(event)` - Updates compressor ref
   - `handleLoudnessChanged(event)` - Updates loudness ref

4. **Zone Propagation** (lines 536-576):
   - `propagateToLinkedClients()` - Syncs to ONLINE zone members
   - Filters by `registryStore.isClientOnline(id)`
   - Tracks errors in `propagationErrors` ref

### WebSocket Events

| Event | Category | Data |
|-------|----------|------|
| `compressor_changed` | dsp | `{enabled, threshold, ratio, attack, release, makeup_gain}` |
| `loudness_changed` | dsp | `{enabled, reference_level, high_boost, low_boost}` |

### Key Code Locations

| Component | File | Lines |
|-----------|------|-------|
| CamillaDSPService.set_compressor | backend/core/dsp/service.py | 592-686 |
| CamillaDSPService.set_loudness | backend/core/dsp/service.py | 687-770 |
| Compressor API routes | backend/presentation/api/routes/dsp.py | 396-426 |
| Loudness API routes | backend/presentation/api/routes/dsp.py | 430-458 |
| Client proxy routes | backend/presentation/api/routes/dsp.py | 855-893 |
| Frontend compressor state | frontend/src/stores/dspStore.js | 54-61 |
| Frontend loudness state | frontend/src/stores/dspStore.js | 63-68 |
| Frontend update methods | frontend/src/stores/dspStore.js | 822-852 |
| Frontend WebSocket handlers | frontend/src/stores/dspStore.js | 1188-1194 |
| Zone propagation | frontend/src/stores/dspStore.js | 536-576 |

### Domain Models

**CompressorSettings** (backend/core/multiroom/models.py lines 115-167):
```python
@dataclass
class CompressorSettings:
    enabled: bool = False
    threshold: float = -20.0   # -60 to 0 dB
    ratio: float = 4.0         # 1 to 20
    attack: float = 10.0       # 0.1 to 100 ms
    release: float = 100.0     # 10 to 1000 ms
    makeup_gain: float = 0.0   # 0 to 30 dB
```

**LoudnessSettings** (backend/core/multiroom/models.py lines 173-217):
```python
@dataclass
class LoudnessSettings:
    enabled: bool = False
    reference_level: int = 80    # 60 to 100 SPL
    high_boost: float = 5.0      # 0 to 15 dB
    low_boost: float = 8.0       # 0 to 15 dB
```

### CamillaDSP Technical Notes

**Compressor Parameters** (from [CamillaDSP documentation](https://github.com/HEnquist/camilladsp)):
- `channels`: Number of channels (must match pipeline, typically 2 for stereo)
- `threshold`: Level (dB) above which compression begins
- `factor`: Compression ratio (called "ratio" in UI)
- `attack`: Time constant (seconds) for attack response
- `release`: Time constant (seconds) for release response
- `makeup_gain`: Gain (dB) applied after compression

**Loudness Implementation** (from [CamillaDSP documentation](https://henquist.github.io/0.5.1/)):
- Milo uses shelf filters instead of CamillaDSP's native Loudness processor
- `Lowshelf` at 100 Hz boosts bass at low volumes
- `Highshelf` at 8000 Hz boosts treble at low volumes
- `reference_level` controls when compensation kicks in (not directly mapped to CamillaDSP - UI reference only)

### Settings Persistence

**Storage:** `/var/lib/milo/settings.json`

```json
{
  "dsp": {
    "compressor": {
      "enabled": true,
      "threshold": -20,
      "ratio": 4,
      "attack": 10,
      "release": 100,
      "makeup_gain": 0
    },
    "loudness": {
      "enabled": false,
      "reference_level": 80,
      "high_boost": 5,
      "low_boost": 8
    }
  }
}
```

### Effects Bypass/Restore Integration

**Important:** The `bypass_effects()` / `restore_effects()` methods (lines 926-1013) handle compressor and loudness:

- **Bypass:** Disables compressor and loudness (persist=False), saves current settings
- **Restore:** Re-enables from saved settings

This must be verified to work correctly with any changes made in this story.

### Project Structure Notes

- Backend follows feature-based architecture: `backend/core/dsp/`
- Frontend uses Pinia with Composition API: `frontend/src/stores/dspStore.js`
- All state changes must go through `state_machine._broadcast_event()` for WebSocket sync
- Settings persist via `SettingsService.set_setting("dsp.compressor", ...)`

### Testing Approach

**Backend (pytest):**
```python
@pytest.mark.asyncio
async def test_set_compressor_enables_processor(dsp_service, mock_camilla):
    await dsp_service.set_compressor(enabled=True, threshold=-25)
    # Verify processor added to CamillaDSP config
    # Verify WebSocket event broadcast
    # Verify settings persisted

@pytest.mark.asyncio
async def test_set_loudness_creates_shelf_filters(dsp_service, mock_camilla):
    await dsp_service.set_loudness(enabled=True, low_boost=10)
    # Verify loudness_low filter created with gain=10
    # Verify loudness_high filter created
```

**Integration:**
- Mock CamillaDSP connection with `@patch("camilladsp.CamillaClient")`
- Test zone propagation via mock HTTP responses to proxy endpoints
- Verify WebSocket events received by frontend

### Previous Story Intelligence (4-3-eq-filter-management)

**Learnings to apply:**
1. Implementation already exists - focus on verification and testing
2. Frontend throttling (50ms during drag, 200ms final) works well for responsive UI
3. Zone propagation should skip OFFLINE clients to prevent 503 errors
4. `propagationErrors` ref tracks and displays propagation failures

**Testing patterns established:**
- 34 backend integration tests covering all ACs
- Frontend tests verify zone propagation logic
- All tests use mocks for CamillaDSP and HTTP

### Git Intelligence (Recent Commits)

Recent relevant commits:
- `57877fd fix(dsp): resolve preset loading and filter restoration issues`
- `14c47ed refactor(frontend): consolidate Pinia stores and eliminate state duplication`

These suggest DSP code is stable and recently cleaned up.

### References

- [Source: backend/core/dsp/service.py#set_compressor] - Compressor implementation (lines 592-686)
- [Source: backend/core/dsp/service.py#set_loudness] - Loudness implementation (lines 687-770)
- [Source: backend/api/models.py#DspCompressorRequest] - API validation (lines 269-277)
- [Source: backend/api/models.py#DspLoudnessRequest] - API validation (lines 279-285)
- [Source: frontend/src/stores/dspStore.js#updateCompressor] - Frontend method (lines 822-836)
- [Source: frontend/src/stores/dspStore.js#updateLoudness] - Frontend method (lines 838-852)
- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.4] - Requirements
- [Source: _bmad-output/project-context.md#Framework-Specific-Rules] - Architecture patterns
- [CamillaDSP GitHub](https://github.com/HEnquist/camilladsp) - Compressor processor documentation
- [CamillaDSP Loudness](https://henquist.github.io/0.5.1/) - Loudness filter documentation

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

N/A - Implementation already existed, this story was verification and testing.

### Completion Notes List

1. **AC1 Verified**: `CamillaDSPService.set_compressor()` correctly toggles compressor processor in CamillaDSP pipeline, broadcasts `compressor_changed` event via WebSocket, and persists to `dsp.compressor` in settings.json.

2. **AC2 Verified**: Parameter validation confirmed via `DspCompressorRequest` Pydantic model with correct ranges (threshold -60 to 0, ratio 1 to 20, attack 0.1-100ms, release 10-1000ms, makeup_gain 0-30dB). Attack/release correctly converted from ms to seconds (`/1000.0`) for CamillaDSP API.

3. **AC3 Verified**: `CamillaDSPService.set_loudness()` correctly creates/removes shelf filters (`loudness_low` at 100Hz Lowshelf, `loudness_high` at 8000Hz Highshelf), persists to `dsp.loudness` in settings.json.

4. **AC4 Verified**: Loudness parameter validation confirmed via `DspLoudnessRequest` (reference_level 60-100 SPL, high_boost/low_boost 0-15dB). `loudness_changed` WebSocket event broadcasts correctly.

5. **AC5 Verified**: Frontend `propagateToLinkedClients()` correctly sends compressor/loudness to ONLINE zone members only, skipping OFFLINE clients. Backend proxy routes `/api/dsp/client/{hostname}/compressor` and `/api/dsp/client/{hostname}/loudness` exist and function correctly.

6. **AC6 Design Decision Documented**: Compressor and loudness are **INDEPENDENT** of EQ presets. EQ presets only control the 10-band parametric EQ gains. Modifying compressor/loudness does NOT trigger auto-switch to "Manual" preset. This is intentional - users should be able to apply compression while using a preset EQ.

7. **Tests Written**: 41 integration tests covering all ACs in `backend/tests/integration/test_compressor_loudness_control.py`. All backend tests pass with no regressions.

### File List

**New Files:**
- `backend/tests/integration/test_compressor_loudness_control.py` - 41 integration tests for Story 4.4

**Modified Files (code review fixes):**
- `backend/api/dsp.py` - Removed duplicate WebSocket broadcasts (lines 420, 452) - service already broadcasts internally

**Verified Files (no changes needed - implementation already complete):**
- `backend/core/dsp/service.py` - `set_compressor()` (lines 597-686), `set_loudness()` (lines 692-770)
- `backend/api/models.py` - `DspCompressorRequest` (lines 269-277), `DspLoudnessRequest` (lines 279-285)
- `frontend/src/stores/dspStore.js` - `updateCompressor()` (lines 822-836), `updateLoudness()` (lines 838-852), `propagateToLinkedClients()` (lines 536-576), WebSocket handlers (lines 1188-1194)

## Change Log

| Date | Change |
|------|--------|
| 2026-01-20 | Story 4.4 completed - Verified compressor & loudness implementation, wrote 39 integration tests, documented AC6 design decision (compressor/loudness independent of EQ presets) |
| 2026-01-20 | Code Review fixes: (1) Removed duplicate WebSocket broadcasts in api/dsp.py, (2) Replaced 4 placeholder tests with real tests for AC5/AC6, (3) Added persist=False verification to bypass tests, (4) Git added test file. Tests increased from 39 to 41. |
