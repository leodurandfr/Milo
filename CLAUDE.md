# CLAUDE.md

Guidance for Claude Code when working in this repository. The full reference lives in [docs/architecture.md](docs/architecture.md) and [docs/development.md](docs/development.md) — this file is the **rule index**, not a manual; don't duplicate the docs here.

## Project overview

Milō is a multiroom audio system for Raspberry Pi (Spotify Connect, AirPlay 2, Bluetooth, Mac streaming via ROC, Internet Radio, Podcasts, CD, DLNA/UPnP, Qobuz Connect, a local Music Library). FastAPI (Python) backend + Vue 3 frontend, ALSA for audio — no Pipewire/PulseAudio.

## External API clients — Milo-Mac (read before deleting any route or WS event)

The REST + WebSocket API has a **second consumer beyond `frontend/`: Milo-Mac**, a separate macOS app (`github.com/leodurandfr/Milo-Mac`, not in this checkout) that remotely drives a unit and streams audio via ROC.

**A route or WS event with no caller in `frontend/src/` is NOT necessarily dead** — Milo-Mac may consume it. The surface Milo-Mac depends on is pinned in a manifest, [backend/tests/contracts/milo_mac_contract.json](backend/tests/contracts/milo_mac_contract.json) — **the single source of truth**, no manual grep anymore. A **vendored snapshot** of Milo-Mac's two relevant source files lives in [backend/tests/contracts/vendor/milo-mac/](backend/tests/contracts/vendor/milo-mac/) (`MiloAPIService.swift` = REST, `WebSocketService.swift` = WS). `test_milo_mac_contract.py` (every `pytest`, offline) enforces **three** things: (1) the backend still serves every route/WS event the manifest lists, (2) the manifest **matches the vendored snapshot's surface exactly** — so it can't silently drift from the app it protects, and a broken Swift extractor fails loudly instead of passing on an empty surface — and (3) the manifest's `payload_invariants` (the exact fields Milo-Mac reads) hold on the typed `WsEvent` models. A non-blocking weekly CI job (`check_milo_mac_freshness.py`) re-clones the real Milo-Mac and warns when the **snapshot** (and thus the manifest) has fallen behind upstream.

**Workflow:** the manifest + snapshot are refreshed *consciously, together in one commit* when Milo-Mac evolves, not auto-grepped. If a contract test fails: backend-dropped-a-route → restore it or, if Milo-Mac genuinely dropped it, prune that manifest entry; manifest≠snapshot drift → re-download the two `.swift` files into `vendor/milo-mac/` and update the manifest to match (the freshness job tells you when upstream moved). Don't add compatibility shims; the appliance keeps a single code path.

Milo-Mac couples to the **whole wire contract**: REST paths/methods + request/response keys, and the WS events in the manifest (`full_state` envelope, the `multiroom_changed` discriminator, `volume_changed`, `settings/{volume_limits,dock_apps}_changed`). **Exception:** it reads WS `metadata` as an opaque dict — no metadata *sub-field* (`uri`, `client_count`, `album_art_height`, `track_position/duration`) is coupled, so an over-emitted metadata sub-field with no `frontend/src/` consumer is safe to drop. **Safe without touching the manifest:** purely frontend code (Vue components, Pinia stores, frontend Zod schemas).

## Commands

```bash
# Backend (from backend/)
python main.py                       # dev server on :8000
python -m pytest [-v] [-k name]      # tests

# Frontend (from frontend/)
npm run dev                          # dev server on :5173 (proxies API to backend)
npm run build                        # → frontend/dist/ (runs lint:css first)
npm run lint                         # eslint + stylelint

# Service / logs
sudo journalctl -u milo-backend -f
sudo systemctl restart milo-backend
```

Full setup (venv, deps): [docs/development.md](docs/development.md).

## Architecture

State flow: backend change → `state_machine.broadcast(WsEvent)` → WS → Pinia store → reactive UI. Deep dive: [docs/architecture.md](docs/architecture.md).

**Backend** (`backend/`, source-based, async-first):
- `core/` — infrastructure: `state.py` (`AudioStateMachine`, single source of truth), `audio_source.py` (`BaseAudioSource` ABC), `settings.py`, `systemd.py`; subpackages `models/ volume/ equalizer/ multiroom/ connectivity/ network/ system/ updates/ lyrics/`.
- `sources/` — one subpackage per source (`spotify/ airplay/ mac/ bluetooth/ radio/ podcast/ cd/ dlna/ qobuz/ music_library/`).
- `api/` — REST routes + shared Pydantic `models.py` + `route_helpers.py`.
- `hardware/` (encoder, IR, BT remote, screen), `ws/` (server + manager), `shared/` (`MpvController`, `BackgroundTaskSet`, decorators, persistence), `config/constants.py`, `dependencies.py` (service registry, lazy singletons).

