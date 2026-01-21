# Story 4.2: Implement DspService

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **system**,
I want **a DspService that manages DSP settings for clients and zones**,
So that **DSP changes are properly propagated and applied to CamillaDSP**.

## Acceptance Criteria

1. **AC1: DspService implementation**
   - **Given** DspSettings model exists (Story 4.1 ✅)
   - **When** I implement DspService in `core/dsp/service.py`
   - **Then** the service provides methods for zone and standalone clients
   - **And** the service integrates with ClientRegistryService for client/zone state

2. **AC2: Zone DSP propagation (FR16)**
   - **Given** a client is IN_ZONE
   - **When** DSP changes are made to the zone
   - **Then** zone.dsp_settings is updated (source of truth)
   - **And** changes are applied to all ONLINE clients via CamillaDSPProxy
   - **And** changes are persisted to settings.json via ClientRegistryService
   - **And** OFFLINE clients will receive settings on reconnection
   - **And** a WebSocket event `dsp_changed` is broadcast with target_type=zone

3. **AC3: Standalone DSP management**
   - **Given** a client is STANDALONE (not in any zone)
   - **When** DSP changes are made to the client
   - **Then** client.dsp_settings is updated via ClientRegistryService.set_standalone_dsp()
   - **And** changes are applied via CamillaDSPProxy
   - **And** changes are persisted to settings.json
   - **And** a WebSocket event `dsp_changed` is broadcast with target_type=client

4. **AC4: CamillaDSP failure handling**
   - **Given** CamillaDSP is unavailable (disconnected or error)
   - **When** DSP changes are requested
   - **Then** settings are saved to ClientRegistryService (source of truth)
   - **And** application fails silently with warning log
   - **And** client.dsp_ready is set to false (if we track this)
   - **And** no exception is raised to caller

5. **AC5: Multiroom mode DSP application**
   - **Given** a zone with multiple ONLINE clients
   - **When** DSP settings are changed
   - **Then** each ONLINE client receives the settings via its CamillaDSP instance
   - **And** changes complete within 200ms (NFR3)

## Tasks / Subtasks

- [x] Task 1: Create DspService class structure (AC: 1)
  - [x] Create `backend/core/dsp/multiroom_service.py` (new file for multiroom-aware DSP)
  - [x] Define DspService class with dependencies (ClientRegistryService, CamillaDSPService)
  - [x] Implement constructor with lazy dependency injection pattern
  - [x] Add logging and async lock for thread safety

- [x] Task 2: Implement zone DSP methods (AC: 2, 5)
  - [x] Implement `apply_zone_dsp(zone_id: str, settings: DspSettings)` method
  - [x] Get zone from ClientRegistryService
  - [x] Update zone.dsp_settings in ClientRegistryService
  - [x] For each ONLINE client in zone, apply settings via CamillaDSPProxy
  - [x] Broadcast `dsp_changed` event with target_type="zone"
  - [x] Implement `get_zone_dsp(zone_id: str) -> DspSettings` method

- [x] Task 3: Implement standalone client DSP methods (AC: 3)
  - [x] Implement `apply_client_dsp(mac_id: str, settings: DspSettings)` method
  - [x] Verify client is STANDALONE (zone_id is None)
  - [x] Update standalone_dsp via ClientRegistryService.set_standalone_dsp()
  - [x] Apply settings via CamillaDSPProxy
  - [x] Broadcast `dsp_changed` event with target_type="client"
  - [x] Implement `get_client_dsp(mac_id: str) -> DspSettings` method

- [x] Task 4: Implement target-agnostic DSP methods (AC: 2, 3)
  - [x] Implement `apply_dsp(target_type: str, target_id: str, settings: DspSettings)` method
  - [x] Route to zone or client method based on target_type
  - [x] Implement `get_dsp(target_type: str, target_id: str) -> DspSettings` method
  - [x] Validate target_type is "zone" or "client"

- [x] Task 5: Implement CamillaDSP application with error handling (AC: 4)
  - [x] Implement `_apply_to_camilladsp(mac_id: str, settings: DspSettings)` private method
  - [x] Check CamillaDSPService.connected before applying
  - [x] Apply filters via CamillaDSPService.set_filter() for each EQ band
  - [x] Apply compressor via CamillaDSPService.set_compressor()
  - [x] Apply loudness via CamillaDSPService.set_loudness()
  - [x] Wrap in try/except, log warning on failure, return success/failure
  - [x] Set dsp_ready state if applicable

