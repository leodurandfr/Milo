#!/bin/bash
# Milo - ALSA Configuration (Loopback + Routing)
#
# Configures ALSA loopback module for Snapcast multiroom
# and deploys the complete ALSA routing configuration.
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

configure_alsa_loopback() {
    log_info "Configuring ALSA loopback..."

    echo "snd-aloop" | sudo tee /etc/modules-load.d/snd-aloop.conf
    echo "options snd-aloop index=1 enable=1 pcm_substreams=8" | sudo tee /etc/modprobe.d/snd-aloop.conf

    sudo modprobe snd-aloop || true

    log_success "ALSA loopback configured"
}

configure_alsa_complete() {
    log_info "Configuring complete ALSA setup with CamillaDSP..."

    sudo cp "$MILO_APP_DIR/rootfs/etc/asound.conf" /etc/asound.conf

    sudo tee "$MILO_DATA_DIR/routing.env" > /dev/null << 'EOF'
MILO_MODE=direct
EOF

    sudo tee "$MILO_DATA_DIR/snapclient.env" > /dev/null << 'EOF'
MILO_SNAPCLIENT_BUFFER_TIME=80
MILO_SNAPCLIENT_FRAGMENTS=4
EOF

    sudo tee "$MILO_DATA_DIR/mac.env" > /dev/null << 'EOF'
ROC_TARGET_LATENCY=50ms
ROC_LATENCY_PROFILE=responsive
ROC_FRAME_LENGTH=4ms
EOF

    sudo chown "$MILO_USER:$MILO_USER" \
        "$MILO_DATA_DIR/routing.env" \
        "$MILO_DATA_DIR/snapclient.env" \
        "$MILO_DATA_DIR/mac.env"

    log_success "Complete ALSA configuration done"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    configure_alsa_loopback
    configure_alsa_complete
    log_success "ALSA configuration complete"
fi
