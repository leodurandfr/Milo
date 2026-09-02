#!/bin/bash
# Milo - Fan PWM control
#
# Appends the Pi 5 cooling-fan curve to config.txt. The rest of the system
# configuration (udev rules, sudoers, polkit, the systemd unit copy, the boot
# optimisations) is done by pi-gen/stage-milo/03-configure directly.
#
# Sourced by pi-gen/stage-milo during the image build.

set -e

MILO_USER="${MILO_USER:-milo}"

configure_fan_control() {
    log_info "Configuring fan control..."

    local config_file="/boot/firmware/config.txt"

    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
    fi

    if ! grep -q "cooling_fan=on" "$config_file"; then
        echo "" | sudo tee -a "$config_file"
        echo "# Milo - Fan PWM Control (4 paliers, audio-first, quiet curve)" | sudo tee -a "$config_file"
        echo "dtparam=cooling_fan=on" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0=66000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0_speed=55" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1=79000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1_speed=120" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2=81000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2_speed=200" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3=82000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3_speed=255" | sudo tee -a "$config_file"
    fi

   log_success "Fan control configured"
}

# Note: hardware.json is intentionally NOT seeded here. The backend creates it
# (save_versioned_json, always stamping the current schema_version) when the user
# picks hardware in the setup wizard. A bash-seeded file cannot track the schema
# and shipped a stale, unversioned file that crash-looped the backend with
# SchemaVersionMismatch. Absent file → backend uses in-code defaults
# (see backend/hardware/registry.py).

