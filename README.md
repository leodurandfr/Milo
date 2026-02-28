<picture>
  <img style="pointer-events:none" src="https://leodurand.com/_autres/cover-milo-github@2x.png" />
</picture>

# Milō

> ⚠ Currently in work in progress

### Transform your Raspberry Pi into a multiroom audio system with Spotify Connect, AirPlay 2, Bluetooth, Internet Radio, Podcasts, and Mac streaming.

<!-- TODO: Add screenshot or GIF of the interface -->
<!-- ![Milō Interface](docs/assets/screenshot.png) -->

## Audio Sources

<table>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/spotify.png" width="40"><br><b>Spotify</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Spotify Connect receiver — control playback directly</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/airplay.png" width="40"><br><b>AirPlay</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Stream from any Apple device (iPhone, iPad, Mac)</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/bluetooth.png" width="40"><br><b>Bluetooth</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Pair any device and stream audio</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/radio.png" width="40"><br><b>Radio</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Browse 50,000+ stations, save favorites, add custom stations, their image, and identify tracks with Shazam</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/podcast.png" width="40"><br><b>Podcasts</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Search, subscribe, and resume episodes with variable speed (0.5x–2x)</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/macos.png" width="40"><br><b>Mac</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Stream your Mac's system audio (requires <a href="https://github.com/leodurandfr/Milo-Mac">Milō Mac</a>)</td>
  </tr>
</table>

## Features

<table>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/multiroom.png" width="40"><br><b>Multiroom</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Synchronized playback across speakers with zone management and per-speaker volume</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/equalizer.png" width="40"><br><b>Equalizer</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>10-band equalizer with presets, compressor, and loudness compensation (CamillaDSP)</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/settings.png" width="40"><br><b>Settings</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Control how Milō reacts: now playing, volume management, and more</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/languages.png" width="40"><br><b>Languages</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>English, French, German, Spanish, Portuguese, Italian, Chinese, Hindi</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/updates.png" width="40"><br><b>Updates</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Check and install updates autonomously, including multi-room client speakers</td>
  </tr>
</table>

## Hardware

<table>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/board.png" width="40"><br><b>Board</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Raspberry Pi 4 or 5 (64-bit)</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/audio.png" width="40"><br><b>Audio</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>HiFiBerry HAT — Amplifiers (Amp2, Amp4, Amp4 Pro, Amp100, Beocreate) or DACs (DAC2 HD, DAC+ Pro)</td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/display.png" width="40"><br><b>Display</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Waveshare 7" USB or 8" DSI <i>(optional)</i></td>
  </tr>
  <tr>
    <td align="center" width="64"><img src="docs/images/margin-top.png"><br><img src="docs/images/volume.png" width="40"><br><b>Volume</b><br><img src="docs/images/margin-bottom.png"></td>
    <td>Rotary encoder <i>(optional)</i></td>
  </tr>
</table>

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