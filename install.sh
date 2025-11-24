#!/bin/bash
# Milo Audio System - Installation Script v1.3
#
# IMPORTANT: This script is optimized for Raspberry Pi OS Lite (64-bit)
# Raspberry Pi OS Lite is recommended to minimize resource usage
# and avoid conflicts with unnecessary desktop services.
#
# Download Raspberry Pi OS Lite from: https://www.raspberrypi.com/software/operating-systems/

set -e

MILO_USER="milo"
MILO_HOME="/home/$MILO_USER"
MILO_APP_DIR="$MILO_HOME/milo"
MILO_DATA_DIR="/var/lib/milo"
MILO_REPO="https://github.com/leodurandfr/Milo.git"
REQUIRED_HOSTNAME="milo"
REBOOT_REQUIRED=false

# Variables to store user choices
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
        log_error "This script must not be run as root."
        exit 1
    fi
}

check_system() {
    log_info "Checking system..."

    if ! grep -q "Raspberry Pi" /proc/cpuinfo; then
        log_error "This script is designed for Raspberry Pi only."
        exit 1
    fi

    ARCH=$(uname -m)
    if [[ "$ARCH" != "aarch64" ]]; then
        log_error "Unsupported architecture: $ARCH. Raspberry Pi OS 64bit required."
        exit 1
    fi

    # Warning if a desktop environment is detected
    if systemctl list-units --type=service | grep -qE "lightdm|gdm|sddm|xdm"; then
        log_warning "A desktop environment has been detected."
        log_warning "Raspberry Pi OS Lite is recommended for optimal performance."
        echo ""
    fi

    log_success "Compatible system detected (Raspberry Pi OS 64-bit)"
}

collect_user_choices() {
    echo ""
    log_info "Initial configuration - Please answer the following questions:"
    echo ""
    
    # 1. Hostname verification
    local current_hostname=$(hostname)
    log_info "Current hostname: $current_hostname"
    
    if [ "$current_hostname" != "$REQUIRED_HOSTNAME" ]; then
        echo ""
        echo -e "${YELLOW}⚠️  IMPORTANT:${NC} Milo requires the hostname '${REQUIRED_HOSTNAME}' for:"
        echo "   • Access via ${REQUIRED_HOSTNAME}.local from the browser"
        echo "   • Optimal multiroom functionality (Snapserver)"
        echo "   • Network discovery of other Milo instances"
        echo ""
        echo -e "${BLUE}🔄 Hostname change required:${NC} '$current_hostname' → '$REQUIRED_HOSTNAME'"
        echo ""
        
        while true; do
            read -p "Change hostname to '$REQUIRED_HOSTNAME'? (Y/n): " USER_HOSTNAME_CHANGE
            case $USER_HOSTNAME_CHANGE in
                [Nn]* )
                    log_error "Installation cancelled. Hostname '$REQUIRED_HOSTNAME' is required."
                    exit 1
                    ;;
                [Yy]* | "" )
                    USER_HOSTNAME_CHANGE="yes"
                    break
                    ;;
                * )
                    echo "Please answer 'Y' (yes) or 'n' (no)."
                    ;;
            esac
        done
    else
        USER_HOSTNAME_CHANGE="already_set"
    fi
    
    # 2. HiFiBerry card selection
    echo ""
    log_info "Configuring HiFiBerry audio card..."
    echo ""
    echo "Select your HiFiBerry audio card:"
    echo ""
    echo "1) Amp2"
    echo "2) Amp4"
    echo "3) Amp4 Pro"
    echo "4) Amp100"
    echo "5) Beocreate 4CA"
    echo "6) Skip (for manual installation)"
    echo ""
    
    while true; do
        read -p "Your choice [1]: " USER_HIFIBERRY_CHOICE
        USER_HIFIBERRY_CHOICE=${USER_HIFIBERRY_CHOICE:-1}
        
        case $USER_HIFIBERRY_CHOICE in
            1) HIFIBERRY_OVERLAY="hifiberry-dacplus-std"; CARD_NAME="sndrpihifiberry"; log_success "Selected card: Amp2"; break;;
            2) HIFIBERRY_OVERLAY="hifiberry-dacplus-std"; CARD_NAME="sndrpihifiberry"; log_success "Selected card: Amp4"; break;;
            3) HIFIBERRY_OVERLAY="hifiberry-amp4pro"; CARD_NAME="sndrpihifiberry"; log_success "Selected card: Amp4 Pro"; break;;
            4) HIFIBERRY_OVERLAY="hifiberry-amp100"; CARD_NAME="sndrpihifiberry"; log_success "Selected card: Amp100"; break;;
            5) HIFIBERRY_OVERLAY="hifiberry-dac"; CARD_NAME="sndrpihifiberry"; log_success "Selected card: Beocreate 4CA"; break;;
            6) HIFIBERRY_OVERLAY=""; CARD_NAME=""; log_warning "HiFiBerry configuration skipped"; break;;
            *) echo "Invalid choice. Please enter a number between 1 and 6.";;
        esac
    done
    
    # 3. Screen selection
    echo ""
    log_info "Configuring touchscreen..."
    echo ""
    echo "Select your screen:"
    echo ""
    echo "1) Waveshare 7\" 1024x600 (USB)"
    echo "2) Waveshare 8\" 1280x800 (DSI)"  
    echo "3) No screen or manual installation"
    echo ""
    
    while true; do
        read -p "Your choice [1]: " USER_SCREEN_CHOICE
        USER_SCREEN_CHOICE=${USER_SCREEN_CHOICE:-1}
        
        case $USER_SCREEN_CHOICE in
            1) SCREEN_TYPE="waveshare_7_usb"; log_success "Selected screen: Waveshare 7\" USB"; break;;
            2) SCREEN_TYPE="waveshare_8_dsi"; log_success "Selected screen: Waveshare 8\" DSI"; break;;
            3) SCREEN_TYPE="none"; log_warning "Screen configuration skipped"; break;;
            *) echo "Invalid choice. Please enter a number between 1 and 3.";;
        esac
    done
    
    # 4. Reboot choice
    echo ""
    log_info "A reboot will be required at the end of installation."
    while true; do
        read -p "Automatically reboot at the end? (Y/n): " USER_RESTART_CHOICE
        case $USER_RESTART_CHOICE in
            [Nn]* )
                USER_RESTART_CHOICE="no"
                break
                ;;
            [Yy]* | "" )
                USER_RESTART_CHOICE="yes"
                break
                ;;
            * )
                echo "Please answer 'Y' (yes) or 'n' (no)."
                ;;
        esac
    done
    
    echo ""
    log_success "Configuration complete! Installation will now continue automatically..."
    echo ""
    sleep 2
}

