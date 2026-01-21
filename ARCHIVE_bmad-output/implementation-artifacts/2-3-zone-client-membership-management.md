# Story 2.3: Zone Client Membership Management

Status: completed

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user**,
I want **clients to properly handle DSP settings when joining or leaving zones**,
So that **my audio settings are preserved correctly during zone transitions**.

## Acceptance Criteria

1. **AC1: Client Joins Zone - DSP Adoption (FR15)**
   - **Given** a standalone client with custom dsp_settings
   - **When** the client joins a zone
   - **Then** the client's dsp_settings is overwritten with zone.dsp_settings
   - **And** the client's zone_id is set to the zone's ID
   - **And** a WebSocket event `zone_updated` is broadcast

2. **AC2: Client Leaves Zone - DSP Retention (FR14)**
   - **Given** a client is member of a zone
   - **When** the client leaves the zone
   - **Then** the client retains a copy of the current zone.dsp_settings as its standalone dsp_settings
   - **And** the client's zone_id is set to None
   - **And** if zone has less than 2 clients remaining, the zone persists (clients may be offline)
   - **And** a WebSocket event `zone_updated` is broadcast

3. **AC3: Service Methods Exist**
   - **Given** ClientRegistryService
   - **When** I verify membership methods
   - **Then** both `add_client_to_zone(mac_id, zone_id)` and `remove_client_from_zone(mac_id)` exist
   - **And** both methods handle the DSP transition logic as specified above

4. **AC4: API Endpoints for Zone Membership**
   - **Given** ClientRegistryService membership methods exist
   - **When** I expose REST API endpoints
   - **Then** the following endpoints are available:
     - `POST /api/multiroom/zones/{zone_id}/clients` with `{"mac_id": "client_mac"}` - Add client to zone
     - `DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id}` - Remove client from zone

5. **AC5: Frontend Zone Membership Management**
   - **Given** I open ZoneEdit.vue to edit a zone
   - **When** I add or remove clients from the zone
   - **Then** the API is called with proper endpoints
   - **And** the UI updates immediately via WebSocket events

6. **AC6: Unit Tests for Zone Membership**
   - **Given** the zone membership methods and API endpoints
   - **When** I run the test suite
   - **Then** tests cover: client join DSP adoption, client leave DSP retention, edge cases

## Tasks / Subtasks

