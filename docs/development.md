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
├── domain/                    # Business models
│   └── audio_state.py        # AudioSource, PluginState, SystemAudioState
├── application/
│   └── interfaces/           # Plugin contracts
│       └── audio_source_plugin.py
├── infrastructure/
│   ├── plugins/              # Audio source implementations
│   │   ├── librespot_plugin.py
│   │   ├── bluetooth_plugin.py
│   │   └── roc_plugin.py
│   ├── services/             # Business services
│   │   ├── settings_service.py
│   │   ├── volume_service.py
│   │   ├── audio_routing_service.py
│   │   └── snapcast_service.py
│   ├── hardware/             # Hardware controllers
│   │   ├── rotary_volume_controller.py
│   │   └── screen_controller.py
│   └── state/                # State machine
│       └── unified_audio_state_machine.py
├── presentation/
│   ├── api/routes/           # REST endpoints
│   └── websockets/           # WebSocket server
├── config/
│   └── container.py          # Dependency injection
└── main.py                   # Entry point
```

**Architectural principles:**
- **Domain-Driven Design** (DDD): Clear separation domain/infra/presentation
- **Dependency Injection**: via `dependency-injector`
- **Single Source of Truth**: `UnifiedAudioStateMachine`
- **Async-first**: asyncio everywhere for non-blocking I/O

### Frontend (Vue 3 + Vite)

```
frontend/src/
├── components/
│   ├── audio/                # Audio source components
│   ├── equalizer/            # Equalizer interface
│   ├── snapcast/             # Multiroom controls
│   ├── settings/             # System settings
│   ├── navigation/           # Navigation
│   └── ui/                   # Reusable UI components
├── stores/                   # Pinia stores
│   ├── unifiedAudioStore.js  # Audio state (WebSocket sync)
│   └── settingsStore.js      # Settings
├── services/
│   ├── websocket.js          # WebSocket client
│   └── i18n.js               # Internationalization
├── assets/styles/
│   └── design-system.css     # CSS variables
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
5. State machine calls _broadcast_event()
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

```javascript
{
  "category": "plugin",          // plugin, system, routing, equalizer
  "type": "state_changed",       // event type
  "source": "librespot",         // AudioSource
  "data": {
    "full_state": { ... },       // Complete system state
    "metadata": { ... }          // Specific data
  },
  "timestamp": 1234567890
}
```

## Adding a new audio source

### 1. Define the enum

`backend/domain/audio_state.py`:
```python
class AudioSource(str, Enum):
    LIBRESPOT = "librespot"
    BLUETOOTH = "bluetooth"
    ROC = "roc"
    RADIO = "radio"
    MY_SOURCE = "my_source"  # ← Add here
```

### 2. Create the plugin

`backend/infrastructure/plugins/my_source_plugin.py`:
```python
from backend.application.interfaces.audio_source_plugin import AudioSourcePlugin
from backend.domain.audio_state import AudioSource, PluginState

class MySourcePlugin(AudioSourcePlugin):
    def __init__(self, state_machine):
        self.state_machine = state_machine
        self.source = AudioSource.MY_SOURCE

    async def initialize(self):
        """Initialization on application startup"""
        # Initial setup
        pass

    async def start(self):
        """Start the service (systemctl start, etc.)"""
        # Notify state change
        await self.state_machine.update_plugin_state(
            self.source,
            PluginState.READY
        )
        return True

    async def stop(self):
        """Stop the service"""
        await self.state_machine.update_plugin_state(
            self.source,
            PluginState.INACTIVE
        )
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

`backend/config/container.py`:
```python
from backend.infrastructure.plugins.my_source_plugin import MySourcePlugin

class Container:
    # ... existing code ...

    my_source_plugin = providers.Singleton(
        MySourcePlugin,
        state_machine=audio_state_machine
    )

    def initialize_services(self):
        # ... existing code ...

        # Register plugin BEFORE async init
        state_machine.register_plugin(
            AudioSource.MY_SOURCE,
            self.my_source_plugin()
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
        { @func getenv vars [ MILO_EQUALIZER ] default "" }
    ]
}

