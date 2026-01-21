# Story 2.4: API Endpoints for Zone Management

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **frontend application**,
I want **REST API endpoints to create, manage, and delete zones**,
So that **I can provide zone management functionality in the UI**.

## Acceptance Criteria

1. **AC1: GET /api/multiroom/zones - List All Zones**
   - **Given** ClientRegistryService zone methods are implemented
   - **When** I call `GET /api/multiroom/zones`
   - **Then** I receive a list of all zones with their members and dsp_settings
   - **And** each zone includes enriched data (online_client_count, has_subwoofer, crossover_enabled)

2. **AC2: POST /api/multiroom/zones - Create Zone**
   - **Given** valid zone data
   - **When** I call `POST /api/multiroom/zones` with `{"name": "Salon", "client_ids": ["mac1", "mac2"]}`
   - **Then** a new zone is created and returned with its UUID
   - **And** a WebSocket event `zone_created` is broadcast

3. **AC3: DELETE /api/multiroom/zones/{zone_id} - Delete Zone**
   - **Given** a valid zone_id
   - **When** I call `DELETE /api/multiroom/zones/{zone_id}`
   - **Then** the zone is deleted, clients become standalone
   - **And** a WebSocket event `zone_deleted` is broadcast

4. **AC4: POST /api/multiroom/zones/{zone_id}/clients - Add Client to Zone**
   - **Given** a valid zone_id
   - **When** I call `POST /api/multiroom/zones/{zone_id}/clients` with `{"mac_id": "new_client_mac"}`
   - **Then** the client joins the zone with DSP adoption (FR15)
   - **And** a WebSocket event `zone_updated` is broadcast

5. **AC5: DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id} - Remove Client**
   - **Given** a valid zone_id and client mac_id
   - **When** I call `DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id}`
   - **Then** the client leaves the zone with DSP retention (FR14)
   - **And** if zone < 2 clients remain, zone is deleted
   - **And** WebSocket event `zone_updated` or `zone_deleted` is broadcast

6. **AC6: Error Handling**
   - **Given** any zone endpoint
   - **When** zone_id not found → returns 404 with meaningful message
   - **When** client not found or validation error → returns 400 with detail
   - **When** invalid request body → returns 422 (Pydantic validation)

## Tasks / Subtasks

