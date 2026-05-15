# Milō Developer Guide

This guide is for developers who want to contribute to or fork the Milō project.

## Development environment setup

### Prerequisites

- Raspberry Pi 4 or 5 with Raspberry Pi OS 64-bit
- Python 3.10+
- Node.js 18+
- Git

### Development installation

```bash
# Clone the repository
git clone https://github.com/leodurandfr/Milo.git
cd Milo

# Python backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Vue frontend
cd frontend
npm install
```

### Run in development mode

**Terminal 1 - Backend:**
```bash
source venv/bin/activate
cd backend
python main.py
# → http://0.0.0.0:8000
```

**Terminal 2 - Frontend:**
```bash
cd frontend
npm run dev
# → http://0.0.0.0:5173
```

The Vite frontend automatically proxies requests to the backend (see `frontend/vite.config.js`).

## Code architecture

### Backend (Python FastAPI)

```
backend/
├── core/                      # Core infrastructure
│   ├── models/               # Domain models (AudioSource, SourceState, SystemAudioState)
│   ├── state.py              # AudioStateMachine (single source of truth)
│   ├── audio_source.py       # BaseAudioSource abstract class
│   ├── settings.py           # SettingsService
│   ├── systemd.py            # SystemdServiceManager
│   ├── volume/               # Volume service + handlers
│   ├── dsp/                  # CamillaDSP service + proxy + sync
│   ├── multiroom/            # Snapcast + routing + crossover
│   └── updates/              # Update + version services
├── sources/                   # Audio source implementations
│   ├── spotify/              # SpotifySource + routes
│   ├── airplay/              # AirPlaySource + metadata_reader + routes
│   ├── bluetooth/            # BluetoothSource + routes
│   ├── mac/                  # MacSource + routes
│   ├── radio/                # RadioSource + routes + browser_api
│   ├── podcast/              # PodcastSource + routes + taddy_api
│   └── cd/                   # CDSource + routes
├── api/                       # REST API routes
├── hardware/                  # Hardware controllers (rotary, IR remote, BT remote, screen)
├── ws/                        # WebSocket server + manager
├── shared/                    # Shared utilities (MpvController)
├── config/constants.py        # Centralized constants
├── dependencies.py            # Service Registry (lazy singletons)
└── main.py                    # Entry point
```

**Architectural principles:**
- **Source-Based**: Each audio source is a self-contained module
- **Service Registry**: Simple dict-based DI with lazy singleton creation
- **Single Source of Truth**: `AudioStateMachine`
- **Async-first**: asyncio everywhere for non-blocking I/O

### Frontend (Vue 3 + Vite)

```
frontend/src/
├── components/
│   ├── audio/                # Shared audio player + screensaver + source layout
│   ├── airplay/              # AirPlay source UI
│   ├── cd/                   # CD source UI
│   ├── equalizer/            # Equalizer / DSP controls
│   ├── multiroom/            # Multiroom (Snapcast) controls
│   ├── navigation/           # Navigation stack
│   ├── network/              # Network / WiFi settings
│   ├── podcasts/             # Podcast source UI
│   ├── radio/                # Radio source UI
│   ├── settings/             # System settings (nested categories)
│   ├── setup/                # First-boot setup wizard
│   ├── spotify/              # Spotify source UI
│   ├── system/               # System-level UI (info, update)
│   └── ui/                   # Reusable UI primitives
├── stores/                   # Pinia stores
│   ├── unifiedAudioStore.js  # Central audio state (WebSocket-synced)
│   ├── settingsStore.js      # Settings
│   ├── equalizerStore.js     # CamillaDSP state
│   ├── multiroomStore.js     # Multiroom state
│   ├── snapcastStore.js      # Snapcast server config
│   ├── radioStore.js         # Radio stations + playback
│   ├── podcastStore.js       # Podcasts + playback progress
│   ├── cdStore.js            # CD playback
│   ├── discoveryStore.js     # mDNS discovery
│   └── systemStore.js        # System info / updates
├── composables/              # Vue composables (useSourceProgress, useVolumeThrottle, ...)
├── services/
│   ├── websocket.js          # WebSocket client (auto-reconnect)
│   ├── apiCall.js            # Wrapped fetch helper for stores
│   └── i18n.js               # Internationalization
├── assets/styles/
│   └── design-system.css     # CSS variables + typography utilities
└── views/
    └── MainView.vue          # Main view (SPA)
```

**Architectural principles:**
- **Composition API**: Code organized by functionality
- **Reactive State**: Pinia + WebSocket sync
- **Single Page App**: No routing (single view)

## Data flow

### State change (user action → UI update)

