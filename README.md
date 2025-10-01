
<picture>
<img style="pointer-events:none" src="https://leodurand.com/_autres/cover-milo-github@2x.png" />
</picture>

# Milō

Milō is an audio application for Raspberry Pi that transforms your device into a versatile audio platform. The system allows seamless switching between different audio sources (Spotify, Bluetooth, Mac audio output) while offering a sleek and responsive user interface. 

## Features

- Audio sources :
  - Spotify (displays music playing and playback buttons)
  - Bluetooth (displays connected device and a button to disconnect the connected device)
  - Mac audio output (displays connected Mac name)
- Multiroom audio streaming
- Equalizer
- Cross-device synchronization
- Centralized volume control

## Additional applications 
- [Milō Mac](https://github.com/Leshauts/Milo-Mac) : Native look and feel app in Menu Bar to control Milō from your Mac.
- [Milō iOS](https://github.com/Leshauts/Milo-iOS) : iOS app that displays milo.local in full screen
- [Milō Android](https://github.com/Leshauts/Milo-Android) : Android app that displays milo.local in full screen

## Dependencies
- go-librespot (Spotify Connect)
- bluez-alsa (Bluetooth Audio)
- roc-streaming (Mac audio ouput)
- snapcast (Multiroom)
- alsaequal (Equalizer)

## Hardware
- Raspberry Pi (tested on 4 and 5)
- Audio Amplifier Hat (like Hifiberry)
- Touch display (optional)
- Rotary encoder (optional)

## Installation for Milō
```
wget https://raw.githubusercontent.com/Leshauts/Milo/main/install.sh
chmod +x install.sh
./install.sh
```

**Uninstall Milō:**
```
./install.sh --uninstall
```

## Installation for Milō Sat
Enjoy Multiroom by installing Milō Sat on other Raspberry Pi devices with an audio‑amp hat and listen to your music all around your home.
```
wget https://raw.githubusercontent.com/Leshauts/Milo/main/milo-sat/install-sat.sh
chmod +x install-sat.sh
./install-sat.sh
```

**Uninstall Milō Sat:**
```
./install-sat.sh --uninstall
```