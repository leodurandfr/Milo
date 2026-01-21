# Story 4.1: Define DspSettings Model

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **developer**,
I want **a well-defined DspSettings model with all DSP properties**,
So that **I have a consistent data structure for audio processing settings**.

## Acceptance Criteria

1. **AC1: Model structure complete**
   - **Given** the backend codebase
   - **When** I create/update the DspSettings model in `core/multiroom/models.py`
   - **Then** the model includes:
     - `enabled` (bool) - global DSP bypass toggle
     - `filters` (List[EqFilter]) - EQ bands with id, frequency, gain, q, filter_type
     - `compressor` (CompressorSettings) - enabled, threshold, ratio, attack, release
     - `loudness` (LoudnessSettings) - enabled, reference_level

2. **AC2: Pydantic validation**
   - **Given** the model is Pydantic-based
   - **When** invalid values are provided
   - **Then** appropriate validation errors are raised

3. **AC3: Default flat configuration**
   - **Given** a new DspSettings instance
   - **When** created with defaults
   - **Then** it creates a "flat" configuration (no processing): enabled=True, empty filters, compressor/loudness disabled

4. **AC4: Serialization and deserialization**
   - **Given** a DspSettings instance
   - **When** converted to dict and back
   - **Then** all values are preserved correctly

## Tasks / Subtasks

- [x] Task 1: Create EqFilter dataclass (AC: 1, 2)
  - [x] Define fields: id, frequency, gain, q, filter_type, enabled
  - [x] Add validation: frequency 20-20000 Hz, gain -15 to +15 dB, Q 0.1-10.0
  - [x] Add filter_type enum validation (Peaking, Lowshelf, Highshelf, etc.)
  - [x] Implement to_dict() and from_dict() methods

- [x] Task 2: Create CompressorSettings dataclass (AC: 1, 2)
  - [x] Define fields: enabled, threshold, ratio, attack, release, makeup_gain
  - [x] Add validation: threshold -60 to 0 dB, ratio 1-20, attack 0.1-100 ms, release 10-1000 ms
  - [x] Set sensible defaults matching CamillaDSPService
  - [x] Implement to_dict() and from_dict() methods

- [x] Task 3: Create LoudnessSettings dataclass (AC: 1, 2)
  - [x] Define fields: enabled, reference_level, high_boost, low_boost
  - [x] Add validation: reference_level 60-100, boosts 0-15 dB
  - [x] Set sensible defaults matching CamillaDSPService
  - [x] Implement to_dict() and from_dict() methods

- [x] Task 4: Refactor DspSettings to use typed sub-models (AC: 1, 2, 3)
  - [x] Add `enabled` field (default True)
  - [x] Change filters from List[Dict] to List[EqFilter]
  - [x] Change compressor from Optional[Dict] to CompressorSettings
  - [x] Change loudness from Optional[Dict] to LoudnessSettings
  - [x] Maintain backward compatibility with existing to_dict()/from_dict()

- [x] Task 5: Add default() factory method (AC: 3)
  - [x] Create flat EQ defaults (10 bands, 0 dB gain)
  - [x] Create disabled compressor with sensible defaults
  - [x] Create disabled loudness with sensible defaults

- [x] Task 6: Update RegistryState and Zone to use new DspSettings (AC: 4)
  - [x] Ensure Zone.dsp_settings uses the updated model
  - [x] Ensure standalone_dsp in RegistryState works with new model
  - [x] Update from_dict() methods for backward compatibility

- [x] Task 7: Write unit tests (AC: 1, 2, 3, 4)
  - [x] Test EqFilter validation boundaries
  - [x] Test CompressorSettings validation boundaries
  - [x] Test LoudnessSettings validation boundaries
  - [x] Test DspSettings serialization roundtrip
  - [x] Test backward compatibility with existing settings.json format

## Dev Notes

### Context: This is Epic 4's Foundation Story

This story creates the data model foundation for all DSP-related stories in Epic 4:
- Story 4.2: DspService will use these models
- Story 4.3: EQ filter management uses EqFilter
- Story 4.4: Compressor/loudness control uses CompressorSettings/LoudnessSettings
- Story 4.5: Global DSP bypass uses the `enabled` field
- Story 4.6: Presets system will serialize/deserialize these models

### Existing Code Analysis

**Current DspSettings in `backend/core/multiroom/models.py:39-79`:**
```python
@dataclass
class DspSettings:
    filters: List[Dict[str, Any]] = field(default_factory=list)
    compressor: Optional[Dict[str, Any]] = None
    loudness: Optional[Dict[str, Any]] = None
```

**CamillaDSPService internal defaults (backend/core/dsp/service.py:79-96):**
```python
self._compressor: Dict[str, Any] = {
    "enabled": False,
    "threshold": -20.0,
    "ratio": 4.0,
    "attack": 10.0,
    "release": 100.0,
    "makeup_gain": 0.0
}
self._loudness: Dict[str, Any] = {
    "enabled": False,
    "reference_level": 80,
    "high_boost": 5.0,
    "low_boost": 8.0
}
```

