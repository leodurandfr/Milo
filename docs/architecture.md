# Milō Architecture

This document explains the technologies used in Milō and how they work together.

## Overview

Milō is built around a client-server architecture with real-time synchronization:

```
┌─────────────────────────────────────────────────────────────────┐
│                      Frontend (Vue 3)                           │
│                 Responsive user interface                       │
└────────────────────┬────────────────────────────────────────────┘
                     │ WebSocket (real-time)
                     │ HTTP REST (actions)
┌────────────────────▼────────────────────────────────────────────┐
│                  Backend (Python FastAPI)                       │
│                State machine + Audio routing                    │
└──┬──────┬─────────┬──────────┬────────┬───────┬────────┬───────┘
   │      │         │          │        │       │        │
 ┌─▼──┐ ┌─▼──────┐ ┌▼─────┐ ┌──▼─┐ ┌────▼──┐ ┌──▼────┐ ┌─▼─┐
 │Spo-│ │AirPlay │ │Blue- │ │Mac │ │Radio  │ │Podcast│ │CD │
 │tify│ │(shair- │ │tooth │ │(roc│ │(mpv)  │ │(mpv)  │ │   │
 │    │ │ port)  │ │(bluez│ │)   │ │       │ │       │ │   │
 └─┬──┘ └────┬───┘ └──┬───┘ └─┬──┘ └───┬───┘ └───┬───┘ └─┬─┘
   └────────┴────────┴───────┴───────┴───────┴────────┘
                              │
                              ▼
                    ┌───────────────────┐
                    │   CamillaDSP      │
                    │ (volume + EQ/DSP) │
                    └─────────┬─────────┘
                              ▼
                    ┌───────────────────┐
                    │  Audio Amplifier  │
                    │    (HiFiBerry)    │
                    └───────────────────┘
```

## Technologies used

### Backend: Python + FastAPI

**Source-based architecture:**
- **Core**: Domain models, state machine, services (volume, DSP, multiroom, settings)
- **Sources**: Self-contained audio source modules (spotify, airplay, radio, etc.)
- **API**: REST endpoints + WebSocket server
- **Hardware**: Hardware controllers (rotary encoder, IR remote, BT remote, screen)

**Key components:**
- `AudioStateMachine`: Single source of truth for system state
- `AudioRoutingService`: Manages multiroom routing between sources and outputs
- `SettingsService`: Centralized settings management (with SHA256 checksum)
- `VolumeService`: Unified volume control across all ALSA devices

### Frontend: Vue 3 + Vite

**Architecture:**
- **Pinia**: State management (synced with backend via WebSocket)
- **Components**: Organized by domain (audio, equalizer, snapcast, settings)
- **Services**: WebSocket (auto-reconnect), i18n (8 languages)

**Real-time synchronization:**
```
User Action → API Call → Backend Update → WebSocket Event → Store Update → UI Update
```

## Audio sources

### 1. Spotify Connect (go-librespot)

