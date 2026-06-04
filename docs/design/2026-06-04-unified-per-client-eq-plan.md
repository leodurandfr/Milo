# Unified per-client equalizer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to implement this plan **one phase at a time**.
> Steps use checkbox (`- [ ]`) syntax. Each phase is a self-contained, committable unit and is
> intended to be executed in its **own fresh conversation**. Within a phase, follow TDD
> (superpowers:test-driven-development): write the failing test, watch it fail, implement the
> minimum, watch it pass, then run `code-review` on the diff before committing.

**Goal:** Collapse the three overlapping equalizer stores into **one EQ record per client**,
with zones deriving their EQ from members — eliminating the wrong-name and reboot-revert bugs
by construction.

**Architecture:** Unified access API (`get/set_client_eq(mac)`) with storage-by-domain — the
local client's record stays in `equalizer.json` (drives the DAC, boot-loaded, multiroom-independent);
remote clients' records live in `settings.json: multiroom.client_equalizer[mac]`; zones store no EQ.
See the design: [2026-06-04-unified-per-client-eq.md](2026-06-04-unified-per-client-eq.md).

**Tech Stack:** FastAPI/Python backend, Vue 3/Pinia frontend, ALSA + CamillaDSP, Snapcast multiroom.

**Conventions (every phase):**
- Solo dev → commit directly on `main` (no feature branch).
- TDD per change; run `cd backend && python -m pytest -q` before each commit; backend CI floor
  must stay green: `ruff check backend/`, and `ruff check --select F401,F841` on touched files.
- Frontend: `cd frontend && npm run lint:js && npm run lint:css` for touched files.
- Run `code-review` (feature-dev:code-reviewer) on the diff before committing.
- Persisted-data changes: bump `schema_version`, add a [BREAKING_CHANGES.md](../../BREAKING_CHANGES.md)
  entry, let the file reset on boot (no migration code).
- After each phase, STOP for human review before starting the next phase.

---

## File map (whole refactor)

**Backend**
- `backend/core/multiroom/models.py` — `EqualizerSettings` (already holds filters/preset/compressor/
  loudness/mono/custom_gains); remove `Zone.equalizer_settings`.
- `backend/core/multiroom/client_registry.py` — rename `_standalone_equalizer` → `_client_equalizer`
  (covers all remote clients); simplify `_make_clients_standalone` (clients already own their record);
  drop zone EQ persistence; bump settings `SCHEMA_VERSION`.
- `backend/core/equalizer/service.py` (`CamillaDSPService`) — the local record store; expose a public
  `persist_state()`; otherwise DAC logic unchanged.
- `backend/core/equalizer/multiroom_service.py` — the access layer: `get_client_eq`/`set_client_eq`,
  `get_zone_eq`/`set_zone_eq`; rework `apply_*`/`load_*_preset`/`save_custom_preset`/`update_*` onto it.
- `backend/core/multiroom/websocket.py` — remove the **local** branch of `_sync_*_to_client`; remote
  push reads `client_equalizer`; zone re-sync derives from a member record.
- `backend/api/equalizer.py` — unify routes to per-client; remove zone-EQ-specific endpoints.
- `backend/api/multiroom.py` — zone create → `set_zone_eq(neutral)`; delete/remove → no EQ action.
- `backend/dependencies.py` — boot: local EQ applied from `equalizer.json`; remote push on connect.
- `BREAKING_CHANGES.md` — entry for the schema bump.
- `backend/tests/` — rework `test_core_multiroom.py`, `test_multiroom_equalizer_service.py`,
  `test_core_equalizer.py`, `test_api_multiroom.py`.

**Frontend**
- `frontend/src/stores/equalizerStore.js` — unify `getApiBase` + fetch onto one per-client record.
- `frontend/src/components/equalizer/EqualizerModal.vue` — read name+gains from one record.
- `frontend/src/schemas/ws.js` / multiroom store — adjust if EQ payload shapes change.

---

## Phase 1 — Backend model: per-client record + access API + drop zone store

**Outcome:** One EQ record per client behind `get/set_client_eq`; zones derive; `standalone_equalizer`
renamed to `client_equalizer`; settings schema bumped. Audio behavior unchanged.

### Task 1.1 — Rename `standalone_equalizer` → `client_equalizer` (registry)
**Files:** Modify `backend/core/multiroom/client_registry.py`; Test `backend/tests/test_core_multiroom.py`.
- [x] Write/adjust failing tests: registry stores/reads a per-client EQ record via the new
      `get_client_equalizer`/`set_client_equalizer` names for a remote client.
- [x] Run tests → fail (old names).
- [x] Rename `_standalone_equalizer` and its public accessors to `client_equalizer`; update the
      persisted settings key; update all in-repo callers (`grep -rn standalone_equalizer backend/`).
- [x] Run tests → pass. Run `ruff check --select F401,F841` on touched files.
- [x] Commit: `refactor(eq): rename standalone_equalizer → client_equalizer`.

