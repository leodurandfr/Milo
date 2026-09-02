#!/bin/bash
# Milo - Shared install helpers
#
# Sourced first by every pi-gen stage block and by the two systemd units that
# reuse an install/ function at run time, so the modules they source find the
# log helpers and the pinned dependency versions already defined.

# The validated dependency set. Sourced here rather than in each module so
# every consumer gets the same numbers from the same file — the versions
# below are read, never declared. See dependencies.env.
source "$(dirname "${BASH_SOURCE[0]}")/../dependencies.env"

# ============================================================================
# Colour codes
# ============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ============================================================================
# Log helpers
# ============================================================================

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