- [x] Task 6: Implement partial DSP update methods (AC: 2, 3)
  - [x] Implement `update_filter(target_type, target_id, filter_id, **params)` method
  - [x] Implement `update_compressor(target_type, target_id, **params)` method
  - [x] Implement `update_loudness(target_type, target_id, **params)` method
  - [x] Implement `update_dsp_enabled(target_type, target_id, enabled: bool)` method
  - [x] Each method updates only specific settings, preserving others

- [x] Task 7: Register DspService in dependencies.py (AC: 1)
  - [x] Add DspService to service registry in `backend/dependencies.py`
  - [x] Ensure proper initialization order (after ClientRegistryService, CamillaDSPService)
  - [x] Wire dependencies via constructor injection

- [x] Task 8: Write unit tests (AC: 1, 2, 3, 4)
  - [x] Test zone DSP propagation with mock clients
  - [x] Test standalone client DSP management
  - [x] Test CamillaDSP failure handling (mock disconnected)
  - [x] Test partial update methods
  - [x] Test event broadcasting

## Dev Notes

### Context: This Story Bridges Models and API

Story 4.1 created the DspSettings model with typed sub-models (EqFilter, CompressorSettings, LoudnessSettings).
This story creates the service layer that:
1. Uses these models as the data structure
2. Integrates with ClientRegistryService for state management
3. Integrates with existing CamillaDSPService for hardware control
4. Provides the API layer with clean methods to call

### Existing Code Analysis

**CamillaDSPService (backend/core/dsp/service.py)**
- Already exists and handles direct CamillaDSP communication
- Has methods: `set_filter()`, `set_compressor()`, `set_loudness()`, `bypass_effects()`, `restore_effects()`
- Works with Dict format internally, not typed models
- Has its own state cache (`_filters`, `_compressor`, `_loudness`)
- Persists to `dsp.*` settings keys (separate from multiroom)

**ClientRegistryService (backend/core/multiroom/registry.py)**
- Manages zones and standalone DSP via `_standalone_dsp` dict
- Has methods: `get_zone()`, `get_standalone_dsp()`, `set_standalone_dsp()`
- Persists to `multiroom.*` settings keys
- Already emits `DSP_SETTINGS_CHANGED` events

**Key Decision: Create Multiroom DSP Service, Not Replace CamillaDSPService**

The existing `CamillaDSPService` handles:
- Direct CamillaDSP daemon communication (WebSocket)
- Volume control for the LOCAL device
- Filter/compressor/loudness for the LOCAL device
- Preset management for the LOCAL device

The NEW `DspService` (or `MultiroomDspService`) should:
- Coordinate DSP settings across multiple clients/zones
- Use ClientRegistryService as source of truth for settings
- Route DSP commands to appropriate CamillaDSP instances
- Handle zone propagation logic

### Architecture Decision: Service Coordination

```
API Layer
    │
    ▼
DspService (NEW - multiroom-aware)
    │
    ├─── ClientRegistryService (state/persistence)
    │       └── zone.dsp_settings, standalone_dsp
    │
    └─── CamillaDSPClientProxy (per-client DSP control)
             └── CamillaDSPService (local daemon)
             └── Remote client proxies (future)
```

**For now (single-device):**
- DspService manages settings in ClientRegistryService
- For "local" client, applies via existing CamillaDSPService
- For remote clients (satellites), will need client proxies (Epic 5+)

### WebSocket Event Structure (from Architecture)

```json
{
  "category": "multiroom",
  "type": "dsp_changed",
  "data": {
    "target_type": "zone",
    "target_id": "uuid-...",
    "dsp_settings": { ... }
  }
}
```

### NFR3 Performance Requirement

DSP filter changes must be applied within 200ms. This means:
- No synchronous blocking operations
- Parallel application to multiple clients if possible
- CamillaDSP commands are already async

### Integration with Existing Preset System

The existing CamillaDSPService has a preset system that:
- Saves gains to `dsp.manual_gains`
- Loads from `dsp.active_preset`
- Auto-switches to "Manual" on modification

This story focuses on zone/standalone DSP management. Presets will be handled in Story 4.6.

### Project Structure Notes

**Files to create:**
- `backend/core/dsp/multiroom_service.py` - New DspService for multiroom coordination