### Task 1.2 — Drop `Zone.equalizer_settings`; zone EQ derives from members
**Files:** Modify `backend/core/multiroom/models.py`, `backend/core/multiroom/client_registry.py`;
Test `backend/tests/test_core_multiroom.py`.
- [x] Write failing tests: creating a zone sets each member's `client_equalizer` to a neutral
      record; `_make_clients_standalone` is a no-op for EQ (members already own their record);
      `Zone` no longer exposes `equalizer_settings`.
- [x] Run → fail.
- [x] Remove `Zone.equalizer_settings`; `create_zone` writes a neutral record per member;
      `_make_clients_standalone` only flips `zone_id = None`; remove zone-EQ persistence.
- [x] Run → pass.
- [x] Commit: `refactor(eq): zones derive EQ from members (drop zone equalizer_settings)`.
      *(Landed together with Task 1.3 as one atomic commit — see note below.)*

### Task 1.3 — Access API in `MultiroomEqualizerService`
**Files:** Modify `backend/core/equalizer/multiroom_service.py`, `backend/core/equalizer/service.py`
(add public `persist_state()`); Test `backend/tests/test_multiroom_equalizer_service.py`.
- [x] Write failing tests: `get_client_eq(local)` reads CamillaDSP; `get_client_eq(remote)` reads
      registry; `set_client_eq(local, eq)` applies to DAC **and** persists `equalizer.json`;
      `set_client_eq(remote, eq)` writes registry; `set_zone_eq(zone, eq)` writes every member;
      `get_zone_eq(zone)` returns a member's record.
- [x] Run → fail.
- [x] Implement `get/set_client_eq`, `get/set_zone_eq`; rework `apply_zone_equalizer`,
      `apply_client_equalizer`, `load_zone_preset`, `load_client_preset`, `save_custom_preset`,
      `update_filter/compressor/loudness/mono` to route through them. Remove the now-redundant
      interim helpers (`_affects_local_client`, the inline `_apply_to_local` name-sync) — folded in.
- [x] Run → pass.
- [x] Commit: `feat(eq): unified get/set_client_eq access layer`.

### Task 1.4 — Schema bump + BREAKING_CHANGES
**Files:** Modify `backend/core/multiroom/client_registry.py` (`SCHEMA_VERSION`), `BREAKING_CHANGES.md`;
Test `backend/tests/test_breaking_changes_coherence.py` (keep coherent).
- [ ] Bump settings `SCHEMA_VERSION`; add a BREAKING_CHANGES entry (file, bump, reason, `rm` command,
      impact: **EQ settings reset once**).
- [ ] Run `python -m pytest backend/tests/test_breaking_changes_coherence.py -q` → pass.
- [ ] Commit: `chore(eq): bump settings schema_version for unified client EQ`.

> **Phase 1 was expanded (per Léo, 2026-06-04):** dropping `Zone.equalizer_settings` cannot keep the
> suite green without also reworking the websocket reconnect-sync (Phase 2) and the zone-create /
> add-client API (Phase 3), so Tasks 1.2 + 1.3 plus those indissociable bits landed as **one atomic
> commit**. Beyond the literal task text it also: removed `websocket._sync_zone_equalizer_to_client`
> (reconnect now uses the per-client record for all contexts; the local client is a natural no-op);
> made `api/multiroom` zone-create apply a neutral EQ and add-client adopt the zone's EQ; added
> `CamillaDSPService.persist_state()/get_equalizer_settings()/set_custom_gains()`. One robustness item
> (disconnected-CamillaDSP cache drift, from the Phase-1 code review) was **deferred to Phase 2** —
> see its note. Task 1.4 (schema bump) is the remaining Phase-1 commit.

**Phase 1 acceptance:** full `pytest` green; `ruff` clean; no `standalone_equalizer`/
`zone.equalizer_settings` references remain (`grep`); `code-review` clean. STOP for review.

---

## Phase 2 — Backend boot/restore

**Outcome:** Local EQ always persisted to `equalizer.json` (incl. zone-applied) and restored at boot;
remote records pushed to satellites on (re)connect; the local branch of the websocket re-sync removed.

### Task 2.1 — Remove the local branch of `_sync_*_to_client`
**Files:** Modify `backend/core/multiroom/websocket.py`; Test `backend/tests/test_core_multiroom.py`.
- [ ] Adjust tests: `_sync_standalone_equalizer_to_client` / `_sync_zone_equalizer_to_client` push
      to **remote** satellites only; the local client is NOT driven through this path (it owns
      `equalizer.json`). Remote push reads `client_equalizer`.
- [ ] Run → fail. Implement. Run → pass.
- [ ] Commit: `refactor(eq): local client no longer restored via websocket re-sync`.

### Task 2.2 — Boot application of local EQ + remote push
**Files:** Modify `backend/dependencies.py`, `backend/core/multiroom/websocket.py`; Test the relevant
init/sync tests.
- [ ] Write failing tests: at boot, the local CamillaDSP reflects `equalizer.json`; a remote client
      that connects (new or already-known) gets its `client_equalizer` pushed.
