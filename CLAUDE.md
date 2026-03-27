

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Milō is a multiroom audio system for Raspberry Pi that supports Spotify Connect, AirPlay 2, Bluetooth, Mac streaming (ROC), Internet Radio, and Podcasts. Built with FastAPI (Python) backend and Vue 3 frontend, using ALSA for audio without Pipewire/PulseAudio.

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
sudo systemctl status milo-spotify
sudo systemctl status milo-airplay
sudo systemctl status milo-radio
sudo systemctl status milo-podcast
```

## Project Structure

```
milo/
├── backend/              # FastAPI backend (Python)
├── frontend/             # Vue 3 frontend
├── system/               # Systemd service files (.service)
├── install.sh            # Main installation script
├── install/              # Installation helper scripts (run only during install)
│   ├── airplay.sh
│   ├── boot-common.sh
│   ├── screen-waveshare-7-usb.sh
│   └── screen-waveshare-8-dsi.sh
├── rootfs/               # Files deployed to system (mirrors target filesystem)
│   ├── etc/NetworkManager/dispatcher.d/   # Network event scripts
│   ├── usr/local/bin/                     # System scripts (milo-wait-ready.sh)
│   └── usr/share/plymouth/themes/milo/    # Boot animation theme
├── milo-client/          # Satellite client for multiroom
└── docs/                 # Documentation
```

**Directory conventions:**
- `install/` - Scripts executed only during `install.sh` (screen/boot configuration)
- `rootfs/` - Files copied to the system at install time, run at boot/runtime
- `system/` - Systemd unit files

## Architecture Overview

### Backend: Source-Based Architecture

```
backend/
├── core/                      # Core infrastructure
│   ├── models/               # Domain models (AudioSource, SourceState, SystemAudioState, Volume)
│   ├── state.py              # AudioStateMachine (single source of truth)
│   ├── audio_source.py       # AudioSourceProtocol interface
│   ├── settings.py           # SettingsService
│   ├── systemd.py            # SystemdServiceManager
│   ├── volume/               # Volume service + handlers
│   ├── dsp/                  # CamillaDSP service + proxy + sync
│   ├── multiroom/            # Snapcast + routing + crossover
│   └── updates/              # Update + version services
├── sources/                   # Audio source implementations
│   ├── spotify/              # SpotifySource + routes
│   ├── airplay/              # AirPlaySource + metadata_reader + routes
│   ├── mac/                  # MacSource + routes
│   ├── bluetooth/            # BluetoothSource + routes
│   ├── radio/                # RadioSource + routes + browser_api + genres
│   └── podcast/              # PodcastSource + routes + taddy_api
├── api/                       # REST API routes
│   ├── audio.py, dsp.py, volume.py, settings.py, etc.
│   └── models.py             # Pydantic models
├── hardware/                  # Hardware controllers (rotary, screen)
├── ws/                 # WebSocket server + manager
├── shared/                    # Shared utilities (MpvController)
├── config/constants.py        # Centralized constants
└── dependencies.py            # Service Registry (lazy singletons)
```

**Key architectural principles:**
- **Single Source of Truth**: `AudioStateMachine` manages all audio state
- **Source-Based**: Each audio source is a self-contained source module
- **Service Registry**: Simple dict-based DI with lazy singleton creation
- **Async-first**: asyncio everywhere for non-blocking I/O
- **WebSocket broadcasting**: State changes broadcast via `state_machine.broadcast_event()`

### Frontend: Vue 3 Composition API

```
frontend/src/
├── components/
│   ├── audio/         # Shared audio player (AudioPlayer.vue, AudioPlayerFull.vue, PlaybackControls.vue, ConnectProgressBar.vue, AudioScreensaver.vue, AudioSourceLayout.vue, AudioSourceStatus.vue, AudioSourceView.vue)
│   ├── airplay/       # AirPlay UI (AirPlaySource.vue)
│   ├── podcasts/      # Podcast-specific UI (PodcastSource.vue, HomeView.vue, etc.)
│   ├── radio/         # Radio-specific UI (RadioSource.vue)
│   ├── spotify/       # Spotify UI (SpotifySource.vue)
│   ├── dsp/           # CamillaDSP controls (parametric EQ, compressor, loudness)
│   ├── multiroom/     # Multiroom controls (Snapcast management)
│   ├── navigation/    # Navigation components
│   ├── settings/      # System settings (SettingsModal.vue, SettingsCategory.vue) with nested categories/:
│   │                   #   ApplicationsSettings, UpdateManager, PodcastSettings, MultiroomSettings,
│   │                   #   VolumeSettings, LanguageSettings, ScreenSettings, InfoSettings, SpotifySettings,
│   │                   #   MacSettings, radio/RadioSettings, radio/ManageStation
│   └── ui/            # Reusable components
├── composables/       # Vue composables (useAnimatedHeight, useNavigationStack, etc.)
├── stores/            # Pinia stores (see below)
├── services/          # WebSocket client + i18n
├── locales/           # i18n translations (en.json, fr.json)
└── views/             # Single page app (MainView.vue)
```

**Pinia Stores** (`frontend/src/stores/`):
- `unifiedAudioStore.js` - Central audio state management
- `settingsStore.js` - Settings management
- `equalizerStore.js` - DSP/equalizer state (CamillaDSP)
- `multiroomStore.js` - Multiroom/Snapcast state
- `snapcastStore.js` - Snapcast server configuration
- `podcastStore.js` - Podcast data and playback
- `radioStore.js` - Radio stations and playback

**Vue Composables** (`frontend/src/composables/`):
- `useAnimatedHeight.js` - Animation helper for expandable elements
- `useHardwareConfig.js` - Hardware configuration access
- `useNavigationStack.js` - Navigation state management
- `useScreenActivity.js` - Screen activity tracking
- `useSettingsAPI.js` - Settings API interactions
- `useSourceProgress.js` - Playback progress tracking with local interpolation and seek
- `useVirtualKeyboard.js` - Virtual keyboard state
- `useVolumeThrottle.js` - Volume control throttling

**State synchronization**: Backend state changes → WebSocket event → Pinia store update → Reactive UI update

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

**Do NOT modify this order without understanding the circular dependencies documented in dependencies.py:227-348**

### 2. Audio Source Architecture

All audio sources must implement `AudioSourceProtocol` interface:

```python
class AudioSourceProtocol(Protocol):
    async def initialize(self) -> bool
    async def start(self) -> bool
    async def stop(self) -> bool
    async def get_status(self) -> Dict[str, Any]
    async def handle_command(self, command: str, data: Dict) -> Dict[str, Any]
