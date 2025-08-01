
# 07 · Configuration de Multiroom + Equalizer

#### Configuration Snapserver Raspberry #1 :

**Création de ALSA Loopback**
```bash
# Création snd-aloop.cong via modules-load.d 
echo "snd_aloop" | sudo tee /etc/modules-load.d/snd-aloop.conf

# Créer la configuration pour le module
sudo tee /etc/modprobe.d/snd-aloop.conf << 'EOF'

# Configuration du module loopback ALSA
options snd-aloop index=1 enable=1
EOF

# Redémarrer (important)
sudo reboot
```

```bash
# Vérifier que le loopback est bien chargé
lsmod | grep snd_aloop
```

**Installation de ALSA Equal** 
```bash
sudo apt-get install -y libasound2-plugin-equal
```


**Modifier le fichier : /etc/asound.conf** 
```bash
sudo tee /etc/asound.conf > /dev/null << 'EOF'
# Configuration ALSA Milo - Version corrigée sans double plug
pcm.!default {
    type plug
    slave.pcm {
        type hw
        card sndrpihifiberry
        device 0
    }
}

ctl.!default {
    type hw
    card sndrpihifiberry
}

# === DEVICES DYNAMIQUES POUR OAKOS (avec support equalizer) ===

# Spotify - Device configurable avec equalizer
pcm.milo_spotify {
    @func concat
    strings [
        "pcm.milo_spotify_"
        { @func getenv vars [ OAKOS_MODE ] default "direct" }
        { @func getenv vars [ OAKOS_EQUALIZER ] default "" }
    ]
}

# Bluetooth - Device configurable avec equalizer
pcm.milo_bluetooth {
    @func concat
    strings [
        "pcm.milo_bluetooth_"
        { @func getenv vars [ OAKOS_MODE ] default "direct" }
        { @func getenv vars [ OAKOS_EQUALIZER ] default "" }
    ]
}

# ROC - Device configurable avec equalizer
pcm.milo_roc {
    @func concat
    strings [
        "pcm.milo_roc_"
        { @func getenv vars [ OAKOS_MODE ] default "direct" }
        { @func getenv vars [ OAKOS_EQUALIZER ] default "" }
    ]
}

# === IMPLEMENTATIONS PAR MODE ===

# Mode MULTIROOM (via snapserver loopback) - SANS equalizer
pcm.milo_spotify_multiroom {
    type plug
    slave.pcm {
        type hw
        card 1
        device 0
        subdevice 2
    }
}

pcm.milo_bluetooth_multiroom {
    type plug
    slave.pcm {
        type hw
        card 1
        device 0
        subdevice 0
    }
}

pcm.milo_roc_multiroom {
    type plug
    slave.pcm {
        type hw
        card 1
        device 0
        subdevice 1
    }
}

# Mode MULTIROOM - AVEC equalizer (vers equalizer multiroom)
pcm.milo_spotify_multiroom_eq {
    type plug
    slave.pcm "equal_multiroom"
}

pcm.milo_bluetooth_multiroom_eq {
    type plug
    slave.pcm "equal_multiroom"
}

pcm.milo_roc_multiroom_eq {
    type plug
    slave.pcm "equal_multiroom"
}

# Mode DIRECT (vers HiFiBerry) - SANS equalizer
pcm.milo_spotify_direct {
    type plug
    slave.pcm {
        type hw
        card sndrpihifiberry
        device 0
    }
}

pcm.milo_bluetooth_direct {
    type plug
    slave.pcm {
        type hw
        card sndrpihifiberry
        device 0
    }
}

pcm.milo_roc_direct {
    type plug
    slave.pcm {
        type hw
        card sndrpihifiberry
        device 0
    }
}

# Mode DIRECT - AVEC equalizer (sans double plug - CORRIGÉ)
pcm.milo_spotify_direct_eq {
    type plug
    slave.pcm "equal"
}

pcm.milo_bluetooth_direct_eq {
    type plug
    slave.pcm "equal"
}

pcm.milo_roc_direct_eq {
    type plug
    slave.pcm "equal"
}

# === EQUALIZERS FIXES ===

# Equalizer pour mode direct (contrôlable via alsamixer -D equal)
pcm.equal {
    type equal
    slave.pcm "plughw:sndrpihifiberry"
}

# Equalizer pour mode multiroom (même réglages, différente sortie)
pcm.equal_multiroom {
    type equal
    slave.pcm "plughw:1,0"
}

# Control pour l'equalizer principal (alsamixer)
ctl.equal {
    type equal
}
EOF
```


**Configuration du serveur snapcast**

```bash
sudo tee /etc/snapserver.conf > /dev/null << 'EOF'
# /etc/snapserver.conf
[stream]
default = Multiroom

# Paramètres globaux modifiables via l'interface Milo hormis "sampleformat"
buffer = 1000
codec = pcm
chunk_ms = 20
sampleformat = 48000:16:2

# Meta source : Spotify + Bluetooth + Roc
source = meta:///Bluetooth/ROC/Spotify?name=Multiroom

# Source Bluetooth
source = alsa:///?name=Bluetooth&device=hw:1,1,0

# Source ROC
source = alsa:///?name=ROC&device=hw:1,1,1

# Source Spotify
source = alsa:///?name=Spotify&device=hw:1,1,2

[http]
enabled = true
bind_to_address = 0.0.0.0
port = 1780
doc_root = /usr/share/snapserver/snapweb/

[server]
threads = -1

[logging]
enabled = true
EOF
```

