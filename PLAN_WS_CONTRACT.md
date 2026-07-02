# WS Contract Cleanup & Typed Event Layer — Phased Plan

Status legend: `[ ]` pending · `[x]` done (mark + commit at the end of each phase).
Delete this file when all phases are complete (repo convention for plan docs).

## Execution protocol

Triggered by: *"passer à la prochaine phase du plan"* (or similar).

1. Read this file top to bottom. Find the **first unchecked phase**.
2. Execute only that phase. Do not start the next one.
3. Verify: `python -m pytest` (from `backend/`), `npm run lint` (from `frontend/`),
   plus the phase-specific checks listed in the phase.
4. Update the phase checkbox (and any "resolved decision" notes), commit directly
   on `main` (solo-dev convention), one commit per phase:
   `refactor(ws): phase N — <title>`.
5. If a phase reveals a consumer this plan missed, STOP, do not force the change,
   record the finding under the phase and report.

**Standing constraints (every phase):**
- Milo-Mac couples to: the 8 `(category, type)` pairs in
  `backend/tests/contracts/milo_mac_contract.json`, the `full_state` envelope
  (subkeys `active_source, source_state, transitioning, multiroom_enabled,
  equalizer_effects_enabled, metadata`), the `multiroom_changed` boolean
  discriminator, `state.global_volume_db`/`state.mode` in `volume_changed`,
  `limits.{min_db,max_db}`, `config.enabled_apps`. Never touch these without
  re-vendoring the Swift snapshot + manifest together.
- Wire envelope `{category, type, origin, data, timestamp}` is unchanged until
  the end: frontend and Milo-Mac must not need any change to keep working
  (frontend-side edits in this plan are additive or dead-code removal only).
- No compatibility shims, no dual emission paths left behind at the end of a phase.

## Audit baseline (2026-07-02) — findings this plan resolves

Inventory: 67 `broadcast_event()` sites, ~56 `(category, type)` pairs. Dispatch
matches on `category.type` (`frontend/src/services/websocket.js:321`); `origin =
data.get("source", category)` (`backend/core/state.py:406`); `full_state`
injected for categories `source`/`system` only; Zod `parsedOn` failure = warn +
raw pass-through.

**D1 — Emitted, never read (by frontend nor Milo-Mac):**

