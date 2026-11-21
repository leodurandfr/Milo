#!/usr/bin/env bash
# Sign a drafted Milō release, then publish it.
#
#   tools/release/sign-release.sh [--rotate] v1.2.0 [signing key]
#
# Run on the Mac where the release key lives, from the checkout the tag was
# pushed from, once .github/workflows/build-image.yml has left the tag's release
# as a draft. Needs ssh-keygen and gh, nothing else; the key defaults to
# $MILO_RELEASE_KEY, else ~/.ssh/milo-release.
#
# What it does, in order, stopping at the first thing that is not as it should
# be — before anything is uploaded:
#   1. downloads the draft's manifest and frontend;
#   2. checks the manifest against this checkout: its tag, and the commit the
#      tag names HERE (not on GitHub); and checks that the draft's frontend is
#      the one its manifest names — both come from GitHub, so that is agreement
#      between the two, not where the frontend came from;
#   3. signs the manifest (ssh-keygen -Y sign, namespace milo-release);
#   4. verifies the signature with the keys of the release the units run now
#      (releases/latest) — the check that decides whether any of them installs
#      it — and with the keys of the release being signed, which its units will
#      verify the next release with: a key that release no longer lists is
#      refused unless --rotate says the release hands the fleet over to the
#      keys it lists (docs/development.md § Signing a release);
#   5. uploads the signature, then publishes the draft. A published release is
#      immutable: the signature is attached first, never after.
#
# The names and the namespace are the ones backend/core/updates/
# release_signature.py and catalog.py verify with, held equal by
# backend/tests/architecture/test_release_channel.py.
set -euo pipefail

NAMESPACE="milo-release"
IDENTITY="milo-release"
KEYS="rootfs/etc/milo/release-signing.pub"

die() { echo "sign-release: $*" >&2; exit 1; }

ROTATE=
if [ "${1:-}" = "--rotate" ]; then ROTATE=1; shift; fi
[ $# -ge 1 ] && [ $# -le 2 ] || die "usage: $0 [--rotate] <tag> [signing key]"
TAG=$1
KEY=${2:-${MILO_RELEASE_KEY:-$HOME/.ssh/milo-release}}
MANIFEST="milo-release-${TAG}.manifest"
FRONTEND="milo-frontend-${TAG}.tar.gz"

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1
}

cd "$(git rev-parse --show-toplevel)"
commit=$(git rev-parse --verify --quiet "refs/tags/${TAG}^{commit}") \
    || die "no tag ${TAG} in this checkout: sign from the one it was pushed from"
[ -f "$KEY" ] || die "no signing key at ${KEY}"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

gh release download "$TAG" --pattern "$MANIFEST" --pattern "$FRONTEND" --dir "$work" \
    || die "the release ${TAG} carries no ${MANIFEST} and ${FRONTEND}: is its build finished?"

field() { sed -n "s/^$1=//p" "$work/$MANIFEST"; }
[ "$(field tag)" = "$TAG" ] || die "the manifest describes $(field tag), not ${TAG}"
[ "$(field commit)" = "$commit" ] \
    || die "the draft was built from $(field commit), and ${TAG} names ${commit} here"
[ "$(field frontend_sha256)" = "$(sha256 "$work/$FRONTEND")" ] \
    || die "the draft's ${FRONTEND} is not the one its manifest names"

# Which release the units run now. Asked before signing, so that a GitHub that
# does not answer stops here rather than after the key was used.
if latest=$(gh release view --json tagName --jq .tagName 2>"$work/view.err"); then
    :
elif grep -q "release not found" "$work/view.err"; then
    latest=""    # nothing published yet: no unit runs a release to check against
else
    die "GitHub did not say which release the units run ($(tr '\n' ' ' < "$work/view.err")): nothing was signed"
fi
[ "$latest" != "$TAG" ] || die "${TAG} is already the published release: a published release takes no signature"

echo "Signing the manifest of ${TAG}:"
sed 's/^/  /' "$work/$MANIFEST"
ssh-keygen -q -Y sign -f "$KEY" -n "$NAMESPACE" "$work/$MANIFEST"

# $1: a tag of this checkout. Writes the keys it lists to $work/allowed; fails
# when it lists none (a release from before signing, whose units verify nothing).
keys_of() {
    git rev-parse --verify --quiet "refs/tags/$1^{commit}" >/dev/null \
        || die "no tag $1 in this checkout (git fetch --tags): its keys are what the units on it verify ${TAG} with"
    git show "$1:$KEYS" > "$work/allowed" 2>/dev/null || return 1
    grep -q -v -e '^[[:space:]]*#' -e '^[[:space:]]*$' "$work/allowed"
}
verifies() {
    ssh-keygen -Y verify -f "$work/allowed" -I "$IDENTITY" -n "$NAMESPACE" \
        -s "$work/$MANIFEST.sig" < "$work/$MANIFEST" >/dev/null 2>&1
}

# The gate: the units on releases/latest verify TAG with the keys it lists.
if [ -z "$latest" ]; then
    [ -z "$ROTATE" ] || die "--rotate hands the units over from the keys of releases/latest, and nothing is published"
elif ! keys_of "$latest"; then
    [ -z "$ROTATE" ] || die "--rotate: ${latest} lists no release keys, so there is no key to hand over from"
    echo "  ${latest} lists no release keys: the units on it verify nothing"
elif ! verifies; then
    die "the signature does not verify with the keys ${latest} lists (${KEYS}): the units on it would refuse ${TAG}"
fi

# The keys TAG's own units will verify every later release with.
keys_of "$TAG" || die "${TAG} lists no key in ${KEYS}: once installed, its units would refuse every release after it"
if verifies; then
    [ -z "$ROTATE" ] || die "--rotate, but ${TAG} still lists the key that signs it: nothing rotates, sign without --rotate"
elif [ -n "$ROTATE" ]; then
    echo "  ${TAG} does not list the key that signs it: once installed, its units take only the keys it lists"
else
    die "${TAG} does not list the key that signs it (${KEYS}): once installed, its units would refuse every later release this key signs. If ${TAG} rotates the key, sign with --rotate"
fi

gh release upload "$TAG" "$work/$MANIFEST.sig"
gh release edit "$TAG" --draft=false
echo "${TAG} is signed and published."
