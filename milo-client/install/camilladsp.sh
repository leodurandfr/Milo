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
    install_camilladsp_binary \
        "$MILO_CLIENT_USER" \
        "$MILO_CLIENT_DATA_DIR" \
        "$MILO_CLIENT_REPO_DIR/milo-client/configs/camilladsp/config.yml"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_camilladsp
fi
