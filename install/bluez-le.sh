#!/bin/bash
# Milo - BlueZ LE Connection Parameters
#
# Configures Bluetooth Low Energy connection parameters in /etc/bluetooth/main.conf
# to reduce power consumption of BLE HID peripherals (e.g. ANTICATER VK-01 remote).
#
# The key change is ConnectionLatency=10, which allows BLE peripherals to skip
# up to 10 connection intervals when idle — reducing wake-ups from ~27/sec to ~2.5/sec
# while preserving the same 30-50ms responsiveness during active use.
#
# Only affects the [LE] section — Bluetooth Classic (A2DP audio) is not impacted.
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

configure_bluez_le() {
    log_info "Configuring BlueZ LE connection parameters..."

    local conf="/etc/bluetooth/main.conf"

    if [[ ! -f "$conf" ]]; then
        log_warning "BlueZ config not found at $conf — skipping LE configuration"
        return 0
    fi

    # MinConnectionInterval=24 (30ms) — make hardware default explicit
    sudo sed -i 's/^#\?MinConnectionInterval\s*=.*/MinConnectionInterval=24/' "$conf"

    # MaxConnectionInterval=40 (50ms) — make hardware default explicit
    sudo sed -i 's/^#\?MaxConnectionInterval\s*=.*/MaxConnectionInterval=40/' "$conf"

    # ConnectionLatency=10 — allow peripheral to skip up to 10 intervals when idle
    sudo sed -i 's/^#\?ConnectionLatency\s*=.*/ConnectionLatency=10/' "$conf"

    # ConnectionSupervisionTimeout=600 (6s) — time before link is declared lost
    sudo sed -i 's/^#\?ConnectionSupervisionTimeout\s*=.*/ConnectionSupervisionTimeout=600/' "$conf"

    sudo systemctl restart bluetooth || true

    log_success "BlueZ LE parameters configured (latency=10, supervision=6s)"
}

# Run if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    configure_bluez_le
fi
