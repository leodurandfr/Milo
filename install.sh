#!/bin/bash
# Milo Audio System - Installation Script v1.0
# Usage: 
#   wget https://raw.githubusercontent.com/Leshauts/Milo/main/install.sh
#   chmod +x install.sh
#   ./install.sh
# Uninstall: ./install.sh --uninstall

set -e

# =====================================
# Configuration et variables globales
# =====================================

MILO_USER="milo"
MILO_HOME="/home/$MILO_USER"
MILO_APP_DIR="$MILO_HOME/milo"
MILO_DATA_DIR="/var/lib/milo"
MILO_REPO="https://github.com/Leshauts/Milo.git"
REQUIRED_HOSTNAME="milo"
REBOOT_REQUIRED=false

# Couleurs pour l'affichage
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# =====================================
# Fonctions utilitaires
# =====================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_banner() {
    echo -e "${BLUE}"
    echo "  __  __ _ _       "
    echo " |  \/  (_) | ___  "
    echo " | |\/| | | |/ _ \ "
    echo " | |  | | | | (_) |"
    echo " |_|  |_|_|_|\___/ "
    echo ""
    echo "Audio System Installation Script v1.0"
    echo -e "${NC}"
}

check_root() {
    if [[ $EUID -eq 0 ]]; then
        log_error "Ce script ne doit pas être exécuté en tant que root."
        log_info "Utilisez: curl -sSL https://raw.githubusercontent.com/Leshauts/Milo/main/install.sh | bash"
        exit 1
    fi
}

# =====================================
# Vérifications système
# =====================================

check_system() {
    log_info "Vérification du système..."
    
    # Vérifier que c'est un Raspberry Pi
    if ! grep -q "Raspberry Pi" /proc/cpuinfo; then
        log_error "Ce script est conçu pour Raspberry Pi uniquement."
        exit 1
    fi
    
    # Vérifier la version du kernel pour HiFiBerry
    KERNEL_VERSION=$(uname -r | cut -d'.' -f1-2)
    log_info "Version kernel détectée: $KERNEL_VERSION"
    
    # Vérifier l'architecture
    ARCH=$(uname -m)
    if [[ "$ARCH" != "aarch64" ]]; then
        log_error "Architecture non supportée: $ARCH. Raspberry Pi OS 64bit requis."
        exit 1
    fi
    
    log_success "Système compatible détecté"
}

# =====================================
# Gestion du hostname
# =====================================

setup_hostname() {
    local current_hostname=$(hostname)
    
    log_info "Vérification du hostname..."
    log_info "Hostname actuel: $current_hostname"
    echo ""
    
    if [ "$current_hostname" != "$REQUIRED_HOSTNAME" ]; then
        echo -e "${YELLOW}⚠️  IMPORTANT:${NC} Milo nécessite le hostname '${REQUIRED_HOSTNAME}' pour:"
        echo "   • Accès via ${REQUIRED_HOSTNAME}.local depuis le navigateur"
        echo "   • Fonctionnement optimal du multiroom (Snapserver)"
        echo "   • Découverte réseau des autres instances Milo"
        echo ""
        echo -e "${BLUE}🔄 Changement de hostname requis:${NC} '$current_hostname' → '$REQUIRED_HOSTNAME'"
        echo ""
        
        read -p "Changer le hostname vers '$REQUIRED_HOSTNAME' ? (O/n): " change_hostname
        case $change_hostname in
            [Nn]* )
                log_error "Installation annulée. Le hostname '$REQUIRED_HOSTNAME' est requis."
                exit 1
                ;;
            * )
                log_info "Configuration du hostname '$REQUIRED_HOSTNAME'..."
                configure_hostname "$REQUIRED_HOSTNAME"
                log_success "Hostname configuré"
                log_warning "Un redémarrage sera nécessaire après l'installation."
                REBOOT_REQUIRED=true
                ;;
        esac
    else
        log_success "Hostname '$REQUIRED_HOSTNAME' déjà configuré"
    fi
}

