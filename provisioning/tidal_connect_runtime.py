#!/usr/bin/env python3
"""Materialise the Tidal Connect runtime tree under /opt/milo/tidal-connect.

Milō runs no containers. The Tidal Connect daemon is nonetheless distributed
only as a container image, because it is a 32-bit armhf binary from Tidal's
Connect Device SDK linked against a 2017 userland — OpenSSL 1.0.0, FFmpeg 3.x
(libav*57), FLAC 8 — whose transitive closure no current archive still carries
in one piece. So this pulls the image's layers straight from the registry over
plain HTTP and unpacks them into a directory. No daemon, no runtime, no
containerd: `milo-tidal-connect` then runs the binary through that tree's own
dynamic loader, and uninstalling is `rm -rf` on one path.

Three corrections are applied on top of the unpacked image:

  1. **Page alignment.** The Pi 5 kernel (rpt-rpi-2712) maps 16K pages, and an
     ELF whose LOAD segments are 4K-aligned cannot be mapped at all. Almost the
     whole image is 64K-aligned and fine; `libsystemd.so.0` and `libudev.so.1`
     are not, and libdbus — which the daemon needs for Avahi/mDNS — pulls them
     in. Docker would not have helped: a container shares the host kernel, so
     it fails identically.

     Their replacements must come from **bullseye**: Debian switched armhf from
     64K to 4K segment alignment between bullseye and bookworm, so a *newer*
     base is the one that breaks. Bullseye is the newest usable source, and its
     glibc 2.31 still runs every ancient library in the image unchanged.

  2. **Shadowed copies.** The bullseye packages install into
     /usr/lib/arm-linux-gnueabihf while the image's 4K originals sit in
     /lib/arm-linux-gnueabihf, which the loader searches first. The stale
     copies are removed and the symlinks repointed, or the fix is inert.

  3. **The rate converter.** /etc/asound.conf asks every `type plug` for
     speexrate_medium, and alsa-lib dlopens a rate converter from a directory
     baked in at build time — /usr/lib/arm-linux-gnueabihf/alsa-lib, which an
     arm64 host does not have, with no environment override in the tree's
     1.1.3. Tidal streams 44.1 kHz into a 48 kHz pipeline, so with no converter
     PortAudio cannot configure the stream at all: the phone shows a track
     playing and nothing comes out. The armhf module is installed into the tree
     and milo-tidal.service bind-mounts it onto that path for itself alone.

The result is verified before the script exits: the loader is asked to resolve
the daemon's full dependency graph, and every resolved object is re-checked for
alignment. A runtime that would fail on the appliance fails here instead.

Every byte it unpacks is checked against a digest named here first: the
manifest against IMAGE_DIGEST, each layer against the digest the manifest
names, each Debian package against BULLSEYE_DEBS. It runs as root, so what it
installs is what this file pins, whatever a registry or a mirror serves.

The image does not carry the result. The SDK is Tidal's, the device
certificate iFi's, and neither is Milō's to redistribute in a release, so a
unit builds its own tree the first time Tidal is selected (or enabled in the
Dock): `milo-deploy-update install-tidal-runtime` runs this as root into a
staging directory and renames the verified tree into place. There is no update
path and no catalog entry — the binary is frozen, and dependencies.env says
why it has no line there either.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

# Pinned by digest, not by tag: the payload is a frozen 2020 binary (the
# upstream repo's bin/ has exactly one commit, 2020-10-29) and nothing about
# this image should move under us.
IMAGE_REPO = "edgecrush3r/tidal-connect"
IMAGE_DIGEST = "sha256:4c4cf9508d947306f3ce2d7c8766392197fa19f046a5a88e79428b8974c2dbcc"

REGISTRY = "https://registry-1.docker.io/v2"
AUTH = "https://auth.docker.io/token?service=registry.docker.io&scope=repository:{repo}:pull"
MANIFEST_ACCEPT = ", ".join([
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
])

# Bullseye left Debian LTS on 2026-08-31 and leaves deb.debian.org with it, so
# its packages are read from snapshot.debian.org, at a moment after the last
# change to bullseye main (11.11, Release dated 2025-08-09) — and each file is
# pinned by its pool path and its sha256, so no index is read at all. The
# digests are the ones bullseye's own Packages index declares for them.
DEBIAN_SNAPSHOT = "https://snapshot.debian.org/archive/debian/20260801T000000Z"
DEBIAN_SUITE = "bullseye"

BULLSEYE_DEBS = {
    "libc6": (
        "pool/main/g/glibc/libc6_2.31-13+deb11u11_armhf.deb",
        "2b8a57429fd1999ae15c144a09a37a2c37a02ec1c5a5e07eded65455934e3b08",
    ),
    "libgcc-s1": (
        "pool/main/g/gcc-10/libgcc-s1_10.2.1-6_armhf.deb",
        "4cfff89c7f492090c5988298f9f9419d3464d294c43883800a2ed55676581d26",
    ),
    "libstdc++6": (
        "pool/main/g/gcc-10/libstdc++6_10.2.1-6_armhf.deb",
        "879258df2ac36d9733df014c5a5443944e1a5c4ba9a7eebe5dd28547b4ebb541",
    ),
    "libsystemd0": (
        "pool/main/s/systemd/libsystemd0_247.3-7+deb11u5_armhf.deb",
        "894c380fbc1d2e96d78b6a6c154a224d72b78671db9b376b3fc2ae620ae713ad",
    ),
    "libudev1": (
        "pool/main/s/systemd/libudev1_247.3-7+deb11u5_armhf.deb",
        "f96851738462d2a1fcde7758e1281f45d1b872ff001529545f2e5c40d68e54fc",
    ),
    "libdbus-1-3": (
        "pool/main/d/dbus/libdbus-1-3_1.12.28-0+deb11u1_armhf.deb",
        "e05ae5e2396de6d4ffb51a6f84fa0b555e8c833da44b5d45af2d397c9d27e71f",
    ),
    "libzstd1": (
        "pool/main/libz/libzstd/libzstd1_1.4.8+dfsg-2.1_armhf.deb",
        "bf9b391dd40d21a237ddcf2720a1f33c2c5eff4bec820a2001aea73e9c7df650",
    ),
    "libasound2-plugins": (
        "pool/main/a/alsa-plugins/libasound2-plugins_1.2.2-2_armhf.deb",
        "925111e32183851e137e7a08ef531a0ae449c422a1a9dffe38b76985ed8ad380",
    ),
}

# Pulled forward from bullseye. libc6 comes along because the replacement
# libsystemd needs a newer glibc than the image's 2.24; glibc is backward
# compatible, so every 2017 library in the tree keeps working. libzstd1 is a
# bullseye libsystemd dependency the 2017 image never carried.
OVERLAY_PACKAGES = [
    "libc6", "libgcc-s1", "libstdc++6",
    "libsystemd0", "libudev1", "libdbus-1-3", "libzstd1",
]

# The image's own 4K-aligned copies, which shadow the bullseye ones.
SHADOWED = ["libsystemd.so.0", "libudev.so.1"]

# The rate converter /etc/asound.conf names. Only the speexrate modules are
# taken: they carry their own resampler (nothing but libasound and libc in
# their NEEDED), while the package's pcm modules would want a libpulse and a
# libjack the tree has no reason to carry.
PLUGIN_PACKAGE = "libasound2-plugins"
PLUGIN_DIR = "usr/lib/arm-linux-gnueabihf/alsa-lib"
RATE_MODULES = "libasound_module_rate_speexrate*.so"

MIN_PAGE_ALIGN = 0x4000  # 16K — anything smaller cannot be mapped by this kernel

RELATIVE_LIB_DIRS = [
    "lib/arm-linux-gnueabihf",
    "usr/lib/arm-linux-gnueabihf",
    "lib",
    "usr/lib",
]

APP_SUBDIR = "app/ifi-tidal-release"


def log(message):
    print(f"  {message}", flush=True)


# === Downloads, each checked against its digest ===

def _check_digest(data, digest, what):
    """`data` (bytes) is what `digest` ("sha256:<hex>") names, or the run ends."""
    actual = "sha256:" + hashlib.sha256(data).hexdigest()
    if actual != digest:
        raise SystemExit(f"FATAL: {what}: declared {digest}, got {actual}")


def _fetch(request, path, sha256, what, timeout):
    """Stream `request` into `path`, hashing as it arrives; a file whose digest
    is not `sha256` (hex) is removed and the run ends.

    Read through urlopen rather than urlretrieve for the timeout: a server that
    accepts the connection and then stalls mid-body would otherwise hang the
    install with no output and no way to tell it from a slow download.
    """
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, open(path, "wb") as f:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                digest.update(chunk)
                f.write(chunk)
    except BaseException:
        if os.path.exists(path):
            os.remove(path)
        raise
    if digest.hexdigest() != sha256:
        os.remove(path)
        raise SystemExit(f"FATAL: {what}: declared {sha256}, got {digest.hexdigest()}")


# === Registry ===

def _token(repo):
    return json.load(urllib.request.urlopen(AUTH.format(repo=repo), timeout=60))["token"]


def _manifest(repo, digest, token):
    """The manifest `digest` names, refused unless its bytes hash to it."""
    request = urllib.request.Request(
        f"{REGISTRY}/{repo}/manifests/{digest}",
        headers={"Authorization": f"Bearer {token}", "Accept": MANIFEST_ACCEPT},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    _check_digest(data, digest, f"manifest {digest[:19]}")
    return json.loads(data)


def unpack_image(root):
    """Download and unpack the pinned image's layers into `root`."""
    token = _token(IMAGE_REPO)
    manifest = _manifest(IMAGE_REPO, IMAGE_DIGEST, token)

    # A multi-arch index would need the 32-bit ARM entry; the pinned digest is a
    # single manifest, so this only guards against re-pinning to an index.
    if "manifests" in manifest:
        entry = next(
            m for m in manifest["manifests"]
            if m.get("platform", {}).get("architecture") == "arm"
        )
        manifest = _manifest(IMAGE_REPO, entry["digest"], token)

    layers = manifest["layers"]
    log(f"{len(layers)} layers, {sum(l['size'] for l in layers) / 1e6:.0f} MB")

    os.makedirs(root, exist_ok=True)
    blob = os.path.join(root, ".layer.tar.gz")

    for index, layer in enumerate(layers, 1):
        algorithm, _, sha256 = layer["digest"].partition(":")
        if algorithm != "sha256":
            raise SystemExit(f"FATAL: layer {index} is named by {algorithm}, not sha256")
        request = urllib.request.Request(
            f"{REGISTRY}/{IMAGE_REPO}/blobs/{layer['digest']}",
            headers={"Authorization": f"Bearer {token}"},
        )
        _fetch(request, blob, sha256, f"layer {index}", timeout=600)

        # Only the libraries and the app payload matter: the tree is never
        # booted, its binary runs as milo through the tree's own loader. So no
        # device node is made and no owner or setuid bit is kept, although
        # this runs as root — every file is root's, at root's umask.
        subprocess.run(
            ["tar", "xzf", blob, "-C", root,
             "--no-same-owner", "--no-same-permissions",
             "--exclude=dev/*", "--exclude=proc/*", "--exclude=sys/*"],
            stderr=subprocess.DEVNULL, check=False,
        )
        log(f"layer {index}/{len(layers)}")

    os.remove(blob)


