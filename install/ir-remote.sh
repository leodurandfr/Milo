#!/bin/bash
# Milo - IR Remote support (Apple Remote via TSOP4838 on GPIO17)
#
# Installs ir-keytable, the gpio-ir device-tree overlay, the keymap directory,
# the sudoers-protected keymap helper, and the oneshot service that enables
# NEC decoding at boot.
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

# Default GPIO pin for the TSOP4838 data line (matches §1.6 / §2.1 of
# docs/plans/remote-controls.md — pin 11 on the 40-pin header).
IR_REMOTE_GPIO_DEFAULT=17

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_ir_keytable() {
    log_info "Installing ir-keytable..."
    sudo apt install -y ir-keytable
    sudo mkdir -p /etc/rc_keymaps
    log_success "ir-keytable installed"
}

configure_ir_overlay() {
    log_info "Configuring gpio-ir overlay (GPIO${IR_REMOTE_GPIO_DEFAULT})..."

    local config_file="/boot/firmware/config.txt"
    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
    fi

    # Remove any previous managed block (idempotent re-run safety)
    sudo sed -i '/^# BEGIN MILO IR$/,/^# END MILO IR$/d' "$config_file"

    sudo tee -a "$config_file" > /dev/null << EOF

# BEGIN MILO IR
dtoverlay=gpio-ir,gpio_pin=${IR_REMOTE_GPIO_DEFAULT}
# END MILO IR
EOF

    log_success "gpio-ir overlay configured in $config_file"
}

install_ir_helpers() {
    log_info "Installing IR helper scripts..."

    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-apply-ir-keymap" \
        /usr/local/bin/milo-apply-ir-keymap
    sudo chmod +x /usr/local/bin/milo-apply-ir-keymap

    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-ir-keytable-setup" \
        /usr/local/bin/milo-ir-keytable-setup
    sudo chmod +x /usr/local/bin/milo-ir-keytable-setup

    sudo cp "$MILO_APP_DIR/rootfs/etc/sudoers.d/milo-ir-remote" \
        /etc/sudoers.d/milo-ir-remote
    sudo visudo -c -f /etc/sudoers.d/milo-ir-remote \
        || { echo "FATAL: sudoers syntax error in milo-ir-remote"; exit 1; }
    sudo chmod 0440 /etc/sudoers.d/milo-ir-remote

    log_success "IR helper scripts installed"
}

install_ir_systemd_service() {
    log_info "Enabling milo-ir-keytable systemd service..."

    # The unit file itself is copied by create_systemd_services() in
    # install/system.sh (which globs system/*.service). Here we just
    # enable it so it runs at boot.
    sudo systemctl enable milo-ir-keytable.service

    log_success "milo-ir-keytable.service enabled"
}

install_ir_remote() {
    install_ir_keytable
    configure_ir_overlay
    install_ir_helpers
    install_ir_systemd_service

    log_success "IR remote support installed (TSOP4838 on GPIO${IR_REMOTE_GPIO_DEFAULT})"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_ir_remote
fi
