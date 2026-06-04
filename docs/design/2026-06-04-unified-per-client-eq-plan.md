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
- [x] Bump settings `SCHEMA_VERSION`; add a BREAKING_CHANGES entry (file, bump, reason, `rm` command,
      impact: **EQ settings reset once**).
- [x] Run `python -m pytest backend/tests/test_breaking_changes_coherence.py -q` → pass.
- [x] Commit: `chore(eq): bump settings schema_version for unified client EQ`.

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
- [x] Adjust tests: `_sync_standalone_equalizer_to_client` / `_sync_zone_equalizer_to_client` push
      to **remote** satellites only; the local client is NOT driven through this path (it owns
      `equalizer.json`). Remote push reads `client_equalizer`.
      *(`_sync_zone_equalizer_to_client` was already removed in Phase 1; only the standalone path
      remained. New `test_local_client_is_noop` replaces the old local-applies tests.)*
- [x] Run → fail. Implement. Run → pass.
- [x] Commit: `refactor(eq): local client no longer restored via websocket re-sync` (`fd9cd44c`).
      *(Also removed the now-orphaned `_camilladsp_service` field/setter on
      `SnapcastWebSocketService` + its `dependencies.py` wiring, and the `is_local` branch of
      `_apply_equalizer_setting` — all dead once the local branch is gone.)*

### Task 2.2 — Boot application of local EQ + remote push
**Files:** Modified `backend/core/equalizer/service.py`, `backend/core/equalizer/multiroom_service.py`,
`backend/core/multiroom/websocket.py`; Tests `test_core_equalizer.py`, `test_multiroom_equalizer_service.py`,
`test_core_multiroom.py`.

> **Scope clarified with Léo (2026-06-04) — investigation revised the "remote push" sub-task.**
> Satellites **self-persist** their own EQ (`/var/lib/milo-client/camilladsp/config.yml`, restored on
> their own boot), and the normal `Client.OnConnect` path already re-pushes the server's record on
> reconnect — so there was no real "remote EQ lost" bug. Léo's requirement: a member that missed a
> zone-EQ change while offline **must** auto-recover it on reconnect. Chosen **Option B** (guarantee it
> on *every* reconnect path): the secondary `Server.OnUpdate` status-flip path
> (`_do_sync_reconnecting_client_volume`, historically volume-only) now also re-pushes the EQ record.
> "Boot restores local EQ" is unchanged existing behavior (`_load_saved_config` + `restore_effects`),
> locked by a characterization test rather than new code.

- [x] Write failing tests: boot restores local EQ from `equalizer.json` (characterization lock);
      a remote client reconnecting via the secondary path gets its EQ re-pushed; disconnected-set →
      reconnect → `restore_effects` end-to-end.
- [x] Run → fail. Implement. Run → pass.
- [x] **Carried over from Phase 1 code review (disconnected-CamillaDSP cache drift):** added
      `CamillaDSPService.update_cache(settings)` (unconditional cache overwrite of
      `_filters/_compressor/_loudness/_mono/_active_preset/_custom_gains` + persist; leaves
      `_effects_enabled` alone). `set_client_eq(local)` and `_apply_partial_update` call it on the
      **disconnected** path so the intent survives to reconnect, where `restore_effects` re-pushes it
      (no `equalizer.json` ↔ remote-member drift).
- [x] **Option B remote re-push:** `_do_sync_reconnecting_client_volume` re-pushes the client's EQ
      record after volume confirms / before marking online (no-op for the local client).
- [x] Commit: `fix(eq): persist local EQ on disconnected DAC; re-push remote EQ on every reconnect`
      (`419b2cee`).

**Phase 2 acceptance:** full `pytest` green (1573); `ruff` clean; `code-review` clean. STOP for review.

---

## Phase 3 — API unification

**Outcome:** Equalizer routes are per-client and uniform; zone-EQ-specific endpoints removed.

### Task 3.1 — Per-client equalizer routes

