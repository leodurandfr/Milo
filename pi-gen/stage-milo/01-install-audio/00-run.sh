#!/bin/bash -e
# Milo pi-gen stage: Install pre-compiled audio binaries
#
# Versions are defined as variables for easy updates.

# ── Version pins ─────────────────────────────────────────────────────────────
GO_LIBRESPOT_VERSION="0.8.0"
CAMILLADSP_VERSION="4.1.3"
SNAPCAST_VERSION="0.35.0"
# Keep in sync with install/navidrome.sh::NAVIDROME_VERSION (single source of truth
# for the config; the binary is downloaded here for the image, there for bash installs).
NAVIDROME_VERSION="0.63.2"
# ─────────────────────────────────────────────────────────────────────────────

# go-librespot
on_chroot << CHROOT
cd /tmp
wget -q "https://github.com/devgianlu/go-librespot/releases/download/v${GO_LIBRESPOT_VERSION}/go-librespot_linux_arm64.tar.gz"
tar -xzf go-librespot_linux_arm64.tar.gz
cp go-librespot /usr/local/bin/
chmod +x /usr/local/bin/go-librespot
rm -f go-librespot_linux_arm64.tar.gz go-librespot
CHROOT

# Navidrome (Music Library catalog engine — single static Go binary)
on_chroot << CHROOT
cd /tmp
wget -q "https://github.com/navidrome/navidrome/releases/download/v${NAVIDROME_VERSION}/navidrome_${NAVIDROME_VERSION}_linux_arm64.tar.gz"
tar -xzf "navidrome_${NAVIDROME_VERSION}_linux_arm64.tar.gz" navidrome
cp navidrome /usr/local/bin/
chmod +x /usr/local/bin/navidrome
rm -f "navidrome_${NAVIDROME_VERSION}_linux_arm64.tar.gz" navidrome
CHROOT

# CamillaDSP
on_chroot << CHROOT
cd /tmp
wget -q "https://github.com/HEnquist/camilladsp/releases/download/v${CAMILLADSP_VERSION}/camilladsp-linux-aarch64.tar.gz"
tar -xzf camilladsp-linux-aarch64.tar.gz
cp camilladsp /usr/local/bin/
chmod +x /usr/local/bin/camilladsp
rm -f camilladsp-linux-aarch64.tar.gz camilladsp
CHROOT

# Snapcast (server + client)
on_chroot << CHROOT
cd /tmp
DEBIAN_VERSION=\$(lsb_release -sc 2>/dev/null || grep VERSION_CODENAME /etc/os-release | cut -d= -f2)
DEBIAN_VERSION=\${DEBIAN_VERSION:-bookworm}

SNAP_INSTALLED=false

# Try detected version first, then bookworm fallback
for DEB_VER in "\$DEBIAN_VERSION" "bookworm"; do
    if wget -q "https://github.com/snapcast/snapcast/releases/download/v${SNAPCAST_VERSION}/snapserver_${SNAPCAST_VERSION}-1_arm64_\${DEB_VER}.deb" 2>/dev/null && \
       wget -q "https://github.com/snapcast/snapcast/releases/download/v${SNAPCAST_VERSION}/snapclient_${SNAPCAST_VERSION}-1_arm64_\${DEB_VER}.deb" 2>/dev/null; then
        if apt-get install -y "./snapserver_${SNAPCAST_VERSION}-1_arm64_\${DEB_VER}.deb" "./snapclient_${SNAPCAST_VERSION}-1_arm64_\${DEB_VER}.deb" || \
           { dpkg -i "snapserver_${SNAPCAST_VERSION}-1_arm64_\${DEB_VER}.deb" "snapclient_${SNAPCAST_VERSION}-1_arm64_\${DEB_VER}.deb" && \
             apt-get --fix-broken install -y; }; then
            SNAP_INSTALLED=true
            break
        fi
    fi
    rm -f snapserver_*.deb snapclient_*.deb
done

# Fallback to apt repositories
if [ "\$SNAP_INSTALLED" != "true" ]; then
    apt-get install -y snapserver snapclient
fi

rm -f /tmp/snapserver_*.deb /tmp/snapclient_*.deb

# Disable default Snapcast services (Milo manages its own)
systemctl stop snapserver.service snapclient.service 2>/dev/null || true
systemctl disable snapserver.service snapclient.service 2>/dev/null || true
CHROOT
