#!/bin/bash -e
# Milo pi-gen stage: install every package the system manifest names

# The system manifest, copied in beside this stage by pi-gen/build.sh and the CI
# image builder, as dependencies.env is: the packages go in before the checkout
# that carries the manifest exists (git is one of them). Later stages read the
# checkout's copy, through milo-deploy-update, the way an update does.
#
# Not pi-gen's own 00-packages list: that is a second declaration no update
# reads, and a package added there alone reached every new card and no unit
# already running (libcairo2, October 2026).
source "$(dirname "${BASH_SOURCE[0]}")/../system-manifest.env"

# With their recommends, as pi-gen installs a list; an update installs only the
# missing ones, without (see the manifest).
on_chroot << CHROOT
apt-get -o Acquire::Retries=3 install -y ${SYSTEM_APT_PACKAGES[*]}
CHROOT
