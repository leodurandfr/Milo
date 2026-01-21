# Story 2.1: Define Zone Model

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **developer**,
I want **a well-defined Zone model with all required properties**,
So that **I have a consistent data structure for zone management throughout the system**.

## Acceptance Criteria

1. **AC1: Model Location and Structure**
   - **Given** the backend codebase
   - **When** I verify/update the Zone model in `core/multiroom/models.py`
   - **Then** the model uses Python `@dataclass` decorator (consistent with Client model pattern)
   - **And** the file location is exactly `backend/core/multiroom/models.py`

2. **AC2: Required Fields**
   - **Given** the Zone model
   - **When** I define the model fields
   - **Then** the model includes ALL of these fields:
     - `id: str` - Primary key, UUID format (e.g., `550e8400-e29b-41d4-a716-446655440000`)
     - `name: str` - Human-readable display name, max 15 characters UTF-8
     - `client_ids: List[str]` - List of mac_ids belonging to this zone
     - `dsp_settings: DspSettings` - Shared DSP settings for all zone members

3. **AC3: Zone ID Format**
   - **Given** a new Zone instance
   - **When** zone is created without explicit id
   - **Then** a default factory generates UUID v4 using `uuid.uuid4()`
   - **And** the format is standard UUID string: `550e8400-e29b-41d4-a716-446655440000`

4. **AC4: Name Validation**
   - **Given** the Zone model
   - **When** name is validated (in API layer, NOT in dataclass)
   - **Then** max length is 15 characters UTF-8
   - **And** validation happens in API Pydantic models (request/response boundary)
   - **Note** Dataclass stores the value; Pydantic validates at API boundary

5. **AC5: Serialization Methods**
   - **Given** the Zone model
   - **When** implementing serialization
   - **Then** `to_dict() -> Dict[str, Any]` method exists for persistence
   - **And** `from_dict(data: Dict[str, Any]) -> 'Zone'` classmethod exists for deserialization
   - **And** `dsp_settings` is serialized using its own `to_dict()` method

6. **AC6: Helper Methods**
   - **Given** the Zone model
   - **When** implementing helper methods
   - **Then** `has_client(mac_id: str) -> bool` checks if client is in zone
   - **And** `client_count() -> int` returns number of clients
   - **And** `is_valid() -> bool` returns `True` if `client_count() >= 2`

7. **AC7: Default Values**
   - **Given** a new Zone instance
   - **When** created with minimal parameters (only name)
   - **Then** `id` is auto-generated as UUID
   - **And** `client_ids` defaults to empty list
   - **And** `dsp_settings` defaults to flat/empty DspSettings

## Tasks / Subtasks

