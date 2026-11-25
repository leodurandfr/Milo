"""`milo-deploy-update install-deb`, run for real under bash with apt and dpkg stubbed.

`UpdateService._update_multiroom` installs snapserver and snapclient through
this subcommand and believes its exit status. The helper used to run
`dpkg -i … || true` then `apt-get -f install -y`, which "repairs" a Depends it
cannot satisfy by removing the package just unpacked, exits 0, and the helper
printed OK: multiroom uninstalled under a successful update, every room mute.

It also checks the .deb against the digest dependencies.env declares, on a copy
only root can write, before dpkg reads anything — and so does
`install-release-binary`, for the tarballs go-librespot, CamillaDSP and
Navidrome ship in, before tar unpacks one. The satellite's installers do the
same, and are run the same way by milo-client's own tests.
"""

import hashlib
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
    # Its first $STUB_DPKG_LOCKED runs answer as dpkg does under another dpkg's
    # lock (dpkg 1.22's words); every other run fails, finishing nothing.
    "dpkg": (
        'runs=$(grep -c "^dpkg " "$STUB_LOG")\n'
        'if [ "$runs" -le "${STUB_DPKG_LOCKED:-0}" ]; then\n'
        '  echo "dpkg: error: dpkg frontend lock was locked by /usr/bin/python3.13 process with pid 812" >&2\n'
        '  exit 2\n'
        'fi\n'
        'exit 1'
    ),
    "dpkg-deb": (
        'case "$3" in\n'
        f"  Package) echo {DEB_PACKAGE} ;;\n"
        f"  Version) echo {DEB_VERSION} ;;\n"
        "  *) exit 2 ;;\n"
        "esac"
    ),
    # An empty answer is dpkg-query's "no packages found matching": exit 1.
    "dpkg-query": 'printf "%s" "$STUB_DPKG_QUERY"\n[ -n "$STUB_DPKG_QUERY" ]',
    # Recorded, never run: these tests judge whether an archive is unpacked at all.
    "tar": "exit 0",
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
    yield {"root": root, "stubs": stubs, "log": root / "stub.log", "deb": deb,
           "sha256": hashlib.sha256(deb.read_bytes()).hexdigest()}
    shutil.rmtree(root, ignore_errors=True)


def _env(world, *, dpkg_query="", apt_get_rc=0, dpkg_locked=0):
    return {
        "PATH": f"{world['stubs']}:/usr/bin:/bin",
        "STUB_LOG": str(world["log"]),
        "STUB_DPKG_QUERY": dpkg_query,
        "STUB_APT_GET_RC": str(apt_get_rc),
        "STUB_DPKG_LOCKED": str(dpkg_locked),
    }


def _log(world) -> str:
    return world["log"].read_text() if world["log"].exists() else ""


