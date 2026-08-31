# backend/config/constants.py
"""
Centralized constants for the Milo backend.
All hardcoded values should be defined here to avoid duplication.
"""
from pathlib import Path
from typing import Literal, get_args

from backend.core.models.audio_state import AudioSource

# =============================================================================
# BASE PATHS
# =============================================================================
MILO_DATA_DIR = Path("/var/lib/milo")

# =============================================================================
# DATA FILES (derived from MILO_DATA_DIR)
# =============================================================================
SETTINGS_FILE = MILO_DATA_DIR / "settings.json"
HARDWARE_FILE = MILO_DATA_DIR / "hardware.json"
ERROR_LOG_FILE = MILO_DATA_DIR / "errors.log"

# =============================================================================
# DIRECTORIES (derived from MILO_DATA_DIR)
# =============================================================================
CD_DATA_FILE = MILO_DATA_DIR / "cd_data.json"
CD_COVERS_DIR = MILO_DATA_DIR / "cd_covers"
CD_DEVICE = "/dev/sr0"

# =============================================================================
# MUSIC LIBRARY / NAVIDROME (catalog engine sidecar)
# =============================================================================
# Navidrome runs as an always-on daemon (milo-navidrome.service) indexing the
# mount root and exposing a localhost Subsonic API. The music_library source
# talks to it over HTTP — never over the LAN (respects "local network only").
# DataFolder lives under MILO_DATA_DIR so backup/restore captures it.
MUSIC_LIBRARY_MOUNT_ROOT = Path("/media/milo")     # Navidrome MusicFolder (mounts appear here)
# Network-share config (SMB/NFS). Non-secret only — id/type/host/path/name.
# The share secrets (username/password/domain) never land here; they live in a
# root-only cred file written by milo-mount (see MILO_MOUNT_CMD --network below).
MUSIC_LIBRARY_DATA_FILE = MILO_DATA_DIR / "music_library_data.json"
# Artist photos Milō resolved from Deezer itself (Navidrome's online tier for
# artist art is off — see artist_images.py). A disposable derived cache: no
# schema_version, safe to delete, refills on demand one artist at a time.
ARTIST_IMAGES_DIR = MILO_DATA_DIR / "artist_images"
NAVIDROME_DATA_DIR = MILO_DATA_DIR / "navidrome"   # Navidrome DataFolder (DB, cache)
# Service-account credentials, provisioned once on first boot by
# milo-navidrome-provision (milo-owned, 0600). Never in settings.json or WS.
NAVIDROME_CRED_FILE = NAVIDROME_DATA_DIR / "milo-service.cred"
NAVIDROME_HOST = "127.0.0.1"
NAVIDROME_PORT = 4533
NAVIDROME_URL = f"http://{NAVIDROME_HOST}:{NAVIDROME_PORT}"

# Privileged storage helpers (pinned sudoers, milo-* doctrine). A USB key is
# detected unprivileged via pyudev, then mounted read-only under MUSIC_LIBRARY_MOUNT_ROOT
# by these helpers — the backend never calls mount/umount directly. milo-mount also
# mounts SMB/NFS network shares (`--network`) and writes/removes their root-only
# credential files (`--forget`); the secret bytes are fed on stdin, never argv. See
# rootfs/usr/local/bin/milo-mount.
MILO_MOUNT_CMD = "/usr/local/bin/milo-mount"
MILO_UMOUNT_CMD = "/usr/local/bin/milo-umount"

# "prev" button: past this many seconds into a track, prev restarts the current
# track instead of stepping to the previous one. Mirrors Spotify/go-librespot's
# standard ~3s rewind-vs-skip threshold (go-librespot's exact value isn't
# exposed in-repo, so we replicate the well-known 3s behavior).
CD_PREV_RESTART_THRESHOLD_S = 3.0

# =============================================================================
# SYSTEM SCRIPTS
# =============================================================================
DEPLOY_UPDATE_CMD = "/usr/local/bin/milo-deploy-update"

# =============================================================================
# NETWORK PORTS
# =============================================================================
CLIENT_API_PORT = 8001          # Milo-client API port (equalizer, health, etc.)

# =============================================================================
# PODCAST INDEX API (app-level credentials)
# =============================================================================
# Single key pair shared by every Milō unit — Podcast Index is free and
# unlimited, so no per-user credentials or quota handling. Deliberately
# embedded (locked decision in docs/podcast-podcastindex-migration.md);
# extraction is low-stakes: the key is revocable and rotated here if needed.
PODCASTINDEX_API_KEY = "7XCFZWYVUFR3MTG6GWXE"
PODCASTINDEX_API_SECRET = "uscKuHPtaWw^hFmpzvDKQZud4cwH7TUkyKh9fuN2"

