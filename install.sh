#!/bin/bash
# Milo Audio System - Installation Script v2.0
#
# Fully non-interactive: installs all dependencies for all hardware combinations.
# Hardware configuration (audio card, screen) is done via the web UI setup wizard
# after first boot at http://milo.local.
#
# IMPORTANT: This script is optimized for Raspberry Pi OS Lite (64-bit)
# Download Raspberry Pi OS Lite from: https://www.raspberrypi.com/software/operating-systems/

set -e

MILO_USER="milo"
MILO_HOME="/home/$MILO_USER"
MILO_APP_DIR="$MILO_HOME/milo"
MILO_DATA_DIR="/var/lib/milo"
MILO_REPO="https://github.com/leodurandfr/Milo.git"
MILO_BRANCH="main"
REQUIRED_HOSTNAME="milo"
REBOOT_REQUIRED=false

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
    echo "Audio System Installation Script v2.0"
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

setup_hostname() {
    local current_hostname=$(hostname)

    if [ "$current_hostname" != "$REQUIRED_HOSTNAME" ]; then
        log_info "Configuring hostname '$REQUIRED_HOSTNAME'..."
        configure_hostname "$REQUIRED_HOSTNAME"
        log_success "Hostname configured"
        REBOOT_REQUIRED=true
    else
        log_success "Hostname '$REQUIRED_HOSTNAME' already configured"
    fi
}

configure_hostname() {
    local new_hostname="$1"
    echo "$new_hostname" | sudo tee /etc/hostname > /dev/null
    sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
    sudo hostnamectl set-hostname "$new_hostname"
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
    
    # sudo -u "$MILO_USER" git clone "$MILO_REPO" "$MILO_APP_DIR"
    sudo -u "$MILO_USER" git clone --branch "$MILO_BRANCH" --single-branch "$MILO_REPO" "$MILO_APP_DIR"
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
    
    wget https://github.com/devgianlu/go-librespot/releases/download/v0.6.1/go-librespot_linux_arm64.tar.gz
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
  address: localhost
  port: 3678
  allow_origin: "*"
  image_size: 'xlarge'
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

    # Use --disable-systemd because we manage our own systemd services
    # SBC codec is built-in and sufficient for Bluetooth audio
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

    # Set Bluetooth device name via machine-info (BlueZ recommended approach)
    sudo cp "$MILO_APP_DIR/rootfs/etc/machine-info" /etc/machine-info

    log_success "bluez-alsa installed"
}

