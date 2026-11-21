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
| `00-install-deps` | Every APT package `system-manifest.env` names (Chromium included), the `milo` user's device groups (PulseAudio is removed later, in `01-install-audio`, once roc-toolkit no longer needs `libpulse-dev`) |
| `01-install-audio` | Pre-compiled binaries (go-librespot, CamillaDSP, Snapcast) + compiled audio software (shairport-sync, NQPTP, bluez-alsa, roc-toolkit), then cloud-init purged |
| `02-install-milo` | Clone repo, Python venvs, install the prebuilt frontend artifact (never built in the stage), deploy configs (ALSA, Nginx, Avahi, systemd services) |
| `03-configure` | Plymouth splash, IR remote, udev rules, sudoers, then the system manifest's boot lines (cmdline.txt, config.txt) and unit states, applied by `milo-deploy-update apply-system-manifest` — the code every Milō update applies them with |

`pi-gen/build.sh` and the CI workflow copy `dependencies.env`, `system-manifest.env` and the
frontend artifact beside `stage-milo/`: the stage runs from a copy inside the pi-gen checkout,
which cannot reach this repo. What the stage installs must be in `system-manifest.env`, in
`rootfs/` or `system/`, or listed with its reason in
`backend/tests/architecture/test_image_update_convergence.py`, so a unit updated to a release
holds what a card flashed with it holds.

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
CI (`backend/tests/architecture/test_dependency_manifest.py`). Before the
manifest, the provisioning tree and the in-app updates each pinned their own
versions, and a freshly flashed card landed behind every unit that had updated
— silently.

## The pi-gen Commit

pi-gen itself is pinned the same way, in **`pi-gen/upstream.env`**: `build.sh` and the
workflow both fetch the commit it names and build on nothing else, rather than the head of
pi-gen's `arm64` branch the day the build runs. To move it, take that branch's head, build an
image on it, pass `inspect-image.sh`, then commit the new hash. The workflow's actions are
pinned by commit too, with their release in a comment beside each (see the top of
`.github/workflows/lint.yml`).

## Output

The image is a universal image supporting both **Milo** (server) and **Milo Client** (satellite) modes. Nobody chooses the mode: on first boot `milo-first-boot` looks on the network for a Milō server's `_milo._tcp` announcement, and a card that finds one becomes a satellite (see **Hostname** below), driven from the server's Settings › Multiroom. Answering to `milo.local` is not enough: a NAS or a Mac named milo does too. A card that finds none stays the server, and its setup wizard at `http://milo.local` configures language, network, audio and screen.

The frontend is built before the stage runs — by `build-image.yml` for a release, the artifact the release publishes; by `build.sh` on the host for a local build — and travels inside `stage-milo/`, at its root beside the sub-stages, as `frontend-dist.tar.gz`. `02-install-milo` installs it and fails when it is missing: the stage never builds one, so an image and a unit updated to the same release serve the same bytes.

### Image Configuration

- **User**: `milo`, factory password `milo`, **SSH disabled**. The password is a local
  convenience only, so the SSH server stays off until the owner opens it. On the server the
  switch refuses to open it while the factory password is still in place, and otherwise
  waits for a gesture on the unit itself (`backend/core/system/presence.py`), as setting the
  password does. Opened from Settings → System on the server, from a satellite's own panel
  under Multiroom for a satellite, or by dropping an empty `ssh` file on the boot partition
  (`sshswitch.service`, shipped enabled by Raspberry Pi OS).
- **No first-boot wizard, no cloud-init**: `DISABLE_FIRST_BOOT_USER_RENAME=1` keeps
  upstream's `rename-user` from re-enabling `userconfig.service`, whose tty8 prompt held
  `multi-user.target` forever on a flashed satellite, and `01-install-audio` purges the
  cloud-init packages stage2 installs whatever `ENABLE_CLOUD_INIT` says. `netplan.io` stays:
  network-manager depends on it. `inspect-image.sh` checks all of it on the built image.
- **Setup without a cable**: a unit with no link raises the `Milō` access point, and the setup
  wizard is reached over it. The access point is **open** — the Pi's WiFi chip offers no
  encryption that needs no password (OWE) in access-point mode — so the home WiFi key typed in
  the wizard crosses it in clear, and so does the key the server hands a fresh speaker during a
  WiFi adoption from Settings › Multiroom. An **Ethernet cable avoids both and is the
  recommended path**, for the first boot and for the adoption alike. SSH is never served to a
  client of the access point's subnet: sshd refuses `10.42.0.0/24` whatever address it dialed,
  which also covers the speaker's own open hotspot while the server is on it for an adoption,
  and neither link carries IPv6, so no link-local address gets around that. Before setup the
  access point's clients may send only the wizard's writes. Once the unit is set up they can
  read, and save a WiFi network only after a gesture on the unit itself
  (`backend/core/system/presence.py`): that is how a server with no screen and no Ethernet whose
  home WiFi changed is repaired. Anything else is repaired from the touch screen or over
  Ethernet.
- **Hostname**: `milo` (renamed to `milo-client` on first boot if a Milō server announces `_milo._tcp` on the network)
- **Locale**: `en_US.UTF-8`
- **Timezone**: `Etc/UTC` — a value meaning "nobody has said yet". The first non-kiosk browser
  that opens the UI reports its own zone and the backend adopts it while that default stands.
- **Architecture**: ARM64 (Raspberry Pi 4/5)

## Raspberry Pi Imager manifest

Any Raspberry Pi Imager manifest (`os_list` JSON) Milō ever publishes must give its image
`"init_format": "none"`. The field tells Imager how to apply its OS customization, and every
other value applies one: `systemd` writes a `firstrun.sh` that renames uid 1000 through
userconf-pi and resets the default target, and the `cloudinit` formats write `user-data` for a
cloud-init this image no longer carries, so the settings the user typed are dropped without a
word. Milō sets up its own Wi-Fi in its wizard, and its account, hostname and default target
are what every path, sudoers policy and unit is written around. With `none`, Imager offers no
customization at all. A user flashing with "Use custom" has no manifest to read, which is
why the top-level README tells them to answer **No** when Imager asks.