setup_hostname() {
    local current_hostname=$(hostname)
    
    log_info "Applying hostname configuration..."
    
    if [ "$USER_HOSTNAME_CHANGE" == "yes" ]; then
        log_info "Configuring hostname '$REQUIRED_HOSTNAME'..."
        configure_hostname "$REQUIRED_HOSTNAME"
        log_success "Hostname configured"
        REBOOT_REQUIRED=true
    elif [ "$USER_HOSTNAME_CHANGE" == "already_set" ]; then
        log_success "Hostname '$REQUIRED_HOSTNAME' already configured"
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
        log_info "HiFiBerry configuration skipped"
        return
    fi
    
    log_info "Configuring audio hardware for HiFiBerry..."
    
    local config_file="/boot/firmware/config.txt"
    
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
        if [[ ! -f "$config_file" ]]; then
            log_error "config.txt file not found"
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
    
    log_success "Audio hardware configuration complete"
    REBOOT_REQUIRED=true
}

configure_screen_hardware() {
    if [[ "$SCREEN_TYPE" == "none" ]]; then
        log_info "Screen configuration skipped"
        return
    fi
    
    log_info "Configuring screen hardware..."
    
    local config_file="/boot/firmware/config.txt"
    
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
        if [[ ! -f "$config_file" ]]; then
            log_error "config.txt file not found"
            exit 1
        fi
    fi
    
    if [[ ! -f "$config_file.backup.$(date +%Y%m%d)" ]]; then
        sudo cp "$config_file" "$config_file.backup.$(date +%Y%m%d_%H%M%S)"
    fi
    
    case $SCREEN_TYPE in
        "waveshare_8_dsi")
            log_info "Configuring for Waveshare 8\" DSI..."
            if ! grep -q "dtoverlay=vc4-kms-dsi-waveshare-panel,8_0_inch" "$config_file"; then
                echo "" | sudo tee -a "$config_file"
                echo "#DSI1 Use - Waveshare 8\" 1280x800" | sudo tee -a "$config_file"
                echo "dtoverlay=vc4-kms-dsi-waveshare-panel,8_0_inch" | sudo tee -a "$config_file"
            fi
            REBOOT_REQUIRED=true
            ;;
        "waveshare_7_usb")
            log_info "Configuring for Waveshare 7\" USB (no config.txt modification required)"
            ;;
    esac
    
    log_success "Screen hardware configuration complete"
}

