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
└──┬─────┬────────┬───────┬───────┬───────┬───────┬──────┬──────┬──┘
   │     │        │       │       │       │       │      │      │
 ┌─▼──┐ ┌▼─────┐ ┌▼────┐ ┌▼───┐ ┌─▼───┐ ┌─▼────┐ ┌▼──┐ ┌─▼──┐ ┌─▼──────┐
 │Spo-│ │Qobuz │ │Air- │ │DLNA│ │Blue-│ │Music │ │Ra-│ │Pod-│ │Mac (roc│
 │tify│ │(qobu-│ │Play │ │(gme│ │tooth│ │Libra-│ │dio│ │cast│ │) + CD  │
 │(li-│ │z-pro-│ │(sha-│ │dia-│ │(blu-│ │ry    │ │(mp│ │(mpv│ │        │
 │bre-│ │xy)   │ │irpo-│ │rend│ │ez)  │ │(navi-│ │v) │ │)   │ │        │
 │spot│ │      │ │rt)  │ │er) │ │     │ │drome)│ │   │ │    │ │        │
 └─┬──┘ └──┬───┘ └──┬──┘ └─┬──┘ └──┬──┘ └──┬───┘ └─┬─┘ └─┬──┘ └───┬────┘
   └───────┴────────┴──────┴───────┴───────┴───────┴─────┴────────┘
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
- `SettingsService`: Centralized settings management (atomic writes, file locks, corruption backups)
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
- Internet radio streaming via mpv media player
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

### 5. Podcasts (mpv + Podcast Index API)

**What is it?**
- Podcast streaming via mpv media player
- Discovery via the Podcast Index REST API (search + episode metadata), with
  charts (top + by genre) from the iTunes RSS feeds
- [**Go to Podcast Index API**](https://podcastindex-org.github.io/docs-api/)

**How does it work?**
- Reuses the `MpvController` shared with the Radio source (separate mpv instance)
- Podcast Index provides podcast search and episode listings; the app
  authenticates with a single **app-level key + secret** embedded in the backend
  (`config/constants.py`) — no per-user credentials, no quota. Auth is a per-request
  SHA-1 signature (`X-Auth-Key`/`X-Auth-Date`/`Authorization`, 3-min window).
  Search is **podcasts-only** (Podcast Index has no cross-podcast episode search).
- Charts (top + by genre) stay on the keyless **iTunes RSS** feeds for exact Apple
  ordering; results are resolved to Podcast Index feeds via `/podcasts/byitunesid`
- Responses are cached in-memory (120min TTL)
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
- Audio output: ALSA (milo_airplay), `output_format = "S32_LE"` — see below
- Visible name: "Milō"

**Audio format.** `output_format = "S32_LE"` matches what CamillaDSP captures,
so the `plug` in front of `milo_airplay` no longer truncates to 16 bits. The
rate stays `"auto"` (44.1 kHz for most senders) and the 44.1 → 48 kHz conversion
is done by `plug`/`speexrate_medium`. Rates above 48 kHz are out of reach:
AirPlay 2 does not carry them and the pipeline is fixed at 48 kHz.

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

### 8. DLNA / UPnP Media Renderer (gmediarender)

**What is it?**
- DLNA renderer (DMR role) — any control point (BubbleUPnP, a NAS, Plex,
  Audirvana, foobar2000…) can push audio to Milō, rich metadata included
- [**Go to gmrender-resurrect repository**](https://github.com/hzeller/gmrender-resurrect)

**How does it work?**
- `gmediarender` announces Milō as a UPnP renderer via SSDP and does the full
  UPnP device work (AVTransport / RenderingControl) + GStreamer→ALSA output
- gmediarender emits no metadata on a pipe, so the backend acts as a UPnP
  **control point toward the local renderer**: `DlnaBridge` builds a `DmrDevice`
  from the fixed description URL, subscribes via **GENA** to the renderer's
  `LastChange` events (title/artist/album/artwork-URI/state pushed on change),
  and polls `GetPositionInfo` for progress
- Artwork arrives as a DIDL-Lite URL; the backend fetches it, decodes its
  dimensions, caches it in memory, and serves it via `GET /api/dlna/artwork`
- No remote playback control (the sender drives playback — Family B, like
  AirPlay); controls are hidden in the UI, only now-playing is shown
- Volume is ignored (fixed 0 dB, `--gstout-initial-volume-db 0`) — CamillaDSP is
  authoritative, as with AirPlay's `ignore_volume_control`
- **Not** a remote audio output: DLNA "Play To" pushes a whole media file to a
  screenless renderer — no video/TV/film audio, no lip-sync

**Configuration:**
- Service: milo-dlna.service (gmediarender, fixed port `49494` + fixed UUID)
- Bridge library: `async-upnp-client` (the one Home Assistant uses)
- Audio output: ALSA (milo_dlna) — GStreamer plugins: base/good/bad + `alsa` +
  `libav` (`avdec_alac` for ALAC); covers FLAC (incl. 24/192), ALAC, AAC, WAV, MP3
- Visible name: "Milo" (ASCII — the apt gmediarender v0.3 crashes on "Milō")
- `MemoryMax=256M` (measured ~70 MiB RSS on hi-res FLAC 24/192)

### 9. Qobuz Connect (qobuz-proxy)

**What is it?**
- Qobuz Connect receiver — any Qobuz app (mobile/desktop) can cast lossless
  audio to Milō, rich metadata included
- Qobuz Connect has no official DIY path, so Milō embeds **qobuz-proxy**, a
  reverse-engineered virtual Qobuz Connect device, as a **sidecar** — the exact
  model as go-librespot for Spotify
- [**Go to qobuz-proxy repository**](https://github.com/leolobato/qobuz-proxy)

**How does it work?**
- `qobuz-proxy` announces Milō as a Qobuz Connect device via mDNS and receives
  the Qobuz cloud's play/pause/seek commands (protobuf) — there is **no local
  playback-control API**, so control belongs to the Qobuz app (Family B, like
  AirPlay/DLNA); the UI hides controls and shows only now-playing
- The proxy's `local` (PortAudio) backend renders to the named ALSA PCM
  `milo_qobuz`; Milō's `QobuzMonitor` **polls `GET http://127.0.0.1:8689/api/status`**
  (~1 Hz) for `now_playing` (title/artist/album/album-art URL + position/duration).
  Upstream reports progress only to the Qobuz cloud, so
  [install/qobuz_proxy_patches.py](../install/qobuz_proxy_patches.py) adds
  `position_ms`/`duration_ms` to the vendored payload; the poll doubles as the
  progress feed (the frontend interpolates between ticks) and the bar is
  read-only — seeking belongs to the Qobuz app
- Artwork is a plain Qobuz CDN URL loaded directly by the kiosk — no binary
  artwork route
- A **one-time Qobuz account login** is required (unlike Spotify's zeroconf) or
  the device won't advertise; done via the in-app **Qobuz account** settings
  screen (backend relay `/api/qobuz/account/*` → the proxy's OAuth), token cached
  in `credentials.json` (see below)
- Volume follows CamillaDSP only: qobuz-proxy's local backend is pinned to unity
  gain at install (the Qobuz app slider is inert), the same role
  `external_volume` plays for go-librespot

**Configuration:**
- Service: milo-qobuz.service (qobuz-proxy sidecar, backend-managed)
- API: http://localhost:8689 (`/api/status` polled; OAuth on `/auth/*`)
- Audio output: ALSA (milo_qobuz)
- Visible name: "Milo" (ASCII — the Qobuz iOS app silently aborts the Connect
  handshake on a non-ASCII device name; Milō's own UI still shows "Qobuz")
- Data: `/var/lib/milo/qobuz/` (venv + `config.yaml` + OAuth `credentials.json`)

### 10. Music Library (Navidrome + mpv)

**What is it?**
- The user's own music, played from a **USB key** or **SMB/NFS network share** — an indexed
  library (Artists / Albums / Genres / Playlists / search / playlists), UI-controlled with rich
  metadata (Family C, like Radio/Podcast/CD)
- First source split into a catalog **engine** (Navidrome) + a **player** (mpv): the engine
  indexes and serves metadata, the player streams bit-perfect audio
- [**Go to Navidrome**](https://www.navidrome.org/)

**How does it work?**
- **Storage layer (ours):** a USB key is detected unprivileged via `pyudev` and mounted
  read-only under `/media/milo/<label>` by the `milo-mount` sudoers helper (SMB/NFS shares the
  same way; `milo-umount` reverses it). Navidrome only indexes folders — mounting the device is
  the backend's job
- **Catalog engine:** `milo-navidrome.service` (always-on, `BindsTo=milo-backend`) indexes
  everything under `/media/milo` and exposes a localhost **Subsonic API** (`127.0.0.1:4533`). A
  mount change triggers an explicit rescan; scan progress is polled over
  `GET /api/music-library/scan-status` and surfaced as a "building library…" state with a live
  indexed-track count
- **Player:** `sources/music_library` browses the Subsonic API through the backend proxy and
  builds an mpv native playlist from `stream?id=…&format=raw` URLs (bit-perfect, no transcode),
  played gapless (`--gapless-audio`) to `alsa/milo_music_library` → CamillaDSP — the same shape
  as Podcast, with Navidrome standing in for Podcast Index. The queue is built from any context
  (album / genre / playlist / search)
- **Cover art** is proxied localhost-only behind `/api/music-library/cover/{id}`; the frontend
  never talks to Navidrome (or sees its credentials) directly. Online metadata/art agents are
  always enabled (no toggle) — album covers still come from embedded/folder art first, the
  agents only enrich what's missing and fail back silently offline
- **Auth:** one service account, provisioned per-device on first boot by
  `milo-navidrome-provision`, stored in a milo-owned 0600 cred file — never in settings.json or
  WS payloads

**Configuration:**
- Services: milo-navidrome.service (catalog engine) + milo-music-library.service (mpv player)
- Storage: milo-mount / milo-umount privileged helpers (pinned sudoers) → mounts under /media/milo
- API: http://127.0.0.1:4533 (Subsonic, localhost only); REST under /api/music-library
- Audio output: ALSA (milo_music_library)
- Data: `/var/lib/milo/navidrome/` (DB + cache + service-account cred), `music_library_data.json`
  (network-share config, non-secret); share passwords live in root-only cred files, never here

## Lyrics (LRCLIB)

**What is it?**
- Time-synced (or plain) lyrics for the current track, opened from the dock like
  the Equalizer and Multiroom "apps" — not an audio source
- [**Go to LRCLIB**](https://lrclib.net/)

**How does it work?**
- A **transverse** feature: `core/lyrics/LyricsService` is keyed off the
  now-playing `(artist, title, album, duration)` of whichever source is active,
  so it works for any rich-metadata source. Mute receivers (Bluetooth, Mac) and
  Podcasts are excluded client-side; Radio reads its Shazam-recognized
  `track_artist`/`track_title` instead of the station name
- One route, `GET /api/lyrics?artist=&title=&album=&duration=`; the frontend
  fetches on modal open (and on track change while open), never over WebSocket
- LRCLIB needs no API key and returns both an LRC (synced) and a plain body.
  Matching drops parenthetical annotations and `- Remastered …` suffixes before
  falling back to search
- Results are cached on disk under `/var/lib/milo/lyrics/` **including
  negatives**, so a track with no lyrics is not re-queried. An unreachable
  LRCLIB is deliberately *not* cached (`LyricsUnavailable` → HTTP 200 +
  `status: error`) so a brief outage isn't frozen into "no lyrics"
- The disk cache is a disposable derived cache — no `schema_version`, no
  fail-loud protocol; wipe the directory if the shape changes

**Configuration:**
- No service — in-process, `aiohttp` (8s timeout, 0.3s polite spacing, 256-entry
  LRU in memory)
- Data: `/var/lib/milo/lyrics/` (disposable cache)

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
- Format: PCM 48kHz 32-bit stereo (`48000:32:2`, forced in `snapcast.py`)

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

Loopback subdevice layout (**card 1 `Loopback`**):
- subdevice 0: DSP input (`pcm.camilladsp`) — captured by milo-camilladsp on `plughw:Loopback,1,0`. Written by the active source (direct mode) or by snapclient (multiroom mode); the two writers are mutually exclusive.
- subdevice 1: Bluetooth (multiroom)
- subdevice 2: ROC / Mac (multiroom)
- subdevice 3: Spotify (multiroom)
- subdevice 4: Radio (multiroom)
- subdevice 5: Podcast (multiroom)
- subdevice 6: AirPlay (multiroom)
- subdevice 7: CD (multiroom)

Card 1 is full: DSP fills slot 0 and the seven sources fill slots 1..7. `snd-aloop` caps at **8 substreams per card** (kernel limit), so an 8th source cannot share the card. DLNA therefore lives on a **second loopback card** created by the same module:

```
options snd-aloop index=1,2 enable=1,1 id=Loopback,LoopbackDLNA pcm_substreams=8,8
```

- **card 2 `LoopbackDLNA`**, subdevice 0: DLNA (multiroom) — gmediarender writes `hw:2,0,0`, Snapserver reads `hw:2,1,0`.
- **card 2 `LoopbackDLNA`**, subdevice 1: Qobuz (multiroom) — qobuz-proxy writes `hw:LoopbackDLNA,0,1`, Snapserver reads `hw:2,1,1`.
- **card 2 `LoopbackDLNA`**, subdevice 2: Music Library (multiroom) — mpv writes `hw:LoopbackDLNA,0,2`, Snapserver reads `hw:2,1,2`. Direct mode routes `pcm.milo_music_library` → `camilladsp`, the same trio pattern as `milo_cd`/`milo_qobuz`.

Any further source needs another loopback card (bump `index`/`enable`/`id`/`pcm_substreams` in the module options at **both** install paths — `install/alsa.sh` and `pi-gen/stage-milo/02-install-milo/01-run.sh`).

### High-quality resampling (44.1 → 48 kHz)

The pipeline runs at a fixed **48 kHz**, so any natively-44.1 kHz source (CD, Spotify, AirPlay, Bluetooth, some radio stations) is resampled by the `type plug` PCM that wraps it. Both `asound.conf` files set:

```
defaults.pcm.rate_converter "speexrate_medium"
```

This replaces ALSA's default low-quality linear-interpolation converter with a **sinc/polyphase resampler** (`speexrate_medium`, from `libasound2-plugins`) for every `type plug` — a good CPU/quality balance on the Pi. The resampling runs **in the address space of the client that opens the PCM** (e.g. inside `go-librespot` for Spotify), not in CamillaDSP; the measured cost is ~+0.6 pt of one core for a 44.1 kHz source. The gain is measurable but inaudible — this is polishing, not a transformation; source quality (lossless vs lossy) remains the real lever.

**Multiroom:** the 44.1→48 conversion still happens at the source's `type plug` (`milo_<src>_multiroom`), *before* the loopback, so it is covered by the same `rate_converter`. Snapserver opens every loopback capture at `48000:32:2` and therefore only ever sees already-48 kHz audio — it is pass-through and does **not** resample (its bundled soxr is present but not exercised on this path). See [docs/plans/plan-resampler.md](plans/plan-resampler.md) for the full investigation.

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

### Bluetooth remote — ANTICATER VK-01 (optional)

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
**qobuz/** - qobuz-proxy sidecar home (`QOBUZPROXY_DATA_DIR`): `venv/` (the pinned qobuz-proxy install), `config.yaml`, and the OAuth `credentials.json` written on first login. Owned `milo:audio`. Not baked into the image — the account login is per-user.
**navidrome/** - Navidrome catalog engine `DataFolder`: the library **DB** (`navidrome.db`), the regenerable art/transcode **cache/**, the baked `navidrome.toml`, and the per-device service-account cred (`milo-service.cred`, 0600) + `navidrome-auth.env` written on first boot. Owned `milo:milo`. Placed under `/var/lib/milo` so any whole-tree backup captures the catalog (see Backups).
**lyrics/** - LRCLIB lookup cache (one JSON per track key, negatives included). Disposable derived cache: no `schema_version`, safe to wipe.
**music_library_data.json** - Music Library network-share config (SMB/NFS): non-secret metadata only (id/type/host/path/name/`has_credentials`). Share passwords never land here — they live in root-only cred files written by `milo-mount`. USB keys are not persisted (auto-detected live). Mounts themselves appear under `/media/milo/` (the Navidrome `MusicFolder`), which is a mount root, not persisted data.

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

There is no whole-tree backup/restore feature today; the appliance's durable state simply lives
under `/var/lib/milo/`. Everything there — including the Navidrome library DB (`navidrome/navidrome.db`)
and per-device credentials — is therefore captured by any backup that archives that directory. The
`navidrome/cache/` subdirectory is a rebuildable art/transcode cache and can be safely excluded from a
future backup (Navidrome regenerates it on demand).

## Real-time communication (WebSocket)

### Architecture

```
Backend State Change → state_machine.broadcast(WsEvent) → WebSocketManager → All clients
                                                               ↓
                                              Frontend Store Update → Reactive UI Update
```

### Typed event layer

Every event is a Pydantic `WsEvent` subclass in
`backend/core/models/ws_events.py` — one class per `(category, type)` pair,
`CATEGORY`/`TYPE` pinned at class level, and the model's own fields ARE the
wire `data` payload. The model is the payload documentation: each class
docstring names its consumers (frontend store/handler, Milo-Mac where
applicable). There is no dict-based emission path — a new event means a new
subclass.

`AudioStateMachine.broadcast(event)` serializes the model, injects the
aggregated `full_state` for `source`/`system` categories (lightweight events
opt out via `INCLUDE_FULL_STATE = False`), and wraps it in the envelope via
`WsEvent.to_envelope()`.

### Message format

Wire format: `{ category, type, origin, data, timestamp }`. The `origin` field
is the event's `source` field (falling back to `CATEGORY`).

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
`settings`, `volume`, `programs`, `network`. Event classes with category
`source` declare a `source` field so the envelope carries a meaningful
`origin`.

The subset of this surface consumed by Milo-Mac is pinned in
`backend/tests/contracts/milo_mac_contract.json`; its payload invariants are
statically verified against the event models on every `pytest` run.

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
milo-dlna                 # DLNA/UPnP renderer (gmediarender + GStreamer)
milo-qobuz                # Qobuz Connect (qobuz-proxy sidecar, backend-managed)
milo-navidrome-config     # Boot oneshot: re-emit the Navidrome TOML from install/navidrome.sh (before milo-navidrome)
milo-navidrome            # Music Library catalog engine (Navidrome, always-on, BindsTo=milo-backend)
milo-music-library        # Music Library player (mpv, gapless; streams from Navidrome)
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
- Privileged exec is centralized (see CLAUDE.md invariant #1): systemd + power actions via `SystemdServiceManager` (`sudo systemctl …`), file deploys via pinned `/usr/local/bin/milo-*` sudoers helpers — all `NOPASSWD` for the `milo` user. PolicyKit covers only NetworkManager.

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

1. Create source subclassing `BaseAudioSource` (backend/core/audio_source.py)
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
