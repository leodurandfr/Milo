# backend/config/constants.py
"""
Centralized constants for the Milo backend.
All hardcoded values should be defined here to avoid duplication.
"""
from pathlib import Path
from typing import Dict, Literal, NamedTuple, get_args

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
# The files set aside because they could not be read, until the owner has read
# the banner that says so (core/system/notices.py).
NOTICES_FILE = MILO_DATA_DIR / "notices.json"
# APNs device tokens (widget / push-to-start / Now Playing session), per iOS install.
PUSH_TOKENS_FILE = MILO_DATA_DIR / "push_tokens.json"
# APNs provider signing key, provisioned per device and deliberately NOT baked
# into the pi-gen image: one .p8 signs for every app in the Apple team, so an
# image carrying it would ship that credential on every card. Expected content
# is exactly one AuthKey_<KEYID>.p8 (milo:milo, 0600) — the Key ID lives in the
# filename, which is Apple's own naming and the only copy of it.
APNS_KEY_DIR = MILO_DATA_DIR / "apns"
APNS_TEAM_ID = "7C2S8B9978"
IOS_APP_BUNDLE_ID = "leodurand.Milo-iOS"   # topics derive from the APP, not the widget

# =============================================================================
# DIRECTORIES (derived from MILO_DATA_DIR)
# =============================================================================
CD_DATA_FILE = MILO_DATA_DIR / "cd_data.json"
CD_COVERS_DIR = MILO_DATA_DIR / "cd_covers"
CD_DEVICE = "/dev/sr0"

# Where the kernel lists the network interfaces. Read at call time
# (`constants.SYSFS_NET`), never imported by value: the test suite points it at
# a fixture tree so that nothing depends on the interfaces of the host it runs on.
SYSFS_NET = "/sys/class/net"

# =============================================================================
# MUSIC LIBRARY / NAVIDROME (catalog engine sidecar)
# =============================================================================
# Navidrome runs as a daemon (milo-navidrome.service) indexing the mount root
# and exposing a localhost Subsonic API, for as long as the dock enables Music
# Library — the backend starts and stops it, nothing else does. The
# music_library source talks to it over HTTP — never over the LAN (respects
# "local network only"). Its DataFolder lives under MILO_DATA_DIR, with the
# rest of the unit's durable state.
NAVIDROME_SERVICE = "milo-navidrome.service"
MUSIC_LIBRARY_MOUNT_ROOT = Path("/media/milo")     # Navidrome MusicFolder (mounts appear here)
# Network-share config (SMB/NFS). Non-secret only — id/type/host/path/name.
# The share secrets (username/password/domain) never land here; they live in a
# root-only cred file written by milo-mount (see MILO_MOUNT_CMD --network below).
MUSIC_LIBRARY_DATA_FILE = MILO_DATA_DIR / "music_library_data.json"
# The Spotify accounts that cast to Milō, with go-librespot's stored credentials
# (a secret: 0600 in a 0700 directory).
SPOTIFY_PROFILES_FILE = MILO_DATA_DIR / "spotify" / "profiles.json"
# The tracks milo-spotify started, per account: the queue page's recently played.
SPOTIFY_HISTORY_FILE = MILO_DATA_DIR / "spotify" / "history.json"
# Artist photos Milō resolved from Deezer itself (Navidrome's online tier for
# artist art is off — see artist_images.py). A disposable derived cache: no
# schema_version, safe to delete, refills on demand one artist at a time.
ARTIST_IMAGES_DIR = MILO_DATA_DIR / "artist_images"
# External radio station logos, fetched once through /api/radio/favicon, checked,
# squared and kept (see sources/radio/logos.py). A disposable derived cache: no
# schema_version, safe to delete, refills on demand one logo at a time.
RADIO_LOGOS_DIR = MILO_DATA_DIR / "radio_logos"
# Radio France song covers, fetched through /api/radio/covers/{id} the same way
# and kept apart: one per song, they would otherwise evict the station logos.
RADIO_COVERS_DIR = MILO_DATA_DIR / "radio_covers"
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
# Pinned helpers the backend runs as root. Like every privileged call here,
# each goes through `sudo -n`, never a bare `sudo`, which run from a terminal
# waits on a password prompt instead of failing (held by
# tests/contracts/test_privileged_exec_contract.py::test_every_backend_sudo_is_non_interactive).
# Each path is the one /etc/sudoers.d/milo-backend grants.
DEPLOY_UPDATE_CMD = "/usr/local/bin/milo-deploy-update"
# config.txt and the bootloader EEPROM from hardware.json (setup, Hardware page).
APPLY_HARDWARE_CMD = "/usr/local/bin/milo-apply-hardware"
# The WiFi regulatory domain, in cmdline.txt and for the running kernel.
SET_WIFI_COUNTRY_CMD = "/usr/local/bin/milo-set-wifi-country"