pcm.milo_mysource_direct {
    type plug
    slave.pcm {
        type hw
        card sndrpihifiberry
        device 0
    }
}

pcm.milo_mysource_multiroom {
    type plug
    slave.pcm {
        type hw
        card 1
        device 0
        subdevice 3
    }
}

# ... _eq versions too
```

### 5. Create API routes

`backend/presentation/api/routes/my_source.py`:
```python
from fastapi import APIRouter

def setup_my_source_routes(get_plugin):
    router = APIRouter(prefix="/my_source", tags=["my_source"])

    @router.post("/play")
    async def play():
        plugin = get_plugin()
        result = await plugin.handle_command("play", {})
        return {"status": "success", "data": result}

    return router
```

Register in `backend/main.py`:
```python
from backend.presentation.api.routes.my_source import setup_my_source_routes

my_source_router = setup_my_source_routes(
    lambda: state_machine.plugins.get(AudioSource.MY_SOURCE)
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

### Reference implementation: Radio plugin

The Radio plugin (`backend/infrastructure/plugins/radio/`) is a complete, production-ready reference implementation that demonstrates advanced plugin architecture:

**Multi-component architecture:**
- `plugin.py` - Main plugin class (AudioSourcePlugin implementation)
- `mpv_controller.py` - IPC communication with mpv media player
- `radio_browser_api.py` - External API integration with caching (60min TTL)
- `station_manager.py` - Favorites, custom stations, broken stations management
- `image_manager.py` - File upload handling (JPG, PNG, WEBP, GIF, up to 10MB)

**Key features demonstrated:**
- External API integration (Radio Browser API)
- Service lifecycle management (systemd + IPC socket)
- Complex data persistence (favorites, custom stations in /var/lib/milo/radio_data.json, images in /var/lib/milo/radio_images/)
- File uploads (station images with validation and storage)
- Caching strategy (API responses cached for performance)
- Error handling (broken station detection and filtering)
- Frontend integration (search, filters, modals, screensaver)

**API routes:** 25+ endpoints including search, favorites, custom stations, image uploads
**Frontend components:** RadioSource.vue, RadioScreensaver.vue, AddRadioStation.vue, ChangeRadioStationImage.vue, RadioSettings.vue
**Store:** radioStore.js (Pinia) with full state management

This is an excellent reference for building a complex audio source plugin with external dependencies, data persistence, and rich UI interactions.

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

`backend/tests/test_my_plugin.py`:
```python
import pytest
from backend.infrastructure.plugins.my_source_plugin import MySourcePlugin

@pytest.mark.asyncio
async def test_plugin_initialization():
    # Mock state machine
    class MockStateMachine:
        async def update_plugin_state(self, source, state):
            pass

    plugin = MySourcePlugin(MockStateMachine())
    await plugin.initialize()

    assert plugin.source == AudioSource.MY_SOURCE
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
# ✅ Good: uses update_plugin_state
await state_machine.update_plugin_state(source, state)

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

# Frontend is served by npm preview via systemd
# See milo-frontend.service
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

# Frontend
cd frontend
npm install
npm run build
sudo systemctl restart milo-frontend
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

⚠️ **CRITICAL**: Service initialization order in `container.py` matters!

1. Create service instances
2. **Register plugins** in state machine
3. Resolve circular dependencies (setters)
4. **Run** `container.initialize_services()`
5. Wait for `await container._init_task`
6. Initialize plugins

See detailed comments in `backend/config/container.py`.

### WebSocket broadcasting

Always use `state_machine._broadcast_event()` to propagate changes:

```python
await self.state_machine._broadcast_event(
    category="plugin",
    type="state_changed",
    source=self.source.value,
    data={
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

## Resources

- [Detailed Architecture](architecture.md)
- [GitHub Token Setup](github-token.md)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Vue 3 Docs](https://vuejs.org/)
- [Pinia Docs](https://pinia.vuejs.org/)

## Contact

- **GitHub Issues:** https://github.com/leodurandfr/Milo/issues
- **Discussions:** https://github.com/leodurandfr/Milo/discussions
