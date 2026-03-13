#!/bin/bash
# Milo Client - Installation Script v2.0 (non-interactive)
#
# Usage:
#   install-client.sh                                # No audio card, configure later via web UI
#   install-client.sh --audio hifiberry_amp2         # Pre-configure audio card
#   install-client.sh --server 192.168.1.10          # Manual server IP (if mDNS fails)
#   install-client.sh --help                         # Show usage and available audio cards
#   install-client.sh --uninstall                    # Remove Milo Client

set -e

MILO_CLIENT_USER="milo-client"
MILO_CLIENT_HOME="/home/$MILO_CLIENT_USER"
MILO_CLIENT_REPO_DIR="$MILO_CLIENT_HOME/repo"
MILO_CLIENT_APP_DIR="$MILO_CLIENT_REPO_DIR/milo-client/app"
MILO_CLIENT_SYSTEM_DIR="$MILO_CLIENT_REPO_DIR/milo-client/system"
MILO_CLIENT_ROOTFS_DIR="$MILO_CLIENT_REPO_DIR/milo-client/rootfs"
MILO_CLIENT_VENV_DIR="$MILO_CLIENT_HOME/venv"
MILO_CLIENT_DATA_DIR="/var/lib/milo-client"
MILO_CLIENT_REPO_URL="https://github.com/leodurandfr/Milo.git"

# Audio card lookup table (must match AUDIO_CARDS in backend/hardware/registry.py)
# Format: "id|overlay|alsa_control|label"
AUDIO_CARD_TABLE=(
    "hifiberry_amp2|hifiberry-dacplus-std|Digital|HiFiBerry Amp2"
    "hifiberry_amp4|hifiberry-dacplus-std|Digital|HiFiBerry Amp4"
    "hifiberry_amp4pro|hifiberry-amp4pro|Digital|HiFiBerry Amp4 Pro"
    "hifiberry_amp100|hifiberry-amp100|Digital|HiFiBerry Amp100"
    "hifiberry_beocreate|hifiberry-dac|DAC|HiFiBerry Beocreate 4CA"
    "hifiberry_dac2hd|hifiberry-dacplushd|DAC|HiFiBerry DAC2 HD"
    "hifiberry_dacplus_pro|hifiberry-dacplus|Digital|HiFiBerry DAC+ Pro"
)

# CLI arguments
ARG_AUDIO_ID=""
ARG_SERVER_IP=""
AUDIO_OVERLAY=""
AUDIO_LABEL=""
ALSA_CONTROL=""
MILO_PRINCIPAL_IP=""

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
    echo "  __  __ _ _         ____ _ _            _   "
    echo " |  \/  (_) | ___   / ___| (_) ___ _ __ | |_ "
    echo " | |\/| | | |/ _ \ | |   | | |/ _ \ '_ \| __|"
    echo " | |  | | | | (_) || |___| | |  __/ | | | |_ "
    echo " |_|  |_|_|_|\___/  \____|_|_|\___|_| |_|\__|"
    echo ""
    echo "Client Installation Script v2.0"
    echo -e "${NC}"
}