```

**Base class available**: `BaseAudioSource` in `backend/core/audio_source.py` provides common functionality (state management, systemd control, logging).

**Uniform source structure** — Every source in `backend/sources/{source}/` must follow:
- `__init__.py` — Docstring + `__all__` exporting `Source`, `router`, `setup_{source}_routes`
- `source.py` — Constructor takes `config`, `state_machine`, `settings_service`, `systemd_manager`
- `routes.py` — `logger = logging.getLogger(__name__)` at module level; `logger.error()` before every `raise HTTPException` in except blocks; use `run_source_command()` for playback routes

**Reference implementations**:
- **Radio source** (`backend/sources/radio/`) - Demonstrates multi-component architecture, external API integration, file uploads, and complex data persistence
- **Podcast source** (`backend/sources/podcast/`) - Demonstrates external API integration (Taddy API), playback progress tracking with resume functionality, subscription management, and advanced playback controls (speed, seek)
- **AirPlay source** (`backend/sources/airplay/`) - Demonstrates metadata pipe parsing, binary artwork handling, and external process integration (shairport-sync)

#### Podcast Source Architecture

The Podcast source demonstrates several advanced patterns:

**External API Integration (Taddy GraphQL API)**:
- `TaddyAPI` class (`taddy_api.py`) handles all GraphQL queries to Taddy API
- Built-in caching layer with configurable duration (60 minutes default)
- Language/country mapping logic (including `ITUNES_COUNTRY_TO_TADDY_COUNTRY`) lives in `taddy_api.py`
- Genre mapping to iTunes RSS feed IDs for charts

**Audio Playback**:
- Reuses `MpvController` from Radio source for consistency
- Systemd service: `milo-podcast.service` (separate instance of mpv)
- IPC socket: `/run/milo/podcast-ipc.sock`
- Playback states: `INACTIVE` (stopped) → `READY` (service running, idle) → `CONNECTED` (episode playing)

**Progress Tracking & Resume**:
- `PodcastDataService` manages playback progress persistence
- Automatic progress save every 10 seconds during playback
- Resume from last position when re-opening episode (if > 10 seconds)
- Progress cleared when episode completes (within 5 seconds of end)

**Advanced Playback Controls**:
- **Speed control**: 0.5x, 0.75x, 1.0x, 1.25x, 1.5x, 2.0x (stored in user preferences)
- **Seek**: Jump to specific timestamp with validation
- **Buffering detection**: Separate `is_buffering` state during stream loading

**Data Persistence**:
- `podcast_data.json` - Subscriptions, favorites, playback progress, user preferences (all keys `snake_case`)
- Automatic migration from legacy `camelCase` keys on load (in `PodcastDataService._migrate_keys()`)
- Episode metadata caching to reduce API calls
- Full episode objects stored for offline UI rendering

**Frontend Components** (`frontend/src/components/podcasts/`):
- `PodcastSource.vue` - Main container with navigation
- `HomeView.vue` - Curated recommendations and charts
- `SearchView.vue` - Search interface with Taddy API
- `SubscriptionsView.vue` - User's subscribed podcasts
- `GenreView.vue` - Browse by genre (mapped to iTunes RSS)
- `QueueView.vue` - Playback queue management
- `PodcastCard.vue`, `EpisodeCard.vue` - Reusable components
- `ProgressBar.vue` - Visual playback progress with seek capability
- `PodcastDetails.vue`, `EpisodeDetails.vue` - Detail views
- `SkeletonPodcastCard.vue`, `SkeletonPodcastDetails.vue` - Loading skeletons for podcasts
- `SkeletonEpisodeCard.vue`, `SkeletonEpisodeDetails.vue` - Loading skeletons for episodes

**API Routes** (`backend/sources/podcast/routes.py`):
- Search podcasts by query
- Get trending podcasts by genre/language
- Get podcast details and episodes
- Manage subscriptions and favorites
- Playback controls (play, pause, resume, seek, stop, speed)
- Progress tracking and retrieval

**Health API Routes** (`backend/api/health.py`):
- `GET /api/health` - Comprehensive health check with all service statuses
- `GET /api/ping` - Simple liveness probe for monitoring

**Programs API Routes** (`backend/api/programs.py`):
- Get current program versions
- Check for available updates from GitHub
- Execute program updates (local and satellite devices)
- Satellite device update coordination (for multi-room setups)

**Key Differences from Radio Source**:
- External API dependency (Taddy) vs. local station management (Radio)
- Progress tracking with resume vs. stateless playback
- Speed control support (not available in Radio)
- More complex frontend with multiple views and navigation

#### Radio Source Architecture

The Radio source (`backend/sources/radio/`) manages internet radio playback with local station management.

**Frontend Components** (`frontend/src/components/radio/`):
- `RadioSource.vue` - Main radio interface with station display
- `FavoritesView.vue` - User's favorite stations
- `SearchView.vue` - Station search interface (RadioBrowser API)
- `SkeletonStationCard.vue` - Loading skeleton for stations

#### Spotify Source Architecture

The Spotify source (`backend/sources/spotify/`) integrates with go-librespot for Spotify Connect functionality.

**Frontend**: `SpotifySource.vue` is a thin wrapper around the shared `AudioPlayerFull.vue` component with playback controls enabled.

#### AirPlay Source Architecture

The AirPlay source (`backend/sources/airplay/`) provides AirPlay 2 support via shairport-sync + NQPTP.

**Metadata Pipe**: `MetadataReader` reads the shairport-sync named pipe (`/tmp/shairport-sync-metadata`) for:
- Track metadata (`core` items: title, artist, album, genre)
- Artwork (`PICT` code: cover art as JPEG/PNG binary)
- Play state (`ssnc` items: `pbeg`/`pend` for play/stop)
- Client name (`snam` code: X-Apple-Client-Name, e.g. "iPhone de Léo")

**Key differences from other sources**:
- No remote playback control (AirPlay 2 doesn't support it) — `showControls=false`
- Metadata comes from a named pipe, not an API or IPC socket
- Artwork is binary data read directly from the pipe
- Uses `shairport-sync` systemd service (not mpv)

**Frontend**: `AirPlaySource.vue` is a thin wrapper around the shared `AudioPlayerFull.vue` component with controls disabled and a source-bar showing the connected device name instead.

#### Shared Audio Player (AudioPlayerFull.vue)

`AudioPlayerFull.vue` in `frontend/src/components/audio/` is the full-screen player shared by Spotify and AirPlay. It accepts:
- `source` prop — audio source identifier
- `showControls` prop — displays `PlaybackControls` + `ConnectProgressBar` when true (Spotify), shows source-bar with icon + device name when false (AirPlay)

### 3. State Management Flow

**Always use state_machine methods, never modify state directly:**

```python
# ✅ Correct
await state_machine.update_source_state(source, SourceState.READY, metadata)

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

