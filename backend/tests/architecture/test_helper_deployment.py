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
    """The stage scripts, plus the `provisioning/` modules they source.

    The stage deploys most files with its own glob loops, but not all: it
    sources `provisioning/ir-remote.sh` and calls `install_ir_helpers`, which is what
    puts `milo-ir-remote` in `/etc/sudoers.d`. Reading the stage alone reported
    that as undeployed — a miss the stage would have to restate to satisfy,
    which is the drift this directory exists to prevent. Only the modules the
    stage actually sources are included: pulling in all of `provisioning/` would make
    every file look deployed by pi-gen.
    """
    stages = sorted((REPO_ROOT / "pi-gen").rglob("*run.sh"))
    text = "\n".join(p.read_text() for p in stages)
    sourced = {
        REPO_ROOT / rel
        for rel in re.findall(r"^\s*(?:source|\.)\s+(provisioning/\S+)\s*$", text, re.MULTILINE)
    }
    assert sourced, "no provisioning/ module sourced from pi-gen; the extractor is broken"
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


_TREES = tuple(role["tree"] for role in ROLES.values())
_ROOTFS_VAR = re.compile(r"""\b(\w+)=(["']?)([^"'\s;]*rootfs)\2(?=[\s;]|$)""", re.MULTILINE)
_TWIN_HEADER = re.compile(r"^#\s*Twin of (\S+?)\s*(?:—|--|$)", re.MULTILINE)


def _expand_rootfs_vars(body: str) -> str:
    """Substitute every variable assigned a tree path (`client_rootfs="…/rootfs"`).

    `milo-first-boot` holds the client tree in a variable and loops over
    `"$client_rootfs"/usr/local/bin/*`; without the substitution its loop names
    no tree at all.
    """
    for name, _, value in _ROOTFS_VAR.findall(body):
        body = re.sub(rf"\"\$\{{?{name}\}}?\"|\$\{{?{name}\}}?(?!\w)", lambda _, v=value: v, body)
    return body


def _tree_anchor(tree: Path) -> str:
    """A regex for the tree's own prefix, which no other tree's prefix satisfies.

    `rootfs/` is also the tail of `milo-client/rootfs/`, so the server's anchor
    refuses to be preceded by what the longer prefix adds.
    """
    own = tree.relative_to(REPO_ROOT).as_posix()
    longer = (
        other.relative_to(REPO_ROOT).as_posix()
        for other in _TREES
        if other != tree and other.relative_to(REPO_ROOT).as_posix().endswith("/" + own)
    )
    refused = "".join(f"(?<!{re.escape(o[: -len(own)])})" for o in longer)
    return rf"(?<![\w.-]){refused}{re.escape(own)}[\"']?/"


