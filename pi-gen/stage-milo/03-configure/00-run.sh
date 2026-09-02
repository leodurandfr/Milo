#!/bin/bash -e
# Milo pi-gen stage: System configuration
# Plymouth, boot params, fan control, udev, sudoers, kiosk, cursor theme

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
# Single source of truth: the parameter list lives in install/boot-common.sh and
# the writer in install/display.sh, exactly as for config.txt below. Restating
# them here is what let the two provisioning paths drift — the inline list said
# `cfg80211.ieee80211_regdom=FR` while the installer's said `=00`, so a flashed
# unit started under French radio rules wherever it was sold, until someone
# opened the WiFi-country setting. Every other token was identical, which is how
# the divergence survived: nothing compared the two lists.

on_chroot << 'CHROOT'
cd /home/milo/milo
source install/common.sh
source install/boot-common.sh
source install/display.sh
configure_cmdline "$BOOT_PARAMS_COMMON $BOOT_PARAMS_SCREEN"
CHROOT

# ── Boot config.txt (silent boot, fan, power-button LED) ─────────────────────
# Single source of truth: reuse the install/ functions so pi-gen and the bash
# installer write identical config.txt entries (no duplicated sed/cat here).
# The EEPROM "wait for power button" half cannot be baked into an image — it is
# applied on the device by milo-eeprom-setup.service (enabled in 01-run.sh).

on_chroot << 'CHROOT'
cd /home/milo/milo
source install/common.sh
source install/boot-common.sh
source install/system.sh
source install/power-button.sh
configure_silent_boot
configure_fan_control
configure_power_led
CHROOT

# ── IR remote (Apple Remote via TSOP4838 on GPIO17) ──────────────────────────
# The ir-keytable package is installed in 00-install-deps (apt lists are wiped
# before this stage, so no apt here).
# Reuse install/ir-remote.sh as the single source of truth: gpio-ir overlay in
# config.txt, keymap helper scripts + sudoers, and the boot keytable service.
on_chroot << 'CHROOT'
cd /home/milo/milo
source install/common.sh
source install/ir-remote.sh
mkdir -p /etc/rc_keymaps
configure_ir_overlay
install_ir_helpers
install_ir_systemd_service
CHROOT

# ── BlueZ LE connection parameters ───────────────────────────────────────────
# Tune /etc/bluetooth/main.conf [LE] for low-power BLE HID remotes.
# Reuse install/bluez-le.sh (file-only sed; its trailing
# `systemctl restart bluetooth` is guarded with `|| true`, safe in the chroot).
on_chroot << 'CHROOT'
cd /home/milo/milo
source install/common.sh
source install/bluez-le.sh
configure_bluez_le
CHROOT

# ── Journald: persistent storage + limits ─────────────────────────────────────
# Persistent (on-disk) storage so logs survive a reboot — a RAM-only journal
# wipes the evidence of any boot-time failure (e.g. NetworkManager not
# starting). The 100 MB / 7-day caps bound SD-card wear.
#
# Applied as an /etc/ drop-in: Raspberry Pi OS ships
# /usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf (Storage=volatile),
# and any drop-in overrides the main journald.conf, so editing the main file
# would be silently ignored. An /etc/ drop-in outranks the vendor /usr/lib/ one.

on_chroot << 'CHROOT'
mkdir -p /etc/systemd/journald.conf.d
cp /home/milo/milo/rootfs/etc/systemd/journald.conf.d/99-milo-journald.conf \
    /etc/systemd/journald.conf.d/99-milo-journald.conf
# Create the persistent journal directory so logs are kept from first boot.
mkdir -p /var/log/journal
CHROOT

# ── udev rules ───────────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
# Apple SuperDrive initialization (sg_raw magic command on USB attach)
cp /home/milo/milo/rootfs/etc/udev/rules.d/90-milo-cd.rules /etc/udev/rules.d/
chmod 0644 /etc/udev/rules.d/90-milo-cd.rules

# Screen brightness control (HID + backlight)
cp /home/milo/milo/rootfs/etc/udev/rules.d/99-milo-screen.rules /etc/udev/rules.d/
chmod 0644 /etc/udev/rules.d/99-milo-screen.rules

# Fan control (runtime PWM fan control without sudo)
cp /home/milo/milo/rootfs/etc/udev/rules.d/99-milo-fan.rules /etc/udev/rules.d/
chmod 0644 /etc/udev/rules.d/99-milo-fan.rules

# DSI backlight permissions
tee /etc/udev/rules.d/99-backlight.rules > /dev/null << 'EOF'
SUBSYSTEM=="backlight", RUN+="/bin/chmod 0666 /sys/class/backlight/%k/brightness"
EOF
CHROOT

# ── Sudoers ───────────────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
# Consolidated sudoers for the milo backend service. Same file install/system.sh
# deploys, so a flashed image and a script-installed unit grant the same set.
cp /home/milo/milo/rootfs/etc/sudoers.d/milo-backend /etc/sudoers.d/milo-backend
visudo -c -f /etc/sudoers.d/milo-backend || { echo "FATAL: sudoers syntax error"; exit 1; }
chmod 0440 /etc/sudoers.d/milo-backend

# Client sudoers (if exists). Validated like the server policy above: sudo
# skips a file with a syntax error rather than refusing to run, so a malformed
# policy drops every grant in it without saying so.
if [ -f /home/milo/milo/milo-client/rootfs/etc/sudoers.d/milo-client ]; then
    cp /home/milo/milo/milo-client/rootfs/etc/sudoers.d/milo-client /etc/sudoers.d/
    visudo -c -f /etc/sudoers.d/milo-client || { echo "FATAL: sudoers syntax error"; exit 1; }
    chmod 0440 /etc/sudoers.d/milo-client
fi
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
systemctl disable lightdm.service 2>/dev/null || true
systemctl mask lightdm.service 2>/dev/null || true
apt-get remove -y lightdm 2>/dev/null || true
CHROOT

# ── Mask getty@tty1 (kiosk takes over) ────────────────────────────────────────

on_chroot << 'CHROOT'
systemctl mask getty@tty1.service
CHROOT
