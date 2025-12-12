#!/bin/bash
# Milo Client - Installation Script v1.2 (with uninstall + default volume)

set -e

MILO_CLIENT_USER="milo-client"
MILO_CLIENT_HOME="/home/$MILO_CLIENT_USER"
MILO_CLIENT_REPO_DIR="$MILO_CLIENT_HOME/repo"
MILO_CLIENT_APP_DIR="$MILO_CLIENT_REPO_DIR/milo-client/app"
MILO_CLIENT_SYSTEM_DIR="$MILO_CLIENT_REPO_DIR/milo-client/system"
MILO_CLIENT_VENV_DIR="$MILO_CLIENT_HOME/venv"
MILO_CLIENT_DATA_DIR="/var/lib/milo-client"
MILO_CLIENT_REPO_URL="https://github.com/leodurandfr/Milo.git"
REBOOT_REQUIRED=false

# Variables to store user choices
USER_HOSTNAME_CHOICE=""
USER_HIFIBERRY_CHOICE=""
HIFIBERRY_OVERLAY=""
CARD_NAME=""
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
    echo "Client Installation Script v1.2"
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
        log_error "Unsupported architecture: $ARCH. Raspberry Pi OS 64-bit required."
        exit 1
    fi

    log_success "Compatible system detected"
}

discover_milo_principal() {
    log_info "Searching for main Milo on the network..."

    if MILO_PRINCIPAL_IP=$(getent hosts milo.local 2>/dev/null | awk '{print $1}' | head -1); then
        log_success "Main Milo found at: $MILO_PRINCIPAL_IP (milo.local)"
        return 0
    fi

    log_error "Unable to find main Milo automatically."
    echo ""
    read -p "Enter the IP address of main Milo: " MILO_PRINCIPAL_IP
    if [[ -z "$MILO_PRINCIPAL_IP" ]]; then
        log_error "IP address required. Installation cancelled."
        exit 1
    fi
}

collect_user_choices() {
    echo ""
    log_info "Milo Client configuration - Answer the following questions:"
    echo ""

    # 1. Hostname choice
    echo "Choose a number for this client (01-99):"
    while true; do
        read -p "Client number [01]: " USER_HOSTNAME_CHOICE
        USER_HOSTNAME_CHOICE=${USER_HOSTNAME_CHOICE:-01}

        if [[ $USER_HOSTNAME_CHOICE =~ ^[0-9]{1,2}$ ]]; then
            USER_HOSTNAME_CHOICE=$(printf "%02d" $USER_HOSTNAME_CHOICE)
            log_success "Hostname: milo-client-$USER_HOSTNAME_CHOICE"
            break
        else
            echo "Please enter a number between 01 and 99."
        fi
    done

    # 2. HiFiBerry card choice
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
    echo ""

    while true; do
        read -p "Your choice [1]: " USER_HIFIBERRY_CHOICE
        USER_HIFIBERRY_CHOICE=${USER_HIFIBERRY_CHOICE:-1}

        case $USER_HIFIBERRY_CHOICE in
            1) HIFIBERRY_OVERLAY="hifiberry-dacplus-std"; CARD_NAME="sndrpihifiberry"; log_success "Card selected: Amp2"; break;;
            2) HIFIBERRY_OVERLAY="hifiberry-dacplus-std"; CARD_NAME="sndrpihifiberry"; log_success "Card selected: Amp4"; break;;
            3) HIFIBERRY_OVERLAY="hifiberry-amp4pro"; CARD_NAME="sndrpihifiberry"; log_success "Card selected: Amp4 Pro"; break;;
            4) HIFIBERRY_OVERLAY="hifiberry-amp100"; CARD_NAME="sndrpihifiberry"; log_success "Card selected: Amp100"; break;;
            5) HIFIBERRY_OVERLAY="hifiberry-dac"; CARD_NAME="sndrpihifiberry"; log_success "Card selected: Beocreate 4CA"; break;;
            *) echo "Invalid choice. Please enter a number between 1 and 5.";;
        esac
    done

    echo ""
    log_success "Configuration complete! Installation will now continue automatically..."
    echo ""
    sleep 2
}

