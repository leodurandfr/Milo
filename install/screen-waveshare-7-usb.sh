#!/bin/bash
# Boot configuration for Waveshare 7" USB (HDMI)
# Sourced by milo-apply-hardware to configure cmdline.txt and config.txt

source "$(dirname "${BASH_SOURCE[0]}")/boot-common.sh"

# HDMI-specific cmdline.txt parameters
BOOT_PARAMS_SCREEN=""

# HDMI-specific config.txt parameters
CONFIG_PARAMS_SCREEN="hdmi_force_hotplug=1
hdmi_blanking=1
hdmi_group=2
hdmi_mode=87
hdmi_cvt=1024 600 60 6 0 0 0
framebuffer_width=1024
framebuffer_height=600"