install_dependencies() {
    log_info "Updating system..."
    
    export DEBIAN_FRONTEND=noninteractive
    export DEBCONF_NONINTERACTIVE_SEEN=true
    
    echo 'Dpkg::Options {
       "--force-confdef";
       "--force-confnew";
    }' | sudo tee /etc/apt/apt.conf.d/local >/dev/null
    
    sudo apt update
    sudo apt upgrade -y
    
    log_info "Installing base dependencies..."
		# Configuration optimized for Raspberry Pi OS Lite
        sudo apt install -y \
            git python3-pip python3-venv python3-dev libasound2-dev libssl-dev \
            cmake build-essential pkg-config swig liblgpio-dev nodejs npm wget unzip \
            fontconfig mpv libinput-tools bc \
            fonts-noto fonts-noto-cjk fonts-lohit-deva fonts-noto-color-emoji
    
    log_info "Updating Node.js and npm..."
    sudo npm install -g n
    sudo n stable
    sudo npm install -g npm@latest
    hash -r
    
    sudo rm -f /etc/apt/apt.conf.d/local
    
    log_success "Dependencies installed"
}

create_milo_user() {
    if id "$MILO_USER" &>/dev/null; then
        log_info "User '$MILO_USER' already exists"
    else
        log_info "Creating user '$MILO_USER'..."
        sudo useradd -m -s /bin/bash "$MILO_USER"
        sudo usermod -aG audio,video,bluetooth,input "$MILO_USER"
        log_success "User '$MILO_USER' created"
    fi
    
    sudo mkdir -p "$MILO_DATA_DIR"
    sudo chown -R "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR"
}

install_milo_application() {
    log_info "Cloning and configuring Milo..."
    
    cd /tmp
    
    if [[ -d "$MILO_APP_DIR" ]]; then
        log_warning "Directory $MILO_APP_DIR already exists, removing..."
        sudo rm -rf "$MILO_APP_DIR"
    fi
    
    sudo -u "$MILO_USER" git clone "$MILO_REPO" "$MILO_APP_DIR"
    cd "$MILO_APP_DIR"
    
    log_info "Configuring Python environment..."
    sudo -u "$MILO_USER" python3 -m venv venv
    sudo -u "$MILO_USER" bash -c "source venv/bin/activate && pip install --upgrade pip"
    sudo -u "$MILO_USER" bash -c "source venv/bin/activate && pip install -r requirements.txt"
    
    log_info "Building frontend..."
    cd frontend
    sudo -u "$MILO_USER" npm install
    sudo -u "$MILO_USER" npm run build
    cd ..
    
    log_success "Milo application installed"
}

fix_nginx_permissions() {
    log_info "Configuring permissions for nginx..."
    
    sudo chmod 755 /home/milo
    sudo chmod 755 /home/milo/milo
    sudo chmod 755 /home/milo/milo/frontend
    sudo chmod -R 755 /home/milo/milo/frontend/dist
    
    sudo chown -R "$MILO_USER:$MILO_USER" /home/milo/milo/frontend/dist
    
    log_success "Nginx permissions configured"
}

suppress_pulseaudio() {
    log_info "Removing PulseAudio/PipeWire..."
    sudo apt remove -y pulseaudio pipewire || true
    sudo apt autoremove -y
    log_success "PulseAudio/PipeWire removed"
}

install_go_librespot() {
    log_info "Installing go-librespot..."
    
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
    
    log_success "go-librespot installed"
}

install_roc_toolkit() {
    log_info "Installing roc-toolkit..."
    
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
    
    log_success "roc-toolkit installed"
}

install_bluez_alsa() {
    log_info "Installing bluez-alsa..."

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
    
    # FIX: Use --disable-systemd because we manage our own systemd services
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
    
    log_success "bluez-alsa installed"
}

