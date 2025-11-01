#!/bin/bash
# Milo Audio System - Installation Script v1.3
#
# IMPORTANT: Ce script est optimisé pour Raspberry Pi OS Lite (64-bit)
# Raspberry Pi OS Lite est recommandé pour minimiser l'utilisation des ressources
# et éviter les conflits avec des services desktop inutiles.
#
# Téléchargez Raspberry Pi OS Lite sur: https://www.raspberrypi.com/software/operating-systems/

set -e

MILO_USER="milo"
MILO_HOME="/home/$MILO_USER"
MILO_APP_DIR="$MILO_HOME/milo"
MILO_DATA_DIR="/var/lib/milo"
MILO_REPO="https://github.com/Leshauts/Milo.git"
REQUIRED_HOSTNAME="milo"
REBOOT_REQUIRED=false

# Variables pour stocker les choix utilisateur
USER_HOSTNAME_CHANGE=""
USER_HIFIBERRY_CHOICE=""
USER_SCREEN_CHOICE=""
USER_RESTART_CHOICE=""
HIFIBERRY_OVERLAY=""
CARD_NAME=""
SCREEN_TYPE=""

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

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
    echo "Audio System Installation Script v1.3"
    echo -e "${NC}"
}

check_root() {
    if [[ $EUID -eq 0 ]]; then
        log_error "Ce script ne doit pas être exécuté en tant que root."
        exit 1
    fi
}

check_system() {
    log_info "Vérification du système..."

    if ! grep -q "Raspberry Pi" /proc/cpuinfo; then
        log_error "Ce script est conçu pour Raspberry Pi uniquement."
        exit 1
    fi

    ARCH=$(uname -m)
    if [[ "$ARCH" != "aarch64" ]]; then
        log_error "Architecture non supportée: $ARCH. Raspberry Pi OS 64bit requis."
        exit 1
    fi

    # Avertissement si un environnement desktop est détecté
    if systemctl list-units --type=service | grep -qE "lightdm|gdm|sddm|xdm"; then
        log_warning "Un environnement desktop a été détecté."
        log_warning "Raspberry Pi OS Lite est recommandé pour des performances optimales."
        echo ""
    fi

    log_success "Système compatible détecté (Raspberry Pi OS 64-bit)"
}

collect_user_choices() {
    echo ""
    log_info "Configuration initiale - Répondez aux questions suivantes :"
    echo ""
    
    # 1. Vérification du hostname
    local current_hostname=$(hostname)
    log_info "Hostname actuel: $current_hostname"
    
    if [ "$current_hostname" != "$REQUIRED_HOSTNAME" ]; then
        echo ""
        echo -e "${YELLOW}⚠️  IMPORTANT:${NC} Milo nécessite le hostname '${REQUIRED_HOSTNAME}' pour:"
        echo "   • Accès via ${REQUIRED_HOSTNAME}.local depuis le navigateur"
        echo "   • Fonctionnement optimal du multiroom (Snapserver)"
        echo "   • Découverte réseau des autres instances Milo"
        echo ""
        echo -e "${BLUE}🔄 Changement de hostname requis:${NC} '$current_hostname' → '$REQUIRED_HOSTNAME'"
        echo ""
        
        while true; do
            read -p "Changer le hostname vers '$REQUIRED_HOSTNAME' ? (O/n): " USER_HOSTNAME_CHANGE
            case $USER_HOSTNAME_CHANGE in
                [Nn]* )
                    log_error "Installation annulée. Le hostname '$REQUIRED_HOSTNAME' est requis."
                    exit 1
                    ;;
                [Oo]* | "" )
                    USER_HOSTNAME_CHANGE="yes"
                    break
                    ;;
                * )
                    echo "Veuillez répondre par 'O' (oui) ou 'n' (non)."
                    ;;
            esac
        done
    else
        USER_HOSTNAME_CHANGE="already_set"
    fi
    
    # 2. Choix de la carte HiFiBerry
    echo ""
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
        read -p "Votre choix [1]: " USER_HIFIBERRY_CHOICE
        USER_HIFIBERRY_CHOICE=${USER_HIFIBERRY_CHOICE:-1}
        
        case $USER_HIFIBERRY_CHOICE in
            1) HIFIBERRY_OVERLAY="hifiberry-dacplus-std"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp2"; break;;
            2) HIFIBERRY_OVERLAY="hifiberry-dacplus-std"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp4"; break;;
            3) HIFIBERRY_OVERLAY="hifiberry-amp4pro"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp4 Pro"; break;;
            4) HIFIBERRY_OVERLAY="hifiberry-amp100"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Amp100"; break;;
            5) HIFIBERRY_OVERLAY="hifiberry-dac"; CARD_NAME="sndrpihifiberry"; log_success "Carte sélectionnée: Beocreate 4CA"; break;;
            6) HIFIBERRY_OVERLAY=""; CARD_NAME=""; log_warning "Configuration HiFiBerry ignorée"; break;;
            *) echo "Choix invalide. Veuillez entrer un nombre entre 1 et 6.";;
        esac
    done
    
    # 3. Choix de l'écran
    echo ""
    log_info "Configuration de l'écran tactile..."
    echo ""
    echo "Sélectionnez votre écran :"
    echo ""
    echo "1) Waveshare 7\" 1024x600 (USB)"
    echo "2) Waveshare 8\" 1280x800 (DSI)"  
    echo "3) Pas d'écran ou installation manuelle"
    echo ""
    
    while true; do
        read -p "Votre choix [1]: " USER_SCREEN_CHOICE
        USER_SCREEN_CHOICE=${USER_SCREEN_CHOICE:-1}
        
        case $USER_SCREEN_CHOICE in
            1) SCREEN_TYPE="waveshare_7_usb"; log_success "Écran sélectionné: Waveshare 7\" USB"; break;;
            2) SCREEN_TYPE="waveshare_8_dsi"; log_success "Écran sélectionné: Waveshare 8\" DSI"; break;;
            3) SCREEN_TYPE="none"; log_warning "Configuration d'écran ignorée"; break;;
            *) echo "Choix invalide. Veuillez entrer un nombre entre 1 et 3.";;
        esac
    done
    
    # 4. Choix du redémarrage
    echo ""
    log_info "Un redémarrage sera nécessaire à la fin de l'installation."
    while true; do
        read -p "Redémarrer automatiquement à la fin ? (O/n): " USER_RESTART_CHOICE
        case $USER_RESTART_CHOICE in
            [Nn]* )
                USER_RESTART_CHOICE="no"
                break
                ;;
            [Oo]* | "" )
                USER_RESTART_CHOICE="yes"
                break
                ;;
            * )
                echo "Veuillez répondre par 'O' (oui) ou 'n' (non)."
                ;;
        esac
    done
    
    echo ""
    log_success "Configuration terminée ! L'installation va maintenant se poursuivre automatiquement..."
    echo ""
    sleep 2
}

