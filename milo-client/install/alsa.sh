#!/bin/bash
# Milo Client - ALSA Configuration (Loopback + Routing)
#
# Configures ALSA loopback module for CamillaDSP
# and deploys the client ALSA routing configuration.
#
# Can be sourced from install-client.sh or run standalone.

set -e

MILO_CLIENT_USER="${MILO_CLIENT_USER:-milo-client}"
MILO_CLIENT_HOME="${MILO_CLIENT_HOME:-/home/$MILO_CLIENT_USER}"
MILO_CLIENT_REPO_DIR="${MILO_CLIENT_REPO_DIR:-$MILO_CLIENT_HOME/repo}"
MILO_CLIENT_ROOTFS_DIR="${MILO_CLIENT_ROOTFS_DIR:-$MILO_CLIENT_REPO_DIR/milo-client/rootfs}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/../../install/common.sh"
fi

configure_alsa_loopback() {
    log_info "Configuring ALSA loopback module for CamillaDSP..."

    # Ensure snd-aloop module loads at boot with subdevices for CamillaDSP
    if ! grep -q "snd-aloop" /etc/modules 2>/dev/null; then
        echo "snd-aloop" | sudo tee -a /etc/modules
    fi

    # Copy loopback module configuration from repo
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/etc/modprobe.d/milo-client-loopback.conf" /etc/modprobe.d/

    # Load module immediately if not loaded (may fail if audio hardware not yet initialized - will load after reboot)
    if ! lsmod | grep -q "snd_aloop"; then
        sudo modprobe snd-aloop pcm_substreams=2 || true
    fi

    log_success "ALSA loopback configured"
}

configure_alsa() {
    log_info "Configuring ALSA..."

    # Copy ALSA configuration from repo
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/etc/asound.conf" /etc/asound.conf

    log_success "ALSA configuration complete"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    configure_alsa_loopback
    configure_alsa
    log_success "ALSA configuration complete"
fi
