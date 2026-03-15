#!/bin/bash -e
# Milo pi-gen stage: Clone repository, build application
#
# MILO_BRANCH can be set in the environment to build a specific tag/branch.
# Defaults to "main".

MILO_BRANCH="${MILO_BRANCH:-main}"
MILO_REPO="https://github.com/leodurandfr/Milo.git"

# Clone the Milo repository
on_chroot << CHROOT
sudo -u milo git clone --branch ${MILO_BRANCH} --single-branch "${MILO_REPO}" /home/milo/milo
CHROOT

# Set up Python virtual environment and install dependencies
on_chroot << 'CHROOT'
sudo -u milo python3 -m venv /home/milo/milo/venv
sudo -u milo bash -c "source /home/milo/milo/venv/bin/activate && pip install --upgrade pip"
sudo -u milo bash -c "source /home/milo/milo/venv/bin/activate && pip install -r /home/milo/milo/requirements.txt"
CHROOT

# Build frontend
on_chroot << 'CHROOT'
cd /home/milo/milo/frontend
sudo -u milo npm install
sudo -u milo npm run build
CHROOT

# Set up milo-client Python environment (for unified image supporting both modes)
on_chroot << 'CHROOT'
sudo -u milo python3 -m venv /home/milo/milo/milo-client/venv
sudo -u milo bash -c "source /home/milo/milo/milo-client/venv/bin/activate && pip install --upgrade pip"
sudo -u milo bash -c "source /home/milo/milo/milo-client/venv/bin/activate && pip install -r /home/milo/milo/milo-client/app/requirements.txt"
sudo -u milo bash -c "source /home/milo/milo/milo-client/venv/bin/activate && pip install git+https://github.com/HEnquist/pycamilladsp.git"
CHROOT

# Write app version from git
on_chroot << 'CHROOT'
mkdir -p /var/lib/milo
APP_VERSION=$(sudo -u milo git -C /home/milo/milo describe --tags --always 2>/dev/null || echo "unknown")
echo "$APP_VERSION" > /var/lib/milo/app-version
chown milo:milo /var/lib/milo/app-version
CHROOT

# Clean up npm/pip caches to reduce image size
on_chroot << 'CHROOT'
sudo -u milo rm -rf /home/milo/.npm /home/milo/.cache/pip
rm -rf /root/.npm /root/.cache/pip
CHROOT
