#!/bin/bash
# Milo - Shared install helpers
#
# Sourced by install.sh and milo-client/install-client.sh to avoid
# duplicating colour codes, log functions, temp directory cleanup,
# and common installation routines across main and client installs.

# ============================================================================
# Colour codes
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ============================================================================
# Log helpers
# ============================================================================

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

# ============================================================================
# Temp directory cleanup on exit/signal
# ============================================================================

# Only initialize once (safe if common.sh is sourced multiple times)
if [[ -z "${_TEMP_DIRS+set}" ]]; then
    _TEMP_DIRS=()
fi

register_temp_dir() {
    _TEMP_DIRS+=("$1")
}

_cleanup_temp_dirs() {
    for dir in "${_TEMP_DIRS[@]}"; do
        [[ -d "$dir" ]] && rm -rf "$dir" 2>/dev/null || true
    done
}
trap _cleanup_temp_dirs EXIT

# ============================================================================
# Shared system helpers
# ============================================================================

# Verify Raspberry Pi hardware and 64-bit architecture.
# Warns (non-fatal) when a desktop environment is detected.
check_system() {
    log_info "Checking system..."

    if ! grep -q "Raspberry Pi" /proc/cpuinfo; then
        log_error "This script is designed for Raspberry Pi only."
        exit 1
    fi

    local arch
    arch=$(uname -m)
    if [[ "$arch" != "aarch64" ]]; then
        log_error "Unsupported architecture: $arch. Raspberry Pi OS 64-bit required."
        exit 1
    fi

    # Milō is tested only on Raspberry Pi OS based on Debian Trixie. Warn (don't
    # block) on other releases — a future Debian may work, older ones likely won't.
    local codename
    codename=$(lsb_release -sc 2>/dev/null || grep VERSION_CODENAME /etc/os-release 2>/dev/null | cut -d= -f2)
    if [[ -n "$codename" && "$codename" != "trixie" ]]; then
        log_warning "Detected Debian '$codename' — Milō is only tested on Debian Trixie."
        log_warning "Installation may fail or behave unexpectedly on other releases."
        echo ""
    fi

    # Warning if a desktop environment is detected
    if systemctl list-units --type=service 2>/dev/null | grep -qE "lightdm|gdm|sddm|xdm"; then
        log_warning "A desktop environment has been detected."
        log_warning "Raspberry Pi OS Lite is recommended for optimal performance."
        echo ""
    fi

    log_success "Compatible system detected (Raspberry Pi OS 64-bit)"
}

# Set the system hostname (hostname file, /etc/hosts, hostnamectl).
# Usage: configure_hostname <new_hostname>
configure_hostname() {
    local new_hostname="$1"
    echo "$new_hostname" | sudo tee /etc/hostname > /dev/null
    sudo sed -i "s/127.0.1.1.*/127.0.1.1\t$new_hostname/" /etc/hosts
    sudo hostnamectl set-hostname "$new_hostname"
}

# Configure journald: persistent storage, 100 MB cap, 7-day retention.
#
# Persistent (on-disk) storage is required so logs survive a reboot — without
# it the journal is RAM-only and a reboot wipes the evidence of any boot-time
# failure (e.g. NetworkManager not starting). The 100 MB / 7-day caps bound
# SD-card wear.
#
# Must be applied as an /etc/ drop-in, NOT by editing /etc/systemd/journald.conf:
# Raspberry Pi OS ships /usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf
# (Storage=volatile), and any drop-in overrides the main config file, so a
# main-file edit would be silently ignored. An /etc/ drop-in outranks /usr/lib/.
configure_journald() {
    log_info "Configuring journald (persistent, 100MB, 7 days)..."

    sudo mkdir -p /etc/systemd/journald.conf.d
    sudo cp "$MILO_APP_DIR/rootfs/etc/systemd/journald.conf.d/99-milo-journald.conf" \
        /etc/systemd/journald.conf.d/99-milo-journald.conf

    # Create the persistent journal directory and apply immediately so logs
    # start being kept on disk without waiting for the next boot.
    sudo mkdir -p /var/log/journal
    sudo systemctl restart systemd-journald 2>/dev/null || true

    log_success "Journald configured (persistent on-disk, 100MB max, 7 days retention)"
}

# Remove PulseAudio and PipeWire (ALSA-only audio stack).
suppress_pulseaudio() {
    log_info "Removing PulseAudio/PipeWire..."
    sudo apt remove -y pulseaudio pipewire || true
    sudo apt autoremove -y
    log_success "PulseAudio/PipeWire removed"
}

# ============================================================================
# Shared install helpers (parameterized for main & client)
# ============================================================================

