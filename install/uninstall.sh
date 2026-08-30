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
    # The Avahi drop-in is not matched by the milo-*.service glob above, and it
    # runs /usr/local/bin/milo-apply-avahi-iface (removed further down) as
    # ExecStartPre. Left behind, it makes avahi-daemon fail 203/EXEC on this boot
    # and every boot after, and nothing else ever removes it.
    sudo rm -f /etc/systemd/system/avahi-daemon.service.d/milo-override.conf
    sudo rmdir /etc/systemd/system/avahi-daemon.service.d 2>/dev/null || true
    sudo systemctl daemon-reload

    # Unmount Music Library USB/SMB/NFS shares BEFORE touching $MILO_DATA_DIR.
    # CIFS credentials (CRED_DIR=$MILO_DATA_DIR/shares, see rootfs/usr/local/bin/
    # milo-mount) live under /var/lib/milo — deleting that tree first would still
    # leave the shares mounted (an orphaned mount survives its backing files) but
    # remove any chance of a clean, credentialed remount/unmount later. Plain
    # `umount`/`umount -l` needs no credentials, only root, which this script
    # already has via sudo.
    if mountpoint -q /media/milo 2>/dev/null || [[ -d /media/milo ]]; then
        log_info "Unmounting Music Library shares under /media/milo..."
        for mnt in /media/milo/*/; do
            [[ -d "$mnt" ]] || continue
            mnt="${mnt%/}"
            if mountpoint -q "$mnt"; then
                sudo umount "$mnt" 2>/dev/null || sudo umount -l "$mnt" || true
            fi
            sudo rmdir "$mnt" 2>/dev/null || true
        done
        sudo rmdir /media/milo 2>/dev/null || true
    fi

    log_info "Removing configurations..."
    sudo rm -f /etc/nginx/sites-enabled/milo
    sudo rm -f /etc/nginx/sites-available/milo
    sudo rm -f /etc/snapserver.conf
    sudo rm -f /etc/shairport-sync.conf
    sudo rm -f /etc/dbus-1/system.d/shairport-sync-dbus.conf
    sudo rm -f /etc/asound.conf
    sudo rm -f /etc/modules-load.d/snd-aloop.conf
    sudo rm -f /etc/modprobe.d/snd-aloop.conf

    log_info "Removing sudoers and PolicyKit rules..."
    sudo rm -f /etc/sudoers.d/milo-backend
    sudo rm -f /etc/sudoers.d/milo-ir-remote
    sudo rm -f /etc/polkit-1/rules.d/50-milo-networkmanager.rules

    log_info "Removing udev rules..."
    sudo rm -f /etc/udev/rules.d/90-milo-cd.rules
    sudo rm -f /etc/udev/rules.d/99-milo-fan.rules
    sudo rm -f /etc/udev/rules.d/99-milo-screen.rules
    sudo rm -f /etc/udev/rules.d/99-backlight.rules
    sudo udevadm control --reload-rules 2>/dev/null || true
    sudo udevadm trigger 2>/dev/null || true

    log_info "Removing application..."
    sudo rm -rf "$MILO_APP_DIR"
    sudo rm -rf "$MILO_DATA_DIR"

    log_info "Removing Milo themes..."
    # Restore the original Adwaita cursors from the backup install/display.sh
    # made before overwriting them in place (install_milo_cursor_theme) — there
    # is no /usr/share/icons/Milo, that path was never installed by any script.
    if [[ -d /usr/share/icons/Adwaita/cursors.backup ]]; then
        sudo rm -rf /usr/share/icons/Adwaita/cursors
        sudo mv /usr/share/icons/Adwaita/cursors.backup /usr/share/icons/Adwaita/cursors
    fi
    sudo rm -rf /usr/share/plymouth/themes/milo

    log_info "Removing binaries and helper scripts..."
    sudo rm -f /usr/local/bin/go-librespot
    sudo rm -f /usr/local/bin/milo-brightness-7
    sudo rm -f /usr/local/bin/navidrome
    sudo rm -f /usr/local/bin/camilladsp
    sudo rm -f /usr/local/bin/milo-wait-ready.sh
    sudo rm -f /usr/local/bin/milo-apply-hardware
    sudo rm -f /usr/local/bin/milo-deploy-update
    sudo rm -f /usr/local/bin/milo-set-wifi-country
    sudo rm -f /usr/local/bin/milo-navidrome-provision
    sudo rm -f /usr/local/bin/milo-mount
    sudo rm -f /usr/local/bin/milo-umount
    sudo rm -f /usr/local/bin/milo-apply-avahi-iface
    sudo rm -f /usr/local/bin/milo-apply-ir-keymap
    sudo rm -f /usr/local/bin/milo-ir-keytable-setup
    sudo rm -f /usr/local/bin/milo-tidal-connect
    sudo rm -f /usr/local/bin/milo-qobuz
    sudo rm -rf /usr/local/lib/milo
    # Whole Tidal Connect runtime tree — it is self-contained by design.
    sudo rm -rf /opt/milo/tidal-connect

    log_info "Cleaning up packages..."
    sudo apt purge -y snapserver snapclient gmediarender 2>/dev/null || true
    sudo apt autoremove -y

    read -p "Restore default hostname 'raspberrypi'? (y/N): " restore_hostname
    case $restore_hostname in
        [Yy]* )
            configure_hostname "raspberrypi"
            log_info "Hostname restored"
            ;;
    esac

    log_info "Restarting system services..."
    # Not `|| true`: a failure here means the uninstall left the host with a
    # broken system service, which is exactly what the drop-in removal above
    # exists to prevent. Report it rather than aborting the uninstall.
    if ! sudo systemctl restart nginx avahi-daemon; then
        log_warning "nginx or avahi-daemon failed to restart — check 'systemctl status avahi-daemon'"
    fi

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
