# Developer guide

This guide is for developers who want to contribute to or fork the Milō project.

## Development environment setup

### Prerequisites

- A **Linux** host. The test suites run off the Pi — CI runs them on Ubuntu x86-64. The backend
  keeps its data in `/var/lib/milo` (create it, owned by your user) and is built to fail open where
  a piece of hardware or a system service is missing; the audio path itself only runs on a
  Raspberry Pi 4 or 5 with Raspberry Pi OS 64-bit (Debian Trixie).
- **Python 3.13** — `pyproject.toml` sets `target-version = "py313"` and CI runs 3.13
- **Node.js 24** — the version CI uses
- The system libraries two Python packages load at import: `libdiscid0` and `libcairo2`
  (`sudo apt-get install libdiscid0 libcairo2`)
- Git

Contributions are accepted under the [Contributor License Agreement](https://github.com/leodurandfr/Milo/blob/main/CLA.md);
see [CONTRIBUTING.md](https://github.com/leodurandfr/Milo/blob/main/CONTRIBUTING.md). Milō is
licensed under the [PolyForm Shield License 1.0.0](https://github.com/leodurandfr/Milo/blob/main/LICENSE).

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
│   ├── models/               # Domain models (AudioSource, the AudioState wire, Session/Phase/ServiceState)
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
│   ├── bluetooth/            # BluetoothSource + agent + monitor (BlueALSA) + avrcp (BlueZ)
│   ├── mac/                  # MacSource + log_patterns + mdns (no routes)
│   ├── radio/                # RadioSource + routes + browser_api
│   ├── podcast/              # PodcastSource + routes + podcast_catalog + feed_resolver + rss_parser
│   ├── cd/                   # CDSource + routes
│   ├── qobuz/                # QobuzSource + monitor (qobuz-proxy /api/status poll)
│   ├── tidal/                # TidalSource + controller_socket (tisoc Unix-socket client)
│   └── music_library/        # MusicLibrarySource + navidrome_client + storage + data + routes
├── api/                       # REST API routes
├── hardware/                  # Hardware controllers (rotary, IR remote, BT remote, screen)
├── ws/                        # WebSocket endpoint + connection manager (manager.py)
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
│   ├── audio/                # Shared audio players + source layout
│   ├── airplay/              # AirPlay source UI
│   ├── cd/                   # CD source UI
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
│   ├── tidal/                # Tidal source UI
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
│   ├── fanStore.js           # Fan status / config
│   ├── updatesStore.js       # Update availability + progress
│   └── systemStore.js        # System info
├── composables/              # Vue composables (useSourceProgress, useVolumeThrottle, ...)
├── services/
│   ├── websocket.js          # WebSocket client (auto-reconnect)
│   ├── apiCall.js            # The one HTTP client (wraps axios) for stores
│   └── i18n.js               # Internationalization
├── assets/styles/
│   └── design-system.css     # CSS variables + typography utilities
└── views/
    └── MainView.vue          # Main view (SPA)
```

**Architectural principles:**
- **Composition API**: Code organized by functionality
- **Reactive State**: Pinia + WebSocket sync
- **Single Page App**: `vue-router` declares two routes — the appliance (`/` → `MainView`) and the
  component gallery (`/components`, lazily loaded). Navigation inside the app is state-driven, not
  URL-driven — don't add routes without a deliberate decision to change that model

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
                                 // multiroom | settings | volume | programs | network
  "type": "state",
  "origin": "spotify",           // populated from data.source
  "data": {                      // the whole AudioState, as GET /api/audio/state returns it
    "source": "spotify",
    "switching": false,
    "service": "running",
    "session": { "phase": "playing", "title": "...", "position": { "ms": 45000, "at": 1790270000.25, "rate": 1.0 }, ... },
    "controls": ["pause", "seek", "skip", "next", "prev"],
    ...
  },
  "timestamp": 1790270000.48
}
```

The audio state is never sent in pieces: `source/state` carries all of it whenever a field other than
the playhead changes, `source/position` carries only a playhead discontinuity (a seek, a reading
more than 2 s off the anchor), and `source/session_ended` announces the end of a
session. The fields are listed in the [API overview](api-overview.md#the-audio-state).

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
    QOBUZ = "qobuz"
    TIDAL = "tidal"
    MUSIC_LIBRARY = "music_library"
    MY_SOURCE = "my_source"  # ← Add here
```

The state carries every source's availability, so the new id also needs a field in
`backend/core/models/audio_wire.py::Availability`; the frontend's strict `AudioStateSchema` derives
its keys from `ALL_AUDIO_SOURCES` (step 6).

Pick the source's **family** first — it decides which modules it has, and
`backend/tests/architecture/test_source_conformance.py::FAMILIES` holds every source to it:

| Family | Control | Metadata | Sources | Modules |
|---|---|---|---|---|
| **A** — mute receiver | the sender | none | Mac | `source.py`; no `routes.py` |
| **B** — passive player | the sender | rich | AirPlay, Qobuz | `source.py` + the feed; `routes.py` only for what the sender can't deliver (AirPlay's artwork) |
| **C** — active player | Milō's UI | rich | Spotify, Tidal, Bluetooth, Radio, Podcast, CD, Music Library | `source.py` + networking as needed; `routes.py` for a catalog to browse |

Besides the steps below, a new source touches:

- `backend/sources/my_source/__init__.py`, exporting exactly the source class:
  `__all__ = ["MySource"]`;
- an entry in `FAMILIES` (above), and the source count in
  `backend/tests/architecture/test_session_sources.py::test_the_scan_reaches_every_source_class`;
- its systemd unit, `system/milo-my-source.service`, named by `service_name` in step 2.

### 2. Create the source

`start()`, `stop()`, `release_for_reroute()`, `acquire_after_reroute()`, `command()` and
`refresh_when_idle()` are **public API on the base class — never override them.** Each one is a
message to the source's actor (one mailbox, one task: `core/audio_source.py`), which is what
serializes a source's lifecycle, commands, pause timer and state reads; `command()` validates against
`COMMANDS` before posting. What you implement are the hooks the handlers call: `_do_start` (the only
`@abstractmethod`), plus `_do_stop`, `_handle_command`, `_cleanup`, `_do_restart`,
`_do_release`/`_do_acquire` (what the source does across a multiroom reroute) and
`refresh_metadata` as needed.

**What the source publishes is its view** (`SourceView`), which the base composes from four pure
hooks: `_session_fields(session)` (title, artist, album, artwork, senders, duration_ms),
`_resume_view()` (what play would bring back while no session runs), `_details()` (one of the
`Details` models in `core/models/audio_wire.py`, or `None`) and `_controls()` (the exact command
names the source takes *now* and that would do something — the frontend draws a button iff its
command is listed). Override `availability()` too if something of the source's own can stop it (a
disc, an account, a storage), and call `_availability_changed()` when that answer moves;
connectivity is the state machine's. After changing anything the view reads, call `_publish()`, or
`_publish_changes()` to publish only if the view moved; the actor also runs `_republish_if_moved()`
after every message, so a handler that forgot is caught, not relied on. The playhead is an anchor
kept by the base: `_anchor_position(ms)` on a discontinuity (a seek, a new track),
`_observe_position(ms)` for a reading, which moves the anchor only past 2 s of drift — never publish
a position on a timer. The ends of sessions announce themselves: `end_session(reason)` sends
`source/session_ended`. A source never builds a payload, calls `state_machine.update_source_view`,
or broadcasts a state event.

There is **no `status()`, `get_status()` or `_get_status()`** anywhere in the hierarchy. Status is
broadcast over the WebSocket, never polled — a `GET /<source>/status` route is explicitly forbidden
and `tests/architecture/test_source_conformance.py` fails the build if one appears.

**Start from the skeleton source**,
[`backend/tests/test_skeleton_source.py`](https://github.com/leodurandfr/Milo/blob/main/backend/tests/test_skeleton_source.py):
one file holding a complete source — its `Session` subclass, the declared policies, `COMMANDS` and
`COMMAND_SCOPES`, `_handle_command`, a feed from its player, `_session_fields` and `_controls` — and
the tests that drive it through a real `AudioStateMachine`: a command opens a session, the player's
announcement moves its phase, the boundary refuses what cannot run, and a stop announces the end
before the state that follows. It runs on every `pytest`, so it cannot fall behind the base class.
Copy the class into `backend/sources/my_source/source.py` and its parameter models into
`models.py`, replace its `Player` with the program your source drives, and give it its own
`source_id`.

**If your source plays through mpv** (like Radio, Podcast, CD and Music Library), extend
`shared/mpv_audio_source.py::MpvAudioSource` instead of `BaseAudioSource`, and put it on the
session model the way `radio/` does: a `MpvSession` subclass for what one session owns, the four
declared policies, `_attach_mpv()` then `_listen_to_mpv()` in `_do_start`, and the `_entry_started` / `_entry_ended` /
`_on_playing_tick` hooks. The phase comes from mpv's events and the one-second tick only reads the
playhead while sound plays; never hand-roll a monitor loop. A source with hardware of its own
(the CD's drive) keeps that device state apart from the session and tags the work on it with a
`DeviceToken`, which a stop neither voids nor cuts.

**If a daemon holds the session** (a sender on shairport-sync, a phone on go-librespot or on the
Tidal daemon, an app on qobuz-proxy, the Macs on roc-recv, a device on BlueALSA), Milō does not
open or end it: after each burst of what the daemon announced, hand
`reconcile(DaemonSnapshot(sender, phase))` where the daemon says the session stands — or
`reconcile(None)` when it holds none — and publish if anything moved (`_publish_changes()`).
Declare `SESSION_DAEMON = True` so the base watches the daemon's process (a pidfd; a daemon killed
without a goodbye ends the session `DAEMON_DIED`), and `IDLE_POLICY = IdlePolicy.REQUEST_END` with a
`_request_end()` that asks the daemon for the end: the end comes back through `reconcile`, recorded
as the idle timeout. Open no session before the daemon names a track. `airplay/` is the reference
for a daemon that only announces; `spotify/` for one that can also be asked where it stands (read
that answer once per burst and let it alone decide the phase — a failed read changes nothing);
`tidal/` for one whose feed carries the playhead too (each reading goes to `_observe_position()`,
then one `_publish_changes()` per burst).

The authoritative shape is the family table above plus an existing source — `radio/` is the
reference for family C, `qobuz/` for family B, `mac/` for family A.

### 3. Register in container

`backend/dependencies.py`:
```python
# In _create_service(), one creator in the `creators` dict — the same four
# constructor arguments for every source:
"my_source": lambda: _import("backend.sources.my_source", "MySource")(
    config={},
    state_machine=get_service("audio_state_machine"),
    settings_service=get_service("settings_service"),
    systemd_manager=get_service("systemd_manager")
),

# In initialize_services(), STEP 3 — registered before the async init:
state_machine.register_source(AudioSource.MY_SOURCE, get_service("my_source"))
```
If the source has an async `initialize()`, add it to the parallel init list at the end of
`initialize_services()`, next to `radio_source.initialize()`.

### 4. Add ALSA devices

`rootfs/etc/asound.conf` (installed as `/etc/asound.conf`):
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
# sources. snd-aloop caps at 8 substreams/card (kernel limit), so the others live
# on a *second* loopback card, `LoopbackDLNA` — and that card is NOT full: it has 8
# subdevices (0..7) and only 3 are taken (1=Qobuz, 2=Music Library, 3=Tidal, see
# provisioning/snapcast.sh). Take the free subdevice 0 first, then 4, 5, 6, 7 —
# do NOT open a third loopback card until all 8 are used.
# Then add a matching `source = alsa:///?...&device=hw:2,1,<subdevice>` line to
# the snapserver.conf that provisioning/snapcast.sh writes, and its slug to the
# `meta:///...` aggregator.
pcm.milo_mysource_multiroom {
    type plug
    slave.pcm {
        type hw
        card LoopbackDLNA
        device 0
        subdevice 0
    }
}
```

Only once all 8 `LoopbackDLNA` subdevices are in use does a new source need a *third* loopback card. The snd-aloop module options are written in exactly one place, `pi-gen/stage-milo/02-install-milo/01-run.sh` (`/etc/modprobe.d/snd-aloop.conf`), which today declares two cards:
```
options snd-aloop index=1,2 enable=1,1 id=Loopback,LoopbackDLNA pcm_substreams=8,8
```
A third card extends each list by one entry.

Note: CamillaDSP is always in the audio path for volume control. DSP effects are toggled within CamillaDSP.

### 5. Create API routes

Every command, whatever the family, travels on the generic
`POST /api/audio/control/{source}`. Two kinds of route may run a command anyway: one that composes
several commands in one request (`POST /api/radio/play`), and one a companion app's contract pins
(`POST /api/radio/stop`, Milo-Mac); a new source adds neither. A `routes.py`
exists only for what is not a command: a catalog to browse, favorites, binary artwork; many
sources (Mac, Qobuz, Tidal, Bluetooth) have none. Use `make_source_dependency` for the source
lookup, reach a non-playback service through a property of the source (`source.station_data`,
`source.data_service`), and use `run_source_command()` when a route really must run a command;
never call `_handle_command` from a route body. Paths are kebab-case.

`backend/sources/my_source/routes.py`:
```python
from typing import Any, Dict

from fastapi import APIRouter, Depends

from backend.api.source_dependency import make_source_dependency
from backend.sources.my_source.source import MySource

router = APIRouter(prefix="/my-source", tags=["my-source"])
set_source_provider, get_source = make_source_dependency("My source")


def setup_my_source_routes(source_provider) -> APIRouter:
    set_source_provider(source_provider)
    return router


@router.get("/favorites")
async def favorites(source: MySource = Depends(get_source)) -> Dict[str, Any]:
    return {"status": "success", "favorites": await source.data_service.get_favorites()}
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
`DockSettings.vue`) and the dock-apps map (`settingsStore.js`). `AppIcon.vue` is the
exception: its `iconMapping` drives its own name list and validator, so the id must be added
there.

Full checklist — walk the whole table:

| Touchpoint | What to add |
|---|---|
| `constants/audioSources.js` | id in `ALL_AUDIO_SOURCES` (order = default dock layout) + label key in `AUDIO_SOURCE_LABEL_KEYS` |
| `assets/app-icons/<id>.svg` + `components/ui/AppIcon.vue::iconMapping` | dock icon, and the id → filename entry (e.g. `mac` → `macos`) |
| `components/<id>/<Name>Source.vue` | source view per the family table (family A: none) |
| `constants/audioSources.js::BROWSER_SOURCES` | only if the source has an in-app browser (Radio, Podcast, Music Library, Spotify) |
| `components/audio/AudioSourceView.vue` | route the new source's view |
| `composables/useRichDisplay.js` | rich-player gating (families B/C) |
| `composables/useSourceStatusDisplay.js` | the status card's loading/ready lines |
| `locales/*.json` (8 files) | the key named in `AUDIO_SOURCE_LABEL_KEYS` (camelCase, e.g. `audioSources.musicLibrary`) — `english.json` first (canonical/fallback) |
| `backend/config/constants.py` | id in `DEFAULT_DOCK_APPS` (`VALID_DOCK_APPS` derives from the `AudioSource` enum) |

View-component sketch — the state is read from `unifiedAudioStore.systemState` (the backend's
`AudioState`, replaced whole by every `source/state`), a button exists iff its command is in
`controls`, and HTTP goes through `apiCall` (here inside `sendCommand`), never raw `fetch`/axios:
```vue
<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const audioStore = useUnifiedAudioStore();
// This source's slice of the state: another source's session is not ours.
const isSelected = computed(() => audioStore.systemState.source === 'my_source');
const session = computed(() => (isSelected.value ? audioStore.systemState.session : null));
const controls = computed(() => (isSelected.value ? audioStore.systemState.controls : []));
const isPlaying = computed(() => session.value?.phase === 'playing');

function togglePlay() {
  // POST /api/audio/control/my_source — only a command the source lists now.
  const command = isPlaying.value ? 'pause' : 'resume';
  if (controls.value.includes(command)) audioStore.sendCommand('my_source', command);
}
</script>
```

### Reference implementation: Radio source

The Radio source (`backend/sources/radio/`) is a complete, production-ready reference implementation that demonstrates advanced source architecture:

**Multi-component architecture:**
- `source.py` - Main source class (BaseAudioSource implementation)
- `browser_api.py` - Radio Browser API client (the country list is cached 24 h)
- `data.py` - Favorites, custom stations, data persistence
- `genres.py` - Genre definitions and mapping
- `shazam.py` - Song recognition integration
- `models.py` - Pydantic models

**Key features demonstrated:**
- External API integration (Radio Browser API)
- Service lifecycle management (systemd + IPC socket)
- Complex data persistence (favorites, custom stations in /var/lib/milo/radio_data.json, images in /var/lib/milo/radio_images/)
- File uploads (station images with validation and storage)
- Error handling (broken station detection and filtering)
- Frontend integration (search, filters, modals, full-screen player)

**API routes:** 14 endpoints, including search, favorites, custom stations and image uploads
**Frontend components:** RadioSource.vue, FavoritesView.vue, SearchView.vue, StationCard.vue, SkeletonStationCard.vue
**Store:** radioStore.js (Pinia) with full state management

This is an excellent reference for building a complex audio source with external dependencies, data persistence, and rich UI interactions.

### Sidecar source: Qobuz (Family B)

Qobuz Connect (`backend/sources/qobuz/`) is a **Family B** source (passive
receiver with no commands) backed by a reverse-engineered
sidecar, **qobuz-proxy**. It is the reference for wiring a source whose playback
is driven entirely by an external app:

- **No `routes.py`, no binary artwork.** `QobuzSource` starts `milo-qobuz.service`
  and a `QobuzMonitor` that polls `GET http://127.0.0.1:8689/api/status` (~1 Hz).
  The session belongs to the Qobuz cloud, so the source follows it through
  `reconcile(DaemonSnapshot)`: the session lives while `renderer_active` says the
  app has picked Milō, and its phase is the player's own `player_state` — never
  upstream's folded `status`, whose "idle" is a load, a failure and a departed app
  alike. Artwork is a Qobuz CDN URL loaded straight by the kiosk. Position and
  duration ride the same poll (see the adaptations below); each reading goes to
  `_observe_position()`, so only a jump (a seek in the app, a new track) is
  published. `AudioPlayerFull` draws what the state carries: `controls` is always
  empty (there is no local control channel, so no transport and no seek), and the
  session's duration and position give a **read-only** bar above the source bar.
- **Install is from git, not PyPI.** qobuz-proxy has no PyPI release, so
  [provisioning/qobuz-proxy.sh](https://github.com/leodurandfr/Milo/blob/main/provisioning/qobuz-proxy.sh) creates a venv under
  `/var/lib/milo/qobuz/` and `pip install`s the **pinned git tag**
  (`QOBUZ_PROXY_VERSION`, PEP 508 direct-URL, `[local]` extra for the PortAudio
  backend + `libportaudio2` from apt). The pi-gen stage-02 sources it rather than
  restating the install (single source of truth).
- **Four adaptations**, applied at runtime by the launcher the unit runs,
  [rootfs/usr/local/bin/milo-qobuz](https://github.com/leodurandfr/Milo/blob/main/rootfs/usr/local/bin/milo-qobuz): the
  local backend's stream is held at unity gain (CamillaDSP is the sole volume
  authority, flag-gated on the "allow app volume" setting);
  `position_ms`/`duration_ms` are added to `now_playing` — the proxy tracks both
  for its cloud state reports but omits them from `/api/status`; and
  `player_state`/`renderer_active` are added to the speaker — the player's own
  state (upstream folds loading, failed and released into one `idle`) and
  whether the Qobuz cloud has this speaker as the app's output, which is what
  the source's session follows; and, with the setting off, JOIN_SESSION tells
  the app the volume is the device's (`volumeRemoteControl` DEVICE, which
  upstream sends only for a fixed-volume DLNA renderer) rather than leaving it a
  slider that does nothing. The app reads that only when the sidecar joins, so
  the setting restarts a running sidecar in both directions. They wrap
  methods rather than rewriting installed sources, so nothing is edited inside
  site-packages and the venv matches the released package exactly. `--check`
  answers whether a release still offers what they bind to; the image build runs
  it after `pip install` and the in-app updater before restarting the service,
  and a release that moved one is a rolled-back update. They used to be source
  patches anchored on exact lines, which 1.6.0 broke by reflowing a dict literal
  without touching a single name.
- **One-time account login.** Unlike Spotify's zeroconf, qobuz-proxy won't
  advertise until a Qobuz account is authenticated. The **Qobuz account** settings
  screen (`components/settings/categories/QobuzSettings.vue`) drives the backend
  relay `backend/api/qobuz_account.py` (`GET /api/qobuz/account`,
  `GET .../login-url`, `POST .../logout`), which proxies qobuz-proxy's OAuth so
  the browser flow stays on `:8689`. The token is cached in
  `/var/lib/milo/qobuz/credentials.json` (per-user, **not** baked into the image).
  **Connecting requires Qobuz to be the selected source**, because the sign-in
  page is served by the sidecar and the sidecar runs only while the source is
  selected — `GET .../login-url` answers 409 otherwise, and the screen drops its
  Connect button rather than offering one. The route used to start the unit
  itself, which raised a Connect speaker from a settings screen and left it
  advertising until the next boot. Reading the account and logging out stay
  available from any source: both go through the credentials cache, not `:8689`.
- **ASCII device name.** `device.name` must be ASCII (`"Milo"`, not `"Milō"`) —
  the Qobuz iOS app silently aborts the Connect handshake on a non-ASCII name.
  Milō's own UI still shows the `audioSources.qobuz` label ("Qobuz").

### Engine + player source: Music Library (Family C)

Music Library (`backend/sources/music_library/`) is a **Family C** source (active
player, `<AudioPlayer>` — it has an in-app browser) but the first one split into a catalog
**engine** + a **player** — the reference for a source backed by an external index
and a storage/mount layer. Mental model: **≈ the Podcast source, with Navidrome
standing in for the podcast catalogue and a mount layer underneath.**

- **Two services, deliberately named differently.**
  `milo-navidrome.service` is the catalog **engine** (tech-named after the product,
  like `milo-camilladsp`), running while the dock enables Music Library — started by
  the backend at init and on the dock toggle, never at boot — `PartOf=milo-backend`, owns
  `/var/lib/milo/navidrome`. `milo-music-library.service` is the on-demand **mpv
  player** (source-named, like `milo-podcast`), started on activation. Both exist —
  they are complementary, not alternatives.
- **`navidrome_client.py`** is an async **Subsonic** client (the analog of
  `browser_api.py`), used both by the `/api/music-library/*` browse routes and by
  `source.py` at play time to build bit-perfect `stream?id=…&format=raw` URLs.
  Auth is Subsonic token auth against a single service account provisioned on first
  boot by `milo-navidrome-provision` (milo-owned 0600 cred file, read via
  `NavidromeClient.from_cred_file()`); a missing cred file surfaces as a 503 on
  browse routes (self-healing — the cred file is re-read on every attempt).
- **`storage.py` (`StorageManager`) is the only hard part** — Navidrome indexes
  `/media/milo` but never mounts anything. A `pyudev` netlink monitor (unprivileged,
  the analog of the CD disc-watcher) mounts USB partitions read-only via the
  `milo-mount` sudoers helper and triggers a Navidrome `startScan`; SMB/NFS shares
  go the same way but are persisted (`data.py`, versioned JSON — non-secret only)
  and replayed at boot. Fail-open throughout (no udev on a dev host just disables
  auto-mount). CIFS credentials are fed to `milo-mount` on **stdin**, never argv.
- **`shares.py` (`NetworkShareService`) owns where the music comes from** — it holds
  `data.py` (config), `storage.py` (mounts) and `libraries.py` (Navidrome libraries)
  together, so "config first, then mount, then library" is one decision rather than
  an ordering every caller must remember. It runs the boot remount and its bounded
  catch-up retry (a NAS often boots slower than the Pi), exposes the uniform
  `storages()` list the UI filters by, and `routes.py` reaches it as `source.shares`
  — the same shape as radio's `source.station_data`. The audio source keeps no share
  state.
- **`libraries.py` + `navidrome_admin.py` give each mount its own Navidrome library**,
  which is what makes "browse this key only" expressible: `musicFolderId` is the sole
  scoping handle the catalog offers, and only the *native* admin API (JWT, not
  Subsonic) can create a library. `libraries.py` is a **reconciler** — `StorageManager`
  calls it after every mount/unmount with the full desired set, so a missed event heals
  on the next one instead of leaving a queue to replay. Four things to know before
  touching it. **Every native-API PUT replaces the whole record** — `/api/user/{id}`
  accepts a partial one and blanks the rest (sending only `libraryIds` empties
  `userName`, which locks the appliance out of its own catalog, Subsonic included),
  while `/api/library/{id}` rejects it (`400 path required`), so a name-only rename
  silently never happens; read-then-merge is the rule on both. Library **1 cannot be
  deleted**, which is why `MusicFolder` points at an empty directory and why the
  reconciler only manages libraries under the mount root. An offline share keeps its
  library while an unplugged key loses its. And **two browse calls ignore
  `musicFolderId`** where the rest honour it: `getGenres`, answered instead from the
  storage space's own album catalog (`source.genres_in_scope`) — the genre
  drill-down *is* scoped, so a genre from another storage would open an empty view —
  and `getPlaylists`, narrowed by `source.playlists_in_scope`, which trusts the
  storage space recorded at creation (the only thing that can place an *empty*
  playlist) and otherwise places a playlist by its first track's album. `getStarred2`
  needs none of this: it takes the scope and honours it.
- **`source.py` builds a gapless mpv playlist** from any context (album / genre /
  playlist / search): the frontend hands ordered Subsonic song dicts to
  `play_context`, the source maps each id to a stream URL and loads them as one mpv
  native playlist (`--gapless-audio`). The playing track is the state's `session`
  (or its `resume` for 600 s after it stops), and the queue, its index, shuffle
  and the current track/album/artist ids are its `details`
  (`kind: "music_library"`). `musicLibraryStore` derives now-playing from
  `unifiedAudioStore.systemState` gated on `source === 'music_library'`; the
  queue running out is a `source/session_ended` with `reason: "eof"`, which no
  frontend store needs today (the state that follows drops the session).
- **Scan-progress UX.** A first index takes minutes (18 for a 10 000-track iPod);
  a rescan of an already-indexed catalog does not (401 ms over 12 488 tracks), so
  only *new* files ever cost time. Nothing polls: `shares.py::_watch_scan` observes
  Navidrome once for the whole appliance and broadcasts `{storages, scanning}` on
  `source/storages_changed`, and `musicLibraryStore` reloads its cached lists on
  the completion edge. Two cadences plus a kick — 15 s idle, 3 s while scanning,
  and an immediate wake whenever a mount changes or a refresh is requested,
  because a quick scan would otherwise begin and end between two idle polls and
  no client would ever see it. Progress is the storage space's own track count,
  never Navidrome's global one (frozen for the duration of a scan).
- **Cover art** is proxied localhost-only behind `/api/music-library/cover/{id}`
  (1-year cache); the frontend never reaches Navidrome directly. Navidrome's online
  metadata/art agents are always enabled (`EnableExternalServices = true` in the
  baked `navidrome.toml` — no user toggle; offline calls fail back silently).
- **Milo-Mac contract:** Milo-Mac browses the library itself, so its manifest pins
  the `/api/music-library/*` routes it calls and the `play_context` body it sends
  on `/api/audio/control/music_library` — whose `tracks` are the song dicts as the
  backend served them, a round trip, so a song field Milo-Mac never reads is still
  not free to drop — and, in the state, `details.music_library.track_id`. Read the
  manifest for the list.

## Testing

### Backend (pytest)

```bash
cd backend
python -m pytest                                        # All tests (-v is the default)
python -m pytest tests/architecture/                     # One directory — the suite has no markers
python -m pytest -k "test_name"                          # By name (substring, across files)
python -m pytest tests/test_radio_source.py              # A single file
python -m pytest tests/test_radio_source.py::TestRadioSourceLifecycle::test_selecting_starts_the_unit_and_attaches_to_mpv  # A single test
python -m pytest --durations=10                           # 10 slowest tests
```

The satellite app has its own suite, run from the repository root as a separate invocation —
`pytest milo-client/` (a combined `pytest backend/ milo-client/` loses `backend/pytest.ini`).

There is **no backend coverage tooling**: `pytest-cov` is deliberately not a dependency, and CI has
no coverage threshold. Coverage as a target rewards exactly the tests this project excludes (see
*What earns a test* below).

**Writing a test:** the skeleton source's tests
([`backend/tests/test_skeleton_source.py`](https://github.com/leodurandfr/Milo/blob/main/backend/tests/test_skeleton_source.py))
are the minimal shape of a source test — a real `AudioStateMachine`, the outside world as a double,
and assertions on the state every client reads, never on the source's private attributes.
See `tests/test_radio_source.py` for a fuller example (mocked service manager, command dispatch, lifecycle transitions).

**Generated reference pages.** `docs/reference/` (REST operations, WebSocket events, the audio state,
source commands, supported hardware) is written by a script from the code, and
`tests/architecture/test_reference_docs.py` fails when a committed page no longer matches it. A change
to a route, an event, a model, a command or `hardware/registry.py` therefore comes with:

```bash
python tools/docs/generate_reference.py            # rewrite the pages; --check only reports
```

Only the **first paragraph** of a class docstring is published, so write it for an integrator — what
the event or model is — and keep consumers, measurements and history for the paragraphs after it.
The same test fails when a published paragraph names a frontend store, a companion app or a date.

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
  expect(await store.setClientVolume(REMOTE_MAC, -25)).toBe(false);
  expect(apiCall.patch).not.toHaveBeenCalled();
});
```

Assert a URL only where the store *chooses* it (EQ target resolution, `local` vs MAC, zone vs client). On a straight pass-through the assertion only restates a string constant.

**2. Drive the real stores.** `equalizerStore` reads `multiroomStore` and `unifiedAudioStore`; the tests populate them through their own WS handlers rather than mocking them. A mocked sibling store asserts a fixture of its API, and that fixture is what goes stale.

**3. Guardrails must be able to fail.** Anything that derives expectations by parsing another file starts by asserting its own extraction is non-trivial — an empty parse must fail loudly rather than make every later assertion vacuous. Same doctrine as the Milo-Mac contract test. When adding one, verify it goes red against a simulated drift before trusting it green.

#### Backend-derived guardrails

Two tests read the backend and would otherwise need hand-written fixtures:

- `tests/schemas/ws.test.js` — parses every `WsEvent` subclass (resolving inheritance) and, for each `wsEventRegistry` entry, builds the payload **from the model's own fields**, then checks the Zod schema accepts it, requires nothing extra, and ignores no field the backend sends. Catches an added/renamed/retyped field and a deleted event class.
- `tests/schemas/api.test.js` — asserts `ALL_AUDIO_SOURCES` matches the backend `AudioSource` enum exactly. A source added on the backend but not there makes every state that names it fail the strict `AudioStateSchema`, which refuses the state whole and keeps the last good one.

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

Transitions are protected by `_transition_lock`. Views a source publishes while `transitioning` is set are **dropped, not buffered**; the post-start resync in `transition_to_source()` re-reads `source.view` to recover the final state. There is no replay queue. A failed start is sticky the same way: while `service` is `failed`, every publish is dropped until a start succeeds.

```python
# ✅ Good: a source publishes its view; the state machine composes the state
self._publish()

# ❌ Bad: directly modifies state
state_machine.system_state.active_source = source
```

## Best practices

### Backend

1. **Always use async/await** for I/O
2. **Appropriate logs**:
   - `logger.debug()`: Detailed info (not in prod)
   - `logger.info()`: Important info
   - `logger.warning()`: Non-blocking issue
   - `logger.error()`: Blocking error

3. **Error handling** depends on the layer:

| Layer | Policy |
|---|---|
| HTTP route | `api_error_handler` or `run_source_command` (`api/route_helpers.py`); no bare try/except in the body |
| Service | log, then raise; a legitimate fallback uses `@handle_errors(default=…, level='error')` |
| Background loop | wrap the loop *body* in `try/except Exception` + an error log + `continue` |
| Best-effort hardware/external call | catch the specific exception, log a warning, fall back to a named value |

   An intentional silent swallow is `contextlib.suppress(Type)`, never a bare `except: pass`.

4. **No ad hoc `sudo`.** systemd and power actions go through `SystemdServiceManager`, privileged file
   work through the `milo-*` helpers in `rootfs/usr/local/bin/`; each command is granted by name in
   `rootfs/etc/sudoers.d/`, and `tests/contracts/test_privileged_exec_contract.py` checks both
   directions.

### Frontend

1. **Composition API** instead of Options API
2. **Computed properties** for derived data
3. **No direct DOM manipulation** (use Vue refs)
4. **Debounce** frequent events with the project's own composable — there is no `lodash` dependency,
   and a bare `setTimeout` is blocked by `no-restricted-globals`. It returns `{ debounced, cancel }`
   and clears its pending timer on unmount by itself:
```javascript
import { useDebounce } from '@/composables/useDebounce';

const { debounced: debouncedSearch, cancel: cancelSearch } = useDebounce((query) => {
  // Logic
}, 300);   // delay defaults to 400 ms
```

5. **No WebSocket subscription in a component.** Events are subscribed in `App.vue` only and
   dispatched into Pinia stores; a component reacts to store state
   (`tests/architecture/wsWiring.test.js`).
6. **Timers through `useTimer()`**, which clears every active timer on unmount:
```javascript
import { useTimer } from '@/composables/useTimer';

const timer = useTimer();
timer.setTimeout(() => { /* … */ }, 600);
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

A tag is the only way a version reaches a unit. Pushing one runs
`.github/workflows/build-image.yml`, which builds the frontend once with
`npm ci`, bakes that artefact into the image, and publishes a GitHub Release
carrying three files:

| Asset | Read by |
|---|---|
| `<build date>-milo.img.xz` (e.g. `2026-10-23-milo.img.xz`) | whoever flashes a card |
| `milo-frontend-<tag>.tar.gz` | every unit's OTA update, via the `frontend_asset_url` entry in `core/updates/catalog.py` |
| `milo-frontend-<tag>.tar.gz.sha256` | the same update, before it swaps `frontend/dist` |

**The tag's annotation is the release page.** The workflow reads it with
`git tag -l --format='%(contents)'` and passes it as the release body; it asks
GitHub for no generated notes, which would add a commit list under a "New
Contributors" heading. So write the annotation as the page you want people to
read — what the image installs, the dependency set, what was measured, and what
is knowingly left undone.

Which means it is Markdown, and **`git tag -a` strips every line beginning with
`#` unless you say otherwise** — its default cleanup treats them as comments.
A heading written `## Known gaps` vanishes between the editor and the tag
object, silently, and the release page is published without it. Always:

```bash
git tag -a v1.2.0 --cleanup=verbatim -F notes.md
git push origin v1.2.0
```

Check what actually landed before pushing — `git tag -l --format='%(contents)' v1.2.0`
is exactly what the workflow will read.

**Every release lists its API changes.** Integrators build on the contracted surface (see
[Stability](integration.md#stability)), and the release notes are where they learn that it moved.
Generate the section from the two tags — on a unit or any host with the backend's virtualenv:

```bash
python tools/release/api_diff.py v1.1.0 v1.2.0 > api-changes.md   # or v1.1.0 HEAD before tagging
```

It compares the OpenAPI schemas, the WebSocket events and the source commands of the two trees,
and files each difference under *Contracted* or *Internal*. It sees shapes, not behavior: a key
dropped from an answer that had no schema, or a field whose meaning changed under the same type, is
added by hand.
The notes follow one template:

```markdown
One paragraph: what this release is about.

## What's new

- …

## Fixes

- …

## API and integration changes

<the output of tools/release/api_diff.py, then anything it cannot see>

## Dependencies

<each line of dependencies.env that moved since the previous release: name, old → new>

## Verified on hardware

<which set of docs/manual/verification-checklist.md was run, on which units; say so if it was cut short>

## Known gaps

- …
```

**A tag carrying a suffix is a pre-release** — `v1.2.0-rc1`, `v1.3.0-beta.2`.
The workflow publishes it with `prerelease: true`, so it never becomes
`releases/latest`, and the backend refuses to offer a non-plain tag anyway
(`helpers.is_stable_release`). Consequences worth knowing before cutting one:

- **no unit in the field is ever shown it.** That is the point: an rc is
  installed on a unit somebody is watching, by somebody who chose it.
- **the update button will not install it either**, for the same reason. On the
  test unit, drive `UpdateService._update_milo_app` with the rc's status
  directly — same checkout, same asset, same reboot, only the offer is skipped.
- **a unit running an rc is not a development build.** It sits on a tag, says
  so on the update screen, and is offered the stable release the channel
  publishes — which is how it gets back.

Withholding and withdrawing are the same one gesture, on GitHub, with no unit
visited: a release that is never published is offered to nobody, and one that is
un-published (or marked pre-release) stops being `releases/latest`, so every
unit that took it is offered the return on its next check.

**A build that fails publishes nothing.** The release step sits after the image
inspection (`pi-gen/inspect-image.sh`, which checks the checkout sits exactly on
the tag and that the frontend artefact landed), so a red build is itself the
withholding mechanism.

### Validating a dependency bump

Every upstream version Milō ships is declared once, in **`dependencies.env`** at the repo
root — read by `provisioning/`, by `pi-gen/stage-milo/` and by the backend's update flow, so a
bump is a one-line edit and there is nowhere else it can disagree. That single line is also
the only thing standing between an upstream release and every unit: a Milō update installs
the app *and* the set declared with it, and nothing else is ever offered.

So "validated" has to mean something. It means **a person watched this version do the thing
the dependency exists for, on a unit** — not that it compiled, and not that audio came out.

On **2026-07-14** the updater compiled shairport-sync 5.1 from 4.3.7. It built, it ran, it
played AirPlay audio perfectly, and it delivered **no track metadata at all** — no title, no
artist, no album, no cover. The player fell back to the plain source card. No error, no log
line, nothing to notice. "Audio plays" would have passed it.

#### The procedure

1. **Try it first, from the Update Manager.** When upstream is ahead of the manifest the row
   offers that release directly, and installing it uses the exact flow a bump would — the same
   download, the same compile, the same rollback. The unit is then off-pin: the row says so,
   the return to the validated version is one click away, and the trial (recorded in
   `updates.forced_versions`) survives Milō updates until you end it or the manifest catches
   up. This is where a version fails, and it costs nothing to find out: a release that fails the
   update's own verification is rolled back.
2. Edit the one line in `dependencies.env`. Nothing else — a version literal anywhere else
   fails CI (`backend/tests/architecture/test_dependency_manifest.py`). The override is dropped
   on its own once the manifest declares that version, so nothing is left to clean up.
3. If you skipped step 1, install the version on a unit through the in-app update, or flash an
   image built with the new manifest. Do not hand-build it: what you are validating includes the
   way Milō installs it.
4. Run the dependency's row below **and** the ~10-minute smoke set from
   [docs/manual/verification-checklist.md](https://github.com/leodurandfr/Milo/blob/main/docs/manual/verification-checklist.md).
5. Commit the bump on its own, saying which unit and which rows were run. A bump folded into
   a feature commit cannot be reverted alone, and reverting is the whole point of pinning.

#### What to watch, per dependency

The column that matters is the third: the thing that has broken before, or the thing whose
absence is silent. A dependency whose feature is *visible* fails loudly; the ones below are
the ones that do not.

| Dependency | Exercise | The check that is **not** "it works" |
|---|---|---|
| `SHAIRPORT_SYNC_VERSION` | Play from an iPhone (Music, then Spotify) and a Mac | **Title, artist, album and cover reach the player.** This is the 5.1 case; audio proves nothing — and since 5.6 they come over D-Bus (`busctl get-property org.gnome.ShairportSync /org/gnome/ShairportSync org.gnome.ShairportSync.Client NowPlayingInformation`), so a renamed property is the same silence. Then the sender obeys pause/next/prev and shuffle/repeat from Milō, and the two volume sliders follow each other. Also confirm AirPlay 2 (not 1) still negotiates — that is `nqptp` and the `--with-airplay-2` build flag together |
| `NQPTP_VERSION` | Play from an iPhone, then leave it playing ~5 min | No drift and no re-sync stutter. NQPTP is the AirPlay 2 clock; a bad one degrades slowly rather than failing. It has no row: the shairport-sync update installs it, so a bump of it alone reaches no running unit until shairport-sync moves too, and a shairport-sync whose shared memory interface it does not speak is refused before anything stops — `nqptp -V` prints the SMI, `nqptp-shm-structures.h` declares it in both repos. 5.6 needs 1.2.9 (smi11); bump both lines in one commit |
| `GO_LIBRESPOT_VERSION` | Hand off from the Spotify app | Artwork + title + artist appear **and change on track change**; the device still appears in the picker after a source switch (zeroconf via Avahi, not the embedded responder — see `provisioning/go-librespot.sh`) — and the error wording `sources/spotify/source.py::login_failure` and `_handle_log_line` match on still exists in the new release (grep it in the tag's source): a renamed message leaves a refused login with no banner, silently. **The browser couples to more of it:** the `state.json` shape (`credentials.{username,data}`, which `SpotifySource` reads and writes), the `credentials`/`metadata` config keys it manages, `/context/tracks`' readiness fields (`ready`, `cached`, `length`), `/library/playlists` paging, `/library/liked`, the playlist writes (`/library/playlists/contains`, `add_tracks`, `remove_track` by position), and `POST /token` with spclient's `user-profile-view` — browse a playlist and Liked Songs, switch profile, read a profile's picture, add a track to a playlist from its ⋯ menu and remove it again. **And to its volume:** `volume`/`volume_steps` in `/status` and `POST /player/volume` accepted with no session (0.10.5), which the app-volume setting stands on — with it on, move the phone's slider and Milō's knob, each must follow the other, and a new cast must open at Milō's level |
| `CAMILLADSP_VERSION` | Change volume, switch an EQ preset | Volume attenuates **in CamillaDSP** and the card mixer stays at unity; a preset change is audible; `bypass_effects()`/`restore_effects()` still round-trip. Check `/proc/asound/card0/pcm0p/sub0/status` reads `RUNNING`, not `PAUSED` — the silence-pause failure leaves no log line |
| `SNAPCAST_VERSION` | Play to a satellite | Both rooms stay in sync for minutes, not seconds. Needs a **second Pi**; this row cannot be run on one unit |
| `NAVIDROME_VERSION` | Browse and play the Music Library | The catalog is complete after a rescan, and a track from an *unmounted* share fails visibly rather than streaming a 200 + JSON error body |
| `QOBUZ_PROXY_VERSION` | Play from the Qobuz app | The device still appears under its ASCII name, **and the progress bar moves** — Milō adds position/duration to a status API that omits them, and the update refuses a release that stopped offering what it binds to (`milo-qobuz --check`). Needs a **paid Qobuz account** |
| `BLUEZ_ALSA_VERSION` | Pair a phone, play, press pause on the phone | Audio, **and** the AVRCP transport: the pause button follows, title/artist arrive. A2DP without AVRCP looks like a working source with an empty card |
| `ROC_TOOLKIT_VERSION` | Stream from the Mac | Audio arrives with no dropouts. Note that roc-vad sends silence continuously, so "connected" never means "playing" — judge it by ear. Needs a **Mac** |

Four of these need hardware a single developer unit does not have (a second Pi, a Mac, a
Qobuz account, a phone). Say so in the commit rather than implying the row was run: an
unstated shortcut is indistinguishable from a skipped check.

### Updating an installation

A unit installs **releases**, not branches, and it does not build a frontend.
`UpdateService._update_milo_app` fetches the tags, checks out the one
`releases/latest` names, and installs the `dist/` that release published — the
artefact CI built once, verified against its `sha256` sidecar. Pressing Update
in the settings screen is the supported path; there is nothing to do by hand.

What a *development* checkout does is a different thing, and the update screen
says so rather than offering a button: a tree that is not sitting exactly on a
tag reads as a development build.

```bash
cd ~/milo
git pull origin main

source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart milo-backend

# The frontend, for development only. A release never builds one here.
cd frontend
npm ci && npm run build
```

## Debugging

### Backend

**Live logs:**
```bash
sudo journalctl -u milo-backend -f
```

**Debug mode:** the log level is the `MILO_LOG_LEVEL` environment variable (`DEBUG`, `INFO`,
`WARNING`…; `WARNING` when unset, `INFO` in the shipped unit).
```bash
MILO_LOG_LEVEL=DEBUG python main.py        # dev server
sudo systemctl edit milo-backend           # on a unit: a drop-in, kept across updates
#   [Service]
#   Environment=MILO_LOG_LEVEL=DEBUG
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

**Logging:** `console.*` is banned by eslint outside the logger itself — use the logger, which adds
the category prefix and central handling:
```javascript
import { logger } from '@/services/logger';

logger.debug('audio', 'Store state', audioStore.$state);
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

Commit messages are in **English**, written `type(scope): summary` — for example
`fix(radio): the station on screen follows its record`. The scope is the area touched
(`radio`, `multiroom`, `api`, `ci`…).

- `feat` New feature
- `fix` Bug fix
- `refactor` Refactoring (no functional change)
- `perf` Performance
- `style` Visual or CSS change
- `docs` Documentation only
- `test` Adding/modifying tests
- `build` Dependencies, packaging, the image
- `chore` Maintenance

### PR checklist

- [ ] The full CI floor is green — there is no formatter in this project (no Black, no Prettier);
  `ruff` is the Python linter and eslint/stylelint the JS/CSS ones:

  ```bash
  ruff check backend/ milo-client/ && pytest backend/ && pytest milo-client/
  cd frontend && npm run lint:js && npm run lint:css && npm run test:run
  ```

- [ ] Documentation updated if necessary
- [ ] A change to the audio path, a source or hardware: the smoke subset of
      [the verification checklist](https://github.com/leodurandfr/Milo/blob/main/docs/manual/verification-checklist.md)
      run on a unit — or, without one, say so in the PR, so it is run before merging

## Important points

### Initialization order

`initialize_services()` in `dependencies.py` runs four steps:

1. Retrieve service instances (triggers lazy creation via `get_service()`)
2. Resolve circular dependencies via setters
3. **Register sources** in the state machine, then (3b) write the env files the source units read
4. **Parallel async initialization** via `asyncio.gather()`

Only three things in it are order-sensitive: the registry-subscription block at the end of step 2
(subscribers are notified in subscription order), step 3 before the async init, and step 3b
before any source unit starts. Every other line of step 2 commutes. The function's docstring is
the reference.

### WebSocket broadcasting

Always use `state_machine.broadcast()` with a typed `WsEvent` subclass from
`backend/core/models/ws_events.py` (one class per `(category, type)` pair —
the model IS the payload documentation; new event → new subclass):

```python
from backend.core.models.ws_events import SourceError, SourceErrorReason

await self.state_machine.broadcast(SourceError(
    source=self.source.value,
    reason=SourceErrorReason.STREAM_LOAD_FAILED,
))
```

The audio state is the exception: nothing constructs `AudioStateChanged` or `SourcePosition` by
hand. A source calls `_publish()` and the state machine's `publish_state()` decides which of the two
goes out (and `BaseAudioSource.broadcast_error()` wraps the example above).

### Settings persistence

Settings modifications must go through `SettingsService`:

```python
# ✅ Good
await settings_service.set_setting('volume.limit_max_db', -20.0)

# ❌ Bad (not persisted)
settings['volume']['limit_max_db'] = -20.0
```

## GitHub Token (Optional)

Milō checks GitHub API for dependency updates. Without a token: 60 req/hour. With token: 5000 req/hour.

### Setup

1. Create a token at https://github.com/settings/tokens → **"Generate new token (classic)"**
2. Select **no scope**: reading public releases needs none, and every scope grants more than that
   (`public_repo` grants *write* access to public repositories)
3. Add it to the backend unit through a drop-in — `/etc/systemd/system/milo-backend.service` itself
   is replaced by every Milō update:

```bash
sudo systemctl edit milo-backend
```

and add:
```ini
[Service]
Environment="GITHUB_TOKEN=ghp_YourTokenHere"
```

4. Restart:
```bash
sudo systemctl restart milo-backend
```

5. Verify:
```bash
sudo journalctl -u milo-backend -b | grep "GitHub token"
# Logged once per backend start:
#   "GitHub token detected - using authenticated API (5000 req/hour)"
#   "No GitHub token - using anonymous API (60 req/hour)"
```

## Dev-only symptoms vs production bugs

Milō is a **fixed-purpose Pi appliance**. End users:

- Run the **pre-built** frontend served by nginx from `frontend/dist/`.
- Do **not** rebuild, hot-reload, or keep stale tabs open across deploys.
- Do **not** interact with the Vite dev server (`npm run dev`), HMR, source-map URLs, or `localhost:5173`.

A bug met *while developing* is first classified, because some cannot happen on a unit:

**A. Dev-only artifact** — not a bug; no code change. Telltale signs:

- Caused by a rebuild while a browser tab was already open (stale JS bundle, renamed chunks → 404 on dynamic imports → blank page).
- Errors referencing `localhost:5173`, `192.168.x.x:5173`, `?t=<timestamp>` query strings, or `Vite HMR` / `[vite]` log lines.
- Stale `sessionStorage` / `localStorage` from a prior dev iteration of the schema.
- Service worker / PWA cache pollution from experimentation (the prod build has no SW).
- Anything that disappears with a hard refresh (`⌘ + Shift + R`) or after clearing site data.

Reload guards, version-check loops or error-handler fallbacks whose only purpose is to mask a
mid-session inconsistency are not accepted: they would ship in the production bundle for a
scenario no end user can create.

**B. Real bug that would also hit production** — fix in code. Telltale signs:

- Reproduces from a clean prod state (fresh boot, nginx-served `dist/`, no dev tools open).
- Triggered by user actions the appliance is built for: connecting AirPlay, selecting a radio station, multi-room handoff, screen sleep, etc.
- Reproduces on the Pi kiosk itself (which always runs the prod build), not just in a desktop browser.
- Backend logs (`journalctl -u milo-backend`) or `errors.log` show a server-side trace independent of how the user got there.
- Hardware-related: ALSA routing, CamillaDSP, ROC, Snapcast, rotary encoder, screen brightness — these always count as prod-relevant.

The deciding question: *is it reproducible from a clean prod boot, or only from the dev session's state?*

## Lint and typing floor

CI ([.github/workflows/lint.yml](https://github.com/leodurandfr/Milo/blob/main/.github/workflows/lint.yml)) blocks merges if any of these fail: `ruff check backend/ milo-client/`, `pytest backend/`, `pytest milo-client/`, `npm run lint:js`, `npm run lint:css`, `npm run test:run`. All rules are **built-in** to standard tools — no custom plugins.

| Tool | Rule |
|---|---|
| eslint | `no-restricted-imports`: no `axios` outside `services/apiCall.js` |
| eslint | `no-restricted-syntax`: no `console.*` outside `services/logger.js`, `main.js` and `schemas/api.js`; no global `$t` |
| eslint | `no-restricted-globals`: no bare `setTimeout`/`setInterval`/`clearTimeout`/`clearInterval` in components, composables, views and directives → use `useTimer()` |
| ruff | `S110` (try-except-pass), `S112` (try-except-continue) |
| ruff | `F`, the pyflakes family (`F401` unused import, `F811` redefinition, `F821` undefined name, `F841` unused variable, `F541` f-string without placeholders…) |
| stylelint | `color-no-hex` in scoped `.vue` styles |
| stylelint | `declaration-property-value-disallowed-list`: no `rgb()`/`rgba()`/`hsl()`/`hsla()` on a color property |
| stylelint | `declaration-property-value-disallowed-list`: no typography redefinition in scoped CSS (use the utility classes) |

**Intentional silent swallows** (Python) — use `contextlib.suppress(ExceptionType)` instead of `try: ... except: pass`. The latter trips `ruff S110/S112`; the former is the documented Pythonic idiom and reads as a deliberate, scoped suppression (cleanup paths, idempotent teardown, transient hardware errors in `finally:` blocks).

**Bypassing a legitimate exception** — add a per-line directive with a written justification after `--`:

- Python: `# noqa: S110 -- <reason>` (or the relevant rule code)
- JS / Vue: `// eslint-disable-next-line <rule> -- <reason>`
- CSS / Vue: avoid `// stylelint-disable` inline; extend the design system or whitelist the file in [`frontend/.stylelintrc.cjs`](https://github.com/leodurandfr/Milo/blob/main/frontend/.stylelintrc.cjs).

No file-level or repo-level disabling. No muted `noqa` without a reason.

**The one `per-file-ignores` entry**, in [`pyproject.toml`](https://github.com/leodurandfr/Milo/blob/main/pyproject.toml): `F821` in
`backend/sources/bluetooth/agent.py`. `dbus-next` declares D-Bus signatures as string
annotations (`device: 'o'`, `-> 's'`, `passkey: 'u'`) — wire type codes, not Python names, so
each one reads to ruff as an unresolvable forward reference. Fourteen of them, all in that one
file. It is pinned to the file rather than the package on purpose: the same annotation anywhere
else in `sources/bluetooth/` still fails.

## Resources

- [Detailed Architecture](architecture.md)
- [FastAPI Docs](https://fastapi.tiangolo.com/)
- [Vue 3 Docs](https://vuejs.org/)
- [Pinia Docs](https://pinia.vuejs.org/)

## Contact

- **GitHub Issues:** https://github.com/leodurandfr/Milo/issues
