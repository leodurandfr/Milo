# Story 4.7: API Endpoints for DSP

Status: completed

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a frontend application,
I want REST API endpoints to control DSP settings for clients and zones,
so that I can provide DSP control functionality in the UI.

## Acceptance Criteria

1. **AC1: Zone filter update** - Given DspService is implemented, when I call `PATCH /api/dsp/zone/{zone_id}/filter/{filter_id}` with `{"gain": 3.0}`, then the filter is updated for the zone and applied to ONLINE clients, and response includes updated dsp_settings and list of applied clients

2. **AC2: Zone compressor control** - Given a valid zone_id, when I call `PATCH /api/dsp/zone/{zone_id}/compressor` with `{"enabled": true, "threshold": -20}`, then compressor settings are updated and applied

3. **AC3: Zone loudness control** - Given a valid zone_id, when I call `PATCH /api/dsp/zone/{zone_id}/loudness` with `{"enabled": true}`, then loudness is enabled and applied

4. **AC4: Zone DSP bypass** - Given a valid zone_id, when I call `PATCH /api/dsp/zone/{zone_id}/enabled` with `{"enabled": false}`, then global DSP bypass is activated (except crossover)

5. **AC5: Zone preset loading** - Given a valid zone_id, when I call `POST /api/dsp/zone/{zone_id}/preset` with `{"preset": "Jazz"}`, then the preset is applied to the zone

6. **AC6: Standalone client endpoints** - Given a standalone client mac_id, when I call equivalent endpoints at `/api/dsp/client/{mac_id}/...`, then DSP changes are applied to the standalone client only

7. **AC7: Presets list** - Given I call `GET /api/dsp/presets`, then I receive list of available presets with their configurations

## Tasks / Subtasks