```
1. User clicks button
   ↓
2. Component calls API (fetch/axios)
   ↓
3. Backend route handler
   ↓
4. Service updates state machine
   ↓
5. State machine calls broadcast_event()
   ↓
6. WebSocketManager sends to all clients
   ↓
7. Frontend WebSocket receives event
   ↓
8. Store updates (Pinia)
   ↓
9. Components re-render (reactive)
```

### WebSocket event format

Wire format: `{ category, type, origin, data, timestamp }`. `origin` is
derived from `data["source"]` (falling back to `category`). When using
category `source`, you **must** include `"source"` in `data`.

```javascript
{
  "category": "source",          // source | system | routing | equalizer |
                                 // multiroom | settings | volume | programs
  "type": "state_changed",
  "origin": "spotify",           // populated from data.source
  "data": {
    "source": "spotify",
    "metadata": { ... }
  },
  "timestamp": 1234567890
}
```

## Adding a new audio source

### 1. Define the enum

`backend/core/models/audio_state.py`:
```python
class AudioSource(Enum):
    NONE = "none"
    SPOTIFY = "spotify"
    BLUETOOTH = "bluetooth"
    RADIO = "radio"
    PODCAST = "podcast"
    AIRPLAY = "airplay"
    MAC = "mac"
    CD = "cd"
    MY_SOURCE = "my_source"  # ← Add here
```

`SourceState` values: `STARTING`, `WAITING`, `ACTIVE`, `ERROR`.

### 2. Create the source

`backend/sources/my_source/source.py`:
```python
from backend.core.audio_source import BaseAudioSource
from backend.core.models.audio_state import AudioSource, SourceState

class MySource(BaseAudioSource):
    def __init__(self, state_machine):
        self.state_machine = state_machine
        self.source = AudioSource.MY_SOURCE

    async def initialize(self):
        """Initialization on application startup"""
        # Initial setup
        pass

    async def start(self):
        """Start the service (systemctl start, etc.)"""
        # Notify state change — WAITING = service up, no client connected yet
        await self.state_machine.update_source_state(
            self.source,
            SourceState.WAITING
        )
        return True

    async def stop(self):
        """Stop the service — the state machine sets the source to NONE
        when this source is no longer active."""
        return True

    async def get_status(self):
        """Get current status"""
        return {
            "status": "active",
            "metadata": {}
        }

    async def handle_command(self, command: str, params: dict):
        """Handle commands (play, pause, etc.)"""
        if command == "play":
            # Implementation
            pass
        return {"success": True}
```

### 3. Register in container

`backend/dependencies.py`:
```python
from backend.sources.my_source.source import MySource

# In _create_service():
my_source = MySource(state_machine=state_machine)

# In initialize_services():
# Register source BEFORE async init
state_machine.register_source(
    AudioSource.MY_SOURCE,
    my_source
)
```

### 4. Add ALSA devices

`/etc/asound.conf`:
```
pcm.milo_mysource {
    @func concat
    strings [
        "pcm.milo_mysource_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
    ]
}

# Direct mode: via CamillaDSP loopback
pcm.milo_mysource_direct {
    type plug
    slave.pcm "camilladsp"
}

# Multiroom mode: via Snapcast loopback
# Slot 0 is reserved for the DSP input; existing sources occupy slots 1..7.
# Use the next free contiguous slot (currently 8) and bump pcm_substreams in
# /etc/modprobe.d/snd-aloop.conf accordingly. Then add a matching `source = alsa:///?...&device=hw:1,1,N` line in /etc/snapserver.conf.
pcm.milo_mysource_multiroom {
    type plug
    slave.pcm {
        type hw
        card Loopback
        device 0
        subdevice 8
    }
}
```

Note: CamillaDSP is always in the audio path for volume control. DSP effects are toggled within CamillaDSP.

### 5. Create API routes

`backend/sources/my_source/routes.py`:
```python
from fastapi import APIRouter

def setup_my_source_routes(get_source):
    router = APIRouter(prefix="/my_source", tags=["my_source"])

    @router.post("/play")
    async def play():
        source = get_source()
        result = await source.handle_command("play", {})
        return {"status": "success", "data": result}

    return router
```

Register in `backend/main.py`:
```python
from backend.sources.my_source.routes import setup_my_source_routes

my_source_router = setup_my_source_routes(
    lambda: state_machine.sources.get(AudioSource.MY_SOURCE)
)
app.include_router(my_source_router)
```

### 6. Create frontend interface

`frontend/src/components/audio/MySourceDisplay.vue`:
```vue
<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const audioStore = useUnifiedAudioStore();

const isActive = computed(() =>
  audioStore.activeSource === 'my_source'
);

const metadata = computed(() =>
  audioStore.metadata || {}
);