# The two pinned helpers behind Settings > Appareil > Système. Both are
# root-only work the backend must not do inline: setting the account password
# (fed on stdin, never argv — /proc/<pid>/cmdline is world-readable) and
# writing /etc/localtime.
SET_PASSWORD_CMD = "/usr/local/bin/milo-set-password"
SET_TIMEZONE_CMD = "/usr/local/bin/milo-set-timezone"

# Written by milo-set-password, read here only for its existence. Absent means
# the unit still carries the factory password `pi-gen/config` bakes — the same
# on every unit, and published — so `PUT /api/system/ssh` refuses to open SSH
# and `GET /api/system/ssh` reports it (`password_is_default`).
PASSWORD_CHANGED_MARKER = MILO_DATA_DIR / "password-changed"

# A proof of presence by power cut (core/system/presence.py): the challenge
# waiting for one, recorded with the boot it was opened in by a root helper, in
# a directory only root writes — never under MILO_DATA_DIR, which every `milo`
# process can write. milo-backend.service's root ExecStopPost clears it at every
# stop of the backend, so a record found at a start, from another boot, is a
# boot no stop came before. The helper's own copy of the path is held equal to
# this one by tests/architecture/test_presence_record.py.
PRESENCE_RECORD_CMD = "/usr/local/bin/milo-presence-record"
PRESENCE_RECORD_FILE = Path("/var/lib/milo-presence/challenge.json")

# The key every satellite's secret is derived from (core/multiroom/satellite_http.py):
# 32 random bytes in hex, 0600, created at the first start. Durable: a new key is
# a secret no satellite pinned, and every one of them refuses this server's
# update, remote access and reboot until it is unplugged and plugged back in.
SATELLITE_KEY_FILE = MILO_DATA_DIR / "satellite-key"

# Dropped by POST /api/system/reset-setup and consumed by milo-first-boot on the
# next boot, which deletes settings.json and re-runs role detection. The
# deletion belongs there, not here: this process owns settings.json, and any
# write between a delete and the reboot would recreate it with setup_completed
# still true — a reset that silently did nothing.
RESET_SETUP_MARKER = MILO_DATA_DIR / "reset-setup"

# Present while the WiFi radio is on only because the network recovery turned
# it back on over the user's "off". NetworkManager keeps the radio on across a
# backend restart, so the override has to outlive the process the same way, or
# the next one takes the "on" for the user's and never gives the setting back.
WIFI_RADIO_FORCED_FILE = MILO_DATA_DIR / "wifi_radio_forced"

# =============================================================================
# NETWORK PORTS
# =============================================================================
CLIENT_API_PORT = 8001          # Milo-client API port (equalizer, health, etc.)

# =============================================================================
# HARDWARE GPIO (BCM numbering)
# =============================================================================
# General-purpose BCM GPIO pins exposed on the Raspberry Pi 40-pin header
# (GPIO 0/1 are reserved for the HAT EEPROM ID line). Single source of truth
# for the bounds of the rotary-encoder / IR-remote / LED pin validators
# (api/models.py) AND the pin list served to the frontend (GET
# /api/settings/hardware-config). It is the range, not what a unit can use:
# the chosen audio card holds some of these pins (registry.reserved_pins),
# which the validator refuses and the Hardware page drops per card.
GPIO_MIN_PIN = 2
GPIO_MAX_PIN = 27
SELECTABLE_GPIO_PINS = list(range(GPIO_MIN_PIN, GPIO_MAX_PIN + 1))

