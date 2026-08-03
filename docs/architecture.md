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

### Source states

`SourceState` has **four** members and all four are reachable — the enum is the
whole vocabulary, and nothing derives a fifth:

| State | Meaning |
|---|---|
| `starting` | The transition to this source is under way |
| `ready` | Engine up, nothing in session |
| `active` | A session or content exists |
| `error` | The source is not operational |

Two things are worth spelling out because both were once ambiguous:

- **`active` is about a session, not about audio coming out.** A paused radio
  stays `active`: a station is still tuned. `ready` is "nothing in session".
- **`ready`, not `connected`.** Nothing connects to Radio, CD or the Music
  Library; they are simply ready to play.

`STARTING` and the machine's `transitioning` flag encode the same fact twice,
deliberately: `exclusive_transition()` sets the flag *without* the state so a
multiroom reroute can keep broadcasting live, and Milo-Mac pins `transitioning`
in `full_state`. Neither is redundant — the frontend follows the flag.

**Two kinds of error, two mechanisms, no overlap.** A source that will not start
is a *state*: the failed transition leaves the source **selected** in `ERROR`
with the message in `full_state.error` (re-selecting it is therefore the retry).
An *operation* that fails while the source keeps working — a station that will
not tune, a rejected command — rides the typed `source/error` event and raises
the notification banner only. Putting the second into the state would be the
mirror of the bug it replaced: a Radio whose browser is perfectly usable would
claim to be down.

Today the state machine's failed-transition path is the only writer of `ERROR`;
`broadcast_error()` carries the other kind and never touches the state. The
frontend adds no fifth member: `useSourceStatusDisplay` derives a *display*
state — the four above plus CD's two transient drive operations (`loading_disc`,
`ejecting`, both `READY` records) — and that list, `DISPLAY_STATES`, is what
`AudioSourceStatus` validates against.

### Unavailable, which is not a state

A source can be perfectly operational and still unable to do anything, because a
prerequisite outside it is missing. That is a second axis, not a fifth state,
and it has **one** name on both sides: `unavailableReason`, with four values.
When it is set the card renders it in place of the state's own phrase, and
`useRichDisplay` drops to the card — a Radio favourites grid whose every tap
fails is a worse screen than one saying why.

| Reason | Source of truth | CTA |
|---|---|---|
| `no_network` | `full_state.network_unavailable` | Network settings |
| `no_internet` | `full_state.network_unavailable` | Network settings |
| `no_account` | metadata `account_authenticated === false` (Qobuz) | Qobuz login |
| `no_drive` | metadata `drive_connected === false` (CD) | none — the UI cannot plug a drive in |

The two network values are computed by the **backend**, in
`AudioStateMachine._network_unavailable()`, by crossing two axes:

- **NetworkManager's `Connectivity` property**, kept whole as `ConnectivityLevel`
  (`unknown` / `none` / `portal` / `limited` / `full`). `limited` is literally
  "LAN reachable, no internet"; `portal` is a captive portal, folded into the
  same answer because Milō has no browser to accept one with. `unknown` is the
  fail-open value and reads as `full`.
- **The active source's `NETWORK_REQUIREMENT`** (`none` / `lan` / `internet`),
  a class attribute on `BaseAudioSource`: `internet` for Spotify, Qobuz, Radio
  and Podcast; `lan` for AirPlay, DLNA and Mac (ROC); `none` for Bluetooth, CD
  and the Music Library.

So a router with no route out blocks Spotify and leaves AirPlay alone, and
nothing at all blocks a CD. Neither axis alone can say that, which is why the
level is published whole rather than flattened to an `online` boolean — and why
there is no global offline banner any more: it fired on every `!online`,
including while listening over Bluetooth.