# =============================================================================
# TIMEOUTS (in seconds)
# =============================================================================
HEALTH_CHECK_TIMEOUT = 2.0      # Timeout for health checks

# =============================================================================
# MAC ROC STREAMING
# =============================================================================
MAC_RTP_PORT = 10001
MAC_RS8M_PORT = 10002
MAC_RTCP_PORT = 10003
MAC_AUDIO_OUTPUT = "hw:1,0"

# =============================================================================
# HARDWARE GPIO (BCM numbering)
# =============================================================================
# General-purpose BCM GPIO pins exposed on the Raspberry Pi 40-pin header
# (GPIO 0/1 are reserved for the HAT EEPROM ID line). Single source of truth
# for the rotary-encoder / IR-remote pin validators (api/models.py) AND the pin
# dropdown options served to the frontend (GET /api/settings/hardware-config),
# so the selectable range can never drift between backend and frontend.
GPIO_MIN_PIN = 2
GPIO_MAX_PIN = 27
SELECTABLE_GPIO_PINS = list(range(GPIO_MIN_PIN, GPIO_MAX_PIN + 1))

# =============================================================================
# BT REMOTE KEY MAP
# =============================================================================
# What a keycode may be mapped to. Declared here rather than in bt_remote.py so
# the request validator (api/models.py) and the dispatcher share one list: an
# action the dispatcher does not know is a key that does nothing, and a
# *non-numeric keycode* used to be worse than that — `int(k)` over the whole map
# raised inside device matching, which caught it at debug and left every remote
# silently unmatched.
BT_REMOTE_ACTIONS = frozenset({'volume_up', 'volume_down', 'click'})

# =============================================================================
# VOLUME SETTINGS (in dB)
# =============================================================================
DEFAULT_VOLUME_DB = -45.0       # Default volume for new clients and startup
MIN_VOLUME_DB = -80.0           # Technical minimum (silent)
MAX_VOLUME_DB = 0.0             # Technical maximum

# =============================================================================
# DOCK APPS & AUDIO SOURCES
# =============================================================================
# Derived from AudioSource enum — always in sync
AUDIO_SOURCE_APPS = frozenset(
    s.value for s in AudioSource if s != AudioSource.NONE
)

# Non-audio dock apps
UTILITY_DOCK_APPS = frozenset({'equalizer', 'multiroom', 'lyrics', 'settings'})

# All valid dock apps
VALID_DOCK_APPS = AUDIO_SOURCE_APPS | UTILITY_DOCK_APPS

# Default dock apps (ordered for UI)
DEFAULT_DOCK_APPS = ["spotify", "bluetooth", "airplay", "music_library", "radio", "cd", "qobuz", "tidal", "podcast", "dlna", "mac", "equalizer", "multiroom", "lyrics", "settings"]

# Supported UI languages (single source of truth for validation)
VALID_LANGUAGES = ['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese', 'italian', 'german']

# =============================================================================
# MAC / ROC STREAMING (the `mac` settings section → /var/lib/milo/mac.env)
# =============================================================================
# The Literal is the declaration; the frozensets are derived from it, so the
# request model's accepted values and the settings validator's accepted values
# cannot drift apart.
ROC_LATENCY_PROFILES = Literal['responsive', 'gradual', 'intact']
ROC_FRAME_LENGTHS = Literal[2, 4, 6, 8, 10, 12]
ALLOWED_LATENCY_PROFILES = frozenset(get_args(ROC_LATENCY_PROFILES))
ALLOWED_FRAME_LENGTHS = frozenset(get_args(ROC_FRAME_LENGTHS))

DEFAULT_ROC_CONFIG = {
    "target_latency_ms": 50,
    "latency_profile": "responsive",
    "frame_length_ms": 4,
}

# =============================================================================
# MULTIROOM CLIENT DISPLAY NAMES
# =============================================================================
# Maps system hostnames to user-friendly display names for multiroom clients.
# Used as fallback when no user-configured name exists in Snapcast.
HOSTNAME_DISPLAY_NAMES = {
    "milo": "Milō",
    "milo-client": "Milō Client",
}


def get_client_display_name(hostname: str) -> str:
    """Return user-friendly display name for a hostname, or the hostname itself."""
    return HOSTNAME_DISPLAY_NAMES.get(hostname, hostname)
