#!/bin/bash -e
# Milo pi-gen stage: Unit states, boot lines, and the final cleanup

# ── Set default boot target ──────────────────────────────────────────────────

on_chroot << 'CHROOT'
# graphical.target is required for milo-kiosk.service (WantedBy=graphical.target)
systemctl set-default graphical.target
CHROOT

# ── The kiosk, enabled for a first boot ──────────────────────────────────────
# A seed, not a manifest unit: from the setup wizard on, milo-apply-hardware
# enables it or disables it for the screen the unit is given, and an update must
# not turn a kiosk back on over no screen.

on_chroot << 'CHROOT'
systemctl enable milo-kiosk.service
CHROOT

# ── Disable conflicting default services ─────────────────────────────────────

on_chroot << 'CHROOT'
# These services are managed dynamically by the Milo backend, not at boot
# milo-spotify, milo-qobuz, milo-tidal, milo-mac, milo-radio, milo-airplay,
# milo-snapserver-multiroom, milo-snapclient-multiroom, and milo-navidrome (the
# Music Library catalog, started only while the dock enables Music Library)

# Default Snapcast services conflict with Milo-managed ones
systemctl disable snapserver.service 2>/dev/null || true
systemctl disable snapclient.service 2>/dev/null || true

# milo-client services are not enabled by default (milo-first-boot auto-detects mode)
systemctl disable milo-client.service 2>/dev/null || true
systemctl disable milo-client-snapclient.service 2>/dev/null || true
systemctl disable milo-client-camilladsp.service 2>/dev/null || true

# Enabled by the base image; the system manifest masks it as well
systemctl disable NetworkManager-wait-online.service 2>/dev/null || true
CHROOT

# ── The system manifest: boot lines and unit states ──────────────────────────
# config.txt's managed block, cmdline.txt's managed tokens, and every unit the
# manifest enables or masks — by the installed milo-deploy-update, over the
# checkout's system-manifest.env: the code and the declaration every Milō update
# applies them with, so a card flashed with a release and a unit updated to it
# end the same. After everything above, so its masks come after the disables.

on_chroot << 'CHROOT'
/usr/local/bin/milo-deploy-update apply-system-manifest
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
