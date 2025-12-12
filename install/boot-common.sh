#!/bin/bash
# Configuration boot commune à tous les écrans
# Utilisé par install.sh pour configurer cmdline.txt et config.txt

# Paramètres cmdline.txt universels
BOOT_PARAMS_COMMON="quiet splash plymouth.ignore-serial-consoles"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON console=tty3 loglevel=0 consoleblank=0"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON logo.nologo vt.global_cursor_default=0"
BOOT_PARAMS_COMMON="$BOOT_PARAMS_COMMON fbcon=map:99 vt.handoff=7"

# Paramètres config.txt universels
CONFIG_PARAMS_COMMON="disable_splash=1"

# Paramètres spécifiques à l'écran (vide par défaut)
BOOT_PARAMS_SCREEN=""
CONFIG_PARAMS_SCREEN=""
