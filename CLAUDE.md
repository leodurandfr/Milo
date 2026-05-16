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

- `core/` — Core infrastructure. Key files: `state.py` (AudioStateMachine — single source of truth), `audio_source.py` (AudioSourceProtocol + BaseAudioSource), `settings.py` (SettingsService), `systemd.py` (SystemdServiceManager). Subpackages: `models/`, `volume/`, `equalizer/`, `multiroom/`, `connectivity/`, `network/`, `system/`, `updates/`.
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

All audio sources must implement `AudioSourceProtocol`:

```python
class AudioSourceProtocol(Protocol):
    async def initialize(self) -> bool
    async def start(self) -> bool
    async def stop(self) -> bool
    async def get_status(self) -> Dict[str, Any]
    async def handle_command(self, command: str, data: Dict) -> Dict[str, Any]
```

`BaseAudioSource` in `backend/core/audio_source.py` provides common functionality (state management, systemd control, logging).

**Uniform source structure** — Every source in `backend/sources/{source}/` must follow:
- `__init__.py` — Docstring + `__all__` exporting `Source`, `router`, `setup_{source}_routes`
- `source.py` — Constructor takes `(config, state_machine, settings_service, systemd_manager)`
- `routes.py` — `logger = logging.getLogger(__name__)` at module level; `logger.error()` before every `raise HTTPException` in except blocks; use `run_source_command()` for playback routes

When you need a reference implementation, read the existing sources rather than relying on summaries — they evolve. `radio/` and `podcast/` are the most feature-rich; `airplay/` shows external-process + named-pipe integration; `spotify/` is the minimal shape.

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

**Pydantic models**: All models use `snake_case` field names. Shared models live in `backend/api/models.py`. Source-specific models live in `backend/sources/{source}/models.py`.

### 8. Frontend Conventions

**API calls**: Use `apiCall()` from `frontend/src/services/apiCall.js` for all store API actions (wraps try/catch with logging).

**i18n**: Use `const { t } = useI18n()` in `<script setup>`, not the global `$t()`.

**CSS**: Use design tokens (`var(--color-*)`, `var(--space-*)`, `var(--radius-*)`) instead of hardcoded values.

**Typography**: Apply text styles via the design-system utility classes (`heading-1`…`heading-4`, `text-body`, `text-mono`, `text-mono-small`, `display-1`) defined in `frontend/src/assets/styles/design-system.css`. Do NOT redeclare `font-family`, `font-size`, `line-height`, `letter-spacing`, or `font-weight` in scoped component CSS to mimic an existing style — compose the utility class on the element instead (e.g. `class="my-block__title heading-3"`). Scoped CSS should only handle layout, color, and component-specific spacing.

**Code style**: All `.js` files use semicolons. Constants files use camelCase naming (`audioPlayer.js`, `musicGenres.js`).

**WS event handling**: WebSocket events should be handled in Pinia stores, not in Vue components directly. Components react to store state changes.

## Adding New Features

### Adding a New Audio Source

1. **Define enum** in `backend/core/models/audio_state.py::AudioSource`
2. **Create source module** in `backend/sources/{source}/` following the uniform source structure (see *Audio Source Architecture* above)
3. **Register in dependencies** (`backend/dependencies.py::_create_service()`)
4. **Add ALSA devices** in `/etc/asound.conf` with 2 variants (direct via CamillaDSP, multiroom via Snapcast)
5. **Register source** in `backend/dependencies.py::initialize_services()`
6. **Register routes** in `backend/main.py`
7. **Create Vue component** in `frontend/src/components/{source}/`
8. **Update stores** if needed in `frontend/src/stores/` (use `apiCall()` for API actions, handle WS events in store)

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

All persistent data in `/var/lib/milo/`:

- `settings.json` - Central settings (language, volume, screen, routing, dock)
- `hardware.json` - Hardware configuration (screen type/resolution)
- `last_volume.json` - Last volume for restoration
- `radio_data.json` - Radio favorites and custom stations
- `radio_images/` - Uploaded station images
- `podcast_data.json` - Podcast subscriptions, favorites, playback progress, and user preferences
- `cd_data.json` - CD disc metadata cache (TOC, MusicBrainz lookups)
- `cd_covers/` - CD cover art cache
- `equalizer.json` - Equalizer state: filters, active preset, custom gains, compressor, loudness, mono (atomic writes, debounced)
- `client_equalizer.json` - Per-client equalizer state for remote multiroom clients (keyed by `mac_id`)
- `pending_clients.json` - Multiroom clients awaiting approval
- `routing.env` - ALSA routing environment variables (auto-generated)
- `mac.env` - ROC receiver env vars consumed by `milo-mac` (auto-generated)
- `snapclient.env` - Snapclient env vars consumed by `milo-snapclient-multiroom` (auto-generated)
- `app-version` - Installed app version marker (written at install time)
- `shairport-sync-version` - Shairport-sync version marker (written by update flow)
- `avahi-interface` - Active network interface for mDNS (written by NetworkManager dispatcher)
- `camilladsp/` - CamillaDSP config directory (`config.yml`, `configs/`, `coeffs/`)
- `go-librespot/` - go-librespot config + runtime state (`config.yml`, `state.json`, `lockfile`)
- `errors.log` - Persisted backend + frontend errors
- `backups/` - Binary backups during updates

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
  - No `if old_key in data` / `data.get("legacy_field") or data.get("new_field")` branches to absorb older stored shapes.
  - No version detection on persisted files (`if version < N: migrate(...)`).
- **Persisted user data is NOT a constraint.** When a schema change is required for files in `/var/lib/milo/` (`radio_data.json`, `podcast_data.json`, `settings.json`, …), implement the **new shape directly**. It is acceptable to require the user to delete the affected file(s) and start fresh — call this out in the commit message. Do NOT write auto-migration helpers, key-renaming loops, or "if missing field, fall back to cache" branches.
- **Remove dead code on sight** while refactoring: unused functions, dead routes, orphaned helpers, abandoned data-layer methods that no route calls anymore. Don't leave them "in case someone wires them up later" — they will rot.
- Always prefer:
  - A **single, optimized code path** over multiple conditional branches for legacy behavior.
  - Clear, simple refactors over incremental "layer on top of legacy" patches.

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