configure_hostname() {
    local new_hostname="$1"
    
    # Mettre à jour /etc/hostname
    echo "$new_hostname" | sudo tee /etc/hostname > /dev/null
    
    # Mettre à jour /etc/hosts
    sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
    
    # Appliquer immédiatement (sans redémarrage)
    sudo hostnamectl set-hostname "$new_hostname"
}

# =====================================
# Sélection carte audio HiFiBerry
# =====================================

choose_hifiberry() {
    log_info "Configuration de la carte audio HiFiBerry..."
    echo ""
    echo "Sélectionnez votre carte audio HiFiBerry:"
    echo ""
    echo "1) Amp2"
    echo "2) Amp4"
    echo "3) Amp4 Pro"
    echo "4) Amp100"
    echo "5) Beocreate 4CA"
    echo "6) Ignorer (pas de HiFiBerry)"
    echo ""
    
    while true; do
        read -p "Votre choix [1]: " hifiberry_choice
        hifiberry_choice=${hifiberry_choice:-1}
        
        case $hifiberry_choice in
            1) HIFIBERRY_OVERLAY="hifiberry-dacplus-std"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp2"; break;;
            2) HIFIBERRY_OVERLAY="hifiberry-dacplus-std"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp4"; break;;
            3) HIFIBERRY_OVERLAY="hifiberry-amp4pro"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp4 Pro"; break;;
            4) HIFIBERRY_OVERLAY="hifiberry-amp100"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp100"; break;;
            5) HIFIBERRY_OVERLAY="hifiberry-dac"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Beocreate 4CA"; break;;
            6) HIFIBERRY_OVERLAY=""; CARD_NAME=""; log_warning "Configuration HiFiBerry ignorée"; break;;
            *) echo "Choix invalide. Veuillez entrer un nombre entre 1 et 6.";;
        esac
    done
}

configure_audio_hardware() {
    if [[ -z "$HIFIBERRY_OVERLAY" ]]; then
        return
    fi
    
    log_info "Configuration du hardware audio..."
    
    local config_file="/boot/firmware/config.txt"
    
    # Vérifier si le fichier existe
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
        if [[ ! -f "$config_file" ]]; then
            log_error "Fichier config.txt non trouvé"
            exit 1
        fi
    fi
    
    # Sauvegarder le fichier original
    sudo cp "$config_file" "$config_file.backup.$(date +%Y%m%d_%H%M%S)"
    
    # Supprimer l'audio onboard
    sudo sed -i '/^dtparam=audio=on/d' "$config_file"
    
    # Configurer vc4 overlay
    if grep -q "vc4-fkms-v3d" "$config_file"; then
        sudo sed -i 's/dtoverlay=vc4-fkms-v3d.*/dtoverlay=vc4-fkms-v3d,audio=off/' "$config_file"
    elif grep -q "vc4-kms-v3d" "$config_file"; then
        sudo sed -i 's/dtoverlay=vc4-kms-v3d.*/dtoverlay=vc4-kms-v3d,noaudio/' "$config_file"
    fi
    
    # Ajouter la configuration HiFiBerry
    if ! grep -q "$HIFIBERRY_OVERLAY" "$config_file"; then
        echo "" | sudo tee -a "$config_file"
        echo "# Milo - HiFiBerry Audio" | sudo tee -a "$config_file"
        echo "dtoverlay=$HIFIBERRY_OVERLAY" | sudo tee -a "$config_file"
    fi
    
    # Configuration du ventilateur si pas déjà présente
    if ! grep -q "cooling_fan=on" "$config_file"; then
        echo "" | sudo tee -a "$config_file"
        echo "# Milo - Fan PWM Control" | sudo tee -a "$config_file"
        echo "dtparam=cooling_fan=on" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0=55000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0_speed=50" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1=60000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1_speed=100" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2=65000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2_speed=150" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3=70000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3_speed=200" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp4=75000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp4_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp4_speed=255" | sudo tee -a "$config_file"
    fi
    
    log_success "Configuration audio hardware terminée"
    REBOOT_REQUIRED=true
}

