<picture>
  <img style="pointer-events:none" src="https://leodurand.com/_autres/cover-milo-github@2x.png" />
</picture>

# Milō

> Transform your Raspberry Pi into a multiroom audio system with Spotify Connect, Bluetooth, Internet Radio, Podcasts, and Mac streaming.

<!-- TODO: Add screenshot or GIF of the interface -->
<!-- ![Milō Interface](docs/assets/screenshot.png) -->

## Audio Sources

| Source | Description |
|--------|-------------|
| 🎵 **Spotify Connect** | See what's playing and control playback |
| 📱 **Bluetooth** | Pair any device and stream audio |
| 📻 **Radio** | Browse 50,000+ stations, save favorites |
| 🎙️ **Podcasts** | Search, subscribe, resume episodes |
| 💻 **Mac** | Stream your Mac's system audio (requires [Milō Mac](https://github.com/leodurandfr/Milo-Mac)) |

## Features

| Feature | Description |
|---------|-------------|
| 🔊 **Multiroom** | Synchronized playback across multiple speakers |
| 🎛️ **10-band Equalizer** | Adjust audio frequencies for all speakers |
| 🌍 **8 Languages** | EN, FR, DE, ES, PT, IT, ZH, HI |

## Hardware

| Component | Requirement |
|-----------|-------------|
| **Board** | Raspberry Pi 4 or 5 (64-bit) |
| **Audio** | HiFiBerry HAT (Amp2, Amp4, Amp4 Pro, Amp100, Beocreate) |
| **Display** | Waveshare 7" USB or 8" DSI *(optional)* |
| **Volume** | Rotary encoder *(optional)* |

## Installation

### Prerequisites

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Flash **Raspberry Pi OS (64-bit) Lite** (Debian Trixie)
3. In "Edit Settings", configure:
   - Hostname: `milo` (or `milo-client-01` for multiroom clients)
   - Username: `milo` (or `milo-client-01`)
   - Password: your choice
   - WiFi if needed

### Main Installation (Milō)

```bash
wget https://raw.githubusercontent.com/leodurandfr/Milo/main/install.sh
chmod +x install.sh
./install.sh
```

The script guides you through audio card and screen selection. Once complete:

- **Web interface** → http://milo.local
- **Spotify** → Select "Milō" in Spotify app
- **Bluetooth** → Connect to "Milō · Bluetooth"
- **Mac** → Install [Milō Mac](https://github.com/leodurandfr/Milo-Mac), then select "Milō" in audio outputs

### Client Installation (Milō Client)

For multiroom, install on additional Raspberry Pis to add synchronized speakers:

```bash
wget https://raw.githubusercontent.com/leodurandfr/Milo/main/milo-client/install-client.sh
chmod +x install-client.sh
./install-client.sh
```

> **Naming convention:** Use `milo-client-01`, `milo-client-02`, etc. for hostname and username.

### Uninstall

```bash
./install.sh --uninstall         # Main
./install-client.sh --uninstall  # Client
```

## Companion Apps

Control Milō from your other devices:

| Platform | Description | Link |
|----------|-------------|------|
| **macOS** | Menu Bar app + audio output | [Milō Mac](https://github.com/leodurandfr/Milo-Mac) |
| **iOS** | Fullscreen web interface | [Milō iOS](https://github.com/leodurandfr/Milo-iOS) |
| **Android** | Fullscreen web interface | [Milō Android](https://github.com/leodurandfr/Milo-Android) |

## Documentation

- [🏗️ Architecture](docs/architecture.md) — How Milō works
- [💻 Developer Guide](docs/development.md) — Contribute to the project

## License

[MIT](LICENSE)