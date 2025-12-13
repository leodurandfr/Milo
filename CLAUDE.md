

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Milō is a multiroom audio system for Raspberry Pi that supports Spotify Connect, Bluetooth, Mac streaming (ROC), Internet Radio, and Podcasts. Built with FastAPI (Python) backend and Vue 3 frontend, using ALSA for audio without Pipewire/PulseAudio.

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
sudo systemctl restart milo-frontend

# Check service status
sudo systemctl status milo-backend
sudo systemctl status milo-spotify
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
│   ├── boot-common.sh
│   ├── screen-waveshare-7-usb.sh
│   └── screen-waveshare-8-dsi.sh
├── rootfs/               # Files deployed to system (mirrors target filesystem)
│   ├── etc/NetworkManager/dispatcher.d/   # Network event scripts
│   ├── usr/local/bin/                     # System scripts (milo-wait-ready.sh)
│   ├── usr/share/plymouth/themes/milo/    # Boot animation theme
│   └── home/milo/                         # User config (.bash_profile, .config/)
├── milo-client/          # Satellite client for multiroom
└── docs/                 # Documentation
```

**Directory conventions:**
- `install/` - Scripts executed only during `install.sh` (screen/boot configuration)
- `rootfs/` - Files copied to the system at install time, run at boot/runtime
- `system/` - Systemd unit files

## Architecture Overview

### Backend: Layered Domain-Driven Design

```
backend/
├── domain/                    # Business models (AudioSource, PluginState, SystemAudioState)
├── application/interfaces/    # Plugin contracts (AudioSourcePlugin)
├── infrastructure/
│   ├── plugins/              # Audio source implementations (spotify, mac, bluetooth, radio, podcast)
│   ├── services/             # Business services:
│   │                          #   - volume* (volume_service orchestrates: volume_config, volume_converter, volume_storage)
│   │                          #   - routing, snapcast, snapcast_websocket, settings, equalizer
│   │                          #   - podcast_data, radio_data, hardware
│   │                          #   - systemd_manager, program_version, program_update, satellite_program_update
│   │                          #   - direct_volume_handler, multiroom_volume_handler
│   ├── hardware/             # Hardware controllers (rotary encoder, screen)
│   └── state/                # UnifiedAudioStateMachine (single source of truth)
├── presentation/
│   ├── api/routes/           # REST endpoints (audio sources, settings, health, etc.)
│   └── websockets/           # WebSocket server
└── config/container.py       # Dependency injection with dependency-injector
```

**Key architectural principles:**
- **Single Source of Truth**: `UnifiedAudioStateMachine` manages all audio state
- **Plugin Architecture**: All audio sources implement `AudioSourcePlugin` interface
- **Async-first**: asyncio everywhere for non-blocking I/O
- **Dependency Injection**: via `dependency-injector` library

### Frontend: Vue 3 Composition API

```
frontend/src/
├── components/
│   ├── audio/         # General audio player (AudioPlayer.vue, AudioSourceLayout.vue, AudioSourceStatus.vue, AudioSourceView.vue)
│   ├── podcasts/      # Podcast-specific UI (PodcastSource.vue, HomeView.vue, etc.)
│   ├── radio/         # Radio-specific UI (RadioSource.vue)
│   ├── spotify/       # Spotify-specific UI (SpotifySource.vue, PlaybackControls.vue)
│   ├── dsp/           # CamillaDSP controls (parametric EQ, compressor, loudness)
│   ├── multiroom/     # Multiroom controls (Snapcast management)
│   ├── navigation/    # Navigation components
│   ├── settings/      # System settings (SettingsModal.vue, SettingsCategory.vue) with nested categories/:
│   │                   #   ApplicationsSettings, UpdateManager, PodcastSettings, MultiroomSettings,
│   │                   #   VolumeSettings, LanguageSettings, ScreenSettings, InfoSettings, SpotifySettings,
│   │                   #   radio/RadioSettings, radio/ManageStation
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
- `dspStore.js` - DSP/equalizer state (CamillaDSP)
- `multiroomStore.js` - Multiroom/Snapcast state
- `podcastStore.js` - Podcast data and playback
- `radioStore.js` - Radio stations and playback