The catalogue flag `api_error` is a different layer and stays: Podcast's
discovery routes and Radio's station search set it when the third-party
directory (api.podcastindex.org, radio-browser.info) does not answer, which
happens while perfectly online. It is deliberately *not* a source-level fact
and never reaches the status card — the loss is partial and per-view, so it is
answered in the block that failed, with a retry: Radio's favourites and its
streams keep working with radio-browser.info down, and a subscribed podcast
still plays with Podcast Index down.

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
- **Catalog engine:** `milo-navidrome.service` (always-on, `PartOf=milo-backend.service` — *not*
  `BindsTo=`, which propagates stop only; the engine must follow a backend **restart** too) indexes
  everything under `/media/milo` and exposes a localhost **Subsonic API** (`127.0.0.1:4533`). A
  mount change triggers an explicit rescan. Scan state is **pushed, not polled**: one backend
  watcher (`shares.py::_watch_scan`) observes Navidrome for the whole appliance and broadcasts it
  with the storage list on `source/storages_changed`, surfaced as a "building library…" state with
  a live indexed-track count. That count is the storage space's **own** (`totalSongs` from
  Navidrome's native `/api/library`), because the Subsonic scan status reports a global counter
  that does not move until a scan ends — it read "2419 tracks indexed…" for the whole 18 minutes
  it took to index a 10 000-track iPod, then jumped
- **One Navidrome library per storage space.** Each mount — a USB key, an SMB/NFS share — gets its
  own Navidrome library, created and retired by `libraries.py` through Navidrome's *native* admin
  API (`navidrome_admin.py`, JWT; the Subsonic API cannot create a library). That library's id is
  the Subsonic `musicFolderId`, and it is the only handle a browse call can be scoped by: a track's
  catalog entry names its library, never its mount. It is what `GET /api/music-library/storages`
  returns and what the library view's storage filter switches between (shown only from two storage
  spaces up, and hidden entirely when `settings.music_library.separate_storages` is off — that
  setting merges every space into one catalog). Two consequences worth knowing:
  `MusicFolder` in `navidrome.toml` points at an **empty** directory, because Navidrome insists on
  one and pins the library it creates from it as undeletable — on `/media/milo` it would index
  every mount a second time; and a **configured share keeps its library while its NAS is offline**
  (deleting it would purge a valid catalog every time the NAS boots slower than the Pi), **and so
  does an unplugged USB key**: it keeps its library and its whole index, so a replug costs a quick
  scan (~0.4 s measured over 12 488 tracks) instead of re-reading every tag. That is why an unplug
  triggers no scan at all — a full one would purge outright (`PurgeMissing="full"`), a quick one
  would walk a path that no longer exists — and why unplugged keys join offline shares in gating
  the full-scan route. A key only loses its library when the user forgets it
  (`DELETE /api/music-library/usb-devices/{uuid}`, offered while it is unplugged)
- **A USB key can be named** (`PUT /api/music-library/usb-devices/{uuid}`, a sub-screen of the
  Music Library settings). The name is filed under the key's filesystem UUID — the only identity
  that survives a relabel or a replug into another port — and becomes its Navidrome library name,
  so the settings row, the storage filter and Navidrome's own UI agree
- **Playlists and favourites belong to a storage space too**, so browsing a key never turns up a
  NAS playlist. Favourites come scoped from Navidrome (`getStarred2` honours `musicFolderId`);
  playlists do not — Navidrome keeps them catalog-wide and ignores the parameter — so a playlist
  created in Milō records the storage space it was created in (`playlist_storages`, keyed by
  storage id so it survives a library being recreated), and any other playlist, such as an `.m3u`
  Navidrome imported from a key, is placed by its first track's album
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

**Multiroom:** the 44.1→48 conversion still happens at the source's `type plug` (`milo_<src>_multiroom`), *before* the loopback, so it is covered by the same `rate_converter`. Snapserver opens every loopback capture at `48000:32:2` and therefore only ever sees already-48 kHz audio — it is pass-through and does **not** resample (its bundled soxr is present but not exercised on this path).

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

