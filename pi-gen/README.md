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

1. Starts from Raspberry Pi OS Lite (arm64, Trixie)
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

Every dependency version comes from **`dependencies.env` at the repo root** —
the single declaration, shared with `provisioning/` and with the backend's update
flow. The stage scripts declare nothing: `build.sh` copies that file in beside
`stage-milo/` and each script sources it as a sibling. It has to be a copy
because a stage is built from a duplicate of `stage-milo/` inside a cloned
pi-gen checkout, often in Docker, which cannot reach the Milō repo.

The numbers are deliberately not repeated here, nor in the scripts. Read
`dependencies.env`. A version declared in a stage script instead of there fails
CI (`backend/tests/architecture/test_dependency_manifest.py`), which is what a
script-installed unit and a flashed one drifting apart used to look like:
nothing, until a reinstall landed behind the fleet.

## Output

The image is a universal image supporting both **Milo** (server) and **Milo Client** (satellite) modes. On first boot, the setup wizard at `http://milo.local` lets the user choose the mode and configure hardware.

### Image Configuration

- **User**: `milo` (no password, SSH enabled)
- **Hostname**: `milo` (renamed to `milo-client` on first boot if an existing `milo.local` is detected on the network)
- **Locale**: `en_US.UTF-8`
- **Timezone**: `Europe/Paris`
- **Architecture**: ARM64 (Raspberry Pi 4/5)
