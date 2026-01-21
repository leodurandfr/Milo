# Story 3.3: Startup Volume Management

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user**,
I want **my system to restore appropriate volume levels after restart**,
So that **I don't get surprised by unexpected volume levels when turning on my audio system**.

## Acceptance Criteria

1. **AC1: Auto-update `startup_volume_db` when restore disabled (FR11)**
   - **Given** settings contain `restore_last_volume: false`
   - **When** any client volume changes (via UI, rotary encoder, or API)
   - **Then** `startup_volume_db` is auto-updated to current local volume
   - **And** the new value is persisted to settings.json
   - **And** a WebSocket event `settings_changed` is broadcast

2. **AC2: Preserve `startup_volume_db` when restore enabled**
   - **Given** settings contain `restore_last_volume: true`
   - **When** client volumes change
   - **Then** `startup_volume_db` remains unchanged (manual configuration only)
   - **And** volume is still saved to `last_volume.json` for restoration

3. **AC3: Backend restart applies startup volume (FR12)**
   - **Given** the backend starts/restarts
   - **When** clients are initialized
   - **Then** `startup_volume_db` from settings is applied to all clients if `restore_last_volume: false`
   - **Or** persisted volume from `last_volume.json` is applied if `restore_last_volume: true`
   - **And** this happens before any user interaction

4. **AC4: VolumeService startup volume logic**
   - **Given** VolumeService
   - **When** I implement startup volume logic
   - **Then** `initialize()` applies startup_volume_db or persisted volume based on flag
   - **And** `_update_startup_volume()` is called on volume changes when restore=false

5. **AC5: Zone volume changes update startup volume**
   - **Given** `restore_last_volume: false`
   - **When** zone volume delta is applied (affects multiple clients)
   - **Then** `startup_volume_db` is updated to the local client's new volume
   - **And** the new value is persisted to settings.json

## Tasks / Subtasks

