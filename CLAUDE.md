# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Milō is a multiroom audio system for Raspberry Pi that supports Spotify Connect, AirPlay 2, Bluetooth, Mac streaming (ROC), Internet Radio, Podcasts, and CD. Built with FastAPI (Python) backend and Vue 3 frontend, using ALSA for audio without Pipewire/PulseAudio.

## Common Development Commands

### Backend (FastAPI + Python)

```bash
# Setup virtual environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run backend in development
cd backend
python main.py
# Backend runs on http://0.0.0.0:8000

# Run tests
cd backend
python -m pytest                 # All tests
python -m pytest -v              # Verbose
python -m pytest -k "test_name"  # Specific test
```

### Frontend (Vue 3 + Vite)

```bash
# Install dependencies
cd frontend
npm install

# Run frontend in development
npm run dev
# Frontend runs on http://0.0.0.0:5173
# Vite proxies API requests to backend (see vite.config.js)

# Build for production
npm run build
# Output: frontend/dist/
```

### Systemd Service Management

```bash
# View backend logs
sudo journalctl -u milo-backend -f

# Restart services after code changes
sudo systemctl restart milo-backend

# Check service status
sudo systemctl status milo-backend
```

## Project Structure

Top-level layout — run `ls <dir>` to see current contents, do not maintain file enumerations here.

- `backend/` — FastAPI backend (Python). See **Backend Architecture** below.
- `frontend/` — Vue 3 frontend.
- `system/` — Systemd unit files installed to `/etc/systemd/system/`.
- `install.sh` + `install/` — Main installer (orchestrator) and modular per-component install scripts.
- `rootfs/` — Files copied verbatim to the target filesystem at install time (mirrors target paths under `etc/`, `usr/`, `var/`).
- `milo-client/` — Satellite client for multiroom (own installer, app, configs, rootfs, system units).
- `scripts/` — Repo-level helper / test scripts.
- `docs/` — Documentation.

## Backend Architecture

Source-based architecture under `backend/`:

- `core/` — Core infrastructure. Key files: `state.py` (AudioStateMachine — single source of truth), `audio_source.py` (BaseAudioSource abstract base class), `settings.py` (SettingsService), `systemd.py` (SystemdServiceManager). Subpackages: `models/`, `volume/`, `equalizer/`, `multiroom/`, `connectivity/`, `network/`, `system/`, `updates/`.
- `sources/` — Audio source implementations (one subpackage per source: `spotify/`, `airplay/`, `mac/`, `bluetooth/`, `radio/`, `podcast/`, `cd/`).
- `api/` — REST API routes + shared Pydantic models (`models.py`) + route helpers (`route_helpers.py`).
- `hardware/` — Hardware controllers (rotary encoder, IR remote, BT remote, screen).
- `ws/` — WebSocket server + manager.
- `shared/` — Shared utilities (e.g. `MpvController`).
- `config/constants.py` — Centralized constants.
- `dependencies.py` — Service Registry (lazy singletons).

**Key architectural principles:**
- **Single Source of Truth**: `AudioStateMachine` manages all audio state.
- **Source-Based**: Each audio source is a self-contained module under `sources/`.
- **Service Registry**: Simple dict-based DI with lazy singleton creation.
- **Async-first**: asyncio everywhere for non-blocking I/O.
- **WebSocket broadcasting**: State changes broadcast via `state_machine.broadcast_event()`.
- **D-Bus via `dbus-next`**: Prefer event-driven D-Bus subscriptions over polling whenever the relevant Linux service publishes events. Currently used for `org.bluez.AgentManager1` (Bluetooth auto-pairing, [backend/sources/bluetooth/agent.py](backend/sources/bluetooth/agent.py)) and `org.freedesktop.NetworkManager` (connectivity, [backend/core/connectivity/service.py](backend/core/connectivity/service.py)). Always fail open (default to a safe state) when D-Bus is unavailable, so the backend still runs in dev environments without the underlying service.

## Frontend Architecture

Vue 3 + Composition API + Pinia under `frontend/src/`:

- `components/` — One subdirectory per feature/source (`audio/`, `airplay/`, `spotify/`, `radio/`, `podcasts/`, `cd/`, `equalizer/`, `multiroom/`, `network/`, `settings/`, `setup/`, `system/`, `ui/`). The shared full-screen player lives in `components/audio/AudioPlayerFull.vue` and is reused by Spotify (`showControls=true`) and AirPlay (`showControls=false`, no remote control).
- `composables/` — Vue composables (run `ls frontend/src/composables/` to see current set).
- `stores/` — Pinia stores. `unifiedAudioStore.js` is the central audio state mirror; other stores are per-feature (`settings`, `equalizer`, `multiroom`, `snapcast`, `podcast`, `radio`, `cd`, `system`, `discovery`).
- `services/` — WebSocket client, `apiCall()` wrapper, i18n.
- `locales/` — i18n translations (`en.json`, `fr.json`).
- `views/` — `MainView.vue` (single-page app) + dev-only style guides.

