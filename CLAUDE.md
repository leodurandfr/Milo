# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Milō is a **multiroom audio appliance** for Raspberry Pi: Spotify Connect, Qobuz Connect, Tidal Connect, AirPlay 2, Bluetooth, DLNA/UPnP, Internet Radio, Podcasts, CD, a local Music Library, and Mac streaming over ROC. FastAPI (Python 3.13) backend + Vue 3 frontend, **ALSA only** — no PipeWire/PulseAudio (HiFiBerry compatibility).

Full reference lives in [docs/architecture.md](docs/architecture.md) and [docs/development.md](docs/development.md). **This file is the rule index, not a manual** — don't duplicate those docs here.

## Commands

```bash
# Backend (from backend/)
python main.py                                   # dev server on :8000
python -m pytest                                 # full suite (asyncio_mode=auto, -v by default)
python -m pytest tests/test_radio_source.py      # one file
python -m pytest -k "test_start_success"          # one test by substring
python -m pytest -m "not integration"            # markers: unit / integration / slow

# Frontend (from frontend/)
npm run dev                                      # :5173, proxies /api to the backend
npm run build                                    # → dist/ (runs lint:css first)
npm run lint                                     # eslint + stylelint
npm run test:run                                 # vitest — needs the WHOLE repo (guardrails read backend/)
npm run test:run -- tests/stores/radioStore.test.js   # one file

# Satellite (from the repo root) — same gate as the backend, separate pytest run:
# a combined `pytest backend/ milo-client/` moves rootdir to the repo root, which
# drops backend/pytest.ini and its `asyncio_mode = auto`.
pytest milo-client/

# Lint floor (what CI blocks on)
ruff check backend/ milo-client/ && pytest backend/ && pytest milo-client/
cd frontend && npm run lint:js && npm run lint:css && npm run test:run

# On a unit
sudo journalctl -u milo-backend -f
sudo systemctl restart milo-backend
```

## Two applications ship from this checkout

1. **`backend/`** — the server. Runs on the main unit, owns all state.
2. **`milo-client/app/`** — a *second, separate* FastAPI app installed on every multiroom **satellite** (never on the server). It is the only listener on `CLIENT_API_PORT` (8001), and the server drives each satellite's DSP entirely over that HTTP surface: volume, mute, EQ bands/filters, compressor, loudness, mono, the master bypass gate, crossover/lowpass, snapclient buffer config, hardware + reboot, and the self-update endpoints. Per-client **delay** is the one exception — it is native Snapcast latency (`Client.SetLatency` from the server), so the satellite's `/equalizer/delay` route stays dormant. Transport is `EqualizerClientProxyService` (`request` / non-raising `try_request`); `SatelliteUpdateService` and a few `api/multiroom.py` routes use `aiohttp` directly.

Both sides ship in the same commit, so there is no versioning and no shim — [backend/tests/contracts/test_milo_client_contract.py](backend/tests/contracts/test_milo_client_contract.py) checks (a) every server→satellite call is served by `milo-client/app/routes/`, and (b) **every key the server puts in a request body is one the satellite's handler actually reads**. The second half matters because Pydantic drops unknown keys silently and `List[dict]` validates nothing inside: a key the satellite ignores is a command that did nothing — no error, no log, reproducible only on a second physical unit.

**One record, one push.** A whole `EqualizerSettings` reaches a satellite through exactly one function, `EqualizerClientProxyService.apply_record` (used by the live write, the reconnection sync, and the pending replay). `EqualizerRouter` owns the other granularity: one targeted setting to one client. Two primitives, one implementation each.

