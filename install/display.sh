#!/bin/bash
# Milo - Display Configuration (Kiosk + Plymouth + Brightness)
#
# Configures the display stack: seatd, Cage Wayland compositor,
# transparent cursors, Plymouth boot splash, login/lightdm,
# and screen brightness controls.
#
# Can be sourced from install.sh or run standalone.

set -e

MILO_USER="${MILO_USER:-milo}"
MILO_APP_DIR="${MILO_APP_DIR:-/home/$MILO_USER/milo}"

# Use parent logging functions if available, otherwise load common helpers
if ! type log_info &>/dev/null; then
    source "$(dirname "$0")/common.sh"
fi

install_seatd() {
    log_info "Installing seatd (required for Wayland/Cage)..."

    # seatd allows milo-kiosk.service to access VTs without root permissions
    sudo apt install -y seatd
    sudo systemctl enable seatd.service

    log_success "seatd installed and enabled"
}

configure_cage_kiosk() {
    log_info "Configuring kiosk mode with Cage..."

    # Install Cage (Wayland compositor)
    # Note: x11-xserver-utils is not needed as Cage is pure Wayland
    sudo apt install -y cage

    # Chromium is already installed via install_avahi_nginx()

    log_success "Kiosk mode configured with Cage"
}

install_milo_cursor_theme() {
    log_info "Installing transparent cursors (modified Adwaita)..."

    # Backup original Adwaita cursors (if not already done)
    if [[ ! -d /usr/share/icons/Adwaita/cursors.backup ]]; then
        log_info "Backing up original Adwaita cursors..."
        sudo cp -r /usr/share/icons/Adwaita/cursors /usr/share/icons/Adwaita/cursors.backup
    else
        log_info "Adwaita cursors already backed up, keeping existing backup"
    fi

    # Full transparent Xcursor file encoded in base64 (68 bytes)
    # Xcursor format with a 1x1 fully transparent pixel (ARGB = 00 00 00 00)
    log_info "Creating transparent cursor..."
    local xcursor_base64="WGN1chAAAAAAAAEAAQAAAAIA/f8YAAAAHAAAACQAAAACAP3/GAAAAAEAAAABAAAAAQAAAAAAAAAAAAAAMgAAAAAAAAA="
    echo "$xcursor_base64" | base64 -d > /tmp/transparent_cursor

    # Replace all Adwaita cursors with the transparent cursor
    log_info "Replacing all Adwaita cursors with transparent cursors..."

    # Find all files in the cursors directory (not symbolic links)
    for cursor_file in /usr/share/icons/Adwaita/cursors/*; do
        # Ignore backups
        if [[ "$cursor_file" != *.backup ]]; then
            # Replace each file or link with our transparent cursor
            sudo cp /tmp/transparent_cursor "$cursor_file"
        fi
    done

    # Cleanup
    rm -f /tmp/transparent_cursor

    log_success "Adwaita cursors replaced with transparent cursors"
    log_info "To restore original cursors: sudo rm -rf /usr/share/icons/Adwaita/cursors && sudo mv /usr/share/icons/Adwaita/cursors.backup /usr/share/icons/Adwaita/cursors"
}

configure_boot_display() {
    log_info "Configuring boot display (no screen selected yet)..."

    # Use common boot config — screen-specific config applied later by milo-apply-hardware
    source "$(dirname "${BASH_SOURCE[0]}")/boot-common.sh"

    # Configure cmdline.txt
    configure_cmdline "$BOOT_PARAMS_COMMON $BOOT_PARAMS_SCREEN"

    # Configure config.txt
    configure_config "$CONFIG_PARAMS_SCREEN"

    log_success "Boot display configured"
}

configure_cmdline() {
    local boot_params="$1"
    local cmdline_file="/boot/firmware/cmdline.txt"
    [[ ! -f "$cmdline_file" ]] && cmdline_file="/boot/cmdline.txt"

    if [[ ! -f "$cmdline_file" ]]; then
        log_error "cmdline.txt not found"
        return 1
    fi

    sudo cp "$cmdline_file" "${cmdline_file}.milo-backup" 2>/dev/null || true

    # Clean current cmdline (remove parameters we will set)
    local current_cmdline
    current_cmdline=$(cat "$cmdline_file")
    current_cmdline=$(echo "$current_cmdline" | sed -E '
        s/console=serial[0-9],[0-9]+//g
        s/console=tty[0-9]//g
        s/loglevel=[0-9]+//g
        s/\bquiet\b//g
        s/\bsplash\b//g
        s/plymouth\.[^ ]*//g
        s/logo\.[^ ]*//g
        s/vt\.[^ ]*//g
        s/fbcon=[^ ]*//g
        s/video=[^ ]*//g
        s/cfg80211\.[^ ]*//g
        s/  +/ /g
    ' | xargs)

    echo "${current_cmdline} ${boot_params}" | tr -s ' ' | sudo tee "$cmdline_file" > /dev/null
    log_success "cmdline.txt configured"
}

