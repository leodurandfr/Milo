# Client Registry Migration Architecture

## Executive Summary

This document describes the migration from a fragmented zone/client management system to a unified architecture centered on `ClientRegistryService` as the Single Source of Truth (SSOT).

**Problem**: Zone configuration is stored in two locations (`dsp.linked_groups` and `multiroom.linked_groups`), causing crossover filters to fail silently.

**Solution**: Migrate all services to use `ClientRegistryService` for zone/client data.

---

## 1. Current State Analysis

### 1.1 Dual Storage Problem

| Component | Reads From | Writes To | Status |
|-----------|------------|-----------|--------|
| `ClientRegistryService` | `multiroom.linked_groups` | `multiroom.linked_groups` | ✅ New SSOT |
| `CrossoverService` | `dsp.linked_groups` | `dsp.linked_groups` | ❌ Stale path |
| `VolumeStateStore` | `dsp.linked_groups` | - | ❌ Stale path |
| DSP routes (`/api/dsp/links`) | `dsp.linked_groups` | `dsp.linked_groups` | ❌ Parallel API |
| Registry routes (`/api/registry/zones`) | `ClientRegistryService` | `ClientRegistryService` | ✅ Correct |

### 1.2 Broken Flow: Subwoofer Crossover

```
User sets client as "subwoofer"
    ↓
CrossoverService.set_client_speaker_type()
    ├─ Updates registry ✓
    ├─ Disables highpass on subwoofer ✓
    └─ Triggers _recalculate_zones_for_client()
        ├─ registry.get_zone_for_client() → finds zone ✓
        └─ apply_zone_crossover(zone_id)
            ├─ Reads dsp.linked_groups → EMPTY! ❌
            └─ zone = None → NO FILTERS APPLIED
```

**Result**: Subwoofers don't get lowpass filter, speakers don't get highpass filter.

### 1.3 Services Requiring Migration

#### CrossoverService (`backend/core/multiroom/crossover.py`)

**Current**: Reads zones from `dsp.linked_groups` settings in multiple methods:
- `get_zone_crossover()` - line 312
- `get_zone_auto_crossover()` - line 356
- `set_zone_crossover_frequency()` - line 397
- `apply_zone_crossover()` - line 453
- `_recalculate_zones_for_client()` - line 717 (fallback)

**Target**: Use `ClientRegistryService` exclusively for zone data.

#### VolumeStateStore (`backend/core/volume/state.py`)

**Current**: `_load_zones()` reads from `dsp.linked_groups` (line 276)

**Target**: Get zones from `ClientRegistryService` (already has `set_registry()` method).

#### DSP Routes (`backend/api/dsp.py`)

**Current**: `/api/dsp/links/*` endpoints manage zones via `dsp.linked_groups`

**Target**: Deprecate in favor of `/api/registry/zones/*` or redirect to registry.

---

## 2. Target Architecture

### 2.1 Single Source of Truth

```
┌─────────────────────────────────────────────────────────────────┐
│                    ClientRegistryService                         │
│                  (Single Source of Truth)                        │
│                                                                  │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │   Clients   │  │    Zones    │  │   Speaker Types         │  │
│  │  (dsp_id)   │  │  (groups)   │  │  (crossover config)     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
│                                                                  │
│  Persists to: multiroom.linked_groups, multiroom.client_types   │
└─────────────────────────────────────────────────────────────────┘
         │                    │                      │
         │ subscribe()        │ subscribe()          │ subscribe()
         ▼                    ▼                      ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐
│ CrossoverService│  │ VolumeStateStore│  │   SnapcastService   │
│                 │  │                 │  │                     │
│ - Speaker types │  │ - Zone volumes  │  │ - Client discovery  │
│ - Crossover     │  │ - Offsets       │  │ - Availability      │
│ - Lowpass/HP    │  │ - Persistence   │  │                     │
└─────────────────┘  └─────────────────┘  └─────────────────────┘
```

### 2.2 Event Flow

```
Zone/Client Change → ClientRegistryService
    │
    ├─ Emits ZONE_CREATED/UPDATED/DELETED
    │   └─ CrossoverService.on_zone_changed() → apply filters
    │
    ├─ Emits SPEAKER_TYPE_CHANGED
    │   └─ CrossoverService._recalculate_zones_for_client()
    │
    ├─ Emits AVAILABILITY_CHANGED
    │   ├─ CrossoverService → apply pending settings
    │   └─ VolumeStateStore → update availability
    │
    └─ Broadcasts via WebSocket → Frontend
```

