#!/bin/bash
# Milo Client - Network Configuration (Avahi + Network Priority)
#
# Configures Avahi (mDNS) for client discovery and
# network priority (ethernet over WiFi).
#
# Can be sourced from install-client.sh or run standalone.

set -e

MILO_CLIENT_USER="${MILO_CLIENT_USER:-milo-client}"
MILO_CLIENT_HOME="${MILO_CLIENT_HOME:-/home/$MILO_CLIENT_USER}"
MILO_CLIENT_REPO_DIR="${MILO_CLIENT_REPO_DIR:-$MILO_CLIENT_HOME/repo}"
MILO_CLIENT_ROOTFS_DIR="${MILO_CLIENT_ROOTFS_DIR:-$MILO_CLIENT_REPO_DIR/milo-client/rootfs}"
MILO_CLIENT_SYSTEM_DIR="${MILO_CLIENT_SYSTEM_DIR:-$MILO_CLIENT_REPO_DIR/milo-client/system}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/../../install/common.sh"
fi

configure_avahi() {
    log_info "Configuring Avahi (mDNS)..."

    # Install Avahi config (eth0 allowed, wlan0 denied by default).
    # Dispatcher flips both keys at runtime if eth0 becomes unavailable.
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/etc/avahi/avahi-daemon.conf" /etc/avahi/avahi-daemon.conf

    # Install the systemd override that re-applies the persisted interface
    # choice on every Avahi start (see milo-client-apply-avahi-iface).
    # The filename must match the one pi-gen and milo-first-boot use: on the
    # universal image both role drop-ins exist and first-boot keeps exactly one
    # by name, so a second spelling here is a drop-in nothing ever removes.
    log_info "Installing Avahi interface override..."
    sudo mkdir -p /etc/systemd/system/avahi-daemon.service.d
    sudo cp "$MILO_CLIENT_SYSTEM_DIR/avahi-daemon-override.conf" \
        /etc/systemd/system/avahi-daemon.service.d/milo-client-override.conf
    sudo rm -f /etc/systemd/system/avahi-daemon.service.d/milo-override.conf
    sudo systemctl daemon-reload

    sudo systemctl enable avahi-daemon
    sudo systemctl restart avahi-daemon

    log_success "Avahi configured"
}

configure_network_dispatcher() {
    log_info "Installing NetworkManager dispatcher (Avahi interface selection)..."

    sudo cp "$MILO_CLIENT_ROOTFS_DIR/etc/NetworkManager/dispatcher.d/90-milo-network" /etc/NetworkManager/dispatcher.d/
    sudo chmod 755 /etc/NetworkManager/dispatcher.d/90-milo-network

    # Remove legacy dispatchers from older installations
    sudo rm -f /etc/NetworkManager/dispatcher.d/98-wifi-eth0-priority
    sudo rm -f /etc/NetworkManager/dispatcher.d/99-avahi-interface

    log_success "Network dispatcher installed"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    configure_avahi
    configure_network_dispatcher
    log_success "Network configuration complete"
fi
