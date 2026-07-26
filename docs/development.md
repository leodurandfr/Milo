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
│   ├── equalizer/            # CamillaDSP service + proxy + sync
│   ├── multiroom/            # Snapcast + routing + crossover
│   ├── lyrics/               # LyricsService (LRCLIB + disk cache)
│   ├── connectivity/         # Internet-connectivity monitor (NetworkManager D-Bus)
│   ├── network/              # WiFi scan / connect / saved networks
│   ├── system/               # mDNS hostname-conflict detection
│   └── updates/              # Update + version services
├── sources/                   # Audio source implementations
│   ├── spotify/              # SpotifySource + routes
│   ├── airplay/              # AirPlaySource + metadata_reader + routes
│   ├── bluetooth/            # BluetoothSource + routes
│   ├── mac/                  # MacSource + routes
│   ├── radio/                # RadioSource + routes + browser_api
│   ├── podcast/              # PodcastSource + routes + podcastindex_api
│   ├── cd/                   # CDSource + routes
│   ├── dlna/                 # DlnaSource + metadata_reader (UPnP bridge) + routes
│   ├── qobuz/                # QobuzSource + monitor (qobuz-proxy /api/status poll)
│   └── music_library/        # MusicLibrarySource + navidrome_client + storage + data + routes
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
│   ├── dlna/                 # DLNA source UI
│   ├── equalizer/            # Equalizer / DSP controls
│   ├── lyrics/               # Lyrics app (full-screen synced view)
│   ├── multiroom/            # Multiroom (Snapcast) controls
│   ├── music-library/        # Music Library source UI (browse + tracklist + queue)
│   ├── network/              # Network / WiFi settings
│   ├── podcasts/             # Podcast source UI
│   ├── qobuz/                # Qobuz source UI
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
│   ├── lyricsStore.js        # Lyrics app state (fetch-on-open, per-track cache)
│   ├── musicLibraryStore.js  # Music Library catalog + queue + scan state
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
2. Component calls API (via apiCall)
   ↓
3. Backend route handler
   ↓
4. Service updates state machine
   ↓
5. State machine calls broadcast(event)
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
    DLNA = "dlna"
    QOBUZ = "qobuz"
    MUSIC_LIBRARY = "music_library"
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
# Card 1 `Loopback` is FULL: slot 0 = DSP input, slots 1..7 = the seven original
# sources. snd-aloop caps at 8 substreams/card (kernel limit), so DLNA opened a
# *second* loopback card, `LoopbackDLNA` — but that card is NOT full: it has 8
# subdevices (0..7) and only 3 are taken (0=DLNA, 1=Qobuz, 2=Music Library, see
# install/snapcast.sh). Use the next free subdevice on LoopbackDLNA (3, then 4,
# 5, 6, 7) — do NOT open a third loopback card until all 8 are used.
# Then add a matching `source = alsa:///?...&device=hw:2,1,<subdevice>` line in
# /etc/snapserver.conf and its slug to the `meta:///...` aggregator.
pcm.milo_mysource_multiroom {
    type plug
    slave.pcm {
        type hw
        card LoopbackDLNA
        device 0
        subdevice 3
    }
}
```

Only once all 8 `LoopbackDLNA` subdevices are in use does a new source need a *third* loopback card. Add it in the snd-aloop module options at BOTH install paths (`install/alsa.sh` and `pi-gen/stage-milo/02-install-milo/01-run.sh`):
```
options snd-aloop index=1,2,3 enable=1,1,1 id=Loopback,LoopbackDLNA,LoopbackX pcm_substreams=8,8,8
```

Note: CamillaDSP is always in the audio path for volume control. DSP effects are toggled within CamillaDSP.

### 5. Create API routes

Only families B and C have a `routes.py` — family A sources take commands via
the generic `/api/audio/control/{source}`. Use `make_source_dependency` for the
source lookup and `run_source_command()` for playback commands; never call
`source.handle_command()` from a route body.

`backend/sources/my_source/routes.py`:
```python
from typing import Any, Dict

from fastapi import APIRouter, Depends

from backend.api.route_helpers import run_source_command
from backend.api.source_dependency import make_source_dependency
from backend.sources.my_source.source import MySource

router = APIRouter(prefix="/my_source", tags=["my_source"])
set_source_provider, get_source = make_source_dependency("My source")


def setup_my_source_routes(source_provider) -> APIRouter:
    set_source_provider(source_provider)
    return router


@router.post("/play")
async def play(source: MySource = Depends(get_source)) -> Dict[str, Any]:
    return await run_source_command(source, "play", {}, "Playback")
```

Register in `backend/main.py`:
```python
from backend.sources.my_source.routes import setup_my_source_routes