install_snapcast() {
    log_info "Installing Snapcast..."
    
    # Detect Debian version (bookworm, trixie, bullseye, etc.)
    DEBIAN_VERSION=$(lsb_release -sc 2>/dev/null || grep VERSION_CODENAME /etc/os-release | cut -d= -f2)
    
    if [[ -z "$DEBIAN_VERSION" ]]; then
        log_warning "Unable to detect Debian version, using bookworm as default"
        DEBIAN_VERSION="bookworm"
    else
        log_info "Detected Debian version: $DEBIAN_VERSION"
    fi

    # Method 1: Try to install from Debian repositories (more reliable)
    log_info "Attempting installation from Debian repositories..."
    if sudo apt install -y snapserver snapclient 2>/dev/null; then
        log_success "Snapcast installed from Debian repositories"
        snapserver --version
        snapclient --version
    else
        log_warning "Installation from repositories failed, downloading packages from GitHub..."
        
        # Method 2: Download .deb packages from GitHub
        local temp_dir=$(mktemp -d)
        cd "$temp_dir"
        
        # Download with detected Debian version
        log_info "Downloading Snapcast for $DEBIAN_VERSION..."
        if ! wget "https://github.com/badaix/snapcast/releases/download/v0.31.0/snapserver_0.31.0-1_arm64_${DEBIAN_VERSION}.deb" 2>/dev/null; then
            log_warning "Package for $DEBIAN_VERSION not available, trying with bookworm..."
            DEBIAN_VERSION="bookworm"
            wget "https://github.com/badaix/snapcast/releases/download/v0.31.0/snapserver_0.31.0-1_arm64_bookworm.deb"
        fi
        
        wget "https://github.com/badaix/snapcast/releases/download/v0.31.0/snapclient_0.31.0-1_arm64_${DEBIAN_VERSION}.deb"
        
        # Install common dependencies before .deb files
        log_info "Installing dependencies..."
        sudo apt install -y libavahi-client3 libavahi-common3 libflac12t64 || sudo apt install -y libflac12 || true
        
        # Install .deb files with dependency management
        if sudo apt install -y ./snapserver_0.31.0-1_arm64_${DEBIAN_VERSION}.deb ./snapclient_0.31.0-1_arm64_${DEBIAN_VERSION}.deb; then
            log_success "Snapcast installed from GitHub packages"
        else
            log_error "Failed to install .deb packages"
            log_warning "Attempting to resolve dependencies..."
            sudo apt --fix-broken install -y || true
            
            # Last attempt
            if sudo dpkg -i snapserver_0.31.0-1_arm64_${DEBIAN_VERSION}.deb snapclient_0.31.0-1_arm64_${DEBIAN_VERSION}.deb 2>/dev/null; then
                sudo apt --fix-broken install -y
                log_success "Snapcast installed after fixing dependencies"
            else
                log_error "Unable to install Snapcast from packages"
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

    log_success "Snapcast installed and configured"
}

configure_journald() {
    log_info "Configuring journald limits..."

    sudo sed -i 's/^#RuntimeMaxUse=$/RuntimeMaxUse=100M/' /etc/systemd/journald.conf
    sudo sed -i 's/^#MaxRetentionSec=$/MaxRetentionSec=7d/' /etc/systemd/journald.conf

    log_success "Journald configured (100MB max, 7 days retention)"
}

install_readiness_script() {
    log_info "Installing readiness script..."

    # Copy readiness script to /usr/local/bin/
    sudo cp "$MILO_APP_DIR/assets/milo-wait-ready.sh" /usr/local/bin/milo-wait-ready.sh
    sudo chmod +x /usr/local/bin/milo-wait-ready.sh

    log_success "Readiness script installed in /usr/local/bin/"
}

install_seatd() {
    log_info "Installing seatd (required for Wayland/Cage)..."

    # seatd allows milo-kiosk.service to access VTs without root permissions
    sudo apt install -y seatd
    sudo systemctl enable seatd.service

    log_success "seatd installed and enabled"
}