setup_hostname() {
    local new_hostname="milo-client-$USER_HOSTNAME_CHOICE"
    local current_hostname=$(hostname)

    if [ "$current_hostname" != "$new_hostname" ]; then
        log_info "Configuring hostname '$new_hostname'..."
        echo "$new_hostname" | sudo tee /etc/hostname > /dev/null
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
        sudo hostnamectl set-hostname "$new_hostname"
        log_success "Hostname configured"
        REBOOT_REQUIRED=true
    else
        log_success "Hostname '$new_hostname' already configured"
    fi
}

configure_audio_hardware() {
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
        echo "# Milo Client - HiFiBerry Audio" | sudo tee -a "$config_file"
        echo "dtoverlay=$HIFIBERRY_OVERLAY" | sudo tee -a "$config_file"
    fi

    log_success "Audio hardware configuration complete"
    REBOOT_REQUIRED=true
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
        avahi-utils

    sudo rm -f /etc/apt/apt.conf.d/local

    log_success "Dependencies installed"
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

    log_info "Downloading Snapclient v0.34.0 for $DEBIAN_VERSION..."
    if wget "https://github.com/snapcast/snapcast/releases/download/v0.34.0/snapclient_0.34.0-1_arm64_${DEBIAN_VERSION}.deb" 2>/dev/null; then

        log_info "Installing dependencies..."
        sudo apt install -y libavahi-client3 libavahi-common3 libflac12t64 || sudo apt install -y libflac12 || true

        if sudo apt install -y "./snapclient_0.34.0-1_arm64_${DEBIAN_VERSION}.deb"; then
            log_success "Snapclient installed from GitHub packages"
            github_install_success=true
        else
            log_warning "Failed to install .deb package, trying with dependency fix..."
            sudo apt --fix-broken install -y || true
            if sudo dpkg -i "snapclient_0.34.0-1_arm64_${DEBIAN_VERSION}.deb" 2>/dev/null; then
                sudo apt --fix-broken install -y
                log_success "Snapclient installed from GitHub after fixing dependencies"
                github_install_success=true
            fi
        fi
    else
        # Try bookworm fallback for download
        log_warning "Package for $DEBIAN_VERSION not available, trying with bookworm..."
        DEBIAN_VERSION="bookworm"

        if wget "https://github.com/snapcast/snapcast/releases/download/v0.34.0/snapclient_0.34.0-1_arm64_bookworm.deb" 2>/dev/null; then

            log_info "Installing dependencies..."
            sudo apt install -y libavahi-client3 libavahi-common3 libflac12t64 || sudo apt install -y libflac12 || true

            if sudo apt install -y "./snapclient_0.34.0-1_arm64_bookworm.deb"; then
                log_success "Snapclient installed from GitHub packages (bookworm fallback)"
                github_install_success=true
            else
                sudo apt --fix-broken install -y || true
                if sudo dpkg -i "snapclient_0.34.0-1_arm64_bookworm.deb" 2>/dev/null; then
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

    # Clone with sparse checkout (only milo-client directory)
    sudo -u "$MILO_CLIENT_USER" git clone --no-checkout --depth 1 "$MILO_CLIENT_REPO_URL" "$MILO_CLIENT_REPO_DIR"
    sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" sparse-checkout init --cone
    sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" sparse-checkout set milo-client
    sudo -u "$MILO_CLIENT_USER" git -C "$MILO_CLIENT_REPO_DIR" checkout

    log_success "Repository cloned (sparse checkout: milo-client/)"
}

install_milo_client_application() {
    log_info "Configuring Python environment for Milo Client..."

    sudo -u "$MILO_CLIENT_USER" python3 -m venv "$MILO_CLIENT_VENV_DIR"
    sudo -u "$MILO_CLIENT_USER" bash -c "source $MILO_CLIENT_VENV_DIR/bin/activate && pip install --upgrade pip"
    sudo -u "$MILO_CLIENT_USER" bash -c "source $MILO_CLIENT_VENV_DIR/bin/activate && pip install -r $MILO_CLIENT_APP_DIR/requirements.txt"

    log_success "Milo Client application installed"
}

configure_alsa() {
    log_info "Configuring ALSA..."

    sudo tee /etc/asound.conf > /dev/null << 'EOF'
# ALSA configuration for Milo Client
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
EOF

    log_success "ALSA configuration complete"
}

