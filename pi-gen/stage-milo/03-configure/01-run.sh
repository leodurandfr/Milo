#!/bin/bash -e
# Milo pi-gen stage: Enable services and finalize system configuration

# ── Set default boot target ──────────────────────────────────────────────────

on_chroot << 'CHROOT'
# graphical.target is required for milo-kiosk.service (WantedBy=graphical.target)
systemctl set-default graphical.target
CHROOT

# ── Enable boot services ─────────────────────────────────────────────────────

on_chroot << 'CHROOT'
systemctl enable milo-first-boot.service
systemctl enable milo-backend.service
systemctl enable milo-readiness.service
systemctl enable milo-kiosk.service
systemctl enable milo-bluealsa.service
systemctl enable milo-bluealsa-aplay.service
systemctl enable milo-disable-wifi-power-management.service
systemctl enable milo-eeprom-setup.service
systemctl enable milo-camilladsp.service
systemctl enable nqptp.service
systemctl enable seatd.service
systemctl enable avahi-daemon
systemctl enable nginx
CHROOT

# ── Disable conflicting default services ─────────────────────────────────────

on_chroot << 'CHROOT'
# These services are managed dynamically by the Milo backend, not at boot
# milo-spotify, milo-mac, milo-radio, milo-airplay,
# milo-snapserver-multiroom, milo-snapclient-multiroom

# Default Snapcast services conflict with Milo-managed ones
systemctl disable snapserver.service 2>/dev/null || true
systemctl disable snapclient.service 2>/dev/null || true

# milo-client services are not enabled by default (milo-first-boot auto-detects mode)
systemctl disable milo-client.service 2>/dev/null || true
systemctl disable milo-client-snapclient.service 2>/dev/null || true
systemctl disable milo-client-camilladsp.service 2>/dev/null || true
CHROOT

# ── Optimize boot performance ────────────────────────────────────────────────

on_chroot << 'CHROOT'
# NetworkManager-wait-online adds ~13s to boot and is not needed
systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
systemctl mask NetworkManager-wait-online.service 2>/dev/null || true
CHROOT

# ── Final cleanup ────────────────────────────────────────────────────────────

on_chroot << 'CHROOT'
# Clean apt cache
apt-get clean
rm -rf /var/lib/apt/lists/*

# Remove temporary files
rm -rf /tmp/*

# Ensure correct ownership on all Milo data
chown -R milo:milo /var/lib/milo
CHROOT
