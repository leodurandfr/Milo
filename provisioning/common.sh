#!/bin/bash
# Milo - Shared install helpers
#
# Sourced first by every pi-gen stage block and by the one systemd unit that
# reuses a provisioning/ function at run time (milo-navidrome-config.service), so
# the modules they source find the log helpers and the pinned dependency
# versions already defined. That ordering is a hard requirement, enforced by
# test_provisioning_parity.py.
#
# Every consumer runs as root — pi-gen through on_chroot, the unit with no
# User= — so no module here uses sudo. It carried one on every privileged line
# back when install.sh ran as the milo user.

# The validated dependency set. Sourced here rather than in each module so
# every consumer gets the same numbers from the same file — the versions
# below are read, never declared. See dependencies.env.
source "$(dirname "${BASH_SOURCE[0]}")/../dependencies.env"

# ============================================================================
# Colour codes
# ============================================================================

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

# ============================================================================
# Boot partition
# ============================================================================

# config.txt is on the boot partition, mounted at /boot/firmware since
# Bookworm — the only layout a Milō image has.
boot_config_file() {
    echo /boot/firmware/config.txt
}
