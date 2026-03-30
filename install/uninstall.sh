#!/bin/bash
# Milo - Uninstallation
#
# Removes Milo services, configurations, application files,
# and optionally restores the default hostname.
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

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

# Run standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    uninstall_milo
fi
