# Story 1.6: API & Schema Harmonization

Status: done

<!-- Note: This story consolidates the API under /api/multiroom/ and harmonizes frontend schemas -->

## Story

As a **developer**,
I want **a single, consistent API prefix and harmonized frontend schemas**,
So that **the codebase is easier to understand and maintain**.

## Acceptance Criteria

1. **AC1: Migrate to `/api/multiroom/state` endpoint**
   - **Given** the backend has `/api/registry/state` returning clients + zones
   - **When** I call `GET /api/multiroom/state`
   - **Then** it returns `{clients: {...}, zones: {...}}` with all data
   - **And** `/api/registry/state` is marked as deprecated

2. **AC2: Frontend uses only `/api/multiroom/` endpoints**
   - **Given** `clientRegistryStore.fetchState()` currently uses `/api/registry/state`
   - **When** the migration is complete
   - **Then** it uses `GET /api/multiroom/state`
   - **And** `updateClient()` uses `PATCH /api/multiroom/clients/{mac_id}`

3. **AC3: Frontend schemas are harmonized**
   - **Given** we have `SnapcastClientSchema` and `VolumeClientSchema` with inconsistent naming
   - **When** the harmonization is complete
   - **Then** `SnapcastClientSchema` is removed (legacy)
   - **And** `VolumeClientSchema.available` is renamed to `online`
   - **And** a new `RegisteredClientSchema` matches the backend `Client` model

4. **AC4: All tests pass**
   - **Given** the API and schema changes
   - **When** I run backend and frontend tests
   - **Then** all tests pass without regressions

## Tasks / Subtasks

