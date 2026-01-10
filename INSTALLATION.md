# Installation file v0.2

————————————————————————————————————————————————

To do:
[ ] Need to be optimized
[ ] Need to be translated
[ ] Make a script?


————————————————————————————————————————————————




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
    sudo apt update
    sudo apt upgrade -y
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
Désactiver la limite de puissance par USB (inutile sur Rpi 4 et 5):
```bash
usb_max_current_enable=1
```
 
## Étape 3: Cloner et configurer Milo

1.  **Cloner le dépôt**

    ```bash
    cd ~
    git clone https://github.com/leodurandfr/Milo.git
    cd Milo
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
wget https://github.com/devgianlu/go-librespot/releases/download/v0.3.1/go-librespot_linux_arm64.tar.gz

# Décompresser l'archive
tar -xvzf go-librespot_linux_arm64.tar.gz

# Déplacer l'exécutable dans /usr/local/bin pour une installation système
sudo cp go-librespot /usr/local/bin/
sudo chmod +x /usr/local/bin/go-librespot

# Créer le répertoire de configuration pour le service
sudo mkdir -p /var/lib/milo/go-librespot
# Nécessaire pour accéder au fichier de configuration
sudo chown -R milo:audio /var/lib/milo/go-librespot
```

### 3. Configuration de go-librespot

Créer le fichier configuration de go-librespot :
```bash
# Créer le fichier de configuration principal
sudo tee /var/lib/milo/go-librespot/config.yml > /dev/null << 'EOF'
# Configuration Spotify Connect
device_name: "Milo"
device_type: "speaker"
bitrate: 320

# Configuration Audio > ALSA
audio_backend: "alsa"
audio_device: "milo_spotify"

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

**Nettoyer les fichiers d'installation :**
```bash
# Nettoyer les fichiers temporaires
cd ~
rm -rf ~/temp
```







# 03 · Installation "roc-toolkit"

## Installation sur Raspberry

### 1. Installation des prérequis
```bash
# Installation des dépendances nécessaires
sudo apt install -y g++ pkg-config scons ragel gengetopt libuv1-dev \
  libspeexdsp-dev libunwind-dev libsox-dev libsndfile1-dev libssl-dev libasound2-dev \
  libtool intltool autoconf automake make cmake avahi-utils libpulse-dev
```

### 2. Compilation et installation


```bash
cd ~/Milo
git clone https://github.com/roc-streaming/roc-toolkit.git
cd roc-toolkit
scons -Q --build-3rdparty=openfec
sudo scons -Q --build-3rdparty=openfec install
sudo ldconfig
```

```bash
# Supprimer les fichiers d'installation
rm -rf ~/Milo/roc-toolkit
```

### 3. Vérification
```bash
roc-recv --version
```



## ⚠️ Installation sur Mac 🖥️

### 1. Création le dispositif virtuel
```bash
roc-vad device add sender --name "Milo · Network"
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

## Plan d'installation et d'intégration optimisée pour Milo

### 1. Installation optimisée de bluez-alsa

```bash
# Installation alternatives
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
cd ~/Milo
git clone https://github.com/arkq/bluez-alsa.git
cd bluez-alsa
git checkout v4.3.1

# Générer les scripts de configuration
autoreconf --install

# Créer le répertoire de build
mkdir build && cd build

# Configuration optimisée pour Milo (sans AAC)
../configure --prefix=/usr --enable-systemd \
  --with-alsaplugindir=/usr/lib/aarch64-linux-gnu/alsa-lib \
  --with-bluealsauser=$USER --with-bluealsaaplayuser=$USER \
  --enable-cli

# Compilation
make -j$(nproc)

# Installation
sudo make install
sudo ldconfig

# Supprimer les fichiers d'installation
rm -rf ~/Milo/bluez-alsa
```



### 2. Arrêter et désactiver les .service par defaut liés au bluetooth

```bash
sudo systemctl stop bluealsa-aplay.service
sudo systemctl stop bluealsa.service
sudo systemctl disable bluealsa-aplay.service
sudo systemctl disable bluealsa.service
```



### 2. Si on utilise un dongle usb Bluetooth (normalement pas utile si Raspberry pi 5)

Désactiver le bluetooth intégré au raspberry :

```bash
sudo nano /boot/firmware/config.txt
```
Ajouter sous [all]
```bash
dtoverlay=disable-bt
```

