#!/bin/bash
# Milo - Fan PWM control and Ethernet EEE
#
# Appends the Pi 5 cooling-fan curve and the Ethernet EEE switch-off to
# config.txt. The rest of the system configuration (udev rules, sudoers, polkit,
# the systemd unit copy, the boot optimisations) is done by
# pi-gen/stage-milo/03-configure directly.
#
# Sourced by pi-gen/stage-milo during the image build.

set -e

MILO_USER="${MILO_USER:-milo}"

boot_config_file() {
    if [[ -f /boot/firmware/config.txt ]]; then
        echo /boot/firmware/config.txt
    else
        echo /boot/config.txt
    fi
}

configure_fan_control() {
    log_info "Configuring fan control..."

    local config_file
    config_file=$(boot_config_file)

    if ! grep -q "cooling_fan=on" "$config_file"; then
        echo "" | tee -a "$config_file"
        echo "# Milo - Fan PWM Control (4 paliers, audio-first, quiet curve)" | tee -a "$config_file"
        echo "dtparam=cooling_fan=on" | tee -a "$config_file"
        echo "dtparam=fan_temp0=66000" | tee -a "$config_file"
        echo "dtparam=fan_temp0_hyst=2500" | tee -a "$config_file"
        echo "dtparam=fan_temp0_speed=55" | tee -a "$config_file"
        echo "dtparam=fan_temp1=79000" | tee -a "$config_file"
        echo "dtparam=fan_temp1_hyst=2500" | tee -a "$config_file"
        echo "dtparam=fan_temp1_speed=120" | tee -a "$config_file"
        echo "dtparam=fan_temp2=81000" | tee -a "$config_file"
        echo "dtparam=fan_temp2_hyst=2500" | tee -a "$config_file"
        echo "dtparam=fan_temp2_speed=200" | tee -a "$config_file"
        echo "dtparam=fan_temp3=82000" | tee -a "$config_file"
        echo "dtparam=fan_temp3_hyst=2500" | tee -a "$config_file"
        echo "dtparam=fan_temp3_speed=255" | tee -a "$config_file"
    fi

   log_success "Fan control configured"
}

# Energy Efficient Ethernet off from the first negotiation, by the firmware's own
# parameter: the board's device tree maps it to `eee-broken-1000t`/`-100tx` on
# the PHY (Pi 5, current Pi 4 trees) or to `genet.eee=N` on the kernel command
# line (older Pi 4 trees), and either way the PHY never advertises EEE, whose
# LPI wake-ups drop Snapcast chunks. Nothing has to switch it off at runtime —
# which renegotiates a link already up (measured on a Pi 5: 3.1 s down at every
# boot, DHCP restarted, milo.local briefly on WiFi).
# `[all]` leaves any model filter the file ends in, and the empty `dtoverlay=`
# ends the last overlay's scope, so the parameter reaches the base device tree.
configure_ethernet_eee() {
    log_info "Turning Ethernet EEE off..."

    local config_file
    config_file=$(boot_config_file)

    if ! grep -q "^dtparam=eee=off" "$config_file"; then
        cat >> "$config_file" << 'EOF'

# Milo - Ethernet EEE off (Snapcast drops on LPI wake-up)
[all]
dtoverlay=
dtparam=eee=off
EOF
    fi

    log_success "Ethernet EEE off"
}

# Note: hardware.json is intentionally NOT seeded here. The backend creates it
# (save_versioned_json, always stamping the current schema_version) when the user
# picks hardware in the setup wizard. A bash-seeded file cannot track the schema
# and shipped a stale, unversioned file that crash-looped the backend with
# SchemaVersionMismatch. Absent file → backend uses in-code defaults
# (see backend/hardware/registry.py).

