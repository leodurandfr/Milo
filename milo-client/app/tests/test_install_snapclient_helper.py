"""`milo-client-install-snapclient`, run for real under bash with apt and dpkg stubbed.

`SnapclientService.update_snapclient` (services/snapclient.py) installs the .deb the
server's update sends through this wrapper, and believes its exit status. The
server's own `milo-deploy-update install-deb` finishes an interrupted dpkg run
and refuses removals; this one did neither, so one power cut during a snapclient
update left the satellite's dpkg "interrupted" and every later update refused,
on a unit with no shell anyone opens.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HELPER = (
    Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-client-install-snapclient"
)

# The name the satellite downloads (`snapclient_{version}-1_arm64_{codename}.deb`),
# which the wrapper's filename check accepts.
DEB_NAME = "snapclient_0.35.0-1_arm64_trixie.deb"

# Each stub appends its name and argv to $STUB_LOG, so a test can prove the
# wrapper ran the stubs and not the host's real apt.
STUBS = {
    "apt-get": (
        'case "$1" in\n'
        '  install) exit "${STUB_APT_INSTALL_RC:-0}" ;;\n'
        "  *) exit 0 ;;\n"
        "esac"
    ),
    # Fails, as on a dpkg that cannot finish what was interrupted: the wrapper
    # must leave the verdict to apt-get.
    "dpkg": "exit 1",
    "dpkg-deb": "echo snapclient",
}


@pytest.fixture
def world():
    """A stub directory, a log, and the .deb under /tmp.

    The .deb is not under `tmp_path`: the wrapper accepts /tmp and /var/tmp
    only, and pytest's base directory may sit elsewhere.
    """
    root = Path(tempfile.mkdtemp(dir="/tmp"))
    stubs = root / "bin"
    stubs.mkdir()
    for name, body in STUBS.items():
        stub = stubs / name
        stub.write_text(
            "#!/bin/sh\n"
            f'echo "{name} $*" >> "$STUB_LOG"\n'
            f"{body}\n"
        )
        stub.chmod(0o755)
    deb = root / DEB_NAME
    deb.write_bytes(b"!<arch>\n")
    yield {"stubs": stubs, "log": root / "stub.log", "deb": deb}
    shutil.rmtree(root, ignore_errors=True)


def _run(world, *, apt_install_rc=0):
    env = {
        "PATH": f"{world['stubs']}:/usr/bin:/bin",
        "STUB_LOG": str(world["log"]),
        "STUB_APT_INSTALL_RC": str(apt_install_rc),
    }
    result = subprocess.run(
        ["bash", str(HELPER), str(world["deb"])],
        env=env, capture_output=True, text=True, timeout=30,
    )
    log = world["log"].read_text().splitlines() if world["log"].exists() else []
    # Without this, a PATH that missed the stubs runs the host's real apt as a
    # non-root user, which fails, and the failure case below passes on it.
    assert any(line.startswith("apt-get install") for line in log), (
        f"the wrapper never reached the apt-get stub; stub log: {log!r}, stderr: {result.stderr!r}"
    )
    return result, log


def test_an_interrupted_dpkg_run_is_finished_before_apt(world):
    """`dpkg --configure -a` runs before apt, and its failure does not stop it.

    Without it, the install a power cut interrupted leaves dpkg "interrupted",
    apt-get refuses every install after it, and the satellite's snapclient
    update fails at every push from then on. Fatal instead, a configure that
    cannot finish anything (dpkg stubbed to fail here) would hide the reason
    apt-get gives.
    """
    result, log = _run(world)

    assert result.returncode == 0, result.stderr
    configure = next((i for i, line in enumerate(log) if line.startswith("dpkg --configure -a")), None)
    assert configure is not None, log
    apt = [i for i, line in enumerate(log) if line.startswith("apt-get ")]
    assert apt and configure < min(apt), log


def test_the_install_refuses_to_remove_a_package(world):
    """The one apt transaction installs the file it was given and removes nothing.

    Without `--no-remove`, apt satisfies a dependency the new .deb cannot meet
    by removing an installed package and exits 0: the wrapper says success over
    a satellite that lost a package, snapclient possibly with it.
    """
    _, log = _run(world)

    installs = [line.split() for line in log if line.startswith("apt-get install")]
    assert len(installs) == 1, log
    assert "--no-remove" in installs[0], installs[0]
    assert str(world["deb"]) in installs[0], installs[0]


def test_an_install_apt_refuses_is_a_failure(world):
    """apt-get's refusal — `--no-remove` aborting, or any other — reaches the
    caller as a non-zero exit, which `SnapclientService` reports and recovers
    from (it starts the stopped unit back).
    """
    result, _ = _run(world, apt_install_rc=100)

    assert result.returncode != 0, result.stdout
