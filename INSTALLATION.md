
# 01 · SETUP

## Étape 1: Préparer le système de base

1.  **Installer Raspberry Pi OS**

    ```bash
    # Téléchargez et flashez Raspberry Pi OS 64bit
    # Utilisez Raspberry Pi Imager: https://www.raspberrypi.com/software/
    # Pensez à activer le SSH
    ```
    
3.  **Premier démarrage et configuration**
    
    ```bash
    # Connectez-vous via ssh (
    sudo raspi-config
    
    # Configurez:
    # - Localization (langue, fuseau horaire, disposition du clavier)
    # - Interface Options: Enable I2C (pour l'écran tactile)
    # - Finish et reboot
    ```
    

## Étape 2: Installer les dépendances système

1.  **Mettre à jour le système**

    ```bash
    sudo apt update
    sudo apt upgrade -y
    ```
    
2.  **Installer les dépendances de base**

    ```bash
    sudo apt install -y git python3-pip python3-venv python3-dev libasound2-dev libssl-dev \
    cmake build-essential pkg-config nodejs npm

	#Mettre à jour node et npm
    sudo npm install -g n
    sudo n stable
    sudo npm install -g npm@latest
    hash -r

	#Vérifier l'installation
	node -v
	npm -v
    ```


## Installation de l'AMP2 Hifiberry :

**Configuration audio pour Hifiberry AMP2**

```bash
sudo nano /boot/firmware/config.txt
```

Supprimer :
```bash
dtparam=audio=on
```
Ajouter ",noaudio" ou "audio=off" en fonction de "kms" ou "fkms" :
```bash
dtoverlay=vc4-fkms-v3d,audio=off
dtoverlay=vc4-kms-v3d,noaudio
```
Ajouter l'ampli audio Hifiberry pour qu'il soit détécté :
```bash
#AMP2
dtoverlay=hifiberry-dacplus-std
#AMP4 Pro
dtoverlay=hifiberry-amp4pro
```
 
## Étape 3: Cloner et configurer oakOS

1.  **Cloner le dépôt**

    ```bash
    cd ~
    git clone https://github.com/Leshauts/oakOS.git
    cd oakOS
    ```
    
2.  **Configurer l'environnement Python**

    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    deactivate
    ```
    
3.  **Compiler le frontend**

    ```bash
    cd frontend
    npm install
    npm run build
    cd ..
    ```

### Supprimer PulseAudio/PipeWire


Désactivez et supprimez PulseAudio/PipeWire pour avoir utiliser uniquement ALSA:
```bash
sudo apt remove pulseaudio pipewire
sudo apt autoremove
```



# 02 · Installation "go-librespot"

## Guide d'installation go-librespot

### 1. Installation des prérequis
```bash
# Installation des dépendances nécessaires
sudo apt-get install -y libogg-dev libvorbis-dev libasound2-dev
```

### 2. Préparation de l'environnement
```bash
# Créer un dossier temporaire pour le téléchargement
mkdir -p ~/temp/go-librespot
cd ~/temp/go-librespot

# Télécharger go-librespot
wget https://github.com/devgianlu/go-librespot/releases/download/v0.2.0/go-librespot_linux_arm64.tar.gz

# Décompresser l'archive
tar -xvzf go-librespot_linux_arm64.tar.gz

# Déplacer l'exécutable dans /usr/local/bin pour une installation système
sudo cp go-librespot /usr/local/bin/
sudo chmod +x /usr/local/bin/go-librespot

# Créer le répertoire de configuration pour le service
sudo mkdir -p /var/lib/oakos/go-librespot
# Nécessaire pour accéder au fichier de configuration
sudo chown -R oakos:audio /var/lib/oakos/go-librespot
```

### 3. Configuration de go-librespot

Créer le fichier configuration de go-librespot :
```bash
# Créer le fichier de configuration principal
sudo tee /var/lib/oakos/go-librespot/config.yml > /dev/null << 'EOF'
# Configuration Spotify Connect
device_name: "oakOS-v0.2"
device_type: "speaker"
bitrate: 320

# Configuration Audio
audio_backend: "alsa"
audio_device: "default"

# Désactive le contrôle du volume via les applications Spotify
external_volume: true

# API
server:
  enabled: true
  address: "0.0.0.0"
  port: 3678
  allow_origin: "*"
EOF
```

** Nettoyer les fichiers d'installation : **
```bash
# Nettoyer les fichiers temporaires
cd ~
rm -rf ~/temp/go-librespot
```


Désactiver au démarrage (TESTER SANS)
```bash
sudo systemctl stop oakos-go-librespot.service
sudo systemctl disable oakos-go-librespot.service
# Recharger
sudo systemctl daemon-reload
# Status
sudo systemctl status oakos-go-librespot.service
```


# 03 · Installation "roc-toolkit"

## Installation sur Raspberry

### 1. Installation des prérequis
```bash
# Installation des dépendances nécessaires
sudo apt install -y g++ pkg-config scons ragel gengetopt libuv1-dev \
  libspeexdsp-dev libunwind-dev libsox-dev libsndfile1-dev libssl-dev libasound2-dev \
  libtool intltool autoconf automake make cmake avahi-utils