# =============================================================================
# BT REMOTE KEY MAP
# =============================================================================
# What a keycode may be mapped to: the list the request validator
# (api/models.py) accepts. The dispatcher (bt_remote.py `_monitor_device` /
# `_dispatch_action`) branches on its own literals, and
# `test_every_declared_bt_action_reaches_the_world` holds every action listed
# here to a dispatched effect, since an action the dispatcher does not know is
# a key that does nothing. A *non-numeric keycode* used to be worse than
# that — `int(k)` over the whole map raised inside device matching, which
# caught it at debug and left every remote silently unmatched.
BT_REMOTE_ACTIONS = frozenset({'volume_up', 'volume_down', 'click'})

# =============================================================================
# VOLUME SETTINGS (in dB)
# =============================================================================
DEFAULT_VOLUME_DB = -45.0       # Default volume for new clients and startup
MIN_VOLUME_DB = -80.0           # Technical minimum (silent)
MAX_VOLUME_DB = 0.0             # Technical maximum

# The main-fader level at which CamillaDSP's native Loudness filter stops
# correcting: flat above it, full correction 20 dB below it, linear in between.
# The fader is digital attenuation, not a sound level, so no value is exact for
# every installation; this is the loud end of Milō's default volume range
# (SettingsService.defaults volume.limit_max_db), where no one needs the bass and
# treble lifted. Sent to every satellite with its loudness settings, so it is
# declared once, here.
LOUDNESS_REFERENCE_DB = -20.0

# =============================================================================
# DOCK APPS & AUDIO SOURCES
# =============================================================================
# Derived from AudioSource enum — always in sync
AUDIO_SOURCE_APPS = frozenset(
    s.value for s in AudioSource if s != AudioSource.NONE
)

# Non-audio dock apps
# Held equal to settingsStore.js::DOCK_UTILITY_APPS by tests/architecture/dockUtilities.test.js:
# an id on one side only is refused on save (422) or never offered as a toggle.
UTILITY_DOCK_APPS = frozenset({'volume', 'equalizer', 'multiroom', 'lyrics', 'settings'})

# All valid dock apps
VALID_DOCK_APPS = AUDIO_SOURCE_APPS | UTILITY_DOCK_APPS


class MultiroomStream(NamedTuple):
    """The snapserver stream a source plays on in multiroom."""
    name: str    # snapserver's stream id, what Group.SetStream names
    device: str  # the Loopback capture snapserver reads it from, card by id, never by index
    pcm: str     # the ALSA PCM the source's unit writes, `pcm.<pcm>_multiroom` in asound.conf


# In multiroom each source writes its own Loopback substream and snapserver
# captures each as a named stream; the backend points every group at the stream
# of the source it starts, because the meta stream left alone switches in its
# own order, after `idle_threshold` of silence. snapserver.conf's `source =`
# lines are derived from it (snapcast.py's MULTIROOM_STREAM_SOURCES) and rewritten
# at every backend start, since no OTA ships that file. provisioning/snapcast.sh
# writes the same lines at image build and cannot import this, and asound.conf
# holds the writer's side: tests/architecture/test_multiroom_capture_map.py holds
# all three equal.
MULTIROOM_STREAMS: Dict[AudioSource, MultiroomStream] = {
    AudioSource.BLUETOOTH: MultiroomStream("Bluetooth", "hw:Loopback,1,1", "milo_bluetooth"),
    AudioSource.MAC: MultiroomStream("ROC", "hw:Loopback,1,2", "milo_roc"),
    AudioSource.SPOTIFY: MultiroomStream("Spotify", "hw:Loopback,1,3", "milo_spotify"),
    AudioSource.RADIO: MultiroomStream("Radio", "hw:Loopback,1,4", "milo_radio"),
    AudioSource.PODCAST: MultiroomStream("Podcast", "hw:Loopback,1,5", "milo_podcast"),
    AudioSource.AIRPLAY: MultiroomStream("AirPlay", "hw:Loopback,1,6", "milo_airplay"),
    AudioSource.CD: MultiroomStream("CD", "hw:Loopback,1,7", "milo_cd"),
    AudioSource.QOBUZ: MultiroomStream("Qobuz", "hw:LoopbackDLNA,1,1", "milo_qobuz"),
    AudioSource.MUSIC_LIBRARY: MultiroomStream("MusicLibrary", "hw:LoopbackDLNA,1,2", "milo_music_library"),
    AudioSource.TIDAL: MultiroomStream("Tidal", "hw:LoopbackDLNA,1,3", "milo_tidal"),
}

