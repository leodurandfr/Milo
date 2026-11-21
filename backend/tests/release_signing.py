"""A release signing key of the tests' own, and manifests signed with it.

Real ssh-keygen on both sides — the key, the signature and the verification —
because the property under test is that what the owner's `ssh-keygen -Y sign`
writes is what a unit's `ssh-keygen -Y verify` accepts: a stand-in for either
would agree with the other by construction. The allowed-signers line is written
the way rootfs/etc/milo/release-signing.pub writes its own (held there by
test_release_channel.py).
"""
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from backend.core.updates.release_signature import SIGNER_IDENTITY, SIGNING_NAMESPACE


@dataclass(frozen=True)
class SigningKey:
    """An ed25519 key pair, and an allowed-signers file listing its public half."""

    private: Path
    allowed_signers: Path

    def sign(self, manifest: bytes, *, namespace: str = SIGNING_NAMESPACE) -> bytes:
        """`ssh-keygen -Y sign`'s signature of `manifest`, as the owner makes it."""
        with tempfile.TemporaryDirectory(dir=self.private.parent) as scratch:
            message = Path(scratch) / "manifest"
            message.write_bytes(manifest)
            subprocess.run(
                ["ssh-keygen", "-q", "-Y", "sign", "-f", str(self.private), "-n", namespace, str(message)],
                check=True, capture_output=True, timeout=30,
            )
            return Path(f"{message}.sig").read_bytes()


def make_key(directory: Path, name: str = "release") -> SigningKey:
    """A fresh key in `directory`, and `<name>.allowed` trusting it."""
    directory.mkdir(parents=True, exist_ok=True)
    private = directory / name
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"{name}@tests", "-f", str(private)],
        check=True, capture_output=True, timeout=30,
    )
    public = Path(f"{private}.pub").read_text().strip()
    allowed = directory / f"{name}.allowed"
    allowed.write_text(allowed_signers_line(public) + "\n")
    return SigningKey(private, allowed)


def allowed_signers_line(public_key: str) -> str:
    return f'{SIGNER_IDENTITY} namespaces="{SIGNING_NAMESPACE}" {public_key}'


def manifest_for(tag: str, commit: str, frontend_sha256: str) -> bytes:
    """A manifest of the shape build-image.yml writes."""
    return (
        f"tag={tag}\n"
        f"commit={commit}\n"
        f"frontend=milo-frontend-{tag}.tar.gz\n"
        f"frontend_sha256={frontend_sha256}\n"
        "image=2026-11-21-milo.img.xz\n"
        f"image_sha256={'e' * 64}\n"
    ).encode()
