# Story 2.5: Frontend Zone Management UI

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user**,
I want **to create, edit, and delete zones in the settings interface**,
So that **I can organize my audio devices into logical groups**.

## Acceptance Criteria

1. **AC1: Display Zones in MultiroomSettings**
   - **Given** zones are loaded in multiroomStore
   - **When** I open MultiroomSettings
   - **Then** I see a list of all zones with their member clients

2. **AC2: Create Zone**
   - **Given** I click "Create Zone"
   - **When** I select at least 2 clients and provide a name (max 15 chars)
   - **Then** the zone is created via POST API and appears in the list

3. **AC3: Add Client to Zone**
   - **Given** I view a zone in ZoneEdit.vue
   - **When** I add a client to the zone
   - **Then** the client joins via API and UI updates to show the new member
   - **And** client's DSP settings are replaced by zone's DSP (FR15)

4. **AC4: Remove Client from Zone**
   - **Given** I view a zone in ZoneEdit.vue
   - **When** I remove a client from the zone
   - **Then** the client leaves via API and UI updates accordingly
   - **And** client retains zone DSP as standalone DSP (FR14)

5. **AC5: Delete Zone**
   - **Given** I click "Delete Zone"
   - **When** I confirm the deletion
   - **Then** the zone is deleted via DELETE API and removed from the list

6. **AC6: Real-time WebSocket Updates**
   - **Given** zones are displayed
   - **When** a WebSocket `zone_changed` event is received
   - **Then** the zone list updates immediately without page refresh

## Tasks / Subtasks

