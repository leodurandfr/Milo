#!/bin/bash
# Milo Client - Installation Script v2.0 (non-interactive)
#
# Usage:
#   install-client.sh                                # Auto-discover main Milo via mDNS
#   install-client.sh --server 192.168.1.10          # Manual server IP (if mDNS fails)
#   install-client.sh --help                         # Show usage
#   install-client.sh --uninstall                    # Remove Milo Client

set -e

MILO_CLIENT_USER="milo-client"
MILO_CLIENT_HOME="/home/$MILO_CLIENT_USER"
MILO_CLIENT_REPO_DIR="$MILO_CLIENT_HOME/repo"
MILO_CLIENT_APP_DIR="$MILO_CLIENT_REPO_DIR/milo-client/app"
MILO_CLIENT_SYSTEM_DIR="$MILO_CLIENT_REPO_DIR/milo-client/system"
MILO_CLIENT_ROOTFS_DIR="$MILO_CLIENT_REPO_DIR/milo-client/rootfs"
MILO_CLIENT_VENV_DIR="$MILO_CLIENT_HOME/venv"
MILO_CLIENT_DATA_DIR="/var/lib/milo-client"
MILO_CLIENT_REPO_URL="https://github.com/leodurandfr/Milo.git"

# CLI arguments
ARG_SERVER_IP=""
MILO_PRINCIPAL_IP=""

# --- Source all install modules ---
SCRIPT_DIR="$(dirname "$0")"

source "$SCRIPT_DIR/../install/common.sh"
source "$SCRIPT_DIR/install/base.sh"
source "$SCRIPT_DIR/install/snapclient.sh"
source "$SCRIPT_DIR/install/camilladsp.sh"
source "$SCRIPT_DIR/install/alsa.sh"
source "$SCRIPT_DIR/install/network.sh"
source "$SCRIPT_DIR/install/system.sh"

# --- Orchestrator functions ---

show_banner() {
    echo -e "${BLUE}"
    echo "  __  __ _ _         ____ _ _            _   "
    echo " |  \/  (_) | ___   / ___| (_) ___ _ __ | |_ "
    echo " | |\/| | | |/ _ \ | |   | | |/ _ \ '_ \| __|"
    echo " | |  | | | | (_) || |___| | |  __/ | | | |_ "
    echo " |_|  |_|_|_|\___/  \____|_|_|\___|_| |_|\__|"
    echo ""
    echo "Client Installation Script v2.0"
    echo -e "${NC}"
}

show_help() {
    echo "Usage: install-client.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --server <ip>        Main Milo IP address (if mDNS auto-discovery fails)"
    echo "  --uninstall          Remove Milo Client"
    echo "  --help               Show this help message"
    echo ""
    echo "Examples:"
    echo "  install-client.sh                                # Auto-discover main Milo via mDNS"
    echo "  install-client.sh --server 192.168.1.10          # Specify main Milo IP"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --server)
                if [[ -z "${2:-}" ]]; then
                    log_error "--server requires an IP address argument"
                    exit 1
                fi
                ARG_SERVER_IP="$2"
                shift 2
                ;;
            --help)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown argument: $1"
                echo ""
                show_help
                exit 1
                ;;
        esac
    done
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
        log_error "Unsupported architecture: $ARCH. Raspberry Pi OS 64-bit required."
        exit 1
    fi

    log_success "Compatible system detected"
}

finalize_installation() {
    echo ""
    echo -e "${GREEN}====================================${NC}"
    echo -e "${GREEN}  Milo Client Installation Complete! ${NC}"
    echo -e "${GREEN}====================================${NC}"
    echo ""
    echo -e "  ${BLUE}Next steps:${NC}"
    echo "    1. Open http://milo.local → Settings → Multiroom"
    echo "    2. Your new speaker will appear for configuration"
    echo ""

    log_info "Rebooting in 5 seconds..."
    sleep 5
    sudo reboot
}

# === UNINSTALL FUNCTION ===

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
    sudo systemctl daemon-reload

    # 3. Remove sudoers rules and wrapper scripts
    log_info "Removing sudoers rules and scripts..."
    sudo rm -f /etc/sudoers.d/milo-client
    sudo rm -f /usr/local/bin/milo-client-install-snapclient
    sudo rm -f /usr/local/bin/milo-client-deploy-update
    sudo rm -f /usr/local/bin/milo-client-apply-hardware

    # 4. Uninstall Snapclient
    log_info "Uninstalling Snapclient..."
    sudo apt remove -y snapclient 2>/dev/null || true
    sudo apt autoremove -y

    # 5. Remove CamillaDSP
    log_info "Removing CamillaDSP..."
    sudo rm -f /usr/local/bin/camilladsp
    sudo rm -f /etc/modprobe.d/milo-client-loopback.conf
    sudo sed -i '/snd-aloop/d' /etc/modules 2>/dev/null || true

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
        echo "raspberrypi" | sudo tee /etc/hostname > /dev/null
        sudo sed -i "s/127.0.1.1.*/127.0.1.1\traspberrypi/" /etc/hosts
        sudo hostnamectl set-hostname "raspberrypi"
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

# --- Main installation sequence ---

main() {
    # Check if uninstall mode (scan all args)
    for arg in "$@"; do
        if [[ "$arg" == "--uninstall" ]]; then
            if [[ $# -gt 1 ]]; then
                log_error "--uninstall cannot be combined with other arguments"
                exit 1
            fi
            show_banner
            check_root
            uninstall_milo_client
            exit 0
        fi
    done

    # Normal installation
    show_banner
    check_root
    check_system
    parse_args "$@"

    log_info "Starting Milo Client installation"
    echo ""

    # Base system setup
    install_dependencies
    suppress_pulseaudio
    discover_milo_principal
    configure_journald
    setup_hostname

    # User and application
    create_milo_client_user
    clone_milo_client_repo
    install_snapclient
    install_camilladsp
    install_milo_client_application

    # Audio routing
    configure_alsa_loopback
    configure_alsa

    # System configuration
    install_apply_hardware_script
    save_hardware_config
    create_systemd_services
    enable_services
    install_wrapper_scripts
    configure_sudoers

    # Network
    configure_avahi
    configure_network_priority

    # Cleanup non-interactive apt overrides (must happen after all apt calls)
    sudo rm -f /etc/apt/apt.conf.d/local

    # Finalize
    finalize_installation
}

main "$@"
