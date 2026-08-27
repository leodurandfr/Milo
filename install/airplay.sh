#!/bin/bash
# Milo - AirPlay 2 Installation (shairport-sync + NQPTP)
#
# This script installs shairport-sync with AirPlay 2 support
# and NQPTP (timing daemon required for AirPlay 2).
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

install_nqptp() {
    log_info "Installing NQPTP (AirPlay 2 timing daemon)..."

    local temp_dir
    temp_dir=$(mktemp -d) || { log_error "Failed to create temp directory"; return 1; }
    register_temp_dir "$temp_dir"
    pushd "$temp_dir" > /dev/null

    # Pinned from dependencies.env, which pi-gen reads too: the AirPlay 2 clock
    # must be one version across both install paths, and HEAD is not a version.
    git clone --branch "${NQPTP_VERSION}" --depth 1 https://github.com/mikebrady/nqptp.git
    cd nqptp
    autoreconf -fi
    ./configure --with-systemd-startup
    make -j$(nproc)
    sudo make install

    sudo systemctl enable nqptp
    sudo systemctl start nqptp

    popd > /dev/null

    log_success "NQPTP installed"
}

install_shairport_sync() {
    log_info "Installing shairport-sync (AirPlay 2)..."

    # Build dependencies
    sudo apt-get install -y \
        build-essential git autoconf automake libtool \
        libpopt-dev libconfig-dev libasound2-dev libavahi-client-dev \
        libssl-dev libsoxr-dev libplist-dev libplist-utils libsodium-dev libavutil-dev \
        libavcodec-dev libavformat-dev uuid-dev libgcrypt20-dev xxd \
        libglib2.0-dev

    local temp_dir
    temp_dir=$(mktemp -d) || { log_error "Failed to create temp directory"; return 1; }
    register_temp_dir "$temp_dir"
    pushd "$temp_dir" > /dev/null

    # Pinned from dependencies.env, not HEAD: 5.0/5.1 delivered no AirPlay
    # metadata (fixed upstream in 5.2).
    git clone --branch "${SHAIRPORT_SYNC_VERSION}" --depth 1 https://github.com/mikebrady/shairport-sync.git
    cd shairport-sync
    autoreconf -fi
    ./configure --sysconfdir=/etc \
        --with-alsa \
        --with-avahi \
        --with-ssl=openssl \
        --with-soxr \
        --with-metadata \
        --with-metadata-pipe \
        --with-airplay-2 \
        --with-dbus-interface
    # --with-metadata already implies the pipe on 5.2.x, but the pipe is the
    # channel Milo reads AirPlay metadata from — name it rather than inherit it.
    make -j$(nproc)
    sudo make install

    popd > /dev/null

    log_success "shairport-sync installed"
}

configure_shairport_sync() {
    log_info "Configuring shairport-sync for Milo..."

    # Create metadata pipe directory
    sudo mkdir -p /tmp
    sudo mkfifo /tmp/shairport-sync-metadata 2>/dev/null || true
    sudo chown "$MILO_USER:audio" /tmp/shairport-sync-metadata

    # Deploy configuration from rootfs/ — pi-gen copies the same two files, so
    # neither installer restates their content (the S32_LE capture format below
    # reached script-installed units only, for as long as pi-gen inlined its own
    # copy of this config).
    sudo cp "$MILO_APP_DIR/rootfs/etc/shairport-sync.conf" /etc/shairport-sync.conf

    # D-Bus policy: allow milo user to own the ShairportSync bus name
    sudo cp "$MILO_APP_DIR/rootfs/etc/dbus-1/system.d/shairport-sync-dbus.conf" \
        /etc/dbus-1/system.d/shairport-sync-dbus.conf

    # Disable default shairport-sync service (Milo manages its own)
    sudo systemctl stop shairport-sync.service 2>/dev/null || true
    sudo systemctl disable shairport-sync.service 2>/dev/null || true

    log_success "shairport-sync configured for Milo"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_nqptp
    install_shairport_sync
    configure_shairport_sync
    log_success "AirPlay 2 installation complete"
fi