show_help() {
    echo "Usage: install-client.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --audio <card_key>   Pre-configure audio card (see list below)"
    echo "  --server <ip>        Main Milo IP address (if mDNS auto-discovery fails)"
    echo "  --uninstall          Remove Milo Client"
    echo "  --help               Show this help message"
    echo ""
    echo "Available audio cards:"
    for entry in "${AUDIO_CARD_TABLE[@]}"; do
        local id="${entry%%|*}"
        local label="${entry##*|}"
        printf "  %-25s %s\n" "$id" "$label"
    done
    echo ""
    echo "Examples:"
    echo "  install-client.sh                                # Configure audio later via web UI"
    echo "  install-client.sh --audio hifiberry_amp2         # Pre-configure HiFiBerry Amp2"
    echo "  install-client.sh --server 192.168.1.10          # Specify main Milo IP"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --audio)
                if [[ -z "${2:-}" ]]; then
                    log_error "--audio requires a card key argument"
                    echo ""
                    show_help
                    exit 1
                fi
                ARG_AUDIO_ID="$2"
                shift 2
                ;;
            --server)
                if [[ -z "${2:-}" ]]; then
                    log_error "--server requires an IP address argument"
                    exit 1
                fi
                ARG_SERVER_IP="$2"
                shift 2
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown argument: $1"
                echo ""
                show_help
                exit 1
                ;;
        esac
    done

    # Validate --audio if provided
    if [[ -n "$ARG_AUDIO_ID" ]]; then
        local found=false
        for entry in "${AUDIO_CARD_TABLE[@]}"; do
            local id="${entry%%|*}"
            if [[ "$id" == "$ARG_AUDIO_ID" ]]; then
                # Parse: id|overlay|alsa_control|label
                IFS='|' read -r _ AUDIO_OVERLAY ALSA_CONTROL AUDIO_LABEL <<< "$entry"
                found=true
                break
            fi
        done

        if [[ "$found" != "true" ]]; then
            log_error "Unknown audio card: $ARG_AUDIO_ID"
            echo ""
            echo "Available audio cards:"
            for entry in "${AUDIO_CARD_TABLE[@]}"; do
                local id="${entry%%|*}"
                local label="${entry##*|}"
                printf "  %-25s %s\n" "$id" "$label"
            done
            exit 1
        fi

        log_success "Audio card: $AUDIO_LABEL ($ARG_AUDIO_ID)"
    else
        log_info "No audio card specified (configure later via web UI)"
    fi
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
        log_error "Unsupported architecture: $ARCH. Raspberry Pi OS 64-bit required."
        exit 1
    fi

    log_success "Compatible system detected"
}

discover_milo_principal() {
    # Use --server if provided
    if [[ -n "$ARG_SERVER_IP" ]]; then
        MILO_PRINCIPAL_IP="$ARG_SERVER_IP"
        log_success "Main Milo server: $MILO_PRINCIPAL_IP (from --server)"
        return 0
    fi

    log_info "Searching for main Milo on the network..."

    if MILO_PRINCIPAL_IP=$(getent hosts milo.local 2>/dev/null | awk '{print $1}' | head -1) && [[ -n "$MILO_PRINCIPAL_IP" ]]; then
        log_success "Main Milo found at: $MILO_PRINCIPAL_IP (milo.local)"
        return 0
    fi

    log_error "Unable to find main Milo on the network."
    log_error "Make sure the main Milo is running, or use --server <ip> to specify its IP address."
    exit 1
}

setup_hostname() {
    local new_hostname="milo-client"
    local current_hostname=$(hostname)

    if [ "$current_hostname" != "$new_hostname" ]; then
        log_info "Configuring hostname '$new_hostname'..."
        echo "$new_hostname" | sudo tee /etc/hostname > /dev/null
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
        sudo hostnamectl set-hostname "$new_hostname"
        log_success "Hostname configured"
    else
        log_success "Hostname '$new_hostname' already configured"
    fi
}

save_hardware_config() {
    log_info "Saving hardware configuration..."

    sudo mkdir -p "$MILO_CLIENT_DATA_DIR"

    if [[ -n "$ARG_AUDIO_ID" ]]; then
        sudo tee "$MILO_CLIENT_DATA_DIR/hardware.json" > /dev/null << EOF
{
  "audio": {
    "id": "$ARG_AUDIO_ID",
    "overlay": "$AUDIO_OVERLAY"
  }
}
EOF
        log_success "Hardware config saved (audio: $AUDIO_LABEL)"
    else
        sudo tee "$MILO_CLIENT_DATA_DIR/hardware.json" > /dev/null << 'EOF'
{
  "audio": {
    "id": "none"
  }
}
EOF
        log_success "Hardware config saved (audio: none)"
    fi

    sudo chown "$MILO_CLIENT_USER:audio" "$MILO_CLIENT_DATA_DIR/hardware.json"
}

