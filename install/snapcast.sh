#!/bin/bash
# Milo - Snapcast Installation (Multiroom Audio)
#
# Installs Snapcast (server + client) from GitHub releases or Debian repos,
# and configures Snapserver for Milo's multiroom audio.
#
# Can be sourced from install.sh or run standalone.

set -e

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_snapcast() {
    install_snapcast_packages snapserver snapclient
}

configure_snapserver() {
    log_info "Configuring Snapserver..."

    sudo tee /etc/snapserver.conf > /dev/null << 'EOF'

[stream]
default_source = Multiroom

buffer = 300
codec = flac
chunk_ms = 40
sampleformat = 48000:32:2

source = meta:///Bluetooth/ROC/Spotify/Radio/Podcast/AirPlay/CD/DLNA?name=Multiroom

source = alsa:///?name=Bluetooth&device=hw:1,1,1&idle_threshold=5000
source = alsa:///?name=ROC&device=hw:1,1,2&idle_threshold=5000
source = alsa:///?name=Spotify&device=hw:1,1,3&idle_threshold=5000
source = alsa:///?name=Radio&device=hw:1,1,4&idle_threshold=5000
source = alsa:///?name=Podcast&device=hw:1,1,5&idle_threshold=5000
source = alsa:///?name=AirPlay&device=hw:1,1,6&idle_threshold=5000
source = alsa:///?name=CD&device=hw:1,1,7&idle_threshold=5000
source = alsa:///?name=DLNA&device=hw:1,1,8&idle_threshold=5000

[http]
enabled = true
bind_to_address = 0.0.0.0
port = 1780
doc_root = /usr/share/snapserver/snapweb/

[server]
threads = 4

[logging]
enabled = true
EOF
    log_success "Snapserver configured"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_snapcast
    configure_snapserver
    log_success "Snapcast installation complete"
fi