| Event | Dead fields | Emitter |
|---|---|---|
| system/transition_start | from_source, to_source | core/state.py:155 |
| system/transition_complete | active_source, source_state | core/state.py:185 |
| system/state_changed | old_state, new_state, multiroom_enabled (data-level), equalizer_effects_changed, snapcast_update, reason | multiroom/routing.py:538,753 · multiroom/routes.py:30 · core/state.py:331 |
| system/cd_drive_status | drive_connected, disc_present (handler reads full_state only) | sources/cd/source.py:343,394,542 |
| source/state_changed | old_state | core/state.py:269 |
| source/favorite_added\|removed (radio) | favorites_count | sources/radio/data.py:386,407 |
| settings/* (~15 types) | reload_success | api/settings.py:83 |
| settings/mac_roc_changed | service_restarted | api/settings.py:785 |
| routing/multiroom_enabling\|disabling\|error | reason | multiroom/routing.py:689,700 |
| equalizer/enabled_changed | effects_bypassed | multiroom/routing.py:735 |
| system/backend_error | level, logger | core/log_handler.py:50 |
| multiroom/crossover_changed (client variants) | entire payloads {client_id, crossover_frequency} and {client_id, settings_applied} | multiroom/crossover.py:215,610 |
| programs/*_progress | progress, message (silently stripped by Zod) | api/programs.py:56 |
| programs/*_complete | message, error, new_version, old_version (stripped by Zod) | api/programs.py:74-90 |

**D2 — Read, never emitted:** Milo-Mac reads `data.multiroom_enabled` and legacy
fallback `data.volume_db` on `volume/volume_changed`; emitter
(`core/volume/service.py:887-893`) sends only `{show_bar, step_mobile_db, state}`.

**D3 — Same pair, divergent shapes:** crossover_changed (3 shapes, only zone
shape consumed — each client emission triggers a Zod warn in every connected
browser); system/state_changed (4 ad-hoc discriminator variants; `new_state` is
bool here vs string in source/state_changed); volume_startup_changed (route vs
FR11 auto-track emitter); source/state_changed (error variant omits old_state);
favorite_added/removed (radio vs podcast union, undocumented radio side);
equalizer/filter_changed omits `enabled` vs canonical filter wire shape
(multiroom/models.py:79 "Pitfall #18"); pending_client_changed ({action, client}
vs {action, mac_id}).

**D4 — Doc drift:** CLAUDE.md category list omits `network`
(core/network/service.py:936); routing.py:700 docstring claims a
"machine-readable reason code" nobody reads; bt_remote.py:252 docstring omits
the emitted `source` key; manifest `payload_invariants.volume_changed` lists
`multiroom_enabled`, never emitted (D2).

**D5 — origin pitfalls:** single origin consumer in the whole system
(`frontend/src/stores/podcastStore.js:228`); `data["source"]` semantically
overloaded (audio source id / category echo / internal service name / job kind);
`source/error_cleared` handler (`App.vue:496`) clears the error banner without
checking `data.source`.

---

## [x] Phase 1 — Real-impact fixes (wire noise + contract alignment)

*Done 2026-07-02. Notes: the shared `_broadcast_event` helper stays (it serves
the canonical zone emission); `multiroom_enabled` derives from
`volume_state.mode == "multiroom"` (same VolumeState as `state.mode`);
`currentError` in App.vue now carries `source` so `error_cleared` can match.*

Smallest changes with observable value; no field removal yet.

1. **Kill the systematic Zod warns**: remove the two client-shaped
   `multiroom/crossover_changed` emissions (`crossover.py:215` and `:610`,
   plus the shared helper path at `:504` if it only serves them). Keep the
   zone-shaped emission (`:302`) as the single canonical shape. Update the
   long docstring that documented the divergence. Verify: no consumer of the
   client variants exists (audit confirmed: frontend handler early-returns on
   `!payload.zone_id`; not in manifest).
2. **Align `volume/volume_changed` with its documented contract (D2)**: add
   `"multiroom_enabled": <bool>` to the payload in
   `core/volume/service.py:887-893` (derive from the same source of truth as
   `state.mode`; if strictly equivalent to `mode == "multiroom"`, still emit it —
   the manifest documents it and Milo-Mac reads it).
3. **Scope `source/error_cleared`** (D5): in `App.vue`, clear the banner only if
   the displayed error came from `event.data.source`.
4. **CLAUDE.md**: add `network` to the allowed broadcast categories list.

Phase checks: `pytest` (contract tests green), `npm run lint`, quick dev-server
smoke: toggle a zone crossover → no Zod warn in console; per-client EQ change →
no `crossover_changed` warn either.

## [x] Phase 2 — Dead-field pruning (wire diet)

*Done 2026-07-02.*

**FINDING — consumer the audit missed:** `routing/multiroom_error`'s `reason`
IS read by the frontend: `multiroomStore.js:343` maps it via
`MULTIROOM_ERROR_KEYS` to a localized `transitionError` (since commit
`ab902e97`, 2026-06-05 — predates the audit). The D1 entry
"routing/multiroom_enabling|disabling|error → reason" and the D4 claim that
the `routing.py:700` docstring is wrong are both incorrect for the `_error`
event. **Kept** `reason` on `multiroom_error` (docstring already accurate);
dropped it only from `multiroom_enabling|disabling` (`:689`), whose handlers
switch on event type alone. Phases 3–6: treat
`multiroom_error.{reason}` as a live contract field.

*Other notes: `api/programs.py` — removed error details from the broadcast
required adding a `logger.error` in `_create_background_update` (failure was
otherwise unrecorded); dead `default_success_msg` param removed.
`state.py:269` — the `old_state` local went away with the field.*

Remove every D1 field from its emitter. All were verified consumer-free on
2026-07-02 (frontend greps + Zod strip analysis + Milo-Mac vendored Swift).
**Keep**: `multiroom_changed` (Milo-Mac), `full_state` injection, everything in
the standing constraints.

- `core/state.py:155` → data becomes `{"source": "system"}`.
- `core/state.py:185` → data becomes `{"source": "system"}`.
- `core/state.py:331` → drop `reason`.
- `multiroom/routing.py:538` → keep only `{"multiroom_changed": True, "source": "routing"}`.
- `multiroom/routing.py:753` → keep only `{"source": "equalizer"}`.
- `multiroom/routes.py:30` → keep only `{"source": "snapcast"}`.
- `sources/cd/source.py:343,394,542` → data becomes `{"source": "cd"}` (event
  stays as a full_state carrier). **Verify first** that no component/store reads
  `drive_connected`/`disc_present` through a path the audit missed (grep both
  names in `frontend/src/`), and that CD availability UI is driven by
  REST/full_state, not by these fields.
- `core/state.py:269` → drop `old_state` (also converges the two
  source/state_changed shapes, closing part of D3).
- `sources/radio/data.py:386,407` → drop `favorites_count`.
- `api/settings.py:83` → drop `reload_success` from the broadcast payload (keep
  the internal variable for the HTTP response if it's part of it).
- `api/settings.py:785` → drop `service_restarted` from the broadcast (keep in
  HTTP response).
- `multiroom/routing.py:689` → drop `reason` (consumers switch on `event.type`).
  ~~`:700`~~ NOT done — see FINDING above: `multiroom_error.reason` has a real
  consumer; field and docstring kept as-is.
- `multiroom/routing.py:735` → drop `effects_bypassed`.
- `core/log_handler.py:50` → drop `level`, `logger` (only `message` is read).
- `api/programs.py:56,74,79,87` → stop emitting `progress`, `message`, `error`,
  `new_version`, `old_version` (Zod already strips them; the UI shows
  `status`/`success` only and refetches versions over REST). If richer update
  progress UI is ever wanted, re-add BOTH sides deliberately (emitter + Zod
  schema + store) — do not resurrect one side only.
- `hardware/bt_remote.py:252` docstring: include the `source` key (D4), or drop
  the key if pruning makes it redundant — pick one, keep docstring exact.

Phase checks: `pytest` (Milo-Mac contract suite MUST stay green), `npm run
lint`, dev smoke: source switch, multiroom toggle, a settings change, a radio
favorite add/remove — UI reacts as before, no console warnings.

## [ ] Phase 3 — Shape unification (last dict-era phase)

Goal: exactly one shape per `(category, type)` pair (documented unions allowed
only where discriminated by `data.source`).

1. **`settings/volume_startup_changed`**: make both emitters
   (`api/settings.py:201` and `core/volume/service.py:435` FR11 auto-track) emit
   the identical `{"source": "settings", "config": {"startup_volume_db": …,
   "restore_last_volume": …}}`.
2. **`equalizer/filter_changed`** (`core/equalizer/service.py:563`): emit the
   canonical filter wire shape (add `enabled`, matching
   `EqFilter.to_wire_dict()` — multiroom/models.py:79). Reuse `to_wire_dict()`
   instead of hand-building the dict if the call site has an `EqFilter`.
3. **`source/favorite_added|removed|modified`**: keep the radio/podcast union
   (discriminated by `data.source`), but add the same payload docstrings on the
   radio side (`sources/radio/data.py:386,407,609`) that podcast already has.
4. **`multiroom/pending_client_changed`**: document the
   `{action, client} | {action: "removed", mac_id}` union at the emitter
   (`multiroom/pending_clients.py:242`); no shape change (frontend handles both).
5. **`system/state_changed`**: after Phase 2 the four variants have converged to
   `{"source": <str>}` + optional `multiroom_changed: true`. Document exactly
   that at each emitter. Do NOT split into new event types — Milo-Mac couples to
   this pair + discriminator.

Phase checks: `pytest`, `npm run lint`, grep sanity: for every pair emitted from
more than one site, the data keys are now identical across sites.

## [ ] Phase 4 — Typed Pydantic event layer: foundation + core migration

One class per event; `category`/`type` are class-level, never passed at call
sites; wire format byte-identical to today.

1. **Foundation** — new module `backend/core/models/ws_events.py`:
   - `class WsEvent(BaseModel)`: `CATEGORY: ClassVar[str]`, `TYPE: ClassVar[str]`,
     payload = the model's own fields. `origin` property: `getattr(self,
     "source", None) or CATEGORY` (formalizes today's `data.get("source",
     category)`). Optional `INCLUDE_FULL_STATE: ClassVar[bool] = True` override
     (needed by `SourcePositionUpdate`).
   - `AudioStateMachine.broadcast(event: WsEvent)`: serializes
     (`model_dump()`), injects `full_state` under the existing rules
     (`CATEGORY in {"source","system"}` and `INCLUDE_FULL_STATE`), builds the
     same envelope, calls `ws_manager.broadcast_dict`. The legacy
     `broadcast_event(category, type, data)` stays working during Phases 4–5
     (thin, unchanged) — removed in Phase 5.
   - Shared sub-models where payloads repeat: settings config envelope
     (`source: Literal["settings"]`, `config: <per-event model>`), programs
     progress/complete (2 models covering 8 event classes), registry
     client/zone payloads, `EqFilterWire`.
   - Events with passthrough/variable types get one subclass per concrete type
     (settings: ~15 tiny classes; programs: 8; radio/podcast favorites: 5).
     The helper functions (`radio/data.py::_broadcast_event`,
     `podcast/data.py::_broadcast_event`, `api/settings.py::_handle_setting_update`,
     `api/programs.py::_create_background_update`,
     `multiroom/websocket.py::_broadcast_registry_event`) are refactored to take
     an event class / typed payload instead of `(event_type, dict)`.
2. **Migrate the core families** to `broadcast(event)`:
   `core/state.py` (6 sites), `core/audio_source.py` (3), `core/volume/service.py`
   (2), `api/settings.py` (4 + the 15 typed subclasses), `api/programs.py` (4).
3. Payload docstrings at migrated call sites are deleted — the model IS the
   documentation now (each model gets a one-line docstring naming its consumers:
   frontend store/handler, Milo-Mac if applicable).

Phase checks: `pytest`; add a unit test asserting envelope equivalence: for a
representative event of each migrated family, `broadcast(event)` produces the
exact dict the Phase-3 code produced (snapshot the expected dicts in the test).
Dev smoke: source switch, volume change, one settings change.

## [ ] Phase 5 — Typed layer: remaining families + single code path

1. Migrate: `core/equalizer/*` (11 sites), `core/multiroom/*` (15),
   `sources/{radio,podcast,cd}` (12), `hardware/*` (5),
   `core/{network,connectivity,system,log_handler}` (4).
   - Registry forward (`multiroom/websocket.py:193`): map
     `RegistryEventType` → event classes explicitly (dict of constructors), no
     stringly `mapped_type` left.
   - `network/status_changed` keeps building from the existing `NetworkStatus`
     model (embed it, don't duplicate fields).
2. **Remove the dict path**: `broadcast_event(category, type, data)` is deleted;
   `broadcast(event: WsEvent)` is the only emission API. Grep-verify zero
   remaining `broadcast_event(` callers (tests included).
3. `ws/manager.py` `initial_state`: give it a typed model too (it's the one
   envelope built outside the state machine), reusing the full_state model.
4. Update CLAUDE.md: the "Broadcast every state change" rule now points to
   `WsEvent` subclasses + `broadcast()`; the per-site docstring rule is replaced
   by "shape lives in `core/models/ws_events.py`".

Phase checks: `pytest`, `ruff check backend/`, `npm run lint`, full dev smoke
(source switch, multiroom toggle, EQ edit, zone crossover, favorites, an update
check, network change if testable).

## [ ] Phase 6 — Contract hardening + docs

1. **Make the Milo-Mac payload invariants executable**: extend
   `test_milo_mac_contract.py` to validate `payload_invariants` from the
   manifest against the Pydantic event classes (full_state subkeys exist on the
   full-state model; `volume_changed` model carries `state.global_volume_db`,
   `state.mode`, `multiroom_enabled`; `limits.{min_db,max_db}`;
   `config.enabled_apps`; `multiroom_changed` discriminator exists). The
   manifest note "not statically verified" becomes obsolete — update it.
2. **Zod cross-check (one-shot, manual)**: for each pair in
   `frontend/src/schemas/ws.js`, diff schema fields against the Pydantic model;
   fix any residual mismatch on the side that's wrong. Do NOT add new Zod
   schemas (the admission rule in CLAUDE.md stands) and do NOT introduce codegen.
3. Update `docs/architecture.md` (state-flow section: typed events) and prune
   any stale payload docs.
4. Delete this plan file.

Phase checks: `pytest`, `npm run lint`, re-read CLAUDE.md/docs for coherence.

---

## Effort estimate (for planning, not a gate)

Phase 1: ~half a day · Phase 2: ~half a day · Phase 3: ~half a day ·
Phase 4: ~1 day · Phase 5: ~1 day · Phase 6: ~half a day. Total ≈ 3–4 days,
each phase independently shippable.