# =====================================
# Installation des dépendances
# =====================================

install_dependencies() {
    log_info "Mise à jour du système..."
    sudo apt update
    sudo apt upgrade -y
    
    log_info "Installation des dépendances de base..."
    sudo apt install -y \
        git python3-pip python3-venv python3-dev libasound2-dev libssl-dev \
        cmake build-essential pkg-config nodejs npm curl wget \
        libogg-dev libvorbis-dev libasound2-dev \
        g++ pkg-config scons ragel gengetopt libuv1-dev \
        libspeexdsp-dev libunwind-dev libsox-dev libsndfile1-dev \
        libtool intltool autoconf automake make avahi-utils \
        libbluetooth-dev libdbus-1-dev libglib2.0-dev libsbc-dev \
        bluez bluez-tools autotools-dev \
        libasound2-plugin-equal \
        avahi-daemon avahi-utils nginx
    
    log_info "Mise à jour de Node.js et npm..."
    sudo npm install -g n
    sudo n stable
    sudo npm install -g npm@latest
    hash -r
    
    # Supprimer PulseAudio/PipeWire
    log_info "Suppression de PulseAudio/PipeWire..."
    sudo apt remove -y pulseaudio pipewire || true
    sudo apt autoremove -y
    
    log_success "Dépendances installées"
}

# =====================================
# Création de l'utilisateur Milo
# =====================================

create_milo_user() {
    if id "$MILO_USER" &>/dev/null; then
        log_info "Utilisateur '$MILO_USER' existe déjà"
    else
        log_info "Création de l'utilisateur '$MILO_USER'..."
        sudo useradd -m -s /bin/bash -G audio,sudo "$MILO_USER"
        log_success "Utilisateur '$MILO_USER' créé"
    fi
    
    # Créer les répertoires nécessaires
    sudo mkdir -p "$MILO_DATA_DIR"
    sudo chown -R "$MILO_USER:audio" "$MILO_DATA_DIR"
}

# =====================================
# Installation de go-librespot
# =====================================

install_go_librespot() {
    log_info "Installation de go-librespot..."
    
    local temp_dir=$(mktemp -d)
    cd "$temp_dir"
    
    wget https://github.com/devgianlu/go-librespot/releases/download/v0.2.0/go-librespot_linux_arm64.tar.gz
    tar -xvzf go-librespot_linux_arm64.tar.gz
    sudo cp go-librespot /usr/local/bin/
    sudo chmod +x /usr/local/bin/go-librespot
    
    # Configuration
    sudo mkdir -p "$MILO_DATA_DIR/go-librespot"
    sudo tee "$MILO_DATA_DIR/go-librespot/config.yml" > /dev/null << 'EOF'
device_name: "Milo"
device_type: "speaker"
bitrate: 320

audio_backend: "alsa"
audio_device: "milo_spotify"

external_volume: true

server:
  enabled: true
  address: "0.0.0.0"
  port: 3678
  allow_origin: "*"
EOF
    
    sudo chown -R "$MILO_USER:audio" "$MILO_DATA_DIR/go-librespot"
    
    cd ~
    rm -rf "$temp_dir"
    
    log_success "go-librespot installé"
}

# =====================================
# Installation de roc-toolkit
# =====================================

install_roc_toolkit() {
    log_info "Installation de roc-toolkit..."
    
    # Créer un répertoire temporaire
    local temp_dir=$(mktemp -d)
    cd "$temp_dir"
    
    git clone https://github.com/roc-streaming/roc-toolkit.git
    cd roc-toolkit
    scons -Q --build-3rdparty=openfec
    sudo scons -Q --build-3rdparty=openfec install
    sudo ldconfig
    
    cd ~
    rm -rf "$temp_dir"
    
    log_success "roc-toolkit installé"
}