setup_hostname() {
    local current_hostname=$(hostname)
    
    log_info "Application de la configuration hostname..."
    
    if [ "$USER_HOSTNAME_CHANGE" == "yes" ]; then
        log_info "Configuration du hostname '$REQUIRED_HOSTNAME'..."
        configure_hostname "$REQUIRED_HOSTNAME"
        log_success "Hostname configuré"
        REBOOT_REQUIRED=true
    elif [ "$USER_HOSTNAME_CHANGE" == "already_set" ]; then
        log_success "Hostname '$REQUIRED_HOSTNAME' déjà configuré"
    fi
}

configure_hostname() {
    local new_hostname="$1"
    echo "$new_hostname" | sudo tee /etc/hostname > /dev/null
    sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
    sudo hostnamectl set-hostname "$new_hostname"
}

configure_audio_hardware() {
    if [[ -z "$HIFIBERRY_OVERLAY" ]]; then
        log_info "Configuration HiFiBerry ignorée"
        return
    fi
    
    log_info "Configuration du hardware audio pour HiFiBerry..."
    
    local config_file="/boot/firmware/config.txt"
    
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
        if [[ ! -f "$config_file" ]]; then
            log_error "Fichier config.txt non trouvé"
            exit 1
        fi
    fi
    
    sudo cp "$config_file" "$config_file.backup.$(date +%Y%m%d_%H%M%S)"
    
    sudo sed -i '/^dtparam=audio=on/d' "$config_file"
    
    if grep -q "vc4-fkms-v3d" "$config_file"; then
        sudo sed -i 's/dtoverlay=vc4-fkms-v3d.*/dtoverlay=vc4-fkms-v3d,audio=off/' "$config_file"
    fi
    
    if grep -q "vc4-kms-v3d" "$config_file"; then
        sudo sed -i 's/dtoverlay=vc4-kms-v3d.*/dtoverlay=vc4-kms-v3d,noaudio/' "$config_file"
    fi
    
    if ! grep -q "$HIFIBERRY_OVERLAY" "$config_file"; then
        echo "" | sudo tee -a "$config_file"
        echo "#AMP2" | sudo tee -a "$config_file"
        echo "dtoverlay=$HIFIBERRY_OVERLAY" | sudo tee -a "$config_file"
    fi
    
    if ! grep -q "usb_max_current_enable=1" "$config_file"; then
        echo "usb_max_current_enable=1" | sudo tee -a "$config_file"
    fi
    
    log_success "Configuration audio hardware terminée"
    REBOOT_REQUIRED=true
}

configure_screen_hardware() {
    if [[ "$SCREEN_TYPE" == "none" ]]; then
        log_info "Configuration d'écran ignorée"
        return
    fi
    
    log_info "Configuration du hardware d'écran..."
    
    local config_file="/boot/firmware/config.txt"
    
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
        if [[ ! -f "$config_file" ]]; then
            log_error "Fichier config.txt non trouvé"
            exit 1
        fi
    fi
    
    if [[ ! -f "$config_file.backup.$(date +%Y%m%d)" ]]; then
        sudo cp "$config_file" "$config_file.backup.$(date +%Y%m%d_%H%M%S)"
    fi
    
    case $SCREEN_TYPE in
        "waveshare_8_dsi")
            log_info "Configuration pour Waveshare 8\" DSI..."
            if ! grep -q "dtoverlay=vc4-kms-dsi-waveshare-panel,8_0_inch" "$config_file"; then
                echo "" | sudo tee -a "$config_file"
                echo "#DSI1 Use - Waveshare 8\" 1280x800" | sudo tee -a "$config_file"
                echo "dtoverlay=vc4-kms-dsi-waveshare-panel,8_0_inch" | sudo tee -a "$config_file"
            fi
            REBOOT_REQUIRED=true
            ;;
        "waveshare_7_usb")
            log_info "Configuration pour Waveshare 7\" USB (aucune modification config.txt requise)"
            ;;
    esac
    
    log_success "Configuration écran hardware terminée"
}

install_dependencies() {
    log_info "Mise à jour du système..."
    
    export DEBIAN_FRONTEND=noninteractive
    export DEBCONF_NONINTERACTIVE_SEEN=true
    
    echo 'Dpkg::Options {
       "--force-confdef";
       "--force-confnew";
    }' | sudo tee /etc/apt/apt.conf.d/local >/dev/null
    
    sudo apt update
    sudo apt upgrade -y
    
    log_info "Installation des dépendances de base..."
		# Configuration optimisée pour Raspberry Pi OS Lite
        sudo apt install -y \
            git python3-pip python3-venv python3-dev libasound2-dev libssl-dev \
            cmake build-essential pkg-config swig liblgpio-dev nodejs npm wget unzip \
            fontconfig mpv libinput-tools \
            fonts-noto fonts-noto-cjk fonts-lohit-deva fonts-noto-color-emoji
    
    log_info "Mise à jour de Node.js et npm..."
    sudo npm install -g n
    sudo n stable
    sudo npm install -g npm@latest
    hash -r
    
    sudo rm -f /etc/apt/apt.conf.d/local
    
    log_success "Dépendances installées"
}

create_milo_user() {
    if id "$MILO_USER" &>/dev/null; then
        log_info "Utilisateur '$MILO_USER' existe déjà"
    else
        log_info "Création de l'utilisateur '$MILO_USER'..."
        sudo useradd -m -s /bin/bash "$MILO_USER"
        sudo usermod -aG audio,video,bluetooth,input "$MILO_USER"
        log_success "Utilisateur '$MILO_USER' créé"
    fi
    
    sudo mkdir -p "$MILO_DATA_DIR"
    sudo chown -R "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR"
}

install_milo_application() {
    log_info "Clonage et configuration de Milo..."
    
    cd /tmp
    
    if [[ -d "$MILO_APP_DIR" ]]; then
        log_warning "Le répertoire $MILO_APP_DIR existe déjà, suppression..."
        sudo rm -rf "$MILO_APP_DIR"
    fi
    
    sudo -u "$MILO_USER" git clone "$MILO_REPO" "$MILO_APP_DIR"
    cd "$MILO_APP_DIR"
    
    log_info "Configuration de l'environnement Python..."
    sudo -u "$MILO_USER" python3 -m venv venv
    sudo -u "$MILO_USER" bash -c "source venv/bin/activate && pip install --upgrade pip"
    sudo -u "$MILO_USER" bash -c "source venv/bin/activate && pip install -r requirements.txt"
    
    log_info "Compilation du frontend..."
    cd frontend
    sudo -u "$MILO_USER" npm install
    sudo -u "$MILO_USER" npm run build
    cd ..
    
    log_success "Application Milo installée"
}