**What is it?**
- Open-source implementation of the Spotify Connect protocol
- Allows any Spotify device to cast to Milō
- [**Go to go-librespot repository**](https://github.com/devgianlu/go-librespot)

**How does it work?**
- go-librespot announces itself on the network as "Milō"
- The Spotify app (mobile/desktop) detects it automatically
- Direct audio streaming from Spotify servers (320kbps quality)
- Milō backend controls play/pause via go-librespot HTTP API

**Configuration:**
- Device name: "Milō"
- API: http://localhost:3678
- Audio output: ALSA (milo_spotify)

### 2. Bluetooth (bluez-alsa)

**What is it?**
- Linux Bluetooth stack + ALSA plugin
- A2DP support (Advanced Audio Distribution Profile) = high quality

**How does it work?**
- `bluealsa`: daemon that manages Bluetooth connections
- `bluealsa-aplay`: reads Bluetooth audio and sends to ALSA
- Milō backend automatically detects connections/disconnections

**Configuration:**
- Profile: A2DP Sink (Milō receives audio)
- Visible name: "Milō · Bluetooth"
- Audio output: ALSA (milo_bluetooth)

### 3. Mac streaming (roc-toolkit)

**What is it?**
- Network audio streaming protocol with error correction
- Automatic clock synchronization + adaptive buffering
- [**Go to roc-toolkit repository**](https://github.com/roc-streaming/roc-toolkit)

**How does it work?**
- Milō Mac application (using [**rov-vad**](https://github.com/roc-streaming/roc-vad)) captures system audio 
- Encodes and sends over network (RTP + Reed-Solomon FEC)
- `roc-recv` on Raspberry Pi decodes and plays to ALSA
- Latency ~100-200ms (acceptable for daily use)

**Configuration:**
- Source port: 10001
- Repair port: 10002
- Control port: 10003
- Audio output: ALSA (milo_roc)

### 4. Internet Radio (mpv + Radio Browser API)

**What is it?**
- Web radio streaming via mpv media player
- Station discovery via Radio Browser API (community-driven database)
- 50,000+ stations from around the world
- [**Go to Radio Browser API**](https://www.radio-browser.info/)

**How does it work?**
- Radio Browser API provides searchable database of internet radio stations
- mpv plays HLS/MP3/AAC streams with automatic codec detection
- Backend manages favorites, custom stations, and metadata caching
- Image upload support for custom station branding

**Features:**
- Search by station name, country, or genre
- Favorite stations with fast cached loading
- Custom station creation (add your own stream URLs)
- Broken station detection (auto-hide non-working streams)
- Station image customization (upload custom logos)
- Metadata display (bitrate, codec, country, genre)

**Configuration:**
- Service: milo-radio.service (mpv)
- IPC Socket: /run/milo/radio-ipc.sock
- Audio output: ALSA (milo_radio)
- API Endpoint: https://all.api.radio-browser.info/json
- Cache duration: 60 minutes
- Max image size: 10MB (JPG, PNG, WEBP, GIF)

### 5. Podcasts (mpv + Taddy API)

**What is it?**
- Podcast streaming via mpv media player
- Discovery via Taddy GraphQL API (charts, search, episode metadata)
- [**Go to Taddy API**](https://taddy.org/developers)

**How does it work?**
- Reuses the `MpvController` shared with the Radio source (separate mpv instance)
- Taddy API provides search, charts, and episode listings (60min cache)
- Playback progress is saved every 10s and resumed on next launch (if > 10s in)
- Speed control (0.5x–2x) and seek supported

**Configuration:**
- Service: milo-podcast.service (mpv)
- IPC Socket: /run/milo/podcast-ipc.sock
- Audio output: ALSA (milo_podcast)
- Data: `/var/lib/milo/podcast_data.json` (subscriptions, favorites, progress, preferences)

### 6. AirPlay 2 (shairport-sync + NQPTP)

**What is it?**
- AirPlay 2 receiver — any Apple device can stream to Milō
- Metadata pipe for track info and cover art
- [**Go to shairport-sync repository**](https://github.com/mikebrady/shairport-sync)

**How does it work?**
- `shairport-sync` announces Milō as an AirPlay 2 receiver via mDNS
- NQPTP daemon provides precision timing required by AirPlay 2
- Metadata (title, artist, album, artwork, device name) is read from the
  named pipe `/tmp/shairport-sync-metadata` by `MetadataReader`
- Artwork arrives as binary JPEG/PNG (PICT items)
- No remote playback control (AirPlay 2 protocol limitation) — controls are
  hidden in the UI; the source bar shows the connected device name

**Configuration:**
- Service: milo-airplay.service (shairport-sync)
- Metadata pipe: /tmp/shairport-sync-metadata
- Audio output: ALSA (milo_airplay)
- Visible name: "Milō"

### 7. CD Player (libdiscid + MusicBrainz)

**What is it?**
- Optical CD playback for USB CD drives
- Automatic disc identification via MusicBrainz (TOC lookup)

**How does it work?**
- USB CD drive detected via udev rules
- `libdiscid` computes the disc TOC; queries MusicBrainz for metadata
- Track-by-track playback with metadata + cover art caching

**Configuration:**
- Service: milo-cd.service
- Audio output: ALSA (milo_cd)
- Data: `/var/lib/milo/cd_data.json` (TOC cache), `cd_covers/` (cover art)

## Multiroom (Snapcast)

**What is it?**
- Multi-room synchronized audio streaming system
- Server/client architecture (like Sonos but open-source)
- [**Go to snapcast repository**](https://github.com/badaix/snapcast)

**How does it work?**

### Direct mode (multiroom disabled)
```
Audio source → ALSA → Amplifier → Speakers
```

### Multiroom mode (multiroom enabled)
```
Audio source → ALSA Loopback → Snapserver → Network
                                    ↓
            ┌───────────────────────┼───────────────────────┐
            ↓                       ↓                       ↓
      Snapclient 1            Snapclient 2            Snapclient 3
      (Raspberry 1)           (Raspberry 2)           (Raspberry 3)
            ↓                       ↓                       ↓
        Speakers                Speakers                Speakers
```

**Synchronization:**
- Snapserver sends precise timestamps with each audio packet
- Snapclients adjust their playback to stay synchronized (±1ms)
- Automatic network jitter compensation

**Configuration:**
- Server: http://localhost:1780 (REST API + WebSocket)
- Buffer: 1000ms (adjustable based on network latency)
- Format: PCM 48kHz 16-bit stereo

## DSP Processing (CamillaDSP)

**What is it?**
- Real-time audio DSP processor written in Rust
- Parametric equalizer, compressor, and loudness control
- WebSocket API for configuration (port 1234)
- [**Go to CamillaDSP repository**](https://github.com/HEnquist/camilladsp)

**How does it work?**
- CamillaDSP is ALWAYS in the audio path (for volume control)
- DSP effects (EQ, compressor, loudness) can be enabled/disabled via toggle
- When effects are disabled, CamillaDSP still handles volume control
- Configuration stored in `/var/lib/milo/camilladsp/config.yml`

**Features:**
- **Parametric EQ**: 10-band fully configurable (frequency, gain, Q)
- **Compressor**: Dynamic range control with makeup gain
- **Loudness**: Fletcher-Munson curve compensation
- **Volume control**: -80 dB to 0 dB range

## ALSA audio routing

ALSA (Advanced Linux Sound Architecture) is the Linux audio subsystem. Milō uses a complex configuration to dynamically route audio.

### Dynamic virtual devices

Each audio source (Spotify, Bluetooth, Mac) has 2 possible ALSA devices:
```
milo_spotify_direct          → Via CamillaDSP to amplifier
milo_spotify_multiroom       → To Snapcast (loopback, each client applies CamillaDSP locally)
```

### Automatic selection

The backend uses environment variables to select the right device:
```bash
MILO_MODE=direct           # or "multiroom"
```

**Example:** If multiroom enabled:
```
MILO_MODE=multiroom → milo_spotify_multiroom
```

**Note:** CamillaDSP is always in the audio path for volume control. DSP effects (EQ, compressor, loudness) are toggled via bypass/restore within CamillaDSP, not via ALSA routing.

### ALSA Loopback

Virtual device that captures audio and makes it available to snapcast:
```
Source → Loopback (hw:1,0,X) → Snapserver reads from hw:1,1,X
```

Loopback subdevice layout:
- subdevice 0: DSP input (`pcm.camilladsp`) — captured by milo-camilladsp on `plughw:Loopback,1,0`. Written by the active source (direct mode) or by snapclient (multiroom mode); the two writers are mutually exclusive.
- subdevice 1: Bluetooth (multiroom)
- subdevice 2: ROC / Mac (multiroom)
- subdevice 3: Spotify (multiroom)
- subdevice 4: Radio (multiroom)
- subdevice 5: Podcast (multiroom)
- subdevice 6: AirPlay (multiroom)
- subdevice 7: CD (multiroom)

Sources are strictly contiguous in slots 1..7; DSP is isolated in slot 0 so adding a future source (`pcm_substreams` bump → slot 8) does not require reshuffling.

## Hardware control

### Rotary encoder (optional)

**GPIO pins:**
- CLK: GPIO 22 (rotation)
- DT: GPIO 27 (direction)
- SW: GPIO 23 (button)

**Operation:**
- GPIO interrupts for rotation detection
- Software debouncing (10ms)
- Configurable volume step adjustment

### IR remote — Apple Remote A1156 (optional)

**Hardware:** TSOP4838 IR receiver, VS → 3V3, GND → GND, OUT → GPIO17 (configurable from the Hardware settings page; persisted in `hardware.json` under `hardware.ir_remote.gpio_pin`).

**Decoding chain:**
```
TSOP4838 pulses → gpio-ir overlay → /dev/lirc0
                                   → rc-core NEC decoder
                                   → /dev/input/eventN (EV_MSC + EV_KEY)
```

**Pairing model:** the user runs an in-app wizard that listens for `EV_MSC/MSC_SCAN`, extracts the Apple Remote's `device_id` byte from the 32-bit scancode (`0x87EE DD CC`), and writes `/etc/rc_keymaps/milo-apple-remote.toml` containing only that `device_id` — strict filtering ignores any other Apple Remote in range. Both parity variants of each command byte are emitted so the keymap survives a user-side `Menu+Play` device_id roll without re-pairing.

**Runtime:** `IrRemoteController` listens for `EV_KEY` (the wizard listens for `EV_MSC`; the two modes are mutually exclusive via an `asyncio.Lock`). Volume ± share `VolumeAccumulator` with the rotary encoder. Track Next/Prev/Play-Pause go through the public `PlaybackDispatcher.dispatch_*` methods. The Menu button resolves at T+400 ms: hold → `screen.force_sleep()`, 1-click → cycle to the next dock-ordered audio source, 2+ clicks → `transition_to_source(NONE)`. Volume buttons hold-to-repeat at the same cadence as the `Dock.vue` long-press.

### Bluetooth remote — ANTICATER VK1 Mini (optional)

**Discovery:** persistent D-Bus BlueZ listener for instant reconnect, periodic discovery+pair, on-demand battery read.

**Runtime:** evdev `EV_KEY` listener on the BlueZ-created `/dev/input/eventN`. Shares `VolumeAccumulator` + `PlaybackDispatcher` with the rotary encoder. SW button uses the same multi-click resolver (1=play/pause, 2=next, 3=prev) as the rotary; no hold gesture.

### Touch screen (optional)

**Support:**
- Waveshare 7" USB (1024x600)
- Waveshare 8" DSI (1280x800)

**Power management:**
- Configurable timeout
- Automatic shutoff when inactive
- Wake on touch

## Data persistence

### Configuration files in /var/lib/milo/

**settings.json** - Central file for all system parameters:
```json
{
  "language": "french",
  "volume": { "alsa_min": 0, "alsa_max": 65, ... },
  "screen": { "timeout_seconds": 10, ... },
  "routing": { "multiroom_enabled": false, "equalizer_enabled": false },
  "dock": { "enabled_apps": [...] }
}
```

**hardware.json** - Hardware configuration (screen, rotary encoder, IR remote):
```json
{
  "audio":  { "id": "hifiberry_amp4pro", ... },
  "screen": { "type": "waveshare_8_dsi", "resolution": "1280x800" },
  "rotary_encoder": { "enabled": true,  "clk_pin": 22, "dt_pin": 27, "sw_pin": 23 },
  "ir_remote":      { "enabled": false, "gpio_pin": 17 }
}
```

**radio_data.json** - Radio favorites and custom stations
**routing.env** - Derived artifact of `settings.routing.multiroom_enabled`. Holds `MILO_MODE=direct|multiroom`. Read by every source systemd unit via `EnvironmentFile=` and by `/etc/asound.conf` via `@func getenv vars [ MILO_MODE ]` for `milo_*` alias resolution. Regenerated exclusively by `AudioRoutingService` whenever the setting changes.
**last_volume.json** - Last saved volume for restoration

**Integrity protection:**
- ✅ Atomic write (`os.replace()`)
- ✅ File locks for concurrent access
- ✅ Automatic backup if corruption detected

### Backups

Automatic binary backups during updates:
```
/var/lib/milo/backups/
├── go-librespot-0.4.0
├── snapserver-0.31.0
└── snapclient-0.31.0
```

## Real-time communication (WebSocket)

### Architecture

```
Backend State Change → WebSocketManager → All connected clients
                            ↓
                    Frontend Store Update → Reactive UI Update
```

### Message format

Wire format: `{ category, type, origin, data, timestamp }`. The `origin` field
is derived from `data["source"]` (falling back to `category`).

```json
{
  "category": "source",
  "type": "state_changed",
  "origin": "spotify",
  "data": {
    "source": "spotify",
    "metadata": { ... }
  },
  "timestamp": 1234567890
}
```

Event categories: `source`, `system`, `routing`, `equalizer`, `multiroom`,
`settings`, `volume`, `programs`. Callers using category `source` **must**
include a `"source"` field in `data` so the manager can populate `origin`.

### Disconnection handling

**Frontend:**
- Automatic disconnection if tab hidden (resource saving)
- Automatic reconnection when tab becomes visible
- Fixed 3s delay between attempts (sufficient for local use)

**Backend:**
- Automatic ping every 30s
- Clean client disconnection handling
- No connection limit (domestic use)

## Systemd services

All components are managed by systemd:

```bash
milo-backend              # FastAPI backend (nginx serves frontend/dist/ — no frontend service)
milo-spotify              # Spotify Connect (go-librespot)
milo-airplay              # AirPlay 2 (shairport-sync + NQPTP)
milo-bluealsa             # Bluetooth daemon
milo-bluealsa-aplay       # Bluetooth player
milo-mac                  # Mac receiver (ROC)
milo-radio                # Radio player (mpv)
milo-podcast              # Podcast player (mpv, separate instance from radio)
milo-cd                   # CD player
milo-camilladsp           # CamillaDSP audio processing (always in path for volume)
milo-snapserver-multiroom # Snapcast server (started/stopped by AudioRoutingService — no WantedBy)
milo-snapclient-multiroom # Local snapcast client (started/stopped by AudioRoutingService — no WantedBy)
milo-ir-keytable          # Boot oneshot: enable NEC decoding + reload paired Apple Remote keymap
milo-kiosk                # Chromium kiosk (touchscreen)
milo-readiness            # System readiness check
```

**Dependencies:**
- All sources `BindsTo=milo-backend` (stop if backend stops)
- Automatic restart on error

**Multiroom lifecycle:** Snapserver and snapclient units do **not** auto-start at boot. `AudioRoutingService._sync_snapcast_state` is the only writer: it reads `settings.routing.multiroom_enabled` and reconciles both units accordingly during backend init and after every multiroom toggle. This avoids the desync class where snapcast could run while the backend believed it was in direct mode (which holds `hw:Loopback,0,0` and produces `Device or resource busy` on other sources).

## Security

### Rate limiting
- Global: 100 requests/minute
- Sufficient for domestic/family use

### CORS
Allowed origins only:
- http://milo.local
- http://localhost:5173 (dev)

### Permissions
- Backend runs as `milo` user (not root)
- Systemctl commands via PolicyKit (no sudo in code)

## Performance

### Optimizations

**Backend:**
- Async/await for non-blocking I/O
- Locks for thread-safety (no race conditions)
- Timeouts (2s) for volume operations (avoids hangs)
- Settings cached in memory (avoids file reads)

**Frontend:**
- Lazy loading components
- WebSocket singleton (one shared connection)
- User event debouncing

### Known limitations

- Mac streaming latency: ~100-200ms (acceptable)
- Multiroom buffer: 1000ms (adjustable)
- System state broadcast on every change (can be optimized if needed)

## Scalability

### Adding an audio source

1. Create source implementing `AudioSourceProtocol`
2. Register in `dependencies.py`
3. Add ALSA devices in `/etc/asound.conf`
4. Create Vue component for UI

### Adding a feature

1. Create service in `backend/core/`
2. Add API route in `backend/api/`
3. Create Vue component in `frontend/src/components/`
4. Update Pinia store if needed

## Additional resources

- [Developer Guide](development.md)
