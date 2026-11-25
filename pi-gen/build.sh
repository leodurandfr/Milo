#!/bin/bash
# Milo - Pi-gen Build Wrapper
#
# Fetches the official pi-gen at the commit upstream.env pins, copies the Milo
# stage configuration, and launches the image build.
#
# Usage:
#   ./build.sh                    # Build using Docker (recommended)
#   ./build.sh --native           # Build natively (requires Debian/Ubuntu host)
#   MILO_BRANCH=v1.0.0 ./build.sh # Build a specific branch/tag, from a
#                                 # checkout of it (refused otherwise)
#   MILO_REPO=https://github.com/<owner>/<repo>.git ./build.sh
#                                 # Clone another repository than this
#                                 # checkout's origin (checkout-env.sh)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIGEN_DIR="${SCRIPT_DIR}/pi-gen-upstream"
# PIGEN_REPO and PIGEN_COMMIT, the pi-gen every image is built on: declared once,
# beside this script, and read by the CI workflow too.
source "${SCRIPT_DIR}/upstream.env"

BUILD_MODE="docker"
if [[ "$1" == "--native" ]]; then
    BUILD_MODE="native"
fi

echo "=== Milo Image Builder ==="
echo "Build mode: ${BUILD_MODE}"
echo "Milo branch: ${MILO_BRANCH:-main}"

# What the image clones, decided before anything is fetched or built: the
# repository this checkout came from, unless MILO_REPO names another, at
# MILO_BRANCH. Refused here, a remote the chroot cannot clone (or one carrying a
# token, which every flashed unit would keep as its origin) costs nothing.
CHECKOUT_ENV="$("${SCRIPT_DIR}/checkout-env.sh" "${MILO_BRANCH:-main}" "${MILO_REPO:-}")"
echo "${CHECKOUT_ENV}"
echo ""

# One build, one tree. The image is made of two: this checkout, which supplies
# pi-gen/config, the stage, dependencies.env, system-manifest.env (whose packages
# 00-install-deps installs) and the frontend; and the clone of MILO_BRANCH the
# stage makes, from which apply-system-manifest applies the unit states and boot
# lines. Built from another ref than this checkout's, the image carries a
# package its release does not declare, or fails enabling a unit whose package
# was never installed, hours into the build. So the build is refused, before
# anything is fetched, unless HEAD is the commit the image will clone and the
# files copied from here hold nothing uncommitted. The image then clones that
# commit by its hash, not by the ref's name, which the chroot would read again
# hours from now, after the branch may have moved.
refuse_tree() {
    printf 'ERROR: %s\n' "$1" >&2
    echo "Build from the ref you want: check out ${CLONE_REF} (as ${CLONE_REPO} holds it) and run this script again." >&2
    exit 1
}
CHECKOUT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CLONE_REPO="$(eval "${CHECKOUT_ENV}"; printf '%s' "${MILO_REPO}")"
CLONE_REF="$(eval "${CHECKOUT_ENV}"; printf '%s' "${MILO_BRANCH}")"
HEAD_COMMIT="$(git -C "${CHECKOUT}" rev-parse --verify HEAD)" \
    || refuse_tree "${CHECKOUT} is not a git checkout with a commit"
if [[ "${CLONE_REF}" =~ ^[0-9a-f]{7,40}$ && "${HEAD_COMMIT}" == "${CLONE_REF}"* ]]; then
    # A commit named by its hash: the clone finds it only if one of the
    # origin's branches or tags holds it. Asked of the refs themselves, fetched
    # under a namespace of their own and dropped after — a fetch of the hash
    # alone answers from this checkout, which always has it.
    CHECK_REFS="refs/milo-build-check"
    git -C "${CHECKOUT}" fetch --quiet --no-tags "${CLONE_REPO}" \
        "+refs/heads/*:${CHECK_REFS}/heads/*" "+refs/tags/*:${CHECK_REFS}/tags/*" \
        || refuse_tree "cannot read the refs of ${CLONE_REPO}"
    HOLDER="$(git -C "${CHECKOUT}" for-each-ref --count=1 --contains HEAD "${CHECK_REFS}")"
    git -C "${CHECKOUT}" for-each-ref --format='delete %(refname)' "${CHECK_REFS}" \
        | git -C "${CHECKOUT}" update-ref --stdin
    [[ -n "${HOLDER}" ]] || refuse_tree "${CLONE_REPO} holds no branch or tag containing ${HEAD_COMMIT}"
    CLONE_COMMIT="${HEAD_COMMIT}"