fix_nginx_permissions() {
    log_info "Configuration des permissions pour nginx..."
    
    sudo chmod 755 /home/milo
    sudo chmod 755 /home/milo/milo
    sudo chmod 755 /home/milo/milo/frontend
    sudo chmod -R 755 /home/milo/milo/frontend/dist
    
    sudo chown -R "$MILO_USER:$MILO_USER" /home/milo/milo/frontend/dist
    
    log_success "Permissions nginx configurées"
}

suppress_pulseaudio() {
    log_info "Suppression de PulseAudio/PipeWire..."
    sudo apt remove -y pulseaudio pipewire || true
    sudo apt autoremove -y
    log_success "PulseAudio/PipeWire supprimés"
}

install_go_librespot() {
    log_info "Installation de go-librespot..."
    
    sudo apt-get install -y libogg-dev libvorbis-dev libasound2-dev
    
    local temp_dir=$(mktemp -d)
    cd "$temp_dir"
    
    wget https://github.com/devgianlu/go-librespot/releases/download/v0.4.0/go-librespot_linux_arm64.tar.gz
    tar -xvzf go-librespot_linux_arm64.tar.gz
    sudo cp go-librespot /usr/local/bin/
    sudo chmod +x /usr/local/bin/go-librespot
    
    sudo mkdir -p "$MILO_DATA_DIR/go-librespot"
    sudo chown -R "$MILO_USER:audio" "$MILO_DATA_DIR/go-librespot"
    
    sudo tee "$MILO_DATA_DIR/go-librespot/config.yml" > /dev/null << 'EOF'
device_name: "Milō"
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

install_roc_toolkit() {
    log_info "Installation de roc-toolkit..."
    
    sudo apt install -y g++ pkg-config scons ragel gengetopt libuv1-dev \
      libspeexdsp-dev libunwind-dev libsox-dev libsndfile1-dev libssl-dev libasound2-dev \
      libtool intltool autoconf automake make cmake avahi-utils libpulse-dev
    
    local temp_dir=$(mktemp -d)
    cd "$temp_dir"
    
    git clone https://github.com/roc-streaming/roc-toolkit.git
    cd roc-toolkit
    scons -Q --build-3rdparty=openfec
    sudo scons -Q --build-3rdparty=openfec install
    sudo ldconfig
    
    cd ~
    rm -rf "$temp_dir"
    
    roc-recv --version
    
    log_success "roc-toolkit installé"
}

install_bluez_alsa() {
    log_info "Installation de bluez-alsa..."

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
    
    REBOOT_REQUIRED=true
    
    local temp_dir=$(mktemp -d)
    cd "$temp_dir"
    
    git clone https://github.com/arkq/bluez-alsa.git
    cd bluez-alsa
    git checkout v4.3.1
    
    autoreconf --install
    mkdir build && cd build
    
    # FIX: Utiliser --disable-systemd car nous gérons nos propres services systemd
    ../configure --prefix=/usr --disable-systemd \
      --with-alsaplugindir=/usr/lib/aarch64-linux-gnu/alsa-lib \
      --with-bluealsauser="$MILO_USER" --with-bluealsaaplayuser="$MILO_USER" \
      --enable-cli
    
    make -j$(nproc)
    sudo make install
    sudo ldconfig
    
    cd ~
    rm -rf "$temp_dir"
    
    sudo systemctl stop bluealsa-aplay.service bluealsa.service || true
    sudo systemctl disable bluealsa-aplay.service bluealsa.service || true
    
    log_success "bluez-alsa installé"
}

install_snapcast() {
    log_info "Installation de Snapcast..."
    
    # Détecter la version de Debian (bookworm, trixie, bullseye, etc.)
    DEBIAN_VERSION=$(lsb_release -sc 2>/dev/null || grep VERSION_CODENAME /etc/os-release | cut -d= -f2)
    
    if [[ -z "$DEBIAN_VERSION" ]]; then
        log_warning "Impossible de détecter la version Debian, utilisation de bookworm par défaut"
        DEBIAN_VERSION="bookworm"
    else
        log_info "Version Debian détectée: $DEBIAN_VERSION"
    fi

    # Méthode 1 : Essayer d'installer depuis les dépôts Debian (plus fiable)
    log_info "Tentative d'installation depuis les dépôts Debian..."
    if sudo apt install -y snapserver snapclient 2>/dev/null; then
        log_success "Snapcast installé depuis les dépôts Debian"
        snapserver --version
        snapclient --version
    else
        log_warning "Installation depuis les dépôts échouée, téléchargement des paquets GitHub..."
        
        # Méthode 2 : Télécharger les .deb depuis GitHub
        local temp_dir=$(mktemp -d)
        cd "$temp_dir"
        
        # Téléchargement avec la version Debian détectée
        log_info "Téléchargement de Snapcast pour $DEBIAN_VERSION..."
        if ! wget "https://github.com/badaix/snapcast/releases/download/v0.31.0/snapserver_0.31.0-1_arm64_${DEBIAN_VERSION}.deb" 2>/dev/null; then
            log_warning "Paquet pour $DEBIAN_VERSION non disponible, tentative avec bookworm..."
            DEBIAN_VERSION="bookworm"
            wget "https://github.com/badaix/snapcast/releases/download/v0.31.0/snapserver_0.31.0-1_arm64_bookworm.deb"
        fi
        
        wget "https://github.com/badaix/snapcast/releases/download/v0.31.0/snapclient_0.31.0-1_arm64_${DEBIAN_VERSION}.deb"
        
        # Installer les dépendances communes avant les .deb
        log_info "Installation des dépendances..."
        sudo apt install -y libavahi-client3 libavahi-common3 libflac12t64 || sudo apt install -y libflac12 || true
        
        # Installation des .deb avec gestion des dépendances
        if sudo apt install -y ./snapserver_0.31.0-1_arm64_${DEBIAN_VERSION}.deb ./snapclient_0.31.0-1_arm64_${DEBIAN_VERSION}.deb; then
            log_success "Snapcast installé depuis les paquets GitHub"
        else
            log_error "Échec de l'installation des paquets .deb"
            log_warning "Tentative de résolution des dépendances..."
            sudo apt --fix-broken install -y || true
            
            # Dernière tentative
            if sudo dpkg -i snapserver_0.31.0-1_arm64_${DEBIAN_VERSION}.deb snapclient_0.31.0-1_arm64_${DEBIAN_VERSION}.deb 2>/dev/null; then
                sudo apt --fix-broken install -y
                log_success "Snapcast installé après correction des dépendances"
            else
                log_error "Impossible d'installer Snapcast depuis les paquets"
                cd ~
                rm -rf "$temp_dir"
                return 1
            fi
        fi
        
        cd ~
        rm -rf "$temp_dir"
    fi

    snapserver --version
    snapclient --version

    sudo systemctl stop snapserver.service snapclient.service || true
    sudo systemctl disable snapserver.service snapclient.service || true

    log_success "Snapcast installé et configuré"
}

configure_journald() {
    log_info "Configuration des limites de journald..."

    sudo sed -i 's/^#RuntimeMaxUse=$/RuntimeMaxUse=100M/' /etc/systemd/journald.conf
    sudo sed -i 's/^#MaxRetentionSec=$/MaxRetentionSec=7d/' /etc/systemd/journald.conf

    log_success "Journald configuré (100MB max, 7 jours de rétention)"
}

install_readiness_script() {
    log_info "Installation du script de readiness..."

    # Copier le script de readiness vers /usr/local/bin/
    sudo cp "$MILO_APP_DIR/assets/milo-wait-ready.sh" /usr/local/bin/milo-wait-ready.sh
    sudo chmod +x /usr/local/bin/milo-wait-ready.sh

    log_success "Script de readiness installé dans /usr/local/bin/"
}

install_seatd() {
    log_info "Installation de seatd (requis pour Wayland/Cage)..."

    # seatd permet à milo-kiosk.service d'accéder aux VT sans permissions root
    sudo apt install -y seatd
    sudo systemctl enable seatd.service

    log_success "seatd installé et activé"
}

create_systemd_services() {
    log_info "Création des services systemd..."

    # milo-backend.service
    sudo tee /etc/systemd/system/milo-backend.service > /dev/null << 'EOF'
[Unit]
Description=Milo Backend Service
After=network.target

[Service]
Type=simple
User=milo
Group=milo
WorkingDirectory=/home/milo/milo
ExecStart=/home/milo/milo/venv/bin/python3 backend/main.py

# Token Github to check dependencies updates
#Environment="GITHUB_TOKEN=ADD_TOKEN_HERE"

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

    # milo-readiness.service
    sudo tee /etc/systemd/system/milo-readiness.service > /dev/null << 'EOF'
[Unit]
Description=Milo Readiness Check (waits for backend and frontend before quitting Plymouth)
After=milo-backend.service nginx.service
Requires=milo-backend.service nginx.service
Before=getty@tty1.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/milo-wait-ready.sh

# Timeout after 90s if services don't start
TimeoutStartSec=90

StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-readiness

[Install]
WantedBy=multi-user.target
EOF

    # milo-frontend.service - DISABLED (nginx serves /dist directly)
    # No longer needed as nginx serves the static files from /home/milo/milo/frontend/dist
    # This saves ~150MB RAM and one Node.js process

    # sudo tee /etc/systemd/system/milo-frontend.service > /dev/null << 'EOF'
# [Unit]
# Description=Milo Frontend Service (DEPRECATED - nginx serves /dist directly)
# After=network.target
#
# [Service]
# Type=simple
# User=milo
# Group=milo
# WorkingDirectory=/home/milo/milo/frontend
# ExecStart=/usr/bin/npm run preview -- --host 0.0.0.0 --port 3000
#
# Restart=always
# RestartSec=5
# TimeoutStopSec=10
#
# StateDirectory=milo
# StateDirectoryMode=0755
#
# Environment=NODE_ENV=production
# Environment=HOME=/home/milo
#
# StandardOutput=journal
# StandardError=journal
# SyslogIdentifier=milo-frontend
#
# [Install]
# WantedBy=multi-user.target
# EOF

    # milo-disable-wifi-power-management.service
    sudo tee /etc/systemd/system/milo-disable-wifi-power-management.service > /dev/null << 'EOF'
[Unit]
Description=Disable WiFi Power Management
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/sbin/iw dev wlan0 set power_save off
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

    # milo-kiosk.service
    sudo tee /etc/systemd/system/milo-kiosk.service > /dev/null << 'EOF'
[Unit]
Description=Milo Kiosk Mode (Cage + Chromium)
After=milo-readiness.service seatd.service
Requires=milo-readiness.service seatd.service
Conflicts=getty@tty1.service

[Service]
Type=simple
User=milo
Group=milo

# Create /run/user/1000 for XDG_RUNTIME_DIR
RuntimeDirectory=user/1000
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes

# Supplementary groups for seatd access
SupplementaryGroups=video

# Take control of tty1
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
StandardInput=tty
StandardOutput=journal
StandardError=journal

# Environment variables for Wayland/Cage
Environment=XDG_RUNTIME_DIR=/run/user/1000
Environment=XCURSOR_THEME=Adwaita
Environment=XCURSOR_SIZE=24
Environment=WLR_XCURSOR_THEME=Adwaita
Environment=WLR_XCURSOR_SIZE=24

# Launch Cage with Chromium in kiosk mode
ExecStart=/usr/bin/cage -- /usr/bin/chromium \
  --kiosk \
  --incognito \
  --password-store=basic \
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
  --touch-events=enabled \
  --enable-features=TouchpadAndWheelScrollLatching \
  --force-device-scale-factor=1 \
  --disable-pinch \
  --disable-features=VizDisplayCompositor \
  --app=http://milo.local

Restart=always
RestartSec=3

[Install]
WantedBy=graphical.target
EOF

    # milo-go-librespot.service
    sudo tee /etc/systemd/system/milo-go-librespot.service > /dev/null << 'EOF'
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
EnvironmentFile=/var/lib/milo/milo_environment
WorkingDirectory=/var/lib/milo
Restart=always
RestartSec=5

StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-go-librespot

[Install]
WantedBy=multi-user.target
EOF
    
    # milo-bluealsa.service
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
    
    # milo-bluealsa-aplay.service
    sudo tee /etc/systemd/system/milo-bluealsa-aplay.service > /dev/null << 'EOF'
[Unit]
Description=BlueALSA player for Milo
Requires=milo-bluealsa.service
After=milo-bluealsa.service milo-backend.service
BindsTo=milo-backend.service milo-bluealsa.service

[Service]
EnvironmentFile=/var/lib/milo/milo_environment
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

    # milo-roc.service
    sudo tee /etc/systemd/system/milo-roc.service > /dev/null << 'EOF'
[Unit]
Description=Milo ROC Audio Receiver
Documentation=https://roc-streaming.org/
After=network.target sound.service milo-backend.service
Wants=network.target milo-backend.service
BindsTo=milo-backend.service

[Service]
Type=exec
User=milo
Group=audio

EnvironmentFile=/etc/environment
EnvironmentFile=/var/lib/milo/milo_environment
Environment=HOME=/home/milo

ExecStart=/usr/bin/roc-recv -vvv \
  -s rtp+rs8m://0.0.0.0:10001 \
  -r rs8m://0.0.0.0:10002 \
  -c rtcp://0.0.0.0:10003 \
#  --target-latency=30ms \
#  --latency-profile=responsive \
#  --resampler-backend=builtin \
#  --resampler-profile=high \
#  --frame-len=6ms \
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

    # milo-radio.service
    sudo tee /etc/systemd/system/milo-radio.service > /dev/null << 'EOF'
[Unit]
Description=Milo Radio Player (mpv)
Documentation=https://mpv.io/manual/stable/
After=sound.target
# Dépend de l'environnement ALSA configuré par Milo
Requires=sound.target

[Service]
Type=simple
User=milo
Group=milo

# Créer automatiquement /run/milo/ pour le socket IPC
RuntimeDirectory=milo
RuntimeDirectoryMode=0755

# Charger les variables d'environnement Milo (MILO_MODE et MILO_EQUALIZER)
EnvironmentFile=/var/lib/milo/milo_environment

# Le device ALSA milo_radio se route automatiquement vers le bon device
# en fonction de MILO_MODE (direct/multiroom) et MILO_EQUALIZER (vide/_eq)
ExecStartPre=/bin/sh -c 'echo "ALSA routing: MILO_MODE=${MILO_MODE} MILO_EQUALIZER=${MILO_EQUALIZER}"'

# Lancer mpv en mode daemon avec IPC socket
ExecStart=/usr/bin/mpv \
    --no-video \
    --audio-device=alsa/milo_radio \
    --input-ipc-server=/run/milo/radio-ipc.sock \
    --idle=yes \
    --msg-level=all=info \
    --no-config \
    --no-terminal \
    --really-quiet

# Restart policy
Restart=on-failure
RestartSec=5s

# Limites de ressources
CPUQuota=50%
MemoryMax=256M

# Security hardening
NoNewPrivileges=true
PrivateTmp=true

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=milo-radio

[Install]
WantedBy=multi-user.target
EOF

    # milo-snapserver-multiroom.service
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

    # milo-snapclient-multiroom.service
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

    log_success "Services systemd créés"
}

configure_alsa_loopback() {
    log_info "Configuration du loopback ALSA..."
    
    echo "snd-aloop" | sudo tee /etc/modules-load.d/snd-aloop.conf
    echo "options snd-aloop index=1 enable=1 pcm_substreams=8" | sudo tee /etc/modprobe.d/snd-aloop.conf
    
    sudo modprobe snd-aloop || true
    
    log_success "Loopback ALSA configuré"
}

install_alsa_equal() {
    log_info "Installation de alsaequal..."
    
    sudo apt install -y libasound2-plugin-equal caps
    
    log_success "alsaequal installé"
}

configure_alsa_complete() {
    log_info "Configuration complète d'ALSA..."

    sudo tee /etc/asound.conf > /dev/null << 'EOF'
# Configuration ALSA pour Milo avec support Radio
# À copier dans /etc/asound.conf : sudo cp asound.conf.radio /etc/asound.conf

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

# === Aliases dynamiques (avec variables d'environnement MILO_MODE et MILO_EQUALIZER) ===

pcm.milo_spotify {
    @func concat
    strings [
        "pcm.milo_spotify_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
        { @func getenv vars [ MILO_EQUALIZER ] default "" }
    ]
}

pcm.milo_bluetooth {
    @func concat
    strings [
        "pcm.milo_bluetooth_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
        { @func getenv vars [ MILO_EQUALIZER ] default "" }
    ]
}

pcm.milo_roc {
    @func concat
    strings [
        "pcm.milo_roc_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
        { @func getenv vars [ MILO_EQUALIZER ] default "" }
    ]
}

pcm.milo_radio {
    @func concat
    strings [
        "pcm.milo_radio_"
        { @func getenv vars [ MILO_MODE ] default "direct" }
        { @func getenv vars [ MILO_EQUALIZER ] default "" }
    ]
}

# === Mode Multiroom (via snapcast) ===

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

pcm.milo_radio_multiroom {
    type plug
    slave.pcm {
        type hw
        card 1
        device 0
        subdevice 3
    }
}

# === Mode Multiroom avec Equalizer ===

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

pcm.milo_radio_multiroom_eq {
    type plug
    slave.pcm "equal_multiroom"
}

# === Mode Direct (vers hardware) ===

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

pcm.milo_radio_direct {
    type plug
    slave.pcm {
        type hw
        card sndrpihifiberry
        device 0
    }
}

# === Mode Direct avec Equalizer ===

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

pcm.milo_radio_direct_eq {
    type plug
    slave.pcm "equal"
}

# === Equalizer devices ===

pcm.equal {
    type equal
    slave.pcm "plughw:sndrpihifiberry"
}

pcm.equal_multiroom {
    type equal
    slave.pcm "plughw:1,0"
}

ctl.equal {
    type equal
}
EOF

    sudo tee /var/lib/milo/milo_environment > /dev/null << 'EOF'
MILO_MODE=direct
MILO_EQUALIZER=
EOF

    sudo chown "$MILO_USER:$MILO_USER" /var/lib/milo/milo_environment

    log_success "Configuration ALSA complète terminée"
}

configure_snapserver() {
    log_info "Configuration de Snapserver..."
    
    sudo tee /etc/snapserver.conf > /dev/null << 'EOF'

[stream]
default = Multiroom

buffer = 150
codec = opus
chunk_ms = 10
sampleformat = 48000:16:2

source = meta:///Bluetooth/ROC/Spotify/Radio?name=Multiroom

source = alsa:///?name=Bluetooth&device=hw:1,1,0
source = alsa:///?name=ROC&device=hw:1,1,1
source = alsa:///?name=Spotify&device=hw:1,1,2
source = alsa:///?name=Radio&device=hw:1,1,3

[streaming_client]
initial_volume = 28

[http]
enabled = true
bind_to_address = 0.0.0.0
port = 1780
doc_root = /usr/share/snapserver/snapweb/

[server]
threads = 4

[logging]
enabled = true
EOF
    log_success "Snapserver configuré"
}


configure_fan_control() {
    log_info "Configuration du contrôle du ventilateur..."
    
    local config_file="/boot/firmware/config.txt"
    
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
    fi
    
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
   
   log_success "Contrôle du ventilateur configuré"
}

install_avahi_nginx() {
    log_info "Installation d'Avahi, Nginx et Chromium..."
    
    sudo apt install -y avahi-daemon avahi-utils nginx
    
    # Installer Chromium (gère les 2 noms de paquets)
    if ! sudo apt install -y chromium 2>/dev/null; then
        log_info "Tentative avec chromium-browser..."
        sudo apt install -y chromium-browser
    fi
    
    log_success "Avahi, Nginx et Chromium installés"
}

configure_avahi() {
    log_info "Configuration d'Avahi (mDNS)..."
    
    sudo systemctl enable avahi-daemon
    sudo systemctl start avahi-daemon
    
    sudo tee /etc/avahi/services/milo.service > /dev/null << 'EOF'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Milo Audio System on %h</name>
  <service>
    <type>_http._tcp</type>
    <port>80</port>
    <txt-record>path=/</txt-record>
  </service>
  <service>
    <type>_snapcast._tcp</type>
    <port>1705</port>
  </service>
</service-group>
EOF

    sudo systemctl restart avahi-daemon
    
    log_success "Avahi configuré (accès via milo.local)"
}

configure_nginx() {
    log_info "Configuration de Nginx..."

    sudo tee /etc/nginx/sites-available/milo > /dev/null << 'EOF'
upstream milo_backend {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name milo.local localhost _;

    # Serve frontend static files directly from /dist
    root /home/milo/milo/frontend/dist;
    index index.html;

    # Cache static assets for better performance
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
        expires 1y;
        add_header Cache-Control "public, max-age=31536000, immutable";
        try_files $uri =404;
    }

    # Backend API endpoints
    location /api/ {
        proxy_pass http://milo_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Disable buffering for real-time API responses
        proxy_buffering off;
    }

    # WebSocket endpoint for real-time updates
    location /ws {
        proxy_pass http://milo_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # Long timeout for WebSocket connections
        proxy_read_timeout 86400;
        proxy_send_timeout 86400;
        proxy_buffering off;
    }

    # Serve index.html for all other routes (SPA routing)
    location / {
        try_files $uri $uri/ /index.html;
        add_header Cache-Control "no-cache, no-store, must-revalidate";
    }
}
EOF

    sudo ln -sf /etc/nginx/sites-available/milo /etc/nginx/sites-enabled/milo
    sudo rm -f /etc/nginx/sites-enabled/default

    sudo nginx -t
    sudo systemctl reload nginx

    log_success "Nginx configuré pour servir directement le frontend depuis /dist"
}

