#!/bin/bash -e
# Milo pi-gen stage: Install non-apt dependencies and configure base system

# Install Chromium (package name varies by distribution)
on_chroot << 'CHROOT'
if ! apt-get install -y chromium 2>/dev/null; then
    apt-get install -y chromium-browser
fi
CHROOT

# Install libflac (package name varies by version)
on_chroot << 'CHROOT'
apt-get install -y libflac12t64 2>/dev/null || apt-get install -y libflac12 || true
CHROOT

# Upgrade Node.js to latest stable via n
on_chroot << 'CHROOT'
npm install -g n
n stable
npm install -g npm@latest
hash -r
CHROOT

# Note: PulseAudio/PipeWire removal is done after compilation in 01-install-audio/01-run.sh
# because libpulse-dev (needed by roc-toolkit) pulls in pulseaudio as a dependency.

# Add milo user to required groups (pi-gen creates the user via FIRST_USER_NAME).
# Same list as install/base.sh::MILO_USER_GROUPS — each entry is a device node
# Milō opens; see the comment there. This list is what the appliance needs, not
# what the base image happens to grant its first user.
on_chroot << 'CHROOT'
usermod -aG audio,video,render,bluetooth,input,cdrom,gpio milo
CHROOT
