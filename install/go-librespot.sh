#!/bin/bash
# Milo - go-librespot Installation (Spotify Connect)
#
# Downloads and installs go-librespot binary for Spotify Connect support.
#
# Sourced by pi-gen/stage-milo during the image build, or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_go_librespot() {
    log_info "Installing go-librespot..."

    sudo apt-get install -y libogg-dev libvorbis-dev libasound2-dev

    local temp_dir
    temp_dir=$(mktemp -d) || { log_error "Failed to create temp directory"; return 1; }
    register_temp_dir "$temp_dir"
    pushd "$temp_dir" > /dev/null

    wget "https://github.com/devgianlu/go-librespot/releases/download/v${GO_LIBRESPOT_VERSION}/go-librespot_linux_arm64.tar.gz"
    tar -xvzf go-librespot_linux_arm64.tar.gz
    sudo cp go-librespot /usr/local/bin/
    sudo chmod +x /usr/local/bin/go-librespot

    configure_go_librespot

    popd > /dev/null

    log_success "go-librespot installed"
}

# Write /var/lib/milo/go-librespot/config.yml. Kept separate from the binary
# download so the pi-gen image build can reuse it as the single source of truth
# (pi-gen installs the binary in its own audio stage). Inline-copying this block
# is exactly how the pi-gen image drifted and shipped without zeroconf_backend.
configure_go_librespot() {
    sudo mkdir -p "$MILO_DATA_DIR/go-librespot"

    # zeroconf_backend=avahi: delegate Spotify Connect mDNS registration to
    # the system Avahi daemon over D-Bus. Without it, go-librespot ships its
    # own embedded mDNS responder that ignores Avahi's allow-interfaces and
    # broadcasts on every interface — racing Avahi and causing the milo.local
    # → milo-2.local rename whenever wlan0's DHCP lease rolls over.
    sudo tee "$MILO_DATA_DIR/go-librespot/config.yml" > /dev/null << 'EOF'
device_name: "Milō"
device_type: "speaker"
bitrate: 320

audio_backend: "alsa"
audio_device: "milo_spotify"

external_volume: true

zeroconf_backend: avahi

server:
  enabled: true
  address: localhost
  port: 3678
  allow_origin: "*"
  image_size: 'xlarge'
EOF

    sudo chown -R "$MILO_USER:audio" "$MILO_DATA_DIR/go-librespot"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_go_librespot
fi
