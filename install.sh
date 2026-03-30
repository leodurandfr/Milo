#!/bin/bash
# Milo Audio System - Installation Script v2.0
#
# Fully non-interactive: installs all dependencies for all hardware combinations.
# Hardware configuration (audio card, screen) is done via the web UI setup wizard
# after first boot at http://milo.local.
#
# IMPORTANT: This script is optimized for Raspberry Pi OS Lite (64-bit)
# Download Raspberry Pi OS Lite from: https://www.raspberrypi.com/software/operating-systems/

set -e

MILO_USER="milo"
MILO_HOME="/home/$MILO_USER"
MILO_APP_DIR="$MILO_HOME/milo"
MILO_DATA_DIR="/var/lib/milo"
MILO_REPO="https://github.com/leodurandfr/Milo.git"
MILO_BRANCH="main"
REQUIRED_HOSTNAME="milo"

# --- Source all install modules ---
INSTALL_DIR="$(dirname "$0")/install"

source "$INSTALL_DIR/common.sh"
source "$INSTALL_DIR/base.sh"
source "$INSTALL_DIR/go-librespot.sh"
source "$INSTALL_DIR/roc-toolkit.sh"
source "$INSTALL_DIR/bluez-alsa.sh"
source "$INSTALL_DIR/bluez-le.sh"
source "$INSTALL_DIR/airplay.sh"
source "$INSTALL_DIR/snapcast.sh"
source "$INSTALL_DIR/camilladsp.sh"
source "$INSTALL_DIR/alsa.sh"
source "$INSTALL_DIR/network.sh"
source "$INSTALL_DIR/display.sh"
source "$INSTALL_DIR/system.sh"
source "$INSTALL_DIR/uninstall.sh"

# --- Orchestrator functions ---

show_banner() {
    echo -e "${BLUE}"
    echo "  __  __ _ _       "
    echo " |  \/  (_) | ___  "
    echo " | |\/| | | |/ _ \ "
    echo " | |  | | | | (_) |"
    echo " |_|  |_|_|_|\___/ "
    echo ""
    echo "Audio System Installation Script v2.0"
    echo -e "${NC}"
}

check_root() {
    if [[ $EUID -eq 0 ]]; then
        log_error "This script must not be run as root."
        exit 1
    fi
}

finalize_installation() {
    log_info "Finalizing installation..."

    echo ""
    echo -e "${GREEN}=================================${NC}"
    echo -e "${GREEN}   Milo Installation Complete!   ${NC}"
    echo -e "${GREEN}=================================${NC}"
    echo ""
    echo -e "  ${BLUE}Next steps:${NC}"
    echo "    1. Open http://milo.local"
    echo "    2. Follow the setup wizard to configure your hardware"
    echo ""

    log_info "Rebooting in 5 seconds..."
    sleep 5
    sudo reboot
}

# --- Main installation sequence ---

main() {
    show_banner

    if [[ "$1" == "--uninstall" ]]; then
        uninstall_milo
        exit 0
    fi

    check_root

    log_info "Starting Milo Audio System installation (fully non-interactive)"
    echo ""

    check_system

    # Base system setup
    install_dependencies
    setup_hostname
    create_milo_user
    install_milo_application
    fix_nginx_permissions
    suppress_pulseaudio

    # Audio source components
    install_go_librespot
    install_roc_toolkit
    install_bluez_alsa
    configure_bluez_le
    install_nqptp
    install_shairport_sync
    configure_shairport_sync
    install_snapcast

    # System configuration
    install_readiness_script
    install_apply_hardware_script
    install_polkit_rules
    create_systemd_services
    configure_journald
    install_udev_rules

    # Audio routing
    configure_alsa_loopback
    install_camilladsp
    configure_alsa_complete
    configure_snapserver

    # Hardware
    configure_fan_control

    # Network & web
    install_seatd
    install_avahi_nginx
    configure_avahi
    configure_nginx

    # Display & kiosk
    configure_cage_kiosk
    install_milo_cursor_theme
    configure_plymouth_splash
    disable_lightdm
    configure_silent_login
    optimize_boot_performance

    # Screen & hardware config
    install_screen_brightness_control
    save_hardware_config

    # Cleanup non-interactive apt overrides (must happen after all apt calls)
    sudo rm -f /etc/apt/apt.conf.d/local

    # Finalize
    enable_services
    finalize_installation
}

main "$@"
