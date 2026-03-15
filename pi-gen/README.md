# Milo Pi-gen Image Builder

Build a pre-configured Raspberry Pi OS image with Milo fully installed, ready to flash and boot.

## Quick Start

```bash
cd pi-gen
./build.sh
```

The image will be generated in `pi-gen-upstream/deploy/`.

## How It Works

This uses [pi-gen](https://github.com/RPi-Distro/pi-gen), the official Raspberry Pi OS image builder. The build:

1. Starts from Raspberry Pi OS Lite (arm64, Bookworm)
2. Runs stages 0-2 (base system)
3. Runs `stage-milo` which installs everything Milo needs
4. Exports a compressed `.img.xz` image

### Stage Structure

| Stage | Purpose |
|---|---|
| `00-install-deps` | APT packages, Node.js upgrade, PulseAudio removal |
| `01-install-audio` | Pre-compiled binaries (go-librespot, CamillaDSP, Snapcast) + compiled audio software (shairport-sync, NQPTP, bluez-alsa, roc-toolkit) |
| `02-install-milo` | Clone repo, Python venv, frontend build, deploy configs (ALSA, Nginx, Avahi, systemd services) |
| `03-configure` | Plymouth splash, boot params, fan control, udev rules, sudoers, kiosk mode, service enablement |

## Build Options

### Docker Build (recommended)

```bash
./build.sh
```

Requires Docker. Uses pi-gen's `build-docker.sh` internally.

### Native Build

```bash
./build.sh --native
```

Requires a Debian/Ubuntu host with ARM64 QEMU support.

### Build a Specific Branch/Tag

```bash
MILO_BRANCH=v1.0.0 ./build.sh
```

## CI/CD

The GitHub Actions workflow (`.github/workflows/build-image.yml`) automatically builds images:

- **On tag push** (`v*`): Builds, uploads artifact, and creates a GitHub Release with the image attached
- **Manual dispatch**: Builds any branch/tag and uploads as an artifact

Build time is approximately 1-2 hours due to ARM64 cross-compilation via QEMU.

## Dependency Versions

All dependency versions are pinned as variables at the top of the stage scripts for easy updates:

| Dependency | Version | Location |
|---|---|---|
| go-librespot | 0.6.1 | `01-install-audio/00-run.sh` |
| CamillaDSP | 3.0.1 | `01-install-audio/00-run.sh` |
| Snapcast | 0.35.0 | `01-install-audio/00-run.sh` |
| NQPTP | 1.2.4 | `01-install-audio/01-run.sh` |
| shairport-sync | 4.3.7 | `01-install-audio/01-run.sh` |
| bluez-alsa | 4.3.1 | `01-install-audio/01-run.sh` |
| roc-toolkit | 0.4.0 | `01-install-audio/01-run.sh` |

## Output

The image is a universal image supporting both **Milo** (server) and **Milo Client** (satellite) modes. On first boot, the setup wizard at `http://milo.local` lets the user choose the mode and configure hardware.

### Image Configuration

- **User**: `milo` (no password, SSH enabled)
- **Hostname**: `milo`
- **Locale**: `en_US.UTF-8`
- **Timezone**: `Europe/Paris`
- **Architecture**: ARM64 (Raspberry Pi 4/5)