# =====================================
# Installation de bluez-alsa
# =====================================

install_bluez_alsa() {
    log_info "Installation de bluez-alsa..."
    
    # Créer un répertoire temporaire  
    local temp_dir=$(mktemp -d)
    cd "$temp_dir"
    
    git clone https://github.com/arkq/bluez-alsa.git
    cd bluez-alsa
    git checkout v4.3.1
    
    autoreconf --install
    mkdir build && cd build
    
    ../configure --prefix=/usr --enable-systemd \
        --with-alsaplugindir=/usr/lib/aarch64-linux-gnu/alsa-lib \
        --with-bluealsauser="$MILO_USER" --with-bluealsaaplayuser="$MILO_USER" \
        --enable-cli
    
    make -j$(nproc)
    sudo make install
    sudo ldconfig
    
    # Arrêter les services par défaut
    sudo systemctl stop bluealsa-aplay.service bluealsa.service || true
    sudo systemctl disable bluealsa-aplay.service bluealsa.service || true
    
    cd ~
    rm -rf "$temp_dir"
    
    log_success "bluez-alsa installé"
}

# =====================================
# Installation de Snapcast
# =====================================

install_snapcast() {
    log_info "Installation de Snapcast..."
    
    local temp_dir=$(mktemp -d)
    cd "$temp_dir"
    
    wget https://github.com/badaix/snapcast/releases/download/v0.31.0/snapserver_0.31.0-1_arm64_bookworm.deb
    wget https://github.com/badaix/snapcast/releases/download/v0.31.0/snapclient_0.31.0-1_arm64_bookworm.deb
    
    sudo apt install -y ./snapserver_0.31.0-1_arm64_bookworm.deb
    sudo apt install -y ./snapclient_0.31.0-1_arm64_bookworm.deb
    
    # Arrêter les services par défaut
    sudo systemctl stop snapserver.service snapclient.service || true
    sudo systemctl disable snapserver.service snapclient.service || true
    
    cd ~
    rm -rf "$temp_dir"
    
    log_success "Snapcast installé"
}

# =====================================
# Configuration ALSA
# =====================================

configure_alsa() {
    log_info "Configuration ALSA..."
    
    # Configuration du module loopback
    echo "snd_aloop" | sudo tee /etc/modules-load.d/snd-aloop.conf
    sudo tee /etc/modprobe.d/snd-aloop.conf > /dev/null << 'EOF'
# Configuration du module loopback ALSA pour Milo
options snd-aloop index=1 enable=1
EOF
    
    # Configuration ALSA principale
    sudo tee /etc/asound.conf > /dev/null << EOF
# Configuration ALSA Milo
pcm.!default {
    type plug
    slave.pcm {
        type hw
        card $CARD_NAME
        device 0
    }
}

ctl.!default {
    type hw
    card $CARD_NAME
}

# === DEVICES DYNAMIQUES POUR MILO (avec support equalizer) ===

# Spotify - Device configurable avec equalizer
pcm.milo_spotify {
    @func concat
    strings [
        "pcm.milo_spotify_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
        { @func getenv vars [ MILO_EQUALIZER ] default "" }
    ]
}

# Bluetooth - Device configurable avec equalizer
pcm.milo_bluetooth {
    @func concat
    strings [
        "pcm.milo_bluetooth_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
        { @func getenv vars [ MILO_EQUALIZER ] default "" }
    ]
}

# ROC - Device configurable avec equalizer
pcm.milo_roc {
    @func concat
    strings [
        "pcm.milo_roc_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
        { @func getenv vars [ MILO_EQUALIZER ] default "" }
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

# Mode MULTIROOM - AVEC equalizer
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
        card $CARD_NAME
        device 0
    }
}

pcm.milo_bluetooth_direct {
    type plug
    slave.pcm {
        type hw
        card $CARD_NAME
        device 0
    }
}

pcm.milo_roc_direct {
    type plug
    slave.pcm {
        type hw
        card $CARD_NAME
        device 0
    }
}

# Mode DIRECT - AVEC equalizer
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

# === EQUALIZERS ===

# Equalizer pour mode direct
pcm.equal {
    type equal
    slave.pcm "plughw:$CARD_NAME"
}

# Equalizer pour mode multiroom
pcm.equal_multiroom {
    type equal
    slave.pcm "plughw:1,0"
}

ctl.equal {
    type equal
}
EOF
    
    log_success "Configuration ALSA terminée"
    REBOOT_REQUIRED=true
}

