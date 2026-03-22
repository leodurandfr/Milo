#!/bin/bash -e
# Milo pi-gen stage: System configuration
# Plymouth, boot params, fan control, udev, sudoers, kiosk, cursor theme

MILO_APP_DIR="/home/milo/milo"

# ── Plymouth boot splash ─────────────────────────────────────────────────────

on_chroot << 'CHROOT'
mkdir -p /usr/share/plymouth/themes/milo

for theme_file in /home/milo/milo/rootfs/usr/share/plymouth/themes/milo/*; do
    if [ -f "$theme_file" ]; then
        cp "$theme_file" /usr/share/plymouth/themes/milo/
    fi
done

plymouth-set-default-theme milo
update-initramfs -u

# Mask plymouth-quit services (milo-readiness handles quit manually)
systemctl mask plymouth-quit.service plymouth-quit-wait.service
CHROOT

# ── Boot parameters (cmdline.txt) ────────────────────────────────────────────

on_chroot << 'CHROOT'
CMDLINE="/boot/firmware/cmdline.txt"
if [ ! -f "$CMDLINE" ]; then
    CMDLINE="/boot/cmdline.txt"
fi

if [ -f "$CMDLINE" ]; then
    # Read current cmdline and clean conflicting params
    CURRENT=$(cat "$CMDLINE")
    CURRENT=$(echo "$CURRENT" | sed -E '
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

    BOOT_PARAMS="quiet splash plymouth.ignore-serial-consoles console=tty3 loglevel=0 consoleblank=0 logo.nologo vt.global_cursor_default=0 fbcon=map:99 vt.handoff=7 cfg80211.ieee80211_regdom=FR"
    echo "${CURRENT} ${BOOT_PARAMS}" | tr -s ' ' > "$CMDLINE"
fi
CHROOT

# ── Boot parameters (config.txt) ─────────────────────────────────────────────

on_chroot << 'CHROOT'
CONFIG="/boot/firmware/config.txt"
if [ ! -f "$CONFIG" ]; then
    CONFIG="/boot/config.txt"
fi

if [ -f "$CONFIG" ]; then
    # Silent boot
    if ! grep -q "disable_splash=1" "$CONFIG"; then
        sed -i '/^\[all\]$/a\\n# Milo - Silent boot\ndisable_splash=1' "$CONFIG"
    fi

    # Fan PWM control
    if ! grep -q "cooling_fan=on" "$CONFIG"; then
        cat >> "$CONFIG" << 'EOF'

# Milo - Fan PWM Control
dtparam=cooling_fan=on
dtparam=fan_temp0=55000
dtparam=fan_temp0_hyst=2500
dtparam=fan_temp0_speed=50
dtparam=fan_temp1=60000
dtparam=fan_temp1_hyst=2500
dtparam=fan_temp1_speed=100
dtparam=fan_temp2=65000
dtparam=fan_temp2_hyst=2500
dtparam=fan_temp2_speed=150
dtparam=fan_temp3=70000
dtparam=fan_temp3_hyst=2500
dtparam=fan_temp3_speed=200
dtparam=fan_temp4=75000
dtparam=fan_temp4_hyst=2500
dtparam=fan_temp4_speed=255
EOF
    fi
fi
CHROOT

# ── Journald limits ──────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
sed -i 's/^#RuntimeMaxUse=$/RuntimeMaxUse=100M/' /etc/systemd/journald.conf
sed -i 's/^#MaxRetentionSec=$/MaxRetentionSec=7d/' /etc/systemd/journald.conf
CHROOT

# ── udev rules ───────────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
# Screen brightness control (HID + backlight)
cp /home/milo/milo/rootfs/etc/udev/rules.d/99-milo-screen.rules /etc/udev/rules.d/
chmod 0644 /etc/udev/rules.d/99-milo-screen.rules

# DSI backlight permissions
tee /etc/udev/rules.d/99-backlight.rules > /dev/null << 'EOF'
SUBSYSTEM=="backlight", RUN+="/bin/chmod 0666 /sys/class/backlight/%k/brightness"
EOF
CHROOT

# ── Sudoers ───────────────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
# Consolidated sudoers for the milo backend service
tee /etc/sudoers.d/milo-backend > /dev/null << 'EOF'
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
visudo -c -f /etc/sudoers.d/milo-backend || { echo "FATAL: sudoers syntax error"; exit 1; }
chmod 0440 /etc/sudoers.d/milo-backend

# Client sudoers (if exists)
if [ -f /home/milo/milo/milo-client/rootfs/etc/sudoers.d/milo-client ]; then
    cp /home/milo/milo/milo-client/rootfs/etc/sudoers.d/milo-client /etc/sudoers.d/
    chmod 0440 /etc/sudoers.d/milo-client
fi
CHROOT

# ── Kiosk mode (Cage + Chromium) ──────────────────────────────────────────────

on_chroot << 'CHROOT'
# Cage launch script
sudo -u milo mkdir -p /home/milo/.config
cp /home/milo/milo/rootfs/home/milo/.config/milo-cage-start.sh /home/milo/.config/
chmod +x /home/milo/.config/milo-cage-start.sh
chown milo:milo /home/milo/.config/milo-cage-start.sh

# .bash_profile for auto-launch on tty1
cp /home/milo/milo/rootfs/home/milo/.bash_profile /home/milo/.bash_profile
chown milo:milo /home/milo/.bash_profile
CHROOT

# ── Transparent cursor theme ─────────────────────────────────────────────────

on_chroot << 'CHROOT'
if [ -d /usr/share/icons/Adwaita/cursors ]; then
    # Backup original cursors
    if [ ! -d /usr/share/icons/Adwaita/cursors.backup ]; then
        cp -r /usr/share/icons/Adwaita/cursors /usr/share/icons/Adwaita/cursors.backup
    fi

    # 1x1 transparent Xcursor (68 bytes, base64)
    XCURSOR_B64="WGN1chAAAAAAAAEAAQAAAAIA/f8YAAAAHAAAACQAAAACAP3/GAAAAAEAAAABAAAAAQAAAAAAAAAAAAAAMgAAAAAAAAA="
    echo "$XCURSOR_B64" | base64 -d > /tmp/transparent_cursor

    for cursor_file in /usr/share/icons/Adwaita/cursors/*; do
        if [ -f "$cursor_file" ]; then
            cp /tmp/transparent_cursor "$cursor_file"
        fi
    done

    rm -f /tmp/transparent_cursor
fi
CHROOT

# ── Screen brightness controls ───────────────────────────────────────────────

# Waveshare 8" DSI brightness tool
on_chroot << 'CHROOT'
cd /tmp
if wget -q https://files.waveshare.com/wiki/common/Brightness.zip 2>/dev/null; then
    unzip -o Brightness.zip
    cd Brightness
    chmod +x install.sh
    ./install.sh || true
    cd /tmp
    rm -rf Brightness Brightness.zip
fi
CHROOT

# ── Clear /etc/issue ─────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
cp /etc/issue /etc/issue.backup 2>/dev/null || true
echo "" > /etc/issue
rm -f /etc/issue.d/IP.issue
CHROOT

# ── Disable lightdm if present ───────────────────────────────────────────────

on_chroot << 'CHROOT'
systemctl stop lightdm.service 2>/dev/null || true
systemctl disable lightdm.service 2>/dev/null || true
systemctl mask lightdm.service 2>/dev/null || true
apt-get remove -y lightdm 2>/dev/null || true
CHROOT

# ── Mask getty@tty1 (kiosk takes over) ────────────────────────────────────────

on_chroot << 'CHROOT'
systemctl mask getty@tty1.service
CHROOT
