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
source "$SCRIPT_DIR/install/uninstall.sh"

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
