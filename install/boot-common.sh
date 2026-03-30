#!/bin/bash
# Common boot configuration shared by all screens
# Sourced by install.sh to configure cmdline.txt and config.txt

# Universal cmdline.txt parameters
BOOT_PARAMS_COMMON="quiet splash plymouth.ignore-serial-consoles"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON console=tty3 loglevel=0 consoleblank=0"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON logo.nologo vt.global_cursor_default=0"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON fbcon=map:99 vt.handoff=7"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON cfg80211.ieee80211_regdom=00"

# Universal config.txt parameters
CONFIG_PARAMS_COMMON="disable_splash=1"

# Screen-specific parameters (empty by default, overridden by screen modules)
BOOT_PARAMS_SCREEN=""
CONFIG_PARAMS_SCREEN=""