**State synchronization**: Backend state changes → WebSocket event → Pinia store update → reactive UI update.

## Critical Implementation Details

### 1. Service Initialization Order (CRITICAL)

The order in `backend/dependencies.py::initialize_services()` is **CRITICAL** due to circular dependencies:

1. **Retrieve instances** (triggers lazy creation via `get_service()`)
2. **Resolve circular dependencies** via setters:
   - `routing_service.set_source_callback()` → allows access to state_machine sources
   - `routing_service.set_snapcast_websocket_service()` → enables lifecycle control
   - `routing_service.set_state_machine()` → enables event broadcasting
   - `state_machine.routing_service = routing_service` → circular reference completion
3. **Register sources** in state_machine (BEFORE async init)
4. **Parallel async initialization** via `asyncio.gather()`

**Do NOT modify this order without understanding the circular dependencies documented in dependencies.py.**

### 2. Audio Source Architecture

All audio sources extend `BaseAudioSource(ABC)` ([backend/core/audio_source.py](backend/core/audio_source.py)):
- Public API: `start() / stop() / restart() / status() / command()`
- Hooks to override in subclasses: `_do_start() / _do_stop() / _get_status() / _handle_command()`
- `BaseAudioSource.__init__` instantiates `self._logger = logging.getLogger(f"source.{source_id}")` — sub-modules of a source must extend this hierarchy (see *Logger* rule below).

There is **no `AudioSourceProtocol`** — any mention in a docstring is a drift to fix.

**Three families of sources** — pick a source's family from two questions: *(1) is playback control sent from Milō's UI, or from an external sender?* and *(2) does the source expose rich metadata (artwork, title, artist)?*

| Family | Sources | Backend layout (`backend/sources/{source}/`) | Frontend layout (`frontend/src/components/{source}/`) |
|---|---|---|---|
| **A. Mute receiver** — external control, no rich metadata | Bluetooth, Mac | `source.py` (+ internal helpers like `agent.py`, `monitor.py`). **No `routes.py`** — commands flow through the generic `/api/audio/control/{source}`. `__all__ = ["{Name}Source"]`. | No dedicated component — rendered via `AudioSourceStatus` (icon + device name). |
| **B. Passive player** — external control, rich metadata displayed | AirPlay | `source.py` + minimal `routes.py` for what the sender protocol can't deliver (e.g. binary artwork) + metadata reader if needed (`metadata_reader.py`). `__all__` exposes `{Name}Source, router, setup_{source}_routes`. | Dedicated Vue component wrapping `<AudioPlayerFull source="..." :showControls="false" />`. |
| **C. Active player** — control from Milō's UI, rich metadata | Spotify, Radio, Podcast, CD | `source.py` + dedicated networking layer as needed: `websocket.py` (Spotify), full `routes.py` + `data.py` + external API (Radio, Podcast, CD), `models.py` Pydantic. `__all__` follows the same shape as family B when a `router` exists. | Dedicated Vue component wrapping `<AudioPlayerFull>` with controls enabled; additional custom UI when relevant (CD tracklist, podcast queue, radio favorites). |

**Rules shared by all three families**:
- **No `GET /<source>/status` route** — status is exclusively broadcast over WebSocket via `state_machine.broadcast_event()`.
- **No `POST /<source>/restart` route** — restart is an admin/systemd concern, not exposed to the UI.
- **Logger names**: at module level in routes, `logger = logging.getLogger(__name__)`. For sub-modules of a source (`websocket.py`, `agent.py`, `monitor.py`, `metadata_reader.py`, `reader.py`, `data.py`, `shazam.py`, …), use `logging.getLogger(f"source.{source_id}.<sub>")` so they hang under the hierarchy created by `BaseAudioSource.__init__`. The legacy `feature.*` namespace is retired.
- **Routes (families B and C)**: use `run_source_command()` for playback routes and call `logger.error(...)` before every `raise HTTPException` in `except` blocks.

When you need a reference implementation, read the existing sources rather than relying on summaries — they evolve. `radio/` and `podcast/` are the richest family-C examples; `airplay/` is the family-B reference (external-process + named-pipe + binary artwork); `spotify/` is a family-C source without `routes.py` (commands flow through the generic endpoint, all UI state via WebSocket); `mac/` and `bluetooth/` are the family-A references.

### 3. State Management Flow

**Always use state_machine methods, never modify state directly:**

```python
# ✅ Correct
await state_machine.update_source_state(source, SourceState.ACTIVE, metadata)

# ❌ Wrong - bypasses locks and broadcasting
state_machine._state.active_source = source
```

**State transitions are protected** by `_transition_lock`. During transitions, state updates are buffered and replayed after.

### 4. WebSocket Broadcasting

All state changes must be broadcast via `state_machine.broadcast_event()`:

```python
await self.state_machine.broadcast_event(
    category="source",           # source, system, routing, equalizer, settings, multiroom, programs
    type="state_changed",
    data={"source": self.source.value, "metadata": {...}}
)
```

