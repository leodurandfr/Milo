
<picture>
<img style="pointer-events:none" src="https://leodurand.com/_autres/cover-milo-github@2x.png" />
</picture>

# Milō

Transform your Raspberry Pi into a multiroom audio system with Spotify Connect, Bluetooth, and network streaming. Responsive touch interface with real-time synchronization.

## ✨ Features

- **Multiple audio sources**
  - 🎵 Spotify Connect (playback control, metadata)
  - 📱 Bluetooth (quick connect/disconnect)
  - 💻 Mac streaming (system audio over network)
- **Synchronized multiroom** (snapcast)
- **10-band equalizer** with presets
- **Unified volume control** (touch + rotary encoder)
- **Responsive interface** (8 supported languages)

## 🎛️ Companion apps

- [**Milō Mac**](https://github.com/Leshauts/Milo-Mac) - Native Menu Bar app to control Milō from macOS
- [**Milō iOS**](https://github.com/Leshauts/Milo-iOS) - iOS app (fullscreen web interface)
- [**Milō Android**](https://github.com/Leshauts/Milo-Android) - Android app (fullscreen web interface)

## 🔧 Hardware requirements

- **Raspberry Pi 4 or 5** (64-bit)
- **Audio card** (HiFiBerry recommended: Amp2, Amp4, Amp4 Pro, Amp100, Beocreate)
- **Touch screen** (optional: Waveshare 7" USB or 8" DSI)
- **Rotary encoder** (optional: volume control)

## 🚀 Quick installation

### Milō (main installation)

```bash
wget https://raw.githubusercontent.com/Leshauts/Milo/main/install.sh
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

**Uninstall:**
```bash
./install.sh --uninstall
```

### Milō Sat (multiroom satellites)

Install Milō Sat on other Raspberry Pis to create a synchronized multiroom system.

```bash
wget https://raw.githubusercontent.com/Leshauts/Milo/main/milo-sat/install-sat.sh
chmod +x install-sat.sh
./install-sat.sh
```

**Uninstall:**
```bash
./install-sat.sh --uninstall
```

## 📚 Documentation

### For users

- **[🏗️ Architecture & Technologies](docs/architecture.md)** - How Milō works
- **[🔑 GitHub Token Setup](docs/github-token.md)** - Configure automatic updates (recommended)

### For developers

- **[💻 Developer Guide](docs/development.md)** - Contribute to the project
- **[📋 CLAUDE.md](CLAUDE.md)** - Complete technical documentation (not committed to git)

## 🛠️ Technologies used

- **Backend:** Python + FastAPI (DDD architecture, async/await)
- **Frontend:** Vue 3 + Vite (Composition API, Pinia)
- **Audio:**
  - **Spotify:** go-librespot (Spotify Connect)
  - **Bluetooth:** bluez-alsa (A2DP)
  - **Streaming:** roc-toolkit (FEC error correction)
  - **Multiroom:** snapcast (<1ms synchronization)
  - **Equalizer:** alsaequal (10 bands)

## 🔒 Security & Performance

- Global rate limiting (100 req/min)
- Restricted CORS (localhost + milo.local)
- Log rotation (100MB max, 7 days)
- Settings with SHA256 checksum
- WebSocket with auto-reconnect

## ⚡ Recommended post-installation setup

### GitHub Token (5000/h API limit instead of 60/h)

Enables more frequent automatic dependency updates.

**Quick guide:**
1. Create a token at https://github.com/settings/tokens (scope: `public_repo`)
2. Edit the service: `sudo nano /etc/systemd/system/milo-backend.service`
3. Replace `YOUR_GITHUB_TOKEN_HERE` with your token
4. Reload: `sudo systemctl daemon-reload && sudo systemctl restart milo-backend`

**[📖 Detailed guide with explanations](docs/github-token.md)**

## 🤝 Contributing

Contributions are welcome! Check the [developer guide](docs/development.md) to get started.

1. Fork the project
2. Create a branch (`git checkout -b feature/amazing-feature`)
3. Commit (`git commit -m 'feat: add amazing feature'`)
4. Push (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📝 License



## 🐛 Support

- **Issues:** https://github.com/Leshauts/Milo/issues
- **Discussions:** https://github.com/Leshauts/Milo/discussions