> **Phase 3 scoped to Option A (per Léo, 2026-06-04).** Investigation revised this task. With
> multiroom **OFF** the local Milō has **no entry in the client registry** (it is populated only by
> Snapcast connections), so a single `/client/{mac}/…` shape cannot address the local device — it has
> no MAC the frontend can use — and the design deliberately keeps base-audio EQ independent of the
> multiroom registry. Confirmed **no external client** (Milo-Mac, milo-client satellites) calls the
> server's `/api/equalizer/*` (comms are server→satellite only). Frontend safety verified: the 5
> client write routes only read `result.ok`, and `handleEqualizerChanged` already handles
> `target_type:"client"` (raw `on()`, no schema rejection).
>
> Léo chose **Option A**: the local client keeps its dedicated non-scoped `/api/equalizer/…` routes;
> only the **remote per-client** and **zone** EQ writes are unified. So Phase 3 is an **internal-only**
> refactor — **no URL changes, no frontend-breakage window**. The remote partial-update routes route
> through the unified access layer instead of the duplicate `equalizer_router + _persist_remote` path.
> Side benefit: offline remote clients now **persist** EQ edits (sync on reconnect) instead of
> silently dropping them. No endpoints are removed (the zone + local routes are still needed by the
> Phase-4 frontend); the plan's original "removed endpoints return 404" / "zone create → neutral" /
> "add-client adopts zone EQ" items were already satisfied in Phase 1's atomic commit.

**Files:** Modify `backend/api/equalizer.py`, `backend/core/equalizer/multiroom_service.py`;
Test `backend/tests/test_api_equalizer.py` (new), `backend/tests/test_multiroom_equalizer_service.py`.
- [x] Write failing tests: `PUT /client/{mac}/{filter,compressor,loudness,mono}` route through
      `multiroom_equalizer_service.update_*` with `target_type="client"`; `PUT /client/{mac}/enabled`
      routes through a new `set_client_equalizer_effects_enabled`; not-found → 404; local non-scoped
      routes unchanged. Service tests for `set_client_equalizer_effects_enabled` (local→routing,
      remote-online→push+persist, remote-offline→persist-only). *(New `tests/test_api_equalizer.py`
      + additions to `test_multiroom_equalizer_service.py`; the old proxy-path asserts in
      `tests/integration/test_equalizer_zone_endpoints.py` retargeted at the access layer.)*
- [x] Run → fail. Implement: rewrite the 5 remote partial routes onto the access layer; add
      `set_client_equalizer_effects_enabled` and share `_set_remote_client_enabled` with the zone
      enabled fan-out; delete the now-dead `_persist_remote` + `_eqfilter_from_body`.