Settings are stored in `/var/lib/milo/settings.json` with:
- Atomic writes via `os.replace()`
- File locks for concurrent access
- Automatic backups on corruption

### 6. ALSA Dynamic Routing

Each audio source has 4 ALSA devices selected via environment variables:

```
milo_{source}_direct          # Direct via CamillaDSP to amplifier
milo_{source}_multiroom       # To Snapcast loopback (DSP applied on each client)
```

Selection controlled by:
- `MILO_MODE=direct` or `multiroom`

CamillaDSP is ALWAYS in the audio path for volume control.
DSP effects (EQ, compressor, loudness) are toggled via `bypass_effects()` / `restore_effects()` in CamillaDSP, not via ALSA routing.

These are auto-generated in `/var/lib/milo/routing.env` based on settings.json.

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

**Pydantic models**: All models use `snake_case` field names. Shared models live in `backend/api/models.py` (e.g., `ClientUpdateRequest`). Source-specific models live in `backend/sources/{source}/models.py`.

### 8. Frontend Conventions

**API calls**: Use `apiCall()` from `frontend/src/services/apiCall.js` for all store API actions (wraps try/catch with logging).

**i18n**: Use `const { t } = useI18n()` in `<script setup>`, not the global `$t()`.

**CSS**: Use design tokens (`var(--color-*)`, `var(--space-*)`, `var(--radius-*)`) instead of hardcoded values.

