#!/bin/bash
# Milo - Shared install helpers
#
# Sourced by install.sh and milo-client/install-client.sh to avoid
# duplicating colour codes, log functions, and journald configuration.

# --- Colour codes ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# --- Log helpers ---
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# --- Temp directory cleanup on exit/signal ---
_TEMP_DIRS=()

register_temp_dir() {
    _TEMP_DIRS+=("$1")
}

_cleanup_temp_dirs() {
    for dir in "${_TEMP_DIRS[@]}"; do
        [[ -d "$dir" ]] && rm -rf "$dir" 2>/dev/null || true
    done
}
trap _cleanup_temp_dirs EXIT

# --- Journald configuration ---
configure_journald() {
    log_info "Configuring journald limits..."

    sudo sed -i 's/^#\?RuntimeMaxUse=.*/RuntimeMaxUse=100M/' /etc/systemd/journald.conf
    sudo sed -i 's/^#\?MaxRetentionSec=.*/MaxRetentionSec=7d/' /etc/systemd/journald.conf

    log_success "Journald configured (100MB max, 7 days retention)"
}