**Vue Composables** (`frontend/src/composables/`):
- `useAnimatedHeight.js` - Animation helper for expandable elements
- `useHardwareConfig.js` - Hardware configuration access
- `useNavigationStack.js` - Navigation state management
- `useScreenActivity.js` - Screen activity tracking
- `useSettingsAPI.js` - Settings API interactions
- `useVirtualKeyboard.js` - Virtual keyboard state

**Component-specific composables** (in `components/spotify/`):
- `usePlaybackProgress.js` - Spotify playback progress tracking
- `useSpotifyControl.js` - Spotify control actions

**State synchronization**: Backend state changes → WebSocket event → Pinia store update → Reactive UI update

## Critical Implementation Details

### 1. Service Initialization Order (CRITICAL)

The order in `backend/config/container.py::initialize_services()` is **CRITICAL** due to circular dependencies:

1. **Create instances** (order non-critical)
2. **Resolve circular dependencies** via setters:
   - `routing_service.set_plugin_callback()` → allows access to state_machine plugins
   - `routing_service.set_snapcast_websocket_service()` → enables lifecycle control
   - `routing_service.set_state_machine()` → enables event broadcasting
   - `state_machine.routing_service = routing_service` → circular reference completion
3. **Register plugins** in state_machine (BEFORE async init)
4. **Parallel async initialization** via `asyncio.gather()`

**Do NOT modify this order without understanding the circular dependencies documented in container.py:142-186**

### 2. Audio Plugin Architecture

All audio sources must implement `AudioSourcePlugin` interface:

```python
class AudioSourcePlugin(ABC):
    async def initialize(self) -> bool
    async def start(self) -> bool
    async def stop(self) -> bool
    async def get_status(self) -> Dict[str, Any]
    async def handle_command(self, command: str, data: Dict) -> Dict[str, Any]
```

**Base class available**: `UnifiedAudioPlugin` in `backend/infrastructure/plugins/base.py` provides common functionality (state management, systemd control, logging).

**Reference implementations**:
- **Radio plugin** (`backend/infrastructure/plugins/radio/`) - Demonstrates multi-component architecture, external API integration, file uploads, and complex data persistence
- **Podcast plugin** (`backend/infrastructure/plugins/podcast/`) - Demonstrates external API integration (Taddy API), playback progress tracking with resume functionality, subscription management, and advanced playback controls (speed, seek)

#### Podcast Plugin Architecture

The Podcast plugin demonstrates several advanced patterns:

**External API Integration (Taddy GraphQL API)**:
- `TaddyAPI` class (`taddy_api.py`) handles all GraphQL queries to Taddy API
- Built-in caching layer with configurable duration (60 minutes default)
- Language mapping from Milo settings to Taddy API enums
- Genre mapping to iTunes RSS feed IDs for charts

**Audio Playback**:
- Reuses `MpvController` from Radio plugin for consistency
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
- `podcast_data.json` - Subscriptions, favorites, playback progress, user preferences
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

**API Routes** (`backend/presentation/api/routes/podcast.py`):
- Search podcasts by query
- Get trending podcasts by genre/language
- Get podcast details and episodes
- Manage subscriptions and favorites
- Playback controls (play, pause, resume, seek, stop, speed)
- Progress tracking and retrieval

**Health API Routes** (`backend/presentation/api/routes/health.py`):
- `GET /api/health` - Comprehensive health check with all service statuses
- `GET /api/ping` - Simple liveness probe for monitoring

**Programs API Routes** (`backend/presentation/api/routes/programs.py`):
- Get current program versions
- Check for available updates from GitHub
- Execute program updates (local and satellite devices)
- Satellite device update coordination (for multi-room setups)

**Key Differences from Radio Plugin**:
- External API dependency (Taddy) vs. local station management (Radio)
- Progress tracking with resume vs. stateless playback
- Speed control support (not available in Radio)
- More complex frontend with multiple views and navigation

#### Radio Plugin Architecture

The Radio plugin (`backend/infrastructure/plugins/radio/`) manages internet radio playback with local station management.