# Default dock apps (ordered for UI)
DEFAULT_DOCK_APPS = ["spotify", "bluetooth", "airplay", "music_library", "radio", "cd", "qobuz", "tidal", "podcast", "mac", "equalizer", "multiroom", "lyrics", "volume", "settings"]

# Supported UI languages (single source of truth for validation)
VALID_LANGUAGES = ['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese', 'italian', 'german']

# `auto` follows the sun on the kiosk (frontend `useTheme`); every other browser
# follows its own system theme and never reads this setting.
SCREEN_THEMES = Literal['light', 'dark', 'auto']
ALLOWED_SCREEN_THEMES = frozenset(get_args(SCREEN_THEMES))

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
ROC_TARGET_LATENCY_RANGE = (20, 500)

# The sender half, applied by Milo-Mac to its roc-vad device. The FEC scheme is
# not among them: roc-recv's `rtp+rs8m` endpoint fixes it, and a sender on any
# other scheme is refused at connect. Nor is the device buffer: roc-vad drains it
# on every I/O cycle, so it adds no latency, and one smaller than CoreAudio's
# I/O buffer overruns roc-vad's ring — Milo-Mac leaves roc-vad's default.
ROC_PACKET_LENGTHS = Literal[2, 3, 4, 5, 6, 8, 10]
ALLOWED_PACKET_LENGTHS = frozenset(get_args(ROC_PACKET_LENGTHS))
ROC_FEC_SOURCE_RANGE = (10, 50)
ROC_FEC_REPAIR_RANGE = (5, 30)

# What roc-recv reads from mac.env; the other keys are the Mac's.
ROC_RECEIVER_KEYS = ("target_latency_ms", "latency_profile", "frame_length_ms")

DEFAULT_ROC_CONFIG = {
    "target_latency_ms": 50,
    # roc's own rule (latency_tuner.cpp, 0.4.0): responsive under 30 ms only,
    # gradual above, where it absorbs the jitter a higher target allows.
    "latency_profile": "gradual",
    "frame_length_ms": 4,
    "packet_length_ms": 3,
    "fec_block_source": 10,
    "fec_block_repair": 5,
    "packet_interleaving": False,
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

# =============================================================================
# RECOVERY ACCESS POINT
# =============================================================================
# The address the recovery AP (core/network/service.py) pins on wlan0, and the
# subnet its clients get from NetworkManager's `shared` mode. One spelling for the
# AP profile, the request guard (api/middleware.py), sshd's refusal and the
# captive DNS answer (rootfs/etc/NetworkManager/dnsmasq-shared.d/milo-captive.conf).
RECOVERY_AP_ADDRESS = "10.42.0.1"
RECOVERY_AP_PREFIX = 24
RECOVERY_AP_SUBNET = f"10.42.0.0/{RECOVERY_AP_PREFIX}"
# The 403 detail a write refused over the AP answers with. A wire contract
# between two units as well: an adopting server reads it from a speaker that
# refused `become-client` because it is already set up
# (core/multiroom/wifi_adoption.py).
RECOVERY_AP_REFUSAL = "Not available over the recovery access point"