create_systemd_services() {
    log_info "Installing systemd services..."

    # Copy all .service files from system/ to /etc/systemd/system/
    for service_file in "$MILO_APP_DIR/system"/*.service; do
        if [[ -f "$service_file" ]]; then
            local service_name=$(basename "$service_file")
            sudo cp "$service_file" /etc/systemd/system/
            log_success "Installed $service_name"
        fi
    done

    # Reload systemd daemon to recognize new services
    sudo systemctl daemon-reload

    log_success "Systemd services installed"
}

configure_alsa_loopback() {
    log_info "Configuring ALSA loopback..."
    
    echo "snd-aloop" | sudo tee /etc/modules-load.d/snd-aloop.conf
    echo "options snd-aloop index=1 enable=1 pcm_substreams=8" | sudo tee /etc/modprobe.d/snd-aloop.conf
    
    sudo modprobe snd-aloop || true
    
    log_success "ALSA loopback configured"
}

install_alsa_equal() {
    log_info "Installing alsaequal..."
    
    sudo apt install -y libasound2-plugin-equal caps
    
    log_success "alsaequal installed"
}

configure_alsa_complete() {
    log_info "Configuring complete ALSA setup..."

    sudo tee /etc/asound.conf > /dev/null << 'EOF'
# ALSA Configuration for Milo with Radio support
# To copy to /etc/asound.conf: sudo cp asound.conf.radio /etc/asound.conf

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

# === Dynamic aliases (with MILO_MODE and MILO_EQUALIZER environment variables) ===

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

# === Multiroom Mode (via snapcast) ===

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

# === Multiroom Mode with Equalizer ===

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

# === Direct Mode (to hardware) ===

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

# === Direct Mode with Equalizer ===

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

    sudo tee /var/lib/milo/routing.env > /dev/null << 'EOF'
MILO_MODE=direct
MILO_EQUALIZER=
EOF

    sudo chown "$MILO_USER:$MILO_USER" /var/lib/milo/routing.env

    log_success "Complete ALSA configuration done"
}

configure_snapserver() {
    log_info "Configuring Snapserver..."
    
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
initial_volume = 16

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
    log_success "Snapserver configured"
}


configure_fan_control() {
    log_info "Configuring fan control..."
    
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
   
   log_success "Fan control configured"
}

install_avahi_nginx() {
    log_info "Installing Avahi, Nginx and Chromium..."
    
    sudo apt install -y avahi-daemon avahi-utils nginx
    
    # Install Chromium (handles both package names)
    if ! sudo apt install -y chromium 2>/dev/null; then
        log_info "Trying with chromium-browser..."
        sudo apt install -y chromium-browser
    fi
    
    log_success "Avahi, Nginx and Chromium installed"
}

configure_avahi() {
    log_info "Configuring Avahi (mDNS)..."
    
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
    
    log_success "Avahi configured (access via milo.local)"
}

configure_nginx() {
    log_info "Configuring Nginx..."

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

    # Radio images - must come BEFORE static files regex
    location ^~ /api/radio/images/ {
        proxy_pass http://milo_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }

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

    log_success "Nginx configured to serve frontend directly from /dist"
}

configure_cage_kiosk() {
    log_info "Configuring kiosk mode with Cage..."

    # Install Cage (Wayland compositor)
    # Note: x11-xserver-utils is not needed as Cage is pure Wayland
    sudo apt install -y cage

    # Chromium is already installed via install_avahi_nginx()

    # Create .config directory if needed
    sudo -u "$MILO_USER" mkdir -p "$MILO_HOME/.config"

    # Copy Cage launch script from assets/
    if [[ ! -f "$MILO_APP_DIR/assets/milo-cage-start.sh" ]]; then
        log_error "File milo-cage-start.sh not found in $MILO_APP_DIR/assets/"
        return 1
    fi

    sudo cp "$MILO_APP_DIR/assets/milo-cage-start.sh" "$MILO_HOME/.config/milo-cage-start.sh"
    sudo chmod +x "$MILO_HOME/.config/milo-cage-start.sh"
    sudo chown "$MILO_USER:$MILO_USER" "$MILO_HOME/.config/milo-cage-start.sh"

    # Copy .bash_profile from assets/
    if [[ ! -f "$MILO_APP_DIR/assets/bash_profile.template" ]]; then
        log_error "File bash_profile.template not found in $MILO_APP_DIR/assets/"
        return 1
    fi

    sudo cp "$MILO_APP_DIR/assets/bash_profile.template" "$MILO_HOME/.bash_profile"
    sudo chown "$MILO_USER:$MILO_USER" "$MILO_HOME/.bash_profile"

    log_success "Kiosk mode configured with Cage (scripts copied from assets/)"
}

install_milo_cursor_theme() {
    log_info "Installing transparent cursors (modified Adwaita)..."

    # Backup original Adwaita cursors (if not already done)
    if [[ ! -d /usr/share/icons/Adwaita/cursors.backup ]]; then
        log_info "Backing up original Adwaita cursors..."
        sudo cp -r /usr/share/icons/Adwaita/cursors /usr/share/icons/Adwaita/cursors.backup
    else
        log_info "Adwaita cursors already backed up, keeping existing backup"
    fi

    # Full transparent Xcursor file encoded in base64 (68 bytes)
    # Xcursor format with a 1x1 fully transparent pixel (ARGB = 00 00 00 00)
    log_info "Creating transparent cursor..."
    local xcursor_base64="WGN1chAAAAAAAAEAAQAAAAIA/f8YAAAAHAAAACQAAAACAP3/GAAAAAEAAAABAAAAAQAAAAAAAAAAAAAAMgAAAAAAAAA="
    echo "$xcursor_base64" | base64 -d > /tmp/transparent_cursor

    # Replace all Adwaita cursors with the transparent cursor
    log_info "Replacing all Adwaita cursors with transparent cursors..."

    # Find all files in the cursors directory (not symbolic links)
    for cursor_file in /usr/share/icons/Adwaita/cursors/*; do
        # Ignore backups
        if [[ "$cursor_file" != *.backup ]]; then
            # Replace each file or link with our transparent cursor
            sudo cp /tmp/transparent_cursor "$cursor_file"
        fi
    done

    # Cleanup
    rm -f /tmp/transparent_cursor

    log_success "Adwaita cursors replaced with transparent cursors"
    log_info "To restore original cursors: sudo rm -rf /usr/share/icons/Adwaita/cursors && sudo mv /usr/share/icons/Adwaita/cursors.backup /usr/share/icons/Adwaita/cursors"
}

configure_plymouth_splash() {
    log_info "Configuring boot splash screen with Milo theme..."

    # Install Plymouth
    sudo apt install -y plymouth plymouth-themes

    # Create Milo theme directory
    sudo mkdir -p /usr/share/plymouth/themes/milo

    # Generate milo.plymouth
    log_info "Creating Plymouth configuration file..."
    sudo tee /usr/share/plymouth/themes/milo/milo.plymouth > /dev/null << 'EOF'
[Plymouth Theme]
Name=Milo
Description=Milo Audio System Splash Screen
ModuleName=script

[script]
ImageDir=/usr/share/plymouth/themes/milo
ScriptFile=/usr/share/plymouth/themes/milo/milo.script
EOF

    # Generate milo.script
    log_info "Creating Plymouth script..."
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

    # Copy splash image from assets/
    if [[ -f "$MILO_APP_DIR/assets/splash.png" ]]; then
        log_info "Copying splash.png image..."
        sudo cp "$MILO_APP_DIR/assets/splash.png" /usr/share/plymouth/themes/milo/splash.png
    else
        log_error "splash.png image not found in $MILO_APP_DIR/assets/"
        return 1
    fi

    # Set Milo theme as default
    sudo plymouth-set-default-theme milo

    # Update initramfs to apply theme
    sudo update-initramfs -u

    # Remove serial console messages
    sudo sed -i 's/console=serial0,115200//' /boot/firmware/cmdline.txt 2>/dev/null || \
    sudo sed -i 's/console=serial0,115200//' /boot/cmdline.txt 2>/dev/null || true

    # Add kernel parameters for silent boot with splash
    if ! grep -q "plymouth.ignore-serial-consoles" /boot/firmware/cmdline.txt 2>/dev/null && \
       ! grep -q "plymouth.ignore-serial-consoles" /boot/cmdline.txt 2>/dev/null; then
        sudo sed -i '$ s/$/ quiet splash plymouth.ignore-serial-consoles/' /boot/firmware/cmdline.txt 2>/dev/null || \
        sudo sed -i '$ s/$/ quiet splash plymouth.ignore-serial-consoles/' /boot/cmdline.txt 2>/dev/null
    fi

    # Redirect kernel console to tty3 and reduce verbosity
    sudo sed -i 's/console=tty1/console=tty3 loglevel=3/' /boot/firmware/cmdline.txt 2>/dev/null || \
    sudo sed -i 's/console=tty1/console=tty3 loglevel=3/' /boot/cmdline.txt 2>/dev/null || true

    # Clear /etc/issue to hide getty messages
    sudo cp /etc/issue /etc/issue.backup 2>/dev/null || true
    echo "" | sudo tee /etc/issue > /dev/null

    # Remove IP.issue if exists
    sudo rm -f /etc/issue.d/IP.issue

    # Mask plymouth-quit services (milo-readiness handles quit manually)
    sudo systemctl mask plymouth-quit.service plymouth-quit-wait.service

    log_success "Boot splash screen configured with Milo theme, Plymouth stays active until manual quit"
    REBOOT_REQUIRED=true
}

disable_lightdm() {
    log_info "Disabling lightdm (Milo uses autologin + Cage)..."

    # Stop and disable lightdm if active
    if systemctl is-active --quiet lightdm.service 2>/dev/null; then
        log_info "Stopping lightdm..."
        sudo systemctl stop lightdm.service || true
    fi

    if systemctl is-enabled --quiet lightdm.service 2>/dev/null; then
        log_info "Disabling lightdm..."
        sudo systemctl disable lightdm.service || true
    fi

    # Mask the service to prevent activation
    sudo systemctl mask lightdm.service 2>/dev/null || true

    # Remove lightdm package if installed
    if dpkg -l | grep -q "^ii.*lightdm"; then
        log_info "Removing lightdm package..."
        sudo apt remove -y lightdm 2>/dev/null || true
        sudo apt autoremove -y || true
    fi

    log_success "lightdm disabled (Milo uses getty@tty1 + autologin + Cage)"
}

configure_silent_login() {
    log_info "Disabling getty@tty1 (Cage takes control via milo-kiosk.service)..."

    # Mask getty@tty1 as milo-kiosk.service takes control of tty1
    sudo systemctl mask getty@tty1.service

    sudo systemctl daemon-reload

    log_success "getty@tty1 masked (milo-kiosk.service manages tty1)"
}

optimize_boot_performance() {
    log_info "Optimizing boot performance..."

    # Mask NetworkManager-wait-online (saves ~13.5s)
    # This service waits for complete network connection, but Milo doesn't need it
    sudo systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
    sudo systemctl mask NetworkManager-wait-online.service 2>/dev/null || true

    log_success "NetworkManager-wait-online.service masked (saves ~13s at boot)"
}

install_screen_brightness_control() {
    if [[ "$SCREEN_TYPE" == "none" ]]; then
        log_info "No brightness control to install"
        # Still save the "none" type in hardware.json
        sudo tee "$MILO_DATA_DIR/hardware.json" > /dev/null << EOF
{
  "screen": {
    "type": "none"
  }
}
EOF
        sudo chown "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR/hardware.json"
        return
    fi

    log_info "Installing brightness control..."

    case $SCREEN_TYPE in
        "waveshare_7_usb")
            log_info "Installing brightness control for Waveshare 7\" USB..."

            local temp_dir=$(mktemp -d)
            cd "$temp_dir"

            git clone https://github.com/waveshare/RPi-USB-Brightness
            cd RPi-USB-Brightness/64/lite
            sudo chmod +x Raspi_USB_Backlight_nogui
            ./Raspi_USB_Backlight_nogui -b 6

            # Copy utility to accessible location
            sudo cp Raspi_USB_Backlight_nogui /usr/local/bin/milo-brightness-7
            sudo chmod +x /usr/local/bin/milo-brightness-7

            cd ~
            rm -rf "$temp_dir"

            log_success "7\" USB brightness control installed"
            ;;

        "waveshare_8_dsi")
            log_info "Installing brightness control for Waveshare 8\" DSI..."

            local temp_dir=$(mktemp -d)
            cd "$temp_dir"

            wget https://files.waveshare.com/wiki/common/Brightness.zip
            unzip Brightness.zip
            cd Brightness
            sudo chmod +x install.sh
            ./install.sh

            # Test brightness (default value at 100)
            echo 100 | sudo tee /sys/class/backlight/*/brightness > /dev/null 2>&1 || true

            cd ~
            rm -rf "$temp_dir"

            # Create udev rule for backlight permissions
            log_info "Configuring backlight permissions (udev rule)..."
            sudo tee /etc/udev/rules.d/99-backlight.rules > /dev/null << 'EOF'
SUBSYSTEM=="backlight", RUN+="/bin/chmod 0666 /sys/class/backlight/%k/brightness"
EOF

            # Reload udev rules
            sudo udevadm control --reload-rules
            sudo udevadm trigger

            log_success "8\" DSI brightness control installed"
            log_info "Udev rule created for backlight permissions"
            log_info "Use: echo VALUE | sudo tee /sys/class/backlight/*/brightness (VALUE: 0-255)"
            ;;
    esac

    # Save screen type with resolution in hardware.json (new format)
    log_info "Saving screen configuration in $MILO_DATA_DIR/hardware.json..."

    case "$SCREEN_TYPE" in
        "waveshare_7_usb")
            sudo tee "$MILO_DATA_DIR/hardware.json" > /dev/null << 'EOF'
{
  "screen": {
    "waveshare_7_usb": {
      "resolution": "1024x600"
    }
  }
}
EOF
            ;;
        "waveshare_8_dsi")
            sudo tee "$MILO_DATA_DIR/hardware.json" > /dev/null << 'EOF'
{
  "screen": {
    "waveshare_8_dsi": {
      "resolution": "1280x800"
    }
  }
}
EOF
            ;;
        "none"|*)
            sudo tee "$MILO_DATA_DIR/hardware.json" > /dev/null << 'EOF'
{
  "screen": {
    "none": {}
  }
}
EOF
            ;;
    esac

    sudo chown "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR/hardware.json"
    log_success "Screen configuration saved: $SCREEN_TYPE"
}

