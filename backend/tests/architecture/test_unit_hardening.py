"""Structural guardrail: on the server, only the backend may gain privileges.

`/etc/sudoers.d/milo-backend` makes the `milo` user root by construction: a bare
`systemctl`, `milo-deploy-update` (which installs, as root, a checkout `milo` can
write) and `milo-set-password`. Every other process running as `milo` — the
network-facing daemons (go-librespot, shairport-sync, qobuz-proxy, Tidal's SDK,
roc-recv), BlueALSA on the radio, the kiosk's browser — reaches that policy too
unless its unit sets `NoNewPrivileges=true`, which makes the kernel refuse the
setuid `sudo` outright. Measured before this test: ten of them did not, so code
execution in any one led to `sudo -n systemctl link …` and root without a password.

The unit list is derived from `system/`, the tree the image build copies verbatim;
a unit added there with `User=milo` is covered the day it is added.
"""

import re
from pathlib import Path

SYSTEM_ROOT = Path(__file__).resolve().parents[3] / "system"

# The one exception, with its reason. Anything else that needs sudo must be named
# here, which is a review of the privilege it buys.
MAY_GAIN_PRIVILEGES = {
    "milo-backend.service": "the one process that runs sudo (SystemdServiceManager and the milo-* helpers)",
}

_SECTION = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$")


def service_section(unit: Path) -> dict[str, list[str]]:
    """The `[Service]` section's directives, each key mapped to every value it takes."""
    directives: dict[str, list[str]] = {}
    section = None
    for raw in unit.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        header = _SECTION.match(line)
        if header:
            section = header.group("name")
            continue
        if section == "Service" and "=" in line:
            key, _, value = line.partition("=")
            directives.setdefault(key.strip(), []).append(value.strip())
    return directives


def sets_no_new_privileges(directives: dict[str, list[str]]) -> bool:
    """Whether the last `NoNewPrivileges=` assignment is a systemd true."""
    values = directives.get("NoNewPrivileges")
    return bool(values) and values[-1].lower() in {"1", "yes", "true", "on"}


def milo_units() -> dict[str, dict[str, list[str]]]:
    """Every shipped unit whose service runs as the `milo` user."""
    units = {
        unit.name: directives
        for unit in sorted(SYSTEM_ROOT.glob("*.service"))
        if (directives := service_section(unit)).get("User") == ["milo"]
    }
    assert len(units) >= 15, (
        f"only {sorted(units)} run as milo under {SYSTEM_ROOT} — the extractor is broken"
    )
    assert "milo-backend.service" in units, "the backend's unit was not extracted"
    return units


def test_every_milo_unit_but_the_backend_sets_no_new_privileges():
    """A `User=milo` process without NoNewPrivileges can run the backend's sudo grants.

    Fails when a unit under system/ runs as `milo` and leaves the flag unset: a
    compromise of that daemon is then root, through a policy written for the
    backend alone.
    """
    units = milo_units()
    assert set(MAY_GAIN_PRIVILEGES) <= set(units), (
        f"an exception names a unit that no longer runs as milo: "
        f"{sorted(set(MAY_GAIN_PRIVILEGES) - set(units))}"
    )
    missing = sorted(
        name
        for name, directives in units.items()
        if name not in MAY_GAIN_PRIVILEGES and not sets_no_new_privileges(directives)
    )
    assert not missing, (
        f"these units run as milo without NoNewPrivileges=true, so they can reach "
        f"/etc/sudoers.d/milo-backend: {missing}"
    )
