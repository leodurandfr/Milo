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
└───────┬────────────────┬─────────────┬────────────┬────────────┘
        │                │             │            │
  ┌─────▼───────┐ ┌──────▼──────┐ ┌────▼────┐ ┌─────▼──────┐
  │   Spotify   │ │  Bluetooth  │ │   Mac   │ │   Radio    │
  │ (librespot) │ │   (bluez)   │ │  (roc)  │ │   (mpv)    │
  └─────────────┘ └─────────────┘ └─────────┘ └────────────┘
         │               │             │            │
         └───────────────┴─────────────┴────────────┘
                              │
                    ┌─────────▼─────────┐
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
- **Hardware**: Hardware controllers (rotary encoder, screen)

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

**hardware.json** - Hardware configuration (screen type and resolution):
```json
{
  "screen": {
    "waveshare_7_usb": {
      "resolution": "1024x600"
    }
  }
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

```json
{
  "category": "source",
  "type": "state_changed",
  "source": "librespot",
  "data": {
    "full_state": { ... },  // Complete system state
    "metadata": { ... }      // Specific metadata
  },
  "timestamp": 1234567890
}
```

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
milo-backend              # FastAPI backend
milo-frontend             # Frontend (npm preview)
milo-spotify              # Spotify Connect (go-librespot)
milo-bluealsa             # Bluetooth daemon
milo-bluealsa-aplay       # Bluetooth player
milo-mac                  # Mac receiver (ROC)
milo-radio                # Radio player (mpv)
milo-snapserver-multiroom # Snapcast server (started/stopped by AudioRoutingService — no WantedBy)
milo-snapclient-multiroom # Local snapcast client (started/stopped by AudioRoutingService — no WantedBy)
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
