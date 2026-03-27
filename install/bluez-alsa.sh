#!/bin/bash
# Milo - bluez-alsa Installation (Bluetooth Audio)
#
# Builds and installs bluez-alsa from source for Bluetooth audio support.
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

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

    local temp_dir
    temp_dir=$(mktemp -d) || { log_error "Failed to create temp directory"; return 1; }
    register_temp_dir "$temp_dir"
    pushd "$temp_dir" > /dev/null

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

    popd > /dev/null

    sudo systemctl stop bluealsa-aplay.service bluealsa.service || true
    sudo systemctl disable bluealsa-aplay.service bluealsa.service || true

    # Set Bluetooth device name via machine-info (BlueZ recommended approach)
    sudo cp "$MILO_APP_DIR/rootfs/etc/machine-info" /etc/machine-info

    log_success "bluez-alsa installed"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_bluez_alsa
fi