create_systemd_services() {
    log_info "Installing systemd services..."

    # Copy static service files from repo
    sudo cp "$MILO_CLIENT_SYSTEM_DIR/milo-client.service" /etc/systemd/system/
    log_success "Installed milo-client.service"

    sudo cp "$MILO_CLIENT_SYSTEM_DIR/milo-client-snapclient.service" /etc/systemd/system/
    log_success "Installed milo-client-snapclient.service"

    # Create environment file with dynamic value
    sudo tee "$MILO_CLIENT_DATA_DIR/env" > /dev/null << EOF
MILO_PRINCIPAL_IP=$MILO_PRINCIPAL_IP
EOF

    sudo systemctl daemon-reload

    log_success "Systemd services installed"
}

enable_services() {
    log_info "Enabling services..."

    sudo systemctl daemon-reload
    sudo systemctl enable milo-client.service
    sudo systemctl enable milo-client-snapclient.service

    log_success "Services enabled"
}

install_wrapper_script() {
    log_info "Installing secure wrapper script..."

    sudo tee /usr/local/bin/milo-client-install-snapclient > /dev/null << 'WRAPPER_EOF'
#!/bin/bash
# Milo Client - Secure Snapclient Installation Wrapper
# Only allows installing validated snapclient .deb packages
set -euo pipefail

error_exit() { echo "ERROR: $1" >&2; exit 1; }

[ $# -ne 1 ] && error_exit "Usage: $0 <path-to-snapclient-deb>"

DEB_PATH="$1"

# Validate file exists
[ ! -f "$DEB_PATH" ] && error_exit "File does not exist: $DEB_PATH"

# Validate absolute path
[[ "$DEB_PATH" != /* ]] && error_exit "Path must be absolute"

# Validate in temp directory only
case "$DEB_PATH" in
    /tmp/*|/var/tmp/*) ;;
    *) error_exit "DEB file must be in /tmp or /var/tmp" ;;
esac

# Validate .deb extension
[[ "$DEB_PATH" != *.deb ]] && error_exit "File must have .deb extension"

# Validate filename pattern (snapclient releases)
FILENAME=$(basename "$DEB_PATH")
if ! [[ "$FILENAME" =~ ^snapclient_[0-9]+\.[0-9]+\.[0-9]+-[0-9]+_arm64_(bookworm|trixie|bullseye|buster)\.deb$ ]]; then
    error_exit "Filename does not match snapclient pattern: $FILENAME"
fi

# Validate package name inside .deb
PACKAGE_NAME=$(dpkg-deb --show --showformat='${Package}' "$DEB_PATH" 2>/dev/null) || \
    error_exit "Failed to read package info"
[ "$PACKAGE_NAME" != "snapclient" ] && error_exit "Package is not snapclient: $PACKAGE_NAME"

# Update package lists and install
DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "$DEB_PATH"
WRAPPER_EOF

    sudo chmod 755 /usr/local/bin/milo-client-install-snapclient
    sudo chown root:root /usr/local/bin/milo-client-install-snapclient

    log_success "Wrapper script installed"
}

configure_sudoers() {
    log_info "Configuring sudo permissions for milo-client..."

    sudo tee /etc/sudoers.d/milo-client > /dev/null << 'EOF'
# Milo Client - Permissions for automatic updates
milo-client ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop milo-client-snapclient.service
milo-client ALL=(ALL) NOPASSWD: /usr/bin/systemctl start milo-client-snapclient.service
milo-client ALL=(ALL) NOPASSWD: /usr/bin/systemctl is-active milo-client-snapclient.service
milo-client ALL=(root) NOPASSWD: /usr/local/bin/milo-client-install-snapclient
EOF

    sudo chmod 0440 /etc/sudoers.d/milo-client
    log_success "Sudo permissions configured"
}


finalize_installation() {
    log_info "Finalizing installation..."

    echo ""
    echo -e "${GREEN}=====================================${NC}"
    echo -e "${GREEN}  Milo Client installation complete!${NC}"
    echo -e "${GREEN}=====================================${NC}"
    echo ""
    echo -e "${BLUE}Configuration:${NC}"
    echo "  - Hostname: milo-client-$USER_HOSTNAME_CHOICE"
    echo "  - User: $MILO_CLIENT_USER"
    echo "  - Application: $MILO_CLIENT_APP_DIR"
    echo "  - Audio card: $HIFIBERRY_OVERLAY"
    echo "  - Main Milo: $MILO_PRINCIPAL_IP"
    echo "  - Default volume: 16%"
    echo ""
    echo -e "${BLUE}Services:${NC}"
    echo "  - milo-client.service (API on port 8001)"
    echo "  - milo-client-snapclient.service"
    echo ""

    if [[ "$REBOOT_REQUIRED" == "true" ]]; then
        echo -e "${YELLOW}REBOOT REQUIRED${NC}"
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
        log_info "Starting services..."
        sudo systemctl start milo-client.service
        sudo systemctl start milo-client-snapclient.service

        echo ""
        echo -e "${GREEN}Milo Client is ready! It should appear automatically in main Milo.${NC}"
    fi
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
    echo "  - milo-client and milo-client-snapclient services"
    echo "  - The milo-client user and its data"
    echo "  - HiFiBerry audio configuration"
    echo "  - Snapclient"
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
    sudo systemctl disable milo-client.service 2>/dev/null || true
    sudo systemctl disable milo-client-snapclient.service 2>/dev/null || true

    # 2. Remove service files
    log_info "Removing systemd services..."
    sudo rm -f /etc/systemd/system/milo-client.service
    sudo rm -f /etc/systemd/system/milo-client-snapclient.service
    sudo systemctl daemon-reload

    # 3. Remove sudoers rules and wrapper script
    log_info "Removing sudoers rules..."
    sudo rm -f /etc/sudoers.d/milo-client
    sudo rm -f /usr/local/bin/milo-client-install-snapclient

    # 4. Uninstall Snapclient
    log_info "Uninstalling Snapclient..."
    sudo apt remove -y snapclient 2>/dev/null || true
    sudo apt autoremove -y

    # 5. Remove ALSA configuration
    log_info "Removing ALSA configuration..."
    sudo rm -f /etc/asound.conf

    # 6. Restore config.txt (remove HiFiBerry)
    log_info "Restoring audio configuration..."
    local config_file="/boot/firmware/config.txt"
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
    fi

    if [[ -f "$config_file" ]]; then
        sudo sed -i '/# Milo Client - HiFiBerry Audio/d' "$config_file"
        sudo sed -i '/dtoverlay=hifiberry-/d' "$config_file"

        # Re-enable built-in audio
        if ! grep -q "^dtparam=audio=on" "$config_file"; then
            echo "dtparam=audio=on" | sudo tee -a "$config_file" > /dev/null
        fi

        REBOOT_REQUIRED=true
    fi

    # 7. Remove application directories
    log_info "Removing application files..."
    sudo rm -rf "$MILO_CLIENT_REPO_DIR"
    sudo rm -rf "$MILO_CLIENT_VENV_DIR"
    sudo rm -rf "$MILO_CLIENT_DATA_DIR"

    # 8. Remove milo-client user
    log_info "Removing milo-client user..."
    if id "$MILO_CLIENT_USER" &>/dev/null; then
        sudo userdel -r "$MILO_CLIENT_USER" 2>/dev/null || true
    fi

    # 9. Restore default hostname (optional)
    local current_hostname=$(hostname)
    if [[ "$current_hostname" == milo-client-* ]]; then
        log_info "Restoring default hostname..."
        echo "raspberrypi" | sudo tee /etc/hostname > /dev/null
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\traspberrypi/" /etc/hosts
        sudo hostnamectl set-hostname "raspberrypi"
        REBOOT_REQUIRED=true
    fi

    echo ""
    echo -e "${GREEN}=================================${NC}"
    echo -e "${GREEN}   Uninstallation complete!      ${NC}"
    echo -e "${GREEN}=================================${NC}"
    echo ""

    if [[ "$REBOOT_REQUIRED" == "true" ]]; then
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
    # Check if uninstall mode
    if [[ "$1" == "--uninstall" ]]; then
        show_banner
        check_root
        uninstall_milo_client
        exit 0
    fi

    # Normal installation
    show_banner

    check_root
    check_system

    log_info "Starting Milo Client installation"
    echo ""

    discover_milo_principal
    collect_user_choices

    install_dependencies
    setup_hostname
    configure_audio_hardware

    create_milo_client_user
    install_snapclient
    clone_milo_client_repo
    install_milo_client_application

    configure_alsa
    create_systemd_services
    enable_services
    install_wrapper_script
    configure_sudoers

    finalize_installation
}

main "$@"