**Files to modify:**
- `backend/dependencies.py` - Register DspService
- `backend/tests/test_dsp_service.py` - Unit tests (may need to create)

**Related files (no changes needed now):**
- `backend/core/dsp/service.py` - Existing CamillaDSPService (used as dependency)
- `backend/core/multiroom/registry.py` - ClientRegistryService (used as dependency)
- `backend/core/multiroom/models.py` - DspSettings model (used for data)

### References

- [Source: backend/core/multiroom/models.py] - DspSettings, EqFilter, CompressorSettings, LoudnessSettings
- [Source: backend/core/multiroom/registry.py:754-807] - Standalone DSP methods
- [Source: backend/core/dsp/service.py:356-431] - CamillaDSPService.set_filter()
- [Source: backend/core/dsp/service.py:597-686] - CamillaDSPService.set_compressor()
- [Source: backend/core/dsp/service.py:692-769] - CamillaDSPService.set_loudness()
- [Source: _bmad-output/planning-artifacts/architecture.md#DSP-Propagation] - DSP architecture decisions
- [Source: _bmad-output/planning-artifacts/epics.md#Story-4.2] - Original story requirements
- [Source: _bmad-output/project-context.md#Framework-Specific-Rules] - Coding standards

### Previous Story Intelligence (4-1)

From Story 4.1 implementation:
- DspSettings model now uses typed sub-models (EqFilter, CompressorSettings, LoudnessSettings)
- Backward compatibility maintained via from_dict() methods
- Default factory `DspSettings.default()` creates flat 10-band EQ
- FilterType enum is defined in models.py (also duplicated in service.py - consider consolidating)
- All 1057 tests pass with new model structure

**Key patterns to follow:**
- Use `to_dict()` for serialization when saving to ClientRegistryService
- Use `from_dict()` when loading from ClientRegistryService
- Leverage DspSettings.default() for new clients/zones

### Git Context (Recent Commits)

```
9a31e2f fix(volume): sync _local_volume_db in multiroom mode to preserve volume on mode switch
57877fd fix(dsp): resolve preset loading and filter restoration issues
99a98b7 fix(multiroom): compute crossover_enabled dynamically based on subwoofer availability
```

**Relevant insights:**
- Volume sync between modes is important - DSP should follow similar pattern
- Preset loading/restoration was recently fixed - be careful with preset interactions
- crossover_enabled is computed dynamically - DSP enabled should work similarly

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

None - implementation completed without significant debugging issues.

### Completion Notes List

- **MultiroomDspService created**: New service `backend/core/dsp/multiroom_service.py` implements multiroom-aware DSP coordination
- **Zone DSP propagation (AC2, AC5)**: `apply_zone_dsp()` updates zone settings and applies to all ONLINE clients, broadcasts WebSocket event
- **Standalone client DSP (AC3)**: `apply_client_dsp()` manages standalone clients not in zones, validates client is STANDALONE
- **Target-agnostic methods (AC2, AC3)**: `apply_dsp()` and `get_dsp()` route to appropriate zone/client methods
- **CamillaDSP error handling (AC4)**: `_apply_to_camilladsp()` handles disconnection gracefully, logs warning, no exception raised to caller
- **Partial update methods**: `update_filter()`, `update_compressor()`, `update_loudness()`, `update_dsp_enabled()` allow granular updates
- **Service registration**: Added to `dependencies.py` with proper initialization order and circular dependency resolution
- **37 unit tests**: Comprehensive tests covering all acceptance criteria, all passing
- **1100+ total tests pass**: Full regression suite verified, no regressions

### Change Log

- 2026-01-20: Story implementation completed - MultiroomDspService, 36 unit tests, service registration
- 2026-01-20: Code review completed - Fixed 4 issues (H1: parallel client application, H2: encapsulation violation, M1: import location, M2: async method documentation), added 1 new test

### File List

**New files:**
- `backend/core/dsp/multiroom_service.py` - MultiroomDspService implementation
- `backend/tests/test_multiroom_dsp_service.py` - Unit tests (37 tests)

**Modified files:**
- `backend/core/dsp/__init__.py` - Export MultiroomDspService
- `backend/dependencies.py` - Register multiroom_dsp_service, add circular dependency resolution
- `backend/core/multiroom/registry.py` - Added public `set_zone_dsp()` method for proper encapsulation