### 2.3 Zone Model (Already in Domain)

```python
# backend/core/multiroom/client_registry.py - Zone dataclass
@dataclass
class Zone:
    id: str
    name: str
    client_ids: List[str]
    crossover_frequency: int = 80        # Zone-wide crossover
    crossover_enabled: bool = True       # Whether crossover is active
```

This model already exists and has all needed fields.

---

## 3. Migration Plan

### Phase 1: CrossoverService Migration (Critical - Fixes Bug)

**Files to modify**:
- `backend/core/multiroom/crossover.py`

**Changes**:

1. **Remove direct settings reads** - Replace all `settings_service.get_setting("dsp.linked_groups")` with registry queries

2. **Use registry for zone data**:
```python
# BEFORE
linked_groups = await self.settings_service.get_setting("dsp.linked_groups") or []
zone = next((g for g in linked_groups if g.get("id") == zone_id), None)

# AFTER
zone = self._registry.get_zone(zone_id)
```

3. **Subscribe to zone events** - Already subscribes, but ensure handlers exist for:
   - `ZONE_CREATED` → apply_zone_crossover
   - `ZONE_UPDATED` → apply_zone_crossover
   - `ZONE_CLIENT_ADDED` → apply_zone_crossover
   - `ZONE_CLIENT_REMOVED` → apply_zone_crossover

**Methods to refactor**:
| Method | Current | Target |
|--------|---------|--------|
| `get_zone_crossover()` | settings read | `_registry.get_zone()` |
| `get_zone_auto_crossover()` | settings read | `_registry.get_zone()` |
| `set_zone_crossover_frequency()` | settings write | `_registry.update_zone()` |
| `apply_zone_crossover()` | settings read | `_registry.get_zone()` |
| `_recalculate_zones_for_client()` | fallback to settings | registry only |

### Phase 2: VolumeStateStore Migration

**Files to modify**:
- `backend/core/volume/state.py`

**Changes**:

1. **Replace `_load_zones()`** - Use registry instead of settings:
```python
# BEFORE
async def _load_zones(self) -> None:
    linked_groups = await self.settings_service.get_setting("dsp.linked_groups") or []
    ...

# AFTER
async def _load_zones(self) -> None:
    if self._registry:
        for zone in self._registry.get_all_zones().values():
            self._zones[zone.id] = ZoneConfig(
                zone_id=zone.id,
                name=zone.name,
                client_ids=zone.client_ids.copy()
            )
```

2. **Subscribe to zone change events** - Refresh zones when registry changes

### Phase 3: API Route Consolidation

**Files to modify**:
- `backend/api/dsp.py` - Deprecate `/api/dsp/links/*`
- `backend/core/multiroom/routes.py` - Ensure complete zone API

**Options**:

**Option A - Redirect** (Minimal disruption):
```python
@router.get("/links")
async def get_linked_clients():
    """DEPRECATED: Use /api/registry/zones instead"""
    zones = client_registry.get_all_zones()
    # Convert to legacy format for backward compatibility
    return {"linked_groups": [z.to_dict() for z in zones.values()]}
```

**Option B - Remove** (Clean break):
- Remove all `/api/dsp/links/*` routes
- Update frontend to use `/api/registry/zones/*`
- This is cleaner but requires frontend changes

**Recommendation**: Option A first, then Option B in a follow-up.

### Phase 4: Settings Migration (One-time)

**Purpose**: Migrate any existing `dsp.linked_groups` data to `multiroom.linked_groups`

**Implementation**: Add to `ClientRegistryService.initialize()`:
```python
# One-time migration from dsp.linked_groups → multiroom.linked_groups
legacy_groups = await self._settings_service.get_setting("dsp.linked_groups")
if legacy_groups and not zones_data:
    self.logger.info(f"Migrating {len(legacy_groups)} zones from dsp.linked_groups")
    await self._settings_service.set_setting("multiroom.linked_groups", legacy_groups)
    await self._settings_service.set_setting("dsp.linked_groups", None)  # Clear legacy
    zones_data = legacy_groups
```

### Phase 5: Cleanup

1. Remove all references to `dsp.linked_groups` from codebase
2. Update tests to use registry patterns
3. Remove `ZoneConfig` duplication in VolumeStateStore (use domain Zone)

