#!/bin/bash
# Milo Client - Snapclient Installation (Multiroom Audio)
#
# Installs Snapclient from GitHub releases or Debian repos.
# Only the client is needed (server runs on the main Milo).
#
# Can be sourced from install-client.sh or run standalone.

set -e

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/../../install/common.sh"
fi

install_snapclient() {
    log_info "Installing Snapclient..."

    # Detect Debian version
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

    # Restore working directory
    popd > /dev/null

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

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_snapclient
fi
