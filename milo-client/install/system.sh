#!/bin/bash
# Milo Client - System Configuration
#
# Configures systemd services, sudoers, wrapper scripts,
# and default hardware configuration.
#
# Can be sourced from install-client.sh or run standalone.

set -e

MILO_CLIENT_USER="${MILO_CLIENT_USER:-milo-client}"
MILO_CLIENT_HOME="${MILO_CLIENT_HOME:-/home/$MILO_CLIENT_USER}"
MILO_CLIENT_REPO_DIR="${MILO_CLIENT_REPO_DIR:-$MILO_CLIENT_HOME/repo}"
MILO_CLIENT_ROOTFS_DIR="${MILO_CLIENT_ROOTFS_DIR:-$MILO_CLIENT_REPO_DIR/milo-client/rootfs}"
MILO_CLIENT_SYSTEM_DIR="${MILO_CLIENT_SYSTEM_DIR:-$MILO_CLIENT_REPO_DIR/milo-client/system}"
MILO_CLIENT_DATA_DIR="${MILO_CLIENT_DATA_DIR:-/var/lib/milo-client}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/../../install/common.sh"
fi

save_hardware_config() {
    log_info "Saving hardware configuration..."

    sudo mkdir -p "$MILO_CLIENT_DATA_DIR"

    sudo tee "$MILO_CLIENT_DATA_DIR/hardware.json" > /dev/null << 'EOF'
{
  "audio": {
    "id": "none"
  }
}
EOF

    sudo chown "$MILO_CLIENT_USER:audio" "$MILO_CLIENT_DATA_DIR/hardware.json"
    log_success "Hardware config saved"
}

create_systemd_services() {
    log_info "Installing systemd services..."

    # Copy all service files from repo
    sudo cp "$MILO_CLIENT_SYSTEM_DIR/milo-client.service" /etc/systemd/system/
    log_success "Installed milo-client.service"

    sudo cp "$MILO_CLIENT_SYSTEM_DIR/milo-client-snapclient.service" /etc/systemd/system/
    log_success "Installed milo-client-snapclient.service"

    sudo cp "$MILO_CLIENT_SYSTEM_DIR/milo-client-camilladsp.service" /etc/systemd/system/
    log_success "Installed milo-client-camilladsp.service"

    # Create environment file with dynamic values
    sudo tee "$MILO_CLIENT_DATA_DIR/env" > /dev/null << EOF
MILO_PRINCIPAL_IP=$MILO_PRINCIPAL_IP
MILO_CLIENT_DSP_ENABLED=false
EOF
    sudo chown "$MILO_CLIENT_USER:audio" "$MILO_CLIENT_DATA_DIR/env"

    sudo systemctl daemon-reload

    log_success "Systemd services installed"
}

enable_services() {
    log_info "Enabling services..."

    sudo systemctl daemon-reload
    sudo systemctl enable milo-client.service
    sudo systemctl enable milo-client-snapclient.service
    sudo systemctl enable milo-client-camilladsp.service

    log_success "Services enabled"
}

install_wrapper_scripts() {
    log_info "Installing secure wrapper scripts..."

    # Bulk-deploy every milo-client wrapper (apply-hardware, deploy-update,
    # install-snapclient, install-camilladsp, snapclient-launcher) in one loop.
    # A hand-maintained per-file allowlist silently drops newly-added scripts —
    # that is exactly how milo-client-install-camilladsp went missing here while
    # the sudoers rule and camilladsp_update.py still invoked it (CamillaDSP
    # updates then failed every time, with no working rollback). pi-gen already
    # deploys these with a bulk loop; this keeps both install paths in sync.
    for script in "$MILO_CLIENT_ROOTFS_DIR"/usr/local/bin/*; do
        [ -f "$script" ] || continue
        sudo cp "$script" /usr/local/bin/
        sudo chmod 755 "/usr/local/bin/$(basename "$script")"
        sudo chown root:root "/usr/local/bin/$(basename "$script")"
    done

    log_success "Wrapper scripts installed"
}

configure_sudoers() {
    log_info "Configuring sudo permissions for milo-client..."

    # Copy sudoers file from repo
    sudo cp "$MILO_CLIENT_ROOTFS_DIR/etc/sudoers.d/milo-client" /etc/sudoers.d/
    sudo chmod 0440 /etc/sudoers.d/milo-client

    log_success "Sudo permissions configured"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ -z "${MILO_PRINCIPAL_IP:-}" ]]; then
        log_error "MILO_PRINCIPAL_IP must be set (run base.sh first or export it manually)"
        exit 1
    fi
    save_hardware_config
    create_systemd_services
    enable_services
    install_wrapper_scripts
    configure_sudoers
    log_success "System configuration complete"
fi
