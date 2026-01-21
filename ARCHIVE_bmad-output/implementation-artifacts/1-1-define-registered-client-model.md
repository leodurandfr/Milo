# Story 1.1: Define Client Model

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **developer**,
I want **a well-defined Client model with all required properties**,
So that **I have a consistent data structure for client state throughout the system**.

## Acceptance Criteria

1. **AC1: Model Location and Structure**
   - **Given** the backend codebase
   - **When** I create/update the Client model in `core/multiroom/models.py`
   - **Then** the model uses Python `@dataclass` decorator (NOT Pydantic BaseModel)
   - **And** the file location is exactly `backend/core/multiroom/models.py`

2. **AC2: Required Fields**
   - **Given** the Client model
   - **When** I define the model fields
   - **Then** the model includes ALL of these fields:
     - `mac_id: str` - Primary key, MAC address with colons (e.g., `dc:a6:32:7e:d3:43`) or `"local"` for main device
     - `name: str` - Human-readable display name
     - `ip: str` - Current IP address
     - `online: bool` - Connection status (runtime only, not persisted)
     - `zone_id: Optional[str]` - UUID of zone membership, `None` if standalone
     - `volume_db: float` - Individual volume in dB, default `-60.0`
     - `mute: bool` - Mute status
     - `speaker_type: SpeakerType` - One of: `satellite`, `bookshelf`, `tower`, `subwoofer`

3. **AC3: SpeakerType Definition**
   - **Given** the models.py file
   - **When** I define the SpeakerType
   - **Then** it uses `Literal['satellite', 'bookshelf', 'tower', 'subwoofer']`
   - **And** a constant list `SPEAKER_TYPES = ['satellite', 'bookshelf', 'tower', 'subwoofer']` exists
   - **And** `DEFAULT_SPEAKER_TYPE: SpeakerType = 'bookshelf'` is defined
   - **And** `DEFAULT_CROSSOVER_FREQUENCIES` dict maps each type to its Hz value

4. **AC4: MAC Address Format**
   - **Given** the mac_id field
   - **When** storing/displaying MAC addresses
   - **Then** format is WITH colons: `dc:a6:32:7e:d3:43` (17 characters)
   - **And** special case `"local"` is allowed for main device
   - **Note** URL conversion (removing colons) happens in API layer, not in model

5. **AC5: Serialization Methods**
   - **Given** the Client model
   - **When** implementing serialization
   - **Then** `to_dict() -> Dict[str, Any]` method exists for persistence
   - **And** `from_dict(data: Dict[str, Any]) -> 'Client'` classmethod exists for deserialization
   - **And** runtime fields (`online`) are excluded from `to_dict()` output

6. **AC6: Helper Methods**
   - **Given** the Client model
   - **When** implementing helper methods
   - **Then** `is_standalone() -> bool` returns `True` if `zone_id is None`
   - **And** `is_in_zone() -> bool` returns `True` if `zone_id is not None`

7. **AC7: Default Values**
   - **Given** a new Client instance
   - **When** created with minimal parameters
   - **Then** `volume_db` defaults to `DEFAULT_VOLUME_DB` (-60.0 dB from constants)
   - **And** `speaker_type` defaults to `'bookshelf'`
   - **And** `zone_id` defaults to `None` (standalone)
   - **And** `online` defaults to `False`
   - **And** `mute` defaults to `False`

## Tasks / Subtasks

