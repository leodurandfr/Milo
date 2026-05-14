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
