# Story 3.2: Zone Volume Delta

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user**,
I want **to adjust zone volume with a single slider that preserves relative client volumes**,
So that **I can quickly raise or lower volume for an entire zone without losing individual balances**.

## Acceptance Criteria

1. **AC1: Zone delta preserves relative offsets**
   - **Given** a zone with clients at different volumes (e.g., Client A: -20dB, Client B: -25dB)
   - **When** I adjust the zone volume slider by +5dB
   - **Then** delta +5dB is applied to each ONLINE client (Client A: -15dB, Client B: -20dB)
   - **And** relative offsets are preserved (5dB difference maintained)

2. **AC2: Only ONLINE clients receive immediate volume change**
   - **Given** a zone with mixed ONLINE/OFFLINE clients
   - **When** I adjust zone volume
   - **Then** only ONLINE clients receive the volume change immediately
   - **And** OFFLINE clients are not modified (they sync on reconnection)

3. **AC3: VolumeService.set_zone_volume_delta implementation**
   - **Given** VolumeService
   - **When** I implement `apply_zone_volume_delta(zone_id, delta_db)`
   - **Then** the method calculates and applies delta to each ONLINE member
   - **And** each client's volume_db is updated and persisted
   - **And** WebSocket events are broadcast for each affected client

4. **AC4: Zone volume is readonly/calculated**
   - **Given** the frontend requests zone volume
   - **When** I call `GET /api/volume/zone/{zone_id}`
   - **Then** the backend returns the average volume_db of ONLINE clients (readonly, calculated)
   - **And** the frontend displays this calculated average on the zone slider

## Tasks / Subtasks

