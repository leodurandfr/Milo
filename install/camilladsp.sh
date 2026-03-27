#!/bin/bash
# Milo - CamillaDSP Installation (Audio Processing)
#
# Downloads and installs CamillaDSP binary for audio processing
# (volume control, EQ, compressor, loudness).
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_camilladsp() {
    log_info "Installing CamillaDSP..."

    local temp_dir
    temp_dir=$(mktemp -d) || { log_error "Failed to create temp directory"; return 1; }
    register_temp_dir "$temp_dir"
    pushd "$temp_dir" > /dev/null

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

    popd > /dev/null

    log_success "CamillaDSP installed"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_camilladsp
fi
