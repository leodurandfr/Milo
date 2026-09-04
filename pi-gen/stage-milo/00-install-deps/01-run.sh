#!/bin/bash -e
# Milo pi-gen stage: Install non-apt dependencies and configure base system

# Install Chromium (package name varies by distribution)
on_chroot << 'CHROOT'
if ! apt-get install -y chromium 2>/dev/null; then
    apt-get install -y chromium-browser
fi
CHROOT

# Note: PulseAudio/PipeWire removal is done after compilation in 01-install-audio/01-run.sh
# because libpulse-dev (needed by roc-toolkit) pulls in pulseaudio as a dependency.

# Add milo user to required groups (pi-gen creates the user via FIRST_USER_NAME).
# Each entry is a device node Milō opens. Raspberry Pi OS already grants all of
# these to the image's first user, so this is belt-and-braces — but stating the
# list is what the appliance needs, rather than trusting what the base image
# happens to grant, is the only reason a rotary encoder ever worked: `gpio` and
# `render` are the two nothing else guarantees, and hardware that lacks them
# fails silently. usermod -aG is additive and idempotent.
on_chroot << 'CHROOT'
usermod -aG audio,video,render,bluetooth,input,cdrom,gpio milo
CHROOT
