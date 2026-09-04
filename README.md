<picture>
  <img src="https://github.com/user-attachments/assets/799eac15-d66f-4166-8ba9-86f44eb381f5" />
</picture>

# Milō

> ⚠ Currently in work in progress — Available soon

### Transform your Raspberry Pi into a multiroom audio system with Spotify Connect, Qobuz Connect, Tidal Connect, Internet Radio, Bluetooth, AirPlay 2, Podcasts, CD playback, your own Music Library, DLNA, and Mac streaming.

## Audio Sources

<table>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/spotify.png" width="48"></td>
    <td><b>Spotify</b><br>Spotify Connect receiver with artwork and full playback control</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/qobuz.png" width="48"></td>
    <td><b>Qobuz</b><br>Qobuz Connect receiver with lossless audio and rich metadata</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/tidal.png" width="48"></td>
    <td><b>TIDAL</b><br>Tidal Connect receiver with artwork and playback control</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/radio.png" width="48"></td>
    <td><b>Internet Radio</b><br>Browse 50,000+ stations, save favorites, and identify tracks with Shazam</td>
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
    <td><b>Podcasts</b><br>Search, subscribe, and resume episodes where you left off</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/cd.png" width="48"></td>
    <td><b>CD Player</b><br>Play audio CDs with automatic track and metadata lookup</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/music-library.png" width="48"></td>
    <td><b>Music Library</b><br>Play your own music from a USB drive or network share (SMB/NFS), indexed by artists, albums, genres, and playlists</td>
  </tr>
    <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/macos.png" width="48"></td>
    <td><b>macOS receiver</b><br>Stream your Mac's system audio with low latency (requires <a href="https://github.com/leodurandfr/Milo-Mac">Milō Mac</a>)</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/dlna.png" width="48"></td>
    <td><b>DLNA/UPnP</b><br>Play music to Milō from any controller app</td>
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
    <td><b>Equalizer</b><br>10-band parametric EQ with presets, compressor, and loudness compensation (CamillaDSP)</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/lyrics.png" width="48"></td>
    <td><b>Lyrics</b><br>Time-synced lyrics that follow the current track, with a plain-text fallback</td>
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
    <td><b>Board</b><br><b>Main unit:</b> Raspberry Pi 5 (8 GB) recommended<br><b>Multiroom client:</b> Raspberry Pi 4 (2 GB) or Pi 5</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/audio.png" width="48"></td>
    <td><b>Audio</b><br>Any supported HiFiBerry HAT — amplifiers (Amp2, Amp4, Amp4 Pro, Amp100, Beocreate 4CA) or DACs (DAC2 HD, DAC2 Pro, DAC2 ADC Pro).<br><b>Amp4 Pro is required if a screen is connected</b> (power delivery).</td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/display.png" width="48"></td>
    <td><b>Display</b><br>Waveshare 7" USB (1024×600) or Waveshare 8" DSI (1280×800) <i>(optional)</i></td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/volume.png" width="48"></td>
    <td><b>Volume</b><br>Rotary encoder with push button (KY-040 / HW-040) <i>(optional)</i></td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/remote.png" width="48"></td>
    <td><b>Remotes</b><br>Apple Remote (A1156) via a TSOP4838 IR receiver, or an ANTICATER VK-01 Bluetooth remote <i>(optional)</i></td>
  </tr>
  <tr>
    <td align="center" valign="middle" width="100" height="100"><img src="docs/images/cd-hardware.png" width="48"></td>
    <td><b>CD Player</b><br>USB CD drive, e.g. Apple USB SuperDrive <i>(optional)</i></td>
  </tr>
</table>

## Installation

### Quick Start

