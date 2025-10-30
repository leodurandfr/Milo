
<picture>
<img style="pointer-events:none" src="https://leodurand.com/_autres/cover-milo-github@2x.png" />
</picture>

# Milō (🚧 WIP 🚧)

Transform your Raspberry Pi into a multiroom audio system with Spotify Connect, Bluetooth, and network streaming. Responsive touch interface with real-time synchronization.

## ✨ Features

- **Multiple audio sources**
  - 🎵 Spotify Connect (playback control, metadata)
  - 📱 Bluetooth (quick connect/disconnect)
  - 💻 Mac streaming (system audio over network)
  - 📻 Internet Radio (50,000+ stations worldwide)
- **Synchronized multiroom** (snapcast)
- **Settings** to manage Milō entirely
- **10-band equalizer** with presets
- **Unified volume control** (touch + rotary encoder)
- **Responsive interface** (8 supported languages)

## 🎛️ Companion apps

- [**Milō Mac**](https://github.com/leodurandfr/Milo-Mac) - Add "Milō" in your mac audio output and let you control Milō from the Menu Bar app.
- [**Milō iOS**](https://github.com/leodurandfr/Milo-iOS) - iOS app (fullscreen web interface)
- [**Milō Android**](https://github.com/leodurandfr/Milo-Android) - Android app (fullscreen web interface)

## 🔧 Hardware requirements

- **Raspberry Pi 4 or 5** (64-bit)
- **Audio card** (HiFiBerry recommended: Amp2, Amp4, Amp4 Pro, Amp100, Beocreate)
- **Touch screen** (optional: Waveshare 7" USB or 8" DSI)
- **Rotary encoder** (optional: volume control)

## 🚀 Quick installation

### Milō (main installation)

```bash
wget https://raw.githubusercontent.com/leodurandfr/Milo/main/install.sh
chmod +x install.sh
./install.sh
```

The script will guide you through:
- Configuring the hostname (`milo`)
- Selecting your HiFiBerry audio card
- Configuring your touch screen
- Installing all dependencies automatically

**Access after installation:**
- Web interface: **http://milo.local**
- Spotify Connect: Select **"Milō"** in the Spotify app
- Bluetooth: Connect to **"Milō · Bluetooth"**
- Mac audio: After installing [**Milō Mac**](https://github.com/leodurandfr/Milo-Mac), select **"Milo"** in your mac audio output.

**Uninstall:**
```bash
./install.sh --uninstall
```

### Milō Sat (multiroom satellites)

Install Milō Sat on other Raspberry Pis to create a synchronized multiroom system.

```bash
wget https://raw.githubusercontent.com/leodurandfr/Milo/main/milo-sat/install-sat.sh
chmod +x install-sat.sh
./install-sat.sh
```

**Uninstall:**
```bash
./install-sat.sh --uninstall
```

## 📚 Documentation

- **[🏗️ Architecture & Technologies](docs/architecture.md)** - How Milō works
- **[💻 Developer Guide](docs/development.md)** - Contribute to the project
- **[🔑 GitHub Token Setup](docs/github-token.md)** - Configure automatic updates (recommended)

## 📝 License