Principles: single source of truth (`AudioStateMachine`); self-contained source modules; dict-based DI; D-Bus via `dbus-next`, event-driven over polling, **always fail open** when D-Bus is unavailable (so dev runs without the underlying service).

**Frontend** (`frontend/src/`, Vue 3 Composition API + Pinia):
- `components/` — one dir per feature/source + `ui/`. Shared full-screen player `audio/AudioPlayerFull.vue`, reused with controls by Spotify/CD (`showControls=true`, default) and without by AirPlay/DLNA/Qobuz (`showControls=false`) — 5 real consumers; Music Library has its own player UI, not this component.
- `stores/` — `unifiedAudioStore.js` is the central audio mirror; others are per-feature.
- `composables/`, `services/` (WS client, `apiCall`, i18n, logger), `locales/` (8 langs, `english.json` canonical/fallback — all keys must exist there first), `views/` (`MainView.vue` SPA).

**Service init order is critical** — `dependencies.py::initialize_services()` resolves circular deps via setters *before* async init. Don't reorder without reading the comments there. New service: create under `core/{name}/`, add a creator in `dependencies.py::_create_service()`, register its async `initialize()` in `initialize_services()` if it has one. Detail: [docs/development.md](docs/development.md).

## Audio sources

Pick a source's **family** from two questions: *(1) is playback controlled from Milō's UI or by an external sender?* *(2) does it expose rich metadata (artwork/title/artist)?* Then follow only that family's layout. There is **no `AudioSourceProtocol`** — any docstring mention is drift to fix.

| Family | Sources | Backend `sources/{s}/` | Frontend `components/{s}/` |
|---|---|---|---|
| **A. Mute receiver** — external control, no rich metadata | Bluetooth, Mac | `source.py` (+ helpers `agent.py`/`monitor.py`). **No `routes.py`** — commands via generic `/api/audio/control/{source}`. `__all__=["{Name}Source"]`. | None — rendered by `AudioSourceStatus` (icon + device name). |
| **B. Passive player** — external control, rich metadata | AirPlay, DLNA, Qobuz | `source.py` + `routes.py` only for what the sender can't deliver (AirPlay/DLNA: binary artwork; Qobuz: none — CDN artwork URL needs no proxy, so no `routes.py` at all) + `metadata_reader.py`/`monitor.py` as needed for the metadata feed. `__all__` adds `router, setup_{s}_routes` only if a `routes.py` exists. | Vue component wrapping `<AudioPlayerFull :showControls="false" />`. |
| **C. Active player** — UI control, rich metadata | Spotify, Radio, Podcast, CD, Music Library | `source.py` + networking as needed: `websocket.py` (Spotify), full `routes.py`+`data.py`+external API (Radio/Podcast/CD), `models.py`. Music Library is the richest — a catalog-engine split (`navidrome_client.py`, `discovery.py`, `browse.py`, `disc_merge.py`, `storage.py` for USB/SMB/NFS mounting) on top of the usual `routes.py`+`data.py`+`models.py`. | Vue component wrapping `<AudioPlayerFull>` with controls + custom UI (tracklist/queue/favorites), except Music Library which has its own player UI (not `AudioPlayerFull`). |

All sources extend `BaseAudioSource(ABC)` — public `start/stop/status/command`, override `_do_start/_do_stop/_get_status/_handle_command`; constructor `(config, state_machine, settings_service, systemd_manager)`. Adding one: define the enum in `core/models/audio_state.py::AudioSource`, create the module, register a creator + the source in `dependencies.py`, add 2 ALSA device variants, update stores. Full checklist + reference (`radio/`): [docs/development.md](docs/development.md).

**Rules (all families):**
- **No `GET /<source>/status`** — status is broadcast over WS only.
- **No `POST /<source>/restart`** — restart is a systemd/admin concern.
- **Loggers:** routes use `logging.getLogger(__name__)`; source sub-modules use `logging.getLogger(f"source.{source_id}.<sub>")` to hang under the hierarchy `BaseAudioSource.__init__` creates. The legacy `feature.*` namespace is retired.
- **Routes (B, C):** use `run_source_command()` for playback and `logger.error(...)` before every `raise HTTPException`.

