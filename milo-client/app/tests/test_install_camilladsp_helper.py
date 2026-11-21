"""`milo-client-install-camilladsp --release`, run for real under bash with tar stubbed.

`CamillaDSPUpdateService.update_camilladsp` (services/camilladsp_update.py) hands
this wrapper the release archive it downloaded and the digest the server sent
from its dependencies.env. The wrapper unpacks the binary as root, so an archive
that is not the declared file must never reach tar. Only the refusals can run
here: an install writes /usr/local/bin, which a test user cannot.
"""

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HELPER = (
    Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-client-install-camilladsp"
)


@pytest.fixture
def world():
    """A stub tar that records its argv, and an archive under /tmp, the only
    directory the wrapper takes one from."""
    root = Path(tempfile.mkdtemp(dir="/tmp"))
    stubs = root / "bin"
    stubs.mkdir()
    tar = stubs / "tar"
    tar.write_text('#!/bin/sh\necho "tar $*" >> "$STUB_LOG"\nexit 0\n')
    tar.chmod(0o755)
    archive = root / "camilladsp.tar.gz"
    archive.write_bytes(b"not the release archive")
    yield {"stubs": stubs, "log": root / "stub.log", "archive": archive}
    shutil.rmtree(root, ignore_errors=True)


def _run(world, *args):
    env = {"PATH": f"{world['stubs']}:/usr/bin:/bin", "STUB_LOG": str(world["log"])}
    result = subprocess.run(["bash", str(HELPER), *args], env=env, capture_output=True, text=True, timeout=30)
    log = world["log"].read_text() if world["log"].exists() else ""
    return result, log


@pytest.mark.parametrize("sha256", [
    pytest.param("0" * 64, id="another-file"),
    pytest.param("", id="empty"),
    pytest.param("not-a-digest", id="not-a-digest"),
])
def test_an_archive_that_is_not_the_declared_one_is_never_unpacked(world, sha256):
    """If this fails, a CamillaDSP GitHub served differently from the validated
    one is unpacked as root and put in the audio path of a room."""
    result, log = _run(world, "--release", str(world["archive"]), sha256)

    assert result.returncode != 0, result.stdout
    assert "tar " not in log, log


def test_the_digest_is_checked_on_a_copy_not_on_the_path_it_was_given(world):
    """The archive tar would read is the wrapper's own checked copy: the path the
    app named sits in a directory its own user can rewrite after any check."""
    sha256 = hashlib.sha256(world["archive"].read_bytes()).hexdigest()
    _, log = _run(world, "--release", str(world["archive"]), sha256)

    unpack = next(line for line in log.splitlines() if line.startswith("tar "))
    assert str(world["archive"]) not in unpack, unpack
    assert "/camilladsp.tar.gz" in unpack, unpack