install_apply_hardware_script() {
    log_info "Installing hardware apply script..."

    sudo cp "$MILO_CLIENT_ROOTFS_DIR/usr/local/bin/milo-client-apply-hardware" /usr/local/bin/
    sudo chmod +x /usr/local/bin/milo-client-apply-hardware
    sudo chown root:root /usr/local/bin/milo-client-apply-hardware

    log_success "Hardware apply script installed"
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

    log_info "Installing minimal dependencies..."
    sudo apt install -y \
        git \
        python3-pip \
        python3-venv \
        python3-dev \
        libasound2-dev \
        avahi-daemon \
        avahi-utils

    sudo rm -f /etc/apt/apt.conf.d/local

    log_success "Dependencies installed"
}

suppress_pulseaudio() {
    log_info "Removing PulseAudio/PipeWire..."
    sudo apt remove -y pulseaudio pipewire || true
    sudo apt autoremove -y
    log_success "PulseAudio/PipeWire removed"
}

configure_journald() {
    log_info "Configuring journald limits..."

    sudo sed -i 's/^#RuntimeMaxUse=$/RuntimeMaxUse=100M/' /etc/systemd/journald.conf
    sudo sed -i 's/^#MaxRetentionSec=$/MaxRetentionSec=7d/' /etc/systemd/journald.conf

    log_success "Journald configured (100MB max, 7 days retention)"
}

create_milo_client_user() {
    if id "$MILO_CLIENT_USER" &>/dev/null; then
        log_info "User '$MILO_CLIENT_USER' already exists"
    else
        log_info "Creating user '$MILO_CLIENT_USER'..."
        sudo useradd -m -s /bin/bash -G audio,sudo "$MILO_CLIENT_USER"
        log_success "User '$MILO_CLIENT_USER' created"
    fi

    sudo mkdir -p "$MILO_CLIENT_DATA_DIR"
    sudo chown -R "$MILO_CLIENT_USER:audio" "$MILO_CLIENT_DATA_DIR"
}

install_camilladsp() {
    log_info "Installing CamillaDSP..."

    local temp_dir=$(mktemp -d)
    cd "$temp_dir"

    # Download CamillaDSP binary for ARM64
    log_info "Downloading CamillaDSP v3.0.1..."
    wget -q https://github.com/HEnquist/camilladsp/releases/download/v3.0.1/camilladsp-linux-aarch64.tar.gz

    tar -xzf camilladsp-linux-aarch64.tar.gz

    sudo cp camilladsp /usr/local/bin/
    sudo chmod +x /usr/local/bin/camilladsp

    # Create CamillaDSP directories
    sudo mkdir -p "$MILO_CLIENT_DATA_DIR/camilladsp"
    sudo mkdir -p "$MILO_CLIENT_DATA_DIR/camilladsp/configs"
    sudo mkdir -p "$MILO_CLIENT_DATA_DIR/camilladsp/coeffs"

    # Copy default CamillaDSP configuration from repo
    sudo cp "$MILO_CLIENT_REPO_DIR/milo-client/configs/camilladsp/config.yml" "$MILO_CLIENT_DATA_DIR/camilladsp/config.yml"

    sudo chown -R "$MILO_CLIENT_USER:$MILO_CLIENT_USER" "$MILO_CLIENT_DATA_DIR/camilladsp"

    # Cleanup
    cd ~
    rm -rf "$temp_dir"

    log_success "CamillaDSP installed"
}