**Wire format**: `{ category, type, origin, data, timestamp }`. The `origin` field is read from `data["source"]` (falls back to `category`). Callers using category `"source"` **must** provide `"source"` in the data dict.

**Payload contracts** — every WS event payload consumed by the frontend SHOULD have a Zod schema declared in [frontend/src/schemas/ws.js](frontend/src/schemas/ws.js). Handlers consume the validated payload via `parsedOn(category, type, schema, handler)` exposed by `useWebSocket()` — they MUST NOT read `event.data.x` directly. On the backend side, document the expected shape in a docstring next to each `broadcast_event(...)` call site; the schema and producer are kept in sync by code review (no codegen).

The registry is intentionally incremental: only fautive pairs (those that previously absorbed dual-shape drift via `??`/`||` fallbacks) are schematized initially. Other pairs continue to dispatch raw `event` via `on(...)` until a future PR migrates them.

**Event category conventions:**
- `source` — All audio source feature events (state changes, metadata). Never use source-specific categories.
- `settings` — Settings changes. Always via `state_machine.broadcast_event()`, never `ws_manager.broadcast_dict()`.
- `routing` — Multiroom routing transitions (`multiroom_enabling`, `multiroom_disabling`, `multiroom_ready`)
- `equalizer` — EQ filter/preset/compressor/loudness changes and `enabled_changed`
- `multiroom` — Client/zone registry changes and `equalizer_changed` for zone EQ
- `system` — System-level state changes
- `volume` — Volume state changes
- `programs` — Update progress and completion events

### 5. Settings Persistence

**All settings modifications must go through SettingsService:**

```python
# ✅ Correct
await settings_service.set_setting('volume.alsa_max', 80)

# ❌ Wrong - not persisted to disk
settings['volume']['alsa_max'] = 80
```

Settings are stored in `/var/lib/milo/settings.json` with atomic writes (`os.replace()`), file locks, and automatic backups on corruption.

### 6. ALSA Dynamic Routing

Each audio source has two ALSA device variants selected via environment variables:

```
milo_{source}_direct          # Direct via CamillaDSP to amplifier
milo_{source}_multiroom       # To Snapcast loopback (DSP applied on each client)
```

Selection controlled by `MILO_MODE=direct` or `multiroom`, auto-generated in `/var/lib/milo/routing.env` from settings.json.

CamillaDSP is ALWAYS in the audio path for volume control. DSP effects (EQ, compressor, loudness) are toggled via `bypass_effects()` / `restore_effects()` in CamillaDSP, not via ALSA routing.

### 7. API Conventions

**Response format**: All API responses use `"status": "success"` for success. Use `"status": "error"` for errors returned as HTTP 200 (resilience pattern for /status endpoints). For actual errors, raise `HTTPException`.

**REST verbs**:
- `PUT` for idempotent updates (settings, routing config)
- `DELETE` with path params for removals (e.g., `DELETE /radio/favorites/{station_id}`)
- `POST` for actions (play, stop, connect) and resource creation
- `PATCH` for partial updates (volume, zone, client properties)

**Route helpers** (`backend/api/route_helpers.py`):
- `run_source_command(source, cmd, data, context)` — Standard wrapper for `source.command()` with success check + HTTP 400/500 error handling. All feature playback routes should use this.
- `api_error_handler(context, log)` — Async context manager for the common `try/except HTTPException/Exception` pattern.
- `parse_audio_source(name)` — Parse user-provided source name to `AudioSource` or raise HTTP 400 with explicit `"Unknown audio source: '<name>'"`. Use in route handlers receiving a source name from path/query/body.
- `coerce_audio_source_or_none(name)` — Defensive variant for trusted state values (`state_machine.get_current_state()["active_source"]`); returns `None` for `"none"`/missing/invalid + logs warning on truly invalid input so upstream state corruption is visible without crashing the caller.

**Pydantic models**: All models use `snake_case` field names. Shared models live in `backend/api/models.py`. Source-specific models live in `backend/sources/{source}/models.py`.

**Error handling doctrine** — policy per layer:

| Layer | Policy | Mandatory tool | Anti-pattern |
|---|---|---|---|
| **HTTP route** | Catch via `api_error_handler` or `run_source_command` (for sources). No bare `try/except` in route body. Enum validation via `parse_audio_source(name)`. | `api_error_handler`, `run_source_command`, `parse_audio_source` ([backend/api/route_helpers.py](backend/api/route_helpers.py)) | `try: ... except Exception: raise HTTPException(500, str(e))` copy-pasted per route |
| **Service layer** | **Log + raise.** If a fallback is legitimate, `@handle_errors(default=..., level='error')` so the log is explicit. Never `except Exception: pass` nor silent `except: return None`. | `@handle_errors` ([backend/shared/decorators.py](backend/shared/decorators.py)) | `try: ... except: return False` without log |
| **Background task / loop** | Wrap the **loop body** in `try/except Exception` + `error` log + `continue`. `except CancelledError` alone is NEVER enough — transient I/O errors will silently kill the task. | Explicit pattern in the coroutine body (see [backend/sources/podcast/source.py](backend/sources/podcast/source.py) `_progress_save_loop`) | `while True: ... except CancelledError: ...` that dies silently on transient errors |
| **Best-effort hardware / external API** | Legitimate catch but **explicit and scoped**: `except SpecificError as e: self._logger.warning(...)` with named fallback. Use `warning` (not `debug` which is invisible in prod, not `info` which drowns). | `@handle_errors(default=..., level='warning')` | `except Exception: self._logger.debug(...)` masking real failures |