install_airplay() {
    log_info "Installing AirPlay 2 (shairport-sync + NQPTP)..."

    source "$MILO_APP_DIR/install/airplay.sh"

    install_nqptp
    install_shairport_sync
    configure_shairport_sync

    log_success "AirPlay 2 installed"
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

    # Track installation success
    local github_install_success=false

    # Method 1: Try GitHub .deb packages first (to get latest version)
    log_info "Attempting installation from GitHub (latest version)..."

    local temp_dir=$(mktemp -d)
    cd "$temp_dir"

    # Download with detected Debian version
    log_info "Downloading Snapcast v0.35.0 for $DEBIAN_VERSION..."
    if wget "https://github.com/snapcast/snapcast/releases/download/v0.35.0/snapserver_0.35.0-1_arm64_${DEBIAN_VERSION}.deb" 2>/dev/null && \
       wget "https://github.com/snapcast/snapcast/releases/download/v0.35.0/snapclient_0.35.0-1_arm64_${DEBIAN_VERSION}.deb" 2>/dev/null; then

        # Install common dependencies before .deb files
        log_info "Installing dependencies..."
        sudo apt install -y libavahi-client3 libavahi-common3 libflac12t64 || sudo apt install -y libflac12 || true

        # Install .deb files
        if sudo apt install -y ./snapserver_0.35.0-1_arm64_${DEBIAN_VERSION}.deb ./snapclient_0.35.0-1_arm64_${DEBIAN_VERSION}.deb; then
            log_success "Snapcast installed from GitHub packages"
            github_install_success=true
        else
            log_warning "Failed to install .deb packages, trying with dependency fix..."
            sudo apt --fix-broken install -y || true

            if sudo dpkg -i snapserver_0.35.0-1_arm64_${DEBIAN_VERSION}.deb snapclient_0.35.0-1_arm64_${DEBIAN_VERSION}.deb 2>/dev/null; then
                sudo apt --fix-broken install -y
                log_success "Snapcast installed from GitHub after fixing dependencies"
                github_install_success=true
            fi
        fi
    else
        # Try bookworm fallback for download
        log_warning "Package for $DEBIAN_VERSION not available, trying with bookworm..."
        DEBIAN_VERSION="bookworm"

        if wget "https://github.com/snapcast/snapcast/releases/download/v0.35.0/snapserver_0.35.0-1_arm64_bookworm.deb" 2>/dev/null && \
           wget "https://github.com/snapcast/snapcast/releases/download/v0.35.0/snapclient_0.35.0-1_arm64_bookworm.deb" 2>/dev/null; then

            log_info "Installing dependencies..."
            sudo apt install -y libavahi-client3 libavahi-common3 libflac12t64 || sudo apt install -y libflac12 || true

            if sudo apt install -y ./snapserver_0.35.0-1_arm64_bookworm.deb ./snapclient_0.35.0-1_arm64_bookworm.deb; then
                log_success "Snapcast installed from GitHub packages (bookworm fallback)"
                github_install_success=true
            else
                sudo apt --fix-broken install -y || true
                if sudo dpkg -i snapserver_0.35.0-1_arm64_bookworm.deb snapclient_0.35.0-1_arm64_bookworm.deb 2>/dev/null; then
                    sudo apt --fix-broken install -y
                    log_success "Snapcast installed from GitHub after fixing dependencies"
                    github_install_success=true
                fi
            fi
        fi
    fi

    # Cleanup temp directory
    cd ~
    rm -rf "$temp_dir"

    # Method 2: Fall back to apt if GitHub method failed
    if [[ "$github_install_success" != "true" ]]; then
        log_warning "GitHub installation failed, falling back to Debian repositories..."
        if sudo apt install -y snapserver snapclient; then
            log_success "Snapcast installed from Debian repositories"
        else
            log_error "Unable to install Snapcast from any source"
            return 1
        fi
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

install_udev_rules() {
    log_info "Installing udev rules for screen brightness control..."

    # Copy udev rules from rootfs
    sudo cp "$MILO_APP_DIR/rootfs/etc/udev/rules.d/99-milo-screen.rules" /etc/udev/rules.d/99-milo-screen.rules
    sudo chmod 0644 /etc/udev/rules.d/99-milo-screen.rules

    # Reload udev rules
    sudo udevadm control --reload-rules
    sudo udevadm trigger

    # Apply permissions immediately for existing devices
    sudo chmod 0666 /dev/hidraw* 2>/dev/null || true
    sudo chmod 0666 /sys/class/backlight/*/brightness 2>/dev/null || true

    log_success "Udev rules installed (screen brightness without sudo)"
}

install_readiness_script() {
    log_info "Installing readiness script..."

    # Copy readiness script to /usr/local/bin/
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-wait-ready.sh" /usr/local/bin/milo-wait-ready.sh
    sudo chmod +x /usr/local/bin/milo-wait-ready.sh

    log_success "Readiness script installed in /usr/local/bin/"
}

install_apply_hardware_script() {
    log_info "Installing system scripts..."

    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-apply-hardware" /usr/local/bin/milo-apply-hardware
    sudo chmod +x /usr/local/bin/milo-apply-hardware

    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-deploy-update" /usr/local/bin/milo-deploy-update
    sudo chmod +x /usr/local/bin/milo-deploy-update

    # Remove legacy sudoers file if present
    sudo rm -f /etc/sudoers.d/milo-hardware

    # Consolidated sudoers for all backend sudo operations
    sudo tee /etc/sudoers.d/milo-backend > /dev/null << 'EOF'
# System control (used by SystemdServiceManager and api/system.py)
milo ALL=(root) NOPASSWD: /usr/bin/systemctl
milo ALL=(root) NOPASSWD: /usr/bin/hostnamectl
milo ALL=(root) NOPASSWD: /usr/sbin/reboot
milo ALL=(root) NOPASSWD: /usr/sbin/poweroff
# Hardware configuration
milo ALL=(root) NOPASSWD: /usr/local/bin/milo-apply-hardware
# Update deployment (file ops, packages, udev — all via secure wrapper)
milo ALL=(root) NOPASSWD: /usr/local/bin/milo-deploy-update
EOF
    sudo visudo -c -f /etc/sudoers.d/milo-backend || { echo "FATAL: sudoers syntax error"; exit 1; }
    sudo chmod 0440 /etc/sudoers.d/milo-backend

    log_success "Hardware apply script and sudoers installed"
}

install_polkit_rules() {
    log_info "Installing PolicyKit rules for NetworkManager..."

    sudo mkdir -p /etc/polkit-1/rules.d
    sudo cp "$MILO_APP_DIR/rootfs/etc/polkit-1/rules.d/50-milo-networkmanager.rules" \
        /etc/polkit-1/rules.d/50-milo-networkmanager.rules
    sudo chmod 0644 /etc/polkit-1/rules.d/50-milo-networkmanager.rules

    log_success "PolicyKit rules installed"
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

install_camilladsp() {
    log_info "Installing CamillaDSP..."

    local temp_dir=$(mktemp -d)
    cd "$temp_dir"

    # Download CamillaDSP binary for ARM64
    log_info "Downloading CamillaDSP v3.0.1..."
    wget -q https://github.com/HEnquist/camilladsp/releases/download/v3.0.1/camilladsp-linux-aarch64.tar.gz
    tar -xzf camilladsp-linux-aarch64.tar.gz

    # Install binary
    sudo cp camilladsp /usr/local/bin/
    sudo chmod +x /usr/local/bin/camilladsp

    # Create configuration directory
    sudo mkdir -p "$MILO_DATA_DIR/camilladsp"
    sudo mkdir -p "$MILO_DATA_DIR/camilladsp/configs"
    sudo mkdir -p "$MILO_DATA_DIR/camilladsp/coeffs"

    # Copy default CamillaDSP configuration from rootfs
    log_info "Installing CamillaDSP configuration..."
    sudo cp "$MILO_APP_DIR/rootfs/var/lib/milo/camilladsp/config.yml" "$MILO_DATA_DIR/camilladsp/config.yml"

    sudo chown -R "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR/camilladsp"

    # Verify installation
    /usr/local/bin/camilladsp --version

    cd ~
    rm -rf "$temp_dir"

    log_success "CamillaDSP installed"
}

configure_alsa_complete() {
    log_info "Configuring complete ALSA setup with CamillaDSP..."

    sudo cp "$MILO_APP_DIR/rootfs/etc/asound.conf" /etc/asound.conf

    sudo tee /var/lib/milo/routing.env > /dev/null << 'EOF'
MILO_MODE=direct
EOF

    sudo chown "$MILO_USER:$MILO_USER" /var/lib/milo/routing.env

    log_success "Complete ALSA configuration done"
}

configure_snapserver() {
    log_info "Configuring Snapserver..."
    
    sudo tee /etc/snapserver.conf > /dev/null << 'EOF'

[stream]
default_source = Multiroom

buffer = 250
codec = flac
chunk_ms = 15
sampleformat = 48000:32:2

source = meta:///Bluetooth/ROC/Spotify/Radio/Podcast/AirPlay?name=Multiroom

source = alsa:///?name=Bluetooth&device=hw:1,1,0&idle_threshold=5000&send_silence=true
source = alsa:///?name=ROC&device=hw:1,1,1&idle_threshold=5000&send_silence=true
source = alsa:///?name=Spotify&device=hw:1,1,2&idle_threshold=5000&send_silence=true
source = alsa:///?name=Radio&device=hw:1,1,3&idle_threshold=5000&send_silence=true
source = alsa:///?name=Podcast&device=hw:1,1,4&idle_threshold=5000&send_silence=true
source = alsa:///?name=AirPlay&device=hw:1,1,6&idle_threshold=5000&send_silence=true

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

    # Copy Avahi config (eth0 default, no deny-interfaces needed)
    log_info "Installing Avahi config (eth0 default)..."
    sudo cp "$MILO_APP_DIR/rootfs/etc/avahi/avahi-daemon.conf" /etc/avahi/avahi-daemon.conf

    # Install systemd override to reset Avahi config to eth0 on every boot
    # Prevents stale wlan0 config from causing mDNS conflicts (milo -> milo-2)
    log_info "Installing Avahi boot reset override..."
    sudo mkdir -p /etc/systemd/system/avahi-daemon.service.d
    sudo cp "$MILO_APP_DIR/system/avahi-daemon-override.conf" \
        /etc/systemd/system/avahi-daemon.service.d/milo-override.conf
    sudo systemctl daemon-reload

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

    # Install unified NetworkManager dispatcher for WiFi/Ethernet priority and Avahi
    log_info "Installing network dispatcher..."
    sudo cp "$MILO_APP_DIR/rootfs/etc/NetworkManager/dispatcher.d/90-milo-network" /etc/NetworkManager/dispatcher.d/
    sudo chmod 755 /etc/NetworkManager/dispatcher.d/90-milo-network

    # Remove legacy dispatchers from older installations
    sudo rm -f /etc/NetworkManager/dispatcher.d/98-wifi-eth0-priority
    sudo rm -f /etc/NetworkManager/dispatcher.d/99-avahi-interface

    # Install dnsmasq config for captive portal DNS redirect (hotspot mode)
    sudo mkdir -p /etc/NetworkManager/dnsmasq-shared.d
    sudo cp "$MILO_APP_DIR/rootfs/etc/NetworkManager/dnsmasq-shared.d/milo-captive.conf" /etc/NetworkManager/dnsmasq-shared.d/

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

    # Allow file uploads up to 10MB (images are limited to 5MB by the backend)
    client_max_body_size 10M;

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

    # Copy Cage launch script from rootfs/
    if [[ ! -f "$MILO_APP_DIR/rootfs/home/milo/.config/milo-cage-start.sh" ]]; then
        log_error "File milo-cage-start.sh not found in $MILO_APP_DIR/rootfs/home/milo/.config/"
        return 1
    fi

    sudo cp "$MILO_APP_DIR/rootfs/home/milo/.config/milo-cage-start.sh" "$MILO_HOME/.config/milo-cage-start.sh"
    sudo chmod +x "$MILO_HOME/.config/milo-cage-start.sh"
    sudo chown "$MILO_USER:$MILO_USER" "$MILO_HOME/.config/milo-cage-start.sh"

    # Copy .bash_profile from rootfs/
    if [[ ! -f "$MILO_APP_DIR/rootfs/home/milo/.bash_profile" ]]; then
        log_error "File .bash_profile not found in $MILO_APP_DIR/rootfs/home/milo/"
        return 1
    fi

    sudo cp "$MILO_APP_DIR/rootfs/home/milo/.bash_profile" "$MILO_HOME/.bash_profile"
    sudo chown "$MILO_USER:$MILO_USER" "$MILO_HOME/.bash_profile"

    log_success "Kiosk mode configured with Cage (scripts copied from rootfs/)"
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

configure_boot_display() {
    log_info "Configuring boot display (no screen selected yet)..."

    # Use common boot config — screen-specific config applied later by milo-apply-hardware
    source "$MILO_APP_DIR/install/boot-common.sh"

    # Configure cmdline.txt
    configure_cmdline "$BOOT_PARAMS_COMMON $BOOT_PARAMS_SCREEN"

    # Configure config.txt
    configure_config "$CONFIG_PARAMS_COMMON" "$CONFIG_PARAMS_SCREEN"

    log_success "Boot display configured"
}

configure_cmdline() {
    local boot_params="$1"
    local cmdline_file="/boot/firmware/cmdline.txt"
    [[ ! -f "$cmdline_file" ]] && cmdline_file="/boot/cmdline.txt"

    if [[ ! -f "$cmdline_file" ]]; then
        log_error "cmdline.txt not found"
        return 1
    fi

    sudo cp "$cmdline_file" "${cmdline_file}.milo-backup" 2>/dev/null || true

    # Clean current cmdline (remove parameters we will set)
    local current_cmdline=$(cat "$cmdline_file")
    current_cmdline=$(echo "$current_cmdline" | sed -E '
        s/console=serial[0-9],[0-9]+//g
        s/console=tty[0-9]//g
        s/loglevel=[0-9]+//g
        s/\bquiet\b//g
        s/\bsplash\b//g
        s/plymouth\.[^ ]*//g
        s/logo\.[^ ]*//g
        s/vt\.[^ ]*//g
        s/fbcon=[^ ]*//g
        s/video=[^ ]*//g
        s/cfg80211\.[^ ]*//g
        s/  +/ /g
    ' | xargs)

    echo "${current_cmdline} ${boot_params}" | tr -s ' ' | sudo tee "$cmdline_file" > /dev/null
    log_success "cmdline.txt configured"
}

configure_config() {
    local common_params="$1"
    local screen_params="$2"

    local config_file="/boot/firmware/config.txt"
    [[ ! -f "$config_file" ]] && config_file="/boot/config.txt"
    [[ ! -f "$config_file" ]] && return 0

    # Add common params (disable_splash=1)
    if ! grep -q "disable_splash=1" "$config_file"; then
        sudo sed -i '/^\[all\]$/a\\n# Milo - Silent boot\ndisable_splash=1' "$config_file"
    fi

    # Add screen-specific params
    if [[ -n "$screen_params" ]]; then
        echo "$screen_params" | while read -r param; do
            [[ -z "$param" ]] && continue
            if ! grep -q "$param" "$config_file"; then
                sudo sed -i "/disable_splash=1/a\\$param" "$config_file"
            fi
        done
    fi

    log_success "config.txt configured"
}

configure_plymouth_splash() {
    log_info "Configuring boot splash screen with Milo theme..."

    # Install Plymouth
    sudo apt install -y plymouth plymouth-themes

    # Create Milo theme directory
    sudo mkdir -p /usr/share/plymouth/themes/milo

    # Copy all Plymouth theme files from rootfs/
    log_info "Installing Plymouth theme files..."
    if [[ -d "$MILO_APP_DIR/rootfs/usr/share/plymouth/themes/milo" ]]; then
        for theme_file in "$MILO_APP_DIR/rootfs/usr/share/plymouth/themes/milo"/*; do
            if [[ -f "$theme_file" ]]; then
                local filename=$(basename "$theme_file")
                sudo cp "$theme_file" /usr/share/plymouth/themes/milo/
                log_success "Installed Plymouth: $filename"
            fi
        done
    else
        log_error "Plymouth theme directory not found: $MILO_APP_DIR/rootfs/usr/share/plymouth/themes/milo/"
        return 1
    fi

    # Set Milo theme as default
    sudo plymouth-set-default-theme milo

    # Update initramfs to apply theme
    sudo update-initramfs -u

    # Configure boot display (cmdline.txt + config.txt) based on screen type
    configure_boot_display

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
    log_info "Installing brightness control for all screen types..."

    # Waveshare 7" USB brightness control
    log_info "Installing brightness control for Waveshare 7\" USB..."
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-brightness-7" /usr/local/bin/milo-brightness-7
    sudo chmod +x /usr/local/bin/milo-brightness-7
    log_success "7\" USB brightness control installed"

    # Waveshare 8" DSI brightness control
    log_info "Installing brightness control for Waveshare 8\" DSI..."

    local temp_dir=$(mktemp -d)
    cd "$temp_dir"

    wget https://files.waveshare.com/wiki/common/Brightness.zip
    unzip Brightness.zip
    cd Brightness
    sudo chmod +x install.sh
    ./install.sh

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

    log_success "All screen brightness controls installed"
}

save_hardware_config() {
    log_info "Saving default hardware configuration to $MILO_DATA_DIR/hardware.json..."

    sudo tee "$MILO_DATA_DIR/hardware.json" > /dev/null << 'EOF'
{
  "screen": {
    "type": "none",
    "resolution": null
  },
  "audio": {
    "id": "none"
  },
  "rotary_encoder": {
    "clk_pin": 22,
    "dt_pin": 27,
    "sw_pin": 23
  }
}
EOF

    sudo chown "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR/hardware.json"
    log_success "Default hardware configuration saved (configure via setup wizard)"
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
   sudo systemctl enable milo-camilladsp.service
   sudo systemctl enable avahi-daemon
   sudo systemctl enable nginx

   # Note: milo-frontend.service is no longer used (nginx serves /dist directly)
   # Note: getty@tty1 is masked (milo-kiosk.service takes control of tty1)

   # Note: The following services are managed dynamically by the Milo backend:
   # - milo-spotify.service
   # - milo-mac.service
   # - milo-radio.service
   # - milo-airplay.service
   # - milo-snapserver-multiroom.service
   # - milo-snapclient-multiroom.service
   # These services should NOT be "enabled" at boot

   log_success "Automatic startup configured"
}

finalize_installation() {
   log_info "Finalizing installation..."

   echo ""
   echo -e "${GREEN}=================================${NC}"
   echo -e "${GREEN}   Milo Installation Complete!   ${NC}"
   echo -e "${GREEN}=================================${NC}"
   echo ""
   echo -e "  ${BLUE}Next steps:${NC}"
   echo "    1. Open http://milo.local"
   echo "    2. Follow the setup wizard to configure your hardware"
   echo ""

   log_info "Rebooting in 5 seconds..."
   sleep 5
   sudo reboot
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
   sudo rm -f /etc/shairport-sync.conf
   sudo rm -f /etc/dbus-1/system.d/shairport-sync-dbus.conf
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

   log_info "Starting Milo Audio System installation (fully non-interactive)"
   echo ""

   check_system

   install_dependencies
   setup_hostname

   create_milo_user
   install_milo_application
   fix_nginx_permissions
   suppress_pulseaudio

   install_go_librespot
   install_roc_toolkit
   install_bluez_alsa
   install_airplay
   install_snapcast

   install_readiness_script
   install_apply_hardware_script
   install_polkit_rules
   create_systemd_services
   configure_journald
   install_udev_rules

   configure_alsa_loopback
   install_camilladsp
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
   save_hardware_config

   enable_services
   finalize_installation
}

main "$@"