install_snapclient() {
    log_info "Installing Snapclient..."

    # Detect Debian version
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

    log_info "Downloading Snapclient v0.35.0 for $DEBIAN_VERSION..."
    if wget "https://github.com/snapcast/snapcast/releases/download/v0.35.0/snapclient_0.35.0-1_arm64_${DEBIAN_VERSION}.deb" 2>/dev/null; then

        log_info "Installing dependencies..."
        sudo apt install -y libavahi-client3 libavahi-common3 libflac12t64 || sudo apt install -y libflac12 || true

        if sudo apt install -y "./snapclient_0.35.0-1_arm64_${DEBIAN_VERSION}.deb"; then
            log_success "Snapclient installed from GitHub packages"
            github_install_success=true
        else
            log_warning "Failed to install .deb package, trying with dependency fix..."
            sudo apt --fix-broken install -y || true
            if sudo dpkg -i "snapclient_0.35.0-1_arm64_${DEBIAN_VERSION}.deb" 2>/dev/null; then
                sudo apt --fix-broken install -y
                log_success "Snapclient installed from GitHub after fixing dependencies"
                github_install_success=true
            fi
        fi
    else
        # Try bookworm fallback for download
        log_warning "Package for $DEBIAN_VERSION not available, trying with bookworm..."
        DEBIAN_VERSION="bookworm"

        if wget "https://github.com/snapcast/snapcast/releases/download/v0.35.0/snapclient_0.35.0-1_arm64_bookworm.deb" 2>/dev/null; then

            log_info "Installing dependencies..."
            sudo apt install -y libavahi-client3 libavahi-common3 libflac12t64 || sudo apt install -y libflac12 || true

            if sudo apt install -y "./snapclient_0.35.0-1_arm64_bookworm.deb"; then
                log_success "Snapclient installed from GitHub packages (bookworm fallback)"
                github_install_success=true
            else
                sudo apt --fix-broken install -y || true
                if sudo dpkg -i "snapclient_0.35.0-1_arm64_bookworm.deb" 2>/dev/null; then
                    sudo apt --fix-broken install -y
                    log_success "Snapclient installed from GitHub after fixing dependencies"
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
        if sudo apt install -y snapclient; then
            log_success "Snapclient installed from Debian repositories"
        else
            log_error "Unable to install Snapclient from any source"
            return 1
        fi
    fi

    snapclient --version

    sudo systemctl stop snapclient.service || true
    sudo systemctl disable snapclient.service || true

    log_success "Snapclient installed and configured"
}

