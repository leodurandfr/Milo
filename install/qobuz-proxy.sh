#!/bin/bash
# Milo - qobuz-proxy Installation (Qobuz Connect)
#
# Installs qobuz-proxy, a virtual Qobuz Connect device (reverse-engineered),
# as a sidecar under /var/lib/milo/qobuz — the exact model as go-librespot for
# Spotify. Family B source (passive receiver): the Qobuz app controls playback,
# Milō only renders audio + metadata. See PLAN_QOBUZ / docs.
#
# qobuz-proxy is NOT on PyPI — it is installed from its git tag into a venv.
# The one-time Qobuz account login is done by the operator via the settings
# screen (or http://milo.local:8689) and cached in credentials.json; it is
# user-specific and intentionally NOT baked into the image.
#
# Sourced by pi-gen/stage-milo during the image build.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

install_qobuz_proxy() {
    log_info "Installing qobuz-proxy..."

    # PortAudio runtime — required by the 'local' backend (pulls sounddevice).
    sudo apt-get install -y libportaudio2

    sudo mkdir -p "$MILO_DATA_DIR/qobuz"

    # Build the venv + install the pinned tag. The '[local]' extra pulls
    # sounddevice/soundfile/numpy for the PortAudio backend that targets the
    # named ALSA PCM 'milo_qobuz'.
    sudo python3 -m venv "$MILO_DATA_DIR/qobuz/venv"
    sudo "$MILO_DATA_DIR/qobuz/venv/bin/pip" install --upgrade pip
    sudo "$MILO_DATA_DIR/qobuz/venv/bin/pip" install \
        "qobuz-proxy[local] @ git+https://github.com/leolobato/qobuz-proxy@v${QOBUZ_PROXY_VERSION}"

    install_qobuz_adapter
    configure_qobuz_proxy

    # Own the whole tree (venv + config + future credentials.json) as milo:audio
    # so the milo-qobuz.service (User=milo) can read config + write the token.
    sudo chown -R "$MILO_USER:audio" "$MILO_DATA_DIR/qobuz"

    log_success "qobuz-proxy installed"
}

# Milō runs qobuz-proxy through its own launcher, which applies two adaptations
# upstream offers no configuration for: unity-gain volume policy (flag-gated on
# the "allow app volume" setting — CamillaDSP owns volume) and position/duration
# in /api/status. They bind to method names rather than to source text, and
# `--check` refuses a release that moved any of them — the same gate the in-app
# updater runs before restarting the service onto a new version.
install_qobuz_adapter() {
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-qobuz" /usr/local/bin/milo-qobuz
    sudo chmod 0755 /usr/local/bin/milo-qobuz
    sudo "$MILO_DATA_DIR/qobuz/venv/bin/python" /usr/local/bin/milo-qobuz --check
}

# Write /var/lib/milo/qobuz/config.yaml. Speakers-list form → qobuz-proxy builds
# one "local" backend speaker targeting the milo_qobuz ALSA PCM. This is the form
# qobuz-proxy itself normalizes to (it rewrites the file on any web-UI edit), and
# unlike the flat 'device:' form it derives a STABLE, deterministic device uuid
# from hostname+name (generate_speaker_uuid) — no manual uuid pin needed. (The
# flat form leaves uuid empty → a random uuid4 every boot → the Qobuz app's cached
# speaker dies on every source switch, since the sidecar restarts each switch.)
# Auth credentials are NOT written here — the OAuth token is cached separately in
# credentials.json (managed by the login flow), so this file is user-agnostic
# and safe to bake into the image.
configure_qobuz_proxy() {
    sudo tee "$MILO_DATA_DIR/qobuz/config.yaml" > /dev/null << 'EOF'
# qobuz-proxy config for Milō (Qobuz Connect sidecar, Family B).
server:
  http_port: 8689
  bind_address: 0.0.0.0    # phone must reach :8689/:8690 over the LAN via mDNS
logging:
  level: info
speakers:
  # name is ASCII on purpose — the Qobuz iOS app's Connect handshake silently
  # aborts on a non-ASCII device name (tested: "Milō" → the app spins and reverts,
  # never POSTs /streamcore/connect-to-qconnect; "Milo" → connects and plays).
  # Milō's own UI shows "Qobuz", so this only affects the Qobuz app's picker label.
  - name: Milo
    backend: local
    max_quality: 27        # 5=MP3 6=CD 7=HiRes96 27=HiRes192 (capped to device support)
    audio_device: milo_qobuz   # named ALSA PCM → CamillaDSP (direct) / LoopbackDLNA (multiroom)
    audio_buffer_size: 2048
EOF
}

