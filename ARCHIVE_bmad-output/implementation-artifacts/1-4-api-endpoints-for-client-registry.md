# Story 1.4: API Endpoints for Client Registry

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **frontend application**,
I want **REST API endpoints to retrieve and update client information**,
So that **I can display and manage clients in the UI**.

## Acceptance Criteria

1. **AC1: Get All Clients Endpoint**
   - **Given** ClientRegistryService is implemented (Story 1-2: done)
   - **When** I call `GET /api/multiroom/clients`
   - **Then** I receive a list of all registered clients with their current state
   - **And** each client includes runtime `online` status
   - **And** response format is `{"clients": [...]}`

2. **AC2: Update Client Name**
   - **Given** a valid client mac_id
   - **When** I call `PATCH /api/multiroom/clients/{mac_id}` with `{"name": "New Name"}`
   - **Then** the client name is updated in the registry
   - **And** changes are persisted to settings.json
   - **And** a WebSocket event `client_updated` is broadcast

3. **AC3: Update Client Speaker Type**
   - **Given** a valid client mac_id
   - **When** I call `PATCH /api/multiroom/clients/{mac_id}` with `{"speaker_type": "subwoofer"}`
   - **Then** the client speaker_type is updated
   - **And** changes are persisted and broadcast
   - **And** speaker_type is validated (must be one of: satellite, bookshelf, tower, subwoofer)

4. **AC4: Invalid MAC ID Handling**
   - **Given** an invalid mac_id format in URL
   - **When** I call any client endpoint
   - **Then** I receive a 404 Not Found if client doesn't exist
   - **And** error response includes meaningful message

5. **AC5: API Prefix Compliance**
   - **Given** the architecture requires `/api/multiroom/` prefix
   - **When** implementing client endpoints
   - **Then** ALL client endpoints use `/api/multiroom/clients/...` prefix
   - **And** existing `/api/registry/...` endpoints are aliased for backward compatibility

## Tasks / Subtasks