- [x] **Task 1: GET /api/multiroom/zones endpoint** (AC: #1)
  - [x] Returns list of all zones with enriched data
  - [x] Includes online_client_count, has_subwoofer, crossover_enabled computed fields
  - [x] Empty zones returns `{"zones": []}`

- [x] **Task 2: GET /api/multiroom/zones/{zone_id} endpoint** (AC: #1)
  - [x] Returns specific zone with enriched data
  - [x] Returns 404 for unknown zone_id

- [x] **Task 3: POST /api/multiroom/zones endpoint** (AC: #2)
  - [x] Generates UUID for zone_id
  - [x] Validates minimum 2 clients
  - [x] Validates all client mac_ids exist
  - [x] Returns 400 for validation errors
  - [x] Broadcasts WebSocket event on success

- [x] **Task 4: PATCH /api/multiroom/zones/{zone_id} endpoint** (AC: #2)
  - [x] Updates zone name (partial update)
  - [x] Returns 404 for unknown zone_id
  - [x] Broadcasts WebSocket event on success

- [x] **Task 5: DELETE /api/multiroom/zones/{zone_id} endpoint** (AC: #3)
  - [x] Deletes zone, clients become standalone with DSP retention
  - [x] Returns 404 for unknown zone_id
  - [x] Broadcasts WebSocket event on success

- [x] **Task 6: POST /api/multiroom/zones/{zone_id}/clients endpoint** (AC: #4)
  - [x] Adds client to zone, DSP adoption (FR15)
  - [x] Removes client from current zone if in one
  - [x] Returns 404 for unknown zone, 400 for unknown client
  - [x] Returns 400 if client already in zone
  - [x] Broadcasts WebSocket event on success

- [x] **Task 7: DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id} endpoint** (AC: #5)
  - [x] Removes client from zone, DSP retention (FR14)
  - [x] Deletes zone if < 2 clients remain
  - [x] Returns 404 for unknown zone, 400 for client not in zone
  - [x] Broadcasts WebSocket event on success

- [x] **Task 8: Unit tests** (AC: #6)
  - [x] TestGetZones - list all zones
  - [x] TestGetZoneById - get specific zone
  - [x] TestCreateZone - create zone with validation
  - [x] TestUpdateZone - update zone name
  - [x] TestDeleteZone - delete zone
  - [x] TestAddClientToZone - add client with error cases
  - [x] TestRemoveClientFromZone - remove client with zone deletion edge case
  - [x] TestZoneAddClientModel - Pydantic model validation

## Dev Notes

### Critical Discovery: ALREADY IMPLEMENTED

**All endpoints and tests for this story are ALREADY IMPLEMENTED** in previous work:

The zone API endpoints were implemented as part of Story 2-2 (Zone CRUD) and Story 2-3 (Zone Membership):
- **Story 2-2**: Added `GET /zones`, `POST /zones`, `PATCH /zones/{id}`, `DELETE /zones/{id}`
- **Story 2-3**: Added `POST /zones/{id}/clients` and `DELETE /zones/{id}/clients/{mac_id}`

### What Already Exists

**Backend API (`backend/api/multiroom.py`):**
- Lines 163-206: `GET /api/multiroom/zones` and `GET /api/multiroom/zones/{zone_id}`
- Lines 208-242: `POST /api/multiroom/zones` (create zone)
- Lines 244-280: `PATCH /api/multiroom/zones/{zone_id}` (update zone)
- Lines 282-316: `DELETE /api/multiroom/zones/{zone_id}` (delete zone)
- Lines 320-371: `POST /api/multiroom/zones/{zone_id}/clients` (add client, FR15)
- Lines 373-431: `DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id}` (remove client, FR14)

**Pydantic Models (`backend/api/models.py`):**
- `ZoneCreate` (lines 301-328): Validates name, min 2 clients, XSS protection
- `ZoneUpdate` (lines 331-345): Optional name update
- `ZoneResponse` (lines 348-353): Response format
- `ZoneAddClient` (lines 356-363): Client MAC validation

**Service Layer (`backend/core/multiroom/registry.py`):**
- `create_zone()` (lines 371-428)
- `delete_zone()` (lines 430-463)
- `update_zone()` (lines 466-493)
- `add_client_to_zone()` (lines 495-540)
- `remove_client_from_zone()` (lines 542-603)
- `set_zone_clients()` (lines 605-674)
- `zone_to_enriched_dict()` (lines 720-752)

**Unit Tests (`backend/tests/test_api_multiroom.py`):**
- TestGetZones (lines 631-667)
- TestGetZoneById (lines 670-692)
- TestCreateZone (lines 695-784)
- TestUpdateZone (lines 787-833)
- TestDeleteZone (lines 836-863)
- TestAddClientToZone (lines 1010-1079)
- TestRemoveClientFromZone (lines 1082-1179)
- TestZoneAddClientModel (lines 1182-1207)

**Frontend (`frontend/src/stores/clientRegistryStore.js`):**
- `addClientToZone(zoneId, macId)` (lines 458-468)
- `removeClientFromZone(zoneId, macId)` (lines 479-488)
- WebSocket handlers for zone events (lines 339-366)

### Project Structure Notes

**Files already containing this story's implementation:**
- `backend/api/multiroom.py` - Complete zone API endpoints
- `backend/api/models.py` - Pydantic models for zone operations
- `backend/core/multiroom/registry.py` - Zone service methods
- `backend/tests/test_api_multiroom.py` - Complete test coverage
- `frontend/src/stores/clientRegistryStore.js` - Frontend API integration

### API Endpoint Summary

| Method | Endpoint | Description | Response |
|--------|----------|-------------|----------|
| GET | `/api/multiroom/zones` | List all zones | `{"zones": [...]}` |
| GET | `/api/multiroom/zones/{zone_id}` | Get specific zone | Zone with enriched data |
| POST | `/api/multiroom/zones` | Create zone | `{"status": "success", "zone": {...}}` |
| PATCH | `/api/multiroom/zones/{zone_id}` | Update zone name | `{"status": "success", "zone": {...}}` |
| DELETE | `/api/multiroom/zones/{zone_id}` | Delete zone | `{"status": "success", "message": "..."}` |
| POST | `/api/multiroom/zones/{zone_id}/clients` | Add client to zone | `{"status": "success", "zone": {...}}` |
| DELETE | `/api/multiroom/zones/{zone_id}/clients/{mac_id}` | Remove client | Zone or deletion message |

### Enriched Zone Response Format

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Salon",
  "client_ids": ["local", "dc:a6:32:7e:d3:43"],
  "dsp_settings": {...},
  "online_client_count": 2,
  "has_subwoofer": false,
  "crossover_enabled": false
}
```

### WebSocket Events

Events broadcast by zone endpoints:
- `zone_created` - When zone is created via POST
- `zone_updated` - When zone is updated, client added/removed
- `zone_deleted` - When zone is deleted or has < 2 clients

Event structure:
```json
{
  "category": "registry",
  "type": "zone_updated",
  "data": {
    "zone_id": "uuid-...",
    "zone": {...}
  }
}
```

### FRs Covered by This Story

- **FR3**: User can create/delete zones with minimum 2 clients
- **FR4**: Zone stores and shares DSP settings among all member clients
- **FR14**: Client leaving a zone retains current DSP settings as standalone
- **FR15**: Client joining a zone adopts zone's DSP settings

### Error Handling Summary

| Error Case | HTTP Status | Response |
|------------|-------------|----------|
| Zone not found | 404 | `{"detail": "Zone 'id' not found"}` |
| Client not found | 400 | `{"detail": "Client 'mac' not found"}` |
| Client already in zone | 400 | `{"detail": "Client is already in zone"}` |
| Client not in zone | 400 | `{"detail": "Client is not in zone"}` |
| Invalid request body | 422 | Pydantic validation errors |
| Less than 2 clients | 422 | `{"detail": "At least 2 clients required"}` |

### Testing Requirements

Run tests with:
```bash
cd backend
python -m pytest tests/test_api_multiroom.py -v
```

All 76 tests pass, covering:
- Zone CRUD operations
- Zone membership operations (add/remove client)
- Pydantic model validation
- Error handling for all edge cases

### Architecture Compliance

Per architecture.md:
- MAC address format: with colons for storage (`dc:a6:32:7e:d3:43`), without for URLs
- Zone ID format: UUID
- API prefix: `/api/multiroom/zones/...`
- WebSocket events with explicit identifiers in `data` field
- Central service: `ClientRegistryService` is SSOT

### References

- [Source: backend/api/multiroom.py - Zone endpoints (lines 163-431)]
- [Source: backend/api/models.py - ZoneCreate, ZoneUpdate, ZoneAddClient (lines 301-363)]
- [Source: backend/core/multiroom/registry.py - Zone service methods (lines 371-752)]
- [Source: backend/tests/test_api_multiroom.py - Zone tests (lines 631-1207)]
- [Source: frontend/src/stores/clientRegistryStore.js - API integration (lines 458-488)]
- [Source: _bmad-output/planning-artifacts/architecture.md - Zone Management section]
- [Source: _bmad-output/planning-artifacts/epics.md - Story 2.4]
- [Source: _bmad-output/project-context.md - Critical implementation rules]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

None - all work was already completed in Stories 2-2 and 2-3.

### Completion Notes List

1. **All endpoints already exist** - Story 2-2 and 2-3 implemented the complete zone API
2. **76 tests already pass** - Comprehensive test coverage exists
3. **Frontend integration complete** - `clientRegistryStore.js` has all methods
4. **WebSocket events working** - Zone events are properly broadcast and handled
5. **This story is VERIFICATION ONLY** - No new code needed, mark as complete
6. **Verification complete (2026-01-18)** - All 76 zone API tests pass, 914 total tests pass with no regressions
7. **Code review fixes (2026-01-18)** - Staged untracked files to git, fixed frontend API endpoints to use canonical /api/multiroom/zones, fixed comment typo in models.py

### Change Log

- **2026-01-18**: Story verified complete - All zone API endpoints and tests confirmed working from Stories 2-2 and 2-3
- **2026-01-18**: Code review fixes applied - Staged 5 untracked files to git, updated frontend to use canonical /api/multiroom/zones endpoints, fixed comment typo

### File List

**Files staged for commit (code review fixes):**
- `backend/api/multiroom.py` - Zone API endpoints (was untracked, now staged)
- `backend/api/models.py` - Fixed comment path typo
- `backend/tests/test_api_multiroom.py` - Zone API test suite (was untracked, now staged)
- `backend/tests/test_api_registry.py` - Registry API tests (was untracked, now staged)
- `backend/tests/integration/test_reconnection_scenarios.py` - Integration tests (was untracked, now staged)
- `backend/tests/integration/test_snapcast_detection.py` - Integration tests (was untracked, now staged)
- `frontend/src/stores/clientRegistryStore.js` - Updated to use canonical /api/multiroom/zones endpoints

**Previously implemented (no changes):**
- `backend/core/multiroom/registry.py` - Zone service layer
