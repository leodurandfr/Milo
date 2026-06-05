#!/bin/bash
# Milo - Software power button support (status LED on GPIO26)
#
# The momentary button itself wires to the Pi 5 J2 header and needs NO software
# (native power-button behaviour: short press = clean shutdown, press-when-off = boot).
# This module only configures the button's status LED: its cathode is sinked by
# GPIO26, so config.txt must drive GPIO26 as an output held low while the Pi runs.
# When the Pi halts, the RP1 GPIO pad goes high-impedance and the LED turns off.
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

# GPIO pin (BCM) sinking the power-button status LED cathode.
POWER_LED_GPIO_DEFAULT=26

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

configure_power_led() {
    log_info "Configuring power-button status LED (GPIO${POWER_LED_GPIO_DEFAULT})..."

    local config_file="/boot/firmware/config.txt"
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
    fi

    # Remove any previous managed block (idempotent re-run safety)
    sudo sed -i '/^# BEGIN MILO POWER LED$/,/^# END MILO POWER LED$/d' "$config_file"

    # gpio=N=op,dl => set GPIO N as output, driven low at boot (sinks the LED).
    sudo tee -a "$config_file" > /dev/null << EOF

# BEGIN MILO POWER LED
gpio=${POWER_LED_GPIO_DEFAULT}=op,dl
# END MILO POWER LED
EOF

    log_success "Power-button status LED configured in $config_file"
}

install_power_button() {
    configure_power_led
    log_success "Software power button support installed (J2 button + LED on GPIO${POWER_LED_GPIO_DEFAULT})"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_power_button
fi