Vérifier si bluetooth est bloqué
```bash
sudo hciconfig hci0 up
# Vérfier quand le plugin "bluetooth" est actif, doit afficher : UP RUNNING SCAN (et non "DOWN")
hciconfig

# Débloquer le bluetooth USB
sudo rfkill unblock bluetooth
# Vérifier que tout doit est sur "no"
rfkill list
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

# Supprimer les fichiers téléchargés
rm -rf ~/snapcast-install

# Vérifier les versions installées
snapserver --version
snapclient --version
```


### 3. Arrêter et désactiver les .service par defaut de "snapcast"
```bash
sudo systemctl stop snapserver.service
sudo systemctl disable snapserver.service

sudo systemctl stop snapclient.service
sudo systemctl disable snapclient.service

```


# 06 · Create systemd.service files 



## Backend

**milo-backend.service** :

```bash
sudo tee /etc/systemd/system/milo-backend.service > /dev/null << 'EOF'
[Unit]
Description=Milo Backend Service
After=network.target

[Service]
Type=simple
User=milo
Group=milo
WorkingDirectory=/home/milo/Milo
ExecStart=/home/milo/Milo/venv/bin/python3 backend/main.py

Restart=always
RestartSec=5

# Timeout normal car systemd gère les plugins automatiquement
TimeoutStopSec=10

# Répertoire d'état
StateDirectory=milo
StateDirectoryMode=0755

# Logs
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
```

## Frontend

**milo-frontend.service**

```bash
sudo tee /etc/systemd/system/milo-frontend.service > /dev/null << 'EOF'
[Unit]
Description=Milo Frontend Service
After=network.target

[Service]
Type=simple
User=milo
Group=milo
WorkingDirectory=/home/milo/Milo/frontend

# Build et serve en production
ExecStartPre=/usr/bin/npm run build
ExecStart=/usr/bin/npm run preview -- --host 0.0.0.0 --port 3000

Restart=always
RestartSec=5
TimeoutStopSec=10

# Répertoire d'état
StateDirectory=milo
StateDirectoryMode=0755

# Variables d'environnement
Environment=NODE_ENV=production
Environment=HOME=/home/milo

# Logs
StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-frontend

[Install]
WantedBy=multi-user.target
EOF
```


## Kiosk mode

```bash
sudo tee /etc/systemd/system/milo-kiosk.service > /dev/null << 'EOF'
[Unit]
Description=Milo Kiosk Mode (Chromium Fullscreen)
After=graphical.target milo-frontend.service
Wants=graphical.target
Requires=milo-frontend.service

[Service]
Type=simple
User=milo
Group=milo
Environment=DISPLAY=:0
Environment=HOME=/home/milo
Environment=XDG_RUNTIME_DIR=/run/user/1000

# Attendre que le frontend soit prêt
ExecStartPre=/bin/sleep 8

# Lancer Chromium en mode kiosque tactile
ExecStart=/usr/bin/chromium-browser \
  --kiosk \
  --incognito \
  --no-first-run \
  --disable-infobars \
  --disable-notifications \
  --disable-popup-blocking \
  --disable-session-crashed-bubble \
  --disable-restore-session-state \
  --disable-background-timer-throttling \
  --disable-backgrounding-occluded-windows \
  --disable-renderer-backgrounding \
  --disable-translate \
  --disable-sync \
  --hide-scrollbars \
  --disable-background-networking \
  --autoplay-policy=no-user-gesture-required \
  --start-fullscreen \
  --no-sandbox \
  --disable-dev-shm-usage \
  --hide-cursor \
  --touch-events=enabled \
  --enable-features=TouchpadAndWheelScrollLatching \
  --force-device-scale-factor=1 \
  --disable-pinch \
  --disable-features=VizDisplayCompositor \
  --app=http://localhost


Restart=always
RestartSec=5
TimeoutStopSec=5

[Install]
WantedBy=graphical.target
EOF
```



## Mac (ROC)

**milo-mac.service**