# === Debian overlay ===

def _download_deb(package, into):
    """Fetch one pinned armhf .deb into `into`, checked, and return its path."""
    if package not in BULLSEYE_DEBS:
        raise SystemExit(f"FATAL: {package} has no pinned {DEBIAN_SUITE}/armhf file")
    pool_path, sha256 = BULLSEYE_DEBS[package]
    deb = os.path.join(into, f"{package}.deb")
    _fetch(f"{DEBIAN_SNAPSHOT}/{pool_path}", deb, sha256, os.path.basename(pool_path), timeout=300)
    return deb


def overlay_bullseye(root):
    """Install the 64K-aligned replacements over the unpacked image."""
    tmp = os.path.join(root, ".debs")
    os.makedirs(tmp, exist_ok=True)

    for package in OVERLAY_PACKAGES:
        deb = _download_deb(package, tmp)
        subprocess.run(["dpkg-deb", "-x", deb, root], check=True)
        log(f"overlaid {package}")

    shutil.rmtree(tmp)


def install_rate_plugin(root):
    """Install the armhf speexrate modules the daemon resamples through.

    Unpacked aside and copied module by module rather than straight over the
    tree: the same package ships pcm modules for a PulseAudio and a JACK that
    are not there, and a module that fails to dlopen reads in the log exactly
    like the one that matters.
    """
    tmp = os.path.join(root, ".plugins")
    os.makedirs(tmp, exist_ok=True)
    subprocess.run(
        ["dpkg-deb", "-x", _download_deb(PLUGIN_PACKAGE, tmp), tmp], check=True
    )

    modules = glob.glob(os.path.join(tmp, PLUGIN_DIR, RATE_MODULES))
    if not modules:
        raise SystemExit(f"FATAL: {PLUGIN_PACKAGE} carries no {RATE_MODULES}")

    destination = os.path.join(root, PLUGIN_DIR)
    os.makedirs(destination, exist_ok=True)

    for module in modules:
        # These are dlopen'd long after start, so `verify` never resolves them:
        # an unmappable module would surface as silence, not as a failed start.
        align = _segment_alignment(module)
        if align is None or align < MIN_PAGE_ALIGN:
            raise SystemExit(
                f"FATAL: {os.path.basename(module)} is {align}-aligned, "
                f"below this kernel's page size"
            )
        shutil.copy2(module, destination)

    shutil.rmtree(tmp)
    log(f"installed {len(modules)} rate modules")


