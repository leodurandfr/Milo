#!/bin/bash
# Milo - Snapcast Installation (Multiroom Audio)
#
# Installs Snapcast (server + client) from GitHub releases or Debian repos,
# and configures Snapserver for Milo's multiroom audio.
#
# Can be sourced from install.sh or run standalone.

set -e

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_snapcast() {
    log_info "Installing Snapcast..."

    # Detect Debian version (bookworm, trixie, bullseye, etc.)
    local DEBIAN_VERSION
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

    local temp_dir
    temp_dir=$(mktemp -d) || { log_error "Failed to create temp directory"; return 1; }
    register_temp_dir "$temp_dir"
    pushd "$temp_dir" > /dev/null

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

    # Restore working directory
    popd > /dev/null

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

configure_snapserver() {
    log_info "Configuring Snapserver..."

    sudo tee /etc/snapserver.conf > /dev/null << 'EOF'

[stream]
default_source = Multiroom

buffer = 250
codec = flac
chunk_ms = 15
sampleformat = 48000:32:2

source = meta:///Bluetooth/ROC/Spotify/Radio/Podcast/AirPlay/CD?name=Multiroom

source = alsa:///?name=Bluetooth&device=hw:1,1,0&idle_threshold=5000&send_silence=true
source = alsa:///?name=ROC&device=hw:1,1,1&idle_threshold=5000&send_silence=true
source = alsa:///?name=Spotify&device=hw:1,1,2&idle_threshold=5000&send_silence=true
source = alsa:///?name=Radio&device=hw:1,1,3&idle_threshold=5000&send_silence=true
source = alsa:///?name=Podcast&device=hw:1,1,4&idle_threshold=5000&send_silence=true
source = alsa:///?name=AirPlay&device=hw:1,1,6&idle_threshold=5000&send_silence=true
source = alsa:///?name=CD&device=hw:1,1,7&idle_threshold=5000&send_silence=true

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

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_snapcast
    configure_snapserver
    log_success "Snapcast installation complete"
fi