enable_services() {
   log_info "Enabling automatic service startup..."

   sudo systemctl daemon-reload

   # Configure graphical.target as default target
   # Necessary for milo-kiosk.service to start (WantedBy=graphical.target)
   # On Raspberry Pi OS Lite, the system boots to multi-user.target by default
   local current_target=$(systemctl get-default)
   if [[ "$current_target" != "graphical.target" ]]; then
       log_info "Configuring system to boot to graphical.target (required for milo-kiosk)..."
       sudo systemctl set-default graphical.target
       log_success "Default target configured: graphical.target"
   else
       log_info "Default target already configured: graphical.target"
   fi

   # Services that should be enabled at boot
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

   # Note: The following services are managed dynamically by the Milo backend:
   # - milo-go-librespot.service
   # - milo-roc.service
   # - milo-radio.service
   # - milo-snapserver-multiroom.service
   # - milo-snapclient-multiroom.service
   # These services should NOT be "enabled" at boot

   log_success "Automatic startup configured"
}

start_services() {
   log_info "Starting services..."

   # Start only enabled services
   sudo systemctl start milo-backend.service
   sudo systemctl start milo-readiness.service
   sudo systemctl start milo-bluealsa.service
   sudo systemctl start milo-bluealsa-aplay.service
   sudo systemctl start milo-disable-wifi-power-management.service
   sudo systemctl start avahi-daemon
   sudo systemctl start nginx

   # Note: milo-frontend.service is no longer used (nginx serves /dist directly)

   # Note: Audio services (go-librespot, roc, radio, snapcast)
   # will be started automatically by the Milo backend as needed

   log_success "Services started"
}