- [x] Run → pass. `ruff check backend/` + `--select F401,F841` on touched files.
- [x] **Code-review fixes folded in:** filter route uses the `EqualizerFilterUpdateRequest` Pydantic
      model (no `freq`/`frequency` dual-key fallback — Pitfall #11); the 4 `update_*` and
      `set_client_equalizer_effects_enabled` now **fail loud** (`ValueError` → 404) for an unknown MAC
      instead of materializing a phantom record; the enabled route maps that `ValueError` → 404 like
      its siblings; real-path service tests added for the unknown-client guards.
- [x] Commit: `refactor(eq): unify remote per-client EQ writes onto the access layer`.

**Phase 3 acceptance:** full `pytest` green (1597); `ruff` clean; `code-review` clean. STOP for review.

---

## Phase 3.5 — Backend: uniform per-target EQ API (ADDED 2026-06-04, Option B)

> **Architecture pivot (Léo approved "do Option B properly"):** the 3-path split shipped in Phase 3
> (Option A) is incoherent and already buggy (no local `PUT /mono` → Mono toggle is a 404 in prod) and
> incompatible with a one-record frontend. Replace it with **one uniform per-target API**. See the
> design-doc addendum (`2026-06-04-unified-per-client-eq.md` → "Addendum — Option B"). The frontend is
> the **only** consumer of `/api/equalizer/*`, so there is no external blast radius. **No schema bump**
> (transport-only). Sequencing keeps `main` deployable: Phase 3.5 is **additive** (old routes stay);
> the old routes are deleted as the **last task of Phase 4**, once nothing calls them.

**Outcome:** `GET /api/equalizer/target/{target}` returns the complete record and
`PUT/POST /api/equalizer/target/{target}/…` writes it, for `target ∈ local · <mac> · zone:<id>`,
all dispatching through the existing access layer. Old routes untouched and still green.

**Verified contracts:** `routing.set_equalizer_effects_enabled(enabled, active_source=None)` (optional
→ safe to unify); `EqualizerSettings.to_dict()` = `{enabled, filters, compressor, loudness,
active_preset, mono, custom_gains?}`; `EqFilter.to_dict()` emits `frequency`/`filter_type` but the
frontend + local `/filters` use `freq`/`type` → **the uniform GET must emit the frontend shape**
(`freq`/`type`); `camilladsp.get_equalizer_settings()` is the local record; `EqualizerRouter._route`
already falls back to local CamillaDSP for an unknown mac (so `mac_id="local"` dispatches).

### Task 3.5.1 — Access layer recognizes the `local` sentinel
**Files:** Modify `backend/core/equalizer/multiroom_service.py`; Test `backend/tests/test_multiroom_equalizer_service.py`.
- [ ] Failing tests (empty registry, i.e. multiroom-off): `get_client_eq("local")` reads CamillaDSP;
      `set_client_eq("local", eq)` applies to the DAC + persists (no registry write);
      `update_filter("client","local",…)` persists local + routes via the router and does **not**
      materialize a phantom registry record; `set_client_equalizer_effects_enabled("local",…)` →
      `routing_service` bypass/restore; `apply_client_equalizer("local",…)` skips registry validation.
- [ ] Run → fail. Implement: module const `LOCAL_TARGET = "local"` + `_is_local(target)` =
      `target == LOCAL_TARGET or (self._registry and self._registry.is_local_client(target))`; route
      `get_client_eq` / `set_client_eq` / `apply_client_equalizer` / `_apply_partial_update`
      (guard + per-member check) / `set_client_equalizer_effects_enabled` through `_is_local`.
- [ ] Run → pass. `ruff check --select F401,F841` on touched files.
- [ ] Commit: `feat(eq): access layer recognizes the "local" target sentinel`.

### Task 3.5.2 — `GET /target/{target}` uniform read
**Files:** Modify `backend/api/equalizer.py`; Test `backend/tests/test_api_equalizer.py`.
- [ ] Failing tests: `GET /target/local` → local record (filters in `freq`/`type` shape +
      `active_preset` + `enabled` + `compressor`/`loudness`/`mono` + `custom_gains` + `state`);
      `GET /target/{mac}` → remote record; `GET /target/zone:{id}` → zone record (derived);
      unknown client/zone → 404.
- [ ] Run → fail. Implement: `_resolve_target(target)` → `("client","local")` | `("zone",id)` |
      `("client",mac)`; `record = await multiroom_equalizer_service.get_equalizer(tt, tid)` (404 on
      `None`/`ValueError`); assemble the **frontend wire dict** (filters `freq`/`type`) + live
      `state`/`sample_rate` (local/zone → `camilladsp_service.get_status()`; remote mac →
      `equalizer_router_service.get_status(mac)`).
- [ ] Run → pass.
- [ ] Commit: `feat(eq): GET /target/{target} uniform per-target EQ read`.

### Task 3.5.3 — `PUT/POST /target/{target}/…` uniform writes
**Files:** Modify `backend/api/equalizer.py`; Test `backend/tests/test_api_equalizer.py`.
- [ ] Failing tests: `PUT /target/{target}/{filter/{id}|compressor|loudness|mono}` route to the access
      layer `update_*` with the resolved `(target_type, target_id)`; `PUT /target/{target}/enabled` →
      `set_client_equalizer_effects_enabled` (client) / `set_zone_equalizer_effects_enabled` (zone);
      `POST /target/{target}/preset` (`EqualizerPresetRequest`) → `load_client_preset` /
      `load_zone_preset` (returns resolved gains); `POST /target/{target}/save-custom` →
      `save_custom_preset(tt, tid)`; **`PUT /target/local/mono` succeeds (the Mono-404 fix)**;
      unknown target → 404 everywhere.
- [ ] Run → fail. Implement the 6 routes via `_resolve_target` + the access layer; map `ValueError`
      → 404 like the existing siblings. Reuse `EqualizerFilterUpdateRequest` / `EqualizerCompressorRequest`
      / `EqualizerLoudnessRequest` / `EqualizerPresetRequest` (no dual-key fallbacks — Pitfall #11).
- [ ] Run → pass. `ruff check backend/` + `--select F401,F841` on touched files.
- [ ] Commit: `feat(eq): uniform PUT/POST /target/{target} EQ writes`.

**Phase 3.5 acceptance:** new `/target/{target}` routes covered; old routes untouched + still green;
full `pytest` green; `ruff` clean; `code-review` clean. STOP for review.

---

## Phase 4 — Frontend onto the uniform API + remove old routes

**Outcome:** The store and modal treat every target (local · remote · zone) as **one record** behind a
single `/api/equalizer/target/{target}` resolver; the legacy split routes are deleted. (No vitest —
rely on lint + `npm run build` + manual Pi validation.)

### Task 4.1 — Store: one target resolver + one record
**Files:** Modify `frontend/src/stores/equalizerStore.js`.
- [ ] Replace `getApiBase` + every zone / `!isLocalClient` branch with `targetRef()` →
      `'local' | '<mac>' | 'zone:<id>'` and `targetBase = '/api/equalizer/target/' + targetRef()`.
- [ ] `loadStatus`: one `GET ${targetBase}` → parse the whole record (state, filters, compressor,
      loudness, mono, `active_preset`, `enabled`, `custom_gains`). Keep `GET /presets` **only** for the
      builtin catalog (labels + preset gains). Remove the dual `active_preset` reconciliation
      (the `fetchPresets` conditional + the `loadStatus` zone/remote overrides) — the name now comes
      solely from the record. All writes (`sendFilterUpdate`, `updateCompressor`, `updateLoudness`,
      `updateMono`, `loadPreset`, `saveCustomPreset`, enabled toggle) → `${targetBase}/…` uniformly.
      This repoints local Mono to `PUT /target/local/mono` (fixes the 404).
- [ ] `npm run lint:js` → clean.
- [ ] Commit: `refactor(eq): frontend store reads/writes one per-target record`.

### Task 4.2 — Modal/meters read one record; align WS schema
**Files:** Modify `frontend/src/components/equalizer/EqualizerModal.vue`,
`frontend/src/components/equalizer/LevelMeters.vue` (if needed), `frontend/src/schemas/ws.js`.
- [ ] Name + gains come from the one record; no cross-source reconciliation. Check the
      `multiroom.equalizer_changed` payload: the broadcast sends `EqFilter.to_dict()`
      (`frequency`/`filter_type`) while the store reads `freq`/`type` — align the producer to the
      `freq`/`type` wire shape (one canonical key, Pitfall #18) and add/adjust the Zod schema.
- [ ] `npm run lint:js && npm run lint:css` → clean.
- [ ] Commit: `refactor(eq): modal renders one per-target EQ record`.

### Task 4.3 — Delete the legacy split routes
**Files:** Modify `backend/api/equalizer.py`, `backend/tests/…`; verify no other consumer first.
- [ ] Grep-confirm nothing (frontend, hardware, IR/rotary, other services) still calls the bare
      `/status` · `/filters` · `/enabled`(GET/PUT) · `/filter/{id}` · `/compressor`(PUT) ·
      `/loudness`(PUT) · `/preset/{id}` · `/save-custom`, the `/client/{mac}/*` family
      (status/filters/filter/compressor/loudness/mono/enabled/preset/save-custom/**restore**), and the
      `/zone/{id}/*` family. Keep `/presets`, `/levels*`, `/mute`, `/links/*` crossover,
      `/client/{id}/crossover-frequency`. Decide `/client/{mac}/restore`'s fate: under record-as-truth,
      selecting a remote target just `GET`s its record (the satellite is kept in sync by writes +
      reconnect, Phase 2) — drop the restore-on-select call + route if confirmed unused.
- [ ] Remove the dead routes + their now-orphaned helpers (`_get_online_client_ip`, `_get_local_client_mac`,
      `restore_client_settings`, …) and retarget/trim the tests that asserted them.
- [ ] `ruff check backend/` + `--select F401,F841`; full `pytest` green.
- [ ] Commit: `refactor(eq): remove legacy split EQ routes (single per-target path)`.

**Phase 4 acceptance:** `npm run lint:js && lint:css` clean; `npm run build` succeeds; backend
`pytest` green after route removal; `code-review` clean; manual Pi validation of A/B/C **plus** the
local Mono toggle and multiroom-off. STOP for review.

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