- [x] **Task 1: Verify existing API endpoints** (AC: #1, #2, #3, #4)
  - [x] Read `backend/api/registry.py` to understand existing implementation
  - [x] Document existing endpoints and their behavior
  - [x] Identify gaps between existing implementation and acceptance criteria

- [x] **Task 2: Create `/api/multiroom/` router** (AC: #5)
  - [x] Create `backend/api/multiroom.py` if not exists
  - [x] Implement endpoints under `/api/multiroom/clients/` prefix per architecture
  - [x] Ensure parity with existing `/api/registry/clients/` functionality

- [x] **Task 3: Implement PATCH endpoint** (AC: #2, #3)
  - [x] Implement `PATCH /api/multiroom/clients/{mac_id}` accepting partial updates
  - [x] Support `name` and `speaker_type` fields in request body
  - [x] Validate speaker_type against allowed values
  - [x] Return updated client with `online` status

- [x] **Task 4: Add validation and error handling** (AC: #4)
  - [x] Return 404 for non-existent clients with clear error message
  - [x] Return 400 for invalid speaker_type with allowed values in error
  - [x] Validate MAC format if required

- [x] **Task 5: Register router in main.py** (AC: #5)
  - [x] Import and register `/api/multiroom/` router in `backend/main.py`
  - [x] Ensure both `/api/registry/` and `/api/multiroom/` routes work

- [x] **Task 6: Write unit tests** (AC: all)
  - [x] Test `GET /api/multiroom/clients` returns all clients with online status
  - [x] Test `PATCH /api/multiroom/clients/{mac_id}` updates name
  - [x] Test `PATCH /api/multiroom/clients/{mac_id}` updates speaker_type
  - [x] Test 404 for non-existent client
  - [x] Test 400 for invalid speaker_type

## Dev Notes

### Implementation Status: MOSTLY COMPLETE

Based on codebase analysis, API endpoints for client registry are **already implemented** at `/api/registry/`. The main work is:

1. **Verify** existing implementation meets all acceptance criteria
2. **Create** `/api/multiroom/` alias per architecture document
3. **Add PATCH endpoint** if `PUT` doesn't satisfy partial update requirements
4. **Add tests** to confirm behavior

### Existing Implementation Analysis

**Current endpoints in `backend/api/registry.py`:**

| Endpoint | Method | Description | AC Coverage |
|----------|--------|-------------|-------------|
| `/api/registry/clients` | GET | All clients with online status | AC1 |
| `/api/registry/clients/{mac_id}` | GET | Single client | - |
| `/api/registry/clients/{mac_id}` | PUT | Update name and/or speaker_type | AC2, AC3 |
| `/api/registry/clients/{mac_id}` | DELETE | Remove client | - |
| `/api/registry/clients/{mac_id}/type` | PUT | Update speaker_type (legacy) | AC3 |
| `/api/registry/clients/{mac_id}/online` | GET | Check online status | - |
| `/api/registry/clients/{mac_id}/zone` | GET | Get client's zone | - |

**Current request model (`ClientUpdateRequest`):**
```python
class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    speaker_type: Optional[str] = None
```

**Response helper (`_client_with_online`):**
```python
def _client_with_online(client):
    data = client.to_dict()
    data["online"] = client.online
    return data
```

### Architecture Compliance Gap

**Per architecture document section "API Design":**
> **Prefixes separated by domain:**
> - `/api/dsp/` - filters, compressor, loudness
> - `/api/volume/` - volume control
> - `/api/multiroom/` - clients, zones, configuration

The current implementation uses `/api/registry/` but architecture specifies `/api/multiroom/`. Options:

1. **Rename** existing router from `/api/registry` to `/api/multiroom` (breaking change)
2. **Create alias** - keep both paths working (recommended for backward compatibility)
3. **Document** that `/api/registry` is the canonical path (deviation from architecture)

**Recommendation**: Create `/api/multiroom/` router that delegates to existing `ClientRegistryService` methods, keeping `/api/registry/` for backward compatibility until frontend migration is complete.

### Previous Story Learnings

**From Story 1-1:**
- `Client.to_dict()` excludes runtime field `online` (must add separately in API)
- Speaker types: `satellite`, `bookshelf`, `tower`, `subwoofer`
- MAC format: with colons (`dc:a6:32:7e:d3:43`) or `"local"`

**From Story 1-2:**
- `ClientRegistryService` has all required methods
- Thread safety via `asyncio.Lock()`
- Persistence via `SettingsService.set_setting()`
- Events emitted via `_emit_event()`

**From Story 1-3:**
- WebSocket events use format: `{category: "registry", type: "...", data: {...}}`
- Events broadcast within 100ms (NFR2)

### WebSocket Event on Update

When client is updated, emit:
```json
{
  "category": "registry",
  "type": "client_updated",
  "data": {
    "mac_id": "dc:a6:32:7e:d3:43",
    "client": { /* full client state */ }
  }
}
```

This is already implemented in `ClientRegistryService.update_client()` which emits `RegistryEventType.CLIENT_UPDATED`.

### Project Structure Notes

**Files involved:**
- `backend/api/registry.py` - Existing client/zone API routes
- `backend/api/multiroom.py` - New router for `/api/multiroom/` (to create)
- `backend/main.py` - Router registration
- `backend/core/multiroom/registry.py` - ClientRegistryService (from Story 1-2)
- `backend/core/multiroom/models.py` - Client, SpeakerType (from Story 1-1)

**Test files:**
- `backend/tests/test_api_registry.py` - API endpoint tests (exists but may need updates)
- `backend/tests/test_core_multiroom.py` - Service tests

### Pydantic Model Pattern

Per project conventions, API uses Pydantic models:
```python
from pydantic import BaseModel
from typing import Optional

class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    speaker_type: Optional[str] = None

# Validation can be added via Pydantic:
class ClientUpdateRequest(BaseModel):
    name: Optional[str] = None
    speaker_type: Optional[Literal['satellite', 'bookshelf', 'tower', 'subwoofer']] = None
```

### PUT vs PATCH Consideration

Current implementation uses `PUT` which semantically means "replace entire resource". The acceptance criteria mention `PATCH` for partial updates.

Options:
1. Keep `PUT` with optional fields (current approach - works fine for partial updates)
2. Add `PATCH` endpoint that behaves identically to `PUT`
3. Change `PUT` to `PATCH` (minor breaking change)

**Recommendation**: Add `PATCH` as alias to `PUT` for semantic correctness while maintaining backward compatibility.

### References

- [Source: _bmad-output/planning-artifacts/architecture.md - Section "API Design"]
- [Source: _bmad-output/planning-artifacts/epics.md - Story 1.4]
- [Source: _bmad-output/implementation-artifacts/1-2-implement-client-registry-service.md]
- [Source: _bmad-output/implementation-artifacts/1-3-integrate-snapcast-client-detection.md]
- [Source: backend/api/registry.py - Existing implementation]
- [Source: backend/core/multiroom/registry.py - ClientRegistryService]

### Git Intelligence

Recent commits relevant to this story:
- `fa167e4`: Added client deletion and offline handling - shows API pattern for delete
- `14c47ed`: Frontend store consolidation - frontend expects certain API responses
- `9a31e2f`: Volume sync work - shows how multiroom service interacts with API

### Testing Checklist

Per project-context.md:
- Use `@pytest.mark.asyncio` for async tests
- Mock services for unit tests
- Test file: `backend/tests/test_api_registry.py` or new `test_api_multiroom.py`
- Use `httpx.AsyncClient` with FastAPI `TestClient` for API tests

### Code Patterns

**Router creation pattern (from existing code):**
```python
def create_multiroom_router(registry_service):
    router = APIRouter(prefix="/api/multiroom", tags=["multiroom"])

    @router.get("/clients")
    async def get_clients():
        clients = registry_service.get_all_clients()
        return {"clients": [_client_with_online(c) for c in clients.values()]}

    return router
```

**Registration in main.py:**
```python
# In create_app() or similar
registry_service = get_service('client_registry_service')
app.include_router(create_multiroom_router(registry_service))
```

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

- All 16 new tests pass for `/api/multiroom/` endpoints
- All 14 existing `/api/registry/` tests pass (no regression)
- Full backend test suite: 694 tests pass

### Completion Notes List

- **Task 1**: Analyzed existing `/api/registry/` implementation - identified gaps for AC5 (prefix), AC2/AC3 (PATCH method), AC4 (validation)
- **Task 2-4**: Created `backend/api/multiroom.py` with `/api/multiroom/clients/` endpoints implementing:
  - GET /clients - returns all clients with `online` status (AC1)
  - GET /clients/{mac_id} - returns single client with 404 on not found (AC4)
  - PATCH /clients/{mac_id} - partial update for name/speaker_type (AC2, AC3)
  - PUT /clients/{mac_id} - alias for PATCH (backward compatibility)
  - Pydantic validation for speaker_type (satellite, bookshelf, tower, subwoofer)
- **Task 5**: Registered router in `backend/main.py` alongside existing `/api/registry/` router
- **Task 6**: Created comprehensive test suite in `backend/tests/test_api_multiroom.py` covering all ACs

### File List

**New files:**
- `backend/api/multiroom.py` - New multiroom router for /api/multiroom/clients/ endpoints
- `backend/tests/test_api_multiroom.py` - Unit tests for multiroom API (16 tests)
- `backend/tests/test_api_registry.py` - Unit tests for legacy /api/registry/ endpoints (14 tests)

**Modified files:**
- `backend/main.py` - Added import and registration of multiroom_router

### Change Log

- 2026-01-18: Implemented /api/multiroom/clients/ API endpoints per architecture specification (AC1-AC5)
- 2026-01-18: [Code Review] Added test_api_registry.py to File List, updated status to done

