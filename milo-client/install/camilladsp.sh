#!/bin/bash
# Milo Client - CamillaDSP Installation (Audio Processing)
#
# Downloads and installs CamillaDSP binary for client-side audio processing.
#
# Can be sourced from install-client.sh or run standalone.

set -e

MILO_CLIENT_USER="${MILO_CLIENT_USER:-milo-client}"
MILO_CLIENT_HOME="${MILO_CLIENT_HOME:-/home/$MILO_CLIENT_USER}"
MILO_CLIENT_REPO_DIR="${MILO_CLIENT_REPO_DIR:-$MILO_CLIENT_HOME/repo}"
MILO_CLIENT_DATA_DIR="${MILO_CLIENT_DATA_DIR:-/var/lib/milo-client}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/../../install/common.sh"
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

    sudo cp camilladsp /usr/local/bin/
    sudo chmod +x /usr/local/bin/camilladsp

    # Create CamillaDSP directories
    sudo mkdir -p "$MILO_CLIENT_DATA_DIR/camilladsp"
    sudo mkdir -p "$MILO_CLIENT_DATA_DIR/camilladsp/configs"
    sudo mkdir -p "$MILO_CLIENT_DATA_DIR/camilladsp/coeffs"

    # Copy default CamillaDSP configuration from repo
    sudo cp "$MILO_CLIENT_REPO_DIR/milo-client/configs/camilladsp/config.yml" "$MILO_CLIENT_DATA_DIR/camilladsp/config.yml"

    sudo chown -R "$MILO_CLIENT_USER:$MILO_CLIENT_USER" "$MILO_CLIENT_DATA_DIR/camilladsp"

    popd > /dev/null

    log_success "CamillaDSP installed"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_camilladsp
fi