```

### 2. Compilation et installation

Dépendance "libpulse-dev" nécessaire pour l'installation de "roc-toolkit"
```bash
sudo apt install libpulse-dev
```
```bash
cd ~/oakOS
git clone https://github.com/roc-streaming/roc-toolkit.git
cd roc-toolkit
scons -Q --build-3rdparty=openfec
sudo scons -Q --build-3rdparty=openfec install
sudo ldconfig
```

### 3. Vérification
```bash
roc-recv --version
```



## Mac

### 1. Création le dispositif virtuel
```bash
roc-vad device add sender --name "oakOS · Network"
```

### 2. Récuperer l'ID du dispositif virtuel
```bash
roc-vad device list
```

### 3. Associer le dispositif virtuel avec l'ip du raspberry 
```bash
#Si "device list" affiche "6" pour le device virtuel et ajouter l'IP du Raspberry PI.
roc-vad device connect 6 \
  --source rtp+rs8m://192.168.1.152:10001 \
  --repair rs8m://192.168.1.152:10002 \
  --control rtcp://192.168.1.152:10003
```

### 4. Autres : gestion des dispositifs Mac
```bash
# Lister les dispositifs
roc-vad device list

# Voir les détails d'un dispositif
roc-vad device show 1

# Supprimer un dispositif
roc-vad device del 1

# Désactiver temporairement
roc-vad device disable 1
roc-vad device enable 1
```


# 04 · Installation "bluez-alsa"

## Plan d'installation et d'intégration optimisée pour oakOS

### 1. Installation optimisée de bluez-alsa

```bash
# Installation alternatives (à tester en premier maintenant)
sudo apt install -y \
  libasound2-dev \
  libbluetooth-dev \
  libdbus-1-dev \
  libglib2.0-dev \
  libsbc-dev \
  bluez \
  bluez-tools \
  pkg-config \
  build-essential \
  autotools-dev \
  automake \
  libtool

#Important de reboot
sudo reboot
```

```bash
# Cloner et installer bluez-alsa
cd ~/oakOS
git clone https://github.com/arkq/bluez-alsa.git
cd bluez-alsa
git checkout v4.3.1

# Générer les scripts de configuration
autoreconf --install

# Créer le répertoire de build
mkdir build && cd build

# Configuration optimisée pour oakOS (sans AAC)
../configure --prefix=/usr --enable-systemd \
  --with-alsaplugindir=/usr/lib/aarch64-linux-gnu/alsa-lib \
  --with-bluealsauser=$USER --with-bluealsaaplayuser=$USER \
  --enable-cli

# Compilation
make -j$(nproc)

# Installation
sudo make install
sudo ldconfig

```

### 2. Configuration Bluetooth optimisée pour oakOS (

```bash
# Modifier /etc/bluetooth/main.conf pour un appairage facile mais sécurisé
sudo tee /etc/bluetooth/main.conf > /dev/null << 'EOF'
[General]
Class = 0x240404
Name = oakOS
DiscoverableTimeout = 0
PairableTimeout = 0
ControllerMode = dual
Privacy = device
JustWorksRepairing = always

[Policy]
AutoEnable=false
ReconnectAttempts=0
EOF

```


Une fois ces fichiers créés, exécutez:

```bash
#Savoir si bluetooth bloqué
rfkill list

#Débloquer le bluetooth
sudo rfkill unblock bluetooth

# Vérifier que les services sont reconnus
#systemctl list-unit-files | grep blue

# Activer les services au démarrage
#sudo systemctl enable oakos-bluealsa-aplay.service
#sudo systemctl enable oakos-bluealsa.service

# Supprimer les originaux
sudo systemctl stop bluealsa-aplay.service
sudo systemctl stop bluealsa.service
sudo systemctl disable bluealsa-aplay.service
sudo systemctl disable bluealsa.service
```




# 05 · Installation de Snapcast

### 1. Téléchargement des packages v0.31.0

```bash
# Créer un dossier temporaire
mkdir -p ~/snapcast-install
cd ~/snapcast-install

# Télécharger snapserver
wget https://github.com/badaix/snapcast/releases/download/v0.31.0/snapserver_0.31.0-1_arm64_bookworm.deb

# Télécharger snapclient  
wget https://github.com/badaix/snapcast/releases/download/v0.31.0/snapclient_0.31.0-1_arm64_bookworm.deb

