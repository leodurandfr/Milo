#!/bin/bash
# Milo - go-librespot configuration (Spotify Connect)
#
# Writes go-librespot's config.yml. The binary is downloaded by
# pi-gen/stage-milo/01-install-audio.
#
# Sourced by pi-gen/stage-milo during the image build.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"

# Write /var/lib/milo/go-librespot/config.yml. Kept separate from the binary
# download so the pi-gen image build can reuse it as the single source of truth
# (pi-gen installs the binary in its own audio stage). Inline-copying this block
# is exactly how the pi-gen image drifted and shipped without zeroconf_backend.
configure_go_librespot() {
    mkdir -p "$MILO_DATA_DIR/go-librespot"

    # zeroconf_backend=avahi: delegate Spotify Connect mDNS registration to
    # the system Avahi daemon over D-Bus. Without it, go-librespot ships its
    # own embedded mDNS responder that ignores Avahi's allow-interfaces and
    # broadcasts on every interface — racing Avahi and causing the milo.local
    # → milo-2.local rename whenever wlan0's DHCP lease rolls over.
    tee "$MILO_DATA_DIR/go-librespot/config.yml" > /dev/null << 'EOF'
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

    chown -R "$MILO_USER:audio" "$MILO_DATA_DIR/go-librespot"
}