**API Pydantic models already exist in `backend/api/models.py:269-284`:**
```python
class DspCompressorRequest(BaseModel):
    enabled: Optional[bool] = None
    threshold: Optional[float] = Field(None, ge=-60, le=0)
    ratio: Optional[float] = Field(None, ge=1, le=20)
    attack: Optional[float] = Field(None, ge=0.1, le=100)
    release: Optional[float] = Field(None, ge=10, le=1000)
    makeup_gain: Optional[float] = Field(None, ge=0, le=30)

class DspLoudnessRequest(BaseModel):
    enabled: Optional[bool] = None
    reference_level: Optional[int] = Field(None, ge=60, le=100)
    high_boost: Optional[float] = Field(None, ge=0, le=15)
    low_boost: Optional[float] = Field(None, ge=0, le=15)
```

### Architecture Alignment

**From Architecture Document:**
- DSP settings stored in Zone (shared) AND in Client (standalone only)
- DspSettings structure: `filters[], compressor{}, loudness{}, crossover{}`
- Note: `crossover{}` is managed separately by CrossoverService (Epic 5), not part of this story

**From PRD FRs covered by Epic 4:**
- FR16: DSP changes update zone.dsp_settings
- FR19: EQ filters (frequency, gain, Q)
- FR20: Compressor parameters
- FR21: Loudness compensation

### Implementation Approach

1. **Use dataclasses** (not Pydantic for domain models) - consistent with existing Client, Zone models
2. **Validation happens at API boundary** - Pydantic models in `api/models.py` already handle this
3. **Domain models focus on structure** - to_dict()/from_dict() for serialization
4. **Backward compatibility** - existing settings.json with Dict format must still work

### Default EQ Frequencies (10-band parametric)

From existing CamillaDSP configuration pattern:
```python
DEFAULT_EQ_FREQS = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
```

### Filter Types Enum

From `backend/core/dsp/service.py:23-31`:
```python
class FilterType(str, Enum):
    PEAKING = "Peaking"
    LOWSHELF = "Lowshelf"
    HIGHSHELF = "Highshelf"
    LOWPASS = "Lowpass"
    HIGHPASS = "Highpass"
    NOTCH = "Notch"
    ALLPASS = "Allpass"
```

### Project Structure Notes

- **File to modify:** `backend/core/multiroom/models.py`
- **Tests to create:** `backend/tests/test_dsp_models.py`
- **Related files (no changes needed):**
  - `backend/api/models.py` - API validation already exists
  - `backend/core/dsp/service.py` - Will be updated in Story 4.2

### References

- [Source: backend/core/multiroom/models.py:39-79] - Current DspSettings
- [Source: backend/core/dsp/service.py:79-96] - Compressor/loudness defaults
- [Source: backend/core/dsp/service.py:23-31] - FilterType enum
- [Source: backend/api/models.py:269-284] - Pydantic validation models
- [Source: _bmad-output/planning-artifacts/architecture.md#Data-Architecture] - DSP storage decisions
- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.1] - Original story requirements
- [Source: _bmad-output/project-context.md] - Project coding standards

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

N/A

### Completion Notes List

1. **EqFilter dataclass created** - Full implementation with id, frequency, gain, q, filter_type (FilterType enum), enabled fields. Includes to_dict() and from_dict() with backward compatibility for old key names ("freq" → "frequency", "type" → "filter_type").

2. **CompressorSettings dataclass created** - Includes enabled, threshold, ratio, attack, release, makeup_gain with defaults matching CamillaDSPService internal state.

3. **LoudnessSettings dataclass created** - Includes enabled, reference_level, high_boost, low_boost with defaults matching CamillaDSPService.

4. **DspSettings refactored** - Now uses typed sub-models:
   - `enabled: bool = True` (global DSP bypass toggle)
   - `filters: List[EqFilter]` (was List[Dict])
   - `compressor: CompressorSettings` (was Optional[Dict])
   - `loudness: LoudnessSettings` (was Optional[Dict])

5. **DspSettings.default() factory method** - Creates flat 10-band EQ at standard frequencies (31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000 Hz) with 0 dB gain, disabled compressor, disabled loudness.

6. **Backward compatibility preserved** - from_dict() handles old Dict format from existing settings.json files.

7. **55 unit tests written** - Comprehensive test coverage in `backend/tests/test_dsp_models.py` for:
   - EqFilter validation and serialization
   - CompressorSettings validation and serialization
   - LoudnessSettings validation and serialization
   - DspSettings with typed sub-models
   - Backward compatibility with old format

8. **All 1057 tests pass** - No regressions in existing test suite.

### File List

**Modified:**
- `backend/core/multiroom/models.py` - Added FilterType enum, DEFAULT_EQ_FREQUENCIES constant, EqFilter, CompressorSettings, LoudnessSettings dataclasses, refactored DspSettings
- `backend/tests/test_core_multiroom.py` - Updated tests for new typed DspSettings structure
- `backend/tests/integration/test_multiroom_zones.py` - Updated integration tests for new DspSettings

**Created:**
- `backend/tests/test_dsp_models.py` - 55 unit tests for DSP models

