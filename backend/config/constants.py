# backend/config/constants.py
"""
Centralized constants for the Milo backend.
All hardcoded values should be defined here to avoid duplication.
"""
from pathlib import Path
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
LAST_VOLUME_FILE = MILO_DATA_DIR / "last_volume.json"
RADIO_DATA_FILE = MILO_DATA_DIR / "radio_data.json"
PODCAST_DATA_FILE = MILO_DATA_DIR / "podcast_data.json"
CLIENT_EQUALIZER_FILE = MILO_DATA_DIR / "client_equalizer.json"
ROUTING_ENV_FILE = MILO_DATA_DIR / "routing.env"
ERROR_LOG_FILE = MILO_DATA_DIR / "errors.log"

# =============================================================================
# DIRECTORIES (derived from MILO_DATA_DIR)
# =============================================================================
RADIO_IMAGES_DIR = MILO_DATA_DIR / "radio_images"

# =============================================================================
# NETWORK PORTS
# =============================================================================
CLIENT_API_PORT = 8001          # Milo-client API port (equalizer, health, etc.)

# =============================================================================
# TIMEOUTS (in seconds)
# =============================================================================
CLIENT_REQUEST_TIMEOUT = 2.0    # Timeout for requests to milo-client
HEALTH_CHECK_TIMEOUT = 2.0      # Timeout for health checks

# =============================================================================
# MAC ROC STREAMING
# =============================================================================
MAC_RTP_PORT = 10001
MAC_RS8M_PORT = 10002
MAC_RTCP_PORT = 10003
MAC_AUDIO_OUTPUT = "hw:1,0"

# =============================================================================
# VOLUME SETTINGS (in dB)
# =============================================================================
DEFAULT_VOLUME_DB = -60.0       # Default volume for new clients and startup
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
UTILITY_DOCK_APPS = frozenset({'equalizer', 'multiroom', 'settings'})

# All valid dock apps
VALID_DOCK_APPS = AUDIO_SOURCE_APPS | UTILITY_DOCK_APPS

# Default dock apps (ordered for UI)
DEFAULT_DOCK_APPS = ["spotify", "bluetooth", "radio", "podcast", "airplay", "mac", "equalizer", "multiroom", "settings"]

# Supported UI languages (single source of truth for validation)
VALID_LANGUAGES = ['french', 'english', 'spanish', 'hindi', 'chinese', 'portuguese', 'italian', 'german']