Read the existing sources as references — they evolve. `music_library/` (richest C: catalog-engine split), `radio/`+`podcast/` (C), `airplay/`+`dlna/` (B: external process + binary artwork), `qobuz/` (B without `routes.py`), `spotify/` (C without `routes.py`), `mac/`+`bluetooth/` (A).

## Core code rules (backend)

- **Never mutate state directly** — `await state_machine.update_source_state(source, SourceState.ACTIVE, metadata)`, never `state_machine._state.active_source = …`. Transitions are guarded by `_transition_lock`; updates arriving while `transitioning` is set are **dropped, not buffered** — the post-start resync re-reads `source.state`/`source.metadata` to recover the final state. No replay queue exists; don't build one.
- **Broadcast every state change** via `state_machine.broadcast(event)`, where `event` is a `WsEvent` subclass from [backend/core/models/ws_events.py](backend/core/models/ws_events.py) — never `ws_manager.broadcast_dict()`; no dict-based emission path exists (new event → new subclass). One class per `(category, type)` pair, `CATEGORY`/`TYPE` class-level, the model's fields ARE the wire `data` payload. Wire format `{category, type, origin, data, timestamp}`; `origin` is the event's `source` field (falls back to `CATEGORY`), so category `"source"` events declare a `source` field. Categories: `source` (all audio sources — never source-specific ones), `system`, `routing`, `equalizer`, `multiroom`, `volume`, `settings`, `programs`, `network`.
- **WS payload contracts** — a `(category, type)` earns a Zod schema in `frontend/src/schemas/ws.js` **iff** (a) more than one app consumes it (e.g. Milo-Mac + frontend) **or** (b) it has already caused a shape bug; consumers read it via `parsedOn(category, type, schema, handler)` and MUST NOT read `event.data.x` raw. This is a deliberate admission rule, **not** an unfinished migration — pairs meeting neither test stay on raw `on(...)`; don't bulk-schematize them. (`full_state` and `volume_changed` are already validated by their own store schemas — `SystemStateSchema`/`VolumeStateSchema` in `unifiedAudioStore.js` — so they need no registry entry.) The backend-side shape lives in the event model in `core/models/ws_events.py` (each class docstring names its consumers) — no per-call-site payload docstrings, no codegen.
- **All settings via `SettingsService`** — `await settings_service.set_setting('volume.alsa_max', 80)`, never edit the dict/file directly. Stored at `/var/lib/milo/settings.json` (atomic `os.replace`, file locks, corruption backups).
- **Background tasks** — never raw `asyncio.create_task` for fire-and-forget. Services: `self._bg = BackgroundTaskSet(logger, "label")`, then `self._bg.spawn(coro, label=…)`, and `await self._bg.cancel_all()` in `cleanup()`. Routes: add a `background_tasks: BackgroundTasks` param + `background_tasks.add_task(...)`. Raw `create_task` is allowed **only** for tracked long-running tasks stored on `self` (`self._monitor_task = asyncio.create_task(...)`).
- **Encapsulation** — never touch another service's `_private` attrs/methods from a route or another service; expose a public method/property instead.

## API conventions

- **Response format:** success → `"status": "success"`. `/status`-style endpoints return errors as HTTP 200 with `"status": "error"` (resilience pattern). Real errors → `raise HTTPException`.
- **Verbs:** `PUT` idempotent updates (settings, routing), `PATCH` partial updates (volume, zone, client props), `POST` actions + creation, `DELETE` removals (path param).
- **Pydantic:** all fields `snake_case`. Shared models in `api/models.py`, source-specific in `sources/{s}/models.py`.
- **Helpers** (`backend/api/route_helpers.py`): `run_source_command(source, cmd, data, context)`; `api_error_handler(context, log)` (async ctx mgr); `parse_audio_source(name)` (→ `AudioSource` or HTTP 400, for untrusted input); `coerce_audio_source_or_none(name)` (defensive, for trusted state values — returns `None` + logs on invalid).
- **Error-handling doctrine per layer:**

| Layer | Policy | Anti-pattern |
|---|---|---|
| HTTP route | `api_error_handler` or `run_source_command`; no bare try/except in body; enum via `parse_audio_source`. | `except Exception: raise HTTPException(500, str(e))` per route |
| Service | **Log + raise.** Legit fallback → `@handle_errors(default=…, level='error')`. | `except: return False` without log |
| Background loop | Wrap the **loop body** in `try/except Exception` + error log + `continue`. | `except CancelledError` alone (transient I/O kills the task silently) |
| Best-effort hw/external | Scoped `except SpecificError` + `warning` log + named fallback. | `except Exception: logger.debug(...)` masking failures |

