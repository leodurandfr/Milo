#!/bin/bash
# Configuration boot pour Waveshare 7" USB (HDMI)
# Utilisé par install.sh pour configurer cmdline.txt et config.txt

source "$(dirname "${BASH_SOURCE[0]}")/boot-common.sh"

# Paramètres cmdline.txt spécifiques HDMI
BOOT_PARAMS_SCREEN=""

# Paramètres config.txt spécifiques HDMI
CONFIG_PARAMS_SCREEN="hdmi_force_hotplug=1
hdmi_blanking=1
hdmi_group=2
hdmi_mode=87
hdmi_cvt=1024 600 60 6 0 0 0
framebuffer_width=1024
framebuffer_height=600"
