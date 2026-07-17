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

    pin_qobuz_proxy_unity_volume
    configure_qobuz_proxy

    # Own the whole tree (venv + config + future credentials.json) as milo:audio
    # so the milo-qobuz.service (User=milo) can read config + write the token.
    sudo chown -R "$MILO_USER:audio" "$MILO_DATA_DIR/qobuz"

    log_success "qobuz-proxy installed"
}

# Pin qobuz-proxy's local (PortAudio) backend to unity gain. CamillaDSP is the
# single volume authority in Milō's audio path — exactly the role external_volume
# plays for go-librespot and ignore_volume_control for shairport-sync. qobuz-proxy
# has no config knob for this on the local backend (fixed_volume is DLNA-only) and
# its output stream even defaults to 50%, so the Qobuz app's slider would attenuate
# (and lose bits) before CamillaDSP. We edit the two spots in the vendored stream
# that set the software gain so it is always 1.0. Version-pinned (QOBUZ_PROXY_VERSION):
# the edit fails loudly if the anchors move on an upgrade, forcing a conscious re-check.
pin_qobuz_proxy_unity_volume() {
    sudo "$MILO_DATA_DIR/qobuz/venv/bin/python" - << 'PY'
import io
from qobuz_proxy.backends.local import stream as m

path = m.__file__
src = io.open(path, encoding="utf-8").read()

edits = [
    # __init__ default gain (0.0-1.0 float)
    ("        self._volume: float = 0.5  # 0.0 to 1.0",
     "        self._volume: float = 1.0  # Milo: pinned to unity, CamillaDSP owns volume"),
    # set_volume() body: ignore the app slider, stay at unity
    ("        self._volume = max(0.0, min(1.0, level / 100.0))",
     "        self._volume = 1.0  # Milo: ignore the Qobuz app slider, CamillaDSP owns volume"),
]

for old, new in edits:
    if new in src:
        continue  # already applied (idempotent re-install)
    if old not in src:
        raise SystemExit(
            f"qobuz-proxy unity-volume pin: anchor not found in {path!r}:\n  {old!r}\n"
            "Upstream stream.py changed (version bump?) — re-verify the pin."
        )
    src = src.replace(old, new, 1)

io.open(path, "w", encoding="utf-8").write(src)
print("qobuz-proxy: local backend pinned to unity volume")
PY
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