- [x] **Task 1: Add `GET /api/multiroom/state` endpoint** (AC: #1)
  - [x] Add endpoint in `backend/api/multiroom.py` returning `{clients: {...}, zones: {...}}`
  - [x] Include `online` status in each client (runtime field)
  - [x] Ensure response format matches current `/api/registry/state`
  - [x] Add tests for the new endpoint

- [x] **Task 2: Mark `/api/registry/` as deprecated** (AC: #1)
  - [x] Add deprecation warnings to `/api/registry/` docstrings
  - [x] Update backend comments to point to `/api/multiroom/` equivalents

- [x] **Task 3: Migrate frontend to `/api/multiroom/` endpoints** (AC: #2)
  - [x] Update `clientRegistryStore.fetchState()` to use `/api/multiroom/state`
  - [x] Update `clientRegistryStore.updateClient()` to use `PATCH /api/multiroom/clients/{mac_id}`
  - [x] Update `updateClientType()` to use `/api/multiroom/` when no crossoverFrequency
  - [x] Verify all other registry API calls - marked deprecated with TODOs for endpoints not yet in `/api/multiroom/`
  - [x] Test that all functionality works correctly

- [x] **Task 4: Harmonize frontend schemas** (AC: #3)
  - [x] Remove `SnapcastClientSchema` and `SnapcastClientsResponseSchema` (unused legacy)
  - [x] Rename `VolumeClientSchema.available` to `online`
  - [x] Create `RegisteredClientSchema` matching backend `Client` model
  - [x] Update `frontend/tests/schemas/api.test.js` for schema changes

- [x] **Task 5: Run all tests** (AC: #4)
  - [x] Run `python -m pytest` in backend
  - [x] Run `npm run test` in frontend
  - [x] Fix any test failures

## Dev Notes

### Current State

**Backend has two API prefixes:**
- `/api/registry/` - Original implementation
- `/api/multiroom/` - Canonical API per architecture (Story 1-4, 2-4)

**Frontend schemas are inconsistent:**
- `SnapcastClientSchema` - Legacy, mixes metadata + volume in %, not used in stores
- `VolumeClientSchema` - Uses `available` instead of `online`

### Target State

**Single API prefix:**
```
/api/multiroom/
├── GET  /state              ← NEW: clients + zones in one call
├── GET  /clients            ← existing
├── GET  /clients/{mac_id}   ← existing
├── PATCH /clients/{mac_id}  ← existing (canonical for partial updates)
├── PUT  /clients/{mac_id}   ← existing (alias for backward compat)
├── GET  /zones              ← existing
├── POST /zones              ← existing
├── PATCH /zones/{zone_id}   ← existing
├── DELETE /zones/{zone_id}  ← existing
└── ...
```

**Harmonized schemas:**
```javascript
// Client metadata (from registry)
export const RegisteredClientSchema = z.object({
  mac_id: z.string(),
  name: z.string(),
  ip: z.string(),
  online: z.boolean(),
  zone_id: z.string().nullable(),
  speaker_type: z.enum(['satellite', 'bookshelf', 'tower', 'subwoofer'])
});

// Client volume state (for VolumeStateSchema)
export const ClientVolumeSchema = z.object({
  volume_db: z.number(),
  offset_db: z.number().default(0),
  mute: z.boolean().default(false),
  online: z.boolean().default(true)  // renamed from 'available'
});
```

### Migration Impact

| Component | Change Required |
|-----------|-----------------|
| `backend/api/multiroom.py` | Add `/state` endpoint |
| `backend/api/registry.py` | Add deprecation warnings |
| `frontend/src/stores/clientRegistryStore.js` | Update API URLs |
| `frontend/src/schemas/api.js` | Schema harmonization |
| `frontend/tests/schemas/api.test.js` | Update tests |

### References

- [Source: backend/core/multiroom/models.py - Client dataclass]
- [Source: backend/api/multiroom.py - Canonical API router]
- [Source: frontend/src/schemas/api.js - Current schemas]
- [Source: 1-5-frontend-client-registry-display.md - Review findings]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Completion Notes List

1. **Task 1 completed**: Added `GET /api/multiroom/state` endpoint in `backend/api/multiroom.py` returning `{clients: {mac_id: {...}}, zones: {zone_id: {...}}}` with enriched zone fields and runtime `online` status for clients. Added 7 unit tests covering response format, client/zone indexing, enriched fields, and edge cases.

2. **Task 2 completed**: Added deprecation warnings to `backend/api/registry.py` module docstring with migration guide, and to individual endpoints (`/state`, `/clients`, `/clients/{mac_id}`, `PUT /clients/{mac_id}`) pointing to `/api/multiroom/` equivalents.

3. **Task 3 completed**: Migrated `clientRegistryStore.fetchState()` from `/api/registry/state` to `/api/multiroom/state` and `updateClient()` from `PUT /api/registry/clients/{mac_id}` to `PATCH /api/multiroom/clients/{mac_id}`.

4. **Task 4 completed**:
   - Removed legacy `SnapcastClientSchema` and `SnapcastClientsResponseSchema`
   - Renamed `VolumeClientSchema.available` to `online` for consistency
   - Added `RegisteredClientSchema` matching backend `Client` model
   - Added `MultiroomStateSchema` for `/api/multiroom/state` response validation
   - Updated frontend tests to use new schemas

5. **Task 5 completed**: All tests pass
   - Backend: 1064 tests passed
   - Frontend: 135 tests passed (7 test files)

6. **Code Review (2026-01-20)**: Fixed migration gaps found during adversarial review:
   - `updateClientType()` now uses canonical `/api/multiroom/` endpoint when `crossoverFrequency` is null
   - `setZoneClients()` marked `@deprecated` with TODO (no `/api/multiroom/` equivalent exists)
   - `deleteClient()` marked `@deprecated` with TODO (no `/api/multiroom/` equivalent exists)
   - Note: 3 functions still use `/api/registry/` because backend endpoints don't exist in `/api/multiroom/` yet

### File List

**Backend (modified):**
- `backend/api/multiroom.py` - Added `/state` endpoint (lines 67-101)
- `backend/api/registry.py` - Added deprecation warnings to module and endpoints
- `backend/tests/test_api_multiroom.py` - Added 7 tests for `TestGetState` class

**Frontend (modified):**
- `frontend/src/stores/clientRegistryStore.js` - Migrated to `/api/multiroom/` endpoints
- `frontend/src/schemas/api.js` - Schema harmonization (removed legacy, added new schemas)
- `frontend/tests/schemas/api.test.js` - Updated tests for new schemas

## Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-01-20 | Story created based on code review findings from Story 1-5 | Claude Opus 4.5 |
| 2026-01-20 | Implementation complete: API consolidation and schema harmonization | Claude Opus 4.5 |
| 2026-01-20 | Code review: Fixed `updateClientType()` migration, added deprecation TODOs to 3 functions | Claude Opus 4.5 |
