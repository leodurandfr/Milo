"""Structural guardrail: a script may not source a file its own tree omits.

Invariant #2 names **three** independent deployment trees. Two of them are
`rootfs/` trees and share one rule; the third, `pi-gen/stage-milo/`, has the same
invariant in a different shape and is covered at the bottom of this file.

`rootfs/` and `milo-client/rootfs/` are two independent deployment trees — the
server's is installed by `install/system.sh` and `pi-gen/`, the satellite's by
`milo-client/install/system.sh` and, for an already-paired unit, by
`milo-client-deploy-update` unpacking a tarball that contains **only**
`milo-client/`. So a file the satellite needs has to be inside the satellite's
tree; being present in the server's is worth nothing to it.

That is not hypothetical. `milo-client-apply-hardware` sources
`/usr/local/lib/milo/hardware-helpers.sh`, which lived only in `rootfs/`:

  * every script-installed satellite answered **500** on
    `POST /api/hardware/reboot`, so the reboot at the end of the pairing wizard
    never happened and the audio overlay it had just written never took effect;
  * the server only `logger.warning`s a non-200 reboot
    (`api/multiroom.py::_send_audio_config_and_reboot`), so the wizard reported
    success either way;
  * satellites flashed from the Milō image were fine — `pi-gen/` copies the
    helper — which is why one unit in a two-unit fleet worked and hid it.

Nothing else catches this class: the file is read by `source` at run time on a
machine CI never touches, there is no import to fail and no route to 404, and
`test_milo_client_contract.py` checks the HTTP surface, not what the satellite
carries on disk.

The installers that *populate* these trees are governed by its sibling,
`test_install_deployment.py`: the relative `source` every `install/` module uses
is invisible to the absolute-path rule here. What reaches `/usr/local/` on the
appliance is governed by `test_helper_deployment.py` — a tree can carry a helper
correctly while a provisioning path quietly copies a subset of it.

Doctrine note (same as the Milo-Mac / milo-client contract tests): every
extractor asserts its own output is non-trivial first, so a broken parse fails
loudly instead of passing on an empty surface.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# The two trees, each named by the install path that owns it.
TREES = {
    "server": REPO_ROOT / "rootfs",
    "milo-client": REPO_ROOT / "milo-client" / "rootfs",
}

# `source /abs/path` or `. /abs/path`, at the start of a line (so a mention
# inside a comment or a heredoc body does not count as a dependency).
SOURCE_RE = re.compile(r"^\s*(?:source|\.)\s+(/\S+)", re.MULTILINE)


def _shell_files(tree: Path) -> list[Path]:
    return sorted(p for p in tree.rglob("*") if p.is_file() and _is_shell(p))


def _is_shell(path: Path) -> bool:
    try:
        head = path.read_text(errors="ignore").splitlines()[:1]
    except (OSError, IndexError):
        return False
    return bool(head) and head[0].startswith("#!") and "sh" in head[0]


def _sourced_paths(path: Path) -> list[str]:
    return SOURCE_RE.findall(path.read_text(errors="ignore"))


@pytest.mark.parametrize("tree_name", sorted(TREES))
def test_extractor_sees_a_real_tree(tree_name):
    """A tree that reads as empty would make every rule below pass vacuously."""
    tree = TREES[tree_name]
    assert tree.is_dir(), f"{tree} is missing"
    scripts = _shell_files(tree)
    assert len(scripts) >= 3, f"only {len(scripts)} shell scripts found under {tree}"


def test_at_least_one_script_sources_something():
    """The rule below is vacuous if no `source` directive is ever extracted."""
    found = {
        f"{name}:{p.relative_to(tree)}": _sourced_paths(p)
        for name, tree in TREES.items()
        for p in _shell_files(tree)
        if _sourced_paths(p)
    }
    assert found, "no `source /abs/path` directive extracted from either rootfs tree"


@pytest.mark.parametrize("tree_name", sorted(TREES))
def test_sourced_files_are_shipped_by_the_same_tree(tree_name):
    """A script sourcing a file its own tree omits dies at run time, on hardware."""
    tree = TREES[tree_name]
    missing = []
    for script in _shell_files(tree):
        for abs_path in _sourced_paths(script):
            if (tree / abs_path.lstrip("/")).is_file():
                continue
            missing.append(
                f"{script.relative_to(REPO_ROOT)} sources {abs_path}, "
                f"which {tree.relative_to(REPO_ROOT)} does not ship"
            )
    assert not missing, "\n".join(missing)


@pytest.mark.parametrize("tree_name", sorted(TREES))
def test_every_rootfs_file_is_tracked_by_git(tree_name):
    """An untracked rootfs file ships to the fleet but not to a fresh clone.

    The satellite tarball is built from the **working tree**
    (`satellite.py::_create_client_tarball` tars `milo-client/`), so a file git
    ignores still reaches every satellite and the unit looks correct — while CI,
    a rebuild or the next developer gets a tree without it. `.gitignore`'s
    Python `lib/` rule swallowed `milo-client/rootfs/usr/local/lib/` for exactly
    that reason, and only the server's path carried a re-include.
    """
    tree = TREES[tree_name]
    on_disk = {str(p.relative_to(REPO_ROOT)) for p in tree.rglob("*") if p.is_file()}
    assert on_disk, f"{tree} reads as empty; extractor is broken"

    tracked = set(
        subprocess.run(
            ["git", "ls-files", str(tree.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
    )
    untracked = sorted(on_disk - tracked)
    assert not untracked, (
        "these files deploy to the fleet but are not in git (check .gitignore): "
        + ", ".join(untracked)
    )


def test_twin_files_have_not_drifted():
    """A file declaring itself a twin is one behaviour deployed from two trees.

    Sharing a path across the trees does **not** make two files twins —
    `90-milo-network` is deliberately different per role. The declaration is the
    `Twin of <path>` header a file carries; where it is present, the two copies
    must agree on everything but that line, and the other side must point back.
    Both twins today are audio-path scripts (`milo-alsa-passthrough`,
    `hardware-helpers.sh`), so a silent divergence is a satellite whose DSP or
    reboot behaves unlike the server's with nothing to say so.
    """
    declared = [
        (path, m.group(1))
        for tree in TREES.values()
        for path in tree.rglob("*")
        if path.is_file()
        for m in [re.search(r"^#\s*Twin of (\S+?)\s*(?:—|--|$)", path.read_text(errors="ignore"), re.MULTILINE)]
        if m
    ]
    assert len(declared) >= 2, f"only {len(declared)} `Twin of` headers found; extractor is broken"

    strip = lambda p: [ln for ln in p.read_text().splitlines() if "Twin of " not in ln]
    for path, target_rel in declared:
        target = REPO_ROOT / target_rel
        assert target.is_file(), f"{path.relative_to(REPO_ROOT)} names a twin that does not exist: {target_rel}"
        assert strip(path) == strip(target), (
            f"twin drift between {path.relative_to(REPO_ROOT)} and {target_rel} "
            "(ignoring the `Twin of` header line)"
        )
        assert "Twin of " in target.read_text(), f"{target_rel} does not declare itself a twin back"


# --------------------------------------------------------------------------- #
# The third tree: pi-gen/stage-milo/.
# --------------------------------------------------------------------------- #
#
# Invariant #8 calls it "a third independent deployment tree" for a precise
# reason: `pi-gen/build.sh` copies `stage-milo/` into a cloned pi-gen checkout —
# possibly into a Docker container — where it can no longer reach this repo. So a
# stage script that sources a *sibling* is reading a file only `build.sh` can put
# there, and the two were tied by nothing. That is why `dependencies.env`, the
# single declaration invariant #8 is about, is copied in beside the stage rather
# than referenced by a path into the tree.
#
# The rule therefore differs in shape from the two above: a sibling must be a
# file `build.sh` places in the stage, and a repo-relative source (the stage
# scripts `cd /home/milo/milo` first, into the clone the image already carries)
# must exist in this repo. A second sibling added without touching `build.sh` is
# a build that dies inside Docker, on the maintainer's machine, with no test to
# have said so first.

PI_GEN_DIR = REPO_ROOT / "pi-gen"
STAGE_DIR = PI_GEN_DIR / "stage-milo"

# `source "$(dirname "${BASH_SOURCE[0]}")/<rel>"` — a sibling of the stage script.
SIBLING_SOURCE_RE = re.compile(
    r"""^\s*(?:source|\.)\s+"\$\(dirname\s+"\$\{BASH_SOURCE\[0\]\}"\)/(\S+?)"\s*$""",
    re.MULTILINE,
)

# `source install/foo.sh`, run after `cd /home/milo/milo` inside the chroot.
REPO_SOURCE_RE = re.compile(r"^\s*(?:source|\.)\s+(install/\S+)\s*$", re.MULTILINE)

# What `build.sh` places inside the copied stage: `cp <src> "${PIGEN_DIR}/stage-milo/<name>"`.
STAGE_COPY_RE = re.compile(r'\$\{PIGEN_DIR\}/stage-milo/(\S+?)"')


def _stage_scripts() -> list[Path]:
    return sorted(STAGE_DIR.rglob("*.sh"))


def _stage_text() -> str:
    return "\n".join(p.read_text() for p in _stage_scripts())


def test_the_pi_gen_extractors_see_a_real_tree():
    """Every rule below is vacuous if either side reads as empty."""
    scripts = _stage_scripts()
    assert len(scripts) >= 4, f"only {len(scripts)} stage scripts found under {STAGE_DIR}"

    siblings = SIBLING_SOURCE_RE.findall(_stage_text())
    assert siblings, "no sibling `source` extracted from pi-gen/stage-milo"

    repo_sources = REPO_SOURCE_RE.findall(_stage_text())
    assert len(repo_sources) >= 5, f"only {repo_sources} repo-relative sources extracted"

    copied = STAGE_COPY_RE.findall((PI_GEN_DIR / "build.sh").read_text())
    assert copied, "no stage copy extracted from pi-gen/build.sh"


def test_every_sibling_a_stage_sources_is_placed_there_by_the_build():
    """A sibling `build.sh` does not copy is a file the stage cannot reach.

    It is not in the repo at that path either — `pi-gen/stage-milo/` holds no
    `dependencies.env` — so a filesystem check would be wrong here. What has to
    hold is that the build puts it there.
    """
    copied = {c.lstrip("./") for c in STAGE_COPY_RE.findall((PI_GEN_DIR / "build.sh").read_text())}
    missing = []
    for script in _stage_scripts():
        for rel in SIBLING_SOURCE_RE.findall(script.read_text()):
            # Sibling paths are written relative to the script's own directory,
            # one level below the stage root (`../dependencies.env`).
            name = rel.split("/")[-1]
            if name in copied or (STAGE_DIR / rel.replace("../", "")).is_file():
                continue
            missing.append(
                f"{script.relative_to(REPO_ROOT)} sources {rel}, which "
                "pi-gen/build.sh does not copy into the stage"
            )
    assert not missing, "\n".join(missing)


def test_every_repo_file_a_stage_sources_exists_in_this_repo():
    """The stage reads `install/` out of the clone the image carries.

    That clone is this repo at `MILO_BRANCH`, so a module renamed here is a stage
    that aborts under `bash -e` mid-build — after the frontend has been compiled.
    """
    missing = [
        f"{script.relative_to(REPO_ROOT)} sources {rel}, which this repo does not ship"
        for script in _stage_scripts()
        for rel in REPO_SOURCE_RE.findall(script.read_text())
        if not (REPO_ROOT / rel).is_file()
    ]
    assert not missing, "\n".join(missing)
