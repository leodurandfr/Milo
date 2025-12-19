# backend/config/constants.py
"""
Centralized constants for the Milo backend.
All hardcoded values should be defined here to avoid duplication.
"""
from pathlib import Path

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
CLIENT_DSP_FILE = MILO_DATA_DIR / "client_dsp.json"
ROUTING_ENV_FILE = MILO_DATA_DIR / "routing.env"

# =============================================================================
# DIRECTORIES (derived from MILO_DATA_DIR)
# =============================================================================
RADIO_IMAGES_DIR = MILO_DATA_DIR / "radio_images"
CAMILLADSP_CONFIG_DIR = MILO_DATA_DIR / "camilladsp"
BACKUPS_DIR = MILO_DATA_DIR / "backups"
GO_LIBRESPOT_CONFIG_DIR = MILO_DATA_DIR / "go-librespot"

# =============================================================================
# NETWORK PORTS
# =============================================================================
CLIENT_API_PORT = 8001          # Milo-client API port (DSP, health, etc.)
SNAPCAST_PORT = 1780            # Snapcast JSON-RPC port
CAMILLADSP_PORT = 1234          # CamillaDSP WebSocket port
SPOTIFY_PORT = 3678             # go-librespot API port

# =============================================================================
# TIMEOUTS (in seconds)
# =============================================================================
CLIENT_REQUEST_TIMEOUT = 2.0    # Timeout for requests to milo-client
DSP_LEVELS_TIMEOUT = 1.0        # Timeout for DSP level polling
HEALTH_CHECK_TIMEOUT = 2.0      # Timeout for health checks

# =============================================================================
# CAMILLADSP SETTINGS
# =============================================================================
CAMILLADSP_RECONNECT_DELAY = 5  # Seconds between reconnection attempts
CAMILLADSP_COMMAND_TIMEOUT = 5  # Timeout for CamillaDSP commands

# =============================================================================
# MULTIROOM SETTINGS
# =============================================================================
SNAPCAST_CACHE_MS = 5000        # Client cache duration in milliseconds
