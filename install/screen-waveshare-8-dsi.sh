#!/bin/bash
# Boot configuration for Waveshare 8" DSI (1280x800)
# Sourced by milo-apply-hardware to configure cmdline.txt and config.txt

source "$(dirname "${BASH_SOURCE[0]}")/boot-common.sh"

# DSI-specific cmdline.txt parameters (none)
BOOT_PARAMS_SCREEN=""

# DSI-specific config.txt parameters
CONFIG_PARAMS_SCREEN="dtoverlay=vc4-kms-dsi-waveshare-panel,8_0_inch"
