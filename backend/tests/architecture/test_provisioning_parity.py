"""Structural guardrail: the two provisioning paths must enable the same units.

A Milō unit is provisioned one of two ways — `install.sh` on a stock Raspberry Pi
OS Lite, or a flash of the pi-gen image built by `pi-gen/stage-milo/`. Both write
the same files; only one of them decides what starts at boot, and nothing tied
the two lists together.

That gap already shipped: `pi-gen/stage-milo/03-configure/01-run.sh` enables
`milo-navidrome.service` and `install/system.sh::enable_services` did not. The
unit's `PartOf=milo-backend.service` propagates stop and restart but never
*starts* anything, so every script-installed unit booted with the Navidrome
catalog engine down and a permanently empty Music Library — no exception, no log
line, nothing to notice but an empty screen.

Nothing else catches this class. Both lists are shell run on a machine CI never
touches; there is no import to fail and no route to 404, and
`test_rootfs_deployment.py` checks what each tree *carries*, not what it enables.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
import subprocess
from pathlib import Path

import pytest

from backend.core.models.audio_state import AudioSource

REPO_ROOT = Path(__file__).resolve().parents[3]

INSTALL_DIR = REPO_ROOT / "install"
INSTALL_ENTRY = REPO_ROOT / "install.sh"
PI_GEN_DIR = REPO_ROOT / "pi-gen" / "stage-milo"
SYSTEM_DIR = REPO_ROOT / "system"

# `systemctl enable <unit>`, with or without sudo, ignoring a leading comment.
ENABLE_RE = re.compile(r"^[^#\n]*systemctl\s+enable\s+(\S+)", re.MULTILINE)

# A shell function invoked as a bare statement: the name alone on its line, or
# followed by arguments. Anchored at line start so a mention inside a comment or
# a `systemctl` argument never counts as a call.
CALL_RE = re.compile(r"^\s*([a-z_][a-z0-9_]*)(?:\s|$)", re.MULTILINE)

# Units the image enables and a script install deliberately does not. Both are
# specific to a *flashed* device, not to the software.
IMAGE_ONLY = {
    # Detects server-vs-client role on the first boot of a freshly flashed card.
    # A script install has already chosen its role — that is which script was run.
    "milo-first-boot",
    # Writes the bootloader EEPROM, which lives on the board's SPI flash and so
    # cannot be baked into an image. During a script install the same function
    # (install/power-button.sh::configure_power_on_behavior) runs directly.
    "milo-eeprom-setup",
}

# Units a script install enables and the image deliberately does not. Empty: the
# image is the superset by construction. Kept named so a future divergence is
# stated here rather than absorbed into the assertion.
INSTALLER_ONLY: set[str] = set()


def _unit_name(target: str) -> str:
    """`milo-backend.service` and `milo-backend` are the same unit to systemd.

    The trailing `;` is bash's own: `declare -f` re-prints a body with explicit
    statement terminators, where the source file relies on the newline.
    """
    return target.rstrip(";").removesuffix(".service")


def _enabled_in(text: str) -> set[str]:
    return {_unit_name(m) for m in ENABLE_RE.findall(text)}


def _shell_text(paths: list[Path]) -> str:
    return "\n".join(p.read_text() for p in paths)


def _pi_gen_scripts() -> list[Path]:
    return sorted(PI_GEN_DIR.rglob("*run.sh"))


def _install_modules() -> list[Path]:
    return sorted(INSTALL_DIR.glob("*.sh"))


def _function_bodies() -> dict[str, str]:
    """Every function `install/` defines, as bash itself parses it.

    Asking bash rather than brace-matching by hand: these bodies carry heredocs,
    `${...}` expansions and nested braces, and a hand parser that terminates a
    function early silently under-reports what it enables — which is the one
    failure mode this file exists to prevent.
    """
    proc = subprocess.run(
        ["bash", "-c", 'for f in *.sh; do source "./$f" || exit 1; done; declare -f'],
        cwd=INSTALL_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"sourcing install/*.sh failed: {proc.stderr}"

    bodies: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in proc.stdout.splitlines():
        header = re.match(r"^([a-z_][a-z0-9_]*) \(\) $", line)
        if header:
            if current:
                bodies[current] = "\n".join(lines)
            current = header.group(1)
            lines = []
        elif current:
            lines.append(line)
    if current:
        bodies[current] = "\n".join(lines)
    return bodies


def _reachable(entry_text: str, bodies: dict[str, str]) -> set[str]:
    """Transitive closure of the `install/` functions an entry point calls."""
    pending = [n for n in CALL_RE.findall(entry_text) if n in bodies]
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend(n for n in CALL_RE.findall(bodies[name]) if n in bodies)
    return seen


def _units_enabled(entry_paths: list[Path], bodies: dict[str, str]) -> set[str]:
    """What a provisioning path enables: its own lines plus the ones it calls."""
    text = _shell_text(entry_paths)
    units = _enabled_in(text)
    for name in _reachable(text, bodies):
        units |= _enabled_in(bodies[name])
    return units


@pytest.fixture(scope="module")
def bodies() -> dict[str, str]:
    parsed = _function_bodies()
    # Non-trivial-output check: a parse that yields nothing, or that truncates a
    # body at its first heredoc, must fail here rather than pass every assertion
    # below on an empty surface.
    assert len(parsed) > 30, f"only {len(parsed)} install/ functions parsed"
    assert "systemctl enable milo-ir-keytable.service" in bodies_of(
        parsed, "install_ir_systemd_service"
    )
    assert "sites-available/milo" in bodies_of(parsed, "write_nginx_site")
    return parsed


def bodies_of(parsed: dict[str, str], name: str) -> str:
    assert name in parsed, f"install/ no longer defines {name}()"
    return parsed[name]


def test_both_provisioning_paths_enable_the_same_units(bodies):
    """A unit enabled by one installer and not the other boots differently.

    Fails when a new systemd unit is wired into `pi-gen/` or `install/` alone.
    The symptom is never an error — the unit is simply never started on half the
    fleet (see the module docstring for the Navidrome case).
    """
    script_paths = [INSTALL_ENTRY, *_install_modules()]
    image_paths = _pi_gen_scripts()

    assert len(image_paths) >= 4, f"pi-gen stage parse found {len(image_paths)} scripts"

    script_units = _units_enabled(script_paths, bodies)
    image_units = _units_enabled(image_paths, bodies)

    # Non-trivial-output check on both extractions.
    assert "milo-backend" in script_units
    assert "milo-backend" in image_units

    assert image_units - script_units == IMAGE_ONLY, (
        "pi-gen enables units install.sh does not: "
        f"{sorted(image_units - script_units - IMAGE_ONLY)}"
    )
    assert script_units - image_units == INSTALLER_ONLY, (
        "install.sh enables units pi-gen does not: "
        f"{sorted(script_units - image_units - INSTALLER_ONLY)}"
    )


def test_nginx_site_config_is_written_from_one_place(bodies):
    """pi-gen must source the installer's nginx writer, not restate it.

    `install/network.sh` was the one installer file pi-gen copy-pasted instead of
    sourcing, so an nginx change reached script-installed units only. Fails if a
    `server {` block reappears anywhere under `pi-gen/`.
    """
    assert "server_name milo.local" in bodies_of(bodies, "write_nginx_site")

    for script in _pi_gen_scripts():
        assert "server_name" not in script.read_text(), (
            f"{script.relative_to(REPO_ROOT)} restates the nginx site config; "
            "source install/network.sh and call write_nginx_site instead"
        )


def test_the_kernel_command_line_is_written_from_one_place():
    """pi-gen must reuse the installer's cmdline writer, not restate the list.

    `install/boot-common.sh` declares `BOOT_PARAMS_COMMON` and calls itself the
    single source of truth for both paths; `pi-gen/…/03-configure/00-run.sh`
    restated the whole list inline, twenty lines above the block that sources
    that very file. The two then drifted by exactly one token —
    `cfg80211.ieee80211_regdom=FR` on the image, `=00` in the installer — so a
    flashed unit came up under French radio rules wherever it was sold. Fails if
    any pi-gen script grows its own copy of the list.
    """
    declarations = [
        p for p in [*INSTALL_DIR.glob("*.sh"), INSTALL_ENTRY, *_pi_gen_scripts()]
        if "BOOT_PARAMS_COMMON=" in p.read_text()
    ]
    assert declarations == [INSTALL_DIR / "boot-common.sh"], (
        "BOOT_PARAMS_COMMON must be declared exactly once, in install/boot-common.sh; "
        f"found in {[str(p.relative_to(REPO_ROOT)) for p in declarations]}"
    )

    # The token below appears only in a full command line, never in a call to the
    # writer — so its presence under pi-gen/ *is* a restated list.
    for script in _pi_gen_scripts():
        assert "plymouth.ignore-serial-consoles" not in script.read_text(), (
            f"{script.relative_to(REPO_ROOT)} restates the kernel command line; "
            "source install/boot-common.sh + install/display.sh and call "
            "configure_cmdline \"$BOOT_PARAMS_COMMON $BOOT_PARAMS_SCREEN\" instead"
        )


def _source_units() -> set[str]:
    """Units the backend starts on demand, derived from the typed enum.

    `milo-<source_id>.service`, `_`→`-`. These carry an [Install] and are
    deliberately never enabled — `install/system.sh` lists them by name in a
    comment for exactly that reason.
    """
    ids = [s.value for s in AudioSource if s is not AudioSource.NONE]
    assert len(ids) >= 10, f"AudioSource yielded only {ids} — the extractor is broken"
    return {f"milo-{i.replace('_', '-')}" for i in ids}


def test_every_installable_unit_is_enabled_or_is_started_on_demand(bodies):
    """An [Install] nothing acts on is dead config, and a live trap.

    `milo-navidrome-config.service` carried `WantedBy=multi-user.target` while
    being pulled by `Wants=` from `milo-navidrome.service` — enabled by neither
    installer, so the section did nothing. Acting on it would have broken what
    `milo-first-boot`'s server-service list assumes: a converted satellite
    disables `milo-navidrome` but not that unit, so an enabled [Install] would
    re-emit a catalog config at every boot on a machine that serves no catalog.
    """
    installable = {
        p.stem for p in sorted(SYSTEM_DIR.glob("*.service"))
        if re.search(r"^\[Install\]", p.read_text(), re.MULTILINE)
    }
    assert len(installable) >= 15, f"only {sorted(installable)} units parsed"

    enabled = _units_enabled([INSTALL_ENTRY, *_install_modules()], bodies)
    enabled |= _units_enabled(_pi_gen_scripts(), bodies)
    assert "milo-backend" in enabled, "the enabled-unit extractor is broken"

    orphans = sorted(installable - enabled - _source_units())
    assert not orphans, (
        "these units declare [Install] but no provisioning path enables them and "
        "they are not per-source units: " + ", ".join(orphans)
    )
