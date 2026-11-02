"""`milo-deploy-update install-deb`, run for real under bash with apt and dpkg stubbed.

`UpdateService._update_multiroom` installs snapserver and snapclient through
this subcommand and believes its exit status. The helper used to run
`dpkg -i … || true` then `apt-get -f install -y`, which "repairs" a Depends it
cannot satisfy by removing the package just unpacked, exits 0, and the helper
printed OK: multiroom uninstalled under a successful update, every room mute.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-deploy-update"

DEB_PACKAGE = "snapserver"
DEB_VERSION = "0.35.0-1"

# Each stub appends its name and argv to $STUB_LOG, so a test can prove the
# helper ran the stubs and not the host's real apt.
STUBS = {
    "apt": "exit 0",
    "apt-get": 'exit "${STUB_APT_GET_RC:-0}"',
    "dpkg": "exit 1",
    "dpkg-deb": (
        'case "$3" in\n'
        f"  Package) echo {DEB_PACKAGE} ;;\n"
        f"  Version) echo {DEB_VERSION} ;;\n"
        "  *) exit 2 ;;\n"
        "esac"
    ),
    # An empty answer is dpkg-query's "no packages found matching": exit 1.
    "dpkg-query": 'printf "%s" "$STUB_DPKG_QUERY"\n[ -n "$STUB_DPKG_QUERY" ]',
}


@pytest.fixture
def world():
    """A stub directory, a log, and a .deb under /tmp.

    The .deb is not under `tmp_path`: `validate_temp_path` accepts /tmp and
    /var/tmp only, and pytest's base directory may sit elsewhere.
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
    deb = root / f"{DEB_PACKAGE}_{DEB_VERSION}_arm64.deb"
    deb.write_bytes(b"!<arch>\n")
    yield {"root": root, "stubs": stubs, "log": root / "stub.log", "deb": deb}
    shutil.rmtree(root, ignore_errors=True)


def _run(world, *, dpkg_query, apt_get_rc=0):
    env = {
        "PATH": f"{world['stubs']}:/usr/bin:/bin",
        "STUB_LOG": str(world["log"]),
        "STUB_DPKG_QUERY": dpkg_query,
        "STUB_APT_GET_RC": str(apt_get_rc),
    }
    result = subprocess.run(
        ["bash", str(HELPER), "install-deb", str(world["deb"])],
        env=env, capture_output=True, text=True, timeout=30,
    )
    log = world["log"].read_text() if world["log"].exists() else ""
    # Without this, a PATH that missed the stubs runs the host's real apt as a
    # non-root user, which fails — and every failure case below passes on it.
    assert "apt-get " in log, (
        f"the helper never reached the apt-get stub; stub log: {log!r}, stderr: {result.stderr!r}"
    )
    return result, log


@pytest.mark.parametrize(
    "dpkg_query, apt_get_rc",
    [
        pytest.param(f"config-files {DEB_VERSION}", 0, id="apt-removed-the-package"),
        pytest.param("installed 0.34.0-1", 0, id="the-old-version-stayed"),
        pytest.param("", 0, id="not-installed-at-all"),
        pytest.param(f"installed {DEB_VERSION}", 100, id="apt-get-refused"),
    ],
)
def test_install_deb_refuses_to_report_a_package_apt_did_not_install(world, dpkg_query, apt_get_rc):
    """The helper answers OK only when dpkg reads the .deb's own package
    installed at the .deb's own version.

    If this fails, `_update_multiroom` reports a successful update over a
    snapserver or snapclient that apt removed, or left at the old version, and
    the reconciliation then skips the uninstalled package forever.
    """
    result, _ = _run(world, dpkg_query=dpkg_query, apt_get_rc=apt_get_rc)

    assert result.returncode != 0, result.stdout
    assert "OK:" not in result.stdout


def test_install_deb_reports_the_package_it_installed(world):
    """The nominal path still answers OK, having installed the file it was given.

    If this fails, every multiroom update `_update_multiroom` runs is a failure.
    """
    result, log = _run(world, dpkg_query=f"installed {DEB_VERSION}")

    assert result.returncode == 0, result.stderr
    assert "OK:" in result.stdout
    installs = [line for line in log.splitlines() if line.startswith("apt-get ")]
    assert any(str(world["deb"]) in line for line in installs), installs