```bash
sudo tee /etc/systemd/system/milo-mac.service > /dev/null << 'EOF'
[Unit]
Description=Milo Mac Audio Receiver (ROC)
Documentation=https://roc-streaming.org/
After=network.target sound.service milo-backend.service
Wants=network.target
BindsTo=milo-backend.service

[Service]
Type=exec
User=milo
Group=audio

EnvironmentFile=/etc/environment
Environment=HOME=/home/milo

ExecStart=/usr/bin/roc-recv -vv \
  -s rtp+rs8m://0.0.0.0:10001 \
  -r rs8m://0.0.0.0:10002 \
  -c rtcp://0.0.0.0:10003 \
  -o alsa://milo_roc

Restart=always
RestartSec=5

PrivateNetwork=false
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

# Journalisation
StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-mac

[Install]
WantedBy=multi-user.target
EOF
```


## Spotify (go-librespot)

**milo-spotify.service**
```bash
sudo tee /etc/systemd/system/milo-spotify.service > /dev/null << 'EOF'
[Unit]
Description=Milo Spotify Connect via go-librespot
After=network-online.target sound.service milo-backend.service
Wants=network-online.target
BindsTo=milo-backend.service

[Service]
Type=simple
User=milo
Group=audio

ExecStart=/usr/local/bin/go-librespot --config_dir /var/lib/milo/go-librespot
Environment=HOME=/home/milo
WorkingDirectory=/var/lib/milo
Restart=always
RestartSec=5

# Journalisation
StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-spotify

[Install]
WantedBy=multi-user.target
EOF
```


## Bluealsa :

**milo-bluealsa-aplay.service**
```bash
sudo tee /etc/systemd/system/milo-bluealsa-aplay.service > /dev/null << 'EOF'
[Unit]
Description=BlueALSA player for Milo
Requires=milo-bluealsa.service
After=milo-bluealsa.service milo-backend.service
BindsTo=milo-backend.service milo-bluealsa.service

[Service]
Type=simple
User=milo

ExecStart=/usr/bin/bluealsa-aplay --pcm=milo_bluetooth --profile-a2dp 00:00:00:00:00:00

RestartSec=2
Restart=always
PrivateTmp=false
PrivateDevices=false

[Install]
WantedBy=multi-user.target
EOF
```

**milo-bluealsa.service**
```bash
sudo tee /etc/systemd/system/milo-bluealsa.service > /dev/null << 'EOF'
[Unit]
Description=BluezALSA daemon for Milo
Documentation=man:bluealsa(8)
After=dbus.service bluetooth.service milo-backend.service
Requires=dbus.service
Wants=bluetooth.service
BindsTo=milo-backend.service

[Service]
Type=simple
ExecStart=/usr/bin/bluealsa -S -p a2dp-sink
User=milo
Group=audio
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
```


## Snapcast

**milo-snapserver-multiroom.service**

```bash
sudo tee /etc/systemd/system/milo-snapserver-multiroom.service > /dev/null << 'EOF'
[Unit]
Description=Snapcast Server for Milo Multiroom
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/snapserver -c /etc/snapserver.conf
User=milo
Group=audio
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

**milo-snapclient-multiroom.service**

```bash
sudo tee /etc/systemd/system/milo-snapclient-multiroom.service > /dev/null << 'EOF'
[Unit]
Description=Snapcast Client for Milo Multiroom
After=network-online.target milo-snapserver-multiroom.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/snapclient -h 127.0.0.1 -p 1704 --logsink=system --soundcard default:CARD=sndrpihifiberry --mixer hardware:'Digital'
User=milo
Group=audio
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

**Démarrage automatique**

```bash
sudo systemctl daemon-reload
sudo systemctl enable milo-backend.service
sudo systemctl enable milo-frontend.service
sudo systemctl enable milo-kiosk.service
sudo systemctl enable milo-snapclient-multiroom.service
sudo systemctl enable milo-snapserver-multiroom.service
sudo systemctl enable milo-bluealsa-aplay.service
sudo systemctl enable milo-bluealsa.service
sudo systemctl start milo-backend.service
sudo systemctl start milo-frontend.service
sudo systemctl start milo-kiosk.service
sudo systemctl start milo-snapclient-multiroom.service
sudo systemctl start milo-snapserver-multiroom.service
sudo systemctl start milo-bluealsa-aplay.service
sudo systemctl start milo-bluealsa.service
```


**INTÉGRÉ DANS Milo : Commande pour faire passer toutes les sources audio "snapserver" sur "Multiroom".**