async function play() {
  await fetch('/api/my_source/play', { method: 'POST' });
}
</script>

<template>
  <div class="my-source-display" :class="{ active: isActive }">
    <h2>My Source</h2>
    <button @click="play">Play</button>
  </div>
</template>
```

Add to `MainView.vue` or main layout.

### Reference implementation: Radio source

The Radio source (`backend/sources/radio/`) is a complete, production-ready reference implementation that demonstrates advanced source architecture:

**Multi-component architecture:**
- `source.py` - Main source class (BaseAudioSource implementation)
- `browser_api.py` - External API integration with caching (60min TTL)
- `data.py` - Favorites, custom stations, data persistence
- `genres.py` - Genre definitions and mapping
- `shazam.py` - Song recognition integration
- `models.py` - Pydantic models

**Key features demonstrated:**
- External API integration (Radio Browser API)
- Service lifecycle management (systemd + IPC socket)
- Complex data persistence (favorites, custom stations in /var/lib/milo/radio_data.json, images in /var/lib/milo/radio_images/)
- File uploads (station images with validation and storage)
- Caching strategy (API responses cached for performance)
- Error handling (broken station detection and filtering)
- Frontend integration (search, filters, modals, screensaver)

**API routes:** 25+ endpoints including search, favorites, custom stations, image uploads
**Frontend components:** RadioSource.vue, FavoritesView.vue, SearchView.vue, StationCard.vue, SkeletonStationCard.vue
**Store:** radioStore.js (Pinia) with full state management

This is an excellent reference for building a complex audio source with external dependencies, data persistence, and rich UI interactions.

## Testing

### Backend (pytest)

```bash
cd backend
python -m pytest                 # All tests
python -m pytest -v              # Verbose
python -m pytest -m unit         # Unit tests only
python -m pytest -k "test_name"  # Specific test
```

**Writing a test:**

`backend/tests/test_my_source.py`:
```python
import pytest
from backend.sources.my_source.source import MySource

@pytest.mark.asyncio
async def test_source_initialization():
    # Mock state machine
    class MockStateMachine:
        async def update_source_state(self, source, state):
            pass

    source = MySource(MockStateMachine())
    await source.initialize()

    assert source.source == AudioSource.MY_SOURCE
```

### Smoke tests (Pi only)

```bash
# Static ALSA routing check (subdevice layout, CamillaDSP capture, snapserver sources)
bash scripts/test-alsa-routing.sh
bash scripts/test-alsa-routing.sh --with-live   # + non-destructive aplay open per alias

# Multiroom state-coherence — toggles multiroom 20× and asserts settings.json,
# routing.env, and the snapserver/snapclient units stay in agreement.
bash scripts/test-multiroom-desync.sh
sudo bash scripts/test-multiroom-desync.sh --kill-test   # + kill -9 mid-toggle
```

### Frontend (Vitest)

```bash
cd frontend
npm run test        # Run tests
npm run test:ui     # UI mode
```

**Writing a test:**

`frontend/src/components/__tests__/MyComponent.spec.js`:
```javascript
import { describe, it, expect } from 'vitest';
import { mount } from '@vue/test-utils';
import MyComponent from '../MyComponent.vue';

describe('MyComponent', () => {
  it('renders properly', () => {
    const wrapper = mount(MyComponent);
    expect(wrapper.text()).toContain('My Source');
  });
});
```

## Concurrency and thread safety

### Using locks

**Backend:**
```python
import asyncio

class MyService:
    def __init__(self):
        self._lock = asyncio.Lock()

    async def critical_operation(self):
        async with self._lock:
            # Protected code (no race conditions)
            pass
```

### State machine transitions

Transitions are protected by `_transition_lock`. During a transition, state updates are **buffered** and replayed after.

```python
# ✅ Good: uses update_source_state
await state_machine.update_source_state(source, state)

# ❌ Bad: directly modifies state
state_machine._state.active_source = source
```

## Best practices

### Backend

1. **Always use async/await** for I/O
2. **Appropriate logs**:
   - `logger.debug()`: Detailed info (not in prod)
   - `logger.info()`: Important info
   - `logger.warning()`: Non-blocking issue
   - `logger.error()`: Blocking error

3. **Error handling**:
```python
try:
    result = await risky_operation()
except SpecificError as e:
    logger.error(f"Operation failed: {e}")
    return {"status": "error", "message": str(e)}
```

4. **No sudo** in code
   - Use systemctl via `SystemdServiceManager`
   - Systemd services have necessary permissions

### Frontend

1. **Composition API** instead of Options API
2. **Computed properties** for derived data
3. **No direct DOM manipulation** (use Vue refs)
4. **Debounce** frequent events:
```javascript
import { debounce } from 'lodash-es';

