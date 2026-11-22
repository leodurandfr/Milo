#!/bin/bash -e
# Milo pi-gen stage: Compile audio software from source
#
# These are compiled during the image build so users don't need to compile on-device.

# The validated dependency set — see the note in 00-run.sh for why it is a
# sibling file rather than a path into the repo.
source "$(dirname "${BASH_SOURCE[0]}")/../dependencies.env"

# NQPTP (AirPlay 2 timing daemon). No --with-systemd-startup: its unit is
# Milō's (system/nqptp.service), copied in by 02-install-milo.
#
# NQPTP and shairport-sync are built from their tag's source archive, checked
# against the manifest's digest, not from a clone: a tag is a pointer its owner
# can move, and the archive is the same file the in-app update builds from
# (UpdateService._download_source), so an image and an updated unit compile the
# same bytes.
on_chroot << CHROOT
cd /tmp
wget -q -O nqptp.tar.gz "https://github.com/mikebrady/nqptp/archive/refs/tags/${NQPTP_VERSION}.tar.gz"
echo "${NQPTP_SHA256}  nqptp.tar.gz" | sha256sum -c -
tar -xzf nqptp.tar.gz && rm -f nqptp.tar.gz
cd nqptp-${NQPTP_VERSION}
autoreconf -fi
./configure
make -j\$(nproc)
make install
cd /tmp && rm -rf nqptp-${NQPTP_VERSION}
CHROOT

# shairport-sync (AirPlay 2)
on_chroot << CHROOT
cd /tmp
wget -q -O shairport-sync.tar.gz "https://github.com/mikebrady/shairport-sync/archive/refs/tags/${SHAIRPORT_SYNC_VERSION}.tar.gz"
echo "${SHAIRPORT_SYNC_SHA256}  shairport-sync.tar.gz" | sha256sum -c -
tar -xzf shairport-sync.tar.gz && rm -f shairport-sync.tar.gz
cd shairport-sync-${SHAIRPORT_SYNC_VERSION}
autoreconf -fi
./configure --sysconfdir=/etc \
    --with-alsa \
    --with-avahi \
    --with-ssl=openssl \
    --with-soxr \
    --with-metadata \
    --with-metadata-pipe \
    --with-airplay-2 \
    --with-dbus-interface
make -j\$(nproc)
make install
cd /tmp && rm -rf shairport-sync-${SHAIRPORT_SYNC_VERSION}

# Disable default shairport-sync service (Milo manages its own)
systemctl disable shairport-sync.service 2>/dev/null || true
CHROOT

# bluez-alsa and roc-toolkit are cloned by tag, and each clone is held to the
# commit the manifest declares for that tag (`*_COMMIT`): a tag is a pointer
# its owner can move, and the commit is what was validated.
on_chroot << CHROOT
cd /tmp
git clone --branch v${BLUEZ_ALSA_VERSION} --depth 1 https://github.com/arkq/bluez-alsa.git
cd bluez-alsa
[ "\$(git rev-parse HEAD)" = "${BLUEZ_ALSA_COMMIT}" ] || { echo "bluez-alsa v${BLUEZ_ALSA_VERSION} is not commit ${BLUEZ_ALSA_COMMIT}" >&2; exit 1; }
autoreconf --install
mkdir build && cd build
../configure --prefix=/usr --localstatedir=/var --disable-systemd \
    --with-alsaplugindir=/usr/lib/aarch64-linux-gnu/alsa-lib \
    --with-bluealsauser=milo --with-bluealsaaplayuser=milo \
    --enable-cli \
    --enable-aptx --enable-aptx-hd --with-libfreeaptx
make -j\$(nproc)
make install
ldconfig
cd /tmp && rm -rf bluez-alsa

# Disable default bluez-alsa services (Milo manages its own)
systemctl disable bluealsa-aplay.service bluealsa.service 2>/dev/null || true
CHROOT

# roc-toolkit
on_chroot << CHROOT
cd /tmp
git clone --branch v${ROC_TOOLKIT_VERSION} --depth 1 https://github.com/roc-streaming/roc-toolkit.git
cd roc-toolkit
[ "\$(git rev-parse HEAD)" = "${ROC_TOOLKIT_COMMIT}" ] || { echo "roc-toolkit v${ROC_TOOLKIT_VERSION} is not commit ${ROC_TOOLKIT_COMMIT}" >&2; exit 1; }
scons -Q --build-3rdparty=openfec
scons -Q --build-3rdparty=openfec install
ldconfig
cd /tmp && rm -rf roc-toolkit
CHROOT

# Remove PulseAudio/PipeWire now that compilation is complete (ALSA only)
# libpulse-dev (needed by roc-toolkit) pulls in pulseaudio as a dependency
on_chroot << 'CHROOT'
apt-get remove -y pulseaudio pipewire 2>/dev/null || true
apt-get autoremove -y
CHROOT

# cloud-init out of the image: Raspberry Pi Imager's OS customization reaches a
# trixie card through it, renaming the milo account and rewriting the hostname,
# and stage2 installs it whatever ENABLE_CLOUD_INIT says. The root partition is
# grown by raspberrypi-sys-mods' initramfs hook, not by cloud-init's growpart.
# netplan.io stays: network-manager depends on it, and the mark keeps the
# autoremove off it even if that dependency ever loosens.
on_chroot << 'CHROOT'
apt-mark manual netplan.io
apt-get purge -y cloud-init rpi-cloud-init-mods
apt-get autoremove --purge -y
CHROOT

# Clean up build caches to reduce image size. The apt *lists* stay until
# 03-configure's final cleanup wipes them: every package is installed by
# 00-install-deps, from the system manifest, and nothing after it needs apt.
on_chroot << 'CHROOT'
apt-get clean
rm -rf /tmp/*
CHROOT
