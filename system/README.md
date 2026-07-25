# Milo Systemd Services

This directory contains all systemd service files for the Milo Audio System. These services are copied to `/etc/systemd/system/` during installation.

## Service Overview

### Core Services (Always Enabled)

#### milo-backend.service
- **Role**: FastAPI backend server (Python)
- **Port**: 8000
- **Dependencies**: network.target
- **Startup**: Enabled at boot
- **Notes**: Main application logic, WebSocket server, REST API

#### milo-readiness.service
- **Role**: Waits for backend and nginx to be ready before dismissing Plymouth splash
- **Type**: oneshot
- **Dependencies**: milo-backend.service, nginx.service
- **Startup**: Enabled at boot
- **Notes**: Ensures smooth boot experience with splash screen

#### milo-kiosk.service
- **Role**: Launches Chromium in kiosk mode via Cage (Wayland compositor)
- **Target**: graphical.target
- **Dependencies**: milo-readiness.service, seatd.service
- **Startup**: Enabled at boot (if screen is configured)
- **Notes**: Controls tty1, conflicts with getty@tty1

### Audio Source Services (Managed Dynamically)

These services are **NOT enabled at boot**. They are started/stopped by the Milo backend based on active audio source selection. All bind to `milo-backend.service` and stop when backend stops.

#### milo-spotify.service
- **Role**: Spotify Connect via go-librespot
- **Device Name**: "Milō"
- **Port**: 3678 (API)
- **Dependencies**: milo-backend.service, network-online.target, sound.service
- **ALSA Device**: milo_spotify (dynamic routing via routing.env)
- **Managed By**: SpotifyPlugin

#### milo-bluealsa.service + milo-bluealsa-aplay.service
- **Role**: Bluetooth A2DP sink
- **Device Name**: "Milō · Bluetooth"
- **Dependencies**: dbus.service, bluetooth.service, milo-backend.service
- **ALSA Device**: milo_bluetooth (dynamic routing via routing.env)
- **Managed By**: BluetoothPlugin
- **Notes**: Two services work together (daemon + player)

#### milo-mac.service
- **Role**: Mac audio receiver via ROC toolkit
- **Ports**: 10001-10003 (RTP, RS8M, RTCP)
- **Dependencies**: milo-backend.service, network.target, sound.service
- **ALSA Device**: milo_roc (dynamic routing via routing.env)
- **Managed By**: MacPlugin

#### milo-radio.service
- **Role**: Internet radio player via mpv
- **IPC Socket**: /run/milo/radio-ipc.sock
- **Dependencies**: sound.target
- **ALSA Device**: milo_radio (dynamic routing via routing.env)
- **Managed By**: RadioPlugin
- **Notes**: mpv runs in daemon mode, controlled via JSON-IPC

#### milo-podcast.service
- **Role**: Podcast player via mpv (separate instance from radio)
- **IPC Socket**: /run/milo/podcast-ipc.sock
- **Dependencies**: sound.target
- **ALSA Device**: milo_podcast (dynamic routing via routing.env)
- **Managed By**: PodcastPlugin
- **Notes**: Independent mpv instance for podcast playback with progress tracking

#### milo-airplay.service
- **Role**: AirPlay 2 receiver via shairport-sync
- **Dependencies**: milo-backend.service, network-online.target, sound.target, nqptp.service (PTP clock sync, required via `Requires=`), milo-camilladsp.service
- **ALSA Device**: milo_airplay (dynamic routing via routing.env)
- **Managed By**: AirplaySource
- **Notes**: Config baked at install (`install/airplay.sh`), not runtime-managed by the backend

#### milo-cd.service
- **Role**: CD player via mpv, fed raw PCM over a FIFO from the ioctl disc reader
- **IPC Socket**: /run/milo/cd-ipc.sock
- **Dependencies**: sound.target, milo-backend.service, milo-camilladsp.service
- **ALSA Device**: milo_cd (dynamic routing via routing.env)
- **Managed By**: CdSource
- **Notes**: `RuntimeDirectoryPreserve=yes` — shares `/run/milo` with the other mpv-based source units and milo-camilladsp; must not be dropped by a restart

