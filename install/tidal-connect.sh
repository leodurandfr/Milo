#!/bin/bash
# Milo - Tidal Connect installation
#
# Installs the Tidal Connect daemon as a sidecar under /opt/milo/tidal-connect,
# the same model as go-librespot for Spotify. Family C source (active player):
# the Tidal app picks Milō as a speaker, and Milō both displays the track and
# drives transport over the daemon's `tisoc` controller socket.
#
# Unlike every other sidecar this one has no upstream to follow: the binary is
# a build of Tidal's proprietary Connect Device SDK, frozen since 2020, that
# nobody can rebuild. It is therefore deliberately absent from
# `backend/core/updates/catalog.py` — there is no version to read, no release
# to compare against, and an entry there would report an update state that
# cannot exist.
#
# The runtime tree is built by tidal_connect_runtime.py; see its docstring for
# why the payload comes from a container image while Milō runs no containers,
# and why the library overlay must come from bullseye and not anything newer.
#
# Sourced by pi-gen/stage-milo during the image build, or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"
TIDAL_ROOT="${TIDAL_ROOT:-/opt/milo/tidal-connect}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_tidal_connect() {
    log_info "Installing Tidal Connect..."

    # readelf is what the runtime builder verifies segment alignment with —
    # without it the install would "succeed" and the source would die on start.
    sudo apt-get install -y binutils

    sudo mkdir -p "$(dirname "$TIDAL_ROOT")"

    # BASH_SOURCE resolves the script dir even when sourced from a pi-gen stage,
    # same as install/qobuz-proxy.sh does for its patch helper.
    sudo python3 "$(dirname "${BASH_SOURCE[0]}")/tidal_connect_runtime.py" \
        --root "$TIDAL_ROOT"

    install_tidal_launcher

    # Read-only for the service user: nothing under here is written at runtime,
    # the controller socket lives in /run/milo.
    sudo chown -R root:root "$TIDAL_ROOT"

    log_success "Tidal Connect installed"
}

# The launcher is authored once in rootfs/ and copied verbatim, like every
# other /usr/local/bin/milo-* helper. It needs no sudoers entry: it runs as the
# milo user from milo-tidal.service and touches nothing privileged.
install_tidal_launcher() {
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-tidal-connect" \
        /usr/local/bin/milo-tidal-connect
    sudo chmod 0755 /usr/local/bin/milo-tidal-connect
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_tidal_connect
fi