finalize_installation() {
   log_info "Finalizing installation..."
   
   echo ""
   echo -e "${GREEN}=================================${NC}"
   echo -e "${GREEN}   Milo Installation Complete!${NC}"
   echo -e "${GREEN}=================================${NC}"
   echo ""
   echo -e "${BLUE}Configuration:${NC}"
   echo "  • Hostname: milo"
   echo "  • User: $MILO_USER"
   echo "  • Application: $MILO_APP_DIR"
   echo "  • Data: $MILO_DATA_DIR"
   if [[ -n "$HIFIBERRY_OVERLAY" ]]; then
       echo "  • Audio card: $HIFIBERRY_OVERLAY"
   fi
   if [[ "$SCREEN_TYPE" != "none" ]]; then
       case $SCREEN_TYPE in
           "waveshare_7_usb") echo "  • Screen: Waveshare 7\" USB 1024x600" ;;
           "waveshare_8_dsi") echo "  • Screen: Waveshare 8\" DSI 1280x800" ;;
       esac
   fi
   echo ""
   echo -e "${BLUE}Access:${NC}"
   echo "  • Web interface: http://milo.local"
   echo "  • Spotify Connect: 'Milō'"
   echo "  • Bluetooth: 'Milō · Bluetooth'"
   echo ""
   
   if [[ "$REBOOT_REQUIRED" == "true" ]]; then
       echo -e "${YELLOW}⚠️  REBOOT REQUIRED${NC}"
       echo ""
       
       case $USER_RESTART_CHOICE in
           "yes")
               log_info "Automatic reboot in 5 seconds..."
               sleep 5
               sudo reboot
               ;;
           "no")
               echo -e "${YELLOW}Remember to reboot manually with: sudo reboot${NC}"
               ;;
       esac
   else
       start_services
       
       echo ""
       echo -e "${GREEN}✅ Milo is ready! Access it at http://milo.local${NC}"
   fi
}