configure_cage_kiosk() {
    log_info "Configuration du mode kiosk avec Cage..."

    # Installer Cage (Wayland compositor)
    # Note: x11-xserver-utils n'est pas nécessaire car Cage est Wayland pur
    sudo apt install -y cage

    # Chromium est déjà installé via install_avahi_nginx()

    # Créer le répertoire .config si nécessaire
    sudo -u "$MILO_USER" mkdir -p "$MILO_HOME/.config"

    # Copier le script de lancement Cage depuis assets/
    if [[ ! -f "$MILO_APP_DIR/assets/milo-cage-start.sh" ]]; then
        log_error "Fichier milo-cage-start.sh non trouvé dans $MILO_APP_DIR/assets/"
        return 1
    fi

    sudo cp "$MILO_APP_DIR/assets/milo-cage-start.sh" "$MILO_HOME/.config/milo-cage-start.sh"
    sudo chmod +x "$MILO_HOME/.config/milo-cage-start.sh"
    sudo chown "$MILO_USER:$MILO_USER" "$MILO_HOME/.config/milo-cage-start.sh"

    # Copier .bash_profile depuis assets/
    if [[ ! -f "$MILO_APP_DIR/assets/bash_profile.template" ]]; then
        log_error "Fichier bash_profile.template non trouvé dans $MILO_APP_DIR/assets/"
        return 1
    fi

    sudo cp "$MILO_APP_DIR/assets/bash_profile.template" "$MILO_HOME/.bash_profile"
    sudo chown "$MILO_USER:$MILO_USER" "$MILO_HOME/.bash_profile"

    log_success "Mode kiosk configuré avec Cage (scripts copiés depuis assets/)"
}