# =====================================
# Installation de l'application Milo
# =====================================

install_milo_application() {
    log_info "Installation de l'application Milo..."
    
    # Cloner le dépôt
    if [[ -d "$MILO_APP_DIR" ]]; then
        log_warning "Le répertoire $MILO_APP_DIR existe déjà, suppression..."
        sudo rm -rf "$MILO_APP_DIR"
    fi
    
    sudo -u "$MILO_USER" git clone "$MILO_REPO" "$MILO_APP_DIR"
    cd "$MILO_APP_DIR"
    
    # Configuration Python Backend
    log_info "Configuration du backend Python..."
    sudo -u "$MILO_USER" python3 -m venv venv
    sudo -u "$MILO_USER" bash -c "source venv/bin/activate && pip install --upgrade pip"
    sudo -u "$MILO_USER" bash -c "source venv/bin/activate && pip install -r requirements.txt"
    
    # Configuration Frontend
    log_info "Configuration du frontend..."
    cd frontend
    sudo -u "$MILO_USER" npm install
    sudo -u "$MILO_USER" npm run build
    
    cd "$MILO_APP_DIR"
    
    log_success "Application Milo installée"
}

# =====================================
# Configuration des services systemd
# =====================================

create_systemd_services() {
    log_info "Création des services systemd..."
    
    # Backend
    sudo tee /etc/systemd/system/milo-backend.service > /dev/null << EOF
[Unit]
Description=Milo Backend Service
After=network.target

[Service]
Type=simple
User=$MILO_USER
Group=$MILO_USER
WorkingDirectory=$MILO_APP_DIR
ExecStart=$MILO_APP_DIR/venv/bin/python3 backend/main.py

Restart=always
RestartSec=5
TimeoutStopSec=10

StateDirectory=milo
StateDirectoryMode=0755

StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    
    # Frontend
    sudo tee /etc/systemd/system/milo-frontend.service > /dev/null << EOF
[Unit]
Description=Milo Frontend Service
After=network.target

[Service]
Type=simple
User=$MILO_USER
Group=$MILO_USER
WorkingDirectory=$MILO_APP_DIR/frontend

ExecStartPre=/usr/bin/npm run build
ExecStart=/usr/bin/npm run preview -- --host 0.0.0.0 --port 3000

Restart=always
RestartSec=5
TimeoutStopSec=10

StateDirectory=milo
StateDirectoryMode=0755

Environment=NODE_ENV=production
Environment=HOME=$MILO_HOME

StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-frontend

[Install]
WantedBy=multi-user.target
EOF
    
    # Kiosk
    sudo tee /etc/systemd/system/milo-kiosk.service > /dev/null << EOF
[Unit]
Description=Milo Kiosk Mode (Chromium Fullscreen)
After=graphical.target milo-frontend.service
Wants=graphical.target
Requires=milo-frontend.service

[Service]
Type=simple
User=$MILO_USER
Group=$MILO_USER
Environment=DISPLAY=:0
Environment=HOME=$MILO_HOME
Environment=XDG_RUNTIME_DIR=/run/user/1000

ExecStartPre=/bin/sleep 8

ExecStart=/usr/bin/chromium-browser \\
  --kiosk \\
  --incognito \\
  --no-first-run \\
  --disable-infobars \\
  --disable-notifications \\
  --disable-popup-blocking \\
  --disable-session-crashed-bubble \\
  --disable-restore-session-state \\
  --disable-background-timer-throttling \\
  --disable-backgrounding-occluded-windows \\
  --disable-renderer-backgrounding \\
  --disable-translate \\
  --disable-sync \\
  --hide-scrollbars \\
  --disable-background-networking \\
  --autoplay-policy=no-user-gesture-required \\
  --start-fullscreen \\
  --no-sandbox \\
  --disable-dev-shm-usage \\
  --hide-cursor \\
  --touch-events=enabled \\
  --enable-features=TouchpadAndWheelScrollLatching \\
  --force-device-scale-factor=1 \\
  --disable-pinch \\
  --disable-features=VizDisplayCompositor \\
  --app=http://milo.local

Restart=always
RestartSec=5
TimeoutStopSec=5

[Install]
WantedBy=graphical.target
EOF
    
    # ROC
    sudo tee /etc/systemd/system/milo-roc.service > /dev/null << EOF
[Unit]
Description=Milo ROC Audio Receiver
Documentation=https://roc-streaming.org/
After=network.target sound.service milo-backend.service
Wants=network.target
BindsTo=milo-backend.service

[Service]
Type=exec
User=$MILO_USER
Group=audio

EnvironmentFile=/etc/environment
Environment=HOME=$MILO_HOME

ExecStart=/usr/bin/roc-recv -vv \\
  -s rtp+rs8m://0.0.0.0:10001 \\
  -r rs8m://0.0.0.0:10002 \\
  -c rtcp://0.0.0.0:10003 \\
  -o alsa://milo_roc

Restart=always
RestartSec=5

PrivateNetwork=false
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-roc

[Install]
WantedBy=multi-user.target
EOF
    
    # go-librespot
    sudo tee /etc/systemd/system/milo-go-librespot.service > /dev/null << EOF
[Unit]
Description=Milo Spotify Connect via go-librespot
After=network-online.target sound.service milo-backend.service
Wants=network-online.target
BindsTo=milo-backend.service

[Service]
Type=simple
User=$MILO_USER
Group=audio

ExecStart=/usr/local/bin/go-librespot --config_dir $MILO_DATA_DIR/go-librespot
Environment=HOME=$MILO_HOME
WorkingDirectory=$MILO_DATA_DIR
Restart=always
RestartSec=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-go-librespot

[Install]
WantedBy=multi-user.target
EOF
    
    # Bluealsa
    sudo tee /etc/systemd/system/milo-bluealsa.service > /dev/null << EOF
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
User=$MILO_USER
Group=audio
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
    
    # Bluealsa-aplay
    sudo tee /etc/systemd/system/milo-bluealsa-aplay.service > /dev/null << EOF
[Unit]
Description=BlueALSA player for Milo
Requires=milo-bluealsa.service
After=milo-bluealsa.service milo-backend.service
BindsTo=milo-backend.service milo-bluealsa.service

[Service]
Type=simple
User=$MILO_USER

ExecStart=/usr/bin/bluealsa-aplay --pcm=milo_bluetooth --profile-a2dp 00:00:00:00:00:00

RestartSec=2
Restart=always
PrivateTmp=false
PrivateDevices=false

[Install]
WantedBy=multi-user.target
EOF
    
    # Snapserver multiroom
    sudo tee /etc/systemd/system/milo-snapserver-multiroom.service > /dev/null << EOF
[Unit]
Description=Snapcast Server for Milo Multiroom
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/snapserver -c /etc/snapserver.conf
User=$MILO_USER
Group=audio
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    
    # Snapclient multiroom
    sudo tee /etc/systemd/system/milo-snapclient-multiroom.service > /dev/null << EOF
[Unit]
Description=Snapcast Client for Milo Multiroom
After=network-online.target milo-snapserver-multiroom.service
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/snapclient -h 127.0.0.1 -p 1704 --logsink=system --soundcard default:CARD=$CARD_NAME --mixer hardware:'Digital'
User=$MILO_USER
Group=audio
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    
    log_success "Services systemd créés"
}

