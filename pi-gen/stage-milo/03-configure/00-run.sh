#!/bin/bash -e
# Milo pi-gen stage: System configuration
# Plymouth, IR remote, BlueZ, journald, sysctl, IPv6 firewall, udev, sudoers,
# cursor theme. The boot lines and the unit states are the system manifest's,
# applied at the end of 01-run.sh.

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
CHROOT

# ── IR remote (Apple Remote via TSOP4838 on GPIO17) ──────────────────────────
# The ir-keytable package is a package of the system manifest, and the boot
# keytable service one of its units. Reuse provisioning/ir-remote.sh as the
# single source of truth for the rest: the gpio-ir overlay's default in
# config.txt (milo-apply-hardware owns that block from the setup wizard on),
# and the keymap helper scripts + sudoers.
on_chroot << 'CHROOT'
cd /home/milo/milo
source provisioning/common.sh
source provisioning/ir-remote.sh
mkdir -p /etc/rc_keymaps
configure_ir_overlay
install_ir_helpers
CHROOT

# ── BlueZ LE connection parameters ───────────────────────────────────────────
# Tune /etc/bluetooth/main.conf [LE] for low-power BLE HID remotes.
# Reuse provisioning/bluez-le.sh (file-only sed; its trailing
# `systemctl restart bluetooth` is guarded with `|| true`, safe in the chroot).
on_chroot << 'CHROOT'
cd /home/milo/milo
source provisioning/common.sh
source provisioning/bluez-le.sh
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

# ── ARP: each interface answers for its own addresses ────────────────────────
# eth0 and wlan0 share the LAN; without this eth0 answers ARP for wlan0's
# address, and NetworkManager's conflict probe at a lease renewal removes it
# (see the file). Read by systemd-sysctl at every boot, in either mode.

on_chroot << 'CHROOT'
cp /home/milo/milo/rootfs/etc/sysctl.d/90-milo-arp.conf /etc/sysctl.d/90-milo-arp.conf
CHROOT

# ── Link down: a cable without carrier is never routed through ───────────────
# eth0's routes outrank wlan0's; this drops them the instant the cable goes,
# rather than when NetworkManager removes the address (see the file).

on_chroot << 'CHROOT'
cp /home/milo/milo/rootfs/etc/sysctl.d/90-milo-linkdown.conf /etc/sysctl.d/90-milo-linkdown.conf
CHROOT

# ── IPv6 firewall: nothing from beyond the link ──────────────────────────────
# One `ip6` table replaces Debian's /etc/nftables.conf: a new inbound IPv6
# connection is accepted from the link and the unit's own prefixes only (see
# the file). nftables itself is in the base image; the system manifest enables
# it. The satellite tree carries the same three files, so a device converted by
# milo-first-boot keeps them as installed here.

on_chroot << 'CHROOT'
cp /home/milo/milo/rootfs/etc/nftables.conf /etc/nftables.conf
# 0644, the mode every update installs it with: nftables.service reads it with
# `nft -f` and nothing executes it, where the cp keeps Debian's 0755.
chmod 0644 /etc/nftables.conf
cp /home/milo/milo/rootfs/etc/NetworkManager/dispatcher.d/50-milo-ipv6-link-prefixes /etc/NetworkManager/dispatcher.d/
chmod 0755 /etc/NetworkManager/dispatcher.d/50-milo-ipv6-link-prefixes
mkdir -p /etc/systemd/system/nftables.service.d
cp /home/milo/milo/rootfs/etc/systemd/system/nftables.service.d/milo-link-prefixes.conf \
    /etc/systemd/system/nftables.service.d/milo-link-prefixes.conf
CHROOT

# ── udev rules ───────────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
# Apple SuperDrive initialization (sg_raw magic command on USB attach)
cp /home/milo/milo/rootfs/etc/udev/rules.d/90-milo-cd.rules /etc/udev/rules.d/
chmod 0644 /etc/udev/rules.d/90-milo-cd.rules

# Screen brightness control (HID + backlight). The one thing either panel needs
# from the image: the backend sets the 8" DSI's brightness by writing its sysfs
# backlight (backend/hardware/screen.py) and the 7" USB's through
# milo-brightness-7, and this rule grants the milo user both. Waveshare's
# Brightness.zip, which this stage used to download unverified and run as root
# under `|| true`, installs a Qt desktop slider into /usr/share/applications —
# a menu this kiosk never shows — writing that same sysfs file.
cp /home/milo/milo/rootfs/etc/udev/rules.d/99-milo-screen.rules /etc/udev/rules.d/
chmod 0644 /etc/udev/rules.d/99-milo-screen.rules

# Fan control (runtime PWM fan control without sudo)
cp /home/milo/milo/rootfs/etc/udev/rules.d/99-milo-fan.rules /etc/udev/rules.d/
chmod 0644 /etc/udev/rules.d/99-milo-fan.rules

# No Bluetooth input device may act as a logind power/sleep button
cp /home/milo/milo/rootfs/etc/udev/rules.d/71-milo-bt-no-power-switch.rules /etc/udev/rules.d/
chmod 0644 /etc/udev/rules.d/71-milo-bt-no-power-switch.rules
CHROOT

# ── Sudoers ───────────────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
# Consolidated sudoers for the milo backend service: the rootfs/ file every Milō
# update installs too (milo-deploy-update, which validates it the same way).
cp /home/milo/milo/rootfs/etc/sudoers.d/milo-backend /etc/sudoers.d/milo-backend
visudo -c -f /etc/sudoers.d/milo-backend || { echo "FATAL: sudoers syntax error"; exit 1; }
chmod 0440 /etc/sudoers.d/milo-backend

# Client sudoers — every policy the satellite tree carries, as the conversion
# in milo-first-boot and milo-client-deploy-update install them. Validated like
# the server policy above: sudo skips a file with a syntax error rather than
# refusing to run, so a malformed policy drops every grant in it without saying
# so. On this server image they are inert or repeat milo-backend: milo-account
# sorts before it and carries the same first rule.
for policy in /home/milo/milo/milo-client/rootfs/etc/sudoers.d/*; do
    [ -f "$policy" ] || continue
    name=$(basename "$policy")
    cp "$policy" "/etc/sudoers.d/$name"
    visudo -c -f "/etc/sudoers.d/$name" || { echo "FATAL: sudoers syntax error in $name"; exit 1; }
    chmod 0440 "/etc/sudoers.d/$name"
done
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

# ── Clear /etc/issue ─────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
cp /etc/issue /etc/issue.backup 2>/dev/null || true
echo "" > /etc/issue
rm -f /etc/issue.d/IP.issue
CHROOT

# ── No lightdm ───────────────────────────────────────────────────────────────
# Absent from a Lite base; removed should anything ever pull it in. The system
# manifest masks it, as it masks getty@tty1 for the kiosk.

on_chroot << 'CHROOT'
systemctl disable lightdm.service 2>/dev/null || true
apt-get remove -y lightdm 2>/dev/null || true
CHROOT