**Code style**: All `.js` files use semicolons. Constants files use camelCase naming (`audioPlayer.js`, `musicGenres.js`).

**WS event handling**: WebSocket events should be handled in Pinia stores, not in Vue components directly. Components react to store state changes.

## Adding New Features

### Adding a New Audio Source

1. **Define enum** in `backend/core/models/audio_state.py::AudioSource`
2. **Create source module** in `backend/sources/{source}/` with:
   - `source.py` - Extending `BaseAudioSource`, constructor takes `(config, state_machine, settings_service, systemd_manager)`
   - `routes.py` - FastAPI routes with `logger = logging.getLogger(__name__)`, `router` with `responses={404: ...}`, playback routes using `run_source_command()`
   - `models.py` - Pydantic models with `snake_case` fields (if needed)
   - `__init__.py` - Docstring + `__all__` exporting `Source`, `router`, `setup_{source}_routes`
3. **Register in dependencies** (`backend/dependencies.py::_create_service()`)
4. **Add ALSA devices** in `/etc/asound.conf` with 2 variants (direct via CamillaDSP, multiroom via Snapcast)
5. **Register source** in `backend/dependencies.py::initialize_services()`
6. **Register routes** in `backend/main.py`
7. **Create Vue component** in `frontend/src/components/{source}/`
8. **Update stores** if needed in `frontend/src/stores/` (use `apiCall()` for API actions, handle WS events in store)

