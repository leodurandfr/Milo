# Milo - Audio System for Raspberry Pi

Milo is an advanced audio application for Raspberry Pi that transforms your device into a versatile audio platform. The system allows seamless switching between different audio sources (Spotify, Bluetooth, macOS via Snapcast, Web Radio) while offering a sleek and responsive user interface. 

## Features

- Audio sources :
  - Spotify (displays music playing and playback buttons)
  - Bluetooth (displays connected device and a button to disconnect the connected device)
  - Wireless Mac audio output (displays roc-vad connected device)
- Multi-room audio streaming
- Equalizer
- Modern touchscreen user interface
- Cross-device synchronization
- Centralized volume control

## Additional applications 
- [Milo Mac](https://github.com/Leshauts/Milo-Mac) : Native look and feel app in Menu Bar to control Milo from your Mac.
- [Milo iOS](https://github.com/Leshauts/Milo-iOS) : iOS app that displays milo.local in full screen [wip: need to add project in Github]
- Milo Android : Android app that displays milo.local in full screen [wip: need to add project in Github]

## Daemon used for this application
- go-librespot
- bluez-alsa
- roc
- snapcast
- alsaequal

## Requierments
- Raspberry Pi (tested on 4 and 5)
- Audio Amplifier Hat (like Hifiberry)
- Touch display (better to display the app interface)

## Installation
```
wget https://raw.githubusercontent.com/Leshauts/Milo/main/install.sh
chmod +x install.sh
./install.sh
```
**Uninstall:**
```
./install.sh --uninstall
````