install_milo_cursor_theme() {
    log_info "Installation des curseurs transparents (modification d'Adwaita)..."

    # Sauvegarder les curseurs Adwaita originaux (si pas déjà fait)
    if [[ ! -d /usr/share/icons/Adwaita/cursors.backup ]]; then
        log_info "Sauvegarde des curseurs Adwaita originaux..."
        sudo cp -r /usr/share/icons/Adwaita/cursors /usr/share/icons/Adwaita/cursors.backup
    else
        log_info "Curseurs Adwaita déjà sauvegardés, conservation de la sauvegarde existante"
    fi

    # Fichier Xcursor transparent complet encodé en base64 (68 bytes)
    # Format Xcursor avec un pixel 1x1 totalement transparent (ARGB = 00 00 00 00)
    log_info "Création du curseur transparent..."
    local xcursor_base64="WGN1chAAAAAAAAEAAQAAAAIA/f8YAAAAHAAAACQAAAACAP3/GAAAAAEAAAABAAAAAQAAAAAAAAAAAAAAMgAAAAAAAAA="
    echo "$xcursor_base64" | base64 -d > /tmp/transparent_cursor

    # Remplacer tous les curseurs Adwaita par le curseur transparent
    log_info "Remplacement de tous les curseurs Adwaita par des curseurs transparents..."

    # Trouver tous les fichiers dans le répertoire cursors (pas les liens symboliques)
    for cursor_file in /usr/share/icons/Adwaita/cursors/*; do
        # Ignorer les sauvegardes
        if [[ "$cursor_file" != *.backup ]]; then
            # Remplacer chaque fichier ou lien par notre curseur transparent
            sudo cp /tmp/transparent_cursor "$cursor_file"
        fi
    done

    # Nettoyer
    rm -f /tmp/transparent_cursor

    log_success "Curseurs Adwaita remplacés par des curseurs transparents"
    log_info "Pour restaurer les curseurs originaux : sudo rm -rf /usr/share/icons/Adwaita/cursors && sudo mv /usr/share/icons/Adwaita/cursors.backup /usr/share/icons/Adwaita/cursors"
}

configure_plymouth_splash() {
    log_info "Configuration de l'écran de démarrage avec thème Milo..."

    # Installer Plymouth
    sudo apt install -y plymouth plymouth-themes

    # Créer le répertoire du thème Milo
    sudo mkdir -p /usr/share/plymouth/themes/milo

    # Générer milo.plymouth
    log_info "Création du fichier de configuration Plymouth..."
    sudo tee /usr/share/plymouth/themes/milo/milo.plymouth > /dev/null << 'EOF'
[Plymouth Theme]
Name=Milo
Description=Milo Audio System Splash Screen
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/milo
ScriptFile=/usr/share/plymouth/themes/milo/milo.script
EOF

    # Générer milo.script
    log_info "Création du script Plymouth..."
    sudo tee /usr/share/plymouth/themes/milo/milo.script > /dev/null << 'EOF'
screen_width = Window.GetWidth();
screen_height = Window.GetHeight();

theme_image = Image("splash.png");
image_width = theme_image.GetWidth();
image_height = theme_image.GetHeight();

scale_x = image_width / screen_width;
scale_y = image_height / screen_height;

flag = 1;

if (scale_x > 1 || scale_y > 1)
{
	if (scale_x > scale_y)
	{
		resized_image = theme_image.Scale (screen_width, image_height / scale_x);
		image_x = 0;
		image_y = (screen_height - ((image_height  * screen_width) / image_width)) / 2;
	}
	else
	{
		resized_image = theme_image.Scale (image_width / scale_y, screen_height);
		image_x = (screen_width - ((image_width  * screen_height) / image_height)) / 2;
		image_y = 0;
	}
}
else
{
	resized_image = theme_image.Scale (image_width, image_height);
	image_x = (screen_width - image_width) / 2;
	image_y = (screen_height - image_height) / 2;
}

if (Plymouth.GetMode() != "shutdown")
{
	sprite = Sprite (resized_image);
	sprite.SetPosition (image_x, image_y, -100);
}

message_sprite = Sprite();
message_sprite.SetPosition(screen_width * 0.1, screen_height * 0.9, 10000);

fun message_callback (text) {
	my_image = Image.Text(text, 1, 1, 1);
	message_sprite.SetImage(my_image);
	sprite.SetImage (resized_image);
}

Plymouth.SetUpdateStatusFunction(message_callback);
EOF

    # Copier l'image splash depuis assets/
    if [[ -f "$MILO_APP_DIR/assets/splash.png" ]]; then
        log_info "Copie de l'image splash.png..."
        sudo cp "$MILO_APP_DIR/assets/splash.png" /usr/share/plymouth/themes/milo/splash.png
    else
        log_error "Image splash.png non trouvée dans $MILO_APP_DIR/assets/"
        return 1
    fi

    # Définir le thème Milo par défaut
    sudo plymouth-set-default-theme milo

    # Mettre à jour initramfs pour appliquer le thème
    sudo update-initramfs -u

    # Supprimer les messages console série
    sudo sed -i 's/console=serial0,115200//' /boot/firmware/cmdline.txt 2>/dev/null || \
    sudo sed -i 's/console=serial0,115200//' /boot/cmdline.txt 2>/dev/null || true

    # Ajouter les paramètres kernel pour un boot silencieux avec splash
    if ! grep -q "plymouth.ignore-serial-consoles" /boot/firmware/cmdline.txt 2>/dev/null && \
       ! grep -q "plymouth.ignore-serial-consoles" /boot/cmdline.txt 2>/dev/null; then
        sudo sed -i '$ s/$/ quiet splash plymouth.ignore-serial-consoles/' /boot/firmware/cmdline.txt 2>/dev/null || \
        sudo sed -i '$ s/$/ quiet splash plymouth.ignore-serial-consoles/' /boot/cmdline.txt 2>/dev/null
    fi

    # Rediriger console kernel vers tty3 et réduire verbosité
    sudo sed -i 's/console=tty1/console=tty3 loglevel=3/' /boot/firmware/cmdline.txt 2>/dev/null || \
    sudo sed -i 's/console=tty1/console=tty3 loglevel=3/' /boot/cmdline.txt 2>/dev/null || true

    # Vider /etc/issue pour cacher les messages getty
    sudo cp /etc/issue /etc/issue.backup 2>/dev/null || true
    echo "" | sudo tee /etc/issue > /dev/null

    # Supprimer IP.issue si existe
    sudo rm -f /etc/issue.d/IP.issue

    # Masquer plymouth-quit services (milo-readiness gère le quit manuellement)
    sudo systemctl mask plymouth-quit.service plymouth-quit-wait.service

    log_success "Écran de démarrage configuré avec thème Milo, Plymouth reste actif jusqu'au quit manuel"
    REBOOT_REQUIRED=true
}

disable_lightdm() {
    log_info "Désactivation de lightdm (Milo utilise autologin + Cage)..."

    # Arrêter et désactiver lightdm s'il est actif
    if systemctl is-active --quiet lightdm.service 2>/dev/null; then
        log_info "Arrêt de lightdm..."
        sudo systemctl stop lightdm.service || true
    fi

    if systemctl is-enabled --quiet lightdm.service 2>/dev/null; then
        log_info "Désactivation de lightdm..."
        sudo systemctl disable lightdm.service || true
    fi

    # Masquer le service pour empêcher son activation
    sudo systemctl mask lightdm.service 2>/dev/null || true

    # Supprimer le paquet lightdm s'il est installé
    if dpkg -l | grep -q "^ii.*lightdm"; then
        log_info "Suppression du paquet lightdm..."
        sudo apt remove -y lightdm 2>/dev/null || true
        sudo apt autoremove -y || true
    fi

    log_success "lightdm désactivé (Milo utilise getty@tty1 + autologin + Cage)"
}

configure_silent_login() {
    log_info "Désactivation de getty@tty1 (Cage prend le contrôle via milo-kiosk.service)..."

    # Masquer getty@tty1 car milo-kiosk.service prend le contrôle de tty1
    sudo systemctl mask getty@tty1.service

    sudo systemctl daemon-reload

    log_success "getty@tty1 masqué (milo-kiosk.service gère tty1)"
}

optimize_boot_performance() {
    log_info "Optimisation des performances de démarrage..."

    # Masquer NetworkManager-wait-online (économie de ~13.5s)
    # Ce service attend que la connexion réseau soit complète, mais Milo n'en a pas besoin
    sudo systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
    sudo systemctl mask NetworkManager-wait-online.service 2>/dev/null || true

    log_success "NetworkManager-wait-online.service masqué (gain ~13s au boot)"
}

install_screen_brightness_control() {
    if [[ "$SCREEN_TYPE" == "none" ]]; then
        log_info "Pas de contrôle de luminosité à installer"
        # Sauvegarder quand même le type "none" dans milo_hardware.json
        sudo tee "$MILO_DATA_DIR/milo_hardware.json" > /dev/null << EOF
{
  "screen": {
    "type": "none"
  }
}
EOF
        sudo chown "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR/milo_hardware.json"
        return
    fi

    log_info "Installation du contrôle de luminosité..."

        case $SCREEN_TYPE in
        "waveshare_7_usb")
            log_info "Installation du contrôle de luminosité pour Waveshare 7\" USB..."

            local temp_dir=$(mktemp -d)
            cd "$temp_dir"

            git clone https://github.com/waveshare/RPi-USB-Brightness
            cd RPi-USB-Brightness/64/lite
            sudo chmod +x Raspi_USB_Backlight_nogui
            ./Raspi_USB_Backlight_nogui -b 6

            # Copier l'utilitaire dans un emplacement accessible
            sudo cp Raspi_USB_Backlight_nogui /usr/local/bin/milo-brightness-7
            sudo chmod +x /usr/local/bin/milo-brightness-7

            cd ~
            rm -rf "$temp_dir"

            log_success "Contrôle de luminosité 7\" USB installé"
            ;;

        "waveshare_8_dsi")
            log_info "Installation du contrôle de luminosité pour Waveshare 8\" DSI..."

            local temp_dir=$(mktemp -d)
            cd "$temp_dir"

            wget https://files.waveshare.com/wiki/common/Brightness.zip
            unzip Brightness.zip
            cd Brightness
            sudo chmod +x install.sh
            ./install.sh

            # Test de la luminosité (valeur par défaut à 100)
            echo 100 | sudo tee /sys/class/backlight/*/brightness > /dev/null 2>&1 || true

            cd ~
            rm -rf "$temp_dir"

            # Créer la règle udev pour les permissions du backlight
            log_info "Configuration des permissions backlight (règle udev)..."
            sudo tee /etc/udev/rules.d/99-backlight.rules > /dev/null << 'EOF'
SUBSYSTEM=="backlight", RUN+="/bin/chmod 0666 /sys/class/backlight/%k/brightness"
EOF

            # Recharger les règles udev
            sudo udevadm control --reload-rules
            sudo udevadm trigger

            log_success "Contrôle de luminosité 8\" DSI installé"
            log_info "Règle udev créée pour les permissions backlight"
            log_info "Utilisez: echo VALUE | sudo tee /sys/class/backlight/*/brightness (VALUE: 0-255)"
            ;;
    esac

    # Sauvegarder le type d'écran dans milo_hardware.json
    log_info "Sauvegarde du type d'écran dans $MILO_DATA_DIR/milo_hardware.json..."
    sudo tee "$MILO_DATA_DIR/milo_hardware.json" > /dev/null << EOF
{
  "screen": {
    "type": "$SCREEN_TYPE"
  }
}
EOF
    sudo chown "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR/milo_hardware.json"
    log_success "Type d'écran '$SCREEN_TYPE' sauvegardé"
}