---

## 4. Implementation Order

```
┌────────────────────────────────────────────────────────────────┐
│ Phase 1: CrossoverService Migration (CRITICAL - FIXES BUG)     │
│ Estimated: 1-2 hours                                           │
│ Files: crossover_service.py                                    │
│ Risk: Low (service already has registry reference)             │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│ Phase 2: VolumeStateStore Migration                            │
│ Estimated: 1 hour                                              │
│ Files: volume_state.py                                         │
│ Risk: Low (already has registry reference and event handling)  │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│ Phase 3: API Route Consolidation                               │
│ Estimated: 2 hours (including frontend updates)                │
│ Files: dsp.py routes, frontend multiroom components            │
│ Risk: Medium (API changes affect frontend)                     │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│ Phase 4: Settings Migration                                    │
│ Estimated: 30 minutes                                          │
│ Files: client_registry_service.py                              │
│ Risk: Low (one-time migration with fallback)                   │
└────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────┐
│ Phase 5: Cleanup                                               │
│ Estimated: 1 hour                                              │
│ Files: tests, remove legacy code                               │
│ Risk: Low                                                      │
└────────────────────────────────────────────────────────────────┘
```

---

## 5. Testing Strategy

### Unit Tests

1. **CrossoverService**
   - Test `apply_zone_crossover()` with registry mock
   - Test zone with/without subwoofer
   - Test speaker type changes trigger recalculation

2. **VolumeStateStore**
   - Test zone loading from registry
   - Test zone updates via events

### Integration Tests

1. **End-to-End Crossover Flow**
   ```python
   # 1. Create zone via registry
   # 2. Add clients to zone
   # 3. Set one client as subwoofer
   # 4. Verify: subwoofer has lowpass, others have highpass
   ```

2. **Volume Zone Operations**
   ```python
   # 1. Create zone
   # 2. Apply zone volume delta
   # 3. Verify all clients updated
   ```

---

## 6. Rollback Plan

If issues arise:

1. **Phase 1-2**: Revert to reading from both locations (registry first, fallback to settings)
2. **Phase 3**: Keep legacy routes active alongside new ones
3. **Phase 4**: Migration is additive, no data loss

---

## 7. Success Criteria

- [ ] Setting a client as "subwoofer" applies lowpass filter to subwoofer
- [ ] Setting a client as "subwoofer" applies highpass filter to other zone speakers
- [ ] Zone volume operations work correctly
- [ ] No references to `dsp.linked_groups` remain in codebase
- [ ] All tests pass
- [ ] Frontend multiroom controls work without changes (Phase 3 Option A)

---

## 8. Recommended Workflow

### Agent Sequence

1. **Architect (current)** → Created this document ✓

2. **Dev Agent** (`/dev` or `/bmad:bmm:workflows:dev-story`)
   - Execute Phase 1: CrossoverService migration
   - Execute Phase 2: VolumeStateStore migration
   - Execute Phase 4: Settings migration
   - Execute Phase 5: Cleanup

3. **Code Review** (`/bmad:bmm:workflows:code-review`)
   - Review each phase before merging

4. **QA/Testing** (manual or automated)
   - Verify crossover filters work
   - Verify zone volume operations

### Recommended Commands

```bash
# After each phase, test manually:
sudo systemctl restart milo-backend
# Then in UI: Create zone → Add clients → Set subwoofer → Verify audio

# Check logs for crossover operations:
sudo journalctl -u milo-backend -f | grep -i crossover
```

---

## Appendix A: Files to Modify

| File | Phase | Changes |
|------|-------|---------|
| `backend/core/multiroom/crossover.py` | 1 | Replace settings reads with registry |
| `backend/core/volume/state.py` | 2 | Replace `_load_zones()` with registry |
| `backend/api/dsp.py` | 3 | Deprecate `/links/*` routes |
| `backend/core/multiroom/client_registry.py` | 4 | Add migration code |
| `backend/tests/test_crossover_service.py` | 5 | Update mocks |
| `backend/tests/test_volume_service.py` | 5 | Update mocks |

## Appendix B: Settings Path Reference

| Path | Purpose | After Migration |
|------|---------|-----------------|
| `multiroom.linked_groups` | Zone configuration | ✅ Keep (SSOT) |
| `multiroom.client_types` | Speaker types | ✅ Keep |
| `dsp.linked_groups` | Legacy zone storage | ❌ Remove |