- [x] **Task 1: Verify existing ClientRegistryService membership methods** (AC: #3)
  - [x] Confirm `add_client_to_zone(zone_id, mac_id)` handles DSP adoption (line 495-540)
  - [x] Confirm `remove_client_from_zone(zone_id, mac_id)` handles DSP retention (line 542-603)
  - [x] Verify WebSocket events are emitted (`zone_updated`)
  - [x] Verify persistence to `settings.json` works correctly

- [x] **Task 2: Add zone membership endpoints to multiroom router** (AC: #4)
  - [x] Add `POST /api/multiroom/zones/{zone_id}/clients` endpoint
  - [x] Add `DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id}` endpoint
  - [x] Handle error cases (zone not found, client not found, client already in zone)
  - [x] Return enriched zone response on success

- [x] **Task 3: Update frontend ZoneEdit.vue** (AC: #5)
  - [x] Add UI to add client to zone (dropdown or list selection)
  - [x] Add UI to remove client from zone (remove button per client)
  - [x] Integrate with new API endpoints
  - [x] Ensure WebSocket events update UI reactively via multiroomStore

- [x] **Task 4: Add/verify unit tests** (AC: #6)
  - [x] Test client join zone - DSP overwritten
  - [x] Test client leave zone - DSP retained as standalone
  - [x] Test API endpoints return correct responses
  - [x] Test edge case: client already in another zone (should be removed first)
  - [x] Test edge case: zone deletion when < 2 clients after removal

- [x] **Task 5: Run test suite and verify** (AC: all)
  - [x] Run `python -m pytest backend/tests/test_api_multiroom.py -v`
  - [x] Run `python -m pytest backend/tests/test_core_multiroom.py -v`
  - [x] Verify all tests pass

## Dev Notes

### Critical Discovery: Service Methods Already Exist

**The zone membership methods are ALREADY FULLY IMPLEMENTED** in `ClientRegistryService` (backend/core/multiroom/registry.py):

```python
# Already implemented methods (lines 495-603):
async def add_client_to_zone(self, zone_id: str, mac_id: str) -> bool:
    """
    Add a client to a zone. Client's DSP is replaced by zone's.
    - Removes client from current zone if in one
    - Appends client to zone.client_ids
    - Sets client.zone_id = zone_id
    - Deletes standalone DSP (client now uses zone's DSP)
    """

async def remove_client_from_zone(self, zone_id: str, mac_id: str) -> bool:
    """
    Remove a client from a zone. Client keeps current DSP as standalone.
    - Sets client.zone_id = None
    - Copies zone.dsp_settings to standalone_dsp[mac_id]
    - If zone < 2 clients: deletes zone, remaining clients also become standalone
    """

async def set_zone_clients(self, zone_id: str, client_ids: List[str]) -> Optional[Zone]:
    """
    Set the complete client list for a zone (replaces all members in one operation).
    - Handles DSP transitions for clients leaving and joining
    """
```

### What Actually Needs to Be Done

**The ONLY work required is:**
1. Adding REST API endpoints to expose existing service methods
2. Updating frontend ZoneEdit.vue to use the new endpoints
3. Adding unit tests for the API layer

### Implementation Pattern (Follow Existing Code)

The router uses dependency injection pattern. Add to `backend/api/multiroom.py`:

```python
@router.post("/zones/{zone_id}/clients", status_code=200)
async def add_client_to_zone(zone_id: str, request: ZoneAddClient):
    """Add a client to a zone. Client's DSP is replaced by zone's."""
    zone = registry_service.get_zone(zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail=f"Zone '{zone_id}' not found")

    success = await registry_service.add_client_to_zone(zone_id, request.mac_id)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to add client '{request.mac_id}' to zone")

    zone = registry_service.get_zone(zone_id)
    return {"status": "success", "zone": registry_service.zone_to_enriched_dict(zone)}

@router.delete("/zones/{zone_id}/clients/{mac_id}")
async def remove_client_from_zone(zone_id: str, mac_id: str):
    """Remove a client from a zone. Client keeps zone DSP as standalone."""
    zone = registry_service.get_zone(zone_id)
    if not zone:
        raise HTTPException(status_code=404, detail=f"Zone '{zone_id}' not found")

    success = await registry_service.remove_client_from_zone(zone_id, mac_id)
    if not success:
        raise HTTPException(status_code=400, detail=f"Failed to remove client '{mac_id}' from zone")

    # Zone may have been deleted if < 2 clients remain
    zone = registry_service.get_zone(zone_id)
    if zone:
        return {"status": "success", "zone": registry_service.zone_to_enriched_dict(zone)}
    else:
        return {"status": "success", "message": f"Client removed, zone '{zone_id}' deleted (< 2 clients)"}
```

### Pydantic Model for Request Body

Add to `backend/api/models.py`:
```python
class ZoneAddClient(BaseModel):
    """Request body for adding a client to a zone."""
    mac_id: str = Field(..., description="MAC address of client to add")
```

### Frontend Integration (ZoneEdit.vue)

The `ZoneEdit.vue` component needs:
1. **Add Client Section**: Dropdown showing available standalone clients (not in any zone)
2. **Remove Client Action**: Button/icon per client row to remove from zone

```javascript
// In multiroomStore.js - add methods:
async addClientToZone(zoneId, macId) {
  const response = await fetch(`/api/multiroom/zones/${zoneId}/clients`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mac_id: macId })
  })
  // WebSocket event will update state automatically
}

async removeClientFromZone(zoneId, macId) {
  await fetch(`/api/multiroom/zones/${zoneId}/clients/${macId}`, {
    method: 'DELETE'
  })
  // WebSocket event will update state automatically
}
```

### DSP Transition Flow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                    CLIENT JOINS ZONE (FR15)                  │
├─────────────────────────────────────────────────────────────┤
│ 1. Client has standalone_dsp[mac_id] = {custom settings}    │
│ 2. add_client_to_zone(zone_id, mac_id) called               │
│ 3. Client.zone_id = zone_id                                 │
│ 4. DELETE standalone_dsp[mac_id] (zone DSP takes over)      │
│ 5. Client now uses zone.dsp_settings (shared with others)   │
│ 6. WebSocket: zone_updated event broadcast                  │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    CLIENT LEAVES ZONE (FR14)                 │
├─────────────────────────────────────────────────────────────┤
│ 1. Client is in zone with zone.dsp_settings                 │
│ 2. remove_client_from_zone(zone_id, mac_id) called          │
│ 3. Client.zone_id = None                                    │
│ 4. standalone_dsp[mac_id] = COPY of zone.dsp_settings       │
│ 5. Client now has independent DSP settings                  │
│ 6. If zone < 2 clients: zone deleted, remaining standalone  │
│ 7. WebSocket: zone_updated (or zone_deleted) event          │
└─────────────────────────────────────────────────────────────┘
```

### WebSocket Events (Already Implemented)

The service methods already emit these events via `_emit_event()`:
- `zone_updated` - When client is added/removed from zone
- `zone_deleted` - When zone is deleted (< 2 clients remain)

Event structure:
```json
{
  "category": "registry",
  "type": "zone_updated",
  "data": {
    "zone_id": "uuid-...",
    "zone": {
      "id": "uuid-...",
      "name": "Salon",
      "client_ids": ["local", "aa:bb:cc:dd:ee:ff"],
      "dsp_settings": {...},
      "online_client_count": 2,
      "has_subwoofer": false,
      "crossover_enabled": false
    }
  }
}
```

### Previous Story Intelligence (2.2)

From Story 2-2:
- Zone CRUD API endpoints already exist at `/api/multiroom/zones/...`
- `ZoneCreate`, `ZoneUpdate`, `ZoneResponse` Pydantic models in `api/models.py`
- `zone_to_enriched_dict()` adds computed fields (online_client_count, has_subwoofer, crossover_enabled)
- XSS validation added to zone name fields
- 59 tests pass for zone API (892 total backend tests)

### Git Intelligence

Recent relevant commits:
- `fa167e4`: Client deletion and offline handling improvements
- `4e3aa94`: Fix to prevent premature zone deletion when removing client
- `14c47ed`: Consolidated Pinia stores, eliminated state duplication

The fix in `4e3aa94` specifically addressed zone deletion edge cases - follow this pattern.

### Project Structure Notes

**Files to modify:**
- `backend/api/multiroom.py` - Add zone membership endpoints
- `backend/api/models.py` - Add `ZoneAddClient` model
- `frontend/src/components/settings/categories/multiroom/ZoneEdit.vue` - Add/remove UI
- `frontend/src/stores/multiroomStore.js` - Add API methods

**Files to reference:**
- `backend/core/multiroom/registry.py` - Service methods (lines 495-603)
- `backend/tests/test_api_multiroom.py` - Add membership tests
- `backend/tests/test_core_multiroom.py` - Service method tests exist

### FRs Covered by This Story

- **FR14**: Client leaving a zone retains current DSP settings as standalone
- **FR15**: Client joining a zone adopts zone's DSP settings (overwrites current)

### Testing Checklist

Per project-context.md:
- Tests in `backend/tests/test_api_multiroom.py`
- Test client joins zone - DSP adoption
- Test client leaves zone - DSP retention
- Test API endpoint responses
- Test edge case: zone deletion when < 2 clients after removal
- Test edge case: client already in another zone

### Architecture Compliance

Per architecture.md:
- MAC address format: with colons for storage (`dc:a6:32:7e:d3:43`), without for URLs (`dca6327ed343`)
- Zone ID format: UUID
- API prefix: `/api/multiroom/zones/...`
- WebSocket events with explicit identifiers in `data` field
- Central service: `ClientRegistryService` is SSOT for all client/zone state

### Error Handling

| Error Case | Expected Response |
|------------|-------------------|
| Zone not found | 404 with detail message |
| Client not found | 400 with detail message |
| Client already in zone | 400 or no-op (service returns False) |
| Client not in zone (remove) | 400 with detail message |

### References

- [Source: _bmad-output/planning-artifacts/architecture.md - "Zone Management" section]
- [Source: _bmad-output/planning-artifacts/epics.md - Story 2.3, FR14, FR15]
- [Source: _bmad-output/planning-artifacts/prd-multiroom-dsp.md - FR14, FR15]
- [Source: _bmad-output/implementation-artifacts/2-2-implement-zone-crud-in-client-registry-service.md - Previous story]
- [Source: _bmad-output/project-context.md - Critical implementation rules]
- [Source: backend/core/multiroom/registry.py - Zone membership methods (lines 495-603)]
- [Source: backend/api/multiroom.py - Current zone endpoints structure]
- [Source: backend/api/models.py - Pydantic models]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

None - no issues encountered during implementation.

### Completion Notes List

1. **Service methods already existed** - `add_client_to_zone()` and `remove_client_from_zone()` were fully implemented in `ClientRegistryService`

2. **API endpoints added** - Added `POST /api/multiroom/zones/{zone_id}/clients` and `DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id}` to `multiroom.py`

3. **Pydantic model added** - `ZoneAddClient` model added to `models.py` for request validation

4. **Frontend updated** - Updated `clientRegistryStore.js` to use new endpoints and updated `ZoneEdit.vue` to use `registryStore.addClientToZone()` and `registryStore.removeClientFromZone()`

5. **Unit tests added** - 17 new tests for zone membership endpoints (TestAddClientToZone, TestRemoveClientFromZone, TestZoneAddClientModel classes)

6. **All 76 tests pass** for `test_api_multiroom.py`

### File List

**Modified:**
- `backend/api/multiroom.py` - Added zone membership endpoints (+85 lines)
- `backend/api/models.py` - Added ZoneAddClient model (+10 lines)
- `frontend/src/stores/clientRegistryStore.js` - Updated API endpoints for zone membership
- `frontend/src/components/settings/categories/multiroom/ZoneEdit.vue` - Updated to use new registryStore methods
- `backend/tests/test_api_multiroom.py` - Added 17 new unit tests (+343 lines)

### Code Review Record

**Reviewer:** Claude Opus 4.5 (claude-opus-4-5-20251101)
**Date:** 2026-01-18

**Issues Found:** 0 High, 3 Medium, 2 Low
**Issues Fixed:** 5/5

**Fixes Applied:**

1. **[MEDIUM] ZoneEdit.vue consistency** - Changed `handleCreate()` to use `registryStore.createZone()` instead of `dspStore.linkClients()` for architectural consistency with edit operations

2. **[MEDIUM] Missing integration test for DSP retention** - Added `test_zone_dsp_retained_on_leave()` to verify FR14/AC2 behavior (zone DSP copied to standalone when client leaves)

3. **[MEDIUM] Dead code cleanup** - Removed unused `zone_client_added`/`zone_client_removed` event handlers from clientRegistryStore.js (backend only emits `zone_updated`)

4. **[LOW] Incorrect story reference** - Fixed comment referencing "Story 2-4" to remove incorrect reference

5. **[LOW] Documentation** - Added clarifying comment about WebSocket event handling

**Files Modified During Review:**
- `frontend/src/components/settings/categories/multiroom/ZoneEdit.vue` - Fixed handleCreate() to use registryStore
- `frontend/src/stores/clientRegistryStore.js` - Removed dead code, fixed comment
- `backend/tests/test_core_multiroom.py` - Added DSP retention integration test

**Test Results After Review:**
- `test_api_multiroom.py`: 76 passed
- `test_core_multiroom.py`: 119 passed (including new test)
- Total: 195 passed