def _run(world, *, dpkg_query, apt_get_rc=0, sha256=None, dpkg_locked=0):
    result = subprocess.run(
        ["bash", str(HELPER), "install-deb", str(world["deb"]), sha256 or world["sha256"]],
        env=_env(world, dpkg_query=dpkg_query, apt_get_rc=apt_get_rc, dpkg_locked=dpkg_locked),
        capture_output=True, text=True, timeout=30,
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
    assert any(world["deb"].name in line for line in installs), installs


def test_install_deb_installs_its_checked_copy_not_the_path_it_was_given(world):
    """The path the backend names sits in a directory any milo process can
    rewrite after the backend's own check: apt reads the copy the helper made
    and checked where only root writes, so a file swapped in between is never
    what dpkg runs the maintainer scripts of.
    """
    result, log = _run(world, dpkg_query=f"installed {DEB_VERSION}")

    assert result.returncode == 0, result.stderr
    install = next(line for line in log.splitlines() if line.startswith("apt-get install"))
    assert str(world["deb"]) not in install, install
    assert f"/{world['deb'].name}" in install, install


@pytest.mark.parametrize("sha256", [
    pytest.param("0" * 64, id="another-file"),
    pytest.param("", id="empty"),
    pytest.param("not-a-digest", id="not-a-digest"),
])
def test_install_deb_refuses_a_file_that_is_not_the_declared_one(world, sha256):
    """A .deb whose sha256 is not the one dependencies.env declares, or a digest
    argument that is none, is refused before dpkg-deb or apt reads it.

    If this fails, a snapserver GitHub served differently from the one the
    manifest was validated with runs its maintainer scripts as root.
    """
    result = subprocess.run(
        ["bash", str(HELPER), "install-deb", str(world["deb"]), sha256],
        env=_env(world), capture_output=True, text=True, timeout=30,
    )

    assert result.returncode != 0, result.stdout
    assert "OK:" not in result.stdout
    assert not [line for line in _log(world).splitlines() if line.startswith(("apt", "dpkg"))], _log(world)


def test_install_deb_says_when_no_digest_is_declared(world):
    """`-` is a version past the manifest: installed, and said, never silent."""
    result = subprocess.run(
        ["bash", str(HELPER), "install-deb", str(world["deb"]), "-"],
        env=_env(world, dpkg_query=f"installed {DEB_VERSION}"),
        capture_output=True, text=True, timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "no declared digest" in result.stderr


def test_install_release_binary_never_unpacks_an_archive_that_is_not_the_declared_one(world):
    """go-librespot, CamillaDSP and Navidrome arrive as tarballs the helper
    unpacks as root: one that does not match the manifest's digest is refused
    before tar sees it."""
    archive = world["root"] / "go-librespot_linux_arm64.tar.gz"
    archive.write_bytes(b"not the release")
    result = subprocess.run(
        ["bash", str(HELPER), "install-release-binary", str(archive), "go-librespot",
         "/usr/local/bin/go-librespot", "0" * 64],
        env=_env(world), capture_output=True, text=True, timeout=30,
    )

    assert result.returncode != 0, result.stdout
    assert "checksum mismatch" in result.stderr
    assert "tar " not in _log(world), _log(world)


def test_install_release_binary_unpacks_its_checked_copy_not_the_path_it_was_given(world):
    """The archive the backend names sits in a directory any milo process can
    rewrite after the helper's own check: tar reads the copy the helper made and
    checked where only root writes, and unpacks into that same directory, so a
    tarball swapped in between is never what lands in /usr/local/bin.

    The recorded tar unpacks nothing, so the helper stops after it, on the
    missing member; what is judged is what tar was handed.
    """
    archive = world["root"] / "go-librespot_linux_arm64.tar.gz"
    archive.write_bytes(b"the release")
    result = subprocess.run(
        ["bash", str(HELPER), "install-release-binary", str(archive), "go-librespot",
         "/usr/local/bin/go-librespot", hashlib.sha256(archive.read_bytes()).hexdigest()],
        env=_env(world), capture_output=True, text=True, timeout=30,
    )

    assert "sha256 checked" in result.stdout, result.stderr
    unpack = next((line.split() for line in _log(world).splitlines() if line.startswith("tar ")), None)
    assert unpack is not None, f"tar never ran: {_log(world)!r}, stderr: {result.stderr!r}"
    read, into = unpack[unpack.index("-xzf") + 1], unpack[unpack.index("-C") + 1]
    assert read != str(archive), unpack
    assert read == f"{into}/{archive.name}" and into.startswith("/tmp/milo-checked."), unpack
    assert unpack[-1] == "go-librespot", unpack


def test_install_deb_finishes_an_interrupted_dpkg_run_before_apt(world):
    """`dpkg --configure -a` runs before apt, and its failure does not stop it.

    A power cut during an earlier install leaves dpkg "interrupted", and apt
    then refuses every install until someone runs it by hand: the dependency
    reconciliation of each later Milō update failed on it, on every unit that
    had lost power once. Fatal instead, a configure that cannot finish anything
    (dpkg stubbed to fail here) would hide the reason apt-get gives below.
    """
    result, log = _run(world, dpkg_query=f"installed {DEB_VERSION}")

    assert result.returncode == 0, result.stderr
    calls = [line.split(" ", 1)[0] + " " + line.split(" ", 2)[1] for line in log.splitlines()]
    assert "dpkg --configure" in calls, log
    configure = calls.index("dpkg --configure")
    assert configure < next(i for i, c in enumerate(calls) if c.startswith(("apt ", "apt-get "))), log


def test_install_deb_waits_for_a_dpkg_lock_before_it_gives_up_on_finishing(world):
    """A dpkg run that meets another dpkg's lock is tried again, as
    sync-system-files' is: unattended-upgrades can take the lock between the
    backend's wait for apt and this run, and a bare `dpkg --configure -a` gives
    up at once, leaving an interrupted dpkg unfinished under the install. Fails
    when install-deb finishes dpkg its own way again, once and without waiting.
    """
    result, log = _run(world, dpkg_query=f"installed {DEB_VERSION}", dpkg_locked=1)

    assert result.returncode == 0, result.stderr
    calls = [line for line in log.splitlines() if line.startswith(("dpkg ", "apt ", "apt-get "))]
    runs = [i for i, line in enumerate(calls) if line.startswith("dpkg --configure")]
    assert len(runs) == 2, f"dpkg was not tried again past the lock: {calls}"
    assert runs[-1] < next(i for i, line in enumerate(calls) if not line.startswith("dpkg ")), calls