else
    # What the stage's `git checkout --force <ref>` lands on in a fresh clone:
    # the tag (peeled to its commit — ls-remote lists the peeled line only for a
    # pattern naming it), else the branch as the remote holds it — not this
    # checkout's, which may carry commits nobody pushed.
    REMOTE_REFS="$(git -C "${CHECKOUT}" ls-remote "${CLONE_REPO}" "${CLONE_REF}" "${CLONE_REF}^{}")" \
        || refuse_tree "cannot read the refs of ${CLONE_REPO}"
    CLONE_COMMIT="$(awk -v ref="${CLONE_REF}" '
        $2 == "refs/tags/" ref "^{}" { peeled = $1 }
        $2 == "refs/tags/" ref       { tag = $1 }
        $2 == "refs/heads/" ref      { head = $1 }
        END { print (peeled != "" ? peeled : (tag != "" ? tag : head)) }' <<< "${REMOTE_REFS}")"
    [[ -n "${CLONE_COMMIT}" ]] || refuse_tree "${CLONE_REPO} has no tag or branch named ${CLONE_REF}"
fi
[[ "${HEAD_COMMIT}" == "${CLONE_COMMIT}" ]] \
    || refuse_tree "this checkout is at ${HEAD_COMMIT}, the image would clone ${CLONE_REF} at ${CLONE_COMMIT}"
# THIRD_PARTY_NOTICES.md lies outside frontend/, and the frontend build copies
# it into dist/ (frontend/vite-plugins/).
DIRTY="$(git -C "${CHECKOUT}" status --porcelain --untracked-files=all -- \
    dependencies.env system-manifest.env pi-gen/config pi-gen/upstream.env pi-gen/stage-milo frontend \
    THIRD_PARTY_NOTICES.md)"
[[ -z "${DIRTY}" ]] \
    || refuse_tree "uncommitted changes in what this script copies into the image:"$'\n'"${DIRTY}"
CHECKOUT_ENV="$("${SCRIPT_DIR}/checkout-env.sh" "${CLONE_COMMIT}" "${CLONE_REPO}")"
echo "The image clones ${CLONE_REF} as this commit:"
echo "${CHECKOUT_ENV}"
echo ""

# The pinned commit and nothing else, into a checkout made here or one an
# earlier build left: --force puts back what that build changed in pi-gen's own
# files, and its untracked work/ and deploy/ stay.
echo "Fetching pi-gen ${PIGEN_COMMIT}..."
mkdir -p "${PIGEN_DIR}"
cd "${PIGEN_DIR}"
[[ -d .git ]] || git init -q
git fetch --depth 1 "${PIGEN_REPO}" "${PIGEN_COMMIT}"
git checkout -q --force --detach "${PIGEN_COMMIT}"

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

# The system manifest travels the same way, for the same reason: 00-install-deps
# installs its packages before the checkout that carries it is cloned.
cp "${SCRIPT_DIR}/../system-manifest.env" "${PIGEN_DIR}/stage-milo/system-manifest.env"

# The frontend travels with it, for the same reason and under the same fixed
# name the CI workflow uses: the stage installs the artefact it is handed and
# never builds one, so an image and a unit updated to the same release run the
# same bytes. `npm ci` rather than `npm install` — the lockfile is the tree this
# commit was tested with, a fresh resolution of the ranges is whatever is
# current today.
echo "Building the frontend artefact..."
(cd "${SCRIPT_DIR}/../frontend" && npm ci && npm run build)
tar -czf "${PIGEN_DIR}/stage-milo/frontend-dist.tar.gz" \
    -C "${SCRIPT_DIR}/../frontend" dist

# The repository and the ref travel beside the stage too, under the name the CI
# workflow writes. Not in pi-gen's config: pi-gen sources it without exporting
# it, so a line appended there reaches the stage script, a child process, only
# when the variable was in the environment already — never in Docker.
printf '%s\n' "${CHECKOUT_ENV}" > "${PIGEN_DIR}/stage-milo/milo-checkout.env"

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