**radio_data.json** - Radio favorites and custom stations. Durable (`schema_version: 1`).
**radio_images/** - Station artwork (WebP, ≤1024×1024): both auto-cached RadioBrowser logos and manually-uploaded custom-station art. Durable — the auto-cached share is regenerable, but user-uploaded art isn't, so the directory as a whole is treated as durable.
**podcast_data.json** - Subscriptions, favorites, playback progress, playback-speed preference. Durable (`schema_version: 2`).
**cd_data.json** - MusicBrainz disc-TOC/metadata lookup cache, keyed by disc ID. Disposable — no `schema_version`; re-fetched on next disc read if lost.
**cd_covers/** - Downloaded CD cover art, keyed by disc ID. Disposable cache (re-downloadable from Cover Art Archive).
**equalizer.json** - Persisted parametric-EQ/compressor/loudness/mono state (active preset, custom gains, filters). Durable (`schema_version: 2`). Distinct from `camilladsp/config.yml`: CamillaDSP itself resets to its baked static defaults on every restart, and it's this file the backend replays over its WebSocket API to restore the live EQ state afterwards.
**camilladsp/** - CamillaDSP daemon working directory (`WorkingDirectory=` in `milo-camilladsp.service`): `config.yml` is the static default config baked at install time from the repo (`install/camilladsp.sh`) — no backend code rewrites it at runtime, the backend only talks to the running daemon over its WebSocket API. `configs/` and `coeffs/` are empty subdirectories created for future coefficient-file support; nothing reads or writes into them today.
**go-librespot/** - `config.yml` is written at install time (`install/go-librespot.sh`), which owns every key in it *except one*: `crossfade_duration` belongs to the backend, which re-applies it from `SettingsService` on every `SpotifySource._do_start()` — go-librespot parses its config once, at process start, so that is the only moment a settings change can reach it (hence the settings page's "restart to apply"). The patch is key-scoped and atomic; it drops the installer's comments from the deployed copy, whose rationale stays in `install/go-librespot.sh`. Disposable — a deleted file is rebuilt by a re-install plus the next Spotify start. go-librespot itself runs with `--config_dir` pointed at this directory and may write its own session/zeroconf state here — that part is owned by the external binary, not by Milō code.
**routing.env** - Derived artifact of `settings.routing.multiroom_enabled`. Holds `MILO_MODE=direct|multiroom`. Read by every source systemd unit via `EnvironmentFile=` and by `/etc/asound.conf` via `@func getenv vars [ MILO_MODE ]` for `milo_*` alias resolution. Regenerated exclusively by `AudioRoutingService` whenever the setting changes.
**mac.env** - `ROC_TARGET_LATENCY`/`ROC_LATENCY_PROFILE`/`ROC_FRAME_LENGTH`, read only by `milo-mac.service`. Regenerable — derived purely from `settings.json`'s `mac` config by `MacEnv.regenerate()`.
**snapclient.env** - `MILO_SNAPCLIENT_BUFFER_TIME`/`MILO_SNAPCLIENT_FRAGMENTS`, read only by `milo-snapclient-multiroom.service`. Regenerable, same model as `mac.env`, via `SnapclientEnv.regenerate()`.
**pending_clients.json** - Staging area for multiroom client devices that registered via the API but haven't yet appeared in Snapcast. Transient by design: entries self-expire after 45s if never claimed. Safe to lose — clients just re-register.
**pending_client_role.json** - Written by `become_client()` when converting a unit from server to multiroom-client role; consumed and deleted by `milo-first-boot` on the next boot. Normally absent in steady state — only exists mid-conversion.
**last_volume.json** - Last saved volume for restoration
**qobuz/** - qobuz-proxy sidecar home (`QOBUZPROXY_DATA_DIR`): `venv/` (the pinned qobuz-proxy install), `config.yaml`, and the OAuth `credentials.json` written on first login. Owned `milo:audio`. Not baked into the image — the account login is per-user.
**navidrome/** - Navidrome catalog engine `DataFolder`: the library **DB** (`navidrome.db`), the regenerable art/transcode **cache/**, the baked `navidrome.toml`, and the per-device service-account cred (`milo-service.cred`, 0600) + `navidrome-auth.env` written on first boot. Owned `milo:milo`. Placed under `/var/lib/milo` so any whole-tree backup captures the catalog (see Backups).
**lyrics/** - LRCLIB lookup cache (one JSON per track key, negatives included). Disposable derived cache: no `schema_version`, safe to wipe.
**music_library_data.json** (`schema_version` 3) - Music Library storage config, three keys: `shares` — SMB/NFS non-secret metadata only (id/type/host/path/name/`has_credentials`); `known_usb` — every USB key ever mounted, keyed by filesystem UUID, each `{name, label, mountpoint, last_seen}`; `playlist_storages` — which storage space each Milō-created playlist belongs to, keyed by playlist id and valued by *storage* id (not a Navidrome library id, which is reassigned whenever a key comes back). Share passwords never land here — they live in root-only cred files written by `milo-mount`. A USB key's mountpoint is persisted because it is what keeps its Navidrome library — and therefore its index — alive while it is unplugged and across a backend restart. Mounts themselves appear under `/media/milo/`, which is a mount root, not persisted data — and note it is **not** the Navidrome `MusicFolder` (see Music Library above). The 2→3 bump (`usb_names` → `known_usb`) is fail-loud like every other: an already-deployed unit stops at boot with the reset banner, and deleting the file loses the configured shares (their passwords too — the orphaned cred files are dropped by `milo-mount --forget` only on a share deletion), so they have to be re-added.
**shares/** - Root-only (0700, `root:root`) credential store for network (SMB/NFS) shares, one `<id>.cred` file (0600) per share, written/read/removed exclusively by the privileged `milo-mount`/`milo-umount` helpers — the backend process never reads these directly. Durable for any share that has credentials; losing it just means re-entering them on the next mount attempt.
**app-version** - Written once at image-build time by pi-gen (`git describe --tags --always` at build) — not consulted by the running backend, which checks its own version live via `git describe` instead. A build-time artifact, not runtime data.
**avahi-interface** - One-line cache of which physical interface (`eth0`/`wlan0`) Avahi should bind mDNS to, written by the NetworkManager dispatcher on every link up/down or IPv4 lease change (and reset to `eth0` by `milo-first-boot` while setup is incomplete), read by `milo-apply-avahi-iface` before avahi-daemon starts. Regenerable — defaults to `eth0` if absent. Exists to avoid the `milo.local` → `milo-2.local` self-loop rename bug.
**shairport-sync-version** - Last successfully-installed shairport-sync version, written by the updater after a verified update. Best-effort cache: `version.py` falls back to invoking `shairport-sync --version` if the file is missing. Works around 4.3.7's version string not being reliably parseable post-update.
**errors.log** - Rotating application error log (`RotatingFileHandler`), not user data.

`music_library-test-tone.flac` may be seen alongside these on a live unit but has no corresponding code path anywhere in `backend/`, `install/`, or `rootfs/` — it isn't created by any Milō script and is excluded from this inventory as operator-placed residue, not appliance data.

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
- Exponential backoff between attempts: `min(1000 × 2^(attempt−1), 30000)` ms, i.e. 1 s, 2 s, 4 s …
  capped at 30 s. The counter resets on a successful connection (`services/websocket.js`)

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
milo-navidrome            # Music Library catalog engine (Navidrome, always-on, PartOf=milo-backend)
milo-music-library        # Music Library player (mpv, gapless; streams from Navidrome)
milo-camilladsp           # CamillaDSP audio processing (always in path for volume)
milo-snapserver-multiroom # Snapcast server (started/stopped by AudioRoutingService — no WantedBy)
milo-snapclient-multiroom # Local snapcast client (started/stopped by AudioRoutingService — no WantedBy)
milo-ir-keytable          # Boot oneshot: enable NEC decoding + reload paired Apple Remote keymap
milo-kiosk                # Chromium kiosk (touchscreen)
milo-readiness            # System readiness check
milo-first-boot           # Boot oneshot: first-boot/role setup (consumes pending_client_role.json)
milo-eeprom-setup         # Boot oneshot: Pi EEPROM/bootloader configuration
milo-cpu-governor         # Boot oneshot: pin the CPU governor
```

The list above is the whole of `system/` — `ls system/*.service` is the authoritative check.

**Dependencies:**
- Source units use `BindsTo=milo-backend` (stop if the backend stops)
- `milo-navidrome` uses `PartOf=milo-backend.service` instead, because `BindsTo=` propagates
  *stop* only and the always-on catalog engine must follow a backend **restart** as well. The two
  directives are not interchangeable — see the comment header in the unit file.
- Automatic restart on error

**Multiroom lifecycle:** Snapserver and snapclient units do **not** auto-start at boot. `AudioRoutingService._sync_snapcast_state` is the only writer: it reads `settings.routing.multiroom_enabled` and reconciles both units accordingly during backend init and after every multiroom toggle. This avoids the desync class where snapcast could run while the backend believed it was in direct mode (which holds `hw:Loopback,0,0` and produces `Device or resource busy` on other sources).

## Security

The threat model is a **trusted home LAN**: there is no authentication, no authorization and no
rate limiting anywhere in the stack. What protects a unit is that nothing reaches it from outside
the local network. Do not expose a Milō to the internet.

### Rate limiting
**None.** There is no rate limiter in the backend and none in the nginx config — no `slowapi`, no
`limit_req`. The API answers every request it receives.

### CORS
Allowed origins only (`backend/main.py`):
- `http://milo.local`, `https://milo.local`
- `http://localhost:5173`, `http://127.0.0.1:5173` (dev)

Allowed methods: `GET POST PUT PATCH DELETE OPTIONS`. Allowed headers: `Content-Type`, `Accept`,
`Authorization`. Credentials allowed.

### Permissions
- Backend runs as `milo` user (not root)
- Privileged exec is centralized (see CLAUDE.md invariant #1): systemd + power actions via `SystemdServiceManager` (`sudo systemctl …`), file deploys via pinned `/usr/local/bin/milo-*` sudoers helpers — all `NOPASSWD` for the `milo` user. PolicyKit covers only NetworkManager.
- Each policy file is authored once under `rootfs/etc/sudoers.d/` (satellite: `milo-client/rootfs/`) and copied by both the install scripts and `pi-gen/`, so the two install routes cannot grant different sets. `backend/tests/contracts/test_privileged_exec_contract.py` compares the granted commands against the argv the code builds, in both directions.

## Performance

### Optimizations

**Backend:**
- Async/await for non-blocking I/O
- Locks for thread-safety (no race conditions)
- Bounded waits so a stalled device cannot hang a request — e.g. `VolumeService.wait_for_availability`
  defaults to 5 s, and the multi-device apply is wrapped in a 10 s `asyncio.timeout`
- Settings cached in memory (avoids file reads)

**Frontend:**
- Lazy loading components
- WebSocket singleton (one shared connection)
- User event debouncing

### Known limitations

- Mac streaming latency: ~100-200ms (acceptable)
- Multiroom buffer: 1000ms (adjustable)
- System state broadcast on every change (can be optimized if needed)

## Extending Milō

Adding a source is a **six-step checklist across both code bases** (enum, module, `dependencies.py`
registration, two ALSA device variants, frontend touchpoints, i18n), and which files a source needs
depends on its family — a passive receiver and an active player do not have the same layout. That
checklist is maintained in one place, so it cannot drift from the rules that enforce it:

- **Family rules and the source contract** → [CLAUDE.md](../CLAUDE.md) § *Audio sources*
- **Step-by-step walkthrough**, incl. the ALSA loopback-subdevice constraint and the frontend
  touchpoint table → [Adding a new audio source](development.md#adding-a-new-audio-source)
- **Adding a service** (creator in `dependencies.py::_create_service`, async `initialize()`
  registration, the init-order constraints) → [development.md](development.md)

## Additional resources

- [Developer Guide](development.md)
- [API Overview](api-overview.md)
