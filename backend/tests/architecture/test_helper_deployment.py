"""Structural guardrail: every path into a role installs that role's helpers.

`test_rootfs_deployment.py` proves each tree *carries* what its scripts source.
This one proves the helpers actually reach `/usr/local/` — and it has to be a
separate rule, because a tree can be perfect while a provisioning path quietly
copies a subset of it.

Two paths produce a unit: the image build, and — for a satellite — the
server→satellite conversion `milo-first-boot` runs on a flashed card that was
adopted. The second is where this already shipped: the conversion installed the
client tree's `asound.conf`, loopback options, CamillaDSP config, service set,
sudoers and avahi drop-in, and none of the client tree's `/usr/local/bin`. Those
reached the disk from exactly one place, pi-gen's copy loop at image-build time,
and `milo-deploy-update` refreshes the *server* tree only. Measured on a unit
flashed 2026-06-20 and app-updated 2026-08-30: every server helper current,
every client helper still June's, and `milo-client-apply-avahi-iface` (added
2026-08-14) absent — which is
`ExecStartPre=/usr/local/bin/milo-client-apply-avahi-iface` failing 203/EXEC on
`avahi-daemon.service`, i.e. no mDNS at all on the converted satellite.

Nothing else catches this class. These are bash copy loops read on a machine CI
never touches; there is no import to fail and no route to 404, and the symptom is
a helper that is simply not there, months later, on the one unit nobody can log
into.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# Each role: the tree that ships its helpers, and every provisioning path that
# can produce a unit in that role. A helper must be deployed by all of them.
ROLES = {
    "server": {
        "tree": REPO_ROOT / "rootfs",
        "paths": {
            "pi-gen": None,              # resolved to the stage scripts below
        },
    },
    "satellite": {
        "tree": REPO_ROOT / "milo-client" / "rootfs",
        "paths": {
            "pi-gen": None,
            "milo-first-boot": None,     # the server → satellite conversion
        },
    },
}

# What a role needs on disk before it can work: its helpers, and the sudoers
# policy that makes them runnable. The policy belongs here for the same reason
# the helpers do — the conversion path removed the server's and installed no
# replacement, leaving a satellite on whatever the image baked, which nothing
# refreshes. Measured on a unit flashed 2026-06-20: June's policy, carrying no
# `PASSWD: ALL` withdrawal at all.
HELPER_DIRS = ("usr/local/bin", "usr/local/lib/milo", "etc/sudoers.d")


def _pi_gen_text() -> str:
    """The stage scripts, plus the `install/` modules they source.

    The stage deploys most files with its own glob loops, but not all: it
    sources `install/ir-remote.sh` and calls `install_ir_helpers`, which is what
    puts `milo-ir-remote` in `/etc/sudoers.d`. Reading the stage alone reported
    that as undeployed — a miss the stage would have to restate to satisfy,
    which is the drift this directory exists to prevent. Only the modules the
    stage actually sources are included: pulling in all of `install/` would make
    every file look deployed by pi-gen.
    """
    stages = sorted((REPO_ROOT / "pi-gen").rglob("*run.sh"))
    text = "\n".join(p.read_text() for p in stages)
    sourced = {
        REPO_ROOT / rel
        for rel in re.findall(r"^\s*(?:source|\.)\s+(install/\S+)\s*$", text, re.MULTILINE)
    }
    assert sourced, "no install/ module sourced from pi-gen; the extractor is broken"
    return "\n".join([text] + [m.read_text() for m in sorted(sourced) if m.is_file()])


def _path_texts(role: str) -> dict[str, str]:
    if role == "server":
        return {"pi-gen": _pi_gen_text()}
    return {
        "pi-gen": _pi_gen_text(),
        "milo-first-boot": (REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-first-boot").read_text(),
    }


def _helpers(tree: Path) -> list[Path]:
    return sorted(
        p
        for sub in HELPER_DIRS
        for p in (tree / sub).glob("*")
        if p.is_file()
    )


def _uncommented(text: str) -> str:
    """Drop `#` comment lines — a helper named only in a comment is not deployed."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _deploys(text: str, tree: Path, helper: Path) -> bool:
    """Does this provisioning path install this helper?

    Two spellings count, and both are in use: naming the file (pi-gen copies
    `hardware-helpers.sh` and each sudoers policy by name) and copying the
    directory in a glob loop (pi-gen's `/usr/local/bin` loops, and the conversion
    in `milo-first-boot`). A directory loop covers everything the directory
    holds, which is the point of writing it that way — pi-gen's own comment says
    a hand-maintained allowlist is what dropped three scripts from the image.

    The match is on the *tree-relative path*, never the bare basename. A
    basename can be an ordinary word of the script: `milo-first-boot` says
    "milo-client" on almost every line — the account, the hostname, the unit
    names — so basename matching reported `etc/sudoers.d/milo-client` as
    deployed by a script that never touched it, and the rule silently proved
    nothing for that file. Every deployment in the tree writes the path.
    """
    body = _uncommented(text)
    relative = str(helper.relative_to(tree))
    if relative in body:
        return True
    directory = str(helper.parent.relative_to(tree))
    return bool(re.search(rf"{re.escape(directory)}/?\*", body)) or f"{directory}/*" in body