- [ ] Run → fail. Implement (ensure remote push isn't lost when already-known). Run → pass.
- [ ] Commit: `fix(eq): restore local EQ from equalizer.json at boot; push remote on connect`.
- [ ] **Carried over from Phase 1 code review (disconnected-CamillaDSP cache drift):** when
      `set_client_eq(local)` / `_apply_partial_update` run while CamillaDSP is **disconnected**,
      `_apply_to_local` no-ops (set_filter guards on `connected`) and nothing updates the in-memory
      cache, so the local intent is dropped and `equalizer.json` drifts from the remote members'
      records. Add a `CamillaDSPService.update_cache(settings)` that unconditionally overwrites the
      cache (`_filters/_compressor/_loudness/_mono/_active_preset/_custom_gains`) + persists, and call
      it from the disconnected path so the reconnect (`restore_effects`) pushes the correct values.
      Connected path is already correct; this only closes the boot/reconnect window. Test: local EQ
      set while disconnected is persisted and restored on reconnect.

**Phase 2 acceptance:** full `pytest` green; `code-review` clean. STOP for review.

---

## Phase 3 — API unification

**Outcome:** Equalizer routes are per-client and uniform; zone-EQ-specific endpoints removed.

### Task 3.1 — Per-client equalizer routes
**Files:** Modify `backend/api/equalizer.py`, `backend/api/multiroom.py`; Test `backend/tests/test_api_multiroom.py`.
- [ ] Write failing route tests: read/write EQ for any client (incl. local) via one per-client
      endpoint shape; zone routes apply via `set_zone_eq`; removed endpoints return 404.
- [ ] Run → fail. Implement: route handlers call `get/set_client_eq` and `set_zone_eq`; delete
      dead zone-EQ endpoints; zone create → neutral.
- [ ] Run → pass. Confirm Milo-Mac external client contract isn't broken (grep its endpoints).
- [ ] Commit: `refactor(eq): unify equalizer API to per-client`.

**Phase 3 acceptance:** full `pytest` green; `code-review` clean. STOP for review.

---

## Phase 4 — Frontend unification

**Outcome:** The store and modal treat every client (local included) as one per-client record.

### Task 4.1 — Unify `getApiBase` + fetch in the store
**Files:** Modify `frontend/src/stores/equalizerStore.js`; (no vitest configured — rely on manual + lint).
- [ ] Replace the local/remote/zone branching with one per-client fetch of `{filters, active_preset,
      compressor, loudness, mono}`. Remove the `!isLocalClient` gates and the dual name/gains sources.
- [ ] `npm run lint:js` → clean.
- [ ] Commit: `refactor(eq): frontend store reads one record per client`.

### Task 4.2 — Modal reads one record
**Files:** Modify `frontend/src/components/equalizer/EqualizerModal.vue`; adjust `schemas/ws.js` if
EQ payloads changed.
- [ ] Name and gains come from the same per-client record; no cross-source reconciliation.
- [ ] `npm run lint:js && npm run lint:css` → clean.
- [ ] Commit: `refactor(eq): modal renders one per-client EQ record`.

**Phase 4 acceptance:** lint clean; build (`npm run build`) succeeds; `code-review` clean. STOP for review.

---

## Phase 5 — Tests sweep + manual Pi validation

**Outcome:** Test suites reflect the new model; the original scenarios pass on real hardware.

### Task 5.1 — Test sweep
- [ ] Remove tests asserting the old 3-store behavior; ensure access-layer, zone-fan-out, boot-restore
      have coverage. Full `pytest` green; `ruff` + JS/CSS lint clean.
- [ ] Commit: `test(eq): align suites with unified per-client EQ`.

### Task 5.2 — Manual Pi validation (deploy: `sudo systemctl restart milo-backend`)
- [ ] **A** — zone (local+remote), apply a preset, delete zone → local shows correct name + gains.
- [ ] **B** — after A, `sudo systemctl restart milo-backend` (and a full `sudo reboot`) → local keeps
      the EQ (name + gains).
- [ ] **C** — zone, edit a band, "Save as custom", delete zone → local shows "Custom" + gains.
- [ ] Confirm multiroom **disabled** (plain local playback) still has correct local EQ.
- [ ] Note the one-time EQ reset is expected (schema bump).

**Phase 5 acceptance:** all manual scenarios pass from a clean state. Refactor complete.

---

## Self-review (against the design)

- Spec coverage: model (P1), access layer (P1.3), zones derive (P1.2), boot/restore (P2),
  frontend (P4), persistence/migration (P1.4), fate of committed patches (P1.3/P2.1), testing (P5). ✔
- The committed interim patches (`bbcd0337`) are explicitly removed/folded in P1.3 and P2.1.
- Names consistent across tasks: `get_client_eq`/`set_client_eq`, `get_zone_eq`/`set_zone_eq`,
  `client_equalizer`, `persist_state()`.