Examples: `api/equalizer.py`, `sources/radio/shazam.py`, `sources/podcast/source.py::_progress_save_loop`. Intentional silent swallow → `contextlib.suppress(Type)`, not bare `except: pass` (trips ruff S110/S112).

## Frontend conventions

- **HTTP:** every request goes through `apiCall.{get,post,put,patch,delete}(url, {category, message, ...})`, or `apiCall(category, message, fn, options)` for atomic multi-request sequences. **No `import axios` outside `services/apiCall.js`.** Helpers return `{ok, data, error}`; support `errorRef`, `signal` (AbortController), `checkStatus` (resilience), `logLevel`.
- **Logging:** `logger.{debug,info,warn,error}(category, message, data)` from `@/services/logger`. No `console.*` for errors/warnings outside the allowlist (`services/logger.js`, `main.js`, `schemas/api.js`, `services/modalDebug.js`).
- **WS events:** handle in Pinia stores, not in components — components react to store state. Store state maintained by WS *deltas* (not full snapshots) MUST also be refetched in `App.vue::resyncStores()` — deltas missed while a tab was backgrounded/disconnected are never replayed.
- **i18n:** `const { t } = useI18n()` in `<script setup>`, not the global `$t()`.
- **CSS:** use design tokens (`var(--color-*|--space-*|--radius-*)`); stylelint blocks hex literals + `rgba()/hsla()` in scoped `.vue` styles. Missing token → extend `assets/styles/design-system.css`, don't add a local exception. **No inline `// stylelint-disable`** — extend the design system, or whitelist the file in `.stylelintrc.cjs` with a one-line reason.
- **Typography:** apply via utility classes (`heading-1…4`, `text-body`, `text-mono`, `text-mono-small`, `display-1`); don't redeclare `font-*`/`line-height`/`letter-spacing` in scoped CSS (stylelint rejects it). Scoped CSS = layout, color, component spacing only.
- **Timers:** every `setTimeout`/`setInterval` in a component/composable/view/directive goes through `useTimer()` (auto-cleanup on unmount); bare globals are blocked by `no-restricted-globals`. Raw `window.set*` only in the timer-primitive layer (`useTimer/useDebounce/useVolumeThrottle`) and `directives/press.js`.
- **Constants:** structural constants used by 2+ modules live in `frontend/src/constants/`; promote on the 2nd consumer. Backend-derived values (speeds, codecs, presets) are fetched at runtime + cached, not hardcoded on both sides.
- **Code style:** `.js` files use semicolons; constants files use camelCase names.

## Persistence & schema-version protocol

Persistent data lives in `/var/lib/milo/` (settings, hardware, radio/podcast/cd data + image caches, equalizer, routing/mac/snapclient env, `camilladsp/`, `go-librespot/`, `errors.log`, `backups/`). Full inventory: [docs/architecture.md](docs/architecture.md).

**Unified per-client EQ:** one EQ record per client behind `MultiroomEqualizerService.get/set_client_eq(mac)` — the local client's lives in `equalizer.json`, remote clients' in `settings.json: multiroom.client_equalizer[mac]`; a zone holds no EQ (it derives from its members). One HTTP surface: `GET/PUT/POST /api/equalizer/target/{target}`, `target ∈ local · <mac> · zone:<id>`.

**Schema bumps — fail-loud + reset, never migrate.** Each versioned JSON carries `"schema_version": N`; the owning service declares `SCHEMA_VERSION` and uses `load_versioned_json`/`save_versioned_json` ([backend/shared/persistence.py](backend/shared/persistence.py)). On mismatch they raise `SchemaVersionMismatch`; `initialize_services` logs a banner (exact `rm` + pointer) and `SystemExit(1)`, so systemd loops the banner until the operator deletes the file.

## Constraints (invariants)

