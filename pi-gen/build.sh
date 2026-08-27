#!/bin/bash
# Milo - Pi-gen Build Wrapper
#
# Clones the official pi-gen repository, copies the Milo stage configuration,
# and launches the image build.
#
# Usage:
#   ./build.sh                    # Build using Docker (recommended)
#   ./build.sh --native           # Build natively (requires Debian/Ubuntu host)
#   MILO_BRANCH=v1.0.0 ./build.sh # Build a specific branch/tag

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIGEN_DIR="${SCRIPT_DIR}/pi-gen-upstream"
PIGEN_REPO="https://github.com/RPi-Distro/pi-gen.git"
PIGEN_BRANCH="arm64"

BUILD_MODE="docker"
if [[ "$1" == "--native" ]]; then
    BUILD_MODE="native"
fi

echo "=== Milo Image Builder ==="
echo "Build mode: ${BUILD_MODE}"
echo "Milo branch: ${MILO_BRANCH:-main}"
echo ""

# Clone or update pi-gen
if [[ -d "${PIGEN_DIR}" ]]; then
    echo "Updating pi-gen..."
    cd "${PIGEN_DIR}"
    git fetch origin
    git checkout "${PIGEN_BRANCH}"
    git reset --hard "origin/${PIGEN_BRANCH}"
else
    echo "Cloning pi-gen..."
    git clone --branch "${PIGEN_BRANCH}" --depth 1 "${PIGEN_REPO}" "${PIGEN_DIR}"
fi

cd "${PIGEN_DIR}"

# Copy Milo configuration
echo "Copying Milo configuration..."
cp "${SCRIPT_DIR}/config" "${PIGEN_DIR}/config"

# Copy custom stage
rm -rf "${PIGEN_DIR}/stage-milo"
cp -r "${SCRIPT_DIR}/stage-milo" "${PIGEN_DIR}/stage-milo"

# The validated dependency set travels with the stage: the stage scripts source
# it as a sibling, and once copied into the pi-gen checkout (and, in Docker, into
# the container) they can no longer reach the Milō repo it lives in. This copy is
# the only reason a single declaration works across all three provisioning trees.
cp "${SCRIPT_DIR}/../dependencies.env" "${PIGEN_DIR}/stage-milo/dependencies.env"

# Pass MILO_BRANCH to the build environment
if [[ -n "${MILO_BRANCH}" ]]; then
    echo "MILO_BRANCH=${MILO_BRANCH}" >> "${PIGEN_DIR}/config"
fi

# Prevent intermediate image exports (only stage-milo exports)
touch "${PIGEN_DIR}/stage2/SKIP_IMAGES"

# Skip stages 3-5 (desktop variants we don't need)
for stage in stage3 stage4 stage5; do
    if [[ -d "${PIGEN_DIR}/${stage}" ]]; then
        touch "${PIGEN_DIR}/${stage}/SKIP"
    fi
done

# Build
echo ""
echo "Starting pi-gen build..."
echo ""

if [[ "${BUILD_MODE}" == "docker" ]]; then
    ./build-docker.sh
else
    ./build.sh
fi

echo ""
echo "=== Build complete ==="
echo "Image location: ${PIGEN_DIR}/deploy/"
ls -lh "${PIGEN_DIR}/deploy/"*.img* 2>/dev/null || echo "No image found in deploy/"