- [x] **Task 1: Verify and enhance existing zone delta implementation** (AC: #1, #2, #3)
  - [x] 1.1: Review existing `VolumeService.apply_zone_volume_delta()` in `backend/core/volume/service.py`
  - [x] 1.2: Verify delta is applied to ONLINE clients only (already in `VolumeStateStore.apply_zone_delta()`)
  - [x] 1.3: Verify relative offsets are preserved (delta added to each client's current volume)
  - [x] 1.4: Verify WebSocket broadcast after zone update (`_broadcast_volume_state()`)

- [x] **Task 2: Verify zone volume readonly behavior** (AC: #4)
  - [x] 2.1: Review `GET /api/volume/zone/{zone_id}` endpoint returns `average_volume_db` (calculated)
  - [x] 2.2: Verify `VolumeStateStore.compute_zone_average()` computes average of ONLINE clients only
  - [x] 2.3: Verify frontend uses `zone.average_volume_db` from WebSocket state for slider display

- [x] **Task 3: Unit tests for zone volume delta** (AC: #1, #2, #3)
  - [x] 3.1: Test delta applied to multiple clients preserves relative offsets
  - [x] 3.2: Test delta only affects ONLINE clients (OFFLINE clients unchanged)
  - [x] 3.3: Test zone with all clients OFFLINE returns no updates
  - [x] 3.4: Test volume clamping at min/max limits during delta application
  - [x] 3.5: Test WebSocket event broadcast after zone delta

- [x] **Task 4: Unit tests for zone average calculation** (AC: #4)
  - [x] 4.1: Test zone average computed from ONLINE clients only
  - [x] 4.2: Test zone average returns DEFAULT_VOLUME_DB when no clients ONLINE
  - [x] 4.3: Test zone average updates after client volume change

- [x] **Task 5: Integration tests for zone volume workflow** (AC: all)
  - [x] 5.1: Test end-to-end zone delta with WebSocket event verification
  - [x] 5.2: Test frontend slider → API → backend → WebSocket → frontend round-trip
  - [x] 5.3: Test OFFLINE client becomes ONLINE receives zone's current calculated average (reconnection behavior)

## Dev Notes

### Existing Implementation Analysis

**IMPORTANT: Most functionality already exists.** This story validates existing implementation and adds comprehensive tests.

1. **`VolumeService.apply_zone_volume_delta(zone_id, delta_db)`** - Already implemented in `backend/core/volume/service.py:467`
   ```python
   async def apply_zone_volume_delta(self, zone_id: str, delta_db: float) -> float:
       """Apply volume delta to entire zone atomically. Returns new zone average in dB."""
       # ...clears zone targets, applies delta, broadcasts state
   ```

2. **`VolumeStateStore.apply_zone_delta(zone_id, delta_db)`** - Already implemented in `backend/core/volume/state.py:580`
   - Calculates new volume for each ONLINE client (`client.available == True`)
   - Returns dict of `{client_id: new_volume_db}` for ONLINE clients only
   - Does NOT modify OFFLINE clients

3. **`POST /api/volume/zone/{zone_id}/delta`** - Already implemented in `backend/api/volume.py:113`
   - Accepts `{ delta_db: float }` body
   - Returns `{ zone_id, new_average_db, delta_db, clients_updated }`

4. **`GET /api/volume/zone/{zone_id}`** - Already implemented in `backend/api/volume.py:162`
   - Returns zone details including `average_volume_db` (computed, not stored)

5. **Frontend `applyZoneDelta()`** - Already implemented in `frontend/src/stores/dspStore.js:398`
   - Calls `POST /api/volume/zone/{zone_id}/delta`
   - State updated via single WebSocket broadcast

### Key Implementation Details

**Delta application logic (VolumeStateStore.apply_zone_delta):**
```python
for client_id in zone.client_ids:
    if client_id in self._clients:
        client = self._clients[client_id]
        if client.available:  # Only ONLINE clients
            new_volume = self._clamp_db(client.volume_db + delta_db)
            updates[client_id] = new_volume
```

**Zone average calculation (VolumeStateStore.compute_zone_average):**
```python
for client_id in zone.client_ids:
    if client_id in self._clients:
        client = self._clients[client_id]
        if client.available:  # Only ONLINE clients
            volumes.append(client.volume_db)
return sum(volumes) / len(volumes) if volumes else DEFAULT_VOLUME_DB
```

### Project Structure Notes

**Files to review/test (not modify):**
- `backend/core/volume/service.py` - `apply_zone_volume_delta()` method
- `backend/core/volume/state.py` - `apply_zone_delta()` and `compute_zone_average()` methods
- `backend/api/volume.py` - Zone endpoints already exist
- `frontend/src/stores/dspStore.js` - `applyZoneDelta()` method
- `frontend/src/components/multiroom/MultiroomControl.vue` - Zone slider handler

**New test files:**
- `backend/tests/test_zone_volume_delta.py` - Unit tests for zone volume operations
- `backend/tests/integration/test_zone_volume.py` - Integration tests for zone volume workflow

### Volume State Architecture

```
VolumeStateStore (Single Source of Truth)
├── _clients: Dict[hostname, ClientVolume]
│   └── ClientVolume(volume_db, offset_db, mute, available)
├── _zones: Dict[zone_id, ZoneConfig]
│   └── ZoneConfig(zone_id, name, client_ids)
├── compute_zone_average(zone_id) → float (calculated, not stored)
└── apply_zone_delta(zone_id, delta_db) → Dict[client_id, new_volume]
```

**Key Pattern:** Zone volume is NEVER stored - it's always calculated from ONLINE client volumes. This ensures:
- Consistency: Zone average always reflects actual client states
- Simplicity: No synchronization needed between zone and client volumes
- Correctness: OFFLINE clients don't skew the average

### WebSocket Broadcasting

Zone delta broadcasts via existing mechanism in `VolumeService._broadcast_volume_state()`:
```python
await self.state_machine.broadcast_event(
    "volume",           # category
    "volume_changed",   # type
    {
        "show_bar": False,  # Zone changes don't show bar (multiple clients)
        "state": volume_state.to_dict()  # Complete VolumeState including zones
    }
)
```

Frontend `multiroomStore` already handles `volume_changed` events and updates zone display.

### Frontend Slider Behavior

From `MultiroomControl.vue:344-357`:
```javascript
// Zone volume change: apply DELTA atomically to entire zone
const state = getZoneSliderState(zone);
const delta = volumeDb - state.startAvg;  // Calculate delta from slider movement

// Single atomic API call for entire zone
await dspStore.applyZoneDelta(zone.id, delta);
```

The slider captures the starting average when drag begins, then calculates delta as the difference between new slider position and starting average.

### Test Strategy

**Unit tests (mock dependencies):**
- Mock `DSPController` to verify volume updates reach hardware
- Test delta calculation preserves offsets
- Test ONLINE/OFFLINE filtering
- Test clamping at volume limits

**Integration tests (real services, no hardware):**
- Test full API → Service → State → Broadcast flow
- Verify WebSocket events contain correct zone average
- Test reconnection sync applies correct volume

### References

- [Source: backend/core/volume/service.py#apply_zone_volume_delta (line 467)]
- [Source: backend/core/volume/state.py#apply_zone_delta (line 580)]
- [Source: backend/core/volume/state.py#compute_zone_average (line 631)]
- [Source: backend/api/volume.py#apply_zone_delta (line 113)]
- [Source: frontend/src/stores/dspStore.js#applyZoneDelta (line 398)]
- [Source: frontend/src/components/multiroom/MultiroomControl.vue#handleVolumeChange (line 344)]
- [Source: _bmad-output/planning-artifacts/architecture.md#Volume Control]
- [Source: _bmad-output/planning-artifacts/epics.md#Story 3.2]

## Technical Requirements

### NFR Compliance

- **NFR1:** Volume changes applied within 100ms - `apply_zone_volume_delta` uses `DSPController.apply_volumes_parallel()` for parallel hardware updates
- **NFR2:** WebSocket updates within 100ms - Single broadcast via `_broadcast_volume_state()` after all clients updated
- **NFR7:** State persists via `VolumeStateStore._persist_state()` with atomic writes

### Architecture Compliance

- **Single Source of Truth:** VolumeStateStore manages all volume state (clients and zones)
- **Zone volume is calculated:** `compute_zone_average()` computes from ONLINE clients - never stored
- **Delta-based zone control:** Frontend sends delta, backend applies to each client preserving offsets
- **Async/await:** All I/O operations are async
- **WebSocket broadcasting:** Via `state_machine.broadcast_event()`

### FR Coverage

- **FR6:** User can adjust zone volume (delta applied to all ONLINE clients, preserving relative offsets) - **This story**
- **FR5:** User can adjust volume independently for each client - Story 3.1 (DONE)

### Testing Standards

- Use `@pytest.mark.asyncio` for async tests
- Mock `DSPController` for unit tests (avoid hardware calls)
- Integration tests in `backend/tests/integration/`
- Test files: `backend/tests/test_zone_volume_delta.py`, `backend/tests/integration/test_zone_volume.py`

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

N/A - Story validated existing implementation (all tests passed)

### Completion Notes List

1. **Verification completed:** All existing zone delta implementation verified working correctly:
   - `VolumeService.apply_zone_volume_delta()` correctly clears targets, applies delta, and broadcasts state
   - `VolumeStateStore.apply_zone_delta()` only affects ONLINE clients (available=True)
   - Relative offsets preserved via delta addition to each client's current volume
   - WebSocket broadcast happens after all updates via `_broadcast_volume_state(show_bar=False)`

2. **Zone average readonly verified:**
   - `GET /api/volume/zone/{zone_id}` returns computed `average_volume_db`
   - `compute_zone_average()` uses only ONLINE clients, returns DEFAULT_VOLUME_DB when none available
   - Frontend uses zone average from WebSocket state for slider display

3. **Unit tests added (13 new tests):**
   - `TestZoneVolumeDelta` class: 7 tests for zone delta behavior
   - `TestZoneAverageCalculation` class: 6 tests for zone average computation

4. **Integration tests added (8 new tests):**
   - `TestZoneVolumeDeltaIntegration` class covering all acceptance criteria

5. **All 64 volume-related tests pass** (including 21 new tests)

### File List

**Modified:**
- `backend/tests/test_volume_state.py` - Added `TestZoneVolumeDelta` and `TestZoneAverageCalculation` classes
- `backend/tests/integration/test_volume_control.py` - Added `TestZoneVolumeDeltaIntegration` class

**No code changes required:** Implementation was already complete and working correctly. This story focused on verification and comprehensive test coverage.

