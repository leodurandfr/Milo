#!/bin/bash -e
# Milo pi-gen stage: Compile audio software from source
#
# These are compiled during the image build so users don't need to compile on-device.
# Versions are defined as variables for easy updates.

# ── Version pins ─────────────────────────────────────────────────────────────
NQPTP_VERSION="1.2.4"
SHAIRPORT_SYNC_VERSION="4.3.7"
BLUEZ_ALSA_VERSION="4.3.1"
ROC_TOOLKIT_VERSION="0.4.0"
# ─────────────────────────────────────────────────────────────────────────────

# NQPTP (AirPlay 2 timing daemon)
on_chroot << CHROOT
cd /tmp
git clone --branch ${NQPTP_VERSION} --depth 1 https://github.com/mikebrady/nqptp.git
cd nqptp
autoreconf -fi
./configure --with-systemd-startup
make -j\$(nproc)
make install
cd /tmp && rm -rf nqptp
CHROOT

# shairport-sync (AirPlay 2)
on_chroot << CHROOT
cd /tmp
git clone --branch ${SHAIRPORT_SYNC_VERSION} --depth 1 https://github.com/mikebrady/shairport-sync.git
cd shairport-sync
autoreconf -fi
./configure --sysconfdir=/etc \
    --with-alsa \
    --with-avahi \
    --with-ssl=openssl \
    --with-soxr \
    --with-metadata \
    --with-airplay-2 \
    --with-dbus-interface
make -j\$(nproc)
make install
cd /tmp && rm -rf shairport-sync

# Disable default shairport-sync service (Milo manages its own)
systemctl disable shairport-sync.service 2>/dev/null || true
CHROOT

# bluez-alsa
on_chroot << CHROOT
cd /tmp
git clone --branch v${BLUEZ_ALSA_VERSION} --depth 1 https://github.com/arkq/bluez-alsa.git
cd bluez-alsa
autoreconf --install
mkdir build && cd build
../configure --prefix=/usr --disable-systemd \
    --with-alsaplugindir=/usr/lib/aarch64-linux-gnu/alsa-lib \
    --with-bluealsauser=milo --with-bluealsaaplayuser=milo \
    --enable-cli \
    --enable-aac --enable-aptx --enable-aptx-hd --with-libfreeaptx
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

# Clean up build caches to reduce image size. The apt *lists* deliberately stay:
# later stages still install packages (02-install-milo pulls libportaudio2 for
# qobuz-proxy), and without an index apt has no candidate version. 03-configure's
# final cleanup wipes them once nothing else needs apt.
on_chroot << 'CHROOT'
apt-get clean
rm -rf /tmp/*
CHROOT
