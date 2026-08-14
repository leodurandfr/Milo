#!/bin/bash
# Milo Client - Uninstallation
#
# Removes Milo Client services, configurations, application files,
# audio config, user, and optionally restores the default hostname.
#
# Can be sourced from install-client.sh or run standalone.

set -e

MILO_CLIENT_USER="${MILO_CLIENT_USER:-milo-client}"
MILO_CLIENT_HOME="${MILO_CLIENT_HOME:-/home/$MILO_CLIENT_USER}"
MILO_CLIENT_REPO_DIR="${MILO_CLIENT_REPO_DIR:-$MILO_CLIENT_HOME/repo}"
MILO_CLIENT_VENV_DIR="${MILO_CLIENT_VENV_DIR:-$MILO_CLIENT_HOME/venv}"
MILO_CLIENT_DATA_DIR="${MILO_CLIENT_DATA_DIR:-/var/lib/milo-client}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/../../install/common.sh"
fi

uninstall_milo_client() {
    echo -e "${YELLOW}"
    echo "========================================"
    echo "      MILO CLIENT UNINSTALLATION        "
    echo "========================================"
    echo -e "${NC}"
    echo ""
    echo -e "${RED}This operation will remove:${NC}"
    echo "  - milo-client, milo-client-snapclient, and milo-client-camilladsp services"
    echo "  - The milo-client user and its data"
    echo "  - HiFiBerry audio configuration"
    echo "  - Snapclient and CamillaDSP"
    echo ""

    read -p "Are you sure you want to continue? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        log_info "Uninstallation cancelled"
        exit 0
    fi

    log_info "Starting uninstallation..."
    echo ""

    # 1. Stop and disable services
    log_info "Stopping services..."
    sudo systemctl stop milo-client.service 2>/dev/null || true
    sudo systemctl stop milo-client-snapclient.service 2>/dev/null || true
    sudo systemctl stop milo-client-camilladsp.service 2>/dev/null || true
    sudo systemctl disable milo-client.service 2>/dev/null || true
    sudo systemctl disable milo-client-snapclient.service 2>/dev/null || true
    sudo systemctl disable milo-client-camilladsp.service 2>/dev/null || true

    # 2. Remove service files
    log_info "Removing systemd services..."
    sudo rm -f /etc/systemd/system/milo-client.service
    sudo rm -f /etc/systemd/system/milo-client-snapclient.service
    sudo rm -f /etc/systemd/system/milo-client-camilladsp.service
    # The Avahi drop-in is not one of the three units above, and it runs
    # /usr/local/bin/milo-client-apply-avahi-iface (removed further down) as
    # ExecStartPre. Left behind, it makes avahi-daemon fail 203/EXEC on this
    # boot and every boot after, and nothing else ever removes it. Both
    # spellings: the script installer wrote milo-override.conf before the two
    # trees agreed on one name.
    sudo rm -f /etc/systemd/system/avahi-daemon.service.d/milo-client-override.conf
    sudo rm -f /etc/systemd/system/avahi-daemon.service.d/milo-override.conf
    sudo rmdir /etc/systemd/system/avahi-daemon.service.d 2>/dev/null || true
    sudo systemctl daemon-reload

    # 3. Remove sudoers rules and wrapper scripts
    log_info "Removing sudoers rules and scripts..."
    sudo rm -f /etc/sudoers.d/milo-client
    sudo rm -f /usr/local/bin/milo-client-install-snapclient
    sudo rm -f /usr/local/bin/milo-client-install-camilladsp
    sudo rm -f /usr/local/bin/milo-client-deploy-update
    sudo rm -f /usr/local/bin/milo-client-snapclient-launcher
    sudo rm -f /usr/local/bin/milo-client-apply-hardware
    sudo rm -f /usr/local/bin/milo-client-apply-avahi-iface
    sudo rm -f /etc/NetworkManager/dispatcher.d/90-milo-network

    # 4. Uninstall Snapclient
    log_info "Uninstalling Snapclient..."
    sudo apt remove -y snapclient 2>/dev/null || true
    sudo apt autoremove -y

    # 5. Remove CamillaDSP and ALSA loopback
    log_info "Removing CamillaDSP..."
    sudo rm -f /usr/local/bin/camilladsp
    sudo rm -f /etc/modprobe.d/milo-client-loopback.conf
    sudo rm -f /etc/modules-load.d/snd-aloop.conf

    # 6. Remove ALSA configuration
    log_info "Removing ALSA configuration..."
    sudo rm -f /etc/asound.conf

    # 7. Restore config.txt (remove HiFiBerry audio block)
    log_info "Restoring audio configuration..."
    local config_file="/boot/firmware/config.txt"
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
    fi

    local reboot_required=false

    if [[ -f "$config_file" ]]; then
        # Remove managed audio block
        sudo sed -i '/^# BEGIN MILO CLIENT AUDIO$/,/^# END MILO CLIENT AUDIO$/d' "$config_file"

        # Remove legacy (non-managed) entries from old installs
        sudo sed -i '/# Milo Client - HiFiBerry Audio/d' "$config_file"
        sudo sed -i '/dtoverlay=hifiberry-/d' "$config_file"

        # Re-enable built-in audio
        if ! grep -q "^dtparam=audio=on" "$config_file"; then
            echo "dtparam=audio=on" | sudo tee -a "$config_file" > /dev/null
        fi

        reboot_required=true
    fi

    # 8. Remove application directories
    log_info "Removing application files..."
    sudo rm -rf "$MILO_CLIENT_REPO_DIR"
    sudo rm -rf "$MILO_CLIENT_VENV_DIR"
    sudo rm -rf "$MILO_CLIENT_DATA_DIR"

    # 9. Remove milo-client user
    log_info "Removing milo-client user..."
    if id "$MILO_CLIENT_USER" &>/dev/null; then
        sudo userdel -r "$MILO_CLIENT_USER" 2>/dev/null || true
    fi

    # 10. Restore default hostname
    local current_hostname
    current_hostname=$(hostname)
    if [[ "$current_hostname" == "milo-client" || "$current_hostname" == milo-client-* ]]; then
        log_info "Restoring default hostname..."
        configure_hostname "raspberrypi"
        reboot_required=true
    fi

    echo ""
    echo -e "${GREEN}=================================${NC}"
    echo -e "${GREEN}   Uninstallation complete!      ${NC}"
    echo -e "${GREEN}=================================${NC}"
    echo ""

    if [[ "$reboot_required" == "true" ]]; then
        echo -e "${YELLOW}REBOOT REQUIRED to finalize${NC}"
        echo ""

        while true; do
            read -p "Reboot now? (Y/n): " restart_choice
            case $restart_choice in
                [Nn]* )
                    echo -e "${YELLOW}Remember to reboot manually with: sudo reboot${NC}"
                    break
                    ;;
                [Yy]* | "" )
                    log_info "Rebooting in 5 seconds..."
                    sleep 5
                    sudo reboot
                    ;;
                * )
                    echo "Please answer 'Y' (yes) or 'n' (no)."
                    ;;
            esac
        done
    else
        log_success "System cleaned. Milo Client has been completely removed."
    fi
}

# Run standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    uninstall_milo_client
fi
