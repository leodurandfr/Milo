#!/bin/bash
# Milo - ALSA routing configuration
#
# Deploys /etc/asound.conf and the three env files the source units read through
# EnvironmentFile= (routing.env, snapclient.env, mac.env). The snd-aloop module
# options are not here: pi-gen writes /etc/modprobe.d/snd-aloop.conf inline,
# before it sources this file.
#
# Sourced by pi-gen/stage-milo during the image build.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

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