**One client, one admission.** Four snapserver notifications can be first to see a client arrive (the sweep at WebSocket connect, `Client.OnConnect`, `Server.OnUpdate`, the reconcile sweep's online flip) and which wins is a boot-order race — so all four share **one** registration (`_register_snapclient`, the only caller of `registry.register_client`) and **one** sync (`_sync_reconnecting_client_volume`). A client is announced online *only* by that sync, after the hardware confirmed, via `set_online_after`. Enforced by [backend/tests/architecture/test_client_admission.py](backend/tests/architecture/test_client_admission.py).

## Third consumer: Milo-Mac (read before deleting any route or WS event)

The REST + WebSocket API has a consumer outside this checkout: **Milo-Mac** (`github.com/leodurandfr/Milo-Mac`), a macOS app that remotely drives a unit and streams via ROC. **A route or WS event with no caller in `frontend/src/` is NOT necessarily dead.**

The pinned surface is [backend/tests/contracts/milo_mac_contract.json](backend/tests/contracts/milo_mac_contract.json) — the single source of truth, no manual grepping. A **vendored snapshot** of Milo-Mac's two relevant Swift files lives in [backend/tests/contracts/vendor/milo-mac/](backend/tests/contracts/vendor/milo-mac/). `test_milo_mac_contract.py` (offline, every `pytest`) enforces three things: the backend still serves every route/WS event the manifest lists; the manifest **matches the vendored snapshot's surface exactly**; and the manifest's `payload_invariants` hold on the typed `WsEvent` models. A non-blocking weekly CI job (`check_milo_mac_freshness.py`) re-clones the real app and opens a tracking issue when the snapshot falls behind.

**Workflow:** manifest + snapshot are refreshed *consciously, together in one commit*. Contract failure → backend dropped a route: restore it, or prune the manifest entry if Milo-Mac genuinely dropped it. Manifest ≠ snapshot: re-download the two `.swift` files and update the manifest to match. Never add a compatibility shim.

Milo-Mac couples to REST paths/methods + request/response keys, and to WS `(category, type)` pairs across `system`, `source`, `volume`, `routing` and `settings` — plus `payload_invariants` naming the exact fields it reads. **Read the manifest for the list; never trust a summary here.** The one worth knowing by heart is `routing/multiroom_error`: its invariant is *presence only, no payload field is read*, so it looks unreferenced from every angle and is the easiest entry to delete by accident.

**Exception:** Milo-Mac reads WS `metadata` as an opaque dict, so an over-emitted metadata *sub-field* with no `frontend/src/` consumer is safe to drop. Purely frontend code (Vue components, Pinia stores, frontend Zod schemas) never needs a manifest touch.

## Backend architecture

State flow: **backend change → `state_machine.broadcast(WsEvent)` → WS → Pinia store → reactive UI.**

- `core/` — infrastructure: `state.py` (`AudioStateMachine`, single source of truth), `audio_source.py` (`BaseAudioSource` ABC), `settings.py`, `systemd.py`, `log_handler.py`; subpackages `models/ volume/ equalizer/ multiroom/ connectivity/ network/ system/ updates/ lyrics/`.
- `sources/` — one self-contained subpackage per source.
- `api/` — REST routers + shared Pydantic `models.py` + `route_helpers.py` + `source_dependency.py`.
- `hardware/` (rotary, IR, BT remote, screen, fan + their `*_routes.py`), `ws/`, `shared/` (`MpvController`, `BackgroundTaskSet`, `persistence`, decorators, `artwork` = dimension decoding + `artwork_resolver` = iTunes cover lookup, journalctl), `config/constants.py`, `dependencies.py`.

**Service init order.** `dependencies.py::initialize_services()` runs four steps: (1) retrieve instances (triggers lazy creation), (2) resolve circular deps via setters, (3) register sources in the state machine, (4) start parallel async init. Only **three** things are actually order-sensitive, and the function's own docstring names them — the registry-subscription block at the end of STEP 2 (subscribers are notified in subscription order), STEP 3 before the async init, and STEP 3b writing the env files before any source unit starts. Every other STEP 2 line is a plain assignment and commutes. Read that docstring before reordering; don't treat the whole function as untouchable, and don't move those three. New service: create under `core/{name}/`, add a creator in `_create_service()`, register its async `initialize()` if it has one.

Principles: dict-based DI; D-Bus via `dbus-next`; event-driven over polling; **always fail open** when D-Bus or the underlying service is unavailable, so a dev host runs without hardware.

### Audio sources — pick the family first

Two questions decide the layout: *(1) is playback controlled from Milō's UI or by an external sender?* *(2) does it expose rich metadata (artwork/title/artist)?* Then follow **only** that family's shape. There is **no `AudioSourceProtocol`** — any docstring mentioning one is drift to fix.

| Family | Sources | Backend `sources/{s}/` | Frontend `components/{s}/` |
|---|---|---|---|
| **A. Mute receiver** — external control, no rich metadata | Mac | `source.py` + source-specific helpers (`log_patterns.py`). **No `routes.py`** — a command, if one is ever needed, goes through the generic `/api/audio/control/{source}`; Mac itself registers none (`COMMANDS` empty, no `_handle_command`). | None — rendered by `AudioSourceStatus` (icon + device name). |
| **B. Passive player** — external control, rich metadata | AirPlay, DLNA, Qobuz | `source.py` + `routes.py` only for what the sender can't deliver (AirPlay/DLNA: binary artwork; Qobuz: nothing — CDN artwork URL needs no proxy, so **no `routes.py` at all**) + `metadata_reader.py`/`monitor.py` for the metadata feed. | `<AudioPlayerFull :showControls="false" />` — none accepts transport. All three report position/duration and DLNA/Qobuz draw it (`:showProgress="true"`); **AirPlay alone passes `false`** — nothing tells it the sender paused, so the bar ran on through a paused track. Its docstring lists every channel measured. |
| **C. Active player** — UI control, rich metadata | Spotify, Tidal, Bluetooth, Radio, Podcast, CD, Music Library | `source.py` + networking as needed: `websocket.py` (Spotify), `controller_socket.py` (Tidal — a Unix socket where Spotify has a WebSocket, and no `models.py`: every command it takes is param-less), `avrcp.py` (Bluetooth — see below), `routes.py`+`data.py`+external API for the *catalog* (Radio/Podcast), `models.py`. CD's `routes.py` is cover art only. Music Library is the richest: a catalog-engine split (`navidrome_client.py`, `discovery.py`, `browse.py`, `disc_merge.py`, `shares.py`+`storage.py` for USB/SMB/NFS mounting). | One of the **two** shared players (below) + custom UI (tracklist/queue/favorites). |

**Bluetooth is the one source with two feeds, and only one of them is guaranteed.** BlueALSA (`monitor.py`) answers *who is connected* and decides ACTIVE vs READY; BlueZ AVRCP (`avrcp.py`, `org.bluez.MediaPlayer1`) answers *what is playing* and carries the transport — but an AVRCP target is optional and senders publish empty tracks. So it is also the only source that moves between `AudioSourceStatus` and `AudioPlayerFull` on metadata alone, and the gate in `useRichDisplay()` is title + artist with **no `is_playing` clause**: the player draws a pause button, and dropping to the card on pause would delete the button just pressed. Three consequences of AVRCP worth knowing before touching it: **no seek** (`:seekable="false"`); **no cover** over the link, so it is resolved from the track text by `shared/artwork_resolver.py` (album first — the field radio's in-band feed lacks) and **merged in at publish time, never written into the AVRCP snapshot**, which the position poll replaces wholesale; and **a playhead nothing reports reliably** — BlueZ extrapolates `Position` from the sender's last anchor and re-anchors only on a state change, so a signalled Position is authoritative while a read one is advisory (a Get taken next to a state change answers from the stale anchor), nothing at all is signalled between state changes, and after a Previous-restart BlueZ stays adrift *permanently*. Milō therefore counts the playhead itself from the press whenever BlueZ cannot, and hands it back on the first signal. A seek done on the sender is invisible because BlueZ subscribes to the position-changed event with the maximum possible interval on purpose (*"we only use it to resync"*, hardcoded), so positions arrive only at state/track changes; it recovers on the next pause, skip or track end, and that is accepted rather than worked around. Read [avrcp.py](backend/sources/bluetooth/avrcp.py)'s module docstring, which carries the traces, before touching any of it.

Every source package exports **exactly one name**, the `{Name}Source` class `dependencies.py` instantiates (`__all__ = ["{Name}Source"]`). Anything else — `router`, `setup_{s}_routes`, data services, models — is imported from its own submodule by whoever needs it.

All sources extend `BaseAudioSource(ABC)`: public `start/stop/command/refresh_metadata`; override `_do_start` (abstract) plus `_do_stop`/`_handle_command`/`_cleanup`. There is **no `status()`/`_get_status()`** — status is broadcast over WS, never polled. Constructor is `(config, state_machine, settings_service, systemd_manager)`, plus `camilladsp_service` for the two family-A receivers. The four mpv sources (Radio, Podcast, CD, Music Library) extend [backend/shared/mpv_audio_source.py](backend/shared/mpv_audio_source.py)::`MpvAudioSource` instead, which supplies the mpv controller, the shared `_monitor_loop()` and auto-stop-on-pause via hooks (`_on_monitor_tick`, `_on_mpv_disconnect`, `_auto_stop_action`) — never hand-roll a monitor.

Adding a source: enum in `core/models/audio_state.py::AudioSource`, module, creator + registration in `dependencies.py`, 2 ALSA device variants, frontend touchpoints. Full checklist + the loopback-subdevice constraint: [docs/development.md](docs/development.md). Reference: `radio/`.

**Rules (all families):**
- **No `GET /<source>/status`**, **no `POST /<source>/restart`** — status is WS-only, restart is systemd's job.
- **Commands** are declared in `COMMANDS = {name: ParamsModel | None}`; `command()` validates against it before `_handle_command` runs, so an unregistered dispatch arm is unreachable. **One canonical name per concept** across sources — `pause`/`resume`/`stop`/`next`/`prev`/`seek`, `play_{thing}` to start one. Different *semantics* get a different name that says so (Radio's `resume_playback` re-tunes the last station — a live stream has no unpause), never a synonym.
- **One transport for commands: `POST /api/audio/control/{source}`**, for *every* family. A source earns a dedicated command route only when it (a) composes more than one command in one request (`POST /api/radio/play` re-tunes + broadcasts; `POST /api/podcast/play` plays + resume-seeks — splitting them causes an audible artefact) or (b) is pinned by Milo-Mac's manifest. Everything that is *not* a command — catalog browsing, favorites, binary artwork, a canonical list like `GET /api/podcast/playback-speeds` — belongs in `routes.py` as usual.
- **A source exposes its non-playback services, it does not proxy them.** `routes.py` reaches them through the source instance as a **property** — `source.station_data`, `source.podcast_data`, `source.data_service`, `source.shares` — and calls the service's own methods. A forwarding method per service call is a second API surface that drifts.
- **Loggers:** routes use `logging.getLogger(__name__)`; source sub-modules use `logging.getLogger(f"source.{source_id}.<sub>")` to hang under the hierarchy `BaseAudioSource.__init__` creates. The legacy `feature.*` namespace is retired.
- **Routes (B, C):** use `run_source_command()` for playback, and `logger.error(...)` before every `raise HTTPException` that signals a real failure. An expected `404` on an optional asset (no artwork for this track, no cover for this disc) is **not** a failure — `logger.debug` it, so it never reaches the `WebSocketLogHandler` banner.

Enforced by [backend/tests/architecture/test_source_conformance.py](backend/tests/architecture/test_source_conformance.py) and `tests/test_command_contract.py`.

## Core code rules (backend)

- **Never mutate state directly** — `await state_machine.update_source_state(source, SourceState.ACTIVE, metadata)`, never `state_machine._state.active_source = …`. Transitions are guarded by `_transition_lock`; updates arriving while `transitioning` is set are **dropped, not buffered** — the post-start resync re-reads `source.state`/`source.metadata` to recover. No replay queue exists; don't build one.
- **Broadcast every state change** via `state_machine.broadcast(event)` where `event` is a `WsEvent` subclass from [backend/core/models/ws_events.py](backend/core/models/ws_events.py). Never `ws_manager.broadcast_dict()` — no dict-based emission path exists (new event → new subclass). One class per `(category, type)` pair, `CATEGORY`/`TYPE` at class level, **the model's fields ARE the wire `data` payload**, and the class docstring names its consumers. Wire format `{category, type, origin, data, timestamp}`; `origin` is the event's `source` field (falling back to `CATEGORY`), so category `source` events must declare a `source` field. The nine categories: `source` (all audio sources — never source-specific ones), `system`, `routing`, `equalizer`, `multiroom`, `volume`, `settings`, `programs`, `network`.
- **WS payload contracts** — a `(category, type)` earns a Zod schema in `frontend/src/schemas/ws.js` **iff** (a) more than one app consumes it or (b) it has already caused a shape bug; consumers then read it via `parsedOn(category, type, schema, handler)` and MUST NOT touch `event.data.x` raw. This is a deliberate admission rule, **not** an unfinished migration — pairs meeting neither test stay on raw `on(...)`; don't bulk-schematize. (`full_state` and `volume_changed` are already validated by `SystemStateSchema`/`VolumeStateSchema` — declared in `schemas/api.js`, applied in `unifiedAudioStore.js` via `validateSchema` — so they need no `wsEventRegistry` entry.)
- **The registry bus is the one *internal* event system** — `ClientRegistryService._emit_event(RegistryEventType.X, {...})` is its only producer; three services subscribe (`CrossoverService`, `VolumeStateStore`, `SnapcastWebSocketService`, which re-emits each as the typed WS event `REGISTRY_EVENT_CLASSES` maps it to via `event_cls(**data)`). So a payload key must be a field of that class, and a subscriber must only read keys a producer sends — a `.get()` on an absent key skips its arm in silence, which is how a renamed identifier left two dead handlers behind. Don't add a second producer.
- **All settings via `SettingsService`** — `await settings_service.set_setting('volume.alsa_max', 80)`, never edit the dict/file. Stored at `/var/lib/milo/settings.json` (atomic `os.replace`, file locks, corruption backups). The multiroom registry persists through **one** path, `_persist_state()` (clients + zones + EQ in a single write — they are coupled); the persisted client shape is declared once as `Client.PERSISTED_FIELDS`.
- **A settings default is declared once, in `SettingsService.defaults`** — `_validate_and_merge` reads its fallback operands from that dict rather than restating them, every declared section is emitted unconditionally, and `GET /api/settings/bulk` therefore carries **no fallback at all**: it projects keys the validator guarantees. What is *not* a default and stays literal: the validator's clamp bounds, deliberately wider than the matching `*Request`'s `ge`/`le` so a stored out-of-range value is reported, not rejected.
- **Background tasks** — never raw `asyncio.create_task` for fire-and-forget. Services: `self._bg = BackgroundTaskSet(logger, "label")`, `self._bg.spawn(coro, label=…)`, `await self._bg.cancel_all()` in `cleanup()` — and that `cleanup()` must be called from the lifespan teardown. Routes: add a `background_tasks: BackgroundTasks` param + `background_tasks.add_task(...)`. Raw `create_task` is allowed **only** for tracked long-running tasks stored on `self`.
- **Encapsulation** — never touch another service's `_private` attrs/methods from a route or another service; expose a public method/property.

The last three, plus "no injection setter without a production caller", are enforced by [backend/tests/architecture/test_service_wiring.py](backend/tests/architecture/test_service_wiring.py).

## API conventions

- **Response format:** success → `"status": "success"`. `/status`-style endpoints return errors as HTTP 200 with `"status": "error"` (resilience pattern). Real errors → `raise HTTPException`. **Never `{"success": bool}`** — that is not an envelope, it is a failure a consumer misses.
- **Verbs:** `PUT` idempotent updates (settings, routing), `PATCH` partial updates (volume, zone, client props), `POST` actions + creation, `DELETE` removals (path param).
- **Paths:** kebab-case segments, never `snake_case`. A resource has **one** spelling — a read and its write share the path and differ only by verb (`GET`+`PUT /api/routing/snapcast/server-config`). Collections are plural and the item hangs off the collection (`POST /api/podcast/subscriptions`, `DELETE /api/podcast/subscriptions/{uuid}`) — no `/add` suffix.
  - **One deliberate exception:** `sources/music_library/routes.py` mirrors **Subsonic/Navidrome** naming, which is where its data comes from — singular items (`/album/{id}`, `/artist/{id}`) and Subsonic verbs (`star`, `unstar`, `starred`, `genre-songs`) sitting next to Milō-native plurals (`/shares/{id}`). This keeps the proxy readable against the Subsonic docs. Not drift — don't "fix" it, don't copy it into a new router.
- **Router placement — three homes, one owner per prefix.** A router lives in `api/`, in `sources/{s}/routes.py`, or in `hardware/{name}_routes.py`. Never under `core/`, which is infrastructure. And no router's prefix may nest inside another's (`/api` itself excepted — the health router sits at the root): `/api/routing` was once served by two routers in two layers, and a quarter of the commits touching either touched both.
- **Pydantic:** all fields `snake_case`, request and response alike. Shared models in `api/models.py`, source-specific in `sources/{s}/models.py`. A settings category's payload shape lives **once**, in `core/models/settings_config.py`, shared by `GET /api/settings/bulk` and its `settings/<name>_changed` event; only the `*Request` (with its validators) is separate.
- **Helpers** ([backend/api/route_helpers.py](backend/api/route_helpers.py)): `run_source_command(source, cmd, data, context)`; `api_error_handler(context, log)` (async ctx mgr); `parse_audio_source(name)` (→ `AudioSource` or HTTP 400, for untrusted input); `coerce_audio_source_or_none(name)` (defensive, for trusted state values).
- **Error-handling doctrine per layer:**

| Layer | Policy | Anti-pattern |
|---|---|---|
| HTTP route | `api_error_handler` or `run_source_command`; no bare try/except in the body; enum via `parse_audio_source`. | `except Exception: raise HTTPException(500, str(e))` per route |
| Service | **Log + raise.** Legitimate fallback → `@handle_errors(default=…, level='error')`. | `except: return False` with no log |
| Background loop | Wrap the **loop body** in `try/except Exception` + error log + `continue`. | `except CancelledError` alone (transient I/O kills the task silently) |
| Best-effort hw/external | Scoped `except SpecificError` + `warning` log + named fallback. | `except Exception: logger.debug(...)` masking failures |

Intentional silent swallow → `contextlib.suppress(Type)`, never bare `except: pass` (trips ruff S110/S112). Examples: `api/equalizer.py`, `sources/radio/shazam.py`, `sources/podcast/source.py::_progress_save_loop`.

Path casing, one-spelling, snake_case fields, router placement, prefix ownership and the envelope rule are all enforced by [backend/tests/architecture/test_wire_conventions.py](backend/tests/architecture/test_wire_conventions.py).

## Frontend architecture & conventions

`frontend/src/` — Vue 3 Composition API + Pinia. Single view (`views/MainView.vue`); `router/index.js` exists only to set the document title on one route.

- **`components/`** — one dir per feature/source (`<Name>Source.vue` as entry point) + `ui/`. **Two shared players, split by coupling, not chronology** — don't merge them; pick by *does the source have an in-app browser?*
  - `audio/AudioPlayerFull.vue` (~470 l.) — **store-coupled** full-screen takeover: reads `unifiedAudioStore.systemState.metadata` and sends its own commands. For sources with nothing to browse in Milō: Spotify, CD (`showControls=true`, default), Tidal (`showControls=true` + `:seekable="false"` — its protocol has transport but no seek), AirPlay/DLNA/Qobuz (`showControls=false`, receiver-driven).
  - `audio/AudioPlayer.vue` (~1400 l.) — **props-down / events-up** (`:title`/`:artwork`/`:isPlaying`, `@toggle-play`/`@swipe-*`), knows no store and no command name: a teleported mobile mini-player + expandable sheet coexisting with a list view. For the three sources with a browser: Radio, Podcast, Music Library.
  - Which one mounts is decided in exactly one place — `useRichDisplay()`'s `richSource`, read by both `AudioSourceView.vue` and `MainView.vue` so the two can't drift.
- **`stores/`** — `unifiedAudioStore.js` is the central audio mirror; the rest are per-feature. **`composables/`**, **`services/`** (`websocket.js`, `apiCall.js`, `logger.js`, `i18n.js`), **`schemas/`** (Zod), **`locales/`** (8 langs, `english.json` canonical/fallback — every key must exist there first), **`constants/`**, **`directives/`**.

Rules:
- **HTTP:** every request goes through `apiCall.{get,post,put,patch,delete}(url, {category, message, ...})`, or `apiCall(category, message, fn, options)` for atomic multi-request sequences. **No `import axios` outside `services/apiCall.js`.** Helpers return `{ok, data, error}`; they support `errorRef`, `signal` (AbortController), `checkStatus`, `logLevel`.
- **Logging:** `logger.{debug,info,warn,error}(category, message, data)` from `@/services/logger`. No `console.*` outside the eslint allowlist — `services/logger.js` (it *is* the logger), `main.js` (Vue `errorHandler`) and `schemas/api.js` (dev-only Zod warnings).
- **WS events:** subscribe in `App.vue` **only** and dispatch into Pinia stores — components react to store state, never to raw events (`useWebSocket()`'s `onReconnect`/`onVisibilityChange` are lifecycle callbacks, not event handling, and stay allowed in a component). Store state maintained by WS *deltas* MUST also be refetched in `App.vue::resyncStores()` — deltas missed while a tab was backgrounded are never replayed. Enforced with `wsEventRegistry` ↔ `parsedOn()` agreement by `tests/architecture/{wsWiring,resyncStores}.test.js`.
- **i18n:** `const { t } = useI18n()` in `<script setup>`, not the global `$t()`.
- **CSS:** use design tokens (`var(--color-*|--space-*|--radius-*)`); stylelint blocks hex literals and `rgba()/hsla()` in scoped `.vue` styles. Missing token → extend `assets/styles/design-system.css`. **No inline `// stylelint-disable`** — extend the design system, or whitelist the file in `.stylelintrc.cjs` with a one-line reason naming *which* rule it buys.
- **Typography:** apply via utility classes (`heading-1…4`, `text-body`, `text-mono`, `text-mono-small`, `display-1`); never redeclare `font-*`/`line-height`/`letter-spacing` in scoped CSS. Scoped CSS = layout, color, component spacing only.
- **Timers:** every `setTimeout`/`setInterval` in a component/composable/view/directive goes through `useTimer()` (auto-cleanup on unmount); bare globals are blocked by `no-restricted-globals`. Raw `window.set*` only in the timer-primitive layer (`useTimer`/`useDebounce`/`useVolumeThrottle`) and `directives/press.js`.
- **Constants:** structural constants used by 2+ modules live in `src/constants/`; promote on the 2nd consumer. Backend-derived values (speeds, codecs, presets) are fetched at runtime + cached, never hardcoded on both sides.
- **Code style:** `.js` files use semicolons; constants files use camelCase names.

## Persistence & the schema-version protocol

Persistent data lives in `/var/lib/milo/` (settings, hardware, radio/podcast/cd/music-library data + image caches, equalizer, `routing.env`/`mac.env`/`snapclient.env`, `camilladsp/`, `go-librespot/`, `qobuz/`, `navidrome/`, `lyrics/`, `shares/`, `errors.log`, `backups/`). Full inventory with per-file durable/disposable classification: [docs/architecture.md](docs/architecture.md).

**Unified per-client EQ:** one EQ record per client behind `MultiroomEqualizerService.get/set_client_eq(mac)` — the local client's in `equalizer.json`, remote clients' in `settings.json: multiroom.client_equalizer[mac]`; a zone holds no EQ (it derives from its members). One HTTP surface, no exceptions: `GET/PUT/POST /api/equalizer/target/{target}[/…]` with `target ∈ local · <mac> · zone:<id>`. Crossover is zone-only and still goes through it (`PUT /target/zone:<id>/crossover`, 400 on a non-zone target) — a second noun for the same resource is how the grammar rots. Bands carry **tuning only**; per-band pipeline membership belongs to the master toggle (`PUT /equalizer/enabled`, applied last, after the effects it gates), on a satellite exactly as locally.

**Schema bumps — fail loud + reset, never migrate.** Each versioned JSON carries `"schema_version": N`; the owning service declares `SCHEMA_VERSION` and uses `load_versioned_json`/`save_versioned_json` ([backend/shared/persistence.py](backend/shared/persistence.py)). On mismatch they raise `SchemaVersionMismatch`; `initialize_services` logs a banner (exact `rm` + pointer) and `SystemExit(1)`, so systemd loops the banner until the operator deletes the file. Disposable derived caches (`lyrics/`, `cd_data.json`, `cd_covers/`) carry no `schema_version` — wipe them instead.

## Invariants

1. **Privileged exec is centralized, never ad hoc.** systemd + power actions go through `SystemdServiceManager` (which shells `sudo systemctl …`, incl. `power()` for reboot/poweroff and `restart_self()`); privileged file work goes through the pinned sudoers helpers under `/usr/local/bin/milo-*`: `milo-deploy-update`, `milo-apply-hardware`, `milo-set-wifi-country`, `milo-mount`/`milo-umount`, `milo-apply-ir-keymap`. **No bare `sudo` anywhere else.** Permissions come from two `NOPASSWD` policy files for the `milo` user — `/etc/sudoers.d/milo-backend` and `/etc/sudoers.d/milo-ir-remote` — both authored **once** under [rootfs/etc/sudoers.d/](rootfs/etc/sudoers.d/) and copied verbatim by `install/system.sh` + `install/ir-remote.sh` *and* by `pi-gen/`. Never restate a policy inline in an installer — that is how a script-installed unit and a flashed image come to grant different sets, silently. The satellite's `/etc/sudoers.d/milo-client` follows the same rule from `milo-client/rootfs/`, and its grants are **argument-scoped** (`systemctl stop <unit>`), so a verb or unit name that moves alone is a real permission denial. Both directions — every `sudo` the code issues is granted, every grant still has a caller, every granted helper is shipped by the same tree — are enforced offline by [backend/tests/contracts/test_privileged_exec_contract.py](backend/tests/contracts/test_privileged_exec_contract.py). PolicyKit covers only NetworkManager. Several other `milo-*` scripts run directly as root from their own systemd unit and need no sudoers entry (`milo-first-boot`, `milo-wait-ready.sh`, `milo-ir-keytable-setup`, `milo-apply-avahi-iface`, `milo-navidrome-provision`, `milo-mdns-probe`, `milo-brightness-7`). `milo-alsa-passthrough` and `milo-tidal-connect` need neither: the first only wants the `audio` group for `amixer` and runs from the CamillaDSP unit's `ExecStartPre`, the second is a plain launcher that execs the Tidal daemon as the service user.
2. **A `rootfs/` script may not source a file its own tree omits.** `rootfs/` and `milo-client/rootfs/` are two independent deployment trees — a satellite update ships a tarball containing **only** `milo-client/`, so a file present in the server's tree is worth nothing to it. Enforced by [backend/tests/architecture/test_rootfs_deployment.py](backend/tests/architecture/test_rootfs_deployment.py), which also requires every rootfs file to be tracked by git and twin files not to have drifted.
3. **ALSA only** — no PipeWire/PulseAudio.
4. **Async everywhere** — all file/network/subprocess I/O is async; shared state under `asyncio.Lock()`.
5. **Runs as the `milo` user** — no root.
6. **Local network only** — CORS restricted to `milo.local` + `localhost:5173`.
7. **CamillaDSP is always in the audio path** (for volume control) and is the **only** attenuation stage: the card's own mixer is pinned at unity by `milo-alsa-passthrough`, wired as `ExecStartPre` of *both* CamillaDSP units (server + satellite) so it runs on every boot whatever the card. It discovers the control on the card (`Digital`/`DAC`/…) rather than reading a per-card table — a table is what left DAC boards attenuating. EQ/compressor/loudness toggle via `bypass_effects()`/`restore_effects()`, never via ALSA routing. ALSA device selection via `MILO_MODE=direct|multiroom` (auto-generated `routing.env`).

Systemd: all units live in `system/`. Two propagation directives, chosen deliberately — **source** units use `BindsTo=milo-backend` (stop with it), while `milo-navidrome` uses `PartOf=milo-backend.service`, because `BindsTo=` propagates *stop only* and the always-on catalog engine must follow a backend **restart** too. Don't swap one for the other. The two snapcast units have **no `WantedBy`** at all — their lifecycle is owned solely by `AudioRoutingService._sync_snapcast_state`, which prevents the desync class where snapcast holds `hw:Loopback,0,0` while the backend believes it is in direct mode.

## No legacy / migration code

Milō is a fixed-purpose appliance — there is no legacy fleet to protect. Keep a **single optimized code path**:
- No migration/fallback paths, compatibility shims, or legacy feature flags.
- No `data.get("old") or data.get("new")` chains to absorb old payload/stored shapes — fix the producer to one canonical key. Same on the frontend: no `event.data?.x ?? event.data?.y`.
- No version detection on persisted files — use the fail-loud schema-bump protocol above.
- No `data.setdefault(...)` in a loader to auto-create a missing top-level key — fail loud.
- Remove dead code on sight — but check the Milo-Mac manifest first for routes/WS events.

## Dev-only artifacts vs production bugs

End users run the **pre-built** frontend served by nginx from `dist/` — no Vite dev server, no HMR, no stale tabs, no `localhost:5173`. Classify a bug reported *while developing* before writing code:

- **Dev-only artifact** (stale JS chunk after a rebuild with a tab open → blank page; references to `localhost:5173`/`?t=…`/`[vite]`; stale `localStorage`/SW cache; anything that disappears on hard refresh) → **diagnose and explain, do NOT add code.** Reload guards or version checks would bloat the prod bundle for a scenario no end user hits.
- **Real bug** (reproduces from a clean prod boot or on the Pi kiosk; triggered by appliance actions; server-side trace in `journalctl`/`errors.log`; anything hardware — ALSA/CamillaDSP/ROC/Snapcast/encoder/screen/IR) → **fix it.**

When unsure, ask: *"reproducible from a clean prod boot, or only from dev-session state?"* Full telltale lists: [docs/development.md](docs/development.md).

**CI cannot see the appliance.** Any change touching the audio path, a source, or hardware is not done until the ~10-minute smoke subset of [docs/manual/verification-checklist.md](docs/manual/verification-checklist.md) has been run on a unit against the prod build. State in the commit which set was run, and say so explicitly if it was cut short.

## Tests — what earns one

Both suites are blocking. Neither buys coverage; they buy leverage over the surface CI genuinely owns. The behaviours that matter most here — ALSA, CamillaDSP, snapcast, D-Bus, hardware — **cannot run in CI at all** and are covered on a real unit by the manual checklist.

**Backend** (`backend/tests/`), four kinds earn their keep:
1. **Contract guardrails** — `tests/contracts/`, `tests/architecture/`, `test_ws_events.py`, `test_command_contract.py`. They derive expectations from the typed models and enums, never from hand-written fixtures. The highest-value files in the suite: extend them, don't delete them.
2. **Pure logic** — parsing, merging, curves, maths (`_to_ms`, disc merge, version compare, volume clamp, IR scancode decode). Deterministic, no mocks.
3. **Service behaviour across a mocked boundary** — the mock stands for the **outside world** (CamillaDSP, snapserver, Navidrome, systemd, D-Bus, mpv, HTTP) and the assertion is what the service *did* to it: which call, in which order, under which failure. [backend/tests/test_dlna_source.py](backend/tests/test_dlna_source.py) is the reference — GENA resends full state on every event, so the bridge must emit each field only when it actually changed, and `assert_called_once` is the only way to state that.
4. **Persistence and state transitions** — settings round-trips on `tmp_path`, the `SchemaVersionMismatch` fail-loud path, `AudioStateMachine` transition guards and the drop-during-transition rule.

**Frontend** (`frontend/tests/`), four kinds: **structural guardrails** (`tests/i18n/`, `tests/schemas/`, `tests/architecture/` — mount nothing, and two of them read the *backend* models directly so backend drift surfaces on the frontend build); **store logic** (WS deltas, derived state, guards — drive the real stores through their own handlers, never mock a sibling store); **pure functions** (`tests/pure/`) and composable logic (`tests/composables/` — a host component may be mounted, but nothing about the DOM is asserted); **schema contracts** (`schemas/api.js` are *resilience* schemas with `.catch()` defaults — assert the coercion, not rejection). This UI is refactored often, so **a test that mounts a component and asserts rendered markup or CSS classes is not written here.**

**The rule that decides the rest: never assert on a value the test itself wrote.** A test that builds a dict, hands it to a passthrough and checks the keys it just typed cannot fail; a test that re-implements the production expression in its own body asserts the language, not Milō. Corollaries:
- **Mock the outside world, never the unit's own internals.** `patch.object(service, "_private")` pins a method name. Mocking a *collaborator's* public API is fine — that is kind 3. On the frontend the same rule reads: HTTP goes through the [frontend/tests/helpers/apiCallMock.js](frontend/tests/helpers/apiCallMock.js) mock, **never mock `axios`** — it is a layer the stores don't own, and mocking it is what rotted the previous suite.
- **Every extractor asserts its own output is non-trivial first.** A broken parse must **fail loudly, not pass on an empty surface**. When adding a guardrail, verify it goes red against a simulated drift before trusting it green.
- **No wall-clock budgets.** Latency measured on an all-mock path measures the mock. Timing belongs in the manual checklist.
- **Don't restate a constant.** `assert DEFAULT_X["k"] == 120` only fails when someone changes it on purpose — assert the behaviour that reads it. Argv is worth asserting only when argv *is* the contract (`sudo systemctl restart --no-block …` is pinned by sudoers, so `test_systemd.py` asserting it is right).
- **Don't re-check what a guardrail already proves**, and assert an endpoint only where the store *chooses* it (target/zone resolution, local-vs-MAC) — not on straight pass-throughs.
- **No coverage threshold in CI.** Coverage as a target rewards exactly the tests these rules exclude.

Docstrings say what breaks when the test fails and name the consumer — no story/AC/ticket references, there is nothing in the repo to resolve them against.

## Lint floor

CI ([.github/workflows/lint.yml](.github/workflows/lint.yml)) blocks merge on: `ruff check backend/ milo-client/`, `pytest backend/`, `pytest milo-client/`, `npm run lint:js`, `npm run lint:css`, `npm run test:run`.

**Both halves of the appliance are gated.** `milo-client/` sits in the same job as the backend, not a separate one — the two ship in the same commit, so a satellite regression is a regression of this commit. It was excluded from ruff and never tested until 2026-08-13; the excluding is what let a stale test and an `except: pass` sit red in the tree.

- **eslint:** `no-restricted-imports` (axios outside `apiCall.js`), `no-restricted-syntax` (`console.*`), `no-restricted-globals` (bare timers).
- **ruff:** `F` (pyflakes — undefined name, redefinition, unused import/variable) + `S110`/`S112`. `F` is selected as a **family**, not rule by rule: `F401` alone is its least valuable member, and enabling it while `F821`/`F811`/`F841` stay off is what kept individual unused imports coming back as findings. `E4`/`E7`/`E9` stay out on purpose — the `E402`s (imports after code in `api/models.py`, `main.py`) and the `E731` lambdas in tests are deliberate, and they are style rather than faults. `BLE001` stays out too: several hundred broad catches here are legitimately logged-and-handled (best-effort hardware, route translators, background-loop bodies), and the layered doctrine above already governs them — activating it would force a mass refactor for no safety gain. One `per-file-ignores` entry: `F821` in `sources/bluetooth/agent.py`, where `dbus-next` declares D-Bus signatures as string annotations (`'o'`, `'s'`, `'u'`) that read as unresolvable forward references.
- **stylelint:** `color-no-hex`, no `rgba|hsla` on color properties, no typography redefinition in scoped CSS.

Bypass a rule only with a per-line directive + reason (`# noqa: S110 -- <why>`, `// eslint-disable-next-line <rule> -- <why>`); no file-level or repo-level disables. History + deferred items (TypeScript, pyright strict, husky): [docs/development.md](docs/development.md).

## Language

**Everything that lands in the repo is in English** — code, identifiers, comments, docstrings, commit messages, docs, test names, log messages. The only French in the repo is user-facing i18n content under `frontend/src/locales/`.

**Conversation with the user follows the user's language** (French). Not a contradiction: the rule above is about artefacts, this one about the exchange.

## Git

Never create a branch unless explicitly asked — commit to the current branch, `main` included. This overrides the default "branch first on the default branch" behaviour.

## Reference docs

- [docs/architecture.md](docs/architecture.md) — technologies per source, ALSA routing + loopback layout, persistence inventory, systemd, security.
- [docs/development.md](docs/development.md) — setup, adding a source (full checklist), testing, debugging, lint history, dev-vs-prod detail.
- [docs/api-overview.md](docs/api-overview.md) — REST + WS surface at a glance.
- [tools/codemap/CLAUDE.md](tools/codemap/CLAUDE.md) — the call-graph tool's own rules (exact-by-construction, never guess an edge).
