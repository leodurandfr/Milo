# Story 2.2: Implement Zone CRUD in ClientRegistryService

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **system**,
I want **zone management methods in ClientRegistryService**,
So that **zones can be created, retrieved, and deleted with proper persistence**.

## Acceptance Criteria

1. **AC1: Zone Methods Exist**
   - **Given** the Zone model exists (Story 2.1 ✅ DONE)
   - **When** I verify zone methods in ClientRegistryService
   - **Then** the service provides: `create_zone(name, client_ids)`, `delete_zone(zone_id)`, `get_zone(zone_id)`, `get_all_zones()`
   - **Note**: These methods are **ALREADY IMPLEMENTED** in `backend/core/multiroom/registry.py`

2. **AC2: Zone Creation with Minimum 2 Clients**
   - **Given** I call `create_zone(name, client_ids)` (or API equivalent)
   - **When** `client_ids` contains at least 2 valid mac_ids
   - **Then** a new Zone is created with UUID, clients are updated with `zone_id`, and zone is persisted to `settings.json`
   - **And** a WebSocket event `zone_created` is broadcast

3. **AC3: Zone Creation Validation**
   - **Given** I call `create_zone(name, client_ids)`
   - **When** `client_ids` contains less than 2 clients
   - **Then** a validation error is raised (400 Bad Request at API layer)

4. **AC4: Zone Deletion**
   - **Given** I call `delete_zone(zone_id)`
   - **When** the zone exists
   - **Then** all member clients have their `zone_id` set to `None`
   - **And** clients retain zone DSP as standalone DSP (FR14)
   - **And** the zone is removed from persistence
   - **And** a WebSocket event `zone_deleted` is broadcast

5. **AC5: API Endpoints for Zone CRUD**
   - **Given** ClientRegistryService zone methods exist
   - **When** I expose REST API endpoints
   - **Then** the following endpoints are available:
     - `GET /api/multiroom/zones` - List all zones
     - `GET /api/multiroom/zones/{zone_id}` - Get specific zone
     - `POST /api/multiroom/zones` - Create zone
     - `PATCH /api/multiroom/zones/{zone_id}` - Update zone name
     - `DELETE /api/multiroom/zones/{zone_id}` - Delete zone

6. **AC6: Unit Tests for Zone CRUD**
   - **Given** the zone methods and API endpoints
   - **When** I run the test suite
   - **Then** tests cover: zone creation, validation errors, deletion, retrieval

## Tasks / Subtasks