1. **Privileged exec is centralized, never ad hoc** — systemd + power actions go through `SystemdServiceManager` (which shells `sudo systemctl …`, incl. `power()` for reboot/poweroff and `restart_self()` for the updater's own-unit restart); privileged file deploys go through the pinned sudoers helpers under `/usr/local/bin/milo-*`: `milo-deploy-update`, `milo-apply-hardware`, `milo-set-wifi-country`, `milo-mount`/`milo-umount` (Music Library USB+SMB/NFS), `milo-apply-ir-keymap` (IR remote pairing). No bare `sudo` anywhere else. Permissions come from two `NOPASSWD` policy files for the `milo` user — `/etc/sudoers.d/milo-backend` (the first five) and `/etc/sudoers.d/milo-ir-remote` — installed by `install/system.sh`+`install/ir-remote.sh` (and mirrored in `pi-gen/`); PolicyKit covers only NetworkManager. Several other `milo-*` scripts under `/usr/local/bin/` (`milo-first-boot`, `milo-wait-ready.sh`, `milo-ir-keytable-setup`, `milo-apply-avahi-iface`, `milo-navidrome-provision`, `milo-mdns-probe`, `milo-brightness-7`) run directly as root via their own systemd unit instead — no sudoers entry needed.
2. **ALSA only** — no Pipewire/PulseAudio (HiFiBerry compatibility).
3. **Async everywhere** — all file/network/subprocess I/O is async; shared state under `asyncio.Lock()`.
4. **Runs as the `milo` user** — no root.
5. **Local network only** — CORS restricted to milo.local + localhost:5173.
6. **CamillaDSP is always in the audio path** (for volume control); EQ/compressor/loudness are toggled via `bypass_effects()`/`restore_effects()`, not ALSA routing. ALSA device selection via `MILO_MODE=direct|multiroom` (auto-generated `routing.env`).

Systemd: all units live in `system/` (`ls system/`); sources `BindsTo=milo-backend` (stop with it); snapcast units have no `WantedBy` — lifecycle owned by `AudioRoutingService._sync_snapcast_state`. Detail: [docs/architecture.md](docs/architecture.md).

## No legacy / migration code

Milō is a fixed-purpose appliance — there is no legacy fleet to protect. Keep a **single optimized code path**:
- No migration/fallback paths, compatibility shims, or legacy feature flags.
- No `data.get("old") or data.get("new")` chain fallbacks to absorb old payload/stored shapes — fix the producer to one canonical key. Same on the frontend: no `event.data?.x ?? event.data?.y`.
- No version detection on persisted files — use the fail-loud schema-bump protocol above.
- No `data.setdefault(...)` in a loader to auto-create a missing top-level key — fail loud.
- Remove dead code on sight (but check Milo-Mac first for routes/WS events — see top).

## Dev-only vs production bugs

End users run the **pre-built** frontend served by nginx from `dist/` — no Vite dev server, HMR, stale tabs, or `localhost:5173`. When a bug is reported *while developing*, classify it before writing code:
- **Dev-only artifact** (stale JS chunk after a rebuild with a tab open → blank page; references to `localhost:5173`/`?t=…`/`[vite]`; stale `localStorage`/SW cache; anything that disappears on hard refresh) → **diagnose and explain, do NOT add code.** Reload guards / version checks would bloat the prod bundle for a scenario no end user hits.
- **Real bug** (reproduces from a clean prod boot or on the Pi kiosk; triggered by appliance actions; server-side trace in `journalctl`/`errors.log`; anything hardware — ALSA/CamillaDSP/ROC/Snapcast/encoder/screen) → **fix it.**

When unsure, ask: *"reproducible from a clean prod boot, or only from dev-session state?"* Full telltale lists: [docs/development.md](docs/development.md).

## Do not write in french except for i18n


## Git

Never create a branch unless explicitly asked — commit to the current branch, `main` included. This overrides the default "branch first on the default branch" behaviour.

## Lint floor

CI ([.github/workflows/lint.yml](.github/workflows/lint.yml)) blocks merge on: `ruff check backend/`, `pytest backend/`, `npm run lint:js`, `npm run lint:css`. Enforced rules:
- **eslint:** `no-restricted-imports` (axios outside `apiCall.js`), `no-restricted-syntax` (`console.*`), `no-restricted-globals` (bare timers).
- **ruff:** `S110`/`S112` (try-except-pass/continue).
- **stylelint:** `color-no-hex`, no `rgba|hsla` on color properties, no typography redefinition in scoped CSS.

**Frontend Vitest** suite exists in `frontend/tests/` but is **temporarily skipped in CI** — ~97 tests still mock `axios.*` after the apiCall migration; re-enable once retargeted at `apiCall`. Bypass a rule only with a per-line directive + reason (`# noqa: S110 -- <why>`, `// eslint-disable-next-line <rule> -- <why>`); no file/repo-level disables. History + deferred items (TypeScript, pyright strict, husky): [docs/development.md](docs/development.md).

## Reference docs

- [docs/architecture.md](docs/architecture.md) — technologies, audio routing, persistence inventory, systemd, security.
- [docs/development.md](docs/development.md) — setup, adding a source (full), testing, debugging, lint history, dev-vs-prod detail.
- [README.md](README.md) — install + hardware.