enable_services() {
   log_info "Démarrage automatique des services..."

   sudo systemctl daemon-reload

   # Configurer graphical.target comme target par défaut
   # Nécessaire pour que milo-kiosk.service démarre (WantedBy=graphical.target)
   # Sur Raspberry Pi OS Lite, le système démarre en multi-user.target par défaut
   local current_target=$(systemctl get-default)
   if [[ "$current_target" != "graphical.target" ]]; then
       log_info "Configuration du système pour démarrer en graphical.target (requis pour milo-kiosk)..."
       sudo systemctl set-default graphical.target
       log_success "Target par défaut configuré: graphical.target"
   else
       log_info "Target par défaut déjà configuré: graphical.target"
   fi

   # Services qui doivent être enabled au démarrage
   sudo systemctl enable milo-backend.service
   sudo systemctl enable milo-readiness.service
   sudo systemctl enable milo-kiosk.service
   sudo systemctl enable milo-bluealsa.service
   sudo systemctl enable milo-bluealsa-aplay.service
   sudo systemctl enable milo-disable-wifi-power-management.service
   sudo systemctl enable avahi-daemon
   sudo systemctl enable nginx

   # Note: milo-frontend.service is no longer used (nginx serves /dist directly)
   # Note: getty@tty1 is masked (milo-kiosk.service takes control of tty1)

   # Note: Les services suivants sont gérés dynamiquement par le backend Milo:
   # - milo-go-librespot.service
   # - milo-roc.service
   # - milo-radio.service
   # - milo-snapserver-multiroom.service
   # - milo-snapclient-multiroom.service
   # Ces services ne doivent PAS être "enabled" au démarrage

   log_success "Démarrage automatique configuré"
}

