#!/bin/bash
# Common boot configuration shared by all screens
# Sourced by install.sh to configure cmdline.txt and config.txt

# Universal cmdline.txt parameters
BOOT_PARAMS_COMMON="quiet splash plymouth.ignore-serial-consoles"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON console=tty3 loglevel=0 consoleblank=0"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON logo.nologo vt.global_cursor_default=0"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON fbcon=map:99 vt.handoff=7"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON cfg80211.ieee80211_regdom=00"

# Screen-specific parameters (empty by default, overridden by screen modules)
BOOT_PARAMS_SCREEN=""
CONFIG_PARAMS_SCREEN=""

# Insert the silent-boot directive (disable_splash) once, right after [all].
# Single source of truth used by both install/display.sh and the pi-gen build.
configure_silent_boot() {
    local config_file="/boot/firmware/config.txt"
    [[ ! -f "$config_file" ]] && config_file="/boot/config.txt"
    [[ ! -f "$config_file" ]] && return 0

    if ! grep -q "disable_splash=1" "$config_file"; then
        sudo sed -i '/^\[all\]$/a\\n# Milo - Silent boot\ndisable_splash=1' "$config_file"
    fi
}
