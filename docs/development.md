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

    # Per-command parameter contract: command name → Pydantic model (None = no
    # params). command() validates raw input against this at a single boundary
    # before _handle_command() runs, so handlers receive a typed `params`. Param
    # models live in sources/my_source/models.py (pure pydantic/typing leaf).
    COMMANDS = {"play": None, "seek": SeekParams}

    async def _handle_command(self, cmd: str, params):
        """Handle commands on validated params. Only state-dependent checks
        belong here (shape/type/range are already enforced by COMMANDS[cmd])."""
        if cmd == "play":
            return self.success_response("Playing")
        if cmd == "seek":
            return self.success_response(f"Seeked to {params.position_ms}ms")
        return self.error_response(f"Unhandled command: {cmd}")
```

> The other method names above (`initialize`/`start`/`stop`/`get_status`) are a
> simplified sketch — the real `BaseAudioSource` ABC overrides are
> `_do_start`/`_do_stop`/`_get_status`/`_handle_command`. See the CLAUDE.md source
> family table and an existing source (`radio/`) for the authoritative shape.

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

### Frontend (Vitest)

```bash
cd frontend
npm run test            # Watch mode
npm run test:run        # Single run (CI form)
npm run test:coverage   # With coverage
```

> ⚠️ The Vitest suite (`frontend/tests/`) is currently **skipped in CI** — ~97 tests still mock `axios.*` after the apiCall migration. See the *Lint and typing floor* section below.

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

Transitions are protected by `_transition_lock`. State updates arriving while `transitioning` is set are **dropped, not buffered**; the post-start resync in `transition_to_source()` re-reads `source.state`/`source.metadata` to recover the final state. There is no replay queue.

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

## Dev-only symptoms vs production bugs

Milō is a **fixed-purpose Pi appliance**. End users:

- Run the **pre-built** frontend served by nginx from `frontend/dist/`.
- Do **not** rebuild, hot-reload, or keep stale tabs open across deploys.
- Do **not** interact with the Vite dev server (`npm run dev`), HMR, source-map URLs, or `localhost:5173`.

When a developer reports a bug they hit *while developing*, classify the symptom **before** writing any code (summary + decision rule in [CLAUDE.md](../CLAUDE.md)).

**A. Dev-only artifact** — diagnose, explain, **do not modify code**. Telltale signs:

- Caused by a rebuild while a browser tab was already open (stale JS bundle, renamed chunks → 404 on dynamic imports → blank page).
- Errors referencing `localhost:5173`, `192.168.x.x:5173`, `?t=<timestamp>` query strings, or `Vite HMR` / `[vite]` log lines.
- Stale `sessionStorage` / `localStorage` from a prior dev iteration of the schema.
- Service worker / PWA cache pollution from experimentation (the prod build has no SW).
- Anything that disappears with a hard refresh (`⌘ + Shift + R`) or after clearing site data.
- "Page blanche" / "écran blanc" after `npm run build` while a tab was open — almost always the stale-chunk pattern above.

For these: explain *why* it happened, point to the dev workflow that triggered it, and stop. Do **not** add reload guards, version-check loops, or error-handler fallbacks whose only purpose is to mask a developer's mid-session inconsistency — that code would bloat the prod bundle for a scenario no end user will ever create.

**B. Real bug that would also hit production** — fix in code. Telltale signs:

- Reproduces from a clean prod state (fresh boot, nginx-served `dist/`, no dev tools open).
- Triggered by user actions the appliance is built for: connecting AirPlay, selecting a radio station, multi-room handoff, screen sleep, etc.
- Reproduces on the Pi kiosk itself (which always runs the prod build), not just the developer's Mac browser.
- Backend logs (`journalctl -u milo-backend`) or `errors.log` show a server-side trace independent of how the user got there.
- Hardware-related: ALSA routing, CamillaDSP, ROC, Snapcast, rotary encoder, screen brightness — these always count as prod-relevant.

**When in doubt, ask explicitly before implementing**: *"Is this reproducible from a clean prod boot, or only because of your dev session state?"*

## Lint and typing floor

The project ships a lightweight lint floor that mechanically locks the conventions of RFCs 15-21. All rules are **built-in** to standard tools (no custom plugins to maintain). CI ([.github/workflows/lint.yml](../.github/workflows/lint.yml)) blocks merges if any of these fail: `ruff check backend/`, `npm run lint:js`, `npm run lint:css`, `pytest backend/`. The vitest `npm run test:run` step is temporarily skipped — the suite (`frontend/tests/`) still mocks `axios.*` directly after the RFC 17 apiCall migration (~97 stale tests); re-enable once retargeted at `apiCall`.

| Tool | Rule | Source RFC | Activated in |
|---|---|---|---|
| eslint | `no-restricted-imports: axios` | RFC 17 | Lot A — 2026-05-18 |
| eslint | `no-restricted-syntax: console.*` | RFC 17 | Lot A — 2026-05-18 |
| ruff | `S110` (try-except-pass) | RFC 18 | Lot B — 2026-05-18 |
| ruff | `S112` (try-except-continue) | RFC 18 | Lot B — 2026-05-18 |
| stylelint | `color-no-hex` | RFC 21 | RFC 21 PR3 — 2026-05-18 |
| stylelint | `declaration-property-value-disallowed-list` (`rgba\|hsla` on any color property) | RFC 21 | RFC 21 PR3 — 2026-05-18 |
| stylelint | `declaration-property-value-disallowed-list` (typography redefinition in scoped CSS) | RFC 21 + RFC 22 | RFC 21 PR3 — 2026-05-18 |
| eslint | `no-restricted-globals` (bare `setTimeout`/`setInterval`/`clearTimeout`/`clearInterval` in components/composables/views/directives → use `useTimer()`) | §8 Timers | 2026-05-20 |

**Intentional silent swallows** (Python) — use `contextlib.suppress(ExceptionType)` instead of `try: ... except: pass`. The latter trips `ruff S110/S112`; the former is the documented Pythonic idiom and reads as a deliberate, scoped suppression (cleanup paths, idempotent teardown, transient hardware errors in `finally:` blocks).

**Bypassing a legitimate exception** — add a per-line directive with a written justification after `--`:

- Python: `# noqa: S110 -- <reason>` (or the relevant rule code)
- JS / Vue: `// eslint-disable-next-line <rule> -- <reason>`
- CSS / Vue: avoid `// stylelint-disable` inline; extend the design system or whitelist the file in [`frontend/.stylelintrc.cjs`](../frontend/.stylelintrc.cjs).

No file-level or repo-level disabling. No muted `noqa` without a reason.

### Future considerations

- **TypeScript progressive adoption** on stores / composables — deferred to a dedicated RFC.
- **`pyright` strict** on `backend/core/` + `backend/sources/` — deferred.
- **`husky` pre-commit + `lint-staged`** — useful if the team grows to 3+ devs. CI feedback (~5 min) is currently enough for solo dev.
- **Custom eslint plugins** (`no-french-comment`, `no-bare-timer`, `no-raw-ws-event-data`) — not cost-effective at 1-2 devs. Re-arbitrate if recurrence justifies.

## Resources

- [Detailed Architecture](architecture.md)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Vue 3 Docs](https://vuejs.org/)
- [Pinia Docs](https://pinia.vuejs.org/)

## Contact

- **GitHub Issues:** https://github.com/leodurandfr/Milo/issues
- **Discussions:** https://github.com/leodurandfr/Milo/discussions
