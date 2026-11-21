#!/bin/bash
# Milo - IR Remote support (Apple Remote via TSOP4838 on GPIO17)
#
# Writes the gpio-ir device-tree overlay's default and installs the
# sudoers-protected keymap helper. The ir-keytable package and the oneshot
# service that enables NEC decoding at boot (milo-ir-keytable.service) are the
# system manifest's (system-manifest.env).
#
# Sourced by pi-gen/stage-milo during the image build.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

# Default GPIO pin for the TSOP4838 data line — pin 11 on the 40-pin header;
# the wiring is in docs/hardware/wiring.md.
IR_REMOTE_GPIO_DEFAULT=17

configure_ir_overlay() {
    log_info "Configuring gpio-ir overlay (GPIO${IR_REMOTE_GPIO_DEFAULT})..."

    local config_file
    config_file=$(boot_config_file)

    # Remove any previous managed block (idempotent re-run safety)
    sed -i '/^# BEGIN MILO IR$/,/^# END MILO IR$/d' "$config_file"

    tee -a "$config_file" > /dev/null << EOF

# BEGIN MILO IR
dtoverlay=gpio-ir,gpio_pin=${IR_REMOTE_GPIO_DEFAULT}
# END MILO IR
EOF

    log_success "gpio-ir overlay configured in $config_file"
}

install_ir_helpers() {
    log_info "Installing IR helper scripts..."

    cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-apply-ir-keymap" \
        /usr/local/bin/milo-apply-ir-keymap
    chmod +x /usr/local/bin/milo-apply-ir-keymap

    cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-ir-keytable-setup" \
        /usr/local/bin/milo-ir-keytable-setup
    chmod +x /usr/local/bin/milo-ir-keytable-setup

    cp "$MILO_APP_DIR/rootfs/etc/sudoers.d/milo-ir-remote" \
        /etc/sudoers.d/milo-ir-remote
    visudo -c -f /etc/sudoers.d/milo-ir-remote \
        || { echo "FATAL: sudoers syntax error in milo-ir-remote"; exit 1; }
    chmod 0440 /etc/sudoers.d/milo-ir-remote

    log_success "IR helper scripts installed"
}