# =====================================
# Configuration Snapserver
# =====================================

configure_snapserver() {
    log_info "Configuration de Snapserver..."
    
    sudo tee /etc/snapserver.conf > /dev/null << 'EOF'
# Configuration Snapserver pour Milo
[stream]
default = Multiroom

# Paramètres globaux
buffer = 1000
codec = pcm
chunk_ms = 20
sampleformat = 48000:16:2

# Meta source : Bluetooth + ROC + Spotify
source = meta:///Bluetooth/ROC/Spotify?name=Multiroom

# Sources individuelles
source = alsa:///?name=Bluetooth&device=hw:1,1,0
source = alsa:///?name=ROC&device=hw:1,1,1
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
    
    log_success "Snapserver configuré"
}

# =====================================
# Configuration Nginx et Avahi
# =====================================

configure_nginx_avahi() {
    log_info "Configuration de Nginx et Avahi..."
    
    # Configuration Avahi
    sudo tee /etc/avahi/avahi-daemon.conf > /dev/null << 'EOF'
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
    
    # Service Avahi pour Milo
    sudo tee /etc/avahi/services/milo.service > /dev/null << 'EOF'
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
EOF
    
    # Configuration Nginx
    sudo tee /etc/nginx/sites-available/milo > /dev/null << 'EOF'
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
}
EOF
    
    # Activer le site
    sudo ln -sf /etc/nginx/sites-available/milo /etc/nginx/sites-enabled/
    sudo rm -f /etc/nginx/sites-enabled/default
    
    # Tester la configuration
    sudo nginx -t
    
    log_success "Nginx et Avahi configurés"
}

