# Milo Client Systemd Services

This directory contains systemd service files for Milo Client satellites. These services are copied to `/etc/systemd/system/` during installation.

## Service Overview

### milo-client.service
- **Role**: FastAPI backend server for satellite control
- **Port**: 8001
- **Dependencies**: network.target
- **Startup**: Enabled at boot
- **Notes**: Manages local volume control and communicates with main Milo

### milo-client-snapclient.service
- **Role**: Snapcast client for synchronized multiroom audio
- **Port**: Connects to milo.local:1704
- **Dependencies**: network-online.target
- **Startup**: Enabled at boot
- **Notes**: Receives audio stream from main Milo's snapserver

## Environment File

The `milo-client.service` uses an environment file at `/var/lib/milo-client/env` for dynamic configuration:

```bash
MILO_PRINCIPAL_IP=192.168.1.100  # IP of main Milo (discovered during installation)
```

This file is created by `install.sh` during installation.

## Directory Structure

Milo Client uses sparse checkout to clone only the `milo-client/` directory from the main Milo repository:

```
/home/milo-client/
├── repo/                          # Sparse checkout of Milo repository
│   └── milo-client/
│       ├── app/                   # Application files (main.py, requirements.txt)
│       └── system/                # Systemd service files
└── venv/                          # Python virtual environment
```

## Installation

Services are automatically installed by `install.sh`:

```bash
# During installation, service files are copied from the repo:
sudo cp /home/milo-client/repo/milo-client/system/*.service /etc/systemd/system/
sudo systemctl daemon-reload

# Services are enabled at boot:
sudo systemctl enable milo-client.service
sudo systemctl enable milo-client-snapclient.service
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

- `/var/lib/milo-client/env` - Environment variables (MILO_PRINCIPAL_IP)
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