const handleInput = debounce((value) => {
  // Logic
}, 300);
```

5. **Cleanup** in `onUnmounted`:
```javascript
import { onMounted, onUnmounted } from 'vue';

onMounted(() => {
  const cleanup = websocket.on('event', handler);

  onUnmounted(() => {
    cleanup();
  });
});
```

## Deployment

### Production build

```bash
# Frontend
cd frontend
npm run build
# → frontend/dist/

# nginx serves the static dist/ directly — there is no milo-frontend service.
```

### Creating a release

```bash
# Tag the version
git tag -a v1.2.0 -m "Release 1.2.0"
git push origin v1.2.0

# GitHub Actions (if configured) automatically builds
```

### Updating an installation

```bash
cd ~/milo
git pull origin main

# Backend
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart milo-backend

# Frontend (no service to restart — nginx serves the new dist/ as soon as it lands)
cd frontend
npm install
npm run build
```

## Debugging

### Backend

**Live logs:**
```bash
sudo journalctl -u milo-backend -f
```

**Debug mode:**
```python
# backend/main.py
logging.basicConfig(level=logging.DEBUG)
```

**Breakpoints (pdb):**
```python
import pdb; pdb.set_trace()
```

**VSCode launch.json:**
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": "Python: Backend",
      "type": "python",
      "request": "launch",
      "program": "${workspaceFolder}/backend/main.py",
      "cwd": "${workspaceFolder}",
      "env": {
        "PYTHONPATH": "${workspaceFolder}"
      }
    }
  ]
}
```

### Frontend

**Vue DevTools:**
- Chrome/Firefox extension to inspect Vue state

**Console logs:**
```javascript
console.log('State:', audioStore.$state);
```

**WebSocket debug:**
```javascript
// In browser console
wsDebug()  // Shows WebSocket state (dev mode only)
```

## Contributing

### Git workflow

```bash
# Create a branch
git checkout -b feature/my-feature

# Commits
git add .
git commit -m "feat: add my feature"

# Push
git push origin feature/my-feature

# Create a Pull Request on GitHub
```

### Commit convention

- `feat:` New feature
- `fix:` Bug fix
- `refactor:` Refactoring (no functional change)
- `docs:` Documentation only
- `test:` Adding/modifying tests
- `chore:` Maintenance (dependencies, config, etc.)

### PR checklist

- [ ] Tests pass (`pytest`, `npm run test`)
- [ ] Code formatted (Black for Python, Prettier for JS)
- [ ] No linter warnings
- [ ] Documentation updated if necessary
- [ ] Tested locally on Raspberry Pi

## Important points

### Initialization order

⚠️ **CRITICAL**: Service initialization order in `dependencies.py` matters!

1. Retrieve service instances (triggers lazy creation via `get_service()`)
2. Resolve circular dependencies via setters
3. **Register sources** in state machine (BEFORE async init)
4. **Parallel async initialization** via `asyncio.gather()`

See detailed comments in `backend/dependencies.py`.

### WebSocket broadcasting

Always use `state_machine.broadcast_event()` to propagate changes:

```python
await self.state_machine.broadcast_event(
    category="source",
    type="state_changed",
    data={
        "source": self.source.value,
        "metadata": metadata
    }
)
```

### Settings persistence

Settings modifications must go through `SettingsService`:

```python
# ✅ Good
await settings_service.set_setting('volume.alsa_max', 80)

# ❌ Bad (not persisted)
settings['volume']['alsa_max'] = 80
```

## GitHub Token (Optional)

Milō checks GitHub API for dependency updates. Without a token: 60 req/hour. With token: 5000 req/hour.

### Setup

1. Create token at https://github.com/settings/tokens → **"Generate new token (classic)"**
2. Scope: `public_repo` only (read-only, minimal permissions)
3. Add to systemd service:

```bash
sudo nano /etc/systemd/system/milo-backend.service
```

Add this line in the `[Service]` section:
```ini
Environment="GITHUB_TOKEN=ghp_YourTokenHere"
```

4. Reload and restart:
```bash
sudo systemctl daemon-reload && sudo systemctl restart milo-backend
```

5. Verify:
```bash
sudo journalctl -u milo-backend -n 50 | grep "GitHub token"
# Expected: "GitHub token detected - using authenticated API (5000 req/hour)"
```

## Resources

- [Detailed Architecture](architecture.md)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Vue 3 Docs](https://vuejs.org/)
- [Pinia Docs](https://pinia.vuejs.org/)

## Contact

- **GitHub Issues:** https://github.com/leodurandfr/Milo/issues
- **Discussions:** https://github.com/leodurandfr/Milo/discussions
