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
    install_camilladsp_binary \
        "$MILO_USER" \
        "$MILO_DATA_DIR" \
        "$MILO_APP_DIR/rootfs/var/lib/milo/camilladsp/config.yml"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_camilladsp
fi