def drop_shadowed_copies(root):
    """Remove the image's 4K originals so the bullseye builds are what resolves."""
    old_dir = os.path.join(root, "lib/arm-linux-gnueabihf")
    new_dir = os.path.join(root, "usr/lib/arm-linux-gnueabihf")

    for soname in SHADOWED:
        for entry in os.listdir(old_dir):
            if entry.startswith(soname):
                os.remove(os.path.join(old_dir, entry))

        # Point the searched-first directory at the replacement.
        target = next(
            (e for e in sorted(os.listdir(new_dir))
             if e.startswith(soname) and not os.path.islink(os.path.join(new_dir, e))),
            None,
        )
        if not target:
            raise SystemExit(f"FATAL: no {DEBIAN_SUITE} build of {soname} was installed")
        os.symlink(f"../../usr/lib/arm-linux-gnueabihf/{target}",
                   os.path.join(old_dir, soname))
        log(f"repointed {soname} -> {target}")


# === Verification ===

def _segment_alignment(path):
    """Alignment of the first LOAD segment, or None if unreadable as an ELF."""
    output = subprocess.run(
        ["readelf", "-lW", path], capture_output=True, text=True
    ).stdout
    for line in output.split("\n"):
        if "LOAD" in line:
            try:
                return int(line.split()[-1], 16)
            except ValueError:
                return None
    return None


