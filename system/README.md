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

### Utility Services (Always Enabled)

#### milo-disable-wifi-power-management.service
- **Role**: Disables WiFi power saving to prevent audio dropouts
- **Type**: oneshot
- **Dependencies**: multi-user.target
- **Startup**: Enabled at boot
- **Notes**: Executes once at boot

## Service Dependencies Graph

```
graphical.target
  └─ milo-kiosk.service
       ├─ milo-readiness.service
       │    ├─ milo-backend.service
       │    └─ nginx.service
       └─ seatd.service

multi-user.target
  ├─ milo-backend.service
  ├─ milo-disable-wifi-power-management.service
  │
  └─ (dynamically started by backend)
       ├─ milo-spotify.service
       ├─ milo-bluealsa.service
       │    └─ milo-bluealsa-aplay.service
       ├─ milo-mac.service
       ├─ milo-radio.service
       ├─ milo-podcast.service
       └─ (multiroom mode only)
            ├─ milo-snapserver-multiroom.service
            └─ milo-snapclient-multiroom.service
```

## Dynamic ALSA Routing

All audio source services use environment variables from `/var/lib/milo/routing.env`:

- **MILO_MODE**: `direct` or `multiroom`
- **MILO_EQUALIZER**: empty or `_eq`

ALSA device names are dynamically resolved:
- `milo_spotify` → `milo_spotify_direct`, `milo_spotify_direct_eq`, `milo_spotify_multiroom`, or `milo_spotify_multiroom_eq`

This allows runtime switching between:
- **Direct mode**: Audio goes directly to amplifier
- **Multiroom mode**: Audio routed through Snapcast for synchronization
- **Equalizer**: Optional 10-band equalizer enabled/disabled

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
sudo systemctl enable milo-disable-wifi-power-management.service
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
