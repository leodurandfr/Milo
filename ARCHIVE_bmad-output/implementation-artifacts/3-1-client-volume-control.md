# Story 3.1: Client Volume Control

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user**,
I want **to adjust the volume of each client independently**,
So that **I can set different volume levels for different rooms**.

## Acceptance Criteria

1. **AC1: Online client volume control**
   - **Given** a client is ONLINE
   - **When** I set its volume via `VolumeService.set_client_volume(mac_id, volume_db)`
   - **Then** the volume is applied to CamillaDSP within 100ms (NFR1)
   - **And** `client.volume_db` is updated in ClientRegistry
   - **And** changes are persisted to settings.json
   - **And** a WebSocket event `client_state_changed` is broadcast

2. **AC2: Offline client volume persistence**
   - **Given** a client is OFFLINE
   - **When** I set its volume
   - **Then** `client.volume_db` is updated and persisted
   - **And** the volume will be applied when client comes back ONLINE

3. **AC3: VolumeService implementation**
   - **Given** VolumeService
   - **When** I implement `set_client_volume(mac_id, volume_db)` and `get_client_volume(mac_id)`
   - **Then** `volume_db` is validated against min/max from settings
   - **And** volume is applied via CamillaDSPProxy

4. **AC4: Mute control**
   - **Given** a client (online or offline)
   - **When** I toggle its mute state via `VolumeService.set_client_mute(mac_id, mute)`
   - **Then** the mute state is updated in ClientRegistry
   - **And** changes are persisted to settings.json
   - **And** a WebSocket event `client_state_changed` is broadcast
   - **And** if ONLINE, the mute is applied to CamillaDSP immediately

## Tasks / Subtasks

