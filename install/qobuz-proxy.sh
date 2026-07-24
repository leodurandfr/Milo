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
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"

# Version pin — installed from git (no PyPI release). Bump consciously.
QOBUZ_PROXY_VERSION="${QOBUZ_PROXY_VERSION:-1.5.0}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

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

    patch_qobuz_proxy
    configure_qobuz_proxy

    # Own the whole tree (venv + config + future credentials.json) as milo:audio
    # so the milo-qobuz.service (User=milo) can read config + write the token.
    sudo chown -R "$MILO_USER:audio" "$MILO_DATA_DIR/qobuz"

    log_success "qobuz-proxy installed"
}

# Apply Milō's edits to the vendored install: unity-gain volume policy (flag-gated
# on the "allow app volume" setting — CamillaDSP owns volume) and position/duration
# in /api/status. The patches themselves live in qobuz_proxy_patches.py so the
# fragile, version-pinned anchors have a single definition shared with the in-app
# updater — BASH_SOURCE resolves the script dir even when this file is sourced
# from install.sh / pi-gen.
patch_qobuz_proxy() {
    sudo "$MILO_DATA_DIR/qobuz/venv/bin/python" \
        "$(dirname "${BASH_SOURCE[0]}")/qobuz_proxy_patches.py"
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

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_qobuz_proxy
fi
