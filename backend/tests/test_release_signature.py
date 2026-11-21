"""The check a unit makes of a Milō release before it installs it.

Driven with a key of the tests' own and the real ssh-keygen on both sides
(tests/release_signing.py): a release is installed as root, so every way a
signature can fail to vouch for one is a refusal, and a refusal is the one
outcome a stand-in for ssh-keygen would decide by itself. If one of these
fails, a unit installs a release nobody signed — or refuses every one.
"""
import pytest

from backend.core.updates import release_signature
from backend.core.updates.release_signature import (
    UnverifiableRelease,
    parse_manifest,
    verified_manifest,
)
from backend.tests.release_signing import make_key, manifest_for

COMMIT = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "ab" * 32


@pytest.fixture
def key(tmp_path):
    return make_key(tmp_path / "keys")


@pytest.mark.asyncio
async def test_a_manifest_signed_by_a_trusted_key_is_what_the_install_reads(key):
    manifest = manifest_for("v1.2.0", COMMIT, DIGEST)

    parsed = await verified_manifest(manifest, key.sign(manifest), key.allowed_signers, "v1.2.0")

    assert (parsed.tag, parsed.commit, parsed.frontend_sha256) == ("v1.2.0", COMMIT, DIGEST)


@pytest.mark.asyncio
async def test_a_signature_by_a_key_the_unit_does_not_list_is_refused(key, tmp_path):
    """The case signing exists for: an account that can publish a release,
    and so attach a manifest and a signature of its own making."""
    other = make_key(tmp_path / "other", name="intruder")
    manifest = manifest_for("v1.2.0", COMMIT, DIGEST)

    with pytest.raises(UnverifiableRelease, match="does not verify"):
        await verified_manifest(manifest, other.sign(manifest), key.allowed_signers, "v1.2.0")


@pytest.mark.asyncio
async def test_a_manifest_changed_after_it_was_signed_is_refused(key):
    """One digest swapped for another — the frontend an attacker built."""
    manifest = manifest_for("v1.2.0", COMMIT, DIGEST)
    signature = key.sign(manifest)

    with pytest.raises(UnverifiableRelease, match="does not verify"):
        await verified_manifest(
            manifest.replace(DIGEST.encode(), b"cd" * 32), signature, key.allowed_signers, "v1.2.0",
        )


@pytest.mark.asyncio
async def test_a_signature_made_for_another_purpose_is_refused(key):
    """The same key signs git commits or files: a signature it made for
    another namespace vouches for nothing about a release."""
    manifest = manifest_for("v1.2.0", COMMIT, DIGEST)

    with pytest.raises(UnverifiableRelease, match="does not verify"):
        await verified_manifest(
            manifest, key.sign(manifest, namespace="git"), key.allowed_signers, "v1.2.0",
        )


@pytest.mark.asyncio
async def test_a_genuine_manifest_of_another_release_is_refused(key):
    """A replay: the owner's real signature of v1.1.0, attached to a release
    published as v1.2.0."""
    manifest = manifest_for("v1.1.0", COMMIT, DIGEST)

    with pytest.raises(UnverifiableRelease, match="describes v1.1.0, not v1.2.0"):
        await verified_manifest(manifest, key.sign(manifest), key.allowed_signers, "v1.2.0")


@pytest.mark.asyncio
async def test_no_key_to_verify_with_refuses_every_release(key, tmp_path):
    """A tree whose key file lists no key, or a unit missing the file: the
    answer is never "nothing could check it, so install"."""
    manifest = manifest_for("v1.2.0", COMMIT, DIGEST)
    signature = key.sign(manifest)
    comments_only = tmp_path / "empty.allowed"
    comments_only.write_text("# no key yet\n")

    with pytest.raises(UnverifiableRelease, match="does not verify"):
        await verified_manifest(manifest, signature, comments_only, "v1.2.0")
    with pytest.raises(UnverifiableRelease, match="no release signing key"):
        await verified_manifest(manifest, signature, tmp_path / "absent.allowed", "v1.2.0")


@pytest.mark.asyncio
async def test_a_verifier_that_does_not_answer_is_no_refusal(key, monkeypatch):
    """ssh-keygen stuck past its budget said nothing about the release. The
    install fails on it all the same; the offer must read it as a check that
    failed, because a refusal is cached as an answer — *Refused: signature*
    for an hour, with no Retry — of a release that may be fine."""
    monkeypatch.setattr(release_signature, "VERIFY_TIMEOUT_S", 0)
    manifest = manifest_for("v1.2.0", COMMIT, DIGEST)

    with pytest.raises(TimeoutError) as raised:
        await verified_manifest(manifest, key.sign(manifest), key.allowed_signers, "v1.2.0")
    assert not isinstance(raised.value, UnverifiableRelease)


@pytest.mark.asyncio
async def test_a_signature_that_is_not_one_is_refused(key):
    """The download a 404 page or a truncated file would give."""
    manifest = manifest_for("v1.2.0", COMMIT, DIGEST)

    with pytest.raises(UnverifiableRelease):
        await verified_manifest(manifest, b"<html>Not Found</html>", key.allowed_signers, "v1.2.0")


@pytest.mark.parametrize("text, reason", [
    (b"commit=%s\nfrontend_sha256=%s\n" % (COMMIT.encode(), DIGEST.encode()), "names no tag"),
    (b"tag=v1.2.0\nfrontend_sha256=%s\n" % DIGEST.encode(), "names no commit"),
    (b"tag=v1.2.0\ncommit=%s\n" % COMMIT.encode(), "names no frontend_sha256"),
    (b"tag=v1.2.0\ntag=v1.3.0\ncommit=%s\nfrontend_sha256=%s\n" % (COMMIT.encode(), DIGEST.encode()),
     "names tag twice"),
    (b"tag=v1.2.0\ncommit=0123abc\nfrontend_sha256=%s\n" % DIGEST.encode(), "not a full hash"),
    (b"tag=v1.2.0\ncommit=%s\nfrontend_sha256=deadbeef\n" % COMMIT.encode(), "not a sha256"),
    (b"tag v1.2.0\n", "not key=value"),
    (b"\xff\xfe", "not UTF-8"),
], ids=["no-tag", "no-commit", "no-digest", "repeated", "short-commit", "short-digest",
        "no-equals", "binary"])
def test_what_is_not_a_manifest_is_refused(text, reason):
    """Each field the install acts on, missing or malformed. A repeated key is
    not resolved: which of two values a signer meant is not for a unit to pick."""
    with pytest.raises(UnverifiableRelease, match=reason):
        parse_manifest(text)


def test_a_key_a_later_release_adds_is_ignored():
    """The manifest is append-only: release N reads N+1's, and a key N does not
    know is one N+1's CI added, never a reason to refuse it."""
    parsed = parse_manifest(manifest_for("v1.2.0", COMMIT, DIGEST) + b"sbom_sha256=" + b"f" * 64 + b"\n")

    assert parsed.commit == COMMIT