# Install Snapcast .deb packages from GitHub releases with multi-tier fallback.
#
# Usage: install_snapcast_packages <package_names...>
#   install_snapcast_packages snapserver snapclient   # main server
#   install_snapcast_packages snapclient              # satellite client
#
# Strategy: GitHub .deb (detected Debian) -> GitHub .deb (bookworm) -> apt repos
install_snapcast_packages() {
    local packages=("$@")
    local version="0.35.0"
    local label="${packages[*]}"

    log_info "Installing Snapcast ($label)..."

    # Detect Debian version (bookworm, trixie, bullseye, etc.)
    local debian_version
    debian_version=$(lsb_release -sc 2>/dev/null || grep VERSION_CODENAME /etc/os-release | cut -d= -f2)

    if [[ -z "$debian_version" ]]; then
        log_warning "Unable to detect Debian version, using bookworm as default"
        debian_version="bookworm"
    else
        log_info "Detected Debian version: $debian_version"
    fi

    local github_install_success=false

    # Method 1: Try GitHub .deb packages (latest version)
    log_info "Attempting installation from GitHub (latest version)..."

    local temp_dir
    temp_dir=$(mktemp -d) || { log_error "Failed to create temp directory"; return 1; }
    register_temp_dir "$temp_dir"
    pushd "$temp_dir" > /dev/null

    # _try_snapcast_github_install <deb_version>
    # Attempts to download and install .deb packages for a specific Debian version.
    # Uses 'packages', 'version', and 'label' from the enclosing function scope.
    _try_snapcast_github_install() {
        local deb_version="$1"
        local pkg

        log_info "Downloading Snapcast v${version} for $deb_version..."
        for pkg in "${packages[@]}"; do
            if ! wget "https://github.com/snapcast/snapcast/releases/download/v${version}/${pkg}_${version}-1_arm64_${deb_version}.deb" 2>/dev/null; then
                return 1
            fi
        done

        log_info "Installing dependencies..."
        sudo apt install -y libavahi-client3 libavahi-common3 libflac12t64 || sudo apt install -y libflac12 || true

        if sudo apt install -y ./*.deb; then
            log_success "Snapcast ($label) installed from GitHub packages ($deb_version)"
            return 0
        fi

        log_warning "Failed to install .deb packages, trying with dependency fix..."
        sudo apt --fix-broken install -y || true

        if sudo dpkg -i ./*.deb 2>/dev/null; then
            sudo apt --fix-broken install -y
            log_success "Snapcast ($label) installed from GitHub after fixing dependencies ($deb_version)"
            return 0
        fi

        return 1
    }

    # Try detected version, then bookworm fallback
    if _try_snapcast_github_install "$debian_version"; then
        github_install_success=true
    elif [[ "$debian_version" != "bookworm" ]]; then
        log_warning "Package for $debian_version not available, trying with bookworm..."
        rm -f ./*.deb 2>/dev/null
        if _try_snapcast_github_install "bookworm"; then
            github_install_success=true
        fi
    fi

    unset -f _try_snapcast_github_install

    popd > /dev/null

    # Method 2: Fall back to apt if GitHub method failed
    if [[ "$github_install_success" != "true" ]]; then
        log_warning "GitHub installation failed, falling back to Debian repositories..."
        if sudo apt install -y "${packages[@]}"; then
            log_success "Snapcast ($label) installed from Debian repositories"
        else
            log_error "Unable to install Snapcast ($label) from any source"
            return 1
        fi
    fi

    # Verify installation
    local pkg
    for pkg in "${packages[@]}"; do
        "$pkg" --version
    done

    # Stop and disable default services (Milo manages its own service units)
    for pkg in "${packages[@]}"; do
        sudo systemctl stop "${pkg}.service" || true
        sudo systemctl disable "${pkg}.service" || true
    done

    log_success "Snapcast ($label) installed"
}

# Download and install CamillaDSP binary + create config directories.
#
# Usage: install_camilladsp_binary <user> <data_dir> <config_source>
#   install_camilladsp_binary milo /var/lib/milo /home/milo/milo/rootfs/var/lib/milo/camilladsp/config.yml
#   install_camilladsp_binary milo-client /var/lib/milo-client /home/milo-client/repo/milo-client/configs/camilladsp/config.yml
install_camilladsp_binary() {
    local user="$1"
    local data_dir="$2"
    local config_source="$3"
    local version="4.1.3"

    log_info "Installing CamillaDSP..."

    local temp_dir
    temp_dir=$(mktemp -d) || { log_error "Failed to create temp directory"; return 1; }
    register_temp_dir "$temp_dir"
    pushd "$temp_dir" > /dev/null

    # Download CamillaDSP binary for ARM64
    log_info "Downloading CamillaDSP v${version}..."
    wget -q "https://github.com/HEnquist/camilladsp/releases/download/v${version}/camilladsp-linux-aarch64.tar.gz"
    tar -xzf camilladsp-linux-aarch64.tar.gz

    # Install binary
    sudo cp camilladsp /usr/local/bin/
    sudo chmod +x /usr/local/bin/camilladsp

    # Create configuration directories
    sudo mkdir -p "$data_dir/camilladsp"
    sudo mkdir -p "$data_dir/camilladsp/configs"
    sudo mkdir -p "$data_dir/camilladsp/coeffs"

    # Copy default configuration
    log_info "Installing CamillaDSP configuration..."
    sudo cp "$config_source" "$data_dir/camilladsp/config.yml"

    sudo chown -R "$user:$user" "$data_dir/camilladsp"

    # Verify installation
    /usr/local/bin/camilladsp --version

    popd > /dev/null

    log_success "CamillaDSP installed"
}
