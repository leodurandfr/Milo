"""
Pytest wrapper around scripts/test-alsa-routing.sh.

Skipped off-Pi (no /etc/asound.conf, no Loopback card, or aplay missing).
Runs the static checks only — live --with-live probes are gated by manual
flag in the shell script and not driven from pytest (they depend on which
sources are currently holding subdevices).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "test-alsa-routing.sh"


def _on_pi() -> bool:
    return (
        Path("/etc/asound.conf").exists()
        and Path("/proc/asound/Loopback").exists()
        and shutil.which("aplay") is not None
    )


@pytest.mark.skipif(not _on_pi(), reason="ALSA routing smoke test only runs on the Pi")
def test_alsa_routing_smoke():
    assert SCRIPT.exists(), f"smoke test script missing: {SCRIPT}"
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"ALSA routing smoke test failed (exit {result.returncode}).\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
