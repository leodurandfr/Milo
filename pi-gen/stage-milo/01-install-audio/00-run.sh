#!/bin/bash -e
# Milo pi-gen stage: Install pre-compiled audio binaries

# The validated dependency set, copied in beside this stage by pi-gen/build.sh.
# A stage is built from a copy of stage-milo/ inside a cloned pi-gen checkout —
# possibly in Docker — so it cannot reach back into the Milō repo for it; that
# copy is why the file is a sibling here and not a relative path into the tree.
source "$(dirname "${BASH_SOURCE[0]}")/../dependencies.env"

# Every file below is checked against the digest the manifest declares beside
# its version before it is unpacked or handed to apt, as the in-app update and
# its root helper check theirs: `sha256sum -c` exits non-zero on a mismatch, and
# `bash -e` stops the build there.

# go-librespot
on_chroot << CHROOT
cd /tmp
wget -q "https://github.com/devgianlu/go-librespot/releases/download/v${GO_LIBRESPOT_VERSION}/go-librespot_linux_arm64.tar.gz"
echo "${GO_LIBRESPOT_SHA256}  go-librespot_linux_arm64.tar.gz" | sha256sum -c -
tar -xzf go-librespot_linux_arm64.tar.gz
cp go-librespot /usr/local/bin/
chmod +x /usr/local/bin/go-librespot
rm -f go-librespot_linux_arm64.tar.gz go-librespot
CHROOT

# Navidrome (Music Library catalog engine — single static Go binary)
on_chroot << CHROOT
cd /tmp
wget -q "https://github.com/navidrome/navidrome/releases/download/v${NAVIDROME_VERSION}/navidrome_${NAVIDROME_VERSION}_linux_arm64.tar.gz"
echo "${NAVIDROME_SHA256}  navidrome_${NAVIDROME_VERSION}_linux_arm64.tar.gz" | sha256sum -c -
tar -xzf "navidrome_${NAVIDROME_VERSION}_linux_arm64.tar.gz" navidrome
cp navidrome /usr/local/bin/
chmod +x /usr/local/bin/navidrome
rm -f "navidrome_${NAVIDROME_VERSION}_linux_arm64.tar.gz" navidrome
CHROOT

# CamillaDSP
on_chroot << CHROOT
cd /tmp
wget -q "https://github.com/HEnquist/camilladsp/releases/download/v${CAMILLADSP_VERSION}/camilladsp-linux-aarch64.tar.gz"
echo "${CAMILLADSP_SHA256}  camilladsp-linux-aarch64.tar.gz" | sha256sum -c -
tar -xzf camilladsp-linux-aarch64.tar.gz
cp camilladsp /usr/local/bin/
chmod +x /usr/local/bin/camilladsp
rm -f camilladsp-linux-aarch64.tar.gz camilladsp
CHROOT

# Snapcast (server + client). One .deb per Debian release, so the image's own
# codename picks it; there is no second release, method or source to fall back on.
on_chroot << CHROOT
cd /tmp
CODENAME=\$(. /etc/os-release && echo "\$VERSION_CODENAME")
[ -n "\$CODENAME" ] || { echo "FATAL: no VERSION_CODENAME in /etc/os-release"; exit 1; }

wget -q "https://github.com/snapcast/snapcast/releases/download/v${SNAPCAST_VERSION}/snapserver_${SNAPCAST_VERSION}-1_arm64_\${CODENAME}.deb"
wget -q "https://github.com/snapcast/snapcast/releases/download/v${SNAPCAST_VERSION}/snapclient_${SNAPCAST_VERSION}-1_arm64_\${CODENAME}.deb"
# The manifest's digests are of the trixie packages: an image of another release
# stops here rather than shipping a package nobody validated.
echo "${SNAPSERVER_SHA256}  snapserver_${SNAPCAST_VERSION}-1_arm64_\${CODENAME}.deb" | sha256sum -c -
echo "${SNAPCLIENT_SHA256}  snapclient_${SNAPCAST_VERSION}-1_arm64_\${CODENAME}.deb" | sha256sum -c -
apt-get install -y "./snapserver_${SNAPCAST_VERSION}-1_arm64_\${CODENAME}.deb" "./snapclient_${SNAPCAST_VERSION}-1_arm64_\${CODENAME}.deb"

for p in snapserver snapclient; do
    [ "\$(dpkg-query -W -f='\${db:Status-Abbrev}\${Version}' \$p)" = "ii ${SNAPCAST_VERSION}-1" ] || { echo "FATAL: \$p is not ${SNAPCAST_VERSION}-1, installed"; exit 1; }
done

rm -f /tmp/snapserver_*.deb /tmp/snapclient_*.deb

# Disable default Snapcast services (Milo manages its own)
systemctl stop snapserver.service snapclient.service 2>/dev/null || true
systemctl disable snapserver.service snapclient.service 2>/dev/null || true
CHROOT
