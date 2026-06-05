#!/bin/bash
# Milo - Software power button support (status LED + power-on behaviour)
#
# The momentary button itself wires to the Pi 5 J2 header and needs NO software
# (native power-button behaviour: short press = clean shutdown, press-when-off = boot).
# This module configures two things:
#   1. The button's status LED: its cathode is sinked by GPIO26, so config.txt
#      drives GPIO26 as an output held low while the Pi runs. When the Pi halts,
#      the RP1 GPIO pad goes high-impedance and the LED turns off.
#   2. The bootloader EEPROM so the board waits for a power-button press instead
#      of booting automatically when power is applied (PC-like behaviour).
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

configure_power_on_behavior() {
    # Pi 4/5 only: make the board wait for a power-button press instead of
    # booting automatically when power is applied (POWER_OFF_ON_HALT=1 puts the
    # PMIC in STANDBY on halt; WAIT_FOR_POWER_BUTTON=1 holds off boot on power-up
    # until the button is pressed — Raspberry Pi 5 only).
    if ! command -v rpi-eeprom-config >/dev/null 2>&1; then
        log_warning "rpi-eeprom-config not found — skipping power-on behaviour"
        return 0
    fi

    # The bootloader EEPROM lives on the board's SPI flash, NOT on the SD card,
    # so this must run on real hardware (e.g. first boot) — not inside an
    # image-build chroot (pi-gen), where there is no EEPROM/VideoCore to read or
    # flash. Skip gracefully there so the build never aborts.
    if ! vcgencmd bootloader_version >/dev/null 2>&1; then
        log_warning "No on-board EEPROM access (image-build chroot?) — skipping power-on behaviour; run on the device"
        return 0
    fi

    log_info "Configuring power-on behaviour (wait for power button)..."

    local conf
    conf="$(mktemp)"
    rpi-eeprom-config > "$conf"

    if grep -q '^POWER_OFF_ON_HALT=' "$conf"; then
        sed -i 's/^POWER_OFF_ON_HALT=.*/POWER_OFF_ON_HALT=1/' "$conf"
    else
        echo "POWER_OFF_ON_HALT=1" >> "$conf"
    fi
    if grep -q '^WAIT_FOR_POWER_BUTTON=' "$conf"; then
        sed -i 's/^WAIT_FOR_POWER_BUTTON=.*/WAIT_FOR_POWER_BUTTON=1/' "$conf"
    else
        echo "WAIT_FOR_POWER_BUTTON=1" >> "$conf"
    fi

    # Apply to the *currently installed* bootloader image so the firmware version
    # is left untouched (config-only change). Fall back to the latest image if
    # the current one is no longer on disk.
    local cur_date cur_image
    cur_date="$(vcgencmd bootloader_version 2>/dev/null | head -1 | sed 's#/#-#g; s/ .*//')"
    cur_image="$(ls /lib/firmware/raspberrypi/bootloader-2712/*/pieeprom-"${cur_date}".bin 2>/dev/null | head -1)"

    if [[ -n "$cur_image" ]]; then
        sudo rpi-eeprom-config --apply "$conf" "$cur_image" > /dev/null
        log_success "Power-on behaviour set (wait for power button); bootloader firmware unchanged ($cur_date)"
    else
        log_warning "Current bootloader image not on disk — applying with latest (firmware may update)"
        sudo rpi-eeprom-config --apply "$conf" > /dev/null
        log_success "Power-on behaviour set (wait for power button)"
    fi

    rm -f "$conf"
}

install_power_button() {
    configure_power_led
    configure_power_on_behavior
    log_success "Software power button support installed (J2 button + LED on GPIO${POWER_LED_GPIO_DEFAULT})"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_power_button
fi
