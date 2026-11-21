"""
Tests for the satellite's one way to replace a file on its card.

What breaks when these fail: a power cut during a write leaves a truncated file
that the next start reads — an environment file that sends the speaker looking
for its server anew, a hardware.json that reads as no card, a config.yml that
keeps the room silent — or a file the operator can no longer read over SSH.

The power cut is simulated by the rename failing: the new bytes must reach the
live path only through it.
"""
import os
import stat
from unittest.mock import patch

import pytest

from atomic_file import write_atomic


def test_a_write_that_dies_leaves_the_old_file_whole_and_nothing_beside_it(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    path = state / "env"
    path.write_text("MILO_PRINCIPAL_IP=milo.local\n")

    with patch("atomic_file.os.replace", side_effect=OSError("power cut")):
        with pytest.raises(OSError):
            write_atomic(path, "MILO_PRINCIPAL_IP=192.168.1.10\n")

    assert path.read_text() == "MILO_PRINCIPAL_IP=milo.local\n"
    assert os.listdir(state) == ["env"], "a temporary file was left beside the live one"


def test_the_file_gets_the_mode_asked_whatever_the_umask(tmp_path):
    """The operator reads these over SSH as `milo`, not as the app's user:
    mkstemp's 0600 would lock them out of the env file and the server's
    addresses. A secret asks for 0600 and gets it."""
    previous = os.umask(0o077)
    try:
        write_atomic(tmp_path / "server-addresses", "192.168.1.55\n")
        write_atomic(tmp_path / "server-secret", b"5e\n", mode=0o600)
    finally:
        os.umask(previous)

    assert stat.S_IMODE((tmp_path / "server-addresses").stat().st_mode) == 0o644
    assert stat.S_IMODE((tmp_path / "server-secret").stat().st_mode) == 0o600
