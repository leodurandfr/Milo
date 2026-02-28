<picture>
  <img style="pointer-events:none" src="https://leodurand.com/_autres/cover-milo-github@2x.png" />
</picture>

# Milō

> ⚠ Currently in work in progress

### Transform your Raspberry Pi into a multiroom audio system with Spotify Connect, AirPlay 2, Bluetooth, Internet Radio, Podcasts, and Mac streaming.

<!-- TODO: Add screenshot or GIF of the interface -->
<!-- ![Milō Interface](docs/assets/screenshot.png) -->

## Audio Sources

| <img src="docs/images/spotify.png" width="40"><br>**Spotify** | <span style="font-weight:normal">Spotify Connect receiver — control playback directly</span> |
|:---:|---|
| <img src="docs/images/airplay.png" width="40"><br>**AirPlay** | Stream from any Apple device (iPhone, iPad, Mac) |
| <img src="docs/images/bluetooth.png" width="40"><br>**Bluetooth** | Pair any device and stream audio |
| <img src="docs/images/radio.png" width="40"><br>**Radio** | Browse 50,000+ stations, save favorites, add custom stations, their image, and identify tracks with Shazam |
| <img src="docs/images/podcast.png" width="40"><br>**Podcasts** | Search, subscribe, and resume episodes with variable speed (0.5x–2x) |
| <img src="docs/images/macos.png" width="40"><br>**Mac** | Stream your Mac's system audio (requires [Milō Mac](https://github.com/leodurandfr/Milo-Mac)) |

## Features

| <img src="docs/images/multiroom.png" width="40"><br>**Multiroom** | <span style="font-weight:normal">Synchronized playback across speakers with zone management and per-speaker volume</span> |
|:---:|---|
| <img src="docs/images/equalizer.png" width="40"><br>**Equalizer** | 10-band equalizer with presets, compressor, and loudness compensation (CamillaDSP) |
| <img src="docs/images/settings.png" width="40"><br>**Settings** | Control how Milō reacts: now playing, volume management, and more |
| <img src="docs/images/languages.png" width="40"><br>**Languages** | English, French, German, Spanish, Portuguese, Italian, Chinese, Hindi |
| <img src="docs/images/updates.png" width="40"><br>**Updates** | Check and install updates autonomously, including multi-room client speakers |

## Hardware

| |
|---|
| **Board**<br>Raspberry Pi 4 or 5 (64-bit) |
| **Audio**<br>HiFiBerry HAT — Amplifiers (Amp2, Amp4, Amp4 Pro, Amp100, Beocreate) or DACs (DAC2 HD, DAC+ Pro) |
| **Display**<br>Waveshare 7" USB or 8" DSI *(optional)* |
| **Volume**<br>Rotary encoder *(optional)* |

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
- **AirPlay** → Select "Milō" in your iPhone/iPad/Mac AirPlay outputs
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
| **Audio** | ALSA, CamillaDSP, Snapcast, mpv, go-librespot, shairport-sync, bluez-alsa, ROC |
| **Platform** | Raspberry Pi OS (64-bit), systemd |

## Documentation

- [📚 Documentation Index](docs/index.md) — All documentation
- [🏗️ Architecture](docs/architecture-overview.md) — How Milō works
- [💻 Developer Guide](docs/development-guide.md) — Contribute to the project
- [🔌 API Reference](docs/api-contracts-backend.md) — REST API endpoints
- [🧩 Components](docs/component-inventory-frontend.md) — Vue components

## License

[GPL-3.0](LICENSE)