# CLAUDE.md

Guidance for Claude Code when working in this repository. The full reference lives in [docs/architecture.md](docs/architecture.md) and [docs/development.md](docs/development.md) — this file is the **rule index**, not a manual; don't duplicate the docs here.

## Project overview

Milō is a multiroom audio system for Raspberry Pi (Spotify Connect, AirPlay 2, Bluetooth, Mac streaming via ROC, Internet Radio, Podcasts, CD, DLNA/UPnP, Qobuz Connect, a local Music Library). FastAPI (Python) backend + Vue 3 frontend, ALSA for audio — no Pipewire/PulseAudio.

## External API clients — Milo-Mac (read before deleting any route or WS event)

The REST + WebSocket API has a **second consumer beyond `frontend/`: Milo-Mac**, a separate macOS app (`github.com/leodurandfr/Milo-Mac`, not in this checkout) that remotely drives a unit and streams audio via ROC.

**A route or WS event with no caller in `frontend/src/` is NOT necessarily dead** — Milo-Mac may consume it. The surface Milo-Mac depends on is pinned in a manifest, [backend/tests/contracts/milo_mac_contract.json](backend/tests/contracts/milo_mac_contract.json) — **the single source of truth**, no manual grep anymore. A **vendored snapshot** of Milo-Mac's two relevant source files lives in [backend/tests/contracts/vendor/milo-mac/](backend/tests/contracts/vendor/milo-mac/) (`MiloAPIService.swift` = REST, `WebSocketService.swift` = WS). `test_milo_mac_contract.py` (every `pytest`, offline) enforces **three** things: (1) the backend still serves every route/WS event the manifest lists, (2) the manifest **matches the vendored snapshot's surface exactly** — so it can't silently drift from the app it protects, and a broken Swift extractor fails loudly instead of passing on an empty surface — and (3) the manifest's `payload_invariants` (the exact fields Milo-Mac reads) hold on the typed `WsEvent` models. A non-blocking weekly CI job (`check_milo_mac_freshness.py`) re-clones the real Milo-Mac and warns when the **snapshot** (and thus the manifest) has fallen behind upstream.

**Workflow:** the manifest + snapshot are refreshed *consciously, together in one commit* when Milo-Mac evolves, not auto-grepped. If a contract test fails: backend-dropped-a-route → restore it or, if Milo-Mac genuinely dropped it, prune that manifest entry; manifest≠snapshot drift → re-download the two `.swift` files into `vendor/milo-mac/` and update the manifest to match (the freshness job tells you when upstream moved). Don't add compatibility shims; the appliance keeps a single code path.

Milo-Mac couples to the **whole wire contract**: REST paths/methods + request/response keys, and the WS events in the manifest (`full_state` envelope, the `multiroom_changed` discriminator, `volume_changed`, `settings/{volume_limits,dock_apps}_changed`). **Exception:** it reads WS `metadata` as an opaque dict — no metadata *sub-field* (`uri`, `client_count`, `album_art_height`, `track_position/duration`) is coupled, so an over-emitted metadata sub-field with no `frontend/src/` consumer is safe to drop. **Safe without touching the manifest:** purely frontend code (Vue components, Pinia stores, frontend Zod schemas).

## The satellite agent — milo-client (a second app in this repo)

[milo-client/app/](milo-client/app/) is a **separate FastAPI application**, installed on every multiroom *satellite* (never on the server). It is the only thing listening on `CLIENT_API_PORT` (8001), and the server backend drives each satellite's DSP entirely over that HTTP surface — volume, mute, EQ bands, compressor, loudness, mono, the master bypass gate, crossover/lowpass, snapclient buffer config, hardware/audio + reboot, and the satellite self-update endpoints. `EqualizerClientProxyService` is the transport (`request`, non-raising `try_request`); `SatelliteUpdateService` and a few `api/multiroom.py` routes call it directly with `aiohttp`.

