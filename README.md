<picture>
  <img style="pointer-events:none" src="https://leodurand.com/_autres/cover-milo-github@2x.png" />
</picture>

# Milō

> ⚠ Currently in work in progress — Available soon

### Transform your Raspberry Pi into a multiroom audio system with Spotify Connect, AirPlay 2, Bluetooth, CD playback, Internet Radio, Podcasts, and Mac streaming.

<!-- TODO: Add screenshot or GIF of the interface -->
<!-- ![Milō Interface](docs/assets/screenshot.png) -->

## Audio Sources

<table>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/spotify.png" width="48"></td>
    <td><b>Spotify</b><br>Spotify Connect receiver — control playback directly</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/radio.png" width="48"></td>
    <td><b>Radio</b><br>Browse 50,000+ stations, save favorites, add custom stations, their image, and identify tracks with Shazam</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/bluetooth.png" width="48"></td>
    <td><b>Bluetooth</b><br>Pair any device and stream audio</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/airplay.png" width="48"></td>
    <td><b>AirPlay</b><br>Stream from any Apple device (iPhone, iPad, Mac)</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/podcast.png" width="48"></td>
    <td><b>Podcasts</b><br>Search, subscribe, and resume episodes with variable speed (0.5x–2x)</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/cd.png" width="48"></td>
    <td><b>CD Player</b><br>Play audio CDs with automatic track listing and metadata lookup</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/macos.png" width="48"></td>
    <td><b>Mac</b><br>Stream your Mac's system audio with low latency (requires <a href="https://github.com/leodurandfr/Milo-Mac">Milō Mac</a>)</td>
  </tr>
</table>

## Features

<table>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/multiroom.png" width="48"></td>
    <td><b>Multiroom Audio</b><br>Synchronized playback across speakers with zone management and per-speaker volume</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/equalizer.png" width="48"></td>
    <td><b>Parametric EQ</b><br>10-band equalizer with presets, compressor, and loudness compensation (CamillaDSP)</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/settings.png" width="48"></td>
    <td><b>Settings</b><br>Control how Milō reacts: now playing, volume management, and more</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/languages.png" width="48"></td>
    <td><b>8 Languages</b><br>English, French, German, Spanish, Portuguese, Italian, Chinese, Hindi</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/updates.png" width="48"></td>
    <td><b>OTA Updates</b><br>Check and install updates autonomously, including multi-room client speakers</td>
  </tr>
</table>

## Hardware

<table>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/board.png" width="48"></td>
    <td><b>Board</b><br>Raspberry Pi 4 or 5 (64-bit)</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/audio.png" width="48"></td>
    <td><b>Audio</b><br>HiFiBerry HAT — Amplifiers (Amp2, Amp4, Amp4 Pro, Amp100, Beocreate) or DACs (DAC2 HD, DAC+ Pro)</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/display.png" width="48"></td>
    <td><b>Display</b><br>Waveshare 7" USB or 8" DSI <i>(optional)</i></td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/volume.png" width="48"></td>
    <td><b>Volume</b><br>Rotary encoder <i>(optional)</i></td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/cd-hardware.png" width="48"></td>
    <td><b>CD Player</b><br>USB CD drive <i>(optional)</i></td>
  </tr>
</table>

## Installation

### Quick Start (Recommended)

1. Download the latest `.img.xz` from [Releases](https://github.com/leodurandfr/Milo/releases)
2. Flash it with [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (select "Use custom" → choose the `.img.xz` file)
3. Insert the SD card and power on your Raspberry Pi
4. Connect to the **Milō** WiFi network that appears (open, no password)
5. A setup page opens automatically — follow the wizard to configure language, WiFi, audio card, and screen
6. Milō reboots and is ready to use

After setup:

- **Web interface** → http://milo.local
- **Spotify** → Select "Milō" in Spotify app
- **AirPlay** → Select "Milō" in your iPhone/iPad/Mac AirPlay outputs
- **Bluetooth** → Connect to "Milō · Bluetooth"
- **Mac** → Install [Milō Mac](https://github.com/leodurandfr/Milo-Mac), then select "Milō" in audio outputs

### Multiroom (Additional Speakers)

Flash the same image on additional Raspberry Pis. On first boot, the device detects your existing Milō server on the network and automatically configures itself as a client.

### Manual Installation (Advanced)

<details>
<summary>Install from Raspberry Pi OS Lite instead of using the pre-built image</summary>

Flash **Raspberry Pi OS (64-bit) Lite** (Debian Trixie) with Raspberry Pi Imager. In "Edit Settings", set hostname to `milo` and username to `milo`.

```bash
wget https://raw.githubusercontent.com/leodurandfr/Milo/main/install.sh
chmod +x install.sh
./install.sh
```

For multiroom clients, set hostname and username to `milo-client`:

```bash
wget https://raw.githubusercontent.com/leodurandfr/Milo/main/milo-client/install-client.sh
chmod +x install-client.sh
./install-client.sh
```

Uninstall:

```bash
./install.sh --uninstall         # Server
./install-client.sh --uninstall  # Client
```

</details>

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