def _deploys_directly(body: str, tree: Path, helper: Path) -> bool:
    anchor = _tree_anchor(tree)
    relative = helper.relative_to(tree).as_posix()
    if re.search(rf"{anchor}{re.escape(relative)}(?![\w.-])", body):
        return True
    directory = helper.parent.relative_to(tree).as_posix()
    return bool(re.search(rf"{anchor}{re.escape(directory)}/?\*", body))


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

    And the path is anchored on its own tree. `rootfs/` and `milo-client/rootfs/`
    share their layout, so an unanchored `usr/local/bin/*` let pi-gen's satellite
    loop prove every server helper deployed, and the reverse: deleting either
    loop left this guardrail green. A variable assigned a tree path is
    substituted first, which is how `milo-first-boot` spells its loops.

    One exception crosses the trees: a helper whose `Twin of` header names a file
    in the other tree counts as deployed where its twin is, by the same path —
    one hop, never followed further. The image copies `hardware-helpers.sh` from
    the server tree only, and both roles get it; `test_rootfs_deployment.py`
    holds twins identical but for that header, so the same bytes land.
    """
    body = _expand_rootfs_vars(_uncommented(text))
    if _deploys_directly(body, tree, helper):
        return True
    header = _TWIN_HEADER.search(helper.read_text(errors="ignore"))
    if not header:
        return False
    twin = REPO_ROOT / header.group(1)
    twin_tree = next((t for t in _TREES if t != tree and twin.is_relative_to(t)), None)
    return twin_tree is not None and _deploys_directly(body, twin_tree, twin)


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

    # The two trees share their layout, so a path or a loop must count only
    # for its own tree: pi-gen's satellite loop once proved every server helper
    # deployed, and the reverse.
    assert not _deploys("for s in /x/milo-client/rootfs/usr/local/bin/*; do cp $s /usr/local/bin/; done", tree, named)
    assert not _deploys("cp /x/milo-client/rootfs/usr/local/bin/milo-apply-hardware /usr/local/bin/", tree, named)
    assert not _deploys("cp /x/rootfs/usr/local/bin/milo-apply-hardware-old /usr/local/bin/", tree, named)

    client_tree = ROLES["satellite"]["tree"]
    client_named = client_tree / "usr" / "local" / "bin" / "milo-client-join-wifi"
    assert client_named.is_file(), "the file this check is anchored on has moved"
    assert not _deploys("for s in /x/rootfs/usr/local/bin/*; do cp $s /usr/local/bin/; done", client_tree, client_named)
    assert _deploys("for s in /x/milo-client/rootfs/usr/local/bin/*; do cp $s /usr/local/bin/; done", client_tree, client_named)
    # milo-first-boot's spelling: the tree held in a variable, the loop quoting it.
    assert _deploys(
        'local client_rootfs="$D/milo-client/rootfs"\n'
        'for h in "$client_rootfs"/usr/local/bin/*; do cp "$h" /usr/local/bin/; done',
        client_tree, client_named,
    )


_COPY_LOOP = re.compile(r"^\s*for\s+\w+\s+in\s+\S*?(\S*rootfs)/usr/local/bin/\*.*\n", re.MULTILINE)


@pytest.mark.parametrize("role", sorted(ROLES))
def test_deleting_one_roles_copy_loop_is_seen(role):
    """pi-gen's `/usr/local/bin` loop of one role is the only thing installing
    that role's helpers in the image; the other role's loop, writing to the
    same directory from a tree with the same layout, must not stand in for it.
    """
    tree = ROLES[role]["tree"]
    text = _pi_gen_text()
    own = str(tree.relative_to(REPO_ROOT))
    loops = [
        m for m in _COPY_LOOP.finditer(text)
        if m.group(1).endswith("/" + own) and not m.group(1).endswith("/milo-client/" + own)
    ]
    assert len(loops) == 1, f"{role}: expected one pi-gen copy loop from {own}/usr/local/bin, found {len(loops)}"
    without = text[:loops[0].start()] + text[loops[0].end():]

    bin_helpers = [h for h in _helpers(tree) if h.parent == tree / "usr" / "local" / "bin"]
    assert bin_helpers, f"{role}: no helper under usr/local/bin; the extractor is broken"
    assert all(_deploys(text, tree, h) for h in bin_helpers)
    assert not all(_deploys(without, tree, h) for h in bin_helpers), (
        f"{role}: pi-gen with its own copy loop deleted still reads as deploying every helper"
    )


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


def _shell_function(text: str, name: str) -> str:
    """The body of a top-level bash function, from its opening line to the
    first `}` in column 0."""
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}", text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def test_install_binary_never_writes_over_a_running_image():
    """`install-binary` swaps the binary of a unit that may be running.

    Writing onto the path in place fails with "Text file busy" while the old
    image executes (measured on kernel 6.18: `cp` rc=1), which turns an update
    into a rollback — or, when the unit was started after the update read its
    state, into a receiver stopped and never restarted. A rename never meets
    ETXTBSY: the running process keeps its inode. So the only line that may
    name the installed path is the `mv -f` from a `.new` beside it, the idiom
    `install_self` already uses in the same script.
    """
    helper = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-deploy-update"
    body = _shell_function(helper.read_text(), "cmd_install_binary")
    assert body and "validate_temp_path" in body, (
        "could not extract cmd_install_binary from milo-deploy-update; the extractor is broken"
    )

    on_dest = [
        line.strip() for line in _uncommented(body).splitlines()
        if re.search(r'"\$dest"', line)
        and not line.strip().startswith(("validate_", "[[", "case "))
    ]
    assert on_dest == ['mv -f "$dest.new" "$dest"'], (
        "cmd_install_binary writes the installed path other than by renaming "
        f"a .new beside it: {on_dest}"
    )