**Examples by layer**: see [backend/api/equalizer.py](backend/api/equalizer.py) (route with `api_error_handler`), [backend/sources/radio/shazam.py](backend/sources/radio/shazam.py) (best-effort warning split by exception), [backend/sources/podcast/source.py](backend/sources/podcast/source.py) `_progress_save_loop` (background loop with body try/except).

### 8. Frontend Conventions

**API calls** — doctrine "where" + "how":

**Where the I/O lives** (criterion: "do >1 components need this data reactively?"):
- **Pinia store** when state is shared reactively across components (audio, multiroom, settings, equalizer).
- **Composable** when the widget cuts across multiple views without owning shared state (network, hardware config).
- **Component** when the fetch is view-local and one-shot (version banner in InfoSettings, podcast detail by UUID).
- **Shared service (`services/`)** only for cross-store infrastructure (i18n, websocket). No feature data.

**How the I/O flows** (non-negotiable):
- Every HTTP request goes through `apiCall.{get,post,put,patch,delete}(url, { category, message, ... })` for single requests, or `apiCall(category, message, fn, options)` for atomic multi-request sequences.
- **No `import axios` outside `frontend/src/services/apiCall.js`.** RFC 22 will enforce this via ESLint `no-restricted-imports`.
- Typed helpers return `{ ok, data, error }`. The helper extracts `error.response?.data?.detail` automatically when an `errorRef` Ref is passed; native support for `AbortController` via `signal`; `checkStatus: true` for the resilience pattern (`response.data.status === 'success'`); `logLevel: 'debug'` for best-effort beacons that should not flood the console on failure.

**Logging**: Use `logger.{debug,info,warn,error}(category, message, data)` from `@/services/logger`. No direct `console.*` for errors/warnings. The only sanctioned `console.*` sites are `services/logger.js` (the logger itself), `main.js` (Vue errorHandler), `schemas/api.js` (dev-only Zod warnings), and `services/modalDebug.js` (opt-in debug toggle). RFC 22 will lock this via ESLint `no-console`.

**i18n**: Use `const { t } = useI18n()` in `<script setup>`, not the global `$t()`.

**CSS**: Use design tokens (`var(--color-*)`, `var(--space-*)`, `var(--radius-*)`) instead of hardcoded values. The `stylelint` of the project blocks hex literals (`#abc`, `#abcdef`) and `rgba()` in scoped styles of `.vue` files. If a token is missing, extend [design-system.css](frontend/src/assets/styles/design-system.css) rather than adding a local exception.

**Typography**: Apply text styles via the design-system utility classes (`heading-1`…`heading-4`, `text-body`, `text-mono`, `text-mono-small`, `display-1`) defined in `frontend/src/assets/styles/design-system.css`. Do NOT redeclare `font-family`, `font-size`, `line-height`, `letter-spacing`, or `font-weight` in scoped component CSS to mimic an existing style — compose the utility class on the element instead (e.g. `class="my-block__title heading-3"`). Scoped CSS should only handle layout, color, and component-specific spacing. The project `stylelint` rejects these typography redefinitions in any scoped `<style>`.

**Code style**: All `.js` files use semicolons. Constants files use camelCase naming (`audioPlayer.js`, `musicGenres.js`).

**Shared constants**: Structural constants used by 2+ modules (stores, composables, components) live in [`frontend/src/constants/`](frontend/src/constants/) (see [README](frontend/src/constants/README.md)). Don't duplicate a literal value across a store AND a component — promote it to the module as soon as a second site consumes it. Constants derivable from the backend (speeds, codecs, presets) are fetched at runtime and cached in the relevant store, not hardcoded on both sides.

**Timers**: Every `setTimeout` / `setInterval` inside a component or composable must go through [`useTimer()`](frontend/src/composables/useTimer.js) for automatic cleanup on unmount. Direct calls to `window.setTimeout` / `window.setInterval` are only allowed inside `useTimer.js` itself.

**WS event handling**: WebSocket events should be handled in Pinia stores, not in Vue components directly. Components react to store state changes.

### 9. Background tasks

Schedule fire-and-forget coroutines through the right primitive for the call site — never via raw `asyncio.create_task` for untracked work.