```bash
curl -s http://localhost:1780/jsonrpc -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' | grep -o '"id":"[a-f0-9-]*","muted"' | cut -d'"' -f4 | while read group_id; do curl -s http://localhost:1780/jsonrpc -d "{\"id\":1,\"jsonrpc\":\"2.0\",\"method\":\"Group.SetStream\",\"params\":{\"id\":\"$group_id\",\"stream_id\":\"Multiroom\"}}"; echo "→ $group_id switched"; done
```



# 07 · Configuration ALSA + Multiroom

#### ALSA Loopback Configuration:

**Create ALSA Loopback**
```bash
# Create snd-aloop.conf via modules-load.d
echo "snd_aloop" | sudo tee /etc/modules-load.d/snd-aloop.conf

# Create module configuration
sudo tee /etc/modprobe.d/snd-aloop.conf << 'EOF'

# ALSA loopback module configuration
options snd-aloop index=1 enable=1
EOF

# Reboot (important)
sudo reboot
```

```bash
# Verify loopback is loaded
lsmod | grep snd_aloop
```

**Configure ALSA devices : /etc/asound.conf** 
```bash
sudo tee /etc/asound.conf > /dev/null << 'EOF'
# ALSA Configuration for Milo
# CamillaDSP is ALWAYS in the audio path for volume control
# MILO_MODE environment variable controls routing: direct or multiroom
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

# === DYNAMIC DEVICES FOR MILO ===
# CamillaDSP is ALWAYS in the audio path (for volume control)
# DSP effects (EQ, compressor, loudness) are toggled within CamillaDSP via bypass/restore

# Spotify - Configurable device (direct or multiroom)
pcm.milo_spotify {
    @func concat
    strings [
        "pcm.milo_spotify_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
    ]
}

# Bluetooth - Configurable device (direct or multiroom)
pcm.milo_bluetooth {
    @func concat
    strings [
        "pcm.milo_bluetooth_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
    ]
}

# ROC - Configurable device (direct or multiroom)
pcm.milo_roc {
    @func concat
    strings [
        "pcm.milo_roc_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
    ]
}

# === MODE IMPLEMENTATIONS ===

# MULTIROOM mode (via snapserver loopback)
# Each source has its own loopback subdevice
pcm.milo_spotify_multiroom {
    type plug
    slave.pcm {
        type hw
        card Loopback
        device 0
        subdevice 2
    }
}

pcm.milo_bluetooth_multiroom {
    type plug
    slave.pcm {
        type hw
        card Loopback
        device 0
        subdevice 0
    }
}

pcm.milo_roc_multiroom {
    type plug
    slave.pcm {
        type hw
        card Loopback
        device 0
        subdevice 1
    }
}

# DIRECT mode (via CamillaDSP to amplifier)
# CamillaDSP handles volume control and DSP effects
pcm.milo_spotify_direct {
    type plug
    slave.pcm "camilladsp"
}

pcm.milo_bluetooth_direct {
    type plug
    slave.pcm "camilladsp"
}

pcm.milo_roc_direct {
    type plug
    slave.pcm "camilladsp"
}

# === CAMILLADSP LOOPBACK ===
# CamillaDSP reads from this loopback and outputs to HiFiBerry
pcm.camilladsp {
    type plug
    slave.pcm {
        type hw
        card Loopback
        device 0
        subdevice 4
    }
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



# 08 · Configuration et automatisation du ventilateur 


**Ajouter dans /boot/firmware/config.txt :**

```bash
# Fan PWM
dtparam=cooling_fan=on

# 20% ...
dtparam=fan_temp0=55000
dtparam=fan_temp0_hyst=2500
dtparam=fan_temp0_speed=50

# 40% ...
dtparam=fan_temp1=60000
dtparam=fan_temp1_hyst=2500
dtparam=fan_temp1_speed=100

# 60% ...
dtparam=fan_temp2=65000
dtparam=fan_temp2_hyst=2500
dtparam=fan_temp2_speed=150

# 80% ...
dtparam=fan_temp3=70000
dtparam=fan_temp3_hyst=2500
dtparam=fan_temp3_speed=200

# 100% ...
dtparam=fan_temp4=75000
dtparam=fan_temp4_hyst=2500
dtparam=fan_temp4_speed=255

```



# 08 · Configuration et automatisation du ventilateur 


**Ajouter dans /boot/firmware/config.txt :**

```bash
# Fan PWM
dtparam=cooling_fan=on