- [x] **Task 1: Review existing Zone implementation** (AC: #1, #2)
  - [x] Read current `backend/core/multiroom/models.py` Zone class
  - [x] Verify all required fields are present with correct types
  - [x] Document any gaps between current implementation and AC

- [x] **Task 2: Add UUID auto-generation for zone id** (AC: #3, #7)
  - [x] Import `uuid` module at top of file
  - [x] Change `id: str` to `id: str = field(default_factory=lambda: str(uuid.uuid4()))`
  - [x] Verify this generates valid UUID on instantiation

- [x] **Task 3: Add name length constant** (AC: #4)
  - [x] Add `MAX_ZONE_NAME_LENGTH = 15` constant to models.py
  - [x] Document that validation happens in API layer (Pydantic models)

- [x] **Task 4: Verify serialization methods** (AC: #5)
  - [x] Verify `to_dict()` correctly serializes all fields including dsp_settings
  - [x] Verify `from_dict()` handles missing optional fields gracefully
  - [x] Verify DspSettings serialization is properly nested

- [x] **Task 5: Verify helper methods** (AC: #6)
  - [x] Verify `has_client(mac_id)` returns correct boolean
  - [x] Verify `client_count()` returns correct count
  - [x] Verify `is_valid()` checks for minimum 2 clients

- [x] **Task 6: Update API Pydantic models** (AC: #4)
  - [x] Add/verify `ZoneCreate` Pydantic model in `backend/api/models.py`
  - [x] Add `name: str = Field(..., max_length=15)` validation
  - [x] Add `ZoneUpdate` Pydantic model for PATCH operations

- [x] **Task 7: Run existing tests** (AC: all)
  - [x] Run `python -m pytest backend/tests/test_core_multiroom.py -v`
  - [x] Verify all Zone-related tests pass
  - [x] Add tests for UUID auto-generation if missing

## Dev Notes

### Architecture Context

This story verifies and completes the **Zone model** which is foundational for all zone management functionality in Epic 2. Per architecture document:

> "Zone = DSP settings shared, volume independent per client"
> "Client = IN_ZONE OR STANDALONE (never both)"

**Key architectural decisions:**
1. **Zone stores shared DSP** - `zone.dsp_settings` is source of truth for all members
2. **Volume is per-client** - NOT stored in Zone, each client has own `volume_db`
3. **Minimum 2 clients** - Zone must have at least 2 clients to be valid
4. **Zone persists if empty** - Zone can exist with all clients offline

### Previous Story Intelligence (Epic 1)

From Story 1-1 and 1-2:
- **Dataclass pattern established** - Domain models use `@dataclass`, NOT Pydantic BaseModel
- **Pydantic at API boundary** - Validation happens in `backend/api/models.py`
- **Serialization pattern** - `to_dict()` for persistence, `from_dict()` for loading
- **Runtime vs persisted fields** - Runtime fields (like `online`) excluded from persistence

### Existing Implementation Status

The `Zone` dataclass **already exists** in `backend/core/multiroom/models.py` with:
- `id: str` - UUID (but NO auto-generation factory)
- `name: str` - Display name (but NO max length constant)
- `client_ids: List[str]` - Client MAC IDs
- `dsp_settings: DspSettings` - Shared DSP
- `to_dict()` and `from_dict()` methods
- `has_client()`, `client_count()`, `is_valid()` helper methods

**Gaps to address:**
1. Add UUID auto-generation via `field(default_factory=...)`
2. Add `MAX_ZONE_NAME_LENGTH = 15` constant
3. Create/verify Pydantic models in API layer for validation

### Project Structure Notes

**File location:** `backend/core/multiroom/models.py`

**Related files:**
- `backend/core/multiroom/registry.py` - ClientRegistryService uses Zone model
- `backend/api/models.py` - Pydantic models for API validation
- `backend/api/multiroom.py` - API endpoints (Story 2.4)

**Zone structure in settings.json:**
```json
{
  "multiroom": {
    "zones": {
      "550e8400-e29b-41d4-a716-446655440000": {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "name": "Salon",
        "client_ids": ["dc:a6:32:7e:d3:43", "aa:bb:cc:dd:ee:ff"],
        "dsp_settings": {
          "filters": [],
          "compressor": null,
          "loudness": null
        }
      }
    }
  }
}
```

### UUID Generation Pattern

```python
import uuid
from dataclasses import dataclass, field

@dataclass
class Zone:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    client_ids: List[str] = field(default_factory=list)
    dsp_settings: DspSettings = field(default_factory=DspSettings)
```

### API Pydantic Models Pattern

In `backend/api/models.py`:
```python
from pydantic import BaseModel, Field
from typing import List, Optional

MAX_ZONE_NAME_LENGTH = 15

class ZoneCreate(BaseModel):
    """Request model for zone creation."""
    name: str = Field(..., max_length=MAX_ZONE_NAME_LENGTH)
    client_ids: List[str] = Field(..., min_length=2)

class ZoneUpdate(BaseModel):
    """Request model for zone updates."""
    name: Optional[str] = Field(None, max_length=MAX_ZONE_NAME_LENGTH)

class ZoneResponse(BaseModel):
    """Response model for zone data."""
    id: str
    name: str
    client_ids: List[str]
    dsp_settings: dict
    volume_db: float  # Calculated average of online clients
```

### DspSettings Structure

Already defined in `models.py`:
```python
@dataclass
class DspSettings:
    filters: List[Dict[str, Any]] = field(default_factory=list)
    compressor: Optional[Dict[str, Any]] = None
    loudness: Optional[Dict[str, Any]] = None
```

This is used by Zone for shared DSP and by standalone clients (stored in `standalone_dsp` dict in registry).

### Git Intelligence

Recent commits show:
- `99a98b7`: Crossover computed dynamically based on subwoofer availability
- `fa167e4`: Client deletion and offline handling improvements
- `4e3aa94`: Fix to prevent premature zone deletion when removing client
- `14c47ed`: Consolidated Pinia stores, eliminated state duplication

These indicate active work on zone/client relationships - align with existing patterns.

### FRs Covered by This Story

From PRD:
- **FR3 (partial)**: User can create/delete zones with minimum 2 clients - **Model foundation**
- **FR4 (partial)**: Zone stores and shares DSP settings - **Model defines `dsp_settings` field**

### Testing Checklist

Per project-context.md:
- Tests in `backend/tests/test_core_multiroom.py`
- Use `@pytest.mark.asyncio` for async tests (if any)
- Test UUID auto-generation
- Test serialization/deserialization round-trip
- Test helper methods (`has_client`, `client_count`, `is_valid`)

### References

- [Source: _bmad-output/planning-artifacts/architecture.md - "Data Architecture" section]
- [Source: _bmad-output/planning-artifacts/epics.md - Story 2.1]
- [Source: _bmad-output/planning-artifacts/prd-multiroom-dsp.md - FR3, FR4]
- [Source: _bmad-output/implementation-artifacts/1-1-define-registered-client-model.md - Dataclass pattern]
- [Source: _bmad-output/project-context.md - Critical implementation rules]
- [Source: backend/core/multiroom/models.py - Existing Zone implementation]

### Technical Clarification: Dataclass vs Pydantic

The epics.md story says "model uses Pydantic for validation" but this **contradicts** the architectural pattern established in Story 1-1:

> "Use **standard dataclasses** for internal domain models (fast, lightweight)"
> "Use **Pydantic BaseModel** only at API boundaries (request/response validation)"

**Resolution:** Keep Zone as `@dataclass` (consistent with Client). Add Pydantic models in `api/models.py` for validation at API boundary. This matches the established architecture.

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

None

### Completion Notes List

1. **Task 1**: Reviewed existing Zone implementation. Found all required fields present (id, name, client_ids, dsp_settings) with correct types. Identified gaps: no UUID auto-generation, no MAX_ZONE_NAME_LENGTH constant, no API Pydantic models.

2. **Task 2**: Added `import uuid` and modified Zone dataclass to use `id: str = field(default_factory=lambda: str(uuid.uuid4()))`. Reordered fields to put `name` first (required field without default must come before optional fields in dataclass).

3. **Task 3**: Added `MAX_ZONE_NAME_LENGTH = 15` constant to models.py with comment documenting that validation happens in API layer.

4. **Task 4**: Verified `to_dict()` and `from_dict()` methods. Updated `from_dict()` argument order to match new dataclass field order (name first).

5. **Task 5**: Verified all helper methods work correctly: `has_client()`, `client_count()`, `is_valid()`.

6. **Task 6**: Added Pydantic models to `backend/api/models.py`:
   - `ZoneCreate`: name (max 15 chars), client_ids (min 2)
   - `ZoneUpdate`: optional name update
   - `ZoneResponse`: full zone data response model

7. **Task 7**: Ran all tests - 118 unit tests + 25 integration tests pass. Added 2 new tests: `test_zone_uuid_auto_generation` and `test_zone_default_values`. Updated existing test argument order to match new dataclass signature.

### File List

**Modified:**
- `backend/core/multiroom/models.py` - Added uuid import, UUID auto-generation for Zone.id, MAX_ZONE_NAME_LENGTH constant, updated Zone dataclass field order
- `backend/api/models.py` - Added ZoneCreate, ZoneUpdate, ZoneResponse Pydantic models; refactored to import MAX_ZONE_NAME_LENGTH from domain model (DRY)
- `backend/tests/test_core_multiroom.py` - Added 2 new tests for UUID auto-generation, updated existing Zone test arguments order
- `backend/tests/test_api_multiroom.py` - Added 20 new tests for Zone Pydantic model validation (ZoneCreate, ZoneUpdate, ZoneResponse)

## Change Log

- 2026-01-18: Story 2.1 implemented - Zone model enhanced with UUID auto-generation, MAX_ZONE_NAME_LENGTH constant, and API Pydantic models (ZoneCreate, ZoneUpdate, ZoneResponse). All 143 tests pass.
- 2026-01-18: Code review fixes applied:
  - Added 20 unit tests for Zone Pydantic models (ZoneCreate, ZoneUpdate, ZoneResponse) in test_api_multiroom.py
  - Refactored api/models.py to import MAX_ZONE_NAME_LENGTH from domain model (removed duplication)