# =====================================
# Activation des services
# =====================================

enable_services() {
    log_info "Activation des services..."
    
    sudo systemctl daemon-reload
    
    # Services système
    sudo systemctl enable avahi-daemon
    sudo systemctl enable nginx
    
    # Services Milo
    sudo systemctl enable milo-backend.service
    sudo systemctl enable milo-frontend.service
    sudo systemctl enable milo-kiosk.service
    sudo systemctl enable milo-roc.service
    sudo systemctl enable milo-go-librespot.service
    sudo systemctl enable milo-bluealsa.service
    sudo systemctl enable milo-bluealsa-aplay.service
    sudo systemctl enable milo-snapserver-multiroom.service
    sudo systemctl enable milo-snapclient-multiroom.service
    
    log_success "Services activés"
}

# =====================================
# Finalisation
# =====================================

finalize_installation() {
    log_info "Finalisation de l'installation..."
    
    # Affichage du résumé
    echo ""
    echo -e "${GREEN}=================================${NC}"
    echo -e "${GREEN}   Installation Milo terminée !${NC}"
    echo -e "${GREEN}=================================${NC}"
    echo ""
    echo -e "${BLUE}Configuration :${NC}"
    echo "  • Hostname: milo"
    echo "  • Utilisateur: $MILO_USER"
    echo "  • Application: $MILO_APP_DIR"
    echo "  • Données: $MILO_DATA_DIR"
    if [[ -n "$HIFIBERRY_OVERLAY" ]]; then
        echo "  • Carte audio: $HIFIBERRY_OVERLAY"
    fi
    echo ""
    echo -e "${BLUE}Accès :${NC}"
    echo "  • Interface web: http://milo.local"
    echo "  • Spotify Connect: 'Milo'"
    echo "  • Snapserver: http://milo.local:1780"
    echo ""
    
    if [[ "$REBOOT_REQUIRED" == "true" ]]; then
        echo -e "${YELLOW}⚠️  REDÉMARRAGE REQUIS${NC}"
        echo ""
        read -p "Redémarrer maintenant ? (O/n): " restart_now
        case $restart_now in
            [Nn]* )
                echo -e "${YELLOW}Pensez à redémarrer manuellement avec: sudo reboot${NC}"
                ;;
            * )
                log_info "Redémarrage en cours..."
                sudo reboot
                ;;
        esac
    else
        log_info "Démarrage des services..."
        sudo systemctl start avahi-daemon
        sudo systemctl start nginx
        sudo systemctl start milo-backend.service
        sudo systemctl start milo-frontend.service
        
        echo ""
        echo -e "${GREEN}✅ Milo est prêt ! Accédez à http://milo.local${NC}"
    fi
}