# 20% ...
dtparam=fan_temp0=55000
dtparam=fan_temp0_hyst=2500
dtparam=fan_temp0_speed=50

# 40% ...
dtparam=fan_temp1=60000
dtparam=fan_temp1_hyst=2500
dtparam=fan_temp1_speed=100

# 60% ...
dtparam=fan_temp2=65000
dtparam=fan_temp2_hyst=2500
dtparam=fan_temp2_speed=150

# 80% ...
dtparam=fan_temp3=70000
dtparam=fan_temp3_hyst=2500
dtparam=fan_temp3_speed=200

# 100% ...
dtparam=fan_temp4=75000
dtparam=fan_temp4_hyst=2500
dtparam=fan_temp4_speed=255

```


# 09 · Configuration Avahi + Nginx

Ce guide permet d'accéder à votre application Milo via `http://milo.local` au lieu de `http://192.168.1.152:3000`.-

## 1. Configuration Avahi (mDNS)

### Installation d'Avahi

```bash
sudo apt install avahi-daemon avahi-utils
```


### Configuration Avahi

Configuration complète pour la découverte réseau `/etc/avahi/avahi-daemon.conf`  :

```bash
sudo  tee /etc/avahi/avahi-daemon.conf > /dev/null <<  'EOF'
[server]
host-name=milo
domain-name=local
use-ipv4=yes
use-ipv6=no
allow-interfaces=wlan0
deny-interfaces=eth0,docker0,lo
ratelimit-interval-usec=1000000
ratelimit-burst=1000

[wide-area]
enable-wide-area=no

[publish]
publish-hinfo=no
publish-workstation=yes
publish-domain=yes
publish-addresses=yes
publish-aaaa-on-ipv4=no
publish-a-on-ipv6=no
EOF
```

### Service Avahi pour Milo (optionnel - à vérifier sur milo v0.3)

Créer un service Avahi pour annoncer Milo sur le réseau :

```bash
sudo nano /etc/avahi/services/milo.service
```

Contenu :

```xml
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Milo Audio System on %h</name>
  <service>
    <type>_http._tcp</type>
    <port>80</port>
    <txt-record>path=/</txt-record>
    <txt-record>description=Milo Audio Control Interface</txt-record>
  </service>
</service-group>

```

### Redémarrer Avahi

```bash
sudo systemctl enable avahi-daemon
sudo systemctl restart avahi-daemon

```

## 2. Configuration Nginx

### Installation

```bash
sudo apt install nginx

```

### Configuration du site Milo

Créer `/etc/nginx/sites-available/milo` :

```bash
sudo nano /etc/nginx/sites-available/milo
```

Contenu du fichier :
```nginx
server {
    listen 80;
    server_name milo.local;
    
    # Frontend (port 3000)
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # API Backend (port 8000)
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    
    # WebSocket (port 8000)
    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $hostserver {
    listen 80;
    server_name milo.local;
    
    # Frontend (port 3000)
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # API Backend (port 8000)
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    
    # WebSocket
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        proxy_buffering off;
    }
};
        proxy_buffering off;
    }
}

```

### Activation du site

```bash
# Activer le site milo
sudo ln -s /etc/nginx/sites-available/milo /etc/nginx/sites-enabled/

# Supprimer le site par défaut
sudo rm -f /etc/nginx/sites-enabled/default

# Tester la configuration
sudo nginx -t

# Démarrer et activer nginx
sudo systemctl enable nginx
sudo systemctl start nginx
```

### Redémarrer le service frontend

```bash
sudo systemctl restart milo-frontend
```


## Vérification

1.  **Test résolution DNS** :
    
    ```bash
    ping milo.local
    
    ```
    
2.  **Test découverte réseau Avahi** :
    
    ```bash
    # Découvrir les services sur le réseau
    avahi-browse -at
    
    # Rechercher spécifiquement Milo
    avahi-browse -r _http._tcp
    
    ```
    
3.  **Test accès web** :
    
    -   Depuis le Raspberry Pi : `http://milo.local`
    -   Depuis un autre appareil du réseau : `http://milo.local`

4.  **Vérification des services** :
    
    ```bash
    sudo systemctl status avahi-daemon
    sudo systemctl status nginx
    sudo systemctl status milo-frontend
    
    ```