- [x] **Task 1: Implement auto-update of `startup_volume_db` on volume change (FR11)** (AC: #1, #2, #5)
  - [x] 1.1: Add `_update_startup_volume_if_needed()` method to VolumeService
  - [x] 1.2: Call method in `set_volume_db()` after successful volume application
  - [x] 1.3: Call method in `adjust_volume_db()` after successful volume adjustment
  - [x] 1.4: Call method in `apply_zone_volume_delta()` after zone update (using local client volume)
  - [x] 1.5: Only update setting when `restore_last_volume == False`
  - [x] 1.6: Use `SettingsService.set_setting()` for atomic persistence

- [x] **Task 2: Broadcast settings change after startup volume update** (AC: #1)
  - [x] 2.1: Add `_broadcast_startup_volume_changed()` method to VolumeService
  - [x] 2.2: Broadcast WebSocket event with category "settings", type "volume_startup_changed"
  - [x] 2.3: Include `startup_volume_db` and `restore_last_volume` in event data

- [x] **Task 3: Verify backend restart applies startup volume (FR12)** (AC: #3, #4)
  - [x] 3.1: Review existing `_apply_startup_volume()` implementation (lines 529-559)
  - [x] 3.2: Verify `restore_last_volume: false` path uses `startup_volume_db`
  - [x] 3.3: Verify `restore_last_volume: true` path uses persisted volume
  - [x] 3.4: Add logging to trace which volume source was used

- [x] **Task 4: Unit tests for FR11 - auto-update startup_volume_db** (AC: #1, #2, #5)
  - [x] 4.1: Test `set_volume_db()` updates `startup_volume_db` when restore=false
  - [x] 4.2: Test `set_volume_db()` does NOT update `startup_volume_db` when restore=true
  - [x] 4.3: Test `adjust_volume_db()` updates `startup_volume_db` when restore=false
  - [x] 4.4: Test `apply_zone_volume_delta()` updates `startup_volume_db` when restore=false
  - [x] 4.5: Test WebSocket broadcast includes new `startup_volume_db`

- [x] **Task 5: Unit tests for FR12 - backend restart** (AC: #3, #4)
  - [x] 5.1: Test `initialize()` applies `startup_volume_db` when restore=false
  - [x] 5.2: Test `initialize()` applies persisted volume when restore=true
  - [x] 5.3: Test persisted volume older than 7 days is ignored
  - [x] 5.4: Test multiroom mode applies startup volume to all clients

- [x] **Task 6: Integration tests for startup volume workflow** (AC: all)
  - [x] 6.1: Test end-to-end: volume change → settings update → backend restart → volume applied
  - [x] 6.2: Test WebSocket event broadcast on startup volume change
  - [x] 6.3: Test multiroom mode startup volume sync across clients

## Dev Notes

### Existing Implementation Analysis

**PARTIALLY IMPLEMENTED - This story adds the missing FR11 auto-update logic.**

From the previous analysis, the current state is:

1. **Settings persistence** (DONE): `startup_volume_db` and `restore_last_volume` are stored in `/var/lib/milo/settings.json`

2. **Volume restoration on startup** (FR12 - DONE):
   - `VolumeService._apply_startup_volume()` (lines 529-559) handles startup volume
   - Checks `restore_last_volume` flag and applies appropriate volume
   - Works for both direct and multiroom modes

3. **Last volume persistence** (DONE):
   - `VolumeStateStore._persist_state()` saves to `/var/lib/milo/last_volume.json`
   - Called on every volume change via `_save_last_volume()`

4. **Auto-update of `startup_volume_db`** (FR11 - NOT IMPLEMENTED):
   - Currently, `startup_volume_db` is NEVER automatically updated
   - Only manual update via API `/volume-startup` endpoint
   - **THIS IS THE MAIN IMPLEMENTATION WORK**

### Key Methods and Locations

| Method | File:Line | Purpose |
|--------|-----------|---------|
| `set_volume_db()` | `backend/core/volume/service.py:593` | Set local volume - needs FR11 hook |
| `adjust_volume_db()` | `backend/core/volume/service.py:630` | Adjust volume by delta - needs FR11 hook |
| `apply_zone_volume_delta()` | `backend/core/volume/service.py:467` | Zone delta - needs FR11 hook |
| `_apply_startup_volume()` | `backend/core/volume/service.py:529` | Apply volume on startup (FR12 - exists) |
| `_save_last_volume()` | `backend/core/volume/service.py:238` | Save to last_volume.json |
| `initialize()` | `backend/core/volume/service.py:502` | VolumeService init |
| `set_setting()` | `backend/core/settings.py` | Atomic settings persistence |

### Implementation Pattern for FR11

Add new method to `VolumeService`:

```python
async def _update_startup_volume_if_needed(self, volume_db: float) -> None:
    """Auto-update startup_volume_db when restore_last_volume is disabled (FR11)."""
    if self.config.config.restore_last_volume:
        return  # Do nothing when restore is enabled

    current_startup = self.config.config.startup_volume_db
    if abs(current_startup - volume_db) < 0.1:
        return  # Skip if unchanged (avoid unnecessary writes)

    # Update setting atomically
    await self.settings_service.set_setting('volume.startup_volume_db', volume_db)

    # Reload config to get fresh value
    await self.config.load()

    # Broadcast change event
    await self._broadcast_startup_volume_changed(volume_db)

    logger.info(f"FR11: Auto-updated startup_volume_db to {volume_db:.1f} dB")
```

Add call in `set_volume_db()` after line 608:
```python
if success:
    self._save_last_volume(clamped_db)
    await self._update_startup_volume_if_needed(clamped_db)  # NEW FOR FR11
    await self._broadcast_volume_state(show_bar)
```

### WebSocket Event Structure

Per architecture doc, use settings category:
```json
{
  "category": "settings",
  "type": "volume_startup_changed",
  "data": {
    "startup_volume_db": -45.0,
    "restore_last_volume": false
  }
}
```

### Persistence Architecture

**Two separate persistence mechanisms:**

1. **Settings** (`/var/lib/milo/settings.json`):
   ```json
   {
     "volume": {
       "startup_volume_db": -60.0,
       "restore_last_volume": false
     }
   }
   ```
   - Updated by FR11 on volume changes (when restore=false)
   - Uses `SettingsService.set_setting()` for atomic writes

2. **Last Volume** (`/var/lib/milo/last_volume.json`):
   ```json
   {
     "timestamp": "2025-01-18T10:30:00+00:00",
     "local_volume_db": -45.0,
     "clients": { ... }
   }
   ```
   - Always updated on volume changes
   - Uses `VolumeStateStore._persist_state()` for atomic writes
   - Restored when `restore_last_volume: true`

### Project Structure Notes

**Files to modify:**
- `backend/core/volume/service.py` - Add FR11 auto-update logic
  - Add `_update_startup_volume_if_needed()` method
  - Add `_broadcast_startup_volume_changed()` method
  - Modify `set_volume_db()` to call new method
  - Modify `adjust_volume_db()` to call new method
  - Modify `apply_zone_volume_delta()` to call new method

**Files NOT to modify (already complete):**
- `backend/core/volume/state.py` - State management exists
- `backend/core/settings.py` - Settings service exists
- `backend/api/settings.py` - API endpoints exist
- `backend/core/models/volume.py` - VolumeConfig model exists

### Dependencies from Previous Stories

**From Story 3-1 (Client Volume Control):**
- `set_volume_db()` and `set_client_volume()` methods are implemented
- Client volume persistence via `_save_last_volume()` works
- WebSocket broadcasting via `_broadcast_volume_state()` works

**From Story 3-2 (Zone Volume Delta):**
- `apply_zone_volume_delta()` method is implemented
- Zone operations correctly affect only ONLINE clients
- Zone average calculation is readonly

### Volume Limits

From settings.json via `VolumeService.config`:
- MIN_VOLUME_DB: -80 dB (configurable)
- MAX_VOLUME_DB: 0 dB (configurable, user default: -21 dB for safety)
- DEFAULT_VOLUME_DB: -60 dB
- `startup_volume_db` must be within `[limit_min_db, limit_max_db]`

### Multiroom Mode Considerations

- `push_volume_to_all_clients()` already respects `restore_last_volume` and `startup_volume_db`
- When zone volume changes, use local client's volume for `startup_volume_db` update
- Per-client volumes are persisted to `last_volume.json` separately

### References

- [Source: backend/core/volume/service.py#set_volume_db (line 593)]
- [Source: backend/core/volume/service.py#adjust_volume_db (line 630)]
- [Source: backend/core/volume/service.py#apply_zone_volume_delta (line 467)]
- [Source: backend/core/volume/service.py#_apply_startup_volume (line 529)]
- [Source: backend/core/volume/service.py#initialize (line 502)]
- [Source: backend/core/settings.py#set_setting]
- [Source: _bmad-output/planning-artifacts/architecture.md#Volume Control]
- [Source: _bmad-output/planning-artifacts/epics.md#Story 3.3]
- [Source: _bmad-output/planning-artifacts/prd-multiroom-dsp.md#FR11, FR12]

## Technical Requirements

### NFR Compliance

- **NFR1:** Volume changes applied within 100ms - Existing async DSP controller
- **NFR2:** WebSocket updates within 100ms - Via `state_machine.broadcast_event()`
- **NFR7:** State persists via atomic writes - Both `SettingsService` and `VolumeStateStore` use `os.replace()`
- **NFR8:** No data loss on unexpected shutdown - Atomic writes ensure consistency

### Architecture Compliance

- **Single Source of Truth:** Backend settings.json is SSOT for `startup_volume_db`
- **Service Registry:** Use existing `get_settings_service()` dependency injection
- **Async/await:** All I/O operations must be async
- **WebSocket broadcasting:** Via `state_machine.broadcast_event()` with category "settings"

### FR Coverage

- **FR11:** System auto-updates `startup_volume_db` when `restore_last_volume=false` - **This story (main work)**
- **FR12:** System applies `startup_volume_db` on backend restart - **This story (verify existing)**

### Testing Standards

- Use `@pytest.mark.asyncio` for async tests
- Mock `SettingsService` for unit tests to avoid disk writes
- Integration tests in `backend/tests/integration/`
- Test file: `backend/tests/test_startup_volume.py` (new)

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

- All 982 backend tests pass (no regressions)
- 11 new unit tests for FR11/FR12 (TestStartupVolumeAutoUpdate, TestStartupVolumeOnRestart)
- 5 new integration tests (TestStartupVolumeIntegration)

### Completion Notes List

1. **FR11 Implementation Complete**: Added `_update_startup_volume_if_needed()` method that auto-updates `startup_volume_db` when `restore_last_volume=false`. Method is called from `set_volume_db()`, `adjust_volume_db()`, and `apply_zone_volume_delta()`.

2. **WebSocket Broadcast**: Added `_broadcast_startup_volume_changed()` method that broadcasts settings changes via WebSocket with category "settings" and type "volume_startup_changed".

3. **FR12 Verification**: Existing `_apply_startup_volume()` correctly handles both modes:
   - `restore_last_volume=false`: Uses `startup_volume_db` from settings
   - `restore_last_volume=true`: Uses persisted volume from `last_volume.json`
   - Enhanced logging with FR12 prefix for better traceability

4. **Test Coverage**:
   - Unit tests: 6 tests for FR11, 5 tests for FR12
   - Integration tests: 5 end-to-end tests covering both features
   - All tests pass with no regressions

### Change Log

- 2026-01-19: Implemented FR11 auto-update logic and FR12 verification with comprehensive tests
- 2026-01-19: [Code Review] Fixed H1 (incomplete test assertions), M3 (broadcast consistency)

### File List

**Modified:**
- `backend/core/volume/service.py` - Added FR11 methods and enhanced FR12 logging
- `backend/core/volume/service.py` - [Review Fix] Use reloaded config value for broadcast consistency

**Tests Added:**
- `backend/tests/test_core_volume.py` - Added TestStartupVolumeAutoUpdate (6 tests), TestStartupVolumeOnRestart (5 tests)
- `backend/tests/integration/test_volume_control.py` - Added TestStartupVolumeIntegration (5 tests)
- `backend/tests/integration/test_volume_control.py` - [Review Fix] Added proper assertions to test_fr12_stale_persisted_volume_ignored

## Senior Developer Review (AI)

**Reviewer:** Claude Opus 4.5
**Date:** 2026-01-19
**Outcome:** ✅ APPROVED

### Review Summary

All Acceptance Criteria validated against implementation:

| AC | Status | Evidence |
|----|--------|----------|
| AC1: Auto-update startup_volume_db | ✅ | `_update_startup_volume_if_needed()` called in `set_volume_db()`, `adjust_volume_db()` |
| AC2: Preserve when restore=true | ✅ | Early return in `_update_startup_volume_if_needed()` line 285-286 |
| AC3: Backend restart applies volume | ✅ | `_apply_startup_volume()` handles both modes correctly |
| AC4: VolumeService startup logic | ✅ | `initialize()` calls `_apply_startup_volume()` |
| AC5: Zone delta updates startup | ✅ | `apply_zone_volume_delta()` calls `_update_startup_volume_if_needed()` line 548 |

### Issues Found and Fixed

**HIGH (1 fixed):**
- H1: Test `test_fr12_stale_persisted_volume_ignored` had no assertions → Added proper assertions

**MEDIUM (1 fixed, 2 noted):**
- M3: Broadcast used parameter instead of reloaded config value → Fixed to use `persisted_value`
- M1: Silent error handling in `_update_startup_volume_if_needed` → Acceptable design choice (logged)
- M2: Partial mocking in zone delta test → Integration tests provide coverage

**LOW (2 noted):**
- L1: Code comments in English ✅
- L2: Minor duplication in `_apply_startup_volume` → Not critical

### Test Results

- 982 tests pass (0 failures, 0 regressions)
- 16 tests specifically for FR11/FR12 all pass