- [x] Task 1: Audit existing API endpoints completeness (AC: #1-#7)
  - [x] 1.1 Verify all zone endpoints exist in `backend/api/dsp.py`
  - [x] 1.2 Verify all client endpoints exist with proper proxy pattern
  - [x] 1.3 Document any missing endpoints from architecture.md specification
  - [x] 1.4 Verify response formats match architecture.md requirements

- [x] Task 2: Add missing zone-level DSP endpoints (AC: #1, #2, #3, #4)
  - [x] 2.1 Add `PATCH /api/dsp/zone/{zone_id}/filter/{filter_id}` if missing
  - [x] 2.2 Add `PATCH /api/dsp/zone/{zone_id}/compressor` if missing
  - [x] 2.3 Add `PATCH /api/dsp/zone/{zone_id}/loudness` if missing
  - [x] 2.4 Add `PATCH /api/dsp/zone/{zone_id}/enabled` if missing
  - [x] 2.5 All zone endpoints must propagate to ONLINE clients only

- [x] Task 3: Verify client proxy routes (AC: #6)
  - [x] 3.1 Verify `/api/dsp/client/{hostname}/filter/{filter_id}` works
  - [x] 3.2 Verify `/api/dsp/client/{hostname}/compressor` works
  - [x] 3.3 Verify `/api/dsp/client/{hostname}/loudness` works
  - [x] 3.4 Verify `/api/dsp/client/{hostname}/enabled` works
  - [x] 3.5 Verify OFFLINE clients are skipped gracefully

- [x] Task 4: Add frontend API integration (AC: #1-#6)
  - [x] 4.1 Update `dspStore.js` to use zone endpoints when zone is selected
  - [x] 4.2 Update `dspStore.js` to use client endpoints for standalone clients
  - [x] 4.3 Update `propagateToLinkedClients()` for filter/compressor/loudness
  - [x] 4.4 Verify `schemas/api.js` has all required Zod schemas

- [x] Task 5: Write integration tests (AC: #1-#7)
  - [x] 5.1 Test zone filter endpoint propagation
  - [x] 5.2 Test zone compressor endpoint propagation
  - [x] 5.3 Test zone loudness endpoint propagation
  - [x] 5.4 Test zone DSP enabled endpoint
  - [x] 5.5 Test client proxy routes for each DSP operation
  - [x] 5.6 Test presets list endpoint returns all 21 presets + Manual
  - [x] 5.7 Test OFFLINE client handling (graceful skip)

## Dev Notes

### CRITICAL: Existing Implementation Analysis

**IMPORTANT DISCOVERY**: The majority of endpoints specified in this story **ALREADY EXIST** in `backend/api/dsp.py`. This story is primarily about:
1. **Verification** of existing functionality against architecture.md
2. **Adding missing zone-level DSP propagation endpoints** (filter, compressor, loudness, enabled)
3. **Frontend integration** to use zone vs client endpoints appropriately
4. **Integration testing** for all acceptance criteria

### Existing API Endpoints (backend/api/dsp.py)

#### Local DSP Endpoints (EXIST)

| Endpoint | Method | Status | Lines |
|----------|--------|--------|-------|
| `/api/dsp/status` | GET | ✅ EXISTS | 130-141 |
| `/api/dsp/enabled` | GET/PUT | ✅ EXISTS | 78-126 |
| `/api/dsp/filters` | GET | ✅ EXISTS | 212-219 |
| `/api/dsp/filter/{filter_id}` | PUT | ✅ EXISTS | 253-302 |
| `/api/dsp/filter/{filter_id}` | DELETE | ✅ EXISTS | 304-319 |
| `/api/dsp/filter` | POST | ✅ EXISTS | 221-251 |
| `/api/dsp/reset` | POST | ✅ EXISTS | 321-334 |
| `/api/dsp/compressor` | GET/PUT | ✅ EXISTS | 560-590 |
| `/api/dsp/loudness` | GET/PUT | ✅ EXISTS | 594-622 |
| `/api/dsp/presets` | GET | ✅ EXISTS | 338-351 |
| `/api/dsp/preset/{preset_id}` | PUT | ✅ EXISTS | 353-368 |

#### Zone Endpoints (PARTIAL)

| Endpoint | Method | Status | Notes |
|----------|--------|--------|-------|
| `/api/dsp/zone/{zone_id}/preset` | POST | ✅ EXISTS | Lines 370-448 |
| `/api/dsp/zone/{zone_id}/filter/{filter_id}` | PATCH | ❌ MISSING | **TO ADD** |
| `/api/dsp/zone/{zone_id}/compressor` | PATCH | ❌ MISSING | **TO ADD** |
| `/api/dsp/zone/{zone_id}/loudness` | PATCH | ❌ MISSING | **TO ADD** |
| `/api/dsp/zone/{zone_id}/enabled` | PATCH | ❌ MISSING | **TO ADD** |

#### Client Proxy Endpoints (EXIST)

| Endpoint | Method | Status | Lines |
|----------|--------|--------|-------|
| `/api/dsp/client/{hostname}/status` | GET | ✅ EXISTS | 954-977 |
| `/api/dsp/client/{hostname}/filters` | GET | ✅ EXISTS | 979-983 |
| `/api/dsp/client/{hostname}/filter/{filter_id}` | PUT | ✅ EXISTS | 985-1001 |
| `/api/dsp/client/{hostname}/reset` | POST | ✅ EXISTS | 1003-1017 |
| `/api/dsp/client/{hostname}/compressor` | GET/PUT | ✅ EXISTS | 1019-1037 |
| `/api/dsp/client/{hostname}/loudness` | GET/PUT | ✅ EXISTS | 1039-1057 |
| `/api/dsp/client/{hostname}/enabled` | GET/PUT | ✅ EXISTS | 1059-1103 |
| `/api/dsp/client/{hostname}/volume` | GET/PUT | ✅ EXISTS | 1105-1155 |
| `/api/dsp/client/{hostname}/preset/{preset_id}` | PUT | ✅ EXISTS | 498-531 |
| `/api/dsp/client/{mac_id}/preset` | POST | ✅ EXISTS | 450-496 |

### Missing Zone Endpoints - Implementation Pattern

Follow the same pattern as `/api/dsp/zone/{zone_id}/preset` (lines 370-448):

```python
@router.patch("/zone/{zone_id}/filter/{filter_id}")
async def update_zone_filter(zone_id: str, filter_id: str, payload: DspFilterUpdateRequest):
    """
    Update a filter for all clients in a zone.

    Applies the filter change to all ONLINE zone members. OFFLINE clients will
    receive settings on reconnection via sync service.
    """
    try:
        _require_registry()
        zone = client_registry_service.get_zone(zone_id)
        if not zone:
            raise HTTPException(status_code=404, detail=f"Zone '{zone_id}' not found")

        applied_to = []
        errors = []

        for client_id in zone.client_ids:
            normalized = normalize_client_id(client_id)

            if normalized == 'local':
                # Local client: apply directly via dsp_service
                try:
                    success = await dsp_service.set_filter(
                        filter_id=filter_id,
                        freq=payload.freq,
                        gain=payload.gain,
                        q=payload.q,
                        filter_type=payload.filter_type,
                        enabled=payload.enabled
                    )
                    if success:
                        applied_to.append(client_id)
                    else:
                        errors.append({"client_id": client_id, "error": "Failed to update filter"})
                except Exception as e:
                    errors.append({"client_id": client_id, "error": str(e)})
            else:
                # Remote client: check online status, then proxy
                client = client_registry_service.get_client(client_id)
                if not client or not client.online:
                    logger.debug(f"Skipping offline client {client_id} for filter update")
                    continue

                try:
                    hostname = client.host or client.ip
                    if not hostname:
                        errors.append({"client_id": client_id, "error": "No hostname or IP"})
                        continue

                    result = await proxy_service.request(
                        hostname, "PUT", f"/dsp/filter/{filter_id}",
                        payload.model_dump(exclude_none=True)
                    )
                    if result.get("status") == "success":
                        applied_to.append(client_id)
                    else:
                        errors.append({"client_id": client_id, "error": result.get("message", "Unknown error")})
                except Exception as e:
                    errors.append({"client_id": client_id, "error": str(e)})

        # Broadcast zone filter change
        await state_machine.broadcast_event("dsp", "zone_filter_changed", {
            "zone_id": zone_id,
            "filter_id": filter_id,
            "applied_to": applied_to
        })

        return {
            "status": "success" if not errors else "partial",
            "zone_id": zone_id,
            "filter_id": filter_id,
            "applied_to": applied_to,
            "errors": errors if errors else None
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating filter for zone {zone_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

### Architecture Compliance

**From architecture.md:**

API Patterns:
- `/api/dsp/zone/{zone_id}/...` - Zone-level DSP operations
- `/api/dsp/client/{mac_id}/...` - Client-level DSP operations
- Zone endpoints must propagate to all ONLINE clients
- OFFLINE clients receive settings on reconnection (sync service)

**WebSocket Events:**
| Event | Category | Data | Trigger |
|-------|----------|------|---------|
| `zone_filter_changed` | dsp | `{zone_id, filter_id, applied_to}` | Zone filter update |
| `zone_compressor_changed` | dsp | `{zone_id, enabled, applied_to}` | Zone compressor update |
| `zone_loudness_changed` | dsp | `{zone_id, enabled, applied_to}` | Zone loudness update |
| `zone_enabled_changed` | dsp | `{zone_id, enabled, applied_to}` | Zone DSP bypass toggle |

**Response Format (from architecture.md):**
```json
{
  "status": "success",
  "applied_to": ["dca6327ed343"],
  "offline_clients": ["112233445566"],
  "zone_settings_updated": true
}
```

### Previous Story Intelligence (4-6-dsp-presets-system)

**Patterns established:**
1. Zone propagation via `propagateToLinkedClients()` in frontend
2. Proxy routes pattern: `/api/dsp/client/{hostname}/...`
3. Skip OFFLINE clients gracefully (don't error)
4. WebSocket events with explicit target IDs
5. `_require_registry()` helper for service availability
6. `_check_client_or_skip()` helper for offline client handling

**Code locations to reference:**
- Zone preset endpoint: `backend/api/dsp.py:370-448`
- Client preset endpoint: `backend/api/dsp.py:450-496`
- Proxy preset route: `backend/api/dsp.py:498-531`
- Frontend propagation: `frontend/src/stores/dspStore.js:propagateToLinkedClients()`

### Git Intelligence (Recent Commits)

```
57877fd fix(dsp): resolve preset loading and filter restoration issues
99a98b7 fix(multiroom): compute crossover_enabled dynamically based on subwoofer availability
fa167e4 feat(multiroom): add client deletion and improve offline handling
```

Recent work confirms DSP preset system is stable and zone propagation patterns are established.

### Key Implementation Files

| Component | File | Purpose |
|-----------|------|---------|
| DSP API Routes | `backend/api/dsp.py` | All DSP endpoints (add zone propagation here) |
| DSP API Models | `backend/api/models.py` | Request/response models |
| DSP Service | `backend/core/dsp/service.py` | CamillaDSP interface |
| Client Registry | `backend/core/multiroom/registry.py` | Zone/client management |
| Proxy Service | `backend/core/dsp/proxy_service.py` | Remote client proxy |
| Frontend Store | `frontend/src/stores/dspStore.js` | DSP state + API calls |
| API Schemas | `frontend/src/schemas/api.js` | Zod validation schemas |

### Frontend Integration Notes

**Current behavior in dspStore.js:**
- `propagateToLinkedClients(action, data)` handles zone propagation
- Existing cases: `filter`, `compressor`, `loudness`, `enabled`, `preset`
- Uses `/api/dsp/client/{hostname}/...` proxy routes

**Required changes:**
1. When a **zone** is selected as target, use zone endpoints:
   - `PATCH /api/dsp/zone/{zone_id}/filter/{filter_id}`
   - `PATCH /api/dsp/zone/{zone_id}/compressor`
   - `PATCH /api/dsp/zone/{zone_id}/loudness`
   - `PATCH /api/dsp/zone/{zone_id}/enabled`
2. When a **standalone client** is selected, use client endpoints directly
3. `propagateToLinkedClients()` may become obsolete if zone endpoints are used

**Note:** This is an architectural decision - using zone endpoints is cleaner than manual frontend propagation.

### Project Structure Notes

**Backend file organization:**
```
backend/
├── api/
│   ├── dsp.py          # DSP routes (add zone endpoints here)
│   └── models.py       # Add models if needed
├── core/
│   ├── dsp/
│   │   ├── service.py  # CamillaDSP interface
│   │   └── proxy_service.py  # Remote client proxy
│   └── multiroom/
│       └── registry.py # ClientRegistryService
```

**Frontend file organization:**
```
frontend/src/
├── stores/
│   └── dspStore.js     # DSP state management
├── schemas/
│   └── api.js          # Zod schemas for API validation
└── components/settings/categories/dsp/
    ├── ItemSelector.vue     # Zone/client selector
    ├── ParametricEQ.vue     # EQ bands UI
    └── AdvancedDsp.vue      # Compressor/loudness UI
```

### Testing Strategy

**Backend integration tests** (`backend/tests/integration/test_dsp_api_endpoints.py`):
1. Test each zone endpoint individually
2. Test propagation to multiple clients
3. Test OFFLINE client handling (graceful skip)
4. Test error scenarios (zone not found, service unavailable)
5. Test WebSocket event broadcasting

**Frontend tests** (`frontend/tests/stores/dspStore.test.js`):
1. Test API calls use correct endpoints based on target type
2. Test zone vs standalone client logic
3. Test error handling for failed API calls

### References

- [Source: backend/api/dsp.py] - Existing DSP endpoints
- [Source: backend/api/dsp.py#370-448] - Zone preset endpoint (pattern to follow)
- [Source: backend/api/models.py] - Request/response Pydantic models
- [Source: frontend/src/stores/dspStore.js] - Frontend DSP store
- [Source: _bmad-output/planning-artifacts/architecture.md#API-Design] - API patterns
- [Source: _bmad-output/implementation-artifacts/4-6-dsp-presets-system.md] - Previous story reference
- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.7] - FR16, FR19-FR21 requirements

## Dev Agent Record

### Agent Model Used

Claude claude-opus-4-5-20251101

### Debug Log References

N/A

### Completion Notes List

1. **Backend Zone Endpoints Added** (backend/api/dsp.py):
   - `PATCH /api/dsp/zone/{zone_id}/filter/{filter_id}` - Filter updates for zones
   - `PATCH /api/dsp/zone/{zone_id}/compressor` - Compressor settings for zones
   - `PATCH /api/dsp/zone/{zone_id}/loudness` - Loudness settings for zones
   - `PATCH /api/dsp/zone/{zone_id}/enabled` - DSP bypass for zones

2. **All zone endpoints implement**:
   - Propagation to ONLINE clients only
   - OFFLINE clients tracked in `offline_clients` response field
   - Error tracking for failed clients in `errors` response field
   - WebSocket events broadcast for state sync (`zone_filter_changed`, `zone_compressor_changed`, `zone_loudness_changed`, `zone_enabled_changed`)

3. **Frontend Integration** (frontend/src/stores/dspStore.js):
   - Added `getSelectedZoneId()` and `isTargetInZone()` helpers
   - `updateCompressor()` uses zone endpoint when target is in a zone
   - `updateLoudness()` uses zone endpoint when target is in a zone
   - `toggleDspEffectsEnabled()` uses zone endpoint when target is in a zone
   - `finalizeFilterUpdate()` uses zone endpoint when target is in a zone
   - Falls back to direct client endpoints for standalone clients

4. **Zod Schemas Added** (frontend/src/schemas/api.js):
   - `DspZoneResponseSchema` - Response format for zone endpoints
   - `DspCompressorSchema` - Compressor settings validation
   - `DspLoudnessSchema` - Loudness settings validation
   - `DspPresetSchema` and `DspPresetsResponseSchema` - Presets list

5. **Integration Tests**:
   - Backend: `backend/tests/integration/test_dsp_zone_endpoints.py`
   - Frontend: Extended `frontend/tests/stores/dspStore.test.js` with Story 4.7 tests

### File List

| Action | File | Description |
|--------|------|-------------|
| Modified | `backend/api/dsp.py` | Added 4 zone endpoints (filter, compressor, loudness, enabled) |
| Modified | `frontend/src/stores/dspStore.js` | Added zone endpoint detection and usage |
| Modified | `frontend/src/schemas/api.js` | Added Zod schemas for zone responses |
| Created | `backend/tests/integration/test_dsp_zone_endpoints.py` | Backend integration tests |
| Modified | `frontend/tests/stores/dspStore.test.js` | Frontend tests for Story 4.7 |
| Modified | `_bmad-output/implementation-artifacts/sprint-status.yaml` | Updated story status |
| Modified | `_bmad-output/implementation-artifacts/4-7-api-endpoints-for-dsp.md` | Updated story completion |

## Code Review Record

### Review Date
2026-01-20

### Reviewer
Claude Opus 4.5 (Adversarial Senior Developer Review)

### Issues Found: 5 (2 HIGH, 3 MEDIUM)

#### HIGH Issues (Fixed)

| Issue | Description | Fix Applied |
|-------|-------------|-------------|
| H1 | Test file `test_dsp_zone_endpoints.py` was **untracked** in git (not staged) | `git add` applied |
| H2 | API bug: `client.host` attribute doesn't exist in Client model | Changed to `client.ip` across all 6 occurrences in dsp.py |

#### MEDIUM Issues (Fixed)

| Issue | Description | Fix Applied |
|-------|-------------|-------------|
| M1 | `loadPreset()` used manual propagation instead of zone endpoint | Updated to use `POST /api/dsp/zone/{zone_id}/preset` when target is in zone |
| M2 | `DspZoneResponseSchema` missing `filter_id` field | Added `filter_id` and `enabled` optional fields to schema |
| M3 | Frontend tests had mock timing issues (mock set after store creation) | Restructured Story 4.7 tests with helper functions `createZoneMock()` and `createStandaloneMock()` |

### Test Results After Review Fixes

| Test Suite | Result |
|------------|--------|
| Backend: `test_dsp_zone_endpoints.py` | **11 passed** ✅ |
| Frontend: `dspStore.test.js` | **51 passed** ✅ |

### Files Modified During Review

| File | Changes |
|------|---------|
| `backend/api/dsp.py` | Fixed `client.host` → `client.ip` (6 locations) |
| `backend/tests/integration/test_dsp_zone_endpoints.py` | Fixed Client model instantiation (removed invalid `host` param) |
| `frontend/src/stores/dspStore.js` | Updated `loadPreset()` for zone endpoint, exported `getSelectedZoneId` and `isTargetInZone` |
| `frontend/src/schemas/api.js` | Added `filter_id` and `enabled` to `DspZoneResponseSchema` |
| `frontend/tests/stores/dspStore.test.js` | Restructured mock setup for Stories 4.3, 4.6, 4.7 |

### Verification

All ACs verified after fixes:
- AC1: Zone filter update ✅
- AC2: Zone compressor ✅
- AC3: Zone loudness ✅
- AC4: Zone DSP bypass ✅
- AC5: Zone preset ✅
- AC6: Client endpoints ✅
- AC7: Presets list ✅

