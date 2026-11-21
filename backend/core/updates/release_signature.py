# backend/core/updates/release_signature.py
"""The signature a Milō release carries, and the check a unit makes before it installs one.

A unit installs as root whatever release the channel serves it, so the channel
alone cannot be what it trusts: an account that can publish a GitHub release
could otherwise publish one every unit takes. CI therefore publishes each
release as a *draft* carrying a manifest — the tag, the commit it names, the
sha256 of the frontend and of the image — and the owner signs that manifest on
his Mac with `ssh-keygen -Y sign`, off GitHub, before he publishes the draft
(`tools/release/sign-release.sh`). A unit verifies the signature with
`ssh-keygen -Y verify` against the keys of the release it runs
(`rootfs/etc/milo/release-signing.pub`, installed at `/etc/milo/`), and installs
nothing a valid signature does not cover: the offer refuses such a release
(`VersionService._apply_milo_offer`) and the install refuses it again, before
the checkout (`UpdateService._update_milo_app`).

What crosses a release boundary here is frozen and append-only, like the asset
names in test_release_channel.py: release N verifies release N+1 with N's code,
so the manifest's name, the signature's suffix, the namespace, the identity and
the keys N reads must be what N+1's CI and its signer still write. A new key in
the manifest is free (an unknown key is ignored); renaming or dropping one is a
release no earlier unit can install.
"""
import asyncio
import contextlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

# `ssh-keygen -Y sign -n` and `-Y verify -n`: what the signature is *for*. A
# signature made for another purpose (a git commit, a file) by the same key
# carries another namespace and verifies as nothing here.
SIGNING_NAMESPACE = "milo-release"
# The principal the allowed-signers file names each key under, `-Y verify -I`.
SIGNER_IDENTITY = "milo-release"
# What `ssh-keygen -Y sign` appends to the file it signs.
SIGNATURE_SUFFIX = ".sig"

# The manifest is a few hundred bytes; anything much larger is not one, and is
# not read into memory to find out.
MAX_MANIFEST_BYTES = 16 * 1024
VERIFY_TIMEOUT_S = 15

# The keys a unit reads. CI writes these and more (the frontend's file name, the
# image's name and digest, for whoever flashes a card): an unknown key is
# ignored, so adding one costs nothing.
KEY_TAG = "tag"
KEY_COMMIT = "commit"
KEY_FRONTEND_SHA256 = "frontend_sha256"
REQUIRED_KEYS = (KEY_TAG, KEY_COMMIT, KEY_FRONTEND_SHA256)

_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_KEY_RE = re.compile(r"[a-z0-9_]+")


class UnverifiableRelease(Exception):
    """The release cannot be verified: no signed manifest, a signature that does
    not verify, or a manifest that does not describe this release. The answer a
    unit gives it is a refusal, never a retry — unlike a download that failed,
    which raises anything else."""


@dataclass(frozen=True)
class ReleaseManifest:
    """What the owner's signature vouches for, as the install uses it."""

    tag: str
    commit: str
    frontend_sha256: str


def parse_manifest(data: bytes) -> ReleaseManifest:
    """The manifest's fields, from its exact bytes. Raises UnverifiableRelease
    on anything that is not a manifest: a refusal, since its signature was the
    only thing vouching for it.

    One `key=value` per line. A repeated key is refused rather than resolved:
    which of two values the signer meant is not a question to answer here.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise UnverifiableRelease("the release manifest is not UTF-8 text") from None
    fields: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        key, sep, value = line.partition("=")
        if not sep or not _KEY_RE.fullmatch(key):
            raise UnverifiableRelease(f"line {number} of the release manifest is not key=value: {line[:80]!r}")
        if key in fields:
            raise UnverifiableRelease(f"the release manifest names {key} twice")
        fields[key] = value
    missing = [key for key in REQUIRED_KEYS if not fields.get(key)]
    if missing:
        raise UnverifiableRelease(f"the release manifest names no {', '.join(missing)}")
    if not _COMMIT_RE.fullmatch(fields[KEY_COMMIT]):
        raise UnverifiableRelease(f"the release manifest's commit is not a full hash: {fields[KEY_COMMIT]!r}")
    if not _SHA256_RE.fullmatch(fields[KEY_FRONTEND_SHA256]):
        raise UnverifiableRelease("the release manifest's frontend digest is not a sha256")
    return ReleaseManifest(fields[KEY_TAG], fields[KEY_COMMIT], fields[KEY_FRONTEND_SHA256])


def _signature_file(signature: bytes) -> str:
    """`-Y verify` reads the signature from a file and the message from stdin.
    Blocking."""
    fd, path = tempfile.mkstemp(prefix="milo-release-", suffix=SIGNATURE_SUFFIX)
    with os.fdopen(fd, "wb") as f:
        f.write(signature)
    return path


async def verify_signature(manifest: bytes, signature: bytes, allowed_signers: Path) -> None:
    """Raise UnverifiableRelease unless `signature` is a signature of `manifest`'s
    exact bytes, made for SIGNING_NAMESPACE by a key `allowed_signers` names
    under SIGNER_IDENTITY.

    `allowed_signers` is ssh-keygen's own format, so the file a unit trusts is
    the file anyone can verify a release with by hand. A missing file, a file
    holding no key and a missing ssh-keygen all refuse: there is no state in
    which a release is installed because nothing could check it. An ssh-keygen
    that does not answer raises TimeoutError instead — it refuses the install
    all the same, but the offer reads it as a check to retry, not an answer.
    """
    if not await asyncio.to_thread(allowed_signers.is_file):
        raise UnverifiableRelease(f"no release signing key at {allowed_signers}")
    path = await asyncio.to_thread(_signature_file, signature)
    try:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ssh-keygen", "-Y", "verify",
                "-f", str(allowed_signers),
                "-I", SIGNER_IDENTITY,
                "-n", SIGNING_NAMESPACE,
                "-s", path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise UnverifiableRelease("ssh-keygen is not installed: no release can be verified") from None
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(manifest), timeout=VERIFY_TIMEOUT_S)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            # Not a refusal: a verifier that did not answer said nothing about
            # the release, and a refusal is cached as an answer no Retry asks again.
            raise TimeoutError(f"ssh-keygen did not answer within {VERIFY_TIMEOUT_S} s") from None
    finally:
        await asyncio.to_thread(_unlink, path)
    if proc.returncode != 0:
        said = [line for line in (stderr + stdout).decode(errors="replace").splitlines() if line.strip()]
        raise UnverifiableRelease(
            f"the signature does not verify ({said[-1].strip() if said else f'exit code {proc.returncode}'})"
        )


def _unlink(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)


async def verified_manifest(manifest: bytes, signature: bytes, allowed_signers: Path, tag: str) -> ReleaseManifest:
    """The manifest of release `tag`, once its signature verified.

    The signature first: nothing is read out of bytes no trusted key vouched
    for. Then the tag, because a valid signature of *another* release's
    manifest is a replay — a published release's genuine manifest attached to a
    different one — and vouches for nothing about this one.
    """
    await verify_signature(manifest, signature, allowed_signers)
    parsed = parse_manifest(manifest)
    if parsed.tag != tag:
        raise UnverifiableRelease(f"the signed manifest describes {parsed.tag}, not {tag}")
    return parsed