# --------------------------------------------------------------------------- #
# Non-triviality: a broken read must fail here, not pass everything below.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("role", sorted(ROLES))
def test_extractors_see_a_real_surface(role):
    """An empty tree or an empty path text makes the rules below vacuous."""
    tree = ROLES[role]["tree"]
    helpers = _helpers(tree)
    assert len(helpers) >= 5, f"{role}: only {[h.name for h in helpers]} found under {tree}"

    for name, text in _path_texts(role).items():
        assert len(_uncommented(text)) > 500, f"{role}/{name}: read as {len(text)} chars"
        deployed = [h.name for h in helpers if _deploys(text, tree, h)]
        assert deployed, f"{role}/{name} deploys none of {[h.name for h in helpers]}"


def test_both_deployment_spellings_are_exercised():
    """The matcher must recognise a per-file copy *and* a directory glob.

    If it only ever matched one of the two, the rules below would pass on half
    the paths for the wrong reason — a whole provisioning path reading as
    "deploys everything", or as "deploys nothing at all".
    """
    tree = ROLES["server"]["tree"]
    named = tree / "usr" / "local" / "bin" / "milo-apply-hardware"
    assert named.is_file(), "the file this check is anchored on has moved"

    assert _deploys('sudo cp "$X/rootfs/usr/local/bin/milo-apply-hardware" /usr/local/bin/', tree, named)
    assert _deploys("for s in $X/rootfs/usr/local/bin/*; do cp $s /usr/local/bin/; done", tree, named)
    assert not _deploys("# usr/local/bin/milo-apply-hardware is in a comment", tree, named)
    assert not _deploys("nothing to see here", tree, named)
    # The bare basename must NOT satisfy the rule — that is what made it vacuous
    # for a file whose name is also an ordinary word of the deploying script.
    assert not _deploys("milo-apply-hardware", tree, named)


# --------------------------------------------------------------------------- #
# The contract.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("role", sorted(ROLES))
def test_every_helper_is_deployed_by_every_path_into_that_role(role):
    """A helper one path installs and another does not is a unit that differs.

    The symptom is never an error at provisioning time — the file is simply not
    there, and the failure surfaces on the appliance at the moment something
    execs it: a 203/EXEC on an ExecStartPre, a sudo helper that is not found, a
    conversion that produces a satellite with no mDNS.
    """
    tree = ROLES[role]["tree"]
    missing = [
        f"{name} does not deploy {helper.relative_to(REPO_ROOT)}"
        for helper in _helpers(tree)
        for name, text in _path_texts(role).items()
        if not _deploys(text, tree, helper)
    ]
    assert not missing, (
        f"{role}: these helpers are not installed by every path into the role:\n"
        + "\n".join(sorted(missing))
    )


def test_every_helper_is_tracked_by_git():
    """An untracked helper installs on its author's unit and on nobody else's."""
    on_disk = {
        str(p.relative_to(REPO_ROOT))
        for role in ROLES
        for p in _helpers(ROLES[role]["tree"])
    }
    assert on_disk, "no helper found in either tree; the extractor is broken"

    tracked = set(
        subprocess.run(
            ["git", "ls-files", "rootfs", "milo-client/rootfs"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
    )
    assert not sorted(on_disk - tracked), (
        "these helpers deploy to the fleet but are not in git: "
        + ", ".join(sorted(on_disk - tracked))
    )
