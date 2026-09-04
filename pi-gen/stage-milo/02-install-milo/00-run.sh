#!/bin/bash -e
# Milo pi-gen stage: install the application at a given ref
#
# MILO_BRANCH can be set in the environment to build a specific tag/branch.
# Defaults to "main".

MILO_BRANCH="${MILO_BRANCH:-main}"
MILO_REPO="https://github.com/leodurandfr/Milo.git"
FRONTEND_TARBALL="$(dirname "${BASH_SOURCE[0]}")/../frontend-dist.tar.gz"

# The frontend is not built here. It is built once in CI, published with the
# release, and installed from that artefact both here and by every OTA update —
# so an image and a unit updated to the same release run the same bytes. It
# travels as a sibling of the stage, like dependencies.env, because a stage is
# built from a copy inside a cloned pi-gen checkout that cannot reach this repo.
#
# Missing is fatal rather than a fallback to `npm run build`: a fallback is the
# second, divergent build path this exists to remove, and it would fire silently.
[ -f "${FRONTEND_TARBALL}" ] || {
    echo "ERROR: ${FRONTEND_TARBALL} is missing." >&2
    echo "Both image builders copy it in — see pi-gen/build.sh and" >&2
    echo ".github/workflows/build-image.yml. The image cannot be built without it." >&2
    exit 1
}

# Clone the whole repository, then check out the ref being built.
#
# NOT `--branch <tag> --single-branch`: that leaves the refspec
# `+refs/tags/<tag>:refs/tags/<tag>`, so a unit flashed from a release image can
# fetch nothing but the tag it was built from — measured — and every later
# release is unreachable from the update button, forever. The default refspec is
# what makes `git fetch origin --tags` in the update flow able to see anything.
on_chroot << CHROOT
sudo -u milo git clone "${MILO_REPO}" /home/milo/milo
sudo -u milo git -C /home/milo/milo checkout --force ${MILO_BRANCH}
CHROOT

# Set up Python virtual environment and install dependencies
on_chroot << 'CHROOT'
sudo -u milo python3 -m venv /home/milo/milo/venv
sudo -u milo bash -c "source /home/milo/milo/venv/bin/activate && pip install --upgrade pip"
sudo -u milo bash -c "source /home/milo/milo/venv/bin/activate && pip install -r /home/milo/milo/requirements.txt"
CHROOT

# Install the frontend artefact built in CI
cp "${FRONTEND_TARBALL}" "${ROOTFS_DIR}/tmp/frontend-dist.tar.gz"
chmod 0644 "${ROOTFS_DIR}/tmp/frontend-dist.tar.gz"
on_chroot << 'CHROOT'
sudo -u milo tar -xzf /tmp/frontend-dist.tar.gz -C /home/milo/milo/frontend
rm -f /tmp/frontend-dist.tar.gz
# The stage must not continue with a frontend nginx would serve half of.
test -f /home/milo/milo/frontend/dist/index.html
CHROOT

# Set up milo-client Python environment (for unified image supporting both modes)
on_chroot << 'CHROOT'
sudo -u milo python3 -m venv /home/milo/milo/milo-client/venv
sudo -u milo bash -c "source /home/milo/milo/milo-client/venv/bin/activate && pip install --upgrade pip"
sudo -u milo bash -c "source /home/milo/milo/milo-client/venv/bin/activate && pip install -r /home/milo/milo/milo-client/app/requirements.txt"
sudo -u milo bash -c "source /home/milo/milo/milo-client/venv/bin/activate && pip install git+https://github.com/HEnquist/pycamilladsp.git"
CHROOT

# Seed the satellite's own identity, for the half of this unified image that
# may become a client. Both values are what the server would have sent it: the
# release this image was built at, and the fingerprint of the milo-client/ tree
# it carries. Without them a freshly flashed satellite reports no version and is
# offered a push of the code it is already running.
#
# The server needs no equivalent: `git describe` on its checkout is the answer,
# and it is what the backend already asks. /var/lib/milo/app-version was written
# here for years and read by nothing.
on_chroot << 'CHROOT'
mkdir -p /var/lib/milo-client
VERSION=$(sudo -u milo git -C /home/milo/milo describe --tags --always)
PAYLOAD=$(sudo -u milo git -C /home/milo/milo log -1 --format=%h -- milo-client)
printf '%s' "$VERSION" > /var/lib/milo-client/app-version
printf '%s' "$PAYLOAD" > /var/lib/milo-client/app-payload
CHROOT

# Clean up the pip cache to reduce image size
on_chroot << 'CHROOT'
sudo -u milo rm -rf /home/milo/.cache/pip
rm -rf /root/.cache/pip
CHROOT