**Reference implementations**:
- **Radio source** (`backend/sources/radio/`) - Local station management, file uploads, custom stations with image storage
- **Podcast source** (`backend/sources/podcast/`) - External API integration (Taddy), playback progress tracking with resume, speed control, complex multi-view frontend with navigation
- **AirPlay source** (`backend/sources/airplay/`) - Metadata pipe parsing, binary artwork, external process integration (shairport-sync)

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

**Frontend (Vitest):**
- Not currently configured but structure supports it
- Would use `@vue/test-utils` for component testing

## Data Persistence Locations

All persistent data in `/var/lib/milo/`:

- `settings.json` - Central settings (language, volume, screen, routing, dock)
- `hardware.json` - Hardware configuration (screen type/resolution)
- `radio_data.json` - Radio favorites and custom stations
- `radio_images/` - Uploaded station images
- `podcast_data.json` - Podcast subscriptions, favorites, playback progress, and user preferences
- `routing.env` - ALSA routing environment variables (auto-generated)
- `last_volume.json` - Last volume for restoration
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
- `milo-camilladsp` - CamillaDSP audio processing
- `milo-snapserver-multiroom` + `milo-snapclient-multiroom` - Multiroom audio
- `milo-kiosk` - Chromium kiosk mode for touchscreen
- `milo-disable-wifi-power-management` - WiFi power management optimization
- `milo-readiness` - System readiness check

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

1. **Don't modify initialization order** in dependencies.py without understanding circular dependencies
2. **Don't bypass state_machine** - always use `update_source_state()` and `broadcast_event()`
3. **Don't bypass SettingsService** - direct JSON file edits won't persist correctly
4. **Don't use blocking I/O** - always async/await for file, network, subprocess operations
5. **Don't skip source registration** - register in `initialize_services()` BEFORE `init_async()`
6. **Don't hardcode ALSA devices** - use environment variable pattern for multiroom/equalizer switching
7. **Don't use `ws_manager.broadcast_dict()` directly** - use `state_machine.broadcast_event()` for all events
8. **Don't use camelCase in Pydantic models** - all fields must be `snake_case`
9. **Don't handle WS events in Vue components** - handle them in Pinia stores, components react to store state
10. **Don't use `POST` for idempotent updates** - use `PUT` for settings, `DELETE` for removals, `PATCH` for partial updates


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

The codebase must stay **clean, optimized and free of legacy / transitional layers**.

When refactoring or adding features:

- **Do NOT introduce or keep migration / fallback code paths** for old implementations, unless explicitly requested in an issue.
  - No duplicated “old” and “new” versions of the same logic.
  - No compatibility shims that keep unused APIs alive “just in case”.
  - No feature flags whose only purpose is to keep legacy behavior around.
- If a data/model/API change is needed:
  - Implement the **new, final version** directly.
  - Migrate existing data if required.
  - **Remove any temporary migration helpers** once the change is applied.
- Always prefer:
  - A **single, optimized code path** over multiple conditional branches for legacy behavior.
  - Clear, simple refactors over incremental “layer on top of legacy” patches.

In short: **keep the codebase OPTIM** (simple, efficient, modern) and avoid accumulating backward-compatibility or migration baggage.


## Reference Documentation

- [Architecture Details](docs/architecture.md) - Deep dive into technologies and audio routing
- [Development Guide](docs/development.md) - Complete developer reference with examples
- [README](README.md) - Installation and hardware requirements