def verify(root):
    """Resolve the daemon's dependency graph and re-check every object's alignment.

    Catches both failure modes in one pass: a library the tree does not carry
    (the loader says "not found") and one the kernel cannot map (alignment
    below the page size). Either would surface on the appliance as a source
    that starts and immediately dies.
    """
    loader = os.path.join(root, "lib/ld-linux-armhf.so.3")
    binary = os.path.join(root, APP_SUBDIR, "bin/tidal_connect_application")
    lib_path = ":".join(os.path.join(root, d) for d in RELATIVE_LIB_DIRS)

    for required in (loader, binary):
        if not os.path.exists(required):
            raise SystemExit(f"FATAL: {required} missing from the unpacked runtime")

    result = subprocess.run(
        [loader, "--library-path", lib_path, "--list", binary],
        capture_output=True, text=True,
    )
    listing = result.stdout + result.stderr

    resolved = re.findall(r"=> (\S+)", listing)
    # Non-trivial output first: a loader that printed nothing must not read as
    # "no missing libraries".
    if len(resolved) < 10:
        raise SystemExit(
            f"FATAL: dependency listing looks empty ({len(resolved)} entries):\n{listing}"
        )

    if "not found" in listing or "alignment" in listing:
        broken = [line.strip() for line in listing.split("\n")
                  if "not found" in line or "alignment" in line]
        raise SystemExit("FATAL: unresolved dependencies:\n  " + "\n  ".join(broken))

    # An object readelf could not parse is a failed check, not a passed one:
    # skipping it would let a 4K-aligned library ship under a green
    # "all mappable", which is the single thing this function exists to stop.
    alignments = {path: _segment_alignment(path) for path in resolved}

    unreadable = [path for path, align in alignments.items() if align is None]
    if unreadable:
        raise SystemExit(
            "FATAL: alignment unreadable, cannot clear these:\n  " + "\n  ".join(unreadable)
        )

    misaligned = [path for path, align in alignments.items() if align < MIN_PAGE_ALIGN]
    if misaligned:
        raise SystemExit(
            "FATAL: libraries below this kernel's page size:\n  " + "\n  ".join(misaligned)
        )

    log(f"{len(resolved)} libraries resolved, all mappable")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # No default: the one caller builds into a staging directory and renames
    # it into place (milo-deploy-update install-tidal-runtime).
    parser.add_argument("--root", required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    if args.verify_only:
        verify(args.root)
        return

    if os.path.exists(args.root):
        shutil.rmtree(args.root)

    log(f"unpacking {IMAGE_REPO}@{IMAGE_DIGEST[:19]} into {args.root}")
    unpack_image(args.root)
    log(f"overlaying {DEBIAN_SUITE}/armhf libraries")
    overlay_bullseye(args.root)
    drop_shadowed_copies(args.root)
    install_rate_plugin(args.root)
    verify(args.root)


if __name__ == "__main__":
    sys.exit(main())