clone_milo_client_repo() {
    log_info "Cloning Milo repository (sparse checkout)..."

    # If repo already exists, update it instead of cloning
    if [[ -d "$MILO_CLIENT_REPO_DIR/.git" ]]; then
        log_info "Repository already exists, updating..."
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" fetch --depth 1 origin main
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" reset --hard origin/main
        log_success "Repository updated"
    elif [[ -d "$MILO_CLIENT_REPO_DIR" ]]; then
        # Directory exists but not a git repo - remove and clone fresh
        log_warning "Removing incomplete repository directory..."
        sudo rm -rf "$MILO_CLIENT_REPO_DIR"
        sudo -u "$MILO_CLIENT_USER" git clone --no-checkout --depth 1 "$MILO_CLIENT_REPO_URL" "$MILO_CLIENT_REPO_DIR"
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" sparse-checkout init --cone
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" sparse-checkout set milo-client
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" checkout
        log_success "Repository cloned (sparse checkout: milo-client/)"
    else
        # Fresh clone
        sudo -u "$MILO_CLIENT_USER" git clone --no-checkout --depth 1 "$MILO_CLIENT_REPO_URL" "$MILO_CLIENT_REPO_DIR"
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" sparse-checkout init --cone
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" sparse-checkout set milo-client
        sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" checkout
        log_success "Repository cloned (sparse checkout: milo-client/)"
    fi

    # Unshallow and fetch tags so git describe can resolve the version
    # (shallow --depth 1 clone cannot trace back to tags)
    if ! sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" fetch --unshallow --tags origin 2>/dev/null; then
        log_warning "Could not fetch full tag history — version will show as a commit hash"
    fi

    # Write initial app version (same format as deploy_update writes)
    local app_version
    app_version=$(sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" describe --tags --always 2>/dev/null || echo "unknown")
    sudo tee "$MILO_CLIENT_DATA_DIR/app-version" > /dev/null <<< "$app_version"
    sudo chown "$MILO_CLIENT_USER:audio" "$MILO_CLIENT_DATA_DIR/app-version"
    log_success "App version recorded: $app_version"
}

install_milo_client_application() {
    log_info "Configuring Python environment for Milo Client..."

    sudo -u "$MILO_CLIENT_USER" python3 -m venv "$MILO_CLIENT_VENV_DIR"
    sudo -u "$MILO_CLIENT_USER" bash -c "source $MILO_CLIENT_VENV_DIR/bin/activate && pip install --upgrade pip"

    # Install packages from piwheels (faster for ARM)
    sudo -u "$MILO_CLIENT_USER" bash -c "source $MILO_CLIENT_VENV_DIR/bin/activate && pip install -r $MILO_CLIENT_APP_DIR/requirements.txt"

    # Install camilladsp from GitHub (not available on PyPI/piwheels)
    log_info "Installing camilladsp from GitHub..."
    sudo -u "$MILO_CLIENT_USER" bash -c "source $MILO_CLIENT_VENV_DIR/bin/activate && pip install git+https://github.com/HEnquist/pycamilladsp.git"

    log_success "Milo Client application installed"
}

configure_alsa_loopback() {
    log_info "Configuring ALSA loopback module for CamillaDSP..."

    # Ensure snd-aloop module loads at boot with subdevices for CamillaDSP
    if ! grep -q "snd-aloop" /etc/modules 2>/dev/null; then
        echo "snd-aloop" | sudo tee -a /etc/modules
    fi

    # Copy loopback module configuration from repo
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/etc/modprobe.d/milo-client-loopback.conf" /etc/modprobe.d/

    # Load module immediately if not loaded (may fail if audio hardware not yet initialized - will load after reboot)
    if ! lsmod | grep -q "snd_aloop"; then
        sudo modprobe snd-aloop pcm_substreams=2 || true
    fi

    log_success "ALSA loopback configured"
}

configure_alsa() {
    log_info "Configuring ALSA..."

    # Copy ALSA configuration from repo
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/etc/asound.conf" /etc/asound.conf

    log_success "ALSA configuration complete"
}

initialize_alsa_volume() {
    log_info "Initializing ALSA volume to 100%..."

    # Wait for sound card to be available
    sleep 2

    # Set HiFiBerry volume to 100% (passthrough - CamillaDSP manages actual volume)
    if [[ -n "$ALSA_CONTROL" ]]; then
        if amixer -c sndrpihifiberry sset "$ALSA_CONTROL" 100% 2>/dev/null; then
            log_success "ALSA $ALSA_CONTROL volume set to 100%"
            return 0
        fi
    fi

    # Fallback: try common controls
    if amixer -c sndrpihifiberry sset 'Digital' 100% 2>/dev/null; then
        log_success "ALSA Digital volume set to 100%"
    elif amixer -c sndrpihifiberry sset 'DAC' 100% 2>/dev/null; then
        log_success "ALSA DAC volume set to 100%"
    elif amixer -c sndrpihifiberry sset 'Master' 100% 2>/dev/null; then
        log_success "ALSA Master volume set to 100%"
    else
        log_warning "Could not set ALSA volume (card may not be available until reboot)"
    fi
}

create_systemd_services() {
    log_info "Installing systemd services..."

    # Copy all service files from repo
    sudo cp "$MILO_CLIENT_SYSTEM_DIR/milo-client.service" /etc/systemd/system/
    log_success "Installed milo-client.service"

    sudo cp "$MILO_CLIENT_SYSTEM_DIR/milo-client-snapclient.service" /etc/systemd/system/
    log_success "Installed milo-client-snapclient.service"

    sudo cp "$MILO_CLIENT_SYSTEM_DIR/milo-client-camilladsp.service" /etc/systemd/system/
    log_success "Installed milo-client-camilladsp.service"

    # Create environment file with dynamic values
    sudo tee "$MILO_CLIENT_DATA_DIR/env" > /dev/null << EOF
MILO_PRINCIPAL_IP=$MILO_PRINCIPAL_IP
MILO_CLIENT_DSP_ENABLED=false
EOF
    sudo chown "$MILO_CLIENT_USER:audio" "$MILO_CLIENT_DATA_DIR/env"

    sudo systemctl daemon-reload

    log_success "Systemd services installed"
}

enable_services() {
    log_info "Enabling services..."

    sudo systemctl daemon-reload
    sudo systemctl enable milo-client.service
    sudo systemctl enable milo-client-snapclient.service
    sudo systemctl enable milo-client-camilladsp.service

    log_success "Services enabled"
}

install_wrapper_scripts() {
    log_info "Installing secure wrapper scripts..."

    # Snapclient install wrapper
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/usr/local/bin/milo-client-install-snapclient" /usr/local/bin/
    sudo chmod 755 /usr/local/bin/milo-client-install-snapclient
    sudo chown root:root /usr/local/bin/milo-client-install-snapclient

    # Deploy update wrapper
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/usr/local/bin/milo-client-deploy-update" /usr/local/bin/
    sudo chmod 755 /usr/local/bin/milo-client-deploy-update
    sudo chown root:root /usr/local/bin/milo-client-deploy-update

    log_success "Wrapper scripts installed"
}

configure_sudoers() {
    log_info "Configuring sudo permissions for milo-client..."

    # Copy sudoers file from repo
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/etc/sudoers.d/milo-client" /etc/sudoers.d/
    sudo chmod 0440 /etc/sudoers.d/milo-client

    log_success "Sudo permissions configured"
}

configure_avahi() {
    log_info "Configuring Avahi (mDNS)..."

    # Determine active interface (eth0 preferred, wlan0 as fallback)
    local active_iface="eth0"
    if ! ip addr show eth0 2>/dev/null | grep -q 'inet '; then
        if ip addr show wlan0 2>/dev/null | grep -q 'inet '; then
            active_iface="wlan0"
            log_info "eth0 not available, using wlan0 for mDNS"
        fi
    fi

    # Copy and process Avahi config template
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/etc/avahi/avahi-daemon.conf.template" /etc/avahi/avahi-daemon.conf
    sudo sed -i "s/__ALLOW_IFACE__/$active_iface/" /etc/avahi/avahi-daemon.conf

    # Install systemd override to reset Avahi config to eth0 on every boot
    # Prevents stale wlan0 config from causing mDNS conflicts
    log_info "Installing Avahi boot reset override..."
    sudo mkdir -p /etc/systemd/system/avahi-daemon.service.d
    sudo cp "$MILO_CLIENT_SYSTEM_DIR/avahi-daemon-override.conf" \
        /etc/systemd/system/avahi-daemon.service.d/milo-override.conf
    sudo systemctl daemon-reload

    sudo systemctl enable avahi-daemon
    sudo systemctl restart avahi-daemon

    log_success "Avahi configured"
}

configure_network_priority() {
    log_info "Configuring network priority (ethernet over wifi)..."

    # Install unified NetworkManager dispatcher for WiFi/Ethernet priority and Avahi
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/etc/NetworkManager/dispatcher.d/90-milo-network" /etc/NetworkManager/dispatcher.d/
    sudo chmod 755 /etc/NetworkManager/dispatcher.d/90-milo-network

    # Remove legacy dispatchers from older installations
    sudo rm -f /etc/NetworkManager/dispatcher.d/98-wifi-eth0-priority
    sudo rm -f /etc/NetworkManager/dispatcher.d/99-avahi-interface

    # If currently connected via both ethernet and wifi, disconnect wifi now
    if ip addr show eth0 2>/dev/null | grep -q "inet " && \
       nmcli device status | grep -q "^wlan0.*connected"; then
        log_info "Disconnecting WiFi (ethernet is available)..."
        nmcli device disconnect wlan0 || true
    fi

    log_success "Network priority configured"
}

finalize_installation() {
    echo ""
    echo -e "${GREEN}====================================${NC}"
    echo -e "${GREEN}  Milo Client installation complete!${NC}"
    echo -e "${GREEN}====================================${NC}"
    echo ""

    if [[ -n "$ARG_AUDIO_ID" ]]; then
        echo -e "  Audio: ${GREEN}$AUDIO_LABEL${NC}"
    else
        echo -e "  Audio: ${YELLOW}Not configured${NC}"
    fi
    echo ""

    if [[ -z "$ARG_AUDIO_ID" ]]; then
        echo "  Next steps:"
        echo "    1. System will reboot"
        echo "    2. Open http://milo.local → Settings → Multiroom"
        echo "    3. Your new speaker will appear for configuration"
        echo ""
    fi

    log_info "System will reboot in 5 seconds..."
    sleep 5
    sudo reboot
}

# === UNINSTALL FUNCTION ===

uninstall_milo_client() {
    echo -e "${YELLOW}"
    echo "========================================"
    echo "      MILO CLIENT UNINSTALLATION        "
    echo "========================================"
    echo -e "${NC}"
    echo ""
    echo -e "${RED}This operation will remove:${NC}"
    echo "  - milo-client, milo-client-snapclient, and milo-client-camilladsp services"
    echo "  - The milo-client user and its data"
    echo "  - HiFiBerry audio configuration"
    echo "  - Snapclient and CamillaDSP"
    echo ""

    read -p "Are you sure you want to continue? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        log_info "Uninstallation cancelled"
        exit 0
    fi

    log_info "Starting uninstallation..."
    echo ""

    # 1. Stop and disable services
    log_info "Stopping services..."
    sudo systemctl stop milo-client.service 2>/dev/null || true
    sudo systemctl stop milo-client-snapclient.service 2>/dev/null || true
    sudo systemctl stop milo-client-camilladsp.service 2>/dev/null || true
    sudo systemctl disable milo-client.service 2>/dev/null || true
    sudo systemctl disable milo-client-snapclient.service 2>/dev/null || true
    sudo systemctl disable milo-client-camilladsp.service 2>/dev/null || true

    # 2. Remove service files
    log_info "Removing systemd services..."
    sudo rm -f /etc/systemd/system/milo-client.service
    sudo rm -f /etc/systemd/system/milo-client-snapclient.service
    sudo rm -f /etc/systemd/system/milo-client-camilladsp.service
    sudo systemctl daemon-reload

    # 3. Remove sudoers rules and wrapper scripts
    log_info "Removing sudoers rules and scripts..."
    sudo rm -f /etc/sudoers.d/milo-client
    sudo rm -f /usr/local/bin/milo-client-install-snapclient
    sudo rm -f /usr/local/bin/milo-client-deploy-update
    sudo rm -f /usr/local/bin/milo-client-apply-hardware

    # 4. Uninstall Snapclient
    log_info "Uninstalling Snapclient..."
    sudo apt remove -y snapclient 2>/dev/null || true
    sudo apt autoremove -y

    # 5. Remove CamillaDSP
    log_info "Removing CamillaDSP..."
    sudo rm -f /usr/local/bin/camilladsp
    sudo rm -f /etc/modprobe.d/milo-client-loopback.conf
    sudo sed -i '/snd-aloop/d' /etc/modules 2>/dev/null || true

    # 6. Remove ALSA configuration
    log_info "Removing ALSA configuration..."
    sudo rm -f /etc/asound.conf

    # 7. Restore config.txt (remove HiFiBerry audio block)
    log_info "Restoring audio configuration..."
    local config_file="/boot/firmware/config.txt"
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
    fi

    local reboot_required=false

    if [[ -f "$config_file" ]]; then
        # Remove managed audio block
        sudo sed -i '/^# BEGIN MILO CLIENT AUDIO$/,/^# END MILO CLIENT AUDIO$/d' "$config_file"

        # Remove legacy (non-managed) entries from old installs
        sudo sed -i '/# Milo Client - HiFiBerry Audio/d' "$config_file"
        sudo sed -i '/dtoverlay=hifiberry-/d' "$config_file"

        # Re-enable built-in audio
        if ! grep -q "^dtparam=audio=on" "$config_file"; then
            echo "dtparam=audio=on" | sudo tee -a "$config_file" > /dev/null
        fi

        reboot_required=true
    fi

    # 8. Remove application directories
    log_info "Removing application files..."
    sudo rm -rf "$MILO_CLIENT_REPO_DIR"
    sudo rm -rf "$MILO_CLIENT_VENV_DIR"
    sudo rm -rf "$MILO_CLIENT_DATA_DIR"

    # 9. Remove milo-client user
    log_info "Removing milo-client user..."
    if id "$MILO_CLIENT_USER" &>/dev/null; then
        sudo userdel -r "$MILO_CLIENT_USER" 2>/dev/null || true
    fi

    # 10. Restore default hostname
    local current_hostname=$(hostname)
    if [[ "$current_hostname" == "milo-client" || "$current_hostname" == milo-client-* ]]; then
        log_info "Restoring default hostname..."
        echo "raspberrypi" | sudo tee /etc/hostname > /dev/null
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\traspberrypi/" /etc/hosts
        sudo hostnamectl set-hostname "raspberrypi"
        reboot_required=true
    fi

    echo ""
    echo -e "${GREEN}=================================${NC}"
    echo -e "${GREEN}   Uninstallation complete!      ${NC}"
    echo -e "${GREEN}=================================${NC}"
    echo ""

    if [[ "$reboot_required" == "true" ]]; then
        echo -e "${YELLOW}REBOOT REQUIRED to finalize${NC}"
        echo ""

        while true; do
            read -p "Reboot now? (Y/n): " restart_choice
            case $restart_choice in
                [Nn]* )
                    echo -e "${YELLOW}Remember to reboot manually with: sudo reboot${NC}"
                    break
                    ;;
                [Yy]* | "" )
                    log_info "Rebooting in 5 seconds..."
                    sleep 5
                    sudo reboot
                    ;;
                * )
                    echo "Please answer 'Y' (yes) or 'n' (no)."
                    ;;
            esac
        done
    else
        log_success "System cleaned. Milo Client has been completely removed."
    fi
}