1. Download the latest `.img.xz` from [Releases](https://github.com/leodurandfr/Milo/releases)
2. Flash it with [Raspberry Pi Imager](https://www.raspberrypi.com/software/) (select "Use custom" → choose the `.img.xz` file)
3. Insert the SD card and power on your Raspberry Pi
4. Connect to the **Milō** WiFi network that appears (open, no password)
5. A setup page opens automatically — follow the wizard to configure language, WiFi, audio card, and screen
6. Milō reboots and is ready to use

The time zone is picked up from the phone or computer you set Milō up from; Settings →
Language and region changes it. The WiFi country is asked for in the wizard and cannot be
skipped — it is what the radio's legal transmit power depends on.

After setup:

- **Web interface** → http://milo.local
- **Spotify** → Select "Milō" in Spotify app
- **Qobuz** → Select "Milo" in the Qobuz app (requires a one-time login in Milō's settings)
- **Tidal** → Select "Milō" in the Tidal app
- **AirPlay** → Select "Milō" in your iPhone/iPad/Mac AirPlay outputs
- **Bluetooth** → Connect to "Milō · Bluetooth"
- **DLNA** → Select "Milo" as the renderer in any DLNA/UPnP controller app
- **Mac** → Install [Milō Mac](https://github.com/leodurandfr/Milo-Mac), then select "Milō" in audio outputs

### SSH

SSH is **off** by default and the image ships a factory password (`milo`) that is the same on
every unit — which is why nothing on the network accepts it. To open a shell from another
computer on your LAN:

1. Settings → Device → System → **Device password**: set your own.
2. Same page → **SSH**: turn it on. The switch stays inert until step 1 is done, and the backend
   refuses the same request, so a unit that never got a password can never be reached over SSH.
3. `ssh milo@milo.local`

The same password is what `sudo` asks for on the unit itself.

If the interface is unreachable — or on a **satellite**, which has no interface of its own —
create an empty file named `ssh` on the SD card's boot partition. Raspberry Pi OS enables the
server on the next boot and removes the file.

### Multiroom (Additional Speakers)

Flash the same image on additional Raspberry Pis. On first boot, the device detects your existing Milō server on the network and automatically configures itself as a client.

Prefer Ethernet over WiFi — network jitter is what makes rooms drift apart.

## Companion Apps

Control Milō from your other devices:

<table>
  <tr>
    <td><b>macOS</b></td>
    <td>Menu Bar app + audio output</td>
    <td><a href="https://github.com/leodurandfr/Milo-Mac">Milō Mac</a></td>
  </tr>
  <tr>
    <td><b>iOS</b></td>
    <td>Fullscreen web interface</td>
    <td><a href="https://github.com/leodurandfr/Milo-iOS">Milō iOS</a></td>
  </tr>
  <tr>
    <td><b>Android</b></td>
    <td>Fullscreen web interface</td>
    <td><a href="https://github.com/leodurandfr/Milo-Android">Milō Android</a></td>
  </tr>
</table>

## Built With

<table>
  <tr>
    <td><b>Backend</b></td>
    <td>Python, FastAPI, asyncio</td>
  </tr>
  <tr>
    <td><b>Frontend</b></td>
    <td>Vue 3, Pinia, Vite</td>
  </tr>
  <tr>
    <td><b>Audio</b></td>
    <td>ALSA, CamillaDSP, Snapcast, mpv, Navidrome, go-librespot, qobuz-proxy, tidal-connect, shairport-sync, bluez-alsa, gmediarender, ROC</td>
  </tr>
  <tr>
    <td><b>Platform</b></td>
    <td>Raspberry Pi OS (64-bit), systemd</td>
  </tr>
</table>

## Documentation

- [🏗️ Architecture](docs/architecture.md) — Technologies and how Milō works
- [🔌 API Overview](docs/api-overview.md) — REST + WebSocket surface at a glance
- [💻 Developer Guide](docs/development.md) — Setup, adding sources, testing, contributing
- [🔧 Wiring Reference](docs/hardware/wiring.md) — Physical connections (Pi + HiFiBerry)
- [📖 User Manual](docs/manual/manual_en.md) — End-user guide

## License

[GPL-3.0](LICENSE)