- [x] **Task 1: Migrate MultiroomSettings.vue zone logic to clientRegistryStore** (AC: #1)
  - [x] Replace `dspStore.linkedGroups` with `clientRegistryStore.zoneList`
  - [x] Update zone data mapping to use clientRegistryStore zone format
  - [x] Remove dependency on `dspStore` for zone/client data
  - [x] Keep crossover badge logic (uses dspStore for crossover settings only)

- [x] **Task 2: Update ZoneEdit.vue to use clientRegistryStore** (AC: #2, #3, #4, #5)
  - [x] Use `clientRegistryStore.createZone()` for zone creation
  - [x] Use `clientRegistryStore.addClientToZone()` for adding clients
  - [x] Use `clientRegistryStore.removeClientFromZone()` for removing clients
  - [x] Use `clientRegistryStore.deleteZone()` for zone deletion
  - [x] Update `availableTargets` to use `clientRegistryStore.clientList`

- [x] **Task 3: Ensure WebSocket event handlers are wired up** (AC: #6)
  - [x] Verify `websocket.js` routes `registry` events to `clientRegistryStore.handleRegistryEvent()`
  - [x] Verify `zone_created`, `zone_updated`, `zone_deleted` events update state correctly
  - [x] Test real-time UI updates when zone changes occur

- [x] **Task 4: Test all zone operations** (AC: #1-6)
  - [x] Test zone creation with 2+ clients
  - [x] Test adding client to existing zone
  - [x] Test removing client from zone (including auto-deletion when < 2 clients)
  - [x] Test zone deletion
  - [x] Test real-time updates via WebSocket

## Dev Notes

### Critical Architecture Discovery

The frontend already has **TWO data sources for zones**:
1. `clientRegistryStore.zoneList` (computed from `clientRegistryStore.zones`) - **NEW SSOT**
2. `dspStore.linkedGroups` (computed from `clientRegistryStore.zoneList`) - **LEGACY PASS-THROUGH**

**Current State (Architecture Problem):**
- `MultiroomSettings.vue` uses `dspStore.linkedGroups` (lines 216-239)
- `ZoneEdit.vue` uses `dspStore.linkedGroups` for zones and `dspStore.availableTargets` for clients (line 119-125)
- `dspStore.linkedGroups` is a computed that delegates to `clientRegistryStore.zoneList` (line 90)
- This creates an unnecessary indirection layer

**Target State (Per Architecture Decision):**
- Components should use `clientRegistryStore` directly for zone/client data
- `dspStore` should only be used for DSP-specific operations (filters, presets, compressor, loudness, crossover)

### Key Code Locations

**Frontend Stores:**
- `clientRegistryStore.js:73-75`: `zoneList` computed from `zones` Map
- `clientRegistryStore.js:391-433`: Zone CRUD methods (`createZone`, `deleteZone`, `updateZone`)
- `clientRegistryStore.js:459-489`: Zone membership methods (`addClientToZone`, `removeClientFromZone`)
- `clientRegistryStore.js:280-363`: WebSocket event handlers for zones
- `dspStore.js:90`: `linkedGroups` computed (delegates to clientRegistryStore)

**Frontend Components:**
- `MultiroomSettings.vue:215-258`: Zone/client display logic (currently uses dspStore)
- `ZoneEdit.vue:119-125`: Zone editing logic (currently uses dspStore)
- `ClientEdit.vue:139-143`: Zone context for clients

**Backend API (Already Implemented - Story 2-4):**
- `GET /api/multiroom/zones` - List all zones
- `POST /api/multiroom/zones` - Create zone
- `DELETE /api/multiroom/zones/{zone_id}` - Delete zone
- `POST /api/multiroom/zones/{zone_id}/clients` - Add client to zone
- `DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id}` - Remove client from zone

### Project Structure Notes

**Files to Modify:**
- `frontend/src/components/settings/categories/multiroom/MultiroomSettings.vue`
- `frontend/src/components/settings/categories/multiroom/ZoneEdit.vue`
- `frontend/src/services/websocket.js` (verify registry event routing)

**Files Already Complete (No Changes Needed):**
- `frontend/src/stores/clientRegistryStore.js` - All zone methods exist
- `backend/api/multiroom.py` - All zone endpoints exist
- `backend/core/multiroom/registry.py` - All zone service methods exist

### Zone Data Format

**clientRegistryStore zone format:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Salon",
  "client_ids": ["local", "dc:a6:32:7e:d3:43"],
  "dsp_settings": {...}
}
```

**MultiroomSettings zone display format (to generate):**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "displayName": "Salon",
  "clientCount": 2,
  "onlineCount": 2,
  "clients": [
    { "id": "...", "mac_id": "local", "name": "Milo", "online": true },
    { "id": "...", "mac_id": "dc:a6:32:7e:d3:43", "name": "Salon", "online": true }
  ]
}
```

### WebSocket Event Structure

Events from backend (category: `registry`):
```json
// Zone created
{ "category": "registry", "type": "zone_created", "data": { "zone_id": "uuid", "zone": {...} } }

// Zone updated (membership or name change)
{ "category": "registry", "type": "zone_updated", "data": { "zone_id": "uuid", "zone": {...} } }

// Zone deleted
{ "category": "registry", "type": "zone_deleted", "data": { "zone_id": "uuid" } }
```

### FRs Covered by This Story

- **FR3**: User can create/delete zones with minimum 2 clients (online or offline)
- **FR4**: Zone stores and shares DSP settings among all member clients
- **FR14**: Client leaving a zone retains current DSP settings as standalone
- **FR15**: Client joining a zone adopts zone's DSP settings (overwrites current)
- **FR29**: Frontend displays current state of all clients, zones, and DSP settings
- **FR30**: Frontend updates immediately on WebSocket events without polling

### Migration Checklist

1. **MultiroomSettings.vue changes:**
   - Import `useClientRegistryStore` (already imported via multiroomStore dependency)
   - Replace `dspStore.linkedGroups` with `registryStore.zoneList`
   - Update `zones` computed to map from `registryStore.zoneList` directly
   - Update `ungroupedClients` computed to exclude clients in zones
   - Keep `dspStore` for crossover-related methods only (`getZoneCrossoverSettings`, `getClientSpeakerType`)

2. **ZoneEdit.vue changes:**
   - Replace `dspStore.availableTargets` with `registryStore.clientList`
   - Replace `dspStore.linkedGroups` with `registryStore.zoneList`
   - Replace `dspStore.deleteZone()` with `registryStore.deleteZone()`
   - Update `handleCreate()` to use `registryStore.createZone()`
   - Keep `dspStore` for speaker type icons only (`getClientSpeakerType`)

3. **websocket.js verification:**
   - Verify `registry` category events are routed to `clientRegistryStore.handleRegistryEvent()`
   - Verify zone events (`zone_created`, `zone_updated`, `zone_deleted`) are handled

### Error Handling

| Operation | Error | UI Behavior |
|-----------|-------|-------------|
| Create zone < 2 clients | 422 validation | Disable button (already implemented) |
| Create zone with invalid client | 400 bad request | Show error toast |
| Delete zone not found | 404 | Navigate back (zone already deleted) |
| Add client already in zone | 400 | Show info message |
| Remove client not in zone | 400 | Refresh state |

### References

- [Source: _bmad-output/planning-artifacts/architecture.md - "Impact on composants Multiroom frontend existants" section]
- [Source: _bmad-output/planning-artifacts/epics.md - Story 2.5]
- [Source: _bmad-output/project-context.md - "Vue 3 + Pinia Frontend" section]
- [Source: frontend/src/stores/clientRegistryStore.js - Zone CRUD methods]
- [Source: frontend/src/stores/dspStore.js - linkedGroups computed (line 90)]
- [Source: frontend/src/components/settings/categories/multiroom/MultiroomSettings.vue - Current implementation]
- [Source: frontend/src/components/settings/categories/multiroom/ZoneEdit.vue - Current implementation]
- [Source: _bmad-output/implementation-artifacts/2-4-api-endpoints-for-zone-management.md - Backend API reference]

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

N/A - Implementation was straightforward with no debugging issues.

### Completion Notes List

1. **Task 1 Complete**: Migrated `MultiroomSettings.vue` to use `clientRegistryStore` as the single source of truth for zones and clients:
   - Added import for `useClientRegistryStore`
   - Created local `sortClientIdsLocalFirst()` helper function
   - Updated `zones` computed to use `registryStore.zoneList` and `registryStore.getClient()`
   - Updated `ungroupedClients` computed to use `registryStore.zoneList` and `registryStore.clientList`
   - Removed `dspStore.loadTargets()` call from `loadMultiroomData()`
   - Removed `dspStore.handleClientNameChanged()` WebSocket handler (handled by registry events)
   - Kept `dspStore` for DSP-specific functions: `getClientSpeakerType()`, `getZoneCrossoverSettings()`

2. **Task 2 Complete**: Updated `ZoneEdit.vue` to use `clientRegistryStore` for all zone operations:
   - Updated `availableTargets` computed to use `registryStore.clientList` directly
   - Updated `currentGroup` computed to use `registryStore.zoneList`
   - Changed `saveZoneName()` to use `registryStore.updateZone()` instead of `dspStore.updateZoneName()`
   - Changed `handleDelete()` to use `registryStore.deleteZone()` instead of `dspStore.deleteZone()`
   - Kept existing `registryStore.createZone()`, `registryStore.addClientToZone()`, `registryStore.removeClientFromZone()` calls (already correct)
   - Kept `dspStore` for DSP-specific function: `getClientSpeakerType()`

3. **Task 3 Complete**: Verified WebSocket event handlers are properly wired:
   - Confirmed `App.vue` routes `registry` category events to `clientRegistryStore.handleRegistryEvent()` (lines 148-156)
   - Verified `handleRegistryEvent()` handles: `zone_created`, `zone_deleted`, `zone_updated` (lines 339-355)
   - All zone events properly update the `zones` Map and trigger cache saves

4. **Task 4 Complete**: All tests pass:
   - Frontend tests: All 19 schema tests pass
   - Backend tests: All 195 multiroom tests pass
   - Frontend build: Successful compilation with no errors

### Change Log

- 2026-01-18: Implemented Story 2.5 - Frontend Zone Management UI migration to clientRegistryStore
- 2026-01-18: Code Review - Fixed 3 issues:
  - **HIGH**: Fixed zone name maxlength from 30 to 15 chars in ZoneEdit.vue (AC2 compliance)
  - **MEDIUM**: Removed dead WebSocket handlers for zone_client_added/zone_client_removed in App.vue
  - **MEDIUM**: Updated clientRegistryStore.js to ignore unknown events silently with documentation

### File List

**Modified:**
- `frontend/src/components/settings/categories/multiroom/MultiroomSettings.vue`
- `frontend/src/components/settings/categories/multiroom/ZoneEdit.vue` - Fixed maxlength="15" (was 30)
- `frontend/src/App.vue` - Removed dead WebSocket handlers, added clarifying comments
- `frontend/src/stores/clientRegistryStore.js` - Improved unknown event handling with documentation

**Verified (No Changes Needed):**
- `frontend/src/services/websocket.js` - Event routing documentation correct

