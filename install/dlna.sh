#!/bin/bash
# Milo - DLNA / UPnP Media Renderer Installation (gmediarender)
#
# Installs gmediarender-resurrect (UPnP/DLNA Digital Media Renderer, DMR role)
# and the GStreamer plugins it needs for Milo's codec coverage (FLAC/ALAC/AAC/
# WAV/MP3, incl. hi-res 24/192). gmediarender ships as an apt binary on Debian
# Trixie (/usr/bin/gmediarender) — no build step. libav is required for ALAC.
#
# Can be sourced from install.sh or run standalone.

set -e

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_gmediarender() {
    log_info "Installing gmediarender (DLNA renderer) + GStreamer plugins..."

    sudo apt-get install -y \
        gmediarender \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good \
        gstreamer1.0-plugins-bad \
        gstreamer1.0-alsa \
        gstreamer1.0-libav

    # Disable the packaged default service (Milo manages its own milo-dlna.service)
    sudo systemctl stop gmediarender.service 2>/dev/null || true
    sudo systemctl disable gmediarender.service 2>/dev/null || true

    log_success "gmediarender (DLNA renderer) installed"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_gmediarender
    log_success "DLNA installation complete"
fi
