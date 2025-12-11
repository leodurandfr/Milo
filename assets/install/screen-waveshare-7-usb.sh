#!/bin/bash
# Configuration boot pour Waveshare 7" USB (HDMI)
# Utilisé par install.sh pour configurer cmdline.txt et config.txt

source "$(dirname "${BASH_SOURCE[0]}")/boot-common.sh"

# Paramètres cmdline.txt spécifiques HDMI
BOOT_PARAMS_SCREEN="video=HDMI-A-1:1024x600@60D"

# Paramètres config.txt spécifiques HDMI
CONFIG_PARAMS_SCREEN="hdmi_force_hotplug=1
hdmi_safe=0"
