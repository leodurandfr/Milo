# Design — Unified per-client equalizer source of truth

Date: 2026-06-04
Status: Approved design (implementation pending, phased)
Author: Léo + Claude

## Problem

The equalizer state is split across **three overlapping stores** with a local/remote
asymmetry, which is the root cause of a family of bugs (wrong preset name vs. gains,
local EQ reverting after a backend restart):

1. `equalizer.json` (schema_version 2) — owned by `CamillaDSPService`
   ([backend/core/equalizer/service.py](../../backend/core/equalizer/service.py)).
   Drives the local DAC, loaded at boot. This is the **local** client's EQ.
2. `settings.json: multiroom.standalone_equalizer[mac]` — owned by `ClientRegistryService`
   ([backend/core/multiroom/client_registry.py](../../backend/core/multiroom/client_registry.py)).
   The **remote** standalone clients' EQ (and a transient carrier for the local client on
   zone deletion).
3. `settings.json: multiroom.zones[].equalizer_settings` — per-**zone** EQ.

Consequences observed:

- **Wrong name / right gains (local client).** The local client's gains come from the live
  CamillaDSP cache while its preset *name* comes from `CamillaDSPService._active_preset`
  (a separate field). Multiroom apply paths pushed gains with `persist=False` but never the
  name, so after a zone was deleted the local client showed a stale name against fresh gains.
  (Partially patched on `main`; see "Fate of the committed patches".)
- **Local EQ reverts after a backend restart.** On restart, `CamillaDSPService._load_saved_config`
  re-applies `equalizer.json` (never updated during multiroom because apply uses `persist=False`),
  and the registry → local re-sync is gated behind `is_new_client`
  ([backend/core/multiroom/websocket.py:339](../../backend/core/multiroom/websocket.py)),
  which is always false after a restart (the registry is persisted). So the local client loses
  the zone-inherited EQ (name **and** gains) on the next restart.

The asymmetry — local EQ in `equalizer.json`, remote EQ in the registry, plus a separate
zone store — is the structural cause. No amount of patching the transition points removes it.

## Goal

**One equalizer record per client.** A zone holds no EQ of its own; its EQ is simply the
(identical) EQ of its members. This eliminates the bug family by construction:

- name and gains always travel together in one record (no split);
- no zone-vs-client overlap;
- no local-vs-remote duplication for the same client;
- the local client's EQ is always in `equalizer.json`, so a restart restores it.

UX is unchanged from today (validated):

- Creating a zone sets all members to a **neutral** EQ.
- Editing a zone applies identically to all members.
- Leaving a zone (or zone deletion) keeps the current (zone) EQ — no "personal EQ" memory.

## Chosen approach — unified access, store-by-domain (option 1)

Rejected the "single physical file for all clients" option: it would force the **base audio
EQ** (which must work even when multiroom is fully disabled) to depend on a **multiroom**
data structure — a layering violation that is less clean and less robust. Instead, each EQ
record lives in the layer that owns its domain, behind a single access API:

- **Local client** → `equalizer.json` (unchanged role: drives the DAC, loaded at boot,
  works in all modes incl. multiroom-off). This is the local client's one record.
- **Remote clients** → `settings.json: multiroom.client_equalizer[mac]` (the renamed
  `standalone_equalizer`, now covering **all** remote clients uniformly — no standalone/zone
  duality). A remote client only exists when multiroom is on, so this is the right domain.
- **Zones** → drop `equalizer_settings`. Zone EQ is derived from members.

Each client maps to exactly one place; zones derive. No overlap.

### Access layer (in `MultiroomEqualizerService`)

A single per-client API replaces the scattered zone/standalone/local paths:

- `get_client_eq(mac) -> EqualizerSettings`
  → local: read from `CamillaDSPService`; remote: `registry.client_equalizer[mac]`.
- `set_client_eq(mac, eq)`
  → local: apply to the DAC **and persist `equalizer.json`** (name + gains together);
  → remote: write `registry.client_equalizer[mac]` **and push to the satellite**.