my_source_router = setup_my_source_routes(
    lambda: state_machine.sources.get(AudioSource.MY_SOURCE)
)
app.include_router(my_source_router, prefix="/api")
```

### 6. Wire the frontend

Most per-source wiring derives from the registry in
`frontend/src/constants/audioSources.js`: adding the id to `ALL_AUDIO_SOURCES`
and its i18n key to `AUDIO_SOURCE_LABEL_KEYS` automatically covers the Zod
source enums (`schemas/api.js`, `schemas/ws.js`), the dock labels (`Dock.vue`,
`DockSettings.vue`), the icon/status prop validators (`AppIcon.vue`,
`AudioSourceStatus.vue`) and the dock-apps map (`settingsStore.js`).

Full checklist (DLNA Phase 3 needed two follow-up commits because some of
these were missed — walk the whole table):

| Touchpoint | What to add |
|---|---|
| `constants/audioSources.js` | id in `ALL_AUDIO_SOURCES` (order = default dock layout) + label key in `AUDIO_SOURCE_LABEL_KEYS` |
| `assets/app-icons/<id>.svg` | dock icon; map the filename in `AppIcon.vue::iconMapping` if it differs from the id (e.g. `mac` → `macos`) |
| `components/<id>/<Name>Source.vue` | source view per the CLAUDE.md family table (family A: none) |
| `components/audio/AudioSourceView.vue` | route the new source's view |
| `composables/useRichDisplay.js` | rich-player gating (families B/C) |
| `components/audio/AudioSourceStatus.vue` | loading/ready status lines (per-source `switch` cases) |
| `locales/*.json` (8 files) | `audioSources.<id>` — `english.json` first (canonical/fallback) |
| `backend/config/constants.py` | id in `DEFAULT_DOCK_APPS` (`VALID_DOCK_APPS` derives from the `AudioSource` enum) |

View-component sketch — HTTP goes through `apiCall`, never raw `fetch`/axios:
```vue
<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { apiCall } from '@/services/apiCall';

const audioStore = useUnifiedAudioStore();
const metadata = computed(() => audioStore.metadata || {});

async function play() {
  await apiCall.post('/api/my_source/play', null, {
    category: 'my_source',
    message: 'Error starting playback',
  });
}
</script>
```

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

### Sidecar source: Qobuz (Family B)

Qobuz Connect (`backend/sources/qobuz/`) is a **Family B** source (passive
receiver, `showControls=false`, like AirPlay/DLNA) backed by a reverse-engineered
sidecar, **qobuz-proxy**. It is the reference for wiring a source whose playback
is driven entirely by an external app:

- **No `routes.py`, no binary artwork.** `QobuzSource` starts `milo-qobuz.service`
  and a `QobuzMonitor` that polls `GET http://127.0.0.1:8689/api/status` (~1 Hz)
  and maps `playing`/`paused` → ACTIVE via `emit_connection_state(...)`, with a
  short idle-grace window so a track change doesn't flash "ready to stream".
  Artwork is a Qobuz CDN URL loaded straight by the kiosk. Position/duration ride
  the same poll (see the patch below), so the player adds
  `AudioPlayerFull :showProgress="true"` — a **read-only** bar above the source
  bar (no seek: there is no local control channel). AirPlay/DLNA report position
  too and can opt in with the same prop; they currently don't.
- **Install is from git, not PyPI.** qobuz-proxy has no PyPI release, so
  [install/qobuz-proxy.sh](../install/qobuz-proxy.sh) creates a venv under
  `/var/lib/milo/qobuz/` and `pip install`s the **pinned git tag**
  (`QOBUZ_PROXY_VERSION`, PEP 508 direct-URL, `[local]` extra for the PortAudio
  backend + `libportaudio2` from apt). It is called from both `install.sh` and the
  pi-gen stage-02 (single source of truth).
- **Two vendored patches**, both in
  [install/qobuz_proxy_patches.py](../install/qobuz_proxy_patches.py) so the
  version-pinned anchors have one definition, shared by the installer and the
  in-app updater (which re-applies them after every pip upgrade): `stream.py`
  pins the local backend to unity gain (CamillaDSP is the sole volume authority,
  flag-gated on the "allow app volume" setting), and `speaker.py` adds
  `position_ms`/`duration_ms` to `now_playing` — the proxy tracks both for its
  cloud state reports but omits them from `/api/status`. Each edit is idempotent
  and aborts loudly on a version bump if its anchors moved.
- **One-time account login.** Unlike Spotify's zeroconf, qobuz-proxy won't
  advertise until a Qobuz account is authenticated. The **Qobuz account** settings
  screen (`components/settings/categories/QobuzSettings.vue`) drives the backend
  relay `backend/api/qobuz_account.py` (`GET /api/qobuz/account`,
  `GET .../login-url`, `POST .../logout`), which proxies qobuz-proxy's OAuth so
  the browser flow stays on `:8689`. The token is cached in
  `/var/lib/milo/qobuz/credentials.json` (per-user, **not** baked into the image).
- **ASCII device name.** `device.name` must be ASCII (`"Milo"`, not `"Milō"`) —
  the Qobuz iOS app silently aborts the Connect handshake on a non-ASCII name.
  Milō's own UI still shows the `audioSources.qobuz` label ("Qobuz").

### Engine + player source: Music Library (Family C)

Music Library (`backend/sources/music_library/`) is a **Family C** source (active
player, `<AudioPlayerFull>` with controls) but the first one split into a catalog
**engine** + a **player** — the reference for a source backed by an external index
and a storage/mount layer. Mental model: **≈ the Podcast source, with Navidrome
standing in for Podcast Index and a mount layer underneath.**

- **Two services, deliberately named differently.**
  `milo-navidrome.service` is the always-on catalog **engine** (tech-named after the
  product, like `milo-camilladsp`), `BindsTo=milo-backend`, owns
  `/var/lib/milo/navidrome`. `milo-music-library.service` is the on-demand **mpv
  player** (source-named, like `milo-podcast`), started on activation. Both exist —
  they are complementary, not alternatives.
- **`navidrome_client.py`** is an async **Subsonic** client (the analog of
  `browser_api.py`), used both by the `/api/music-library/*` browse routes and by
  `source.py` at play time to build bit-perfect `stream?id=…&format=raw` URLs.
  Auth is Subsonic token auth against a single service account provisioned on first
  boot by `milo-navidrome-provision` (milo-owned 0600 cred file, read via
  `NavidromeClient.from_cred_file()`); a missing cred file surfaces as a 503 on
  browse routes and a null status on the polled scan-status route (self-healing).
- **`storage.py` (`StorageManager`) is the only hard part** — Navidrome indexes
  `/media/milo` but never mounts anything. A `pyudev` netlink monitor (unprivileged,
  the analog of the CD disc-watcher) mounts USB partitions read-only via the
  `milo-mount` sudoers helper and triggers a Navidrome `startScan`; SMB/NFS shares
  go the same way but are persisted (`data.py`, versioned JSON — non-secret only)
  and replayed at boot. Fail-open throughout (no udev on a dev host just disables
  auto-mount). CIFS credentials are fed to `milo-mount` on **stdin**, never argv.
- **`source.py` builds a gapless mpv playlist** from any context (album / genre /
  playlist / search): the frontend hands ordered Subsonic song dicts to
  `play_context`, the source maps each id to a stream URL and loads them as one mpv
  native playlist (`--gapless-audio`). Now-playing (title/artist/album/art + queue/
  index/shuffle/repeat) is broadcast as standard source metadata; the frontend
  derives it from `unifiedAudioStore` gated on `active_source === 'music_library'`.
- **Scan-progress UX.** A fresh library scan takes minutes.
  `GET /api/music-library/scan-status` returns `{scanning, count, folderCount}`;
  `MusicLibrarySource.vue` polls it (via `useTimer`) while scanning or while the
  catalog still looks empty, shows a "building library…" state with a live
  indexed-track count, and calls `store.resync()` on the completion edge so the
  catalog appears without a manual refresh.
- **Cover art** is proxied localhost-only behind `/api/music-library/cover/{id}`
  (1-year cache); the frontend never reaches Navidrome directly. Navidrome's online
  metadata/art agents are always enabled (`EnableExternalServices = true` in the
  baked `navidrome.toml` — no user toggle; offline calls fail back silently).
- **Milo-Mac contract:** no change — playback is generic (`/api/audio/control/{source}`)
  and metadata is opaque, so no `/api/music-library/*` route is in the manifest.

## Testing

### Backend (pytest)

```bash
cd backend
python -m pytest                                        # All tests
python -m pytest -v                                      # Verbose
python -m pytest -m unit                                 # Unit tests only
python -m pytest -k "test_name"                          # By name (substring, across files)
python -m pytest tests/test_radio_source.py              # A single file
python -m pytest tests/test_radio_source.py::TestRadioSourceLifecycle::test_start_success  # A single test
python -m pytest --cov=backend --cov-report=term-missing  # Coverage summary
python -m pytest --cov=backend --cov-report=html          # HTML coverage → htmlcov/index.html
python -m pytest --durations=10                           # 10 slowest tests
```

**Writing a test:**

`backend/tests/test_my_source.py`:
```python
import pytest
from unittest.mock import AsyncMock

from backend.sources.my_source.source import MySource
from backend.core.models.audio_state import SourceState


@pytest.fixture
def my_source():
    return MySource(
        config={},
        state_machine=AsyncMock(),
        settings_service=AsyncMock(),
        systemd_manager=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_start_success(my_source):
    result = await my_source.start()

    assert result is True
    assert my_source.state == SourceState.WAITING
```

See `tests/test_radio_source.py` for a fuller example (mocked service manager, command dispatch, lifecycle transitions).

### Frontend (Vitest)

```bash
cd frontend
npm run test            # Watch mode
npm run test:run        # Single run (CI form — blocking)
npm run test:coverage   # With coverage
```

The suite is **blocking in CI**. Run it from `frontend/`, but note it needs the **whole repo checked out**: the structural guardrails read backend sources (`backend/core/models/ws_events.py`, `audio_state.py`).

#### What earns a test

The UI is refactored often, so **tests that mount a component and assert markup or CSS classes are not written here** — they break on every redesign and catch almost nothing. Layout:

| Directory | Holds | Mounts a component? |
|---|---|---|
| `tests/architecture/` | invariants over the app's own structure (`deltaStores` completeness) | no |
| `tests/i18n/` | locale parity, referenced-key and dead-key checks | no |
| `tests/schemas/` | Zod contracts, cross-checked against the backend event models | no |
| `tests/stores/` | store logic: WS deltas, derived state, guards | no |
| `tests/composables/` | composable behaviour (timers, progress interpolation) | a bare host, nothing asserted on the DOM |
| `tests/pure/` | pure functions (`volumeConversion`, music-library `format`) | no |
| `tests/helpers/` | the `apiCall` mock and the backend-source extractors | — |

#### Three rules

**1. Mock `apiCall`, never `axios`.** `services/apiCall.js` is the only module allowed to import axios; a store test that mocks axios asserts through a layer it doesn't own. That mismatch is what silently rotted the previous suite when stores migrated to `apiCall`.

```javascript
import { apiCall } from '@/services/apiCall';
import { resetApiCallMock, ok, fail } from '../helpers/apiCallMock';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

beforeEach(() => resetApiCallMock());

it('refuses to touch a remote client while multiroom is off', async () => {
  apiCall.patch.mockResolvedValueOnce(ok({ status: 'success' }));
  expect(await store.updateClientEqualizerVolume(REMOTE_MAC, -25)).toBe(false);
  expect(apiCall.patch).not.toHaveBeenCalled();
});
```

Assert a URL only where the store *chooses* it (EQ target resolution, `local` vs MAC, zone vs client). On a straight pass-through the assertion only restates a string constant.

**2. Drive the real stores.** `equalizerStore` reads `multiroomStore` and `unifiedAudioStore`; the tests populate them through their own WS handlers rather than mocking them. A mocked sibling store asserts a fixture of its API, and that fixture is what goes stale.

**3. Guardrails must be able to fail.** Anything that derives expectations by parsing another file starts by asserting its own extraction is non-trivial — an empty parse must fail loudly rather than make every later assertion vacuous. Same doctrine as the Milo-Mac contract test. When adding one, verify it goes red against a simulated drift before trusting it green.

#### Backend-derived guardrails

Two tests read the backend and would otherwise need hand-written fixtures:

- `tests/schemas/ws.test.js` — parses every `WsEvent` subclass (resolving inheritance) and, for each `wsEventRegistry` entry, builds the payload **from the model's own fields**, then checks the Zod schema accepts it, requires nothing extra, and ignores no field the backend sends. Catches an added/renamed/retyped field and a deleted event class.
- `tests/schemas/api.test.js` — asserts `ALL_AUDIO_SOURCES` matches the backend `AudioSource` enum exactly. A source added on the backend but not there is coerced to `'none'` on every state update, which reads as "the new source silently does nothing".

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

Always use `state_machine.broadcast()` with a typed `WsEvent` subclass from
`backend/core/models/ws_events.py` (one class per `(category, type)` pair —
the model IS the payload documentation; new event → new subclass):

```python
from backend.core.models.ws_events import SourceStateChanged

await self.state_machine.broadcast(SourceStateChanged(
    source=self.source.value,
    new_state="active",
    metadata=metadata,
))
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

The project ships a lightweight lint floor that mechanically locks the conventions of RFCs 15-21. All rules are **built-in** to standard tools (no custom plugins to maintain). CI ([.github/workflows/lint.yml](../.github/workflows/lint.yml)) blocks merges if any of these fail: `ruff check backend/`, `npm run lint:js`, `npm run lint:css`, `pytest backend/`, `npm run test:run`. The vitest step was skipped for a while after the RFC 17 apiCall migration left the suite mocking `axios.*` through a layer the stores no longer used; it was rebuilt around the `apiCall` boundary plus structural guardrails and is blocking again — see [What earns a test](#what-earns-a-test).

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