Unlike Milo-Mac it needs **no manifest and no vendored snapshot** — both sides are in this checkout and ship in the same commit, so [backend/tests/contracts/test_milo_client_contract.py](backend/tests/contracts/test_milo_client_contract.py) checks **two** things: every server→satellite call is served by `milo-client/app/routes/` (both sides extracted by AST), **and** every key the server puts in a request body is one the satellite's handler actually reads. The second half exists because serving a route is not reading it: Pydantic drops unknown keys silently and a `List[dict]` field validates nothing inside, so a key the satellite ignores is a command that did nothing, with no error and no log. Producer side is *driven* (the real payloads are captured by running the push functions) since they are built from `to_dict()`s no AST resolves; consumer side is the handler's model fields plus the dict keys the service functions it calls read by name. That all matters because a mismatch is otherwise **invisible in CI and in dev**: no import error, no failing route — just a satellite that silently ignores a command, reproducible only on a second physical unit (the checklist's first blind spot). If the test fails, fix the side that moved; don't add a shim.

**One record, one push.** A whole `EqualizerSettings` reaches a satellite through exactly one function, `EqualizerClientProxyService.apply_record` — used by the live write (`MultiroomEqualizerService`), the reconnection sync (`SnapcastWebSocketService`) and the pending replay (`CrossoverService`). `EqualizerRouter` owns the other granularity, one targeted setting to one client. Two primitives, one implementation each: the alternative (four hand-rolled push sequences) is what let them drift into different endpoint shapes. Bands carry **tuning only** — per-band pipeline membership is the master toggle's (`PUT /equalizer/enabled`, applied last, after the effects it gates), on the satellite exactly as locally.

**One client, one admission.** Four snapserver notifications can be the first to see a client arrive — the sweep at WebSocket connect, `Client.OnConnect`, `Server.OnUpdate`, the online flip the reconcile sweep detects — and which one wins is a race decided by whether the backend or the satellite booted first, so all four must leave the same state. They share **one** registration (`_register_snapclient`, the only caller of `registry.register_client`: it is where the pending wizard identity is honoured and cleared) and **one** sync (`_sync_reconnecting_client_volume` — passthrough, reconnection volume, EQ record, buffer config, then online). A client is announced online **only** by that sync, once the hardware confirmed, via `set_online_after`; announcing it earlier is unretryable, since snapserver and the registry then agree and no later transition fires. Enforced by `tests/architecture/test_client_admission.py`; a fifth path calls the two, it does not restate them.

## Commands

```bash
# Backend (from backend/)
python main.py                       # dev server on :8000
python -m pytest [-v] [-k name]      # tests

# Frontend (from frontend/)
npm run dev                          # dev server on :5173 (proxies API to backend)
npm run build                        # → frontend/dist/ (runs lint:css first)
npm run lint                         # eslint + stylelint
npm run test:run                     # vitest (needs the full repo: guardrails read backend/)

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
- `components/` — one dir per feature/source (`<Name>Source.vue` as entry point) + `ui/`. **Two shared players, and the split is by coupling, not by chronology** — don't merge them, and pick by *does the source have an in-app browser?*:
  - `audio/AudioPlayerFull.vue` (~480 l.) — **store-coupled**, full-screen takeover: it reads `unifiedAudioStore.systemState.metadata` and sends its own commands. For sources with nothing to browse in Milō: Spotify, CD (`showControls=true`, default), AirPlay/DLNA/Qobuz (`showControls=false`, receiver-driven).
  - `audio/AudioPlayer.vue` (~1400 l.) — **props-down / events-up** (`:title`/`:artwork`/`:isPlaying`, `@toggle-play`/`@swipe-*`), knows no store and no command name: a teleported mobile mini-player + expandable sheet that coexists with a list view. For the three sources with a browser: Radio, Podcast, Music Library.
  - Which component mounts is decided in exactly one place, `useRichDisplay()`'s `richSource` — read by both `AudioSourceView.vue` and `MainView.vue` so the two can't drift.
- `stores/` — `unifiedAudioStore.js` is the central audio mirror; others are per-feature.
- `composables/`, `services/` (WS client, `apiCall`, i18n, logger), `locales/` (8 langs, `english.json` canonical/fallback — all keys must exist there first), `views/` (`MainView.vue` SPA).

**Service init order is critical** — `dependencies.py::initialize_services()` resolves circular deps via setters *before* async init. Don't reorder without reading the comments there. New service: create under `core/{name}/`, add a creator in `dependencies.py::_create_service()`, register its async `initialize()` in `initialize_services()` if it has one. Detail: [docs/development.md](docs/development.md).

## Audio sources

Pick a source's **family** from two questions: *(1) is playback controlled from Milō's UI or by an external sender?* *(2) does it expose rich metadata (artwork/title/artist)?* Then follow only that family's layout. There is **no `AudioSourceProtocol`** — any docstring mention is drift to fix.

| Family | Sources | Backend `sources/{s}/` | Frontend `components/{s}/` |
|---|---|---|---|
| **A. Mute receiver** — external control, no rich metadata | Bluetooth, Mac | `source.py` (+ source-specific helpers: `agent.py`/`monitor.py`, `log_patterns.py`). **No `routes.py`** — commands via generic `/api/audio/control/{source}`. | None — rendered by `AudioSourceStatus` (icon + device name). |
| **B. Passive player** — external control, rich metadata | AirPlay, DLNA, Qobuz | `source.py` + `routes.py` only for what the sender can't deliver (AirPlay/DLNA: binary artwork; Qobuz: none — CDN artwork URL needs no proxy, so no `routes.py` at all) + `metadata_reader.py`/`monitor.py` as needed for the metadata feed. | `<AudioPlayerFull :showControls="false" :showProgress="true" />` — all three report position/duration, none accepts transport. |
| **C. Active player** — UI control, rich metadata | Spotify, Radio, Podcast, CD, Music Library | `source.py` + networking as needed: `websocket.py` (Spotify), `routes.py`+`data.py`+external API for the *catalog* (Radio/Podcast), `models.py`. CD's `routes.py` is cover art only — playback is all commands. Music Library is the richest — a catalog-engine split (`navidrome_client.py`, `discovery.py`, `browse.py`, `disc_merge.py`, plus `shares.py`+`storage.py` for USB/SMB/NFS mounting) on top of the usual `routes.py`+`data.py`+`models.py`. | One of the **two** shared players (see below) + custom UI (tracklist/queue/favorites). |

Every source package exports **exactly one name**, the `{Name}Source` class `dependencies.py` instantiates (`__all__ = ["{Name}Source"]`). Anything else — `router`, `setup_{s}_routes`, data services, models — is imported from its own submodule by the code that needs it; re-exporting it just grows a second, unused API surface that drifts.

All sources extend `BaseAudioSource(ABC)` — public `start/stop/command/refresh_metadata`, override `_do_start` (abstract) plus `_do_stop`/`_handle_command`/`_cleanup` as needed (there is no `status()`/`_get_status()`: status is broadcast over WS, never polled); constructor `(config, state_machine, settings_service, systemd_manager)`, plus `camilladsp_service` for the two family-A receivers. The four mpv-based sources (Radio, Podcast, CD, Music Library) extend **`shared/mpv_audio_source.py::MpvAudioSource`** instead, which adds the mpv controller, the shared `_monitor_loop()` and auto-stop-on-pause — its hooks (`_on_monitor_tick`, `_on_mpv_disconnect`, `_auto_stop_action`) replace a hand-rolled monitor. Adding a source: define the enum in `core/models/audio_state.py::AudioSource`, create the module, register a creator + the source in `dependencies.py`, add 2 ALSA device variants, update stores. Full checklist + reference (`radio/`): [docs/development.md](docs/development.md).

**Rules (all families):**
- **No `GET /<source>/status`** — status is broadcast over WS only.
- **No `POST /<source>/restart`** — restart is a systemd/admin concern.
- **Commands:** every command is declared in `COMMANDS = {name: ParamsModel | None}`; `command()` validates against it before `_handle_command` runs, so an unregistered dispatch arm is unreachable. **One canonical name per concept** across sources — `pause`/`resume`/`stop`/`next`/`prev`/`seek`, `play_{thing}` to start one. A source that needs different *semantics* gets a different name that says so (Radio's `resume_playback` re-tunes the last station; a live stream has no unpause), never a synonym for an existing one. Enforced by `tests/architecture/test_source_conformance.py` + `tests/test_command_contract.py`.
- **One transport for commands: `POST /api/audio/control/{source}`** — for *every* family, not just A. `command()` already validates the params against `COMMANDS`, so a dedicated route adds no typing and only a second failure contract to keep in sync. A source gets a command route **only** when it (a) composes more than one command in one request (`POST /api/radio/play` re-tunes + broadcasts, `POST /api/podcast/play` plays + resume-seeks, so a caller cannot split them without an audible artefact) or (b) is pinned by Milo-Mac's manifest (`POST /api/radio/{play,stop}`). Everything else that is *not* a command — catalog browsing, favorites, binary artwork, a canonical list like `GET /api/podcast/playback-speeds` — belongs in `routes.py` as usual.
- **A source exposes its non-playback services, it does not proxy them.** `routes.py` reaches them through the source instance (the only object `make_source_dependency` injects) as a **property** — `source.station_data`, `source.podcast_data`, `source.data_service`, `source.shares` — and calls the service's own methods. A forwarding method on the `{Name}Source` class per service call is a second API surface that drifts, and it puts non-playback responsibility on the audio source. Enforced by `tests/architecture/test_source_conformance.py`.
- **Loggers:** routes use `logging.getLogger(__name__)`; source sub-modules use `logging.getLogger(f"source.{source_id}.<sub>")` to hang under the hierarchy `BaseAudioSource.__init__` creates. The legacy `feature.*` namespace is retired.
- **Routes (B, C):** use `run_source_command()` for playback, and `logger.error(...)` before every `raise HTTPException` that signals a real failure. An expected `404` on an optional asset (no artwork for this track, no cover for this disc) is **not** a failure: `logger.debug` it, so it never reaches the `WebSocketLogHandler` banner.

Read the existing sources as references — they evolve. `music_library/` (richest C: catalog-engine split), `radio/`+`podcast/` (C), `airplay/`+`dlna/` (B: external process + binary artwork), `qobuz/` (B without `routes.py`), `spotify/` (C without `routes.py`), `mac/`+`bluetooth/` (A).

## Core code rules (backend)

- **Never mutate state directly** — `await state_machine.update_source_state(source, SourceState.ACTIVE, metadata)`, never `state_machine._state.active_source = …`. Transitions are guarded by `_transition_lock`; updates arriving while `transitioning` is set are **dropped, not buffered** — the post-start resync re-reads `source.state`/`source.metadata` to recover the final state. No replay queue exists; don't build one.
- **Broadcast every state change** via `state_machine.broadcast(event)`, where `event` is a `WsEvent` subclass from [backend/core/models/ws_events.py](backend/core/models/ws_events.py) — never `ws_manager.broadcast_dict()`; no dict-based emission path exists (new event → new subclass). One class per `(category, type)` pair, `CATEGORY`/`TYPE` class-level, the model's fields ARE the wire `data` payload. Wire format `{category, type, origin, data, timestamp}`; `origin` is the event's `source` field (falls back to `CATEGORY`), so category `"source"` events declare a `source` field. Categories: `source` (all audio sources — never source-specific ones), `system`, `routing`, `equalizer`, `multiroom`, `volume`, `settings`, `programs`, `network`.
- **WS payload contracts** — a `(category, type)` earns a Zod schema in `frontend/src/schemas/ws.js` **iff** (a) more than one app consumes it (e.g. Milo-Mac + frontend) **or** (b) it has already caused a shape bug; consumers read it via `parsedOn(category, type, schema, handler)` and MUST NOT read `event.data.x` raw. This is a deliberate admission rule, **not** an unfinished migration — pairs meeting neither test stay on raw `on(...)`; don't bulk-schematize them. (`full_state` and `volume_changed` are already validated by their own store schemas — `SystemStateSchema`/`VolumeStateSchema` in `unifiedAudioStore.js` — so they need no registry entry.) The backend-side shape lives in the event model in `core/models/ws_events.py` (each class docstring names its consumers) — no per-call-site payload docstrings, no codegen.
- **The registry bus is the one *internal* event system** — `ClientRegistryService._emit_event(RegistryEventType.X, {...})` is its only producer, and three services subscribe: `CrossoverService`, `VolumeStateStore`, and `SnapcastWebSocketService`, which re-emits each one as the typed WS event `REGISTRY_EVENT_CLASSES` maps it to via `event_cls(**data)`. So a payload key must be a field of that class, and a subscriber must only read keys a producer sends — a `.get()` on an absent key skips its arm in silence, which is how a renamed identifier left two dead handlers behind. Enforced by `tests/architecture/test_registry_events.py`; don't add a second producer.
- **All settings via `SettingsService`** — `await settings_service.set_setting('volume.alsa_max', 80)`, never edit the dict/file directly. Stored at `/var/lib/milo/settings.json` (atomic `os.replace`, file locks, corruption backups). The multiroom registry persists through **one** path, `_persist_state()` (clients + zones + EQ in a single write — they are coupled), and the persisted client shape is declared once, as `Client.PERSISTED_FIELDS`.
- **Background tasks** — never raw `asyncio.create_task` for fire-and-forget. Services: `self._bg = BackgroundTaskSet(logger, "label")`, then `self._bg.spawn(coro, label=…)`, and `await self._bg.cancel_all()` in `cleanup()`. Routes: add a `background_tasks: BackgroundTasks` param + `background_tasks.add_task(...)`. Raw `create_task` is allowed **only** for tracked long-running tasks stored on `self` (`self._monitor_task = asyncio.create_task(...)`).
- **Encapsulation** — never touch another service's `_private` attrs/methods from a route or another service; expose a public method/property instead.

## API conventions

- **Response format:** success → `"status": "success"`. `/status`-style endpoints return errors as HTTP 200 with `"status": "error"` (resilience pattern). Real errors → `raise HTTPException`.
- **Verbs:** `PUT` idempotent updates (settings, routing), `PATCH` partial updates (volume, zone, client props), `POST` actions + creation, `DELETE` removals (path param).
- **Paths:** kebab-case segments, never `snake_case` (`/wifi/saved`, `/scan-status`, `/dock-apps`). A resource has **one** spelling — a read and its write share the path and differ only by verb (`GET`+`PUT /api/routing/snapcast/server-config`), never `server-config` for one and `server/config` for the other. Collections are plural, and the item hangs off the collection (`POST /api/podcast/subscriptions`, `DELETE /api/podcast/subscriptions/{uuid}`) — no `/add` suffix.
  - **One deliberate exception:** `sources/music_library/routes.py` mirrors **Subsonic/Navidrome** naming, which is where its data comes from — hence singular items (`/album/{id}`, `/artist/{id}`, `/playlist/{id}`) and Subsonic verbs (`star`, `unstar`, `starred`, `genre-songs`) sitting next to Milō-native plurals (`/shares/{id}`). Keeping the upstream names makes the proxy layer readable against the Subsonic docs. This is **not** drift — don't "fix" it, and don't copy it into a new router either.
- **Router placement — three homes, one owner per prefix.** A router lives in `api/`, in `sources/{s}/routes.py`, or in `hardware/{name}_routes.py`. Never under `core/`, which is infrastructure. And a prefix has exactly **one** owning file: no router's prefix may be nested inside another's (`/api` itself excepted — the health router sits at the root). `/api/routing` was served by two routers in two layers, and a quarter of the commits touching either touched both; that file was also the closing edge of an import cycle the radio route tests had to work around. Enforced by `tests/architecture/test_wire_conventions.py`.
- **Pydantic:** all fields `snake_case`. Shared models in `api/models.py`, source-specific in `sources/{s}/models.py`. A settings category's payload shape lives **once**, in `core/models/settings_config.py`, shared by `GET /api/settings/bulk` and its `settings/<name>_changed` event; only the `*Request` (with its validators) is separate.
- **A settings default is declared once, in `SettingsService.defaults`** — `_validate_and_merge` reads its fallback operands from that dict rather than restating them, every section the dict declares is emitted unconditionally, and `GET /api/settings/bulk` therefore carries **no fallback at all**: it projects keys the validator guarantees. A default restated at the route layer can only disagree with the one the service declares, silently, showing a stale default as a stored value. What is *not* a default and stays literal: the validator's clamp bounds, deliberately wider than the matching `*Request`'s `ge`/`le` so a stored value outside the write range is reported, not rejected. Enforced by `tests/architecture/test_settings_defaults.py` (which mutates each default and requires the validator to follow) and, on the frontend side, `tests/architecture/settingsBulkContract.test.js`.
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
- **WS events:** subscribe in `App.vue` only and dispatch into Pinia stores — components react to store state, never to raw events (`useWebSocket()`'s `onReconnect`/`onVisibilityChange` are lifecycle callbacks, not event handling, and stay allowed in a component). Store state maintained by WS *deltas* (not full snapshots) MUST also be refetched in `App.vue::resyncStores()` — deltas missed while a tab was backgrounded/disconnected are never replayed. Both rules, plus `wsEventRegistry` ↔ `parsedOn()` agreement, are enforced by `tests/architecture/{wsWiring,resyncStores}.test.js`.
- **i18n:** `const { t } = useI18n()` in `<script setup>`, not the global `$t()`.
- **CSS:** use design tokens (`var(--color-*|--space-*|--radius-*)`); stylelint blocks hex literals + `rgba()/hsla()` in scoped `.vue` styles. Missing token → extend `assets/styles/design-system.css`, don't add a local exception. **No inline `// stylelint-disable`** — extend the design system, or whitelist the file in `.stylelintrc.cjs` with a one-line reason.
- **Typography:** apply via utility classes (`heading-1…4`, `text-body`, `text-mono`, `text-mono-small`, `display-1`); don't redeclare `font-*`/`line-height`/`letter-spacing` in scoped CSS (stylelint rejects it). Scoped CSS = layout, color, component spacing only.
- **Timers:** every `setTimeout`/`setInterval` in a component/composable/view/directive goes through `useTimer()` (auto-cleanup on unmount); bare globals are blocked by `no-restricted-globals`. Raw `window.set*` only in the timer-primitive layer (`useTimer/useDebounce/useVolumeThrottle`) and `directives/press.js`.
- **Constants:** structural constants used by 2+ modules live in `frontend/src/constants/`; promote on the 2nd consumer. Backend-derived values (speeds, codecs, presets) are fetched at runtime + cached, not hardcoded on both sides.
- **Code style:** `.js` files use semicolons; constants files use camelCase names.

## Persistence & schema-version protocol

Persistent data lives in `/var/lib/milo/` (settings, hardware, radio/podcast/cd data + image caches, equalizer, routing/mac/snapclient env, `camilladsp/`, `go-librespot/`, `errors.log`, `backups/`). Full inventory: [docs/architecture.md](docs/architecture.md).

**Unified per-client EQ:** one EQ record per client behind `MultiroomEqualizerService.get/set_client_eq(mac)` — the local client's lives in `equalizer.json`, remote clients' in `settings.json: multiroom.client_equalizer[mac]`; a zone holds no EQ (it derives from its members). One HTTP surface, no exceptions: `GET/PUT/POST /api/equalizer/target/{target}[/…]`, `target ∈ local · <mac> · zone:<id>`. Crossover is zone-only and still goes through it (`PUT /target/zone:<id>/crossover`, 400 on a non-zone target) — a second noun for the same resource is how the grammar rots.

**Schema bumps — fail-loud + reset, never migrate.** Each versioned JSON carries `"schema_version": N`; the owning service declares `SCHEMA_VERSION` and uses `load_versioned_json`/`save_versioned_json` ([backend/shared/persistence.py](backend/shared/persistence.py)). On mismatch they raise `SchemaVersionMismatch`; `initialize_services` logs a banner (exact `rm` + pointer) and `SystemExit(1)`, so systemd loops the banner until the operator deletes the file.

## Constraints (invariants)

1. **Privileged exec is centralized, never ad hoc** — systemd + power actions go through `SystemdServiceManager` (which shells `sudo systemctl …`, incl. `power()` for reboot/poweroff and `restart_self()` for the updater's own-unit restart); privileged file deploys go through the pinned sudoers helpers under `/usr/local/bin/milo-*`: `milo-deploy-update`, `milo-apply-hardware`, `milo-set-wifi-country`, `milo-mount`/`milo-umount` (Music Library USB+SMB/NFS), `milo-apply-ir-keymap` (IR remote pairing). No bare `sudo` anywhere else. Permissions come from two `NOPASSWD` policy files for the `milo` user — `/etc/sudoers.d/milo-backend` (the first five) and `/etc/sudoers.d/milo-ir-remote` — both authored **once**, under [rootfs/etc/sudoers.d/](rootfs/etc/sudoers.d/), and copied verbatim by `install/system.sh`+`install/ir-remote.sh` *and* by `pi-gen/`. A policy restated inline in an installer is how a script-installed unit and a flashed image come to grant different sets, silently: never re-author one, extend the file in `rootfs/`. The satellite's `/etc/sudoers.d/milo-client` follows the same rule from `milo-client/rootfs/`, and its grants are **argument-scoped** (`systemctl stop <unit>`), so a verb or unit name that moves alone is a real permission denial. Both directions — every `sudo` the code issues is granted, every grant still has a caller — are enforced offline by [backend/tests/contracts/test_privileged_exec_contract.py](backend/tests/contracts/test_privileged_exec_contract.py); PolicyKit covers only NetworkManager. Several other `milo-*` scripts under `/usr/local/bin/` (`milo-first-boot`, `milo-wait-ready.sh`, `milo-ir-keytable-setup`, `milo-apply-avahi-iface`, `milo-navidrome-provision`, `milo-mdns-probe`, `milo-brightness-7`) run directly as root via their own systemd unit instead — no sudoers entry needed. `milo-alsa-passthrough` needs neither: `amixer` only wants the `audio` group, so it runs as the service user from the CamillaDSP unit's `ExecStartPre`.
2. **ALSA only** — no Pipewire/PulseAudio (HiFiBerry compatibility).
3. **Async everywhere** — all file/network/subprocess I/O is async; shared state under `asyncio.Lock()`.
4. **Runs as the `milo` user** — no root.
5. **Local network only** — CORS restricted to milo.local + localhost:5173.
6. **CamillaDSP is always in the audio path** (for volume control), and it is the **only** attenuation stage: the card's own mixer is pinned at unity by `milo-alsa-passthrough`, wired as `ExecStartPre` of *both* CamillaDSP units (server + satellite) so it runs on every boot whatever the card. It discovers the control on the card (`Digital`/`DAC`/…) instead of reading a per-card table — a table is what left DAC boards attenuating. EQ/compressor/loudness are toggled via `bypass_effects()`/`restore_effects()`, not ALSA routing. ALSA device selection via `MILO_MODE=direct|multiroom` (auto-generated `routing.env`).

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

**CI cannot see the appliance.** Any change touching the audio path, a source, or hardware is not done until the ~10-minute smoke subset of [docs/manual/verification-checklist.md](docs/manual/verification-checklist.md) has been run on a unit against the prod build. State in the commit which set was run, and say so explicitly if it was cut short.

## Language

**Everything that lands in the repo is in English** — code, identifiers, comments, docstrings, commit messages, docs, test names, log messages. The only French in the repo is user-facing i18n content under `frontend/src/locales/`.

**Conversation with the user follows the user's language** (French). This is not a contradiction: the rule above is about artefacts, this one is about the exchange.

## Git

Never create a branch unless explicitly asked — commit to the current branch, `main` included. This overrides the default "branch first on the default branch" behaviour.

## Lint floor

CI ([.github/workflows/lint.yml](.github/workflows/lint.yml)) blocks merge on: `ruff check backend/`, `pytest backend/`, `npm run lint:js`, `npm run lint:css`, `npm run test:run`. Enforced rules:
- **eslint:** `no-restricted-imports` (axios outside `apiCall.js`), `no-restricted-syntax` (`console.*`), `no-restricted-globals` (bare timers).
- **ruff:** `F` (pyflakes — undefined name, redefinition, unused import/variable) + `S110`/`S112` (try-except-pass/continue). `F` is selected as a **family**, not rule by rule: `F401` alone is its least valuable member, and enabling that one while `F821`/`F811`/`F841` stay off is what kept individual unused imports coming back as findings. `E4`/`E7`/`E9` stay out on purpose — the `E402`s (imports after code in `api/models.py`, `main.py`) and the `E731` lambdas in tests are deliberate, and they are style rather than faults.
- **stylelint:** `color-no-hex`, no `rgba|hsla` on color properties, no typography redefinition in scoped CSS.

Bypass a rule only with a per-line directive + reason (`# noqa: S110 -- <why>`, `// eslint-disable-next-line <rule> -- <why>`); no file/repo-level disables. History + deferred items (TypeScript, pyright strict, husky): [docs/development.md](docs/development.md).

## Frontend tests — what earns one

`frontend/tests/` is **blocking again** (`npm run test:run`). It buys leverage, not coverage: this UI is refactored often, so a test that mounts a component and asserts rendered markup or CSS classes breaks on every redesign for near-zero defect yield. **Do not write them.** Four kinds earn their keep:

1. **Structural guardrails** (`tests/i18n/`, `tests/schemas/ws.test.js`, `tests/architecture/`) — mount nothing, and go red only when a real contract moves. Two of them read the **backend** sources directly (`core/models/ws_events.py`, `core/models/audio_state.py`) and derive their expectations from those typed models rather than from hand-written fixtures, so backend drift surfaces on the frontend build. Every extractor asserts its own output is non-trivial first: a broken parse must **fail loudly, not pass on an empty surface** (same doctrine as the Milo-Mac contract test).
2. **Store logic** — WS delta handling, derived state, guards. Drive the real stores through their own handlers; don't mock a sibling store, or you assert a fixture of its API instead of its behaviour.
3. **Pure functions** (`tests/pure/`) and composable logic (`tests/composables/`) — a host component may be mounted to give a composable a lifecycle, but nothing about the DOM is asserted.
4. **Schema contracts** — `schemas/api.js` are *resilience* schemas (`.catch()` defaults): they coerce, they don't reject. Assert the coercion.

HTTP goes through the `@/services/apiCall` mock ([frontend/tests/helpers/apiCallMock.js](frontend/tests/helpers/apiCallMock.js)) — **never mock `axios` in a test**; it is a layer the stores don't own, and mocking it is what made the previous suite rot. Assert an endpoint only where the store *chooses* it (target/zone resolution, local-vs-MAC) — not on straight pass-throughs, where the assertion just restates a string constant.

## Backend tests — what earns one

`backend/tests/` is blocking (`pytest backend/`). The behaviours that matter most here — ALSA, CamillaDSP, snapcast, D-Bus, the hardware — **cannot run in CI at all**; they are covered on a real unit by [docs/manual/verification-checklist.md](docs/manual/verification-checklist.md). A backend test therefore buys exactly one thing: it protects the surface CI genuinely owns. Four kinds earn their keep:

1. **Contract guardrails** — `tests/contracts/` (Milo-Mac manifest, response models), `tests/architecture/`, `test_ws_events.py`, `test_command_contract.py`. They derive their expectations from the typed models and enums, never from hand-written fixtures, and every extractor asserts its own output is non-trivial first: a broken parse must **fail loudly, not pass on an empty surface**. The highest-value files in the suite — extend them, don't delete them.
2. **Pure logic** — parsing, merging, curves, maths: `_to_ms`, disc merge, version compare, volume clamp, IR scancode decode. Deterministic, no mocks, high yield.
3. **Service behaviour across a mocked boundary** — the mock stands for the **outside world** (CamillaDSP, snapserver, Navidrome, systemd, D-Bus, mpv, HTTP) and the assertion is what the service *did* to it: which call, in which order, under which failure. [backend/tests/test_dlna_source.py](backend/tests/test_dlna_source.py) is the reference: GENA resends the full state on every event, so the bridge must emit each field only when it actually changed, and `assert_called_once` is the only way to state that.
4. **Persistence and state transitions** — settings round-trips on a `tmp_path`, the `SchemaVersionMismatch` fail-loud path, `AudioStateMachine` transition guards and the drop-during-transition rule.

**The rule that decides the rest: never assert on a value the test itself wrote.** A test that builds a dict, hands it to a passthrough and checks the keys it just typed cannot fail; a test that re-implements the production expression in its own body asserts Python, not Milō. This is the backend's "never mock `axios`" — it is what the 70 tests deleted in the phase-3 pass had in common. Corollaries:

- **Mock the outside world, never the unit's own internals.** `patch.object(service, "_private")` pins a method name: rename it and the test goes red with no behaviour change. Mocking a *collaborator's* public API is fine — that is kind 3.
- **No wall-clock budgets.** Latency measured on an all-mock path measures the mock; the real cost is network + DSP, which CI does not have. Timing belongs in the manual checklist.
- **Don't restate a constant.** `assert DEFAULT_X["k"] == 120` only fails when someone changes it on purpose. Assert the behaviour that reads it.
- **Don't re-check what a guardrail already proves.** `tests/architecture/test_source_conformance.py` covers every source's base contract for all 10; a per-source `isinstance` test adds nothing.
- **Argv is worth asserting only when argv *is* the contract** — `sudo systemctl restart --no-block …` is pinned by `/etc/sudoers.d/milo-backend`, so `test_systemd.py` asserting it is right. An incidental subprocess call is not.
- **No coverage threshold in CI.** Coverage as a target rewards exactly the tests this rule excludes.

Docstrings say what breaks when the test fails and name the consumer — no story/AC/ticket references, there is nothing in the repo to resolve them against.

## Reference docs

- [docs/architecture.md](docs/architecture.md) — technologies, audio routing, persistence inventory, systemd, security.
- [docs/development.md](docs/development.md) — setup, adding a source (full), testing, debugging, lint history, dev-vs-prod detail.
- [README.md](README.md) — install + hardware.
