#!/bin/bash
# Milo - go-librespot Installation (Spotify Connect)
#
# Downloads and installs go-librespot binary for Spotify Connect support.
#
# Can be sourced from install.sh or run standalone.

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
    temp_dir=$(mktemp -d)
    register_temp_dir "$temp_dir"
    cd "$temp_dir"

    wget https://github.com/devgianlu/go-librespot/releases/download/v0.6.1/go-librespot_linux_arm64.tar.gz
    tar -xvzf go-librespot_linux_arm64.tar.gz
    sudo cp go-librespot /usr/local/bin/
    sudo chmod +x /usr/local/bin/go-librespot

    sudo mkdir -p "$MILO_DATA_DIR/go-librespot"
    sudo chown -R "$MILO_USER:audio" "$MILO_DATA_DIR/go-librespot"

    sudo tee "$MILO_DATA_DIR/go-librespot/config.yml" > /dev/null << 'EOF'
device_name: "Milō"
device_type: "speaker"
bitrate: 320

audio_backend: "alsa"
audio_device: "milo_spotify"

external_volume: true

server:
  enabled: true
  address: localhost
  port: 3678
  allow_origin: "*"
  image_size: 'xlarge'
EOF

    sudo chown -R "$MILO_USER:audio" "$MILO_DATA_DIR/go-librespot"

    cd ~
    rm -rf "$temp_dir"

    log_success "go-librespot installed"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_go_librespot
fi
