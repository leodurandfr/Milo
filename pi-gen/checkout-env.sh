#!/bin/bash
# checkout-env.sh — print what an image clones, for stage-milo to source.
#
# Usage: checkout-env.sh <ref> [<repository URL>]
#
# Prints `MILO_REPO=…` and `MILO_BRANCH=…`, shell-quoted, which both image
# builders write beside the stage as `milo-checkout.env`: the GitHub workflow
# with the repository it runs in, pi-gen/build.sh with MILO_REPO or, unset, the
# `origin` of the checkout this script sits in. 02-install-milo clones that
# repository and checks the ref out, so the image carries the repository that
# built it, not one written into the stage, and a fork builds from itself.
#
# The URL becomes the `origin` of every unit flashed from the image, which
# `git fetch origin --tags` reads at each Milō update, unauthenticated, as the
# chroot reads it at build time. So it must be an https URL with no user or
# token in it: a GitHub SSH remote (`git@github.com:owner/repo.git`,
# `ssh://git@github.com/owner/repo.git`) is given as the same repository over
# https, and anything else is refused, nothing printed.

set -euo pipefail

refuse() { echo "checkout-env.sh: $1" >&2; exit 2; }

(($# >= 1 && $# <= 2)) || refuse "usage: $0 <ref> [<repository URL>]"
REF="$1"
REPO="${2:-}"
[[ -n "$REF" && "$REF" != -* ]] || refuse "no ref to build"

if [[ -z "$REPO" ]]; then
    CHECKOUT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    REPO="$(git -C "$CHECKOUT" remote get-url origin 2>/dev/null)" \
        || refuse "$CHECKOUT has no origin; name the repository: MILO_REPO=https://github.com/<owner>/<repo>.git"
fi

case "$REPO" in
    git@github.com:*) REPO="https://github.com/${REPO#git@github.com:}" ;;
    ssh://git@github.com/*) REPO="https://github.com/${REPO#ssh://git@github.com/}" ;;
esac

# https://host/path, the host carrying no user, password or token.
[[ "$REPO" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?/[^[:space:]@]+$ ]] \
    || refuse "not an https URL without credentials: name the repository the image may clone, MILO_REPO=https://github.com/<owner>/<repo>.git"

printf 'MILO_REPO=%q\n' "$REPO"
printf 'MILO_BRANCH=%q\n' "$REF"