```

### 2. Installation

```bash
# Installer snapserver
sudo apt install ./snapserver_0.31.0-1_arm64_bookworm.deb

# Installer snapclient
sudo apt install ./snapclient_0.31.0-1_arm64_bookworm.deb

# Vérifier les versions installées
snapserver --version
snapclient --version

# Supprimer les fichiers téléchargés
rm -rf ~/snapcast-install
```


### 5. Supprimer les .service d'origine de "snapcast"
```bash
# Arrêter et désactiver snapserver et snapclient
sudo systemctl stop snapserver.service
sudo systemctl disable snapserver.service

sudo systemctl stop snapclient.service
sudo systemctl disable snapclient.service

```




# Configuration de Multiroom + Equalizer

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
```

```bash
# Redémarrer (important)
sudo reboot

# Vérifier que le loopback est bien chargé
lsmod | grep snd_aloop
```

**Installation de ALSA Equal** 
```bash
sudo apt-get install -y libasound2-plugin-equal
```


**Modifier le fichier : /etc/asound.conf ** 
```bash
sudo tee /etc/asound.conf > /dev/null << 'EOF'
# Configuration ALSA oakOS - Version corrigée sans double plug
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
pcm.oakos_spotify {
    @func concat
    strings [
        "pcm.oakos_spotify_"
        { @func getenv vars [ OAKOS_MODE ] default "direct" }
        { @func getenv vars [ OAKOS_EQUALIZER ] default "" }
    ]
}

# Bluetooth - Device configurable avec equalizer
pcm.oakos_bluetooth {
    @func concat
    strings [
        "pcm.oakos_bluetooth_"
        { @func getenv vars [ OAKOS_MODE ] default "direct" }
        { @func getenv vars [ OAKOS_EQUALIZER ] default "" }
    ]
}

# ROC - Device configurable avec equalizer
pcm.oakos_roc {
    @func concat
    strings [
        "pcm.oakos_roc_"
        { @func getenv vars [ OAKOS_MODE ] default "direct" }
        { @func getenv vars [ OAKOS_EQUALIZER ] default "" }
    ]
}

# === IMPLEMENTATIONS PAR MODE ===

# Mode MULTIROOM (via snapserver loopback) - SANS equalizer
pcm.oakos_spotify_multiroom {
    type plug
    slave.pcm {
        type hw
        card 1
        device 0
        subdevice 2
    }
}

pcm.oakos_bluetooth_multiroom {
    type plug
    slave.pcm {
        type hw
        card 1
        device 0
        subdevice 0
    }
}

pcm.oakos_roc_multiroom {
    type plug
    slave.pcm {
        type hw
        card 1
        device 0
        subdevice 1
    }
}

# Mode MULTIROOM - AVEC equalizer (vers equalizer multiroom)
pcm.oakos_spotify_multiroom_eq {
    type plug
    slave.pcm "equal_multiroom"
}

pcm.oakos_bluetooth_multiroom_eq {
    type plug
    slave.pcm "equal_multiroom"
}

pcm.oakos_roc_multiroom_eq {
    type plug
    slave.pcm "equal_multiroom"
}

# Mode DIRECT (vers HiFiBerry) - SANS equalizer
pcm.oakos_spotify_direct {
    type plug
    slave.pcm {
        type hw
        card sndrpihifiberry
        device 0
    }
}

pcm.oakos_bluetooth_direct {
    type plug
    slave.pcm {
        type hw
        card sndrpihifiberry
        device 0
    }
}

pcm.oakos_roc_direct {
    type plug
    slave.pcm {
        type hw
        card sndrpihifiberry
        device 0
    }
}

# Mode DIRECT - AVEC equalizer (sans double plug - CORRIGÉ)
pcm.oakos_spotify_direct_eq {
    type plug
    slave.pcm "equal"
}

pcm.oakos_bluetooth_direct_eq {
    type plug
    slave.pcm "equal"
}

pcm.oakos_roc_direct_eq {
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

# Paramètres globaux modifiables via l'interface oakOS hormis "sampleformat"
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



# 06 · Create systemd.service files 



## Backend

**oakos-backend.service** :

```bash
sudo tee /etc/systemd/system/oakos-backend.service > /dev/null << 'EOF'
[Unit]
Description=oakOS Backend Service
After=network.target

[Service]
Type=simple
User=oakos
Group=oakos
WorkingDirectory=/home/oakos/oakOS
ExecStart=/home/oakos/oakOS/venv/bin/python3 backend/main.py

Restart=always
RestartSec=5

# Timeout normal car systemd gère les plugins automatiquement
TimeoutStopSec=10

# Répertoire d'état
StateDirectory=oakos
StateDirectoryMode=0755