**Services**: instantiate `self._bg = BackgroundTaskSet(logger, "owner_label")` in `__init__` (see [backend/shared/background.py](backend/shared/background.py)), then `self._bg.spawn(coro, label="...")`. Exceptions are logged at ERROR with `exc_info`; `CancelledError` is silent. Call `await self._bg.cancel_all()` in the service's `cleanup()`.

**HTTP routes**: add a `background_tasks: BackgroundTasks` parameter and call `background_tasks.add_task(callable, *args)` (see [backend/api/system.py](backend/api/system.py) and [backend/api/setup.py](backend/api/setup.py)). FastAPI runs the task after the response is sent and propagates exceptions to uvicorn's logger. Do **not** schedule `asyncio.create_task(_delayed_X())` from a route body.

**Tracked long-running tasks** (`self._monitor_task = asyncio.create_task(self._monitor_loop())`): the direct `asyncio.create_task` is still allowed when the handle is stored on `self` for targeted cancellation. This is the only legitimate use of raw `create_task` in service code.

## Adding New Features

### Adding a New Audio Source

Before writing code, **pick the family** (see *Audio Source Architecture* above). The checklist below is differentiated by family — apply only the steps marked for the family you picked.

**Common steps (all families)** :

1. **Define enum** in `backend/core/models/audio_state.py::AudioSource`
2. **Create the module** in `backend/sources/{source}/` with `__init__.py` + `source.py` extending `BaseAudioSource(ABC)`. Constructor takes `(config, state_machine, settings_service, systemd_manager)`. Implement `_do_start / _do_stop / _get_status / _handle_command`.
3. **Register in dependencies** — add a creator in `backend/dependencies.py::_create_service()` and register the source in `initialize_services()`
4. **Add ALSA devices** in `/etc/asound.conf` with 2 variants (direct via CamillaDSP, multiroom via Snapcast)
5. **Update stores** if needed in `frontend/src/stores/` (use `apiCall.{get,post,put,patch,delete}` for API actions, handle WS events in store)

**Family A — Mute receiver** (external control, no rich metadata) :

- `__init__.py` exports `__all__ = ["{Name}Source"]` only. **Do not create `routes.py`** — commands flow through the generic `/api/audio/control/{source}` endpoint.
- No frontend component — the source is rendered by `AudioSourceStatus` (icon + device name).

**Family B — Passive player** (external control, rich metadata displayed) :

- `__init__.py` exports `__all__ = ["{Name}Source", "router", "setup_{source}_routes"]`. `routes.py` is **minimal** — only the bits the sender protocol doesn't deliver (e.g. binary artwork). Register routes in `backend/main.py`.
- Frontend: dedicated Vue component in `frontend/src/components/{source}/` wrapping `<AudioPlayerFull source="..." :showControls="false" />` (no remote control surface).

