#!/bin/bash
# Configuration boot pour Waveshare 8" DSI (1280x800)
# Utilisé par install.sh pour configurer cmdline.txt et config.txt

source "$(dirname "${BASH_SOURCE[0]}")/boot-common.sh"

# Paramètres cmdline.txt spécifiques DSI (aucun)
BOOT_PARAMS_SCREEN=""

# Paramètres config.txt spécifiques DSI
CONFIG_PARAMS_SCREEN="dtoverlay=vc4-kms-dsi-waveshare-panel,8_0_inch"
