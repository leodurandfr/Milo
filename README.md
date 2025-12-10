
<picture>
<img style="pointer-events:none" src="https://leodurand.com/_autres/cover-milo-github@2x.png" />
</picture>

# Milō (🚧 WIP 🚧)

Transform your Raspberry Pi into a multiroom audio system with Spotify Connect, Bluetooth, Internet Radio, Podcasts, and Mac streaming. Responsive touch interface with real-time synchronization.

## ✨ Features

- **Multiple audio sources**
  - 🎵 Spotify Connect — See what's playing and control playback
  - 📱 Bluetooth — Instant pairing, play from any device
  - 💻 Mac — Your Mac's audio, wirelessly
  - 📻 Radio — Explore 50,000+ stations worldwide
  - 🎙️ Podcasts — Search, subscribe and resume
- **Synchronized multiroom** (snapcast)
- **Settings** — Language, volume, screen, routing, and more
- **10-band equalizer** with presets
- **Unified volume control** (touch + rotary encoder)
- **Responsive interface** (EN, FR, DE, ES, PT, IT, ZH, HI)
- **Automatic updates** for Milō and dependencies

## 🎛️ Companion apps

- [**Milō Mac**](https://github.com/leodurandfr/Milo-Mac) - Add "Milō" to your Mac audio outputs and control playback from the Menu Bar.
- [**Milō iOS**](https://github.com/leodurandfr/Milo-iOS) - iOS app (fullscreen web interface)
- [**Milō Android**](https://github.com/leodurandfr/Milo-Android) - Android app (fullscreen web interface)

## 🔧 Hardware requirements

- **Raspberry Pi 4 or 5** (64-bit)
- **Audio card** (HiFiBerry recommended: Amp2, Amp4, Amp4 Pro, Amp100, Beocreate)
- **Touch screen** (optional: Waveshare 7" USB or 8" DSI)
- **Rotary encoder** (optional: volume control)

## 🚀 Quick installation

### Milō (main installation)

**1. Prepare the SD card**

Download and open [Raspberry Pi Imager](https://www.raspberrypi.com/software/):
- Select your Raspberry Pi model (Raspberry Pi 4 or 5)
- Choose **"Raspberry Pi OS (64-bit) Lite"** (based on Debian Trixie)
- Select your microSD card
- Click **"Next"** → **"Edit Settings"**
- Configure:
  - Hostname: `milo`
  - Username: `milo`
  - Password: choose your password
  - WiFi: configure if not using Ethernet
- Click **"Save"** → **"Yes"**

Once flashing is complete, insert the microSD card into your Raspberry Pi and power it on. Wait a few minutes for the first boot to complete.

**2. Run the installation script**

Connect via SSH and run:
```bash
wget https://raw.githubusercontent.com/leodurandfr/Milo/main/install.sh
chmod +x install.sh
./install.sh
```

The script will guide you through:
- Selecting your HiFiBerry audio card
- Configuring your touch screen (optional)
- Installing all dependencies automatically

**Access after installation:**
- Web interface: **http://milo.local**
- Spotify Connect: Select **"Milō"** in the Spotify app
- Bluetooth: Connect to **"Milō · Bluetooth"**
- Mac audio: After installing [**Milō Mac**](https://github.com/leodurandfr/Milo-Mac), select **"Milo"** in your Mac audio output
- Podcasts: Browse and subscribe in **Settings → Podcasts**

**Uninstall:**
```bash
./install.sh --uninstall
```

### Milō Sat (multiroom satellites)

Install Milō Sat on additional Raspberry Pis to create a synchronized multiroom system.

**1. Prepare the SD card**

On [Raspberry Pi Imager](https://www.raspberrypi.com/software/):
- Select your Raspberry Pi model (Raspberry Pi 4 or 5)
- Choose **"Raspberry Pi OS (64-bit) Lite"** (based on Debian Trixie)
- Select your microSD card
- Click **"Next"** → **"Edit Settings"**
- Configure:
  - Hostname: `milo-sat-01` (use `milo-sat-02`, `milo-sat-03`, etc. for additional satellites)
  - Username: `milo-sat-01` (match the hostname)
  - Password: choose your password
  - WiFi: configure if not using Ethernet
- Click **"Save"** → **"Yes"**

Once flashing is complete, insert the microSD card into your Raspberry Pi and power it on. Wait a few minutes for the first boot to complete.

**2. Run the installation script**

Connect via SSH and run:
```bash
wget https://raw.githubusercontent.com/leodurandfr/Milo/main/milo-sat/install-sat.sh
chmod +x install-sat.sh
./install-sat.sh
```

The script will guide you through:
- Selecting your HiFiBerry audio card
- Configuring Snapcast client settings
- Installing all dependencies automatically

**Uninstall:**
```bash
./install-sat.sh --uninstall
```

## 📚 Documentation

- **[🏗️ Architecture & Technologies](docs/architecture.md)** - How Milō works
- **[💻 Developer Guide](docs/development.md)** - Contribute to the project
- **[🔑 GitHub Token Setup](docs/github-token.md)** - Configure automatic updates (recommended)

## 📝 License

This project is licensed under the [MIT](LICENSE) license.