**Frontend Components** (`frontend/src/components/radio/`):
- `RadioSource.vue` - Main radio interface with station display
- `FavoritesView.vue` - User's favorite stations
- `SearchView.vue` - Station search interface (RadioBrowser API)
- `RadioScreensaver.vue` - Screensaver mode for radio playback
- `SkeletonStationCard.vue` - Loading skeleton for stations

#### Spotify Plugin Architecture

The Spotify plugin (`backend/infrastructure/plugins/spotify/`) integrates with go-librespot for Spotify Connect functionality.

**Frontend Components** (`frontend/src/components/spotify/`):
- `SpotifySource.vue` - Main Spotify interface with album art and track info
- `PlaybackControls.vue` - Play/pause/skip controls
- `ProgressBar.vue` - Track progress with seek capability
- `usePlaybackProgress.js` - Composable for progress tracking
- `useSpotifyControl.js` - Composable for Spotify control actions

### 3. State Management Flow

**Always use state_machine methods, never modify state directly:**

```python
# ✅ Correct
await state_machine.update_plugin_state(source, PluginState.READY, metadata)

# ❌ Wrong - bypasses locks and broadcasting
state_machine._state.active_source = source
```

**State transitions are protected** by `_transition_lock`. During transitions, state updates are buffered and replayed after.

### 4. WebSocket Broadcasting

All state changes must be broadcast via `state_machine._broadcast_event()`:

```python
await self.state_machine._broadcast_event(
    category="plugin",           # plugin, system, routing, equalizer
    type="state_changed",
    source=self.source.value,
    data={"metadata": {...}}
)
```

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
milo_{source}_direct          # Direct to amplifier
milo_{source}_direct_eq       # Direct with equalizer
milo_{source}_multiroom       # To snapcast loopback
milo_{source}_multiroom_eq    # To snapcast with equalizer
```

Selection controlled by:
- `MILO_MODE=direct` or `multiroom`
- `MILO_EQUALIZER=_eq` or empty

These are auto-generated in `/var/lib/milo/routing.env` based on settings.json.

## Adding New Features

### Adding a New Audio Source Plugin

1. **Define enum** in `backend/domain/audio_state.py::AudioSource`
2. **Create plugin** implementing `AudioSourcePlugin` (extend `UnifiedAudioPlugin` for base functionality)
3. **Register in container** (`backend/config/container.py`)
4. **Add ALSA devices** in `/etc/asound.conf` with 4 variants (direct, direct_eq, multiroom, multiroom_eq)
5. **Create API routes** in `backend/presentation/api/routes/`
6. **Register routes** in `backend/main.py`
7. **Create Vue component** in `frontend/src/components/audio/`
8. **Update stores** if needed in `frontend/src/stores/`

**Reference implementations**:
- **Radio plugin** - Local station management, file uploads, custom stations with image storage
- **Podcast plugin** - External API integration (Taddy), playback progress tracking with resume, speed control, complex multi-view frontend with navigation

### Adding a New Service

1. Create service in `backend/infrastructure/services/`
2. Add to container in `backend/config/container.py`
3. Inject dependencies via constructor
4. If has async `initialize()`, add to `init_async()` in container
5. Create API routes in `backend/presentation/api/routes/`
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
- `milo-bluealsa` + `milo-bluealsa-aplay` - Bluetooth
- `milo-mac` - Mac streaming (ROC receiver)
- `milo-radio` - mpv radio player
- `milo-podcast` - mpv podcast player (separate from radio)
- `milo-snapserver-multiroom` + `milo-snapclient-multiroom` - Multiroom audio
- `milo-kiosk` - Chromium kiosk mode for touchscreen
- `milo-disable-wifi-power-management` - WiFi power management optimization
- `milo-readiness` - System readiness check

**All plugins `BindsTo=milo-backend`** - they stop if backend stops.

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

1. **Don't modify initialization order** in container.py without understanding circular dependencies
2. **Don't bypass state_machine** - always use `update_plugin_state()` and `_broadcast_event()`
3. **Don't bypass SettingsService** - direct JSON file edits won't persist correctly
4. **Don't use blocking I/O** - always async/await for file, network, subprocess operations
5. **Don't skip plugin registration** - register in container BEFORE `initialize_services()`
6. **Don't hardcode ALSA devices** - use environment variable pattern for multiroom/equalizer switching


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