- [x] **Task 1: API Endpoint for client volume** (AC: #1, #2, #3)
  - [x] 1.1: Create `PATCH /api/volume/client/{client_id}` endpoint in `backend/api/volume.py`
  - [x] 1.2: Add `ClientVolumeRequest` Pydantic model with `volume_db` field
  - [x] 1.3: Add volume_db validation against min/max limits
  - [x] 1.4: Wire endpoint to existing `VolumeService.update_client_volume_db()` method

- [x] **Task 2: API Endpoint for client mute** (AC: #4)
  - [x] 2.1: Create `PATCH /api/volume/client/{client_id}/mute` endpoint
  - [x] 2.2: Add `ClientMuteRequest` Pydantic model with `mute` boolean field
  - [x] 2.3: Wire endpoint to existing `VolumeService.set_client_mute()` method

- [x] **Task 3: GET endpoint for client volume state** (AC: #3)
  - [x] 3.1: Create `GET /api/volume/client/{client_id}` endpoint
  - [x] 3.2: Return `{volume_db, mute, online}` from `VolumeService.get_client_volume()`

- [x] **Task 4: Validation and error handling** (AC: #1, #2, #3)
  - [x] 4.1: Validate client_id exists in ClientRegistry, return 404 if not found
  - [x] 4.2: Validate volume_db is within configured min/max limits, return 400 if invalid
  - [x] 4.3: Log warnings for offline clients (volume saved but not applied immediately)

- [x] **Task 5: Unit tests** (AC: all)
  - [x] 5.1: Test PATCH endpoint sets volume for online client
  - [x] 5.2: Test PATCH endpoint persists volume for offline client
  - [x] 5.3: Test GET endpoint returns correct volume state
  - [x] 5.4: Test mute toggle endpoint
  - [x] 5.5: Test validation rejects invalid client_id
  - [x] 5.6: Test validation rejects out-of-range volume

- [x] **Task 6: Integration tests** (AC: all)
  - [x] 6.1: Test end-to-end volume change with WebSocket event broadcast
  - [x] 6.2: Test volume persistence across service restart simulation
  - [x] 6.3: Test offline → online transition applies persisted volume

## Dev Notes

### Existing Implementation Analysis

**IMPORTANT: Most functionality already exists.** This story primarily adds API endpoints to expose existing `VolumeService` methods:

1. **`VolumeService.update_client_volume_db(client_id, volume_db, broadcast=True)`** - Already implemented at line 443 of `backend/core/volume/service.py`
   - Updates VolumeStateStore
   - Calls `DSPController.set_dsp_volume(client_id, volume_db)`
   - Broadcasts via `_broadcast_volume_state()`

2. **`VolumeService.set_client_mute(client_id, mute, broadcast=True)`** - Already implemented at line 453 of `backend/core/volume/service.py`
   - Calls `DSPController.set_dsp_mute()`
   - Persists and broadcasts

3. **`VolumeService.get_client_volume(hostname)`** - Already implemented at line 777
   - Returns `{volume_db, mute, online}` dict

4. **`DSPController` routing** - Already handles local vs remote clients automatically:
   - `hostname == "local"` → Direct CamillaDSP call
   - Otherwise → Via `DspClientProxyService.request()` to remote client

### Volume Persistence Architecture

```
VolumeStateStore (Single Source of Truth)
├── _clients: Dict[hostname, ClientVolume]
│   └── ClientVolume(volume_db, offset_db, mute, available)
├── Persists to: /var/lib/milo/last_volume.json
└── Auto-updates via ClientRegistryService events
```

**Key Pattern:** Volume state is managed by `VolumeStateStore`, NOT by `ClientRegistryService`. The two services communicate via events:
- `ClientRegistryService` emits `CLIENT_CONNECTED`, `CLIENT_DISCONNECTED`
- `VolumeStateStore` subscribes and updates availability

### WebSocket Broadcasting

Already automatic via `VolumeService._broadcast_volume_state()`:
```python
await self.state_machine.broadcast_event(
    "volume",           # category
    "volume_changed",   # type
    {
        "show_bar": show_bar,
        "state": volume_state.to_dict()  # Complete VolumeState
    }
)
```

The frontend `multiroomStore` already handles `volume_changed` events.

### API Endpoint Pattern to Follow

From existing endpoints in `backend/core/volume/routes.py`:
```python
@router.post("/zone/{zone_id}/delta")
async def apply_zone_delta(
    zone_id: str,
    request: VolumeAdjustRequest,
    volume_service: VolumeService = Depends(get_volume_service)
):
    new_avg = await volume_service.apply_zone_volume_delta(zone_id, request.delta_db)
    return {"status": "success", "zone_id": zone_id, "new_average_db": new_avg}
```

### Project Structure Notes

**Files to modify:**
- `backend/core/volume/routes.py` - Add 3 new endpoints

**Files NOT to modify (already complete):**
- `backend/core/volume/service.py` - Methods exist
- `backend/core/volume/state.py` - State management exists
- `backend/core/volume/dsp_controller.py` - Hardware routing exists
- `frontend/src/stores/multiroomStore.js` - WebSocket handlers exist

### Volume Limits

From settings.json via `VolumeService.config`:
- MIN_VOLUME_DB: -80 dB (configurable)
- MAX_VOLUME_DB: 0 dB (configurable, user default: -21 dB for safety)
- DEFAULT_VOLUME_DB: -60 dB

### MAC Address Format in URLs

Per architecture doc: MAC addresses in URLs use **no separators** format:
- Storage/display: `dc:a6:32:7e:d3:43`
- URLs: `dca6327ed343`
- Special case: `"local"` for local client

However, check existing patterns in codebase - `VolumeService` uses `hostname` which is:
- `"local"` for local client
- Hostname like `"milo-client-01"` for remotes

**Clarification needed:** The `mac_id` in ClientRegistryService may differ from `hostname` in VolumeService. Need to verify mapping.

### References

- [Source: backend/core/volume/service.py#update_client_volume_db (line 443)]
- [Source: backend/core/volume/service.py#set_client_mute (line 453)]
- [Source: backend/core/volume/service.py#get_client_volume (line 777)]
- [Source: backend/core/volume/routes.py - existing endpoint patterns]
- [Source: _bmad-output/planning-artifacts/architecture.md#API Design]
- [Source: _bmad-output/planning-artifacts/epics.md#Story 3.1]

## Technical Requirements

### NFR Compliance

- **NFR1:** Volume changes applied within 100ms - Existing `DSPController` uses `asyncio.wait_for(timeout=5.0)` with retry logic
- **NFR2:** WebSocket updates within 100ms - Automatic via `_broadcast_volume_state()`
- **NFR7:** State persists via `VolumeStateStore._persist_state()` with atomic writes

### Architecture Compliance

- **Single Source of Truth:** VolumeStateStore manages all volume state
- **Service Registry:** Use `get_volume_service()` dependency injection
- **Async/await:** All I/O operations already async in VolumeService
- **WebSocket broadcasting:** Via `state_machine.broadcast_event()`

### Testing Standards

- Use `@pytest.mark.asyncio` for async tests
- Mock `DSPController` for unit tests (avoid hardware calls)
- Integration tests in `backend/tests/integration/`
- Test file: `backend/tests/test_volume_api.py` (new)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

- All 25 unit tests passing (test_volume_api.py)
- All 5 integration tests passing (test_volume_control.py::TestClientVolumeAPI)
- All 47 volume-related tests passing (no regressions)

### Completion Notes List

- **Task 1**: Created `PATCH /api/volume/client/{client_id}` endpoint in `backend/api/volume.py`. Uses client_id (DSP ID like "local" or "milo-client-01") instead of mac_id per existing codebase patterns.
- **Task 2**: Created `PATCH /api/volume/client/{client_id}/mute` endpoint for mute control.
- **Task 3**: Created `GET /api/volume/client/{client_id}` endpoint returning `{volume_db, mute, online}`.
- **Task 4**: Added validation helpers `_validate_client_exists()` (404 for unknown clients) and `_validate_volume_limits()` (400 for out-of-range volume). Logs warnings for offline clients.
- **Task 5**: Created comprehensive unit tests in `backend/tests/test_volume_api.py` (25 tests) covering all endpoints, validation, error handling, and fallback mode.
- **Task 6**: Added integration tests in `backend/tests/integration/test_volume_control.py::TestClientVolumeAPI` (5 tests) for WebSocket broadcasts and state persistence.

### Implementation Notes

1. **URL Parameter**: Changed from `mac_id` to `client_id` to match existing codebase patterns. The client_id is the DSP ID (hostname like "local" or "milo-client-01"), not the MAC address.

2. **File Structure**: Added endpoints to `backend/api/volume.py` (the active router used by main.py) and also to `backend/core/volume/routes.py` for consistency.

3. **ClientRegistry Integration**: The `create_volume_router()` function now accepts an optional `client_registry_service` parameter. When provided, validates client existence before operations. When None (fallback mode), allows all client IDs.

4. **Validation**: Two-level validation:
   - Pydantic model (`ClientVolumeRequest`): -80 to 0 dB range
   - Runtime validation: Against user-configured limits from settings

### File List

**New Files:**
- `backend/tests/test_volume_api.py` - Unit tests for client volume API (25 tests)

**Modified Files:**
- `backend/api/models.py` - Added `ClientVolumeRequest` and `ClientMuteRequest` models
- `backend/api/volume.py` - Added 3 new endpoints and validation helpers
- `backend/core/volume/routes.py` - Added same endpoints for consistency
- `backend/main.py` - Updated `create_volume_router()` call to include `client_registry_service`
- `backend/tests/integration/test_volume_control.py` - Added `TestClientVolumeAPI` class (5 tests)

### Code Review Notes

**Review Date:** 2026-01-18
**Reviewer:** Claude Opus 4.5 (code-review workflow)

**Issues Found & Fixed:**
- **HIGH-1 (Fixed):** Removed duplicated CLIENT VOLUME OPERATIONS section from `backend/core/volume/routes.py` - the active endpoints are in `backend/api/volume.py`
- **LOW-2 (Fixed):** Updated outdated comment in `frontend/src/stores/dspStore.js:376` referencing incorrect method name

**Design Notes (Not Bugs):**
- MEDIUM-1: Frontend uses existing `PUT /api/dsp/client/{hostname}/volume` endpoint rather than new `PATCH /api/volume/client/{client_id}`. Both call the same `VolumeService.update_client_volume_db()` method. The new endpoints provide an alternative REST API with explicit validation.
- MEDIUM-2: Field naming differs between DSP and Volume endpoints (`volume` vs `volume_db`). This is intentional - the new endpoints use explicit `_db` suffix for clarity.

### Change Log

- 2026-01-18: Code review completed - Fixed duplicated code in core/volume/routes.py, fixed outdated comment in dspStore.js
- 2026-01-18: Story 3.1 implemented - Added client volume control API endpoints (PATCH volume, PATCH mute, GET volume state) with validation and comprehensive tests.