# =====================================
# Fonction de désinstallation
# =====================================

uninstall_milo() {
    log_warning "Début de la désinstallation de Milo..."
    echo ""
    read -p "Êtes-vous sûr de vouloir désinstaller Milo ? (o/N): " confirm
    case $confirm in
        [Oo]* )
            ;;
        * )
            log_info "Désinstallation annulée"
            exit 0
            ;;
    esac
    
    log_info "Arrêt des services..."
    sudo systemctl stop milo-*.service || true
    sudo systemctl disable milo-*.service || true
    
    log_info "Suppression des services systemd..."
    sudo rm -f /etc/systemd/system/milo-*.service
    sudo systemctl daemon-reload
    
    log_info "Suppression des configurations..."
    sudo rm -f /etc/nginx/sites-enabled/milo
    sudo rm -f /etc/nginx/sites-available/milo
    sudo rm -f /etc/snapserver.conf
    sudo rm -f /etc/asound.conf
    sudo rm -f /etc/modules-load.d/snd-aloop.conf
    sudo rm -f /etc/modprobe.d/snd-aloop.conf
    
    log_info "Suppression de l'application..."
    sudo rm -rf "$MILO_APP_DIR"
    sudo rm -rf "$MILO_DATA_DIR"
    
    log_info "Suppression des binaires..."
    sudo rm -f /usr/local/bin/go-librespot
    
    log_info "Nettoyage des packages..."
    sudo apt autoremove -y
    
    # Restauration hostname (optionnel)
    read -p "Restaurer l'hostname par défaut 'raspberrypi' ? (o/N): " restore_hostname
    case $restore_hostname in
        [Oo]* )
            configure_hostname "raspberrypi"
            log_info "Hostname restauré"
            ;;
    esac
    
    log_info "Redémarrage des services système..."
    sudo systemctl restart nginx avahi-daemon || true
    
    log_success "Désinstallation terminée !"
    echo ""
    log_warning "Note: L'utilisateur '$MILO_USER' n'a pas été supprimé"
    log_warning "Note: Les modifications de /boot/firmware/config.txt sont conservées"
    echo ""
    read -p "Redémarrer maintenant ? (o/N): " restart_now
    case $restart_now in
        [Oo]* )
            sudo reboot
            ;;
    esac
}

# =====================================
# Point d'entrée principal
# =====================================

main() {
    show_banner
    
    # Vérifier les arguments
    if [[ "$1" == "--uninstall" ]]; then
        uninstall_milo
        exit 0
    fi
    
    check_root
    
    log_info "Début de l'installation de Milo Audio System"
    echo ""
    
    # Étapes d'installation
    check_system
    setup_hostname
    choose_hifiberry
    configure_audio_hardware
    install_dependencies
    create_milo_user
    install_go_librespot
    install_roc_toolkit
    install_bluez_alsa
    install_snapcast
    configure_alsa
    install_milo_application
    create_systemd_services
    configure_snapserver
    configure_nginx_mdns
    enable_services
    finalize_installation
}

# Exécuter le script principal
main "$@"
