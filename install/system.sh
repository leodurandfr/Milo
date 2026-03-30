#!/bin/bash
# Milo - System Configuration
#
# Configures system-level components: udev rules, readiness script,
# sudoers/polkit, systemd services, fan control, boot optimization,
# and default hardware configuration.
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_DATA_DIR="${MILO_DATA_DIR:-/var/lib/milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

configure_journald() {
    log_info "Configuring journald limits..."

    sudo sed -i 's/^#\?RuntimeMaxUse=.*/RuntimeMaxUse=100M/' /etc/systemd/journald.conf
    sudo sed -i 's/^#\?MaxRetentionSec=.*/MaxRetentionSec=7d/' /etc/systemd/journald.conf

    log_success "Journald configured (100MB max, 7 days retention)"
}

install_udev_rules() {
    log_info "Installing udev rules..."

    # Copy udev rules from rootfs
    sudo cp "$MILO_APP_DIR/rootfs/etc/udev/rules.d/99-milo-screen.rules" /etc/udev/rules.d/99-milo-screen.rules
    sudo chmod 0644 /etc/udev/rules.d/99-milo-screen.rules

    # CD drive rules (Apple SuperDrive initialization)
    sudo cp "$MILO_APP_DIR/rootfs/etc/udev/rules.d/90-milo-cd.rules" /etc/udev/rules.d/90-milo-cd.rules
    sudo chmod 0644 /etc/udev/rules.d/90-milo-cd.rules

    # Reload udev rules
    sudo udevadm control --reload-rules
    sudo udevadm trigger

    # Apply permissions immediately for existing devices
    sudo chmod 0666 /dev/hidraw* 2>/dev/null || true
    sudo chmod 0666 /sys/class/backlight/*/brightness 2>/dev/null || true

    log_success "Udev rules installed (screen brightness without sudo)"
}

install_readiness_script() {
    log_info "Installing readiness script..."

    # Copy readiness script to /usr/local/bin/
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-wait-ready.sh" /usr/local/bin/milo-wait-ready.sh
    sudo chmod +x /usr/local/bin/milo-wait-ready.sh

    log_success "Readiness script installed in /usr/local/bin/"
}

install_apply_hardware_script() {
    log_info "Installing system scripts..."

    # Shared hardware helpers library
    sudo mkdir -p /usr/local/lib/milo
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/lib/milo/hardware-helpers.sh" /usr/local/lib/milo/
    sudo chmod +x /usr/local/lib/milo/hardware-helpers.sh

    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-apply-hardware" /usr/local/bin/milo-apply-hardware
    sudo chmod +x /usr/local/bin/milo-apply-hardware

    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-deploy-update" /usr/local/bin/milo-deploy-update
    sudo chmod +x /usr/local/bin/milo-deploy-update

    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-set-wifi-country" /usr/local/bin/milo-set-wifi-country
    sudo chmod +x /usr/local/bin/milo-set-wifi-country

    # Remove legacy sudoers file if present
    sudo rm -f /etc/sudoers.d/milo-hardware

    # Consolidated sudoers for all backend sudo operations
    sudo tee /etc/sudoers.d/milo-backend > /dev/null << 'EOF'
# Suppress sudo + PAM session logs (noisy in journalctl)
Defaults:milo !syslog, !pam_session

# System control (used by SystemdServiceManager and api/system.py)
milo ALL=(root) NOPASSWD: /usr/bin/systemctl
milo ALL=(root) NOPASSWD: /usr/bin/hostnamectl
milo ALL=(root) NOPASSWD: /usr/sbin/reboot
milo ALL=(root) NOPASSWD: /usr/sbin/poweroff
# Hardware configuration
milo ALL=(root) NOPASSWD: /usr/local/bin/milo-apply-hardware
# Update deployment (file ops, packages, udev — all via secure wrapper)
milo ALL=(root) NOPASSWD: /usr/local/bin/milo-deploy-update
# WiFi regulatory domain
milo ALL=(root) NOPASSWD: /usr/local/bin/milo-set-wifi-country
EOF
    sudo visudo -c -f /etc/sudoers.d/milo-backend || { echo "FATAL: sudoers syntax error"; exit 1; }
    sudo chmod 0440 /etc/sudoers.d/milo-backend

    log_success "Hardware apply script and sudoers installed"
}

install_polkit_rules() {
    log_info "Installing PolicyKit rules for NetworkManager..."

    sudo mkdir -p /etc/polkit-1/rules.d
    sudo cp "$MILO_APP_DIR/rootfs/etc/polkit-1/rules.d/50-milo-networkmanager.rules" \
        /etc/polkit-1/rules.d/50-milo-networkmanager.rules
    sudo chmod 0644 /etc/polkit-1/rules.d/50-milo-networkmanager.rules

    log_success "PolicyKit rules installed"
}

create_systemd_services() {
    log_info "Installing systemd services..."

    # Copy all .service files from system/ to /etc/systemd/system/
    for service_file in "$MILO_APP_DIR/system"/*.service; do
        if [[ -f "$service_file" ]]; then
            local service_name
            service_name=$(basename "$service_file")
            sudo cp "$service_file" /etc/systemd/system/
            log_success "Installed $service_name"
        fi
    done

    # Reload systemd daemon to recognize new services
    sudo systemctl daemon-reload

    log_success "Systemd services installed"
}

configure_fan_control() {
    log_info "Configuring fan control..."

    local config_file="/boot/firmware/config.txt"

    if [[ ! -f "$config_file" ]]; then
        config_file="/boot/config.txt"
    fi

    if ! grep -q "cooling_fan=on" "$config_file"; then
        echo "" | sudo tee -a "$config_file"
        echo "# Milo - Fan PWM Control" | sudo tee -a "$config_file"
        echo "dtparam=cooling_fan=on" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0=55000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0_speed=50" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1=60000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1_speed=100" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2=65000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2_speed=150" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3=70000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3_speed=200" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp4=75000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp4_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp4_speed=255" | sudo tee -a "$config_file"
    fi

   log_success "Fan control configured"
}

optimize_boot_performance() {
    log_info "Optimizing boot performance..."

    # Mask NetworkManager-wait-online (saves ~13.5s)
    # This service waits for complete network connection, but Milo doesn't need it
    sudo systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
    sudo systemctl mask NetworkManager-wait-online.service 2>/dev/null || true

    log_success "NetworkManager-wait-online.service masked (saves ~13s at boot)"
}

save_hardware_config() {
    log_info "Saving default hardware configuration to $MILO_DATA_DIR/hardware.json..."

    sudo tee "$MILO_DATA_DIR/hardware.json" > /dev/null << 'EOF'
{
  "screen": {
    "type": "none",
    "resolution": null
  },
  "audio": {
    "id": "none"
  },
  "rotary_encoder": {
    "clk_pin": 22,
    "dt_pin": 27,
    "sw_pin": 23
  }
}
EOF

    sudo chown "$MILO_USER:$MILO_USER" "$MILO_DATA_DIR/hardware.json"
    log_success "Default hardware configuration saved (configure via setup wizard)"
}

# Run all steps if executed standalone
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    install_udev_rules
    install_readiness_script
    install_apply_hardware_script
    install_polkit_rules
    create_systemd_services
    configure_journald
    configure_fan_control
    optimize_boot_performance
    save_hardware_config
    log_success "System configuration complete"
fi
