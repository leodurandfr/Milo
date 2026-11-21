# Milo Client Systemd Services

This directory contains systemd service files for Milo Client satellites. These services are copied to `/etc/systemd/system/` during installation.

## Service Overview

### milo-client.service
- **Role**: FastAPI backend server for satellite control
- **Port**: 8001
- **Dependencies**: network.target
- **Startup**: Enabled at boot
- **Notes**: Manages local volume control and communicates with main Milo
- **Updates**: `ExecStartPre=-/usr/local/bin/milo-client-update-guard start` counts the starts of an app
  version on trial and puts the previous one back at the third; the new process ends the trial after
  holding 120 s (see docs/architecture.md, "How a satellite takes an app update")

### milo-client-snapclient.service
- **Role**: Snapcast client for synchronized multiroom audio
- **Port**: Connects to milo.local:1704
- **Dependencies**: milo-client.service; `network-online.target` orders nothing on the image, which masks
  NetworkManager-wait-online, so the launcher itself waits for NetworkManager's startup (`nm-online -s`),
  eth0's IPv4 included
- **Startup**: Enabled at boot
- **Notes**: Receives audio stream from main Milo's snapserver

### milo-client-camilladsp.service
- **Role**: CamillaDSP, the satellite's only volume stage and its EQ/DSP pipeline, driven by
  milo-client over its WebSocket (`127.0.0.1:1234`)
- **Dependencies**: `BindsTo=milo-client.service`, so it goes down and comes back with every app
  restart
- **Startup**: Enabled at boot; exits cleanly while `hardware.json` names no audio card
- **Notes**: Starts muted at the `--gain` floor until milo-client pushes the real level

## Environment File

`milo-client.service` and `milo-client-snapclient.service` read `/var/lib/milo-client/env`
(`EnvironmentFile=`); `milo-client-camilladsp.service` reads none. What the file holds, who writes
each key and which one is the server's is described once, in
[../ENVIRONMENT.md](../ENVIRONMENT.md).

## Directory Structure

`milo-first-boot` points the client account at the `milo-client/` tree the image
already carries, rather than cloning anything:

```
/home/milo-client/
├── repo/
│   └── milo-client -> /home/milo/milo/milo-client   # symlink to the shipped tree
│       ├── app/                   # Application files (main.py, requirements.txt)
│       └── system/                # Systemd service files
└── venv -> /home/milo/milo/milo-client/venv         # symlink to the pre-built venv
```

## Installation

Services are installed by the pi-gen image build, and by `milo-first-boot` when it converts a flashed card into a satellite:

```bash
# Service files are copied from the repo:
sudo cp /home/milo-client/repo/milo-client/system/*.service /etc/systemd/system/
sudo systemctl daemon-reload

# Services are enabled at boot:
sudo systemctl enable milo-client.service
sudo systemctl enable milo-client-snapclient.service
sudo systemctl enable milo-client-camilladsp.service
```

## Manual Service Control

```bash
# View service logs
sudo journalctl -u milo-client -f
sudo journalctl -u milo-client-snapclient -f

# Restart services
sudo systemctl restart milo-client
sudo systemctl restart milo-client-snapclient

# Check service status
sudo systemctl status milo-client
sudo systemctl status milo-client-snapclient
```

## Configuration

- `/var/lib/milo-client/env` - Environment variables (see [../ENVIRONMENT.md](../ENVIRONMENT.md))
- `/var/lib/milo-client/server-addresses` - The server's IPv4 addresses as its last heartbeat answer listed them, wired first, one per line: where the app and the snapclient launcher look for the server when `milo.local` does not resolve (see [../ENVIRONMENT.md](../ENVIRONMENT.md)). Rewritten by the app whenever the list changes, and read back at its start.
- `/var/lib/milo-client/server-secret` - The secret of the server this unit pinned at its first contact (0600). Its app update, SSH switch, password and reboot answer that server only (`app/server_secret.py`); removing the file lets the next server that reaches the unit pin its own. To move a speaker to a server that was reflashed, unplug it and plug it back in: for five minutes after a start that followed no clean stop, the server's hand-over replaces the pin.
- `/var/lib/milo-client/clean-stop`, `/var/lib/milo-client/started-boot` - How the last stop went: the marker the app writes when systemd stops it, consumed by the next start, and the boot id of the last start. A start on a new boot with no marker is a power cut, which opens that window.
- `/etc/asound.conf` - ALSA configuration for HiFiBerry

## Troubleshooting

### Service fails to start
```bash
# Check service status and logs
sudo systemctl status milo-client
sudo journalctl -u milo-client -n 50

# Verify service file syntax
systemd-analyze verify /etc/systemd/system/milo-client.service
```

### Audio not working
```bash
# Check snapclient connection
sudo journalctl -u milo-client-snapclient -f

# Verify main Milo is reachable
ping milo.local

# Test ALSA device
aplay -D default /usr/share/sounds/alsa/Front_Center.wav
```