# Logs
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```



## ROC

**oakos-roc.service** 

```bash
sudo tee /etc/systemd/system/oakos-roc.service > /dev/null << 'EOF'
[Unit]
Description=oakOS ROC Audio Receiver
Documentation=https://roc-streaming.org/
After=network.target sound.service oakos-backend.service
Wants=network.target
BindsTo=oakos-backend.service

[Service]
Type=exec
User=oakos
Group=audio

EnvironmentFile=/etc/environment
Environment=HOME=/home/oakos

ExecStart=/usr/bin/roc-recv -vv \
  -s rtp+rs8m://0.0.0.0:10001 \
  -r rs8m://0.0.0.0:10002 \
  -c rtcp://0.0.0.0:10003 \
  -o alsa://oakos_roc

Restart=always
RestartSec=5

PrivateNetwork=false
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

# Journalisation
StandardOutput=journal
StandardError=journal
SyslogIdentifier=oakos-roc

[Install]
WantedBy=multi-user.target
EOF
```


## go-librespot

**oakos-go-librespot.service** 
```bash
sudo tee /etc/systemd/system/oakos-go-librespot.service > /dev/null << 'EOF'
[Unit]
Description=oakOS Spotify Connect via go-librespot
After=network-online.target sound.service oakos-backend.service
Wants=network-online.target
BindsTo=oakos-backend.service

[Service]
Type=simple
User=oakos
Group=audio

ExecStart=/usr/local/bin/go-librespot --config_dir /var/lib/oakos/go-librespot
Environment=HOME=/home/oakos
WorkingDirectory=/var/lib/oakos
Restart=always
RestartSec=5

# Journalisation
StandardOutput=journal
StandardError=journal
SyslogIdentifier=oakos-go-librespot

[Install]
WantedBy=multi-user.target
EOF
```


## Bluealsa :

**oakos-bluealsa-aplay.service**
```bash
sudo tee /etc/systemd/system/oakos-bluealsa-aplay.service > /dev/null << 'EOF'
[Unit]
Description=BlueALSA player for oakOS
Requires=oakos-bluealsa.service
After=oakos-bluealsa.service oakos-backend.service
BindsTo=oakos-backend.service oakos-bluealsa.service

[Service]
Type=simple
User=oakos

ExecStart=/usr/bin/bluealsa-aplay --pcm=oakos_bluetooth --profile-a2dp 00:00:00:00:00:00

RestartSec=2
Restart=always
PrivateTmp=false
PrivateDevices=false

[Install]
WantedBy=multi-user.target
EOF
```

**oakos-bluealsa.service**
```bash
sudo tee /etc/systemd/system/oakos-bluealsa.service > /dev/null << 'EOF'
[Unit]
Description=BluezALSA daemon for oakOS
Documentation=man:bluealsa(8)
After=dbus.service bluetooth.service oakos-backend.service
Requires=dbus.service
Wants=bluetooth.service
BindsTo=oakos-backend.service

[Service]
Type=simple
ExecStart=/usr/bin/bluealsa -S -p a2dp-sink
User=oakos
Group=audio
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
```


## Snapcast

**oakos-snapserver-multiroom.service**


```bash
sudo tee /etc/systemd/system/oakos-snapserver-multiroom.service > /dev/null << 'EOF'
[Unit]
Description=Snapcast Server for oakOS Multiroom
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/snapserver -c /etc/snapserver.conf
User=oakos
Group=audio
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

**oakos-snapclient-multiroom.service**

```bash
sudo tee /etc/systemd/system/oakos-snapclient-multiroom.service > /dev/null << 'EOF'
[Unit]
Description=Snapcast Client for oakOS Multiroom
After=network-online.target oakos-snapserver-multiroom.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/snapclient -h 127.0.0.1 -p 1704 --logsink=system
User=oakos
Group=audio
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

**Démarrage automatique **

```bash
sudo systemctl daemon-reload
sudo systemctl enable oakos-snapclient-multiroom.service
sudo systemctl enable oakos-snapserver-multiroom.service
sudo systemctl enable oakos-bluealsa-aplay.service
sudo systemctl enable oakos-bluealsa.service
sudo systemctl start oakos-snapclient-multiroom.service
sudo systemctl start oakos-snapserver-multiroom.service
sudo systemctl start oakos-bluealsa-aplay.service
sudo systemctl start oakos-bluealsa.service
```


**Commande pour faire passer toutes les sources audio "snapserver" sur "Multiroom".**
```bash
curl -s http://localhost:1780/jsonrpc -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' | grep -o '"id":"[a-f0-9-]*","muted"' | cut -d'"' -f4 | while read group_id; do curl -s http://localhost:1780/jsonrpc -d "{\"id\":1,\"jsonrpc\":\"2.0\",\"method\":\"Group.SetStream\",\"params\":{\"id\":\"$group_id\",\"stream_id\":\"Multiroom\"}}"; echo "→ $group_id switched"; done
```