# === MAIN FUNCTION ===

main() {
    # Check if uninstall mode (scan all args)
    for arg in "$@"; do
        if [[ "$arg" == "--uninstall" ]]; then
            if [[ $# -gt 1 ]]; then
                log_error "--uninstall cannot be combined with other arguments"
                exit 1
            fi
            show_banner
            check_root
            uninstall_milo_client
            exit 0
        fi
    done

    # Normal installation
    show_banner
    check_root
    check_system
    parse_args "$@"

    log_info "Starting Milo Client installation"
    echo ""

    install_dependencies
    suppress_pulseaudio
    discover_milo_principal
    configure_journald
    setup_hostname

    create_milo_client_user
    clone_milo_client_repo
    install_snapclient
    install_camilladsp
    install_milo_client_application

    configure_alsa_loopback
    configure_alsa
    install_apply_hardware_script
    save_hardware_config

    # If --audio provided, apply hardware config and set ALSA volume
    if [[ -n "$ARG_AUDIO_ID" ]]; then
        log_info "Applying audio hardware configuration..."
        sudo /usr/local/bin/milo-client-apply-hardware --no-reboot
        initialize_alsa_volume
    fi

    create_systemd_services
    enable_services
    install_wrapper_scripts
    configure_sudoers
    configure_avahi
    configure_network_priority

    finalize_installation
}

main "$@"