uninstall_milo() {
   log_warning "Starting Milo uninstallation..."
   echo ""
   read -p "Are you sure you want to uninstall Milo? (y/N): " confirm
   case $confirm in
       [Yy]* )
           ;;
       * )
           log_info "Uninstallation cancelled"
           exit 0
           ;;
   esac
   
   log_info "Stopping services..."
   sudo systemctl stop milo-*.service || true
   sudo systemctl disable milo-*.service || true
   
   log_info "Removing systemd services..."
   sudo rm -f /etc/systemd/system/milo-*.service
   sudo systemctl daemon-reload
   
   log_info "Removing configurations..."
   sudo rm -f /etc/nginx/sites-enabled/milo
   sudo rm -f /etc/nginx/sites-available/milo
   sudo rm -f /etc/snapserver.conf
   sudo rm -f /etc/asound.conf
   sudo rm -f /etc/modules-load.d/snd-aloop.conf
   sudo rm -f /etc/modprobe.d/snd-aloop.conf
   
   log_info "Removing application..."
   sudo rm -rf "$MILO_APP_DIR"
   sudo rm -rf "$MILO_DATA_DIR"

   log_info "Removing Milo themes..."
   sudo rm -rf /usr/share/icons/Milo
   sudo rm -rf /usr/share/plymouth/themes/milo

   log_info "Removing binaries..."
   sudo rm -f /usr/local/bin/go-librespot
   sudo rm -f /usr/local/bin/milo-brightness-7
   
   log_info "Cleaning up packages..."
   sudo apt autoremove -y
   
   read -p "Restore default hostname 'raspberrypi'? (y/N): " restore_hostname
   case $restore_hostname in
       [Yy]* )
           configure_hostname "raspberrypi"
           log_info "Hostname restored"
           ;;
   esac
   
   log_info "Restarting system services..."
   sudo systemctl restart nginx avahi-daemon || true
   
   log_success "Uninstallation complete!"
   echo ""
   log_warning "Note: User '$MILO_USER' was not removed"
   log_warning "Note: Modifications to /boot/firmware/config.txt are preserved"
   echo ""
   read -p "Reboot now? (y/N): " restart_now
   case $restart_now in
       [Yy]* )
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
   
   log_info "Starting Milo Audio System installation"
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