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

install_udev_rules() {
    log_info "Installing udev rules..."

    # Copy udev rules from rootfs
    sudo cp "$MILO_APP_DIR/rootfs/etc/udev/rules.d/99-milo-screen.rules" /etc/udev/rules.d/99-milo-screen.rules
    sudo chmod 0644 /etc/udev/rules.d/99-milo-screen.rules

    # CD drive rules (Apple SuperDrive initialization)
    sudo cp "$MILO_APP_DIR/rootfs/etc/udev/rules.d/90-milo-cd.rules" /etc/udev/rules.d/90-milo-cd.rules
    sudo chmod 0644 /etc/udev/rules.d/90-milo-cd.rules

    # Fan control rules (runtime PWM fan control without sudo)
    sudo cp "$MILO_APP_DIR/rootfs/etc/udev/rules.d/99-milo-fan.rules" /etc/udev/rules.d/99-milo-fan.rules
    sudo chmod 0644 /etc/udev/rules.d/99-milo-fan.rules

    # Reload udev rules
    sudo udevadm control --reload-rules
    sudo udevadm trigger

    # Apply permissions immediately for existing devices
    sudo chmod 0666 /dev/hidraw* 2>/dev/null || true
    sudo chmod 0666 /sys/class/backlight/*/brightness 2>/dev/null || true
    sudo chmod 0666 /sys/devices/platform/cooling_fan/hwmon/hwmon*/pwm1 2>/dev/null || true
    sudo chmod 0666 /sys/devices/platform/cooling_fan/hwmon/hwmon*/pwm1_enable 2>/dev/null || true
    sudo chmod 0666 /sys/class/thermal/thermal_zone0/mode 2>/dev/null || true

    log_success "Udev rules installed (screen brightness + fan control without sudo)"
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

    # Hardware mixer unity pin, run as ExecStartPre of milo-camilladsp.service
    # (no sudoers entry — amixer only needs the audio group).
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-alsa-passthrough" /usr/local/bin/milo-alsa-passthrough
    sudo chmod +x /usr/local/bin/milo-alsa-passthrough

    # Navidrome first-boot service-account provisioning (no sudoers entry — runs
    # as milo, writes only under /var/lib/milo/navidrome).
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-navidrome-provision" /usr/local/bin/milo-navidrome-provision
    sudo chmod +x /usr/local/bin/milo-navidrome-provision

    # Music Library USB storage helpers (privileged read-only mount / unmount of a
    # USB key under /media/milo, so Navidrome can index it).
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-mount" /usr/local/bin/milo-mount
    sudo chmod +x /usr/local/bin/milo-mount

    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-umount" /usr/local/bin/milo-umount
    sudo chmod +x /usr/local/bin/milo-umount

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
# Music Library USB storage (read-only mount / unmount under /media/milo)
milo ALL=(root) NOPASSWD: /usr/local/bin/milo-mount
milo ALL=(root) NOPASSWD: /usr/local/bin/milo-umount
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
        echo "# Milo - Fan PWM Control (4 paliers, audio-first, quiet curve)" | sudo tee -a "$config_file"
        echo "dtparam=cooling_fan=on" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0=66000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp0_speed=55" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1=79000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp1_speed=120" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2=81000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp2_speed=200" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3=82000" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3_hyst=2500" | sudo tee -a "$config_file"
        echo "dtparam=fan_temp3_speed=255" | sudo tee -a "$config_file"
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

# Note: hardware.json is intentionally NOT seeded here. The backend creates it
# (save_versioned_json, always stamping the current schema_version) when the user
# picks hardware in the setup wizard. A bash-seeded file cannot track the schema
# and shipped a stale, unversioned file that crash-looped the backend with
# SchemaVersionMismatch. Absent file → backend uses in-code defaults
# (see backend/hardware/registry.py).

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
    sudo systemctl enable milo-camilladsp.service
    sudo systemctl enable milo-cpu-governor.service
    sudo systemctl enable avahi-daemon
    sudo systemctl enable nginx

    # Note: milo-frontend.service is no longer used (nginx serves /dist directly)
    # Note: getty@tty1 is masked (milo-kiosk.service takes control of tty1)

    # Note: The following services are managed dynamically by the Milo backend:
    # - milo-spotify.service
    # - milo-qobuz.service
    # - milo-mac.service
    # - milo-radio.service
    # - milo-airplay.service
    # - milo-cd.service
    # - milo-snapserver-multiroom.service
    # - milo-snapclient-multiroom.service
    # These services should NOT be "enabled" at boot

    # Defensive disable: snapcast units shipped before 2026-05 carried
    # WantedBy=multi-user.target. If a previous install enabled them, leftover
    # symlinks in /etc/systemd/system/multi-user.target.wants/ would still cause
    # them to start at boot and produce the state desync class fixed in
    # docs/plans/multiroom-state-desync.md. Newer unit files have no [Install]
    # section, but the symlinks persist until explicitly removed.
    sudo systemctl disable milo-snapserver-multiroom.service 2>/dev/null || true
    sudo systemctl disable milo-snapclient-multiroom.service 2>/dev/null || true

    # Replaced by rootfs/etc/NetworkManager/conf.d/90-milo-wifi-powersave.conf
    sudo systemctl disable milo-disable-wifi-power-management.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/milo-disable-wifi-power-management.service

    log_success "Automatic startup configured"
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
    enable_services
    log_success "System configuration complete"
fi