- [x] **Task 1: Review and update SpeakerType definition** (AC: #3)
  - [x] Verify `SpeakerType = Literal['satellite', 'bookshelf', 'tower', 'subwoofer']` exists
  - [x] Verify `SPEAKER_TYPES` list constant exists
  - [x] Verify `DEFAULT_SPEAKER_TYPE` constant exists
  - [x] Verify `DEFAULT_CROSSOVER_FREQUENCIES` dict exists with correct Hz values

- [x] **Task 2: Review and update Client/RegisteredClient model** (AC: #1, #2, #7)
  - [x] Verify dataclass decorator is used (NOT Pydantic)
  - [x] Verify all 8 fields are present with correct types
  - [x] Verify default values match requirements
  - [x] Verify `field(default_factory=...)` is used for mutable defaults if any

- [x] **Task 3: Implement/verify serialization methods** (AC: #5)
  - [x] Verify `to_dict()` method excludes runtime fields (`online`)
  - [x] Verify `from_dict()` classmethod handles missing fields gracefully
  - [x] Verify both methods handle `speaker_type` correctly

- [x] **Task 4: Implement/verify helper methods** (AC: #6)
  - [x] Verify `is_standalone()` returns `zone_id is None`
  - [x] Verify `is_in_zone()` returns `zone_id is not None`

- [x] **Task 5: Verify volume constants integration** (AC: #7)
  - [x] Import `DEFAULT_VOLUME_DB` from `config/constants.py`
  - [x] Verify volume_db default uses the constant

- [x] **Task 6: Run existing tests** (AC: all)
  - [x] Run `python -m pytest backend/tests/test_core_multiroom.py -v`
  - [x] Verify all model-related tests pass

## Dev Notes

### Architecture Context

This story is **foundational** - the Client model is used by ALL subsequent stories in the multiroom/DSP refactoring. It must be correct and complete before proceeding.

**Key architectural decisions from brainstorming:**

1. **Client = appareil physique avec CamillaDSP** - Each client is a Milo device
2. **Client = IN_ZONE OU STANDALONE** - Mutually exclusive states
3. **Client = ONLINE OU OFFLINE** - Mutually exclusive states
4. **Backend Milo = source de vérité unique** - No frontend storage
5. **"local" = client comme les autres** - Same rules, no special treatment

### DSP Settings Note

The `dsp_settings` field is NOT part of RegisteredClient directly. Per architecture:
- **STANDALONE clients**: DSP stored in `standalone_dsp[mac_id]` in ClientRegistryService
- **IN_ZONE clients**: DSP source of truth is `zone.dsp_settings`

This separation is intentional to maintain clear ownership of DSP settings.

### Existing Implementation Status

The `Client` dataclass already exists in `backend/core/multiroom/models.py` with most fields. This story is about **verifying and completing** the implementation, not starting from scratch.

**Current state (from codebase analysis):**
- `Client` dataclass exists with core fields
- `SpeakerType` Literal and constants exist
- `to_dict()` and `from_dict()` methods exist
- Helper methods `is_standalone()` and `is_in_zone()` exist

**May need verification/updates:**
- Ensure all default values match requirements
- Ensure `online` is excluded from persistence
- Ensure volume_db uses `DEFAULT_VOLUME_DB` constant

### Project Structure Notes

**File location:** `backend/core/multiroom/models.py`

**Related files:**
- `backend/config/constants.py` - Volume constants (DEFAULT_VOLUME_DB, MIN_VOLUME_DB, MAX_VOLUME_DB)
- `backend/core/multiroom/registry.py` - ClientRegistryService that uses these models
- `backend/api/models.py` - Pydantic models for API validation (separate concern)

**Naming convention:**
- Model class: `Client` (currently) - could be renamed to `RegisteredClient` for clarity, but existing code uses `Client`
- Keep consistency with existing codebase

### Volume Constraints

From `backend/config/constants.py`:
```python
DEFAULT_VOLUME_DB = -60.0   # Default for new clients
MIN_VOLUME_DB = -80.0       # Technical minimum (silent)
MAX_VOLUME_DB = 0.0         # Technical maximum
```

**Note:** Validation of volume range happens in API layer (Pydantic), not in the dataclass model.

### Speaker Type Crossover Frequencies

From architecture and brainstorming:
```python
DEFAULT_CROSSOVER_FREQUENCIES = {
    'satellite': 120,   # Small speakers (~120 Hz highpass)
    'bookshelf': 80,    # Medium speakers (THX standard)
    'tower': 50,        # Full-range speakers (~40-50 Hz)
    'subwoofer': None   # Receives lowpass, not highpass
}
```

### MAC Address Handling

**Storage format:** With colons (`dc:a6:32:7e:d3:43`)
**Special case:** `"local"` string for main device

URL conversion (removing colons for path parameters) is handled in the API layer, NOT in the model.

### References

- [Source: _bmad-output-v2/planning-artifacts/architecture.md - Section 3.1 Data Models]
- [Source: _bmad-output-v2/analysis/brainstorming-multiroom-dsp-complet.md - Section 1.2 Entités Minimales]
- [Source: _bmad-output-v2/planning-artifacts/epics.md - Story 1.1]
- [Source: backend/core/multiroom/models.py - Existing implementation]
- [Source: backend/config/constants.py - Volume constants]
- [Source: Pydantic docs - Dataclass best practices](https://docs.pydantic.dev/latest/concepts/dataclasses/)

### Technical Best Practices

Per [Pydantic documentation](https://docs.pydantic.dev/latest/concepts/dataclasses/):
- Use **standard dataclasses** for internal domain models (fast, lightweight)
- Use **Pydantic BaseModel** only at API boundaries (request/response validation)
- This project correctly follows this pattern

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

None required - implementation was straightforward verification with one correction.

### Completion Notes List

- ✅ Verified SpeakerType definition with all constants (SPEAKER_TYPES, DEFAULT_SPEAKER_TYPE, DEFAULT_CROSSOVER_FREQUENCIES)
- ✅ Verified Client dataclass with all 8 required fields and correct types
- ✅ Verified default values match requirements (volume_db=-60.0, speaker_type='bookshelf', etc.)
- ✅ Fixed `to_dict()` to exclude runtime field `online` per AC #5 (was incorrectly included)
- ✅ Updated test `test_client_to_dict` to verify `online` is NOT in serialization output
- ✅ Verified `from_dict()` handles missing fields gracefully with appropriate defaults
- ✅ Verified helper methods `is_standalone()` and `is_in_zone()` work correctly
- ✅ Verified volume_db uses DEFAULT_VOLUME_DB constant from config/constants.py
- ✅ All 143 tests pass (98 multiroom + 45 volume)

### Change Log

- 2026-01-18: Fixed `Client.to_dict()` to exclude runtime field `online` per AC #5
- 2026-01-18: Updated test to verify `online` is excluded from serialization
- 2026-01-18: **Code Review** - Updated story wording from "RegisteredClient" to "Client" (matches implementation)
- 2026-01-18: **Code Review** - Added edge case tests for `from_dict()` (missing required fields, unknown speaker_type)
- 2026-01-18: **Code Review** - Removed obsolete migration comments from integration tests

### File List

**Modified:**
- `backend/core/multiroom/models.py` - Fixed `to_dict()` to exclude `online` field
- `backend/tests/test_core_multiroom.py` - Updated tests to verify `online` exclusion + added edge case tests
- `backend/tests/integration/test_multiroom_zones.py` - Removed obsolete migration comments

**Verified (no changes needed):**
- `backend/config/constants.py` - Volume constants correctly defined