configure_config() {
    local screen_params="$1"

    local config_file="/boot/firmware/config.txt"
    [[ ! -f "$config_file" ]] && config_file="/boot/config.txt"
    [[ ! -f "$config_file" ]] && return 0

    # Silent boot (single source of truth in boot-common.sh)
    configure_silent_boot

    # Add screen-specific params
    if [[ -n "$screen_params" ]]; then
        echo "$screen_params" | while read -r param; do
            [[ -z "$param" ]] && continue
            if ! grep -q "$param" "$config_file"; then
                sudo sed -i "/disable_splash=1/a\\$param" "$config_file"
            fi
        done
    fi

    log_success "config.txt configured"
}

configure_plymouth_splash() {
    log_info "Configuring boot splash screen with Milo theme..."

    # Install Plymouth
    sudo apt install -y plymouth plymouth-themes

    local theme_src="$MILO_APP_DIR/rootfs/usr/share/plymouth/themes/milo"
    if [[ ! -d "$theme_src" ]]; then
        log_error "Plymouth theme directory not found: $theme_src"
        return 1
    fi

    # Mirror the theme rather than copy into it. This function also runs when an
    # installed unit is reinstalled, and copy-only leaves behind assets the theme
    # no longer ships: the fill was three sliced PNGs for one commit before going
    # back to a single image, and those slices would still be sitting next to
    # milo.script. Copy first, then drop the strays — never a moment where the
    # theme is absent, so an install that aborts here still boots with a splash.
    log_info "Installing Plymouth theme files..."
    sudo mkdir -p /usr/share/plymouth/themes/milo
    sudo cp "$theme_src"/* /usr/share/plymouth/themes/milo/
    for installed in /usr/share/plymouth/themes/milo/*; do
        [[ -f "$theme_src/$(basename "$installed")" ]] || sudo rm -f "$installed"
    done
    log_success "Plymouth theme installed ($(ls -1 "$theme_src" | wc -l) files)"

    # Set Milo theme as default
    sudo plymouth-set-default-theme milo

    # The theme is read from the initramfs — that is what lets the splash paint
    # at ~2.9 s, well before the root filesystem's own units are up. A theme
    # change that skips this reaches /usr/share and is never seen at boot.
    sudo update-initramfs -u

    # Configure boot display (cmdline.txt + config.txt) based on screen type
    configure_boot_display

    # Clear /etc/issue to hide getty messages
    sudo cp /etc/issue /etc/issue.backup 2>/dev/null || true
    echo "" | sudo tee /etc/issue > /dev/null

    # Remove IP.issue if exists
    sudo rm -f /etc/issue.d/IP.issue

    # Mask plymouth-quit services (milo-readiness handles quit manually)
    sudo systemctl mask plymouth-quit.service plymouth-quit-wait.service

    log_success "Boot splash screen configured with Milo theme, Plymouth stays active until manual quit"
}

disable_lightdm() {
    log_info "Disabling lightdm (Milo uses autologin + Cage)..."

    # Stop and disable lightdm if active
    if systemctl is-active --quiet lightdm.service 2>/dev/null; then
        log_info "Stopping lightdm..."
        sudo systemctl stop lightdm.service || true
    fi

    if systemctl is-enabled --quiet lightdm.service 2>/dev/null; then
        log_info "Disabling lightdm..."
        sudo systemctl disable lightdm.service || true
    fi

    # Mask the service to prevent activation
    sudo systemctl mask lightdm.service 2>/dev/null || true

    # Remove lightdm package if installed
    if dpkg -l | grep -q "^ii.*lightdm"; then
        log_info "Removing lightdm package..."
        sudo apt remove -y lightdm 2>/dev/null || true
        sudo apt autoremove -y || true
    fi

    log_success "lightdm disabled (Milo uses getty@tty1 + autologin + Cage)"
}

configure_silent_login() {
    log_info "Disabling getty@tty1 (Cage takes control via milo-kiosk.service)..."

    # Mask getty@tty1 as milo-kiosk.service takes control of tty1
    sudo systemctl mask getty@tty1.service

    # Raspberry Pi OS's first-boot user wizard. The milo user is pre-created, so
    # it has nothing to ask, but it holds a whiptail prompt on a tty forever and
    # multi-user.target never finishes behind it. pi-gen/stage-milo disables it
    # too — a unit installed by this script must not differ.
    sudo systemctl disable userconfig.service 2>/dev/null || true

    sudo systemctl daemon-reload

    log_success "getty@tty1 masked (milo-kiosk.service manages tty1)"
}

install_screen_brightness_control() {
    log_info "Installing brightness control for all screen types..."

    # Waveshare 7" USB brightness control
    log_info "Installing brightness control for Waveshare 7\" USB..."
    sudo cp "$MILO_APP_DIR/rootfs/usr/local/bin/milo-brightness-7" /usr/local/bin/milo-brightness-7
    sudo chmod +x /usr/local/bin/milo-brightness-7
    log_success "7\" USB brightness control installed"

    # Waveshare 8" DSI brightness control
    log_info "Installing brightness control for Waveshare 8\" DSI..."

    local temp_dir
    temp_dir=$(mktemp -d) || { log_error "Failed to create temp directory"; return 1; }
    register_temp_dir "$temp_dir"
    pushd "$temp_dir" > /dev/null

    wget https://files.waveshare.com/wiki/common/Brightness.zip
    unzip Brightness.zip
    cd Brightness
    sudo chmod +x install.sh
    ./install.sh

    popd > /dev/null

    # Create udev rule for backlight permissions
    log_info "Configuring backlight permissions (udev rule)..."
    sudo tee /etc/udev/rules.d/99-backlight.rules > /dev/null << 'EOF'
SUBSYSTEM=="backlight", RUN+="/bin/chmod 0666 /sys/class/backlight/%k/brightness"
EOF

    # Reload udev rules
    sudo udevadm control --reload-rules
    sudo udevadm trigger

    log_success "All screen brightness controls installed"
}

# Run all steps if executed standalone
# Note: configure_cage_kiosk requires Chromium (installed by network.sh's install_avahi_nginx)
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if ! command -v chromium &>/dev/null && ! command -v chromium-browser &>/dev/null; then
        log_warning "Chromium not found — installing (normally done by network.sh)..."
        sudo apt install -y chromium 2>/dev/null || sudo apt install -y chromium-browser
    fi
    install_seatd
    configure_cage_kiosk
    install_milo_cursor_theme
    configure_plymouth_splash
    disable_lightdm
    configure_silent_login
    install_screen_brightness_control
    log_success "Display configuration complete"
fi
