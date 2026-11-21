"""Structural guardrail: unattended-upgrades takes Debian-Security and nothing
else, and never reboots, removes or asks.

Every unit installs Debian's security fixes on its own, from the image on: the
package is in system-manifest.env, apt's two daily timers that run it are
enabled there, and its configuration is one file both trees ship,
`etc/apt/apt.conf.d/52milo-unattended-upgrades` — the server's copy reaches a
card converted into a satellite, the satellite's copy every app update after.

What that file means is apt's to say, so apt reads it: `apt-config dump`, over
a directory holding it beside the two files the package itself writes (its
conffile, and the 20auto-upgrades a debconf "no" leaves). apt appends a list
across files, so a pattern list without `#clear` would keep the package's own —
Debian's point releases included — under a file that reads as "security only".
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CONF = Path("etc/apt/apt.conf.d/52milo-unattended-upgrades")
SERVER_CONF = REPO_ROOT / "rootfs" / CONF
SATELLITE_CONF = REPO_ROOT / "milo-client" / "rootfs" / CONF
MANIFEST = REPO_ROOT / "system-manifest.env"

# What Debian's unattended-upgrades package installs before Milō's file is read
# (its conffile, trimmed to the lists it declares), and what its postinst
# writes when the debconf question is answered no. The outside world, as
# Debian ships it.
DEBIAN_CONFFILE = """\
Unattended-Upgrade::Origins-Pattern {
//      "origin=Debian,codename=${distro_codename}-updates";
        "origin=Debian,codename=${distro_codename},label=Debian";
        "origin=Debian,codename=${distro_codename},label=Debian-Security";
        "origin=Debian,codename=${distro_codename}-security,label=Debian-Security";
};
Unattended-Upgrade::Package-Blacklist {
};
"""
DEBCONF_NO = """\
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Unattended-Upgrade "0";
"""

needs_apt = pytest.mark.skipif(
    shutil.which("apt-config") is None, reason="apt-config is apt's: run on Debian or Ubuntu (CI does)"
)


def _apt_dump(tmp_path: Path, conf_text: str) -> dict[str, list[str]]:
    """Every key apt ends up with, reading the package's two files then ours,
    in apt's own order: key -> its values (several for a list)."""
    parts = tmp_path / "apt.conf.d"
    parts.mkdir()
    (parts / "50unattended-upgrades").write_text(DEBIAN_CONFFILE)
    (parts / "20auto-upgrades").write_text(DEBCONF_NO)
    (parts / CONF.name).write_text(conf_text)
    main = tmp_path / "apt.conf"
    main.write_text(f'Dir::Etc::parts "{parts}";\nDir::Etc::main "/dev/null";\n')
    out = subprocess.run(
        ["apt-config", "dump", "--format", "%f=%v%n"],
        env={**os.environ, "APT_CONFIG": str(main)},
        capture_output=True, text=True, check=True,
    ).stdout
    found: dict[str, list[str]] = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        found.setdefault(key.removesuffix("::"), []).append(value)
    return found


def _violations(dump: dict[str, list[str]]) -> list[str]:
    """What breaks the owner's rule, read off apt's own view."""
    found = []
    patterns = [p for p in dump.get("Unattended-Upgrade::Origins-Pattern", []) if p]
    if not patterns:
        found.append("no origin at all: no fix would ever be installed")
    for pattern in patterns:
        fields = dict(f.split("=", 1) for f in pattern.split(",") if "=" in f)
        if fields.get("origin") != "Debian" or fields.get("label") != "Debian-Security":
            found.append(f"an origin beyond Debian-Security: {pattern}")
    if [p for p in dump.get("Unattended-Upgrade::Allowed-Origins", []) if p]:
        found.append("Allowed-Origins adds origins of its own")
    expected = {
        "Unattended-Upgrade::Automatic-Reboot": "false",
        "Unattended-Upgrade::Remove-Unused-Dependencies": "false",
        "Unattended-Upgrade::Remove-New-Unused-Dependencies": "false",
        "Unattended-Upgrade::Remove-Unused-Kernel-Packages": "false",
        "APT::Periodic::Update-Package-Lists": "1",
        "APT::Periodic::Unattended-Upgrade": "1",
    }
    for key, value in expected.items():
        if dump.get(key) != [value]:
            found.append(f"{key} is {dump.get(key)}, not {value!r}")
    options = dump.get("DPkg::Options", [])
    if not {"--force-confdef", "--force-confold"} <= set(options):
        found.append(f"a fix whose conffile Milō rewrote would prompt, and be skipped: {options}")
    return found


@needs_apt
def test_apt_reads_debian_security_only_never_a_reboot(tmp_path):
    dump = _apt_dump(tmp_path, SERVER_CONF.read_text())
    assert dump, "apt-config printed nothing: the dump is not read"

    assert _violations(dump) == []


@needs_apt
@pytest.mark.parametrize("drift", ["no-clear", "reboot"])
def test_the_guardrail_goes_red_on_a_simulated_drift(tmp_path, drift):
    """Without the `#clear`, apt keeps the package's list under ours — the
    point releases with it; a reboot of its own is the one this owner refused."""
    text = SERVER_CONF.read_text()
    if drift == "no-clear":
        text = "\n".join(line for line in text.splitlines() if not line.startswith("#clear"))
    else:
        text = text.replace('Automatic-Reboot "false"', 'Automatic-Reboot "true"')
    assert text != SERVER_CONF.read_text(), "the drift did not apply"

    assert _violations(_apt_dump(tmp_path, text)) != []


def test_both_trees_ship_it():
    """The satellite's deploy installs every rootfs file: its copy is what keeps
    a satellite's rule the server's after a push (the two are held equal, but
    for their `Twin of` line, by test_rootfs_deployment.py)."""
    assert SERVER_CONF.is_file() and SATELLITE_CONF.is_file()
    assert "Twin of " in SERVER_CONF.read_text() and "Twin of " in SATELLITE_CONF.read_text()


def _manifest_array(name: str) -> list[str]:
    out = subprocess.run(
        ["bash", "-c", f'source "{MANIFEST}"; printf "%s\\n" "${{{name}[@]}}"'],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert len(out) >= 5, f"only {out} read from {MANIFEST.name}::{name}"
    return out


def test_the_image_installs_the_package_and_enables_what_runs_it():
    """The file alone does nothing: the package reads it, and apt's timers run
    the package. Each must be in the manifest the image and every update apply."""
    assert "unattended-upgrades" in _manifest_array("SYSTEM_APT_PACKAGES")
    enabled = set(_manifest_array("SYSTEM_UNITS_ENABLED"))
    assert {"apt-daily.timer", "apt-daily-upgrade.timer", "unattended-upgrades.service"} <= enabled
