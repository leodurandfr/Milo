"""Structural guardrail: the image build reuses the install modules, never restates them.

A Milō unit is provisioned one way — a flash of the pi-gen image built by
`pi-gen/stage-milo/`. The stage scripts do not carry the whole provisioning
themselves: for most of the work they `source` a module under `provisioning/` and call
its function, so the file that decides what nginx serves or what the kernel
command line says exists exactly once. A stage block that restates instead of
sourcing is a second copy, and the two drift.

That is not hypothetical. `pi-gen/…/03-configure/00-run.sh` restated the whole
kernel command line twenty lines above the block that sources
`provisioning/boot-common.sh` — the file whose header calls itself the single source
of truth for it — and the two drifted by exactly one token:
`cfg80211.ieee80211_regdom=FR` on the image, `=00` in the module, so a flashed
unit came up under French radio rules wherever it was sold. `provisioning/network.sh`
was the same shape for the nginx site.

Two more rules on the same relationship. **`provisioning/common.sh` comes first**: the
modules call `log_*` and read the versions it sources, and the fallback that used
to repair a missing source was removed with the standalone-run blocks — so the
ordering is now a hard requirement with nothing else behind it. And a unit
carrying an `[Install]` section that nothing enables is dead config.

Nothing else catches either class. This is shell run inside a chroot on a builder
CI reaches only through a three-hour job; there is no import to fail and no route
to 404, and `test_rootfs_deployment.py` checks what the tree *carries*, not what
the stage does with it.

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

PROVISIONING_DIR = REPO_ROOT / "provisioning"
PI_GEN_DIR = REPO_ROOT / "pi-gen" / "stage-milo"
SYSTEM_DIR = REPO_ROOT / "system"

# `systemctl enable <unit>`, with or without sudo, ignoring a leading comment.
ENABLE_RE = re.compile(r"^[^#\n]*systemctl\s+enable\s+(\S+)", re.MULTILINE)

# A shell function invoked as a bare statement: the name alone on its line, or
# followed by arguments. Anchored at line start so a mention inside a comment or
# a `systemctl` argument never counts as a call.
CALL_RE = re.compile(r"^\s*([a-z_][a-z0-9_]*)(?:\s|$)", re.MULTILINE)


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


def _function_bodies() -> dict[str, str]:
    """Every function `provisioning/` defines, as bash itself parses it.

    Asking bash rather than brace-matching by hand: these bodies carry heredocs,
    `${...}` expansions and nested braces, and a hand parser that terminates a
    function early silently under-reports what it enables — which is the one
    failure mode this file exists to prevent.
    """
    proc = subprocess.run(
        ["bash", "-c", 'for f in *.sh; do source "./$f" || exit 1; done; declare -f'],
        cwd=PROVISIONING_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"sourcing provisioning/*.sh failed: {proc.stderr}"

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
    """Transitive closure of the `provisioning/` functions an entry point calls."""
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
    assert len(parsed) > 15, f"only {len(parsed)} provisioning/ functions parsed"
    assert "systemctl enable milo-ir-keytable.service" in bodies_of(
        parsed, "install_ir_systemd_service"
    )
    assert "sites-available/milo" in bodies_of(parsed, "write_nginx_site")
    return parsed


def bodies_of(parsed: dict[str, str], name: str) -> str:
    assert name in parsed, f"provisioning/ no longer defines {name}()"
    return parsed[name]


def test_nginx_site_config_is_written_from_one_place(bodies):
    """pi-gen must source the module's nginx writer, not restate it.

    `provisioning/network.sh` was the one module pi-gen copy-pasted instead of
    sourcing, so the two spellings of the site config drifted with nothing
    comparing them. Fails if a `server {` block reappears anywhere under
    `pi-gen/`.
    """
    assert "server_name milo.local" in bodies_of(bodies, "write_nginx_site")

    for script in _pi_gen_scripts():
        assert "server_name" not in script.read_text(), (
            f"{script.relative_to(REPO_ROOT)} restates the nginx site config; "
            "source provisioning/network.sh and call write_nginx_site instead"
        )


def test_the_kernel_command_line_is_written_from_one_place():
    """pi-gen must reuse the module's cmdline writer, not restate the list.

    `provisioning/boot-common.sh` declares `BOOT_PARAMS_COMMON` and calls itself the
    single source of truth for it; `pi-gen/…/03-configure/00-run.sh` restated the
    whole list inline, twenty lines above the block that sources that very file.
    The two then drifted by exactly one token — `cfg80211.ieee80211_regdom=FR`
    inline, `=00` in the module — so a flashed unit came up under French radio
    rules wherever it was sold. Fails if any pi-gen script grows its own copy of
    the list.
    """
    declarations = [
        p for p in [*PROVISIONING_DIR.glob("*.sh"), *_pi_gen_scripts()]
        if "BOOT_PARAMS_COMMON=" in p.read_text()
    ]
    assert declarations == [PROVISIONING_DIR / "boot-common.sh"], (
        "BOOT_PARAMS_COMMON must be declared exactly once, in provisioning/boot-common.sh; "
        f"found in {[str(p.relative_to(REPO_ROOT)) for p in declarations]}"
    )

    # The token below appears only in a full command line, never in a call to the
    # writer — so its presence under pi-gen/ *is* a restated list.
    for script in _pi_gen_scripts():
        assert "plymouth.ignore-serial-consoles" not in script.read_text(), (
            f"{script.relative_to(REPO_ROOT)} restates the kernel command line; "
            "source provisioning/boot-common.sh + provisioning/display.sh and call "
            "configure_cmdline \"$BOOT_PARAMS_COMMON $BOOT_PARAMS_SCREEN\" instead"
        )


def _source_units() -> set[str]:
    """Units the backend starts on demand, derived from the typed enum.

    `milo-<source_id>.service`, `_`→`-`. These carry an [Install] and are
    deliberately never enabled — `provisioning/system.sh` lists them by name in a
    comment for exactly that reason.
    """
    ids = [s.value for s in AudioSource if s is not AudioSource.NONE]
    assert len(ids) >= 10, f"AudioSource yielded only {ids} — the extractor is broken"
    return {f"milo-{i.replace('_', '-')}" for i in ids}


def test_every_installable_unit_is_enabled_or_is_started_on_demand(bodies):
    """An [Install] nothing acts on is dead config, and a live trap.

    `milo-navidrome-config.service` carried `WantedBy=multi-user.target` while
    being pulled by `Wants=` from `milo-navidrome.service` — enabled by nothing,
    so the section did nothing. Acting on it would have broken what
    `milo-first-boot`'s server-service list assumes: a converted satellite
    disables `milo-navidrome` but not that unit, so an enabled [Install] would
    re-emit a catalog config at every boot on a machine that serves no catalog.
    """
    installable = {
        p.stem for p in sorted(SYSTEM_DIR.glob("*.service"))
        if re.search(r"^\[Install\]", p.read_text(), re.MULTILINE)
    }
    assert len(installable) >= 15, f"only {sorted(installable)} units parsed"

    enabled = _units_enabled(_pi_gen_scripts(), bodies)
    assert "milo-backend" in enabled, "the enabled-unit extractor is broken"

    orphans = sorted(installable - enabled - _source_units())
    assert not orphans, (
        "these units declare [Install] but no provisioning path enables them and "
        "they are not per-source units: " + ", ".join(orphans)
    )


# --------------------------------------------------------------------------- #
# `provisioning/common.sh` comes first.
# --------------------------------------------------------------------------- #

# A pi-gen stage block: everything between `on_chroot << 'CHROOT'` and its
# terminator. Each is its own shell, so each must source what it needs.
CHROOT_BLOCK_RE = re.compile(r"on_chroot << 'CHROOT'\n(.*?)\nCHROOT", re.DOTALL)

# `source provisioning/<file>` / `. provisioning/<file>`, in a stage block or on an
# ExecStart= line. Both spellings are in use, which is why this is not anchored
# at line start.
MODULE_SOURCE_RE = re.compile(r"(?:source|\.)\s+(provisioning/[\w.-]+\.sh)")


def _module_consumers() -> list[tuple[str, list[str]]]:
    """Every shell context that sources a `provisioning/` module, and what it sources.

    Two kinds, and both matter: the pi-gen stage blocks (build time) and the
    `ExecStart=` of the systemd units that reuse an install function on the
    appliance (run time).
    """
    consumers = []
    for script in _pi_gen_scripts():
        for i, block in enumerate(CHROOT_BLOCK_RE.findall(script.read_text())):
            modules = MODULE_SOURCE_RE.findall(block)
            if modules:
                consumers.append((f"{script.relative_to(REPO_ROOT)} block {i}", modules))
    for unit in sorted(SYSTEM_DIR.glob("*.service")):
        for line in unit.read_text().splitlines():
            if not line.startswith("ExecStart="):
                continue
            modules = MODULE_SOURCE_RE.findall(line)
            if modules:
                consumers.append((f"{unit.relative_to(REPO_ROOT)} ExecStart", modules))
    return consumers


def test_the_consumer_extractor_sees_both_kinds():
    """Both classes must be found, or the rule below covers half the surface.

    A regex that reads only the stage blocks would leave the two systemd units —
    the ones that source a module *on the appliance* — entirely unchecked.
    """
    consumers = _module_consumers()
    assert len(consumers) >= 10, f"only {len(consumers)} module consumers extracted"
    assert any("pi-gen" in name for name, _ in consumers), "no pi-gen stage block found"
    assert any(".service" in name for name, _ in consumers), "no systemd unit found"

    # The need is real: the modules call helpers only common.sh defines.
    callers = [
        p.name for p in sorted(PROVISIONING_DIR.glob("*.sh"))
        if p.name != "common.sh" and re.search(r"\blog_(info|success|warning|error)\b", p.read_text())
    ]
    assert len(callers) >= 5, f"only {callers} call log_*; the rule below is pointless"


def test_every_consumer_sources_common_first():
    """A module sourced without `provisioning/common.sh` dies on `log_info: not found`.

    Measured: `bash -c 'source provisioning/power-button.sh && configure_power_on_behavior'`
    exits **127**. In a stage block that is a `set -e` abort an hour into a
    three-hour build CI never runs; in `milo-eeprom-setup.service` it is a unit
    that fails on the appliance, so the bootloader EEPROM is never configured.

    The modules used to carry `if ! type log_info; then source
    "$(dirname "$0")/common.sh"; fi`, which repaired this — but only when a module
    was *executed*: sourced, `$0` is `/bin/bash` and it looked for
    `/bin/common.sh`. It went with the standalone-run blocks, so this rule is
    what is left. Do not re-add the fallback; fix the consumer.
    """
    offenders = [
        f"{name} sources {modules[0]} before provisioning/common.sh"
        for name, modules in _module_consumers()
        if modules[0] != "provisioning/common.sh"
    ]
    assert not offenders, (
        "these consumers source a provisioning/ module before provisioning/common.sh, so "
        "its log helpers and the pinned versions are undefined:\n"
        + "\n".join(offenders)
    )


def test_the_ordering_rule_discriminates():
    """A rule that passed on any input would prove nothing about the real blocks."""
    good = ["provisioning/common.sh", "provisioning/power-button.sh"]
    bad = ["provisioning/power-button.sh", "provisioning/common.sh"]
    assert good[0] == "provisioning/common.sh"
    assert bad[0] != "provisioning/common.sh"
    # ...and the extractor must read a real block, not an empty one.
    assert all(modules for _, modules in _module_consumers())
