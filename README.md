<picture>
  <img style="pointer-events:none" src="https://leodurand.com/_autres/cover-milo-github@2x.png" />
</picture>

# Milō (⚠️WIP⚠️)

> Transform your Raspberry Pi into a multiroom audio system with Spotify Connect, Bluetooth, Internet Radio, Podcasts, and Mac streaming.

<!-- TODO: Add screenshot or GIF of the interface -->
<!-- ![Milō Interface](docs/assets/screenshot.png) -->

## Audio Sources

| Source | Description |
|--------|-------------|
| 🎵 **Spotify Connect** | Native Spotify Connect receiver — control playback directly |
| 📱 **Bluetooth** | Pair any device and stream audio |
| 📻 **Radio** | Browse 50,000+ stations, save favorites, add custom stations, and identify tracks with Shazam |
| 🎙️ **Podcasts** | Search, subscribe, and resume episodes with variable speed (0.5x–2x) |
| 💻 **Mac** | Stream your Mac's system audio (requires [Milō Mac](https://github.com/leodurandfr/Milo-Mac)) |

## Features

| Feature | Description |
|---------|-------------|
| 🔊 **Multiroom Audio** | Synchronized playback across speakers with zone management and per-speaker volume |
| 🎛️ **Parametric EQ** | 10-band equalizer with 21 presets, compressor, and loudness compensation (CamillaDSP) |
| 🎤 **Shazam Recognition** | Automatic track identification on radio streams — see artist, title, and album art |
| 🖥️ **Now Playing Display** | Fullscreen screensaver with album art during playback on touchscreen |
| 🔈 **Volume Management** | Rotary encoder support, volume limits, startup volume mode (fixed or restore last) |
| 🔄 **OTA Updates** | Check and install updates from GitHub, including satellite speakers |
| 🌍 **8 Languages** | English, French, German, Spanish, Portuguese, Italian, Chinese, Hindi |

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

## Built With

| Layer | Technologies |
|-------|-------------|
| **Backend** | Python, FastAPI, asyncio |
| **Frontend** | Vue 3, Pinia, Vite |
| **Audio** | ALSA, CamillaDSP, Snapcast, mpv |
| **Platform** | Raspberry Pi OS (64-bit), systemd |

## Documentation

- [📚 Documentation Index](docs/index.md) — All documentation
- [🏗️ Architecture](docs/architecture-overview.md) — How Milō works
- [💻 Developer Guide](docs/development-guide.md) — Contribute to the project
- [🔌 API Reference](docs/api-contracts-backend.md) — REST API endpoints
- [🧩 Components](docs/component-inventory-frontend.md) — Vue components

## License

[GPL-3.0](LICENSE)