**Family C — Active player** (control from Milō's UI, rich metadata) :

- `__init__.py` exposes `{Name}Source` + (if `routes.py` exists) `router` + `setup_{source}_routes`. Build the networking layer you need: `websocket.py` (e.g. Spotify), full `routes.py` + `data.py` + external API client (Radio / Podcast / CD), `models.py` for Pydantic. Register routes in `backend/main.py` when a router exists.
- Frontend: dedicated Vue component wrapping `<AudioPlayerFull>` with controls enabled; add custom UI on top (tracklist, queue, favorites) as relevant.

**Rules to respect — applicable to all families** (see *Audio Source Architecture* for the full doctrine):

- No `GET /<source>/status` route — status flows over WebSocket via `state_machine.broadcast_event()`.
- No `POST /<source>/restart` route — restart is a systemd/admin concern.
- Sub-module loggers must use `logging.getLogger(f"source.{source_id}.<sub>")`. The `feature.*` namespace is retired.
- Routes (families B and C) call `run_source_command()` for playback commands and `logger.error(...)` before every `raise HTTPException`.

### Adding a New Service

1. Create service in `backend/core/{service_name}/`
2. Add creator to `backend/dependencies.py::_create_service()`
3. Inject dependencies via constructor
4. If has async `initialize()`, add to `init_async()` in `initialize_services()`
5. Create API routes in `backend/api/`
6. Update frontend stores/components as needed

## Testing

**Backend (pytest):**
- Use `@pytest.mark.asyncio` for async tests
- Mock dependencies via constructor injection
- See `backend/tests/` for examples

**ALSA routing smoke test (Pi only):**
- `bash scripts/test-alsa-routing.sh` — static checks of the ALSA chain: Loopback subdevice layout in `/etc/asound.conf`, CamillaDSP capture device, and snapserver source devices all match the documented slot map (DSP→0, sources→1..7). Catches subdevice renumbering, alias renames, and dead-PCM accumulation.
- `bash scripts/test-alsa-routing.sh --with-live` — additionally probes each alias with a non-destructive `aplay` open. BUSY (subdevice held by an active writer) is reported as a warning, not a failure.
- `pytest backend/tests/test_alsa_routing.py` — pytest wrapper around the static checks; auto-skips off-Pi.

**Multiroom state-coherence smoke test (Pi only):**
- `bash scripts/test-multiroom-desync.sh` — toggles multiroom 20 times via `PUT /api/routing/multiroom` and asserts after each toggle that `settings.routing.multiroom_enabled`, `routing.env` `MILO_MODE`, and the `milo-snapserver-multiroom` / `milo-snapclient-multiroom` units all agree.
- `sudo bash scripts/test-multiroom-desync.sh --kill-test` — additionally `kill -9`s the backend mid-toggle and asserts the system reconciles to `settings.json` on restart.

**Frontend (Vitest):** not currently configured.

## Data Persistence Locations

All persistent data in `/var/lib/milo/`. Versioned JSON files use the `schema_version` protocol (cf. §"Persisted-data schema bumps" in Development Guidelines).

- `settings.json` - Central settings (language, volume, screen, routing, dock) — `schema_version: 2` (owned by `SettingsService`)
- `hardware.json` - Hardware configuration (screen type/resolution, audio card, rotary encoder, IR remote) — `schema_version: 2` (owned by `HardwareService`)
- `last_volume.json` - Last volume for restoration — *no `schema_version` yet*
- `radio_data.json` - Radio favorites and custom stations — *no `schema_version` yet*
- `radio_images/` - Uploaded station images
- `podcast_data.json` - Podcast subscriptions, playback progress, and per-podcast settings (playback speed) — `schema_version: 1` (owned by `PodcastDataService`)
- `cd_data.json` - CD disc metadata cache (TOC, MusicBrainz lookups) — *no `schema_version` yet*
- `cd_covers/` - CD cover art cache
- `equalizer.json` - Equalizer state: filters, active preset, custom gains, compressor, loudness, mono (atomic writes, debounced) — `schema_version: 2` (owned by `EqualizerService`)
- `client_equalizer.json` - Per-client equalizer state for remote multiroom clients (keyed by `mac_id`) — *no `schema_version` yet*
- `pending_clients.json` - Multiroom clients awaiting approval — *no `schema_version` yet*
- `routing.env` - ALSA routing environment variables (auto-generated, regenerated on every settings change — no `schema_version`)
- `mac.env` - ROC receiver env vars consumed by `milo-mac` (auto-generated, no `schema_version`)
- `snapclient.env` - Snapclient env vars consumed by `milo-snapclient-multiroom` (auto-generated, no `schema_version`)
- `app-version` - Installed app version marker (written at install time)
- `shairport-sync-version` - Shairport-sync version marker (written by update flow)
- `avahi-interface` - Active network interface for mDNS (written by NetworkManager dispatcher)
- `camilladsp/` - CamillaDSP config directory (`config.yml`, `configs/`, `coeffs/`)
- `go-librespot/` - go-librespot config + runtime state (`config.yml`, `state.json`, `lockfile`)
- `errors.log` - Persisted backend + frontend errors
- `backups/` - Binary backups during updates

*Files annotated «no `schema_version` yet» will adopt the protocol on their first breaking schema change — see [BREAKING_CHANGES.md](BREAKING_CHANGES.md).*

## Important Constraints

1. **No sudo in code** - Use `SystemdServiceManager` for service control (PolicyKit handles permissions)
2. **ALSA only** - No Pipewire/PulseAudio (HiFiBerry compatibility)
3. **Async/await everywhere** - All I/O operations must be async
4. **Lock-protected operations** - Use `asyncio.Lock()` for shared state
5. **No root permissions** - Backend runs as `milo` user
6. **Local network only** - CORS restricted to milo.local and localhost:5173

## Systemd Services

All components managed by systemd:

- `milo-backend` - FastAPI backend
- `milo-spotify` - Spotify Connect (go-librespot)
- `milo-airplay` - AirPlay 2 (shairport-sync)
- `milo-bluealsa` + `milo-bluealsa-aplay` - Bluetooth
- `milo-mac` - Mac streaming (ROC receiver)
- `milo-radio` - mpv radio player
- `milo-podcast` - mpv podcast player (separate from radio)
- `milo-cd` - CD player
- `milo-camilladsp` - CamillaDSP audio processing
- `milo-snapserver-multiroom` + `milo-snapclient-multiroom` - Multiroom audio (no `WantedBy` — lifecycle owned exclusively by `AudioRoutingService._sync_snapcast_state`, gated on `settings.routing.multiroom_enabled`)
- `milo-ir-keytable` - Boot-time oneshot: enables NEC decoding on the rc-core device and reloads the paired Apple Remote keymap (TSOP4838 → GPIO17 → `gpio-ir` overlay → rc-core → evdev).
- `milo-kiosk` - Chromium kiosk mode for touchscreen
- `milo-disable-wifi-power-management` - WiFi power management optimization
- `milo-readiness` - System readiness check
- `milo-first-boot` - First-boot setup oneshot

**All sources `BindsTo=milo-backend`** - they stop if backend stops.

## Debugging Tips

**Backend:**
- Live logs: `sudo journalctl -u milo-backend -f`
- Debug mode: Set `logging.basicConfig(level=logging.DEBUG)` in main.py
- Breakpoints: Use `import pdb; pdb.set_trace()`

**Frontend:**
- Vue DevTools browser extension
- WebSocket state: Check browser console during development
- Vite HMR: Changes hot-reload automatically

## Common Pitfalls

The conventions above are the rules; these are the most common ways they're violated. When in doubt, re-read the relevant section.

1. **Don't bypass `state_machine`** — always use `update_source_state()` and `broadcast_event()`, never touch internals.
2. **Don't bypass `SettingsService`** — direct JSON file edits won't persist correctly.
3. **Don't use blocking I/O** — async/await for all file, network, subprocess operations.
4. **Don't use `ws_manager.broadcast_dict()` directly** — use `state_machine.broadcast_event()`.
5. **Don't use camelCase in Pydantic models** — all fields must be `snake_case`.
6. **Don't handle WS events in Vue components** — handle them in Pinia stores; components react to store state.
7. **Don't use `POST` for idempotent updates** — `PUT` for settings, `DELETE` for removals, `PATCH` for partial updates.
8. **Don't hardcode ALSA devices** — use the env-var pattern for multiroom/equalizer switching.
9. **Don't use `asyncio.create_task()` for fire-and-forget** — use `BackgroundTaskSet.spawn()` (services) or FastAPI `BackgroundTasks` (routes). Direct `create_task` is reserved for long-running tracked tasks stored on `self` (e.g. `self._monitor_task = asyncio.create_task(...)`).
10. **Don't write migration code on persisted data.** Bump `SCHEMA_VERSION`, add an entry to [BREAKING_CHANGES.md](BREAKING_CHANGES.md), let the file reset on first boot via `SchemaVersionMismatch`. Migration code is the path that grows legacy debt — avoid it even when it looks like a 3-line if-block.
11. **Don't use `dict.get(k1, dict.get(k2, default))` chain fallbacks to absorb old payload shapes.** Fix the producer instead (one canonical key). Chain fallbacks rot — they keep absorbing old shapes long after no one emits them.
12. **Don't `import axios` outside `frontend/src/services/apiCall.js`.** Use `apiCall.{get,post,put,patch,delete}(url, { category, message, ... })` for all HTTP requests, or the callback form `apiCall(cat, msg, async () => { ... })` for atomic multi-request sequences. Direct `axios` imports bypass centralized logging, the resilience-pattern check (`response.data.status === 'success'`), and `AbortController` / `errorRef` plumbing.
13. **Don't use `console.*` for errors or warnings outside the documented allowlist** (logger.js, main.js, schemas/api.js, modalDebug.js) — use `logger.{debug,info,warn,error}(category, message, data)`. `console.*` skips the category prefix and timestamp formatting, so the central log view can't filter or correlate.
14. **Don't `except Exception: pass`** — use `@handle_errors(default=..., level=...)` if a fallback is legitimate, otherwise `log + raise`. Silent swallows hide production breakage and force every future debugging session to start by re-adding the log statement.
15. **Don't access `_private` attributes/methods of another service from a route or another service.** If you need the data, the service must expose a public method or property. Encapsulation breaks here become load-bearing fast — the next refactor across the boundary breaks every caller silently.
16. **Don't catch only `CancelledError` in a background loop** — wrap the loop body in `try/except Exception` + log + `continue`. `except CancelledError` alone lets transient I/O errors silently kill the task (e.g. disk-full, lock contention) so the task is gone until the next backend restart.
17. **Don't read `event.data.x` directly in WS handlers.** Declare a Zod schema in [frontend/src/schemas/ws.js](frontend/src/schemas/ws.js) and consume the validated payload via `parsedOn(category, type, schema, handler)`. Raw access bypasses validation and silently absorbs payload drift.
18. **Don't use `event.data?.x ?? event.data?.y` fallbacks to absorb dual payload shapes.** Fix the producer side first (one canonical key in the broadcast), then declare a single schema. Dual-shape fallbacks rot — they outlive the producer that justified them.

## Development & Coding Guidelines

When generating or modifying code in this repository, please follow these rules:

### 1. Comments & Documentation Language

- **Always write comments in English**, even if the current conversation or task description is in French.
- This applies to:
  - Inline code comments
  - Docstrings
  - `TODO` / `FIXME` notes
  - Developer-facing documentation inside the codebase
- User-facing text (UI labels, error messages, i18n strings, marketing copy, etc.) can of course be localized and may be in French or other languages when appropriate.

### 2. No Migration / Fallback Code (Keep the codebase OPTIMIZED)

The codebase must stay **clean, optimized and free of legacy / transitional layers**. This is a fixed-purpose appliance — there is no legacy fleet to protect.

When refactoring or adding features:

- **Do NOT introduce or keep migration / fallback code paths** for old implementations.
  - No duplicated "old" and "new" versions of the same logic.
  - No compatibility shims that keep unused APIs alive "just in case".
  - No feature flags whose only purpose is to keep legacy behavior around.
  - No `if old_key in data` / `data.get("legacy_field") or data.get("new_field")` chain fallback branches to absorb older stored shapes.
  - No `data.setdefault(...)` in a loader to auto-create a missing top-level key — prefer fail-loud.
  - No version detection on persisted files (`if version < N: migrate(...)`).
- **Remove dead code on sight** while refactoring: unused functions, dead routes, orphaned helpers, abandoned data-layer methods that no route calls anymore. Don't leave them "in case someone wires them up later" — they will rot.
- Always prefer:
  - A **single, optimized code path** over multiple conditional branches for legacy behavior.
  - Clear, simple refactors over incremental "layer on top of legacy" patches.

#### Persisted-data schema bumps — fail-loud + reset protocol

**Persisted user data is NOT a constraint.** When the on-disk shape of a file under `/var/lib/milo/` changes, do **not** write a migration. Bump the schema version, document the reset, let the file rewrite itself from defaults on the next boot:

1. Every persisted JSON file in `/var/lib/milo/` carries a top-level `"schema_version": N` integer field.
2. The owning service declares `SCHEMA_VERSION: int` as a class constant and loads via [`load_versioned_json(path, SCHEMA_VERSION)`](backend/shared/persistence.py) — save via `save_versioned_json` so the field is stamped on every write.
3. On version mismatch (or missing field) the primitive raises `SchemaVersionMismatch`. [backend/dependencies.py::initialize_services](backend/dependencies.py) catches it during async init, logs a banner to stderr (the exact `rm` command + pointer to `BREAKING_CHANGES.md`) and `SystemExit(1)`. Systemd restarts the unit, the same banner reappears in `journalctl` until the operator deletes the offending file.
4. **When bumping a `SCHEMA_VERSION`** — add an entry to [BREAKING_CHANGES.md](BREAKING_CHANGES.md) at the repo root: file path, version bump, reason, exact `rm` command, impact on user state.

In short: **keep the codebase OPTIM** (simple, efficient, modern). Backward-compatibility baggage — for code OR for persisted data — is never warranted unless the user explicitly asks for it.

### 3. Dev-Only Symptoms vs. Production Bugs

Milō is a **fixed-purpose Pi appliance**. End users:

- Run the **pre-built** frontend served by nginx from `frontend/dist/`.
- Do **not** rebuild, hot-reload, or keep stale tabs open across deploys.
- Do **not** interact with Vite dev server (`npm run dev`), HMR, source-map URLs, or `localhost:5173`.

When the developer reports a bug they hit *while developing*, classify the symptom **before** writing any code:

**A. Dev-only artifact** — diagnose, explain, **do not modify code**.

Telltale signs that the symptom is dev-only:
  - Caused by a rebuild while a browser tab was already open (stale JS bundle, renamed chunks → 404 on dynamic imports → blank page).
  - Errors referencing `localhost:5173`, `192.168.x.x:5173`, `?t=<timestamp>` query strings, or `Vite HMR` / `[vite]` log lines.
  - Stale `sessionStorage` / `localStorage` from a prior dev iteration of the schema.
  - Service worker / PWA cache pollution from experimentation (the prod build has no SW).
  - Anything that disappears with a hard refresh (`⌘ + Shift + R`) or after clearing site data.
  - "Page blanche" / "écran blanc" after `npm run build` while a tab was open — almost always the stale-chunk pattern above.

For these: explain *why* it happened, point to the dev workflow that triggered it, and stop. Do **not** add reload guards, version-check loops, error-handler fallbacks, or any other code whose only purpose is to mask a developer's mid-session inconsistency. That code would bloat the prod bundle for a scenario no end user will ever create.

**B. Real bug that would also hit production** — fix in code.

Telltale signs that the symptom impacts end users:
  - Reproduces from a clean prod state (fresh boot, nginx-served `dist/`, no dev tools open).
  - Triggered by user actions the appliance is built for: connecting AirPlay, selecting a radio station, multi-room handoff, screen sleep, etc.
  - Reproduces on the Pi kiosk itself (which always runs the prod build), not just the developer's Mac browser.
  - Backend logs (`journalctl -u milo-backend`) or `errors.log` show a server-side trace independent of how the user got there.
  - Hardware-related: ALSA routing, CamillaDSP, ROC, Snapcast, rotary encoder, screen brightness — these always count as prod-relevant.

For these: investigate root cause and fix.

**When in doubt, ask explicitly before implementing**: *"Is this reproducible from a clean prod boot, or only because of your dev session state?"* Do not bake mitigation into the prod codebase to silently absorb developer mistakes.


## Reference Documentation

- [Architecture Details](docs/architecture.md) - Deep dive into technologies and audio routing
- [Development Guide](docs/development.md) - Complete developer reference with examples
- [README](README.md) - Installation and hardware requirements