- Zone helpers built on top:
  - `set_zone_eq(zone_id, eq)` = `set_client_eq` for every member (keeps them identical);
  - `get_zone_eq(zone_id)` = `get_client_eq(first member)` (members are identical).

### Lifecycle (the validated UX, expressed in the new model)

- **Create zone** → `set_zone_eq(zone, neutral)`.
- **Edit zone** → `set_zone_eq(zone, eq)` (fan-out to members).
- **Add client to a zone** → adopt the zone's current EQ via `set_client_eq(member, get_zone_eq(zone))`.
- **Remove from / delete zone** → no EQ action; each client already owns its record. The
  local client's record is in `equalizer.json` → survives restart by construction.

### Boot / restoration

- **Local**: unchanged — `CamillaDSPService` loads `equalizer.json` → DAC. Robust and
  multiroom-independent. Because `set_client_eq(local)` always persists `equalizer.json`
  (including zone-applied EQ), the restart-revert bug disappears.
- **Remote**: `registry.client_equalizer[mac]` is pushed to satellites on (re)connect. The
  `is_new_client` guard no longer affects the local client (it no longer depends on the
  websocket re-sync).

### Frontend

`getApiBase` / `equalizerStore` / `EqualizerModal` unified so every client (local included)
is read/written through **one per-client record**. This removes the local/remote/zone
branching ([frontend/src/stores/equalizerStore.js](../../frontend/src/stores/equalizerStore.js)
lines ~161-173, ~225-244, ~631-655) that produced the name display bug.

## Persistence / migration

Per repo doctrine (no migration code; fail-loud + reset):

- Bump `schema_version` of `settings.json` (zones lose `equalizer_settings`;
  `standalone_equalizer` → `client_equalizer`).
- `equalizer.json` schema unchanged unless the record shape changes (then bump it too).
- On version mismatch the file resets from defaults on next boot (`SchemaVersionMismatch`
  banner). Add an entry to [BREAKING_CHANGES.md](../../BREAKING_CHANGES.md): files, version
  bumps, reason, `rm` commands, impact (**EQ settings reset once** on upgrade).

## Fate of the committed patches

The interim fixes already on `main` (commit `bbcd0337`) are folded into the new model and the
now-redundant code is removed (doctrine: one code path):

- `_apply_to_local` name-sync → becomes an intrinsic property of `set_client_eq(local)`.
- `save_custom_preset` local-name push → becomes `set_client_eq(local, preset="custom")`.
- The websocket re-sync name push (`_sync_*_to_client`) → kept for the **remote** push path;
  the local branch is removed (local no longer depends on the re-sync).

## Phasing (executed later, one phase per dedicated session)

1. **Backend model** — per-client EQ record + `get/set_client_eq` access API; drop zone EQ
   store (derive); rename `standalone_equalizer` → `client_equalizer`; schema bump + BREAKING_CHANGES.
2. **Backend boot/restore** — local always persists `equalizer.json` (incl. zone-applied);
   remote push on connect; remove the local branch of the re-sync.
3. **API** — unify equalizer routes to per-client; remove zone-EQ-specific endpoints.
4. **Frontend** — unify `getApiBase` + store + modal to the per-client record.
5. **Tests + manual Pi validation** — rework unit tests; re-run Test A/B/C scenarios on the Pi.

## Testing strategy

- Unit tests per phase (TDD): access-layer routing (local vs remote), zone fan-out keeps
  members identical, local `set_client_eq` persists `equalizer.json`, boot restores local EQ.
- Manual Pi validation of the original scenarios: apply preset to a zone → delete → check
  local name+gains; reboot backend → check local persists; save-custom → delete → check.

## Risks / notes

- Touches the audio-critical CamillaDSP path; keep the local DAC application logic unchanged,
  only widen *where* `set_client_eq(local)` persists.
- Frontend `getApiBase` unification must not break the multiroom-off (plain local) case.
- Out of scope: the "auto-save on band edit" UX question (Test C) — separate investigation.