- [x] **Task 1: Verify existing ClientRegistryService zone methods** (AC: #1)
  - [x] Confirm `create_zone()`, `delete_zone()`, `update_zone()`, `get_zone()`, `get_all_zones()` exist
  - [x] Verify WebSocket events are emitted (`zone_created`, `zone_deleted`, `zone_updated`)
  - [x] Verify persistence to `settings.json` works correctly

- [x] **Task 2: Add zone endpoints to multiroom router** (AC: #5)
  - [x] Import `ZoneCreate`, `ZoneUpdate`, `ZoneResponse` from `backend/api/models.py`
  - [x] Add `GET /api/multiroom/zones` endpoint
  - [x] Add `GET /api/multiroom/zones/{zone_id}` endpoint
  - [x] Add `POST /api/multiroom/zones` endpoint with `ZoneCreate` validation
  - [x] Add `PATCH /api/multiroom/zones/{zone_id}` endpoint with `ZoneUpdate` validation
  - [x] Add `DELETE /api/multiroom/zones/{zone_id}` endpoint

- [x] **Task 3: Implement zone response enrichment** (AC: #2, #4, #5)
  - [x] Use existing `zone_to_enriched_dict()` for responses (adds `online_client_count`, `has_subwoofer`, `crossover_enabled`)
  - [x] Ensure responses match `ZoneResponse` model structure

- [x] **Task 4: Add/verify unit tests** (AC: #6)
  - [x] Add tests in `backend/tests/test_api_multiroom.py` for zone endpoints
  - [x] Test zone creation with valid clients
  - [x] Test zone creation with < 2 clients (expect 400)
  - [x] Test zone deletion and client state transition
  - [x] Test zone retrieval endpoints

- [x] **Task 5: Run test suite and verify** (AC: all)
  - [x] Run `python -m pytest backend/tests/test_api_multiroom.py -v`
  - [x] Run `python -m pytest backend/tests/test_core_multiroom.py -v`
  - [x] Verify all tests pass

## Dev Notes

### Critical Discovery: Service Methods Already Exist

**The zone CRUD methods are ALREADY FULLY IMPLEMENTED** in `ClientRegistryService` (backend/core/multiroom/registry.py):

```python
# Already implemented methods:
async def create_zone(self, zone_id, name, client_ids, dsp_settings) -> Zone
async def delete_zone(self, zone_id) -> bool
async def update_zone(self, zone_id, name) -> Optional[Zone]
async def add_client_to_zone(self, zone_id, mac_id) -> bool
async def remove_client_from_zone(self, zone_id, mac_id) -> bool
async def set_zone_clients(self, zone_id, client_ids) -> Optional[Zone]

# Query methods:
def get_zone(self, zone_id) -> Optional[Zone]
def get_all_zones(self) -> Dict[str, Zone]
def get_zone_for_client(self, mac_id) -> Optional[Zone]
def get_zone_clients(self, zone_id) -> List[Client]
def get_online_zone_clients(self, zone_id) -> List[Client]
def has_online_subwoofer(self, zone_id) -> bool
def zone_to_enriched_dict(self, zone) -> Dict[str, Any]
```

### What Actually Needs to Be Done

**The ONLY work required is adding REST API endpoints** to expose the existing service methods:

1. The current `backend/api/multiroom.py` only has client endpoints (`/api/multiroom/clients/...`)
2. Need to add zone endpoints (`/api/multiroom/zones/...`)

### Implementation Pattern (Follow Existing Code)

The router uses dependency injection pattern. Follow the existing `create_multiroom_router(registry_service)` structure:

```python
# In backend/api/multiroom.py, add to create_multiroom_router():

from backend.api.models import ZoneCreate, ZoneUpdate, ZoneResponse

@router.get("/zones")
async def get_zones():
    """Get all zones with enriched data."""
    zones = registry_service.get_all_zones()
    return {
        "zones": [registry_service.zone_to_enriched_dict(z) for z in zones.values()]
    }

@router.get("/zones/{zone_id}")
async def get_zone(zone_id: str):
    """Get specific zone."""
    zone = registry_service.get_zone(zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail=f"Zone '{zone_id}' not found")
    return registry_service.zone_to_enriched_dict(zone)

@router.post("/zones", status_code=201)
async def create_zone(request: ZoneCreate):
    """Create a new zone."""
    import uuid
    zone_id = str(uuid.uuid4())

    try:
        zone = await registry_service.create_zone(
            zone_id=zone_id,
            name=request.name,
            client_ids=request.client_ids
        )
        return {"status": "success", "zone": registry_service.zone_to_enriched_dict(zone)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.patch("/zones/{zone_id}")
async def update_zone(zone_id: str, request: ZoneUpdate):
    """Update zone properties."""
    zone = await registry_service.update_zone(zone_id, name=request.name)
    if not zone:
        raise HTTPException(status_code=404, detail=f"Zone '{zone_id}' not found")
    return {"status": "success", "zone": registry_service.zone_to_enriched_dict(zone)}

@router.delete("/zones/{zone_id}")
async def delete_zone(zone_id: str):
    """Delete a zone. Clients become standalone with zone DSP retained."""
    success = await registry_service.delete_zone(zone_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Zone '{zone_id}' not found")
    return {"status": "success", "message": f"Zone '{zone_id}' deleted"}
```

### Pydantic Models (Already Created in Story 2.1)

From `backend/api/models.py`:
```python
class ZoneCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=15)
    client_ids: List[str] = Field(..., min_length=2)

class ZoneUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=15)

class ZoneResponse(BaseModel):
    id: str
    name: str
    client_ids: List[str]
    dsp_settings: Dict[str, Any]
```

### Zone Enriched Response Structure

The `zone_to_enriched_dict()` method adds computed fields:
```python
{
    "id": "uuid-...",
    "name": "Salon",
    "client_ids": ["local", "aa:bb:cc:dd:ee:ff"],
    "dsp_settings": {...},
    # Computed fields added by zone_to_enriched_dict():
    "online_client_count": 2,
    "has_subwoofer": false,
    "crossover_enabled": false
}
```

### WebSocket Events (Already Implemented)

The service methods already emit these events via `_emit_event()`:
- `zone_created` - When zone is created
- `zone_updated` - When zone name or clients change
- `zone_deleted` - When zone is deleted

Event structure:
```json
{
  "category": "registry",
  "type": "zone_created",
  "data": {
    "zone_id": "uuid-...",
    "zone": { ... }
  }
}
```

### Previous Story Intelligence (2.1)

From Story 2-1:
- Zone model uses `@dataclass` (not Pydantic)
- UUID auto-generation via `field(default_factory=lambda: str(uuid.uuid4()))`
- `MAX_ZONE_NAME_LENGTH = 15` constant defined
- Pydantic models (`ZoneCreate`, `ZoneUpdate`, `ZoneResponse`) already in `api/models.py`
- Tests for Zone model pass (143 total tests)

### Git Intelligence

Recent relevant commits:
- `fa167e4`: Client deletion and offline handling improvements
- `4e3aa94`: Fix to prevent premature zone deletion when removing client
- `14c47ed`: Consolidated Pinia stores, eliminated state duplication

These show zone/client relationship handling is working - just needs API exposure.

### Project Structure Notes

**Files to modify:**
- `backend/api/multiroom.py` - Add zone endpoints (MAIN WORK)

**Files to reference:**
- `backend/core/multiroom/registry.py` - Service methods (already implemented)
- `backend/api/models.py` - Pydantic models (already created)
- `backend/tests/test_api_multiroom.py` - Add zone endpoint tests

### FRs Covered by This Story

- **FR3 (partial)**: User can create/delete zones with minimum 2 clients - **API layer**
- **FR4 (partial)**: Zone stores and shares DSP settings - **Exposure via API**

### Testing Checklist

Per project-context.md:
- Tests in `backend/tests/test_api_multiroom.py`
- Test zone creation with valid 2+ clients
- Test zone creation with < 2 clients (expect 400)
- Test zone deletion
- Test zone retrieval (single and list)
- Test zone update (name change)

### References

- [Source: _bmad-output/planning-artifacts/architecture.md - "API Design" section]
- [Source: _bmad-output/planning-artifacts/epics.md - Story 2.2]
- [Source: _bmad-output/planning-artifacts/prd-multiroom-dsp.md - FR3, FR4]
- [Source: _bmad-output/implementation-artifacts/2-1-define-zone-model.md - Previous story]
- [Source: _bmad-output/project-context.md - Critical implementation rules]
- [Source: backend/core/multiroom/registry.py - Existing zone methods (lines 369-718)]
- [Source: backend/api/multiroom.py - Current router structure]
- [Source: backend/api/models.py - ZoneCreate, ZoneUpdate, ZoneResponse models]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

None - Implementation completed successfully without issues.

### Completion Notes List

- ✅ **Task 1**: Verified all zone CRUD methods already exist in `ClientRegistryService` (lines 369-752 in registry.py):
  - `create_zone()`, `delete_zone()`, `update_zone()`, `get_zone()`, `get_all_zones()`
  - `add_client_to_zone()`, `remove_client_from_zone()`, `set_zone_clients()`
  - `zone_to_enriched_dict()` for computed fields
  - WebSocket events (`zone_created`, `zone_updated`, `zone_deleted`) via `_emit_event()`
  - Persistence to settings.json via `_persist_zones()`

- ✅ **Task 2**: Added 5 zone API endpoints to `backend/api/multiroom.py`:
  - `GET /api/multiroom/zones` - List all zones with enriched data
  - `GET /api/multiroom/zones/{zone_id}` - Get specific zone
  - `POST /api/multiroom/zones` - Create zone (generates UUID, validates 2+ clients)
  - `PATCH /api/multiroom/zones/{zone_id}` - Update zone name
  - `DELETE /api/multiroom/zones/{zone_id}` - Delete zone

- ✅ **Task 3**: All zone responses use `zone_to_enriched_dict()` for computed fields:
  - `online_client_count`: Count of online clients in zone
  - `has_subwoofer`: Whether zone has a subwoofer speaker
  - `crossover_enabled`: Whether crossover is active (subwoofer + online clients)

- ✅ **Task 4**: Added 18 new unit tests for zone API endpoints in `test_api_multiroom.py`:
  - `TestGetZones`: 3 tests (returns all, enriched fields, empty list)
  - `TestGetZoneById`: 2 tests (success, 404)
  - `TestCreateZone`: 6 tests (success, enriched, UUID, validation errors)
  - `TestUpdateZone`: 4 tests (name update, enriched, 404, empty request)
  - `TestDeleteZone`: 3 tests (success, 404, registry removal)

- ✅ **Task 5**: All tests pass:
  - `test_api_multiroom.py`: 59 tests passed (5 new XSS validation tests added during code review)
  - `test_core_multiroom.py`: 118 tests passed
  - Full backend suite: 892 tests passed

- ✅ **Code Review Fixes Applied**:
  - Added XSS validation to `ZoneCreate.name` and `ZoneUpdate.name` (alphanumeric, spaces, hyphens, French accents)
  - Added deprecation notices to `/api/registry/zones` endpoints (superseded by `/api/multiroom/zones`)
  - Updated docstring in `multiroom.py` to clarify this is the canonical zone API
  - Fixed File List to correctly list `backend/api/multiroom.py` as "Added" (new file, not modified)
  - Added 5 new tests for XSS validation

### File List

**Added:**
- `backend/api/multiroom.py` - New router with zone CRUD endpoints (GET/POST/PATCH/DELETE /api/multiroom/zones)
- `backend/tests/test_api_multiroom.py` - 54 unit tests for client and zone API endpoints

**Modified:**
- `backend/api/models.py` - Contains ZoneCreate, ZoneUpdate, ZoneResponse Pydantic models (from Story 2.1)
- `backend/api/registry.py` - Added deprecation notices to zone endpoints (superseded by /api/multiroom/zones)
- `backend/main.py` - Router registration for multiroom endpoints

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-01-18 | Added zone CRUD API endpoints to multiroom router | Claude Opus 4.5 |
| 2026-01-18 | Added 54 unit tests for zone API endpoints | Claude Opus 4.5 |
| 2026-01-18 | Code review: Added XSS validation to zone name fields | Claude Opus 4.5 |
| 2026-01-18 | Code review: Added deprecation notices to /api/registry/zones endpoints | Claude Opus 4.5 |
| 2026-01-18 | Code review: Updated File List to reflect actual changes | Claude Opus 4.5 |