start_services() {
   log_info "Démarrage des services..."

   # Démarrage uniquement des services enabled
   sudo systemctl start milo-backend.service
   sudo systemctl start milo-readiness.service
   sudo systemctl start milo-bluealsa.service
   sudo systemctl start milo-bluealsa-aplay.service
   sudo systemctl start milo-disable-wifi-power-management.service
   sudo systemctl start avahi-daemon
   sudo systemctl start nginx

   # Note: milo-frontend.service is no longer used (nginx serves /dist directly)

   # Note: Les services audio (go-librespot, roc, radio, snapcast)
   # seront démarrés automatiquement par le backend Milo selon les besoins

   log_success "Services démarrés"
}

finalize_installation() {
   log_info "Finalisation de l'installation..."
   
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
   if [[ "$SCREEN_TYPE" != "none" ]]; then
       case $SCREEN_TYPE in
           "waveshare_7_usb") echo "  • Écran: Waveshare 7\" USB 1024x600" ;;
           "waveshare_8_dsi") echo "  • Écran: Waveshare 8\" DSI 1280x800" ;;
       esac
   fi
   echo ""
   echo -e "${BLUE}Accès :${NC}"
   echo "  • Interface web: http://milo.local"
   echo "  • Spotify Connect: 'Milō'"
   echo "  • Bluetooth: 'Milō · Bluetooth'"
   echo ""
   
   if [[ "$REBOOT_REQUIRED" == "true" ]]; then
       echo -e "${YELLOW}⚠️  REDÉMARRAGE REQUIS${NC}"
       echo ""
       
       case $USER_RESTART_CHOICE in
           "yes")
               log_info "Redémarrage automatique dans 5 secondes..."
               sleep 5
               sudo reboot
               ;;
           "no")
               echo -e "${YELLOW}Pensez à redémarrer manuellement avec: sudo reboot${NC}"
               ;;
       esac
   else
       start_services
       
       echo ""
       echo -e "${GREEN}✅ Milo est prêt ! Accédez à http://milo.local${NC}"
   fi
}

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

   log_info "Suppression des thèmes Milo..."
   sudo rm -rf /usr/share/icons/Milo
   sudo rm -rf /usr/share/plymouth/themes/milo

   log_info "Suppression des binaires..."
   sudo rm -f /usr/local/bin/go-librespot
   sudo rm -f /usr/local/bin/milo-brightness-7
   
   log_info "Nettoyage des packages..."
   sudo apt autoremove -y
   
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

main() {
   show_banner
   
   if [[ "$1" == "--uninstall" ]]; then
       uninstall_milo
       exit 0
   fi
   
   check_root
   
   log_info "Début de l'installation de Milo Audio System"
   echo ""
   
   check_system
   
   collect_user_choices
   
   install_dependencies
   setup_hostname
   configure_audio_hardware
   configure_screen_hardware
   
   create_milo_user
   install_milo_application
   fix_nginx_permissions
   suppress_pulseaudio
   
   install_go_librespot
   install_roc_toolkit
   install_bluez_alsa
   install_snapcast

   install_readiness_script
   create_systemd_services
   configure_journald

   configure_alsa_loopback
   install_alsa_equal
   configure_alsa_complete
   configure_snapserver
   
   configure_fan_control

   install_seatd
   install_avahi_nginx
   configure_avahi
   configure_nginx
   configure_cage_kiosk
   install_milo_cursor_theme
   configure_plymouth_splash
   disable_lightdm
   configure_silent_login
   optimize_boot_performance

   install_screen_brightness_control

   enable_services
   finalize_installation
}

main "$@"