#### milo-dlna.service
- **Role**: DLNA/UPnP renderer via gmediarender
- **Port**: 49494
- **Dependencies**: milo-backend.service, network-online.target, sound.target, milo-camilladsp.service
- **ALSA Device**: milo_dlna (dynamic routing via routing.env)
- **Managed By**: DlnaSource

#### milo-music-library.service
- **Role**: Music Library player via mpv (local USB/SMB/NFS library, served through Navidrome)
- **IPC Socket**: /run/milo/music_library-ipc.sock
- **Dependencies**: sound.target, milo-backend.service, milo-camilladsp.service
- **ALSA Device**: milo_music_library (dynamic routing via routing.env)
- **Managed By**: MusicLibrarySource
- **Notes**: `--gapless-audio=yes`, no `loudnorm` (bit-perfect; volume/EQ handled downstream by CamillaDSP). Depends on milo-navidrome.service being up to browse/stream, but has no unit-level dependency on it — the backend only reaches Navidrome lazily when the source activates.

#### milo-qobuz.service
- **Role**: Qobuz Connect via the qobuz-proxy sidecar
- **Dependencies**: milo-backend.service, network-online.target, sound.target, milo-camilladsp.service
- **ALSA Device**: milo_qobuz (dynamic routing via routing.env)
- **Managed By**: QobuzSource
- **Notes**: `QOBUZPROXY_DATA_DIR=/var/lib/milo/qobuz` holds the venv, `config.yaml`, and the OAuth `credentials.json` written on first login (per-user, not baked into the image)

### Multiroom Services (Managed Dynamically)

#### milo-snapserver-multiroom.service
- **Role**: Snapcast server for multiroom audio synchronization
- **Port**: 1704 (streaming), 1780 (HTTP control)
- **Config**: /etc/snapserver.conf
- **Dependencies**: network-online.target
- **Managed By**: SnapcastService
- **Notes**: Started only when multiroom mode is enabled

#### milo-snapclient-multiroom.service
- **Role**: Snapcast client (local playback in multiroom mode)
- **Port**: 1704 (connects to local snapserver)
- **Dependencies**: network-online.target, milo-snapserver-multiroom.service
- **Managed By**: SnapcastService
- **Notes**: Plays synchronized audio from snapserver

### Infrastructure Services (Always Enabled, backend-linked)

Unlike the per-source units above (`BindsTo=milo-backend.service`, started/stopped on demand by the backend's source lifecycle), these are always-on daemons other sources depend on. They use `PartOf=milo-backend.service`, which — unlike `BindsTo=` — propagates both stop **and** restart, so a backend crash/restart doesn't leave them running stale.

#### milo-camilladsp.service
- **Role**: CamillaDSP audio processor (volume control + EQ/compressor/loudness, always in the audio path)
- **Port**: 1234 (WebSocket control, localhost only)
- **Dependencies**: sound.target (`Requires=`)
- **Startup**: Enabled at boot
- **Notes**: Starts muted (`-m`) and waits for a valid config (`-w`) at safe startup volume (`--gain=-60.0`); the backend unmutes and restores volume after init. Runs with real-time FIFO scheduling (`CPUSchedulingPolicy=fifo`, priority 15) and locked memory. `RuntimeDirectoryPreserve=yes` on `/run/milo`, shared with the mpv-based source units.

#### milo-navidrome.service
- **Role**: Navidrome catalog engine for the Music Library source (Subsonic API, localhost-only, indexes `/media/milo`)
- **Dependencies**: local-fs.target, milo-backend.service; `Wants=milo-navidrome-config.service` (re-applies the baked TOML ahead of every start)
- **Startup**: Enabled at boot
- **Notes**: `ExecStartPre` runs `milo-navidrome-provision` to generate the per-device service-account credential on first boot. Reached lazily by the backend only when the music_library source activates, so no strict boot ordering before `milo-backend.service`.

#### milo-navidrome-config.service
- **Role**: Oneshot that reconciles the Navidrome catalog TOML (album-grouping persistent ID) from the single source of truth (`install/navidrome.sh`) before Navidrome starts
- **Type**: oneshot (`RemainAfterExit=yes`)
- **Dependencies**: local-fs.target; `Before=milo-navidrome.service`
- **Startup**: Not enabled directly — pulled in on every start (boot or on-demand) via `Wants=milo-navidrome-config.service` from milo-navidrome.service
- **Notes**: Needed because the TOML is written once into `/var/lib/milo` (device data), so a normal app update never refreshes it. Idempotent — an unchanged rewrite is a no-op, and Navidrome only rescans when the PID value actually changes.

### Boot-Time Oneshot Services

#### milo-first-boot.service
- **Role**: Detects server vs. multiroom-client mode on first boot and applies the corresponding setup
- **Type**: oneshot
- **Dependencies**: After NetworkManager.service; Before avahi-daemon.service, milo-backend.service, milo-readiness.service
- **Startup**: Enabled by the pi-gen image build (`pi-gen/stage-milo/03-configure/01-run.sh`), not by `install.sh`
- **Notes**: `TimeoutStartSec=180` to allow for network wait + multi-attempt mDNS probe + client setup. Ordered before `avahi-daemon.service` only (never `avahi-daemon.socket` — see the unit file comment on the ordering-cycle risk that can otherwise strand NetworkManager).

#### milo-ir-keytable.service
- **Role**: Enables NEC IR decoding on the kernel's rc-core, before the backend's IR controller starts
- **Type**: oneshot (`RemainAfterExit=yes`)
- **Dependencies**: `ConditionPathExists=/sys/class/rc`; After systemd-modules-load.service; Before milo-backend.service
- **Startup**: Enabled at boot (`install/ir-remote.sh`)

#### milo-eeprom-setup.service
- **Role**: Applies power-on behaviour (wait for power button) to the Raspberry Pi bootloader EEPROM
- **Type**: oneshot (`RemainAfterExit=yes`)
- **Dependencies**: `ConditionPathExists=/home/milo/milo/install/power-button.sh`; After local-fs.target
- **Startup**: Enabled by the pi-gen image build (`pi-gen/stage-milo/03-configure/01-run.sh`), not by `install.sh`
- **Notes**: The EEPROM lives on the board's SPI flash, not the SD card, so it can't be baked into the pi-gen image — this reuses `configure_power_on_behavior()` from `install/power-button.sh` and re-flashes only when needed, so running every boot is harmless.

### Utility Services (Always Enabled)

#### milo-cpu-governor.service
- **Role**: Sets the CPU frequency governor to schedutil (idle heat reduction)
- **Type**: oneshot
- **Dependencies**: multi-user.target
- **Startup**: Enabled at boot
- **Notes**: WiFi power saving is disabled via NetworkManager config (rootfs/etc/NetworkManager/conf.d/90-milo-wifi-powersave.conf), not a unit

## Service Dependencies Graph

```
(boot-time oneshots, pi-gen image build or install/*.sh — see Boot-Time Oneshot Services)
  milo-first-boot.service → milo-eeprom-setup.service → milo-ir-keytable.service

graphical.target
  └─ milo-kiosk.service
       ├─ milo-readiness.service
       │    ├─ milo-backend.service
       │    └─ nginx.service
       └─ seatd.service

multi-user.target
  ├─ milo-backend.service
  ├─ milo-camilladsp.service
  ├─ milo-navidrome.service
  │    └─ milo-navidrome-config.service (Wants=, reconciles TOML before each start)
  ├─ milo-cpu-governor.service
  │
  └─ (dynamically started by backend)
       ├─ milo-spotify.service
       ├─ milo-bluealsa.service
       │    └─ milo-bluealsa-aplay.service
       ├─ milo-mac.service
       ├─ milo-radio.service
       ├─ milo-podcast.service
       ├─ milo-airplay.service
       ├─ milo-cd.service
       ├─ milo-dlna.service
       ├─ milo-music-library.service
       ├─ milo-qobuz.service
       └─ (multiroom mode only)
            ├─ milo-snapserver-multiroom.service
            └─ milo-snapclient-multiroom.service
```

## Dynamic ALSA Routing

Three environment files split by consumer (a var lives in `routing.env` only if multiple services consume it; otherwise it lives in a dedicated file owned by its consumer):

- **`/var/lib/milo/routing.env`** — `MILO_MODE` (`direct` or `multiroom`). Loaded by every audio source service for ALSA `getenv` resolution.
- **`/var/lib/milo/mac.env`** — `ROC_TARGET_LATENCY`, `ROC_LATENCY_PROFILE`, `ROC_FRAME_LENGTH`. Loaded only by `milo-mac.service`.
- **`/var/lib/milo/snapclient.env`** — `MILO_SNAPCLIENT_BUFFER_TIME`, `MILO_SNAPCLIENT_FRAGMENTS`. Loaded only by `milo-snapclient-multiroom.service`.

All three files are auto-generated from `settings.json` by the backend (`RoutingEnv` / `MacEnv` / `SnapclientEnv` in `backend/core/multiroom/routing.py`); never edit them manually.

ALSA device names are dynamically resolved via `MILO_MODE`:
- `milo_spotify` → `milo_spotify_direct` or `milo_spotify_multiroom`

This allows runtime switching between:
- **Direct mode**: Audio goes through CamillaDSP to amplifier
- **Multiroom mode**: Audio routed through Snapcast for synchronization (each client applies CamillaDSP locally)

CamillaDSP is always in the audio path for volume control. DSP effects (EQ, compressor, loudness) are toggled within CamillaDSP via bypass/restore, not via ALSA routing.

## Installation

Services are automatically installed by `install.sh`:

```bash
# During installation, all .service files are copied:
sudo cp /home/milo/milo/system/*.service /etc/systemd/system/
sudo systemctl daemon-reload

# Only core services are enabled at boot:
sudo systemctl enable milo-backend.service
sudo systemctl enable milo-readiness.service
sudo systemctl enable milo-kiosk.service
sudo systemctl enable milo-bluealsa.service
sudo systemctl enable milo-bluealsa-aplay.service
sudo systemctl enable milo-cpu-governor.service
```

Audio source services (librespot, roc, radio, podcast) and multiroom services (snapcast) are **NOT enabled** - they are managed dynamically by the backend.

## Manual Service Control

```bash
# View backend logs
sudo journalctl -u milo-backend -f

# Restart backend (will stop all audio services)
sudo systemctl restart milo-backend

# Check service status
sudo systemctl status milo-spotify

# Manually start/stop audio service (not recommended)
sudo systemctl start milo-radio
sudo systemctl stop milo-radio
```

**⚠️ Warning**: Manually starting/stopping audio services may conflict with backend state management. Let the backend manage these services.

## Configuration Files

- `/var/lib/milo/routing.env` - ALSA routing environment variables (auto-generated)
- `/var/lib/milo/go-librespot/config.yml` - Spotify Connect configuration
- `/etc/snapserver.conf` - Snapcast server configuration
- `/etc/asound.conf` - ALSA device definitions

## Troubleshooting

### Service fails to start
```bash
# Check service status and logs
sudo systemctl status milo-backend
sudo journalctl -u milo-backend -n 50

# Verify service file syntax
systemd-analyze verify /etc/systemd/system/milo-backend.service
```

### Audio not working
```bash
# Check if audio service is running
sudo systemctl status milo-radio

# Verify ALSA routing
cat /var/lib/milo/routing.env

# Test ALSA device directly
aplay -D milo_radio_direct /usr/share/sounds/alsa/Front_Center.wav
```

### Multiroom sync issues
```bash
# Check snapserver status
sudo systemctl status milo-snapserver-multiroom

# View snapserver logs
sudo journalctl -u milo-snapserver-multiroom -f

# Check snapclient connection
sudo journalctl -u milo-snapclient-multiroom -f
```

## Development

When modifying service files:

1. Edit the `.service` file in this directory
2. Copy to systemd: `sudo cp system/milo-backend.service /etc/systemd/system/`
3. Reload daemon: `sudo systemctl daemon-reload`
4. Restart service: `sudo systemctl restart milo-backend`
5. Verify: `sudo systemctl status milo-backend`

## Security Notes

- All services run as `milo` user (not root)
- Audio services use group `audio` for ALSA access
- Kiosk service uses `--no-sandbox` for Chromium (required for unprivileged user)
- Sensitive credentials stored in `/var/lib/milo/settings.json` (not in Git)

## Additional Resources

- [systemd service documentation](https://www.freedesktop.org/software/systemd/man/systemd.service.html)
- [Milo Architecture Documentation](../docs/architecture.md)
- [Milo Development Guide](../docs/development.md)
