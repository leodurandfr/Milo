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

check_system() {
    log_info "Checking system..."

    if ! grep -q "Raspberry Pi" /proc/cpuinfo; then
        log_error "This script is designed for Raspberry Pi only."
        exit 1
    fi

    ARCH=$(uname -m)
    if [[ "$ARCH" != "aarch64" ]]; then
        log_error "Unsupported architecture: $ARCH. Raspberry Pi OS 64bit required."
        exit 1
    fi

    # Warning if a desktop environment is detected
    if systemctl list-units --type=service | grep -qE "lightdm|gdm|sddm|xdm"; then
        log_warning "A desktop environment has been detected."
        log_warning "Raspberry Pi OS Lite is recommended for optimal performance."
        echo ""
    fi

    log_success "Compatible system detected (Raspberry Pi OS 64-bit)"
}

enable_services() {
   log_info "Enabling automatic service startup..."

   sudo systemctl daemon-reload

   # Configure graphical.target as default target
   # Necessary for milo-kiosk.service to start (WantedBy=graphical.target)
   # On Raspberry Pi OS Lite, the system boots to multi-user.target by default
   local current_target
   current_target=$(systemctl get-default)
   if [[ "$current_target" != "graphical.target" ]]; then
       log_info "Configuring system to boot to graphical.target (required for milo-kiosk)..."
       sudo systemctl set-default graphical.target
       log_success "Default target configured: graphical.target"
   else
       log_info "Default target already configured: graphical.target"
   fi

   # Services that should be enabled at boot
   sudo systemctl enable milo-backend.service
   sudo systemctl enable milo-readiness.service
   sudo systemctl enable milo-kiosk.service
   sudo systemctl enable milo-bluealsa.service
   sudo systemctl enable milo-bluealsa-aplay.service
   sudo systemctl enable milo-disable-wifi-power-management.service
   sudo systemctl enable milo-camilladsp.service
   sudo systemctl enable avahi-daemon
   sudo systemctl enable nginx

   # Note: milo-frontend.service is no longer used (nginx serves /dist directly)
   # Note: getty@tty1 is masked (milo-kiosk.service takes control of tty1)

   # Note: The following services are managed dynamically by the Milo backend:
   # - milo-spotify.service
   # - milo-mac.service
   # - milo-radio.service
   # - milo-airplay.service
   # - milo-cd.service
   # - milo-snapserver-multiroom.service
   # - milo-snapclient-multiroom.service
   # These services should NOT be "enabled" at boot

   log_success "Automatic startup configured"
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

uninstall_milo() {
   log_warning "Starting Milo uninstallation..."
   echo ""
   read -p "Are you sure you want to uninstall Milo? (y/N): " confirm
   case $confirm in
       [Yy]* )
           ;;
       * )
           log_info "Uninstallation cancelled"
           exit 0
           ;;
   esac

   log_info "Stopping services..."
   sudo systemctl stop milo-*.service || true
   sudo systemctl disable milo-*.service || true

   log_info "Removing systemd services..."
   sudo rm -f /etc/systemd/system/milo-*.service
   sudo systemctl daemon-reload

   log_info "Removing configurations..."
   sudo rm -f /etc/nginx/sites-enabled/milo
   sudo rm -f /etc/nginx/sites-available/milo
   sudo rm -f /etc/snapserver.conf
   sudo rm -f /etc/shairport-sync.conf
   sudo rm -f /etc/dbus-1/system.d/shairport-sync-dbus.conf
   sudo rm -f /etc/asound.conf
   sudo rm -f /etc/modules-load.d/snd-aloop.conf
   sudo rm -f /etc/modprobe.d/snd-aloop.conf

   log_info "Removing application..."
   sudo rm -rf "$MILO_APP_DIR"
   sudo rm -rf "$MILO_DATA_DIR"

   log_info "Removing Milo themes..."
   sudo rm -rf /usr/share/icons/Milo
   sudo rm -rf /usr/share/plymouth/themes/milo

   log_info "Removing binaries..."
   sudo rm -f /usr/local/bin/go-librespot
   sudo rm -f /usr/local/bin/milo-brightness-7

   log_info "Cleaning up packages..."
   sudo apt autoremove -y

   read -p "Restore default hostname 'raspberrypi'? (y/N): " restore_hostname
   case $restore_hostname in
       [Yy]* )
           configure_hostname "raspberrypi"
           log_info "Hostname restored"
           ;;
   esac

   log_info "Restarting system services..."
   sudo systemctl restart nginx avahi-daemon || true

   log_success "Uninstallation complete!"
   echo ""
   log_warning "Note: User '$MILO_USER' was not removed"
   log_warning "Note: Modifications to /boot/firmware/config.txt are preserved"
   echo ""
   read -p "Reboot now? (y/N): " restart_now
   case $restart_now in
       [Yy]* )
           sudo reboot
           ;;
   esac
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
