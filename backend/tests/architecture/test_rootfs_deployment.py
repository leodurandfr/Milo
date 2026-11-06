"""Structural guardrail: a script may not source a file its own tree omits.

Invariant #2 names **three** independent deployment trees. Two of them are
`rootfs/` trees and share one rule; the third, `pi-gen/stage-milo/`, has the same
invariant in a different shape and is covered at the bottom of this file.

`rootfs/` and `milo-client/rootfs/` are two independent deployment trees — both
are installed by `pi-gen/`, and the satellite's is refreshed on an already-paired
unit by `milo-client-deploy-update` unpacking a tarball that contains **only**
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

What reaches `/usr/local/` on the appliance is governed by
`test_helper_deployment.py` — a tree can carry a helper correctly while a
provisioning path quietly copies a subset of it.

Doctrine note (same as the Milo-Mac / milo-client contract tests): every
extractor asserts its own output is non-trivial first, so a broken parse fails
loudly instead of passing on an empty surface.
"""
import ast
import re
import shlex
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
PI_GEN_DIR_BUILD_SH = PI_GEN_DIR / "build.sh"

# `source "$(dirname "${BASH_SOURCE[0]}")/<rel>"` — a sibling of the stage script.
SIBLING_SOURCE_RE = re.compile(
    r"""^\s*(?:source|\.)\s+"\$\(dirname\s+"\$\{BASH_SOURCE\[0\]\}"\)/(\S+?)"\s*$""",
    re.MULTILINE,
)

# `source provisioning/foo.sh`, run after `cd /home/milo/milo` inside the chroot.
REPO_SOURCE_RE = re.compile(r"^\s*(?:source|\.)\s+(provisioning/\S+)\s*$", re.MULTILINE)

# A copy *into* the stage directory, whatever the builder calls its checkout:
# `cp <src> "${PIGEN_DIR}/stage-milo/<name>"`, `cp <src> pi-gen-build/stage-milo/<name>`.
# A recursive copy OF the stage (`cp -r pi-gen/stage-milo pi-gen-build/stage-milo`)
# has no name after the directory and correctly matches nothing.
STAGE_COPY_RE = re.compile(r'stage-milo/([A-Za-z0-9_.-]+)')

# Everything that populates a stage before pi-gen runs it. There are two, and
# only one of them was ever pinned: the GitHub workflow restates build.sh's
# configure step inline, and it went three months without the dependencies.env
# copy — a build that aborts on the first line of 01-install-audio, unnoticed
# because it only runs on a `v*` tag or a manual dispatch.
STAGE_BUILDERS = {
    "pi-gen/build.sh": PI_GEN_DIR_BUILD_SH,
    ".github/workflows/build-image.yml": REPO_ROOT / ".github" / "workflows" / "build-image.yml",
}


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

    for name, path in STAGE_BUILDERS.items():
        assert path.is_file(), f"{name} is missing"
        copied = STAGE_COPY_RE.findall(path.read_text())
        assert copied, f"no stage copy extracted from {name}"


@pytest.mark.parametrize("builder", sorted(STAGE_BUILDERS))
def test_every_sibling_a_stage_sources_is_placed_there_by_every_builder(builder):
    """A sibling a builder does not copy is a file the stage cannot reach.

    It is not in the repo at that path either — `pi-gen/stage-milo/` holds no
    `dependencies.env` — so a filesystem check would be wrong here. What has to
    hold is that the builder puts it there.

    Parametrised over *every* builder because that is precisely how this broke:
    `pi-gen/build.sh` copied it and the GitHub workflow, which restates the same
    configure step in YAML, did not.
    """
    copied = {c.lstrip("./") for c in STAGE_COPY_RE.findall(STAGE_BUILDERS[builder].read_text())}
    missing = []
    for script in _stage_scripts():
        for rel in SIBLING_SOURCE_RE.findall(script.read_text()):
            # Sibling paths are written relative to the script's own directory,
            # one level below the stage root (`../dependencies.env`).
            name = rel.split("/")[-1]
            if name in copied or (STAGE_DIR / rel.replace("../", "")).is_file():
                continue
            missing.append(
                f"{script.relative_to(REPO_ROOT)} sources {rel}, "
                f"which {builder} does not copy into the stage"
            )
    assert not missing, "\n".join(missing)


def test_every_repo_file_a_stage_sources_exists_in_this_repo():
    """The stage reads `provisioning/` out of the clone the image carries.

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


SERVER_SYSCTL_DIR = TREES["server"] / "etc" / "sysctl.d"
INSPECT_IMAGE_SH = PI_GEN_DIR / "inspect-image.sh"


def test_every_sysctl_file_the_server_tree_carries_is_flashed_and_inspected():
    """A kernel setting in rootfs/ reaches a freshly flashed card, and is checked there.

    A Milō update applies every `etc/sysctl.d/*` it installs, so a running unit
    gets a new file at its next update; a card flashed from the image gets only
    what the stage copies. A file the stage omits is a setting every new unit
    lacks until its first update — for the network files, a WiFi fallback that
    behaves differently on the one unit just set up. The server and the
    satellites are flashed from the same image.
    """
    files = sorted(p.name for p in SERVER_SYSCTL_DIR.glob("*.conf"))
    assert files, f"no sysctl file found under {SERVER_SYSCTL_DIR}"
    # A commented-out copy or check is no copy or check.
    code = lambda text: "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    stage, inspect = code(_stage_text()), code(INSPECT_IMAGE_SH.read_text())
    unflashed = [
        name for name in files
        if not re.search(rf"cp\s+\S*/rootfs/etc/sysctl\.d/{re.escape(name)}\s", stage)
    ]
    uninspected = [
        name for name in files
        if not re.search(rf"cmp -s '\$REPO/rootfs/etc/sysctl\.d/{re.escape(name)}'", inspect)
    ]
    assert not unflashed, f"pi-gen/stage-milo never copies: {unflashed}"
    assert not uninspected, f"pi-gen/inspect-image.sh never checks: {uninspected}"


# --------------------------------------------------------------------------- #
# BlueALSA's storage directory: declared by the build, created by the unit.
# --------------------------------------------------------------------------- #
#
# bluealsa saves per-device state under `<localstatedir>/lib/bluealsa`, a path
# compiled in by the stage's `configure`, and runs as `User=milo` from a unit
# Milō writes itself (`--disable-systemd` drops upstream's), so the directory
# exists only if that unit's `StateDirectory=` makes it. The two declarations
# live in different trees and nothing tied them: built with `--prefix=/usr` and
# no `--localstatedir`, the binary looked for `/usr/var/lib/bluealsa`, which no
# unit creates — a warning at every start and an ERROR "Couldn't save storage"
# at every disconnect.

BLUEALSA_STAGE_SCRIPT = STAGE_DIR / "01-install-audio" / "01-run.sh"
BLUEALSA_UNIT = REPO_ROOT / "system" / "milo-bluealsa.service"


def _bluealsa_configure_line() -> str:
    text = BLUEALSA_STAGE_SCRIPT.read_text().replace("\\\n", " ")
    block = text[text.index("bluez-alsa.git"):]
    return next(line for line in block.splitlines() if line.strip().startswith("../configure"))


def test_bluealsa_storage_directory_is_declared_by_its_unit():
    """The directory the binary writes to is the one systemd creates for it.

    Fails when the stage's configure line and `milo-bluealsa.service` name
    different directories: the daemon then loses every device's saved state and
    logs an error each time a phone leaves.
    """
    configure = _bluealsa_configure_line()
    assert "--prefix=/usr" in configure and "--with-bluealsauser=milo" in configure, (
        f"the bluez-alsa configure line was not extracted: {configure!r}"
    )
    options = dict(
        opt.split("=", 1) for opt in configure.split() if opt.startswith("--") and "=" in opt
    )
    # autoconf's default once --prefix is given.
    localstatedir = options.get("--localstatedir", options["--prefix"] + "/var")
    binary_dir = f"{localstatedir}/lib/bluealsa"

    state_dirs = re.findall(r"^StateDirectory=(\S+)\s*$", BLUEALSA_UNIT.read_text(), re.MULTILINE)
    unit_dirs = [f"/var/lib/{d}" for d in state_dirs]
    assert unit_dirs == [binary_dir], (
        f"bluealsa is built to store under {binary_dir}, but "
        f"{BLUEALSA_UNIT.relative_to(REPO_ROOT)} creates {unit_dirs or 'no StateDirectory'}"
    )


# --------------------------------------------------------------------------- #
# The kiosk's Chromium keeps its sandbox.
# --------------------------------------------------------------------------- #
#
# `milo-kiosk.service` is copied verbatim by `milo-deploy-update` and enabled by
# pi-gen. Its Chromium draws images picked by whoever publishes a podcast feed,
# straight from third-party CDNs. With `--no-sandbox` every renderer decoding
# them ran as uid milo with no seccomp filter, in the host's namespaces: a
# decoder exploit was then an ordinary milo process, reading the stored Spotify
# and Qobuz credentials and driving the local API. The flag was justified as
# "required for unprivileged user", which is false on the image: the setuid
# `chrome-sandbox` helper ships with Chromium and user namespaces are enabled.

KIOSK_UNIT = REPO_ROOT / "system" / "milo-kiosk.service"

# Every Chromium switch that turns a layer of the Linux sandbox off.
SANDBOX_OFF_SWITCHES = (
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-namespace-sandbox",
    "--disable-seccomp-filter-sandbox",
    "--disable-gpu-sandbox",
)

# A systemd line continuation: the trailing backslash and the newline it hides.
CONTINUATION_RE = re.compile(r"\\\n\s*")


def _kiosk_argv() -> list[str]:
    joined = CONTINUATION_RE.sub(" ", KIOSK_UNIT.read_text(encoding="utf-8"))
    for line in joined.splitlines():
        line = line.strip()
        if line.startswith("ExecStart="):
            return shlex.split(line.removeprefix("ExecStart="))
    return []


def test_kiosk_keeps_the_chromium_sandbox():
    """The kiosk's renderers run sandboxed.

    Fails when the kiosk unit passes Chromium a switch that turns its sandbox,
    or one of its layers, off. The consumer is systemd on the unit (cage +
    Chromium), through `milo-deploy-update` copying `system/*.service`.
    """
    argv = _kiosk_argv()
    assert "/usr/bin/chromium" in argv and "--app=http://localhost" in argv, (
        f"the kiosk's Chromium invocation was not extracted: {argv!r}"
    )
    assert [arg for arg in argv if arg in SANDBOX_OFF_SWITCHES] == []


# --- Mount helpers: "mounted" is read from the table, never by a stat --------

MOUNT_HELPERS = tuple(
    REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / name
    for name in ("milo-mount", "milo-umount")
)
MOUNT_TABLE_FUNC_RE = re.compile(r"^in_mount_table\(\) \{\n.*?^\}\n", re.MULTILINE | re.DOTALL)
# A command that answers "mounted?" by stat-ing the target: mountpoint(1), or
# findmnt pointed at a path (it canonicalizes, which is a stat).
STAT_MOUNT_TEST_RE = re.compile(
    r"\bmountpoint\s+(?:-\w+\s+)*[\"$]|\bfindmnt\b.*(?:--mountpoint|--target|\s-[MT]\b)"
)


def _mount_table_func(path: Path) -> str:
    match = MOUNT_TABLE_FUNC_RE.search(path.read_text(encoding="utf-8"))
    return match.group(0) if match else ""


def _code_lines(path: Path) -> list[str]:
    return [
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def test_mount_helpers_read_the_mount_table_not_the_target():
    """milo-mount and milo-umount decide "mounted" from /proc/self/mountinfo.

    Fails when either helper asks by a stat of the target again. On a CIFS share
    whose NAS is gone that stat fails after ~10 s, which mountpoint(1) reads as
    "not mounted": milo-umount then skipped the unmount and exited 0 (the backend
    logged "Unmounted share"), and milo-mount's mkdir -p died on it, so an edited
    share stayed down until a reboot. Consumer: the backend's StorageManager,
    through the milo-backend sudoers grants for both helpers.
    """
    funcs = {path.name: _mount_table_func(path) for path in MOUNT_HELPERS}
    for name, func in funcs.items():
        assert "/proc/self/mountinfo" in func, f"{name}: in_mount_table() not extracted"
    assert len(set(funcs.values())) == 1, "the two copies of in_mount_table() differ"

    for path in MOUNT_HELPERS:
        code = _code_lines(path)
        calls = [line for line in code if re.search(r"\bin_mount_table\s+\"", line)]
        assert calls, f"{path.name} never asks in_mount_table()"
        assert [line for line in code if STAT_MOUNT_TEST_RE.search(line)] == [], (
            f"{path.name} decides 'mounted' by a stat of the target"
        )


@pytest.mark.parametrize("target,listed", [
    ("/media/milo/My Music", True),
    ("/media/milo/My", False),
    ("/media/milo/a\\b", True),
    ("/media/milo/a\\134b", False),
    ("/media/milo/tab\there", True),
    ("/media/milo/elsewhere", False),
])
def test_the_mount_table_check_escapes_the_target_as_the_kernel_does(
    tmp_path, target, listed
):
    """The kernel lists a mountpoint with its space, tab, newline and backslash
    as octal; a check comparing the raw path would call "My Music" unmounted
    (milo-mount would then mkdir and mount over it) and a prefix of it mounted.
    Field 4, the root inside the filesystem, is never the mountpoint."""
    mountinfo = tmp_path / "mountinfo"
    mountinfo.write_text(
        "36 25 0:40 / /media/milo/My\\040Music ro - cifs //nas/m ro\n"
        "37 25 0:41 / /media/milo/a\\134b ro - cifs //nas/b ro\n"
        "38 25 0:42 / /media/milo/tab\\011here ro - vfat /dev/sda1 ro\n"
        "39 25 8:1 /media/milo/elsewhere /mnt/bind rw - ext4 /dev/sdb1 rw\n",
        encoding="utf-8",
    )
    func = _mount_table_func(MOUNT_HELPERS[1])
    assert func, "in_mount_table() not extracted from milo-umount"
    result = subprocess.run(
        ["bash", "-c", func + 'in_mount_table "$1" "$2"', "_", target, str(mountinfo)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == (0 if listed else 1), result.stderr


# --------------------------------------------------------------------------- #
# What an update installs, against what it says it installs.
# --------------------------------------------------------------------------- #

SERVER_DEPLOY_SCRIPT = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-deploy-update"
CLIENT_DEPLOY_SCRIPT = (
    REPO_ROOT / "milo-client" / "rootfs" / "usr" / "local" / "bin" / "milo-client-deploy-update"
)
CLIENT_APP_UPDATE = REPO_ROOT / "milo-client" / "app" / "services" / "app_update.py"

# One branch of a shell `case`: `pattern) action ;;` on a single line.
CASE_BRANCH_RE = re.compile(r"^[ \t]*([^\s()#][^)\n]*)\)[ \t]*(.*?)[ \t]*;;[ \t]*$", re.MULTILINE)


def _rootfs_case_branches() -> list[tuple[str, str]]:
    """The `case "$rel_path"` that decides, per rootfs file, what sync-system-files does."""
    text = SERVER_DEPLOY_SCRIPT.read_text(encoding="utf-8")
    loop = text[text.index('echo "Deploying rootfs files..."'):]
    block = loop[loop.index('case "$rel_path" in') + len('case "$rel_path" in'):]
    block = block[:block.index("esac")]
    return CASE_BRANCH_RE.findall(block)


def test_sync_system_files_skips_no_static_config():
    """Every file under rootfs/ reaches the unit on a Milō update.

    Fails when the rootfs loop of `milo-deploy-update` grows a branch that
    leaves a path out instead of installing it. The one there was
    `var/lib/milo/camilladsp/*`, skipped as "user config" although nothing
    writes that file at run time: a release that corrected CamillaDSP's
    `devices` reached a freshly flashed card and no running unit, and the same
    release moving a Loopback slot in asound.conf, which is synced, left the
    capture reading a slot nobody played into. Only two branches are admitted,
    the two files a plain copy cannot install.
    """
    branches = _rootfs_case_branches()
    actions = {action.split()[0] for _, action in branches}
    assert {"install_self", "install_sudoers"} <= actions, (
        f"the rootfs case was not read: {branches}"
    )
    skipped = [
        pattern for pattern, action in branches
        if action.split()[0] not in {"install_self", "install_sudoers"}
    ]
    assert not skipped, f"sync-system-files leaves these rootfs paths out: {skipped}"


def _satellite_synced_trees() -> set[str]:
    """`SYNCED_TREES`, the trees the satellite app installs itself, read from source."""
    tree = ast.parse(CLIENT_APP_UPDATE.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SYNCED_TREES" for t in node.targets
        ):
            return set(ast.literal_eval(node.value))
    return set()


def test_every_payload_directory_lands():
    """The satellite's payload fingerprint covers exactly what a push installs.

    The fingerprint decides whether a satellite is offered a push, and a push
    restarts a speaker in an occupied room. It covered the whole of
    `milo-client/` while the app installed `app/` and the deploy script
    `system/` and `rootfs/`: a commit touching only `configs/` lit the update
    on every satellite, restarted it, and marked it up to date with nothing
    installed. Fails both ways: a fingerprinted directory nothing installs, and
    an installed one a change to which would never be offered.
    """
    from backend.core.updates.satellite import PAYLOAD_PATHSPEC

    fingerprinted = {
        spec.removeprefix("milo-client/") for spec in PAYLOAD_PATHSPEC if not spec.startswith(":")
    }
    synced = _satellite_synced_trees()
    deployed = set(re.findall(
        r'"\$MILO_DIR/([A-Za-z0-9_-]+)', CLIENT_DEPLOY_SCRIPT.read_text(encoding="utf-8")
    ))
    assert "app" in synced, f"SYNCED_TREES was not read from {CLIENT_APP_UPDATE.name}: {synced}"
    assert {"system", "rootfs"} <= deployed, f"the deploy script was not read: {deployed}"
    assert len(fingerprinted) >= 3, f"PAYLOAD_PATHSPEC was not read: {PAYLOAD_PATHSPEC}"

    for name in fingerprinted:
        assert (REPO_ROOT / "milo-client" / name).is_dir(), f"milo-client/{name} does not exist"
    assert fingerprinted == synced | deployed, (
        f"fingerprinted but never installed: {sorted(fingerprinted - synced - deployed)}; "
        f"installed but never fingerprinted: {sorted((synced | deployed) - fingerprinted)}"
    )


# --------------------------------------------------------------------------- #
# The hardware helpers flush the boot partition before they hand back.
# --------------------------------------------------------------------------- #

APPLY_HARDWARE_SCRIPTS = (
    REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-apply-hardware",
    REPO_ROOT / "milo-client" / "rootfs" / "usr" / "local" / "bin" / "milo-client-apply-hardware",
)
SHELL_FUNC_RE = re.compile(r"^([a-z0-9_]+)\(\) \{\n(.*?)^\}\n", re.MULTILINE | re.DOTALL)
CONFIG_WRITE_RE = re.compile(r"(?:\bsed\s+-i\b|>>|\bcp\b).*\"\$CONFIG_FILE\"|\"\$CONFIG_FILE\".*>>")
# At column 0, so every path that reaches the line runs it (never one branch's).
# Both: syncfs writes the whole partition back but sends no cache flush on vfat,
# the fsync of the file does.
BOOT_SYNCS = (
    re.compile(r'^sync\s+-f\s+"\$CONFIG_FILE"\s*$'),
    re.compile(r'^sync\s+"\$CONFIG_FILE"\s*$'),
)
EXIT_RE = re.compile(r"^\s*(?:exit|return|exec)\b|^\s*(?:\S*/)?reboot\b|\bsystemctl\s+reboot\b")


def _config_writing_helpers(tree_root: Path) -> set[str]:
    """The hardware-helpers.sh functions that write config.txt, read from their body."""
    text = (tree_root / "usr" / "local" / "lib" / "milo" / "hardware-helpers.sh").read_text(
        encoding="utf-8"
    )
    return {
        name for name, body in SHELL_FUNC_RE.findall(text)
        if "CONFIG_FILE" in body and re.search(r"\bsed\s+-i\b|\bcp\b", body)
    }


@pytest.mark.parametrize("script", APPLY_HARDWARE_SCRIPTS, ids=lambda p: p.name)
def test_apply_hardware_syncs_the_boot_partition_before_it_returns(script):
    """Both apply helpers flush config.txt to disk before every way out.

    /boot/firmware is vfat, with no journal, and the backend makes
    `setup_completed` durable on ext4 the moment the helper returns, then
    reboots. With the pages of config.txt still in the cache, a power cut in the
    seconds before the shutdown unmounts the partition left a unit marked
    configured over the image's config.txt: no overlay, no sound, no wizard.
    Fails when a write to config.txt can reach an exit, a reboot or the end of
    the script with no `sync -f "$CONFIG_FILE"` and `sync "$CONFIG_FILE"` at
    top level after it. Consumers: milo-backend's setup wizard and Hardware page
    (server); the satellite's `POST /api/hardware/reboot` and its first-boot
    conversion (satellite).
    """
    tree_root = script.parents[3]
    writers = _config_writing_helpers(tree_root)
    assert {"remove_block", "backup_config", "disable_vc4_audio"} <= writers, (
        f"helpers not extracted: {writers}"
    )

    lines = _code_lines(script)
    call_re = re.compile(rf"^\s*(?:{'|'.join(sorted(writers))})\b")
    writes = [i for i, line in enumerate(lines) if CONFIG_WRITE_RE.search(line) or call_re.match(line)]
    assert len(writes) >= 3, f"{script.name}: config.txt writes not extracted: {writes}"

    syncs = [[i for i, line in enumerate(lines) if rx.match(line)] for rx in BOOT_SYNCS]
    exits = [i for i, line in enumerate(lines) if EXIT_RE.search(line) and i > writes[0]]
    for exit_at in [*exits, len(lines)]:
        last_write = max(i for i in writes if i < exit_at)
        for rx, found in zip(BOOT_SYNCS, syncs):
            assert any(last_write < s < exit_at for s in found), (
                f"{script.name}: line {lines[last_write].strip()!r} reaches "
                f"{lines[exit_at].strip() if exit_at < len(lines) else 'the end of the script'!r} "
                f"with no top-level line matching {rx.pattern!r} in between"
            )


# --------------------------------------------------------------------------- #
# No helper truncates a boot partition file in place.
# --------------------------------------------------------------------------- #

# A `>` (not `>>`, which the apply-hardware guardrail above owns) whose target
# is a boot partition file: an O_TRUNC on the inode the next boot reads.
BOOT_FILE_TRUNCATE_RE = re.compile(
    r'(?<![>&0-9])>\|?\s*"?(?:\$CMDLINE_FILE|\$CONFIG_FILE|\S*(?:cmdline|config)\.txt)"?(?:\s|$)'
)
CMDLINE_SYNCS = (
    re.compile(r'^sync\s+-f\s+"\$CMDLINE_FILE"\s*$'),
    re.compile(r'^sync\s+"\$CMDLINE_FILE"\s*$'),
)
CMDLINE_RENAME_RE = re.compile(r'^mv\s+-f\s+"\$\w+"\s+"\$CMDLINE_FILE"\s*$')


def test_no_helper_truncates_a_boot_partition_file_in_place():
    """cmdline.txt and config.txt are rewritten through a renamed, flushed sibling.

    /boot/firmware is vfat, with no journal. A helper that truncates one of
    those files in place and is cut there — by a power loss before writeback,
    or by a second run reading it empty in between — leaves a unit that does
    not boot (no `root=`) or boots without its overlay, recoverable only from
    another computer. Every script that writes cmdline.txt renames a sibling
    over it, then runs `sync -f "$CMDLINE_FILE"` and `sync "$CMDLINE_FILE"` at
    top level after the rename. Consumers: `milo-set-wifi-country` in both
    trees (the server's WiFi country, a satellite's admission).
    """
    scripts = [p for tree in TREES.values() for p in _shell_files(tree)]
    assert len(scripts) >= 20, f"shell scripts not extracted: {scripts}"

    truncations = [
        f"{p.relative_to(REPO_ROOT)}: {line.strip()}"
        for p in scripts for line in _code_lines(p)
        if BOOT_FILE_TRUNCATE_RE.search(line)
    ]
    assert not truncations, "in-place truncation of a boot file:\n" + "\n".join(truncations)

    writers = [p for p in scripts if any("CMDLINE_FILE" in line for line in _code_lines(p))]
    assert [p.name for p in writers].count("milo-set-wifi-country") == 2, (
        f"cmdline.txt writers not extracted: {writers}"
    )
    for path in writers:
        lines = _code_lines(path)
        renames = [i for i, line in enumerate(lines) if CMDLINE_RENAME_RE.match(line)]
        assert renames, f"{path.relative_to(REPO_ROOT)}: no rename over $CMDLINE_FILE"
        for rx in CMDLINE_SYNCS:
            assert any(rx.match(line) for line in lines[renames[-1] + 1:]), (
                f"{path.relative_to(REPO_ROOT)}: no top-level line matching {rx.pattern!r} "
                "after the rename"
            )


# --------------------------------------------------------------------------- #
# A helper run by sudo imports nothing from the caller's directory.
# --------------------------------------------------------------------------- #

SUDOERS_HELPER_RE = re.compile(r"^\S+\s+ALL=\(root\)\s+NOPASSWD:\s+(/usr/local/bin/\S+)", re.MULTILINE)
# The interpreter word and the short options that follow it, up to the first
# word that is not one.
PYTHON_CALL_RE = re.compile(r"\bpython3?(?:\.\d+)?\b((?:\s+-[A-Za-z]+)*)")
HARDWARE_HELPERS = {
    name: tree / "usr" / "local" / "lib" / "milo" / "hardware-helpers.sh" for name, tree in TREES.items()
}


def _python_flags(options: str) -> str:
    """The flag letters before the one that ends the options (`-c` / `-m`)."""
    flags = ""
    for word in options.split():
        letters = word[1:]
        cut = min((letters.index(c) for c in "cm" if c in letters), default=None)
        flags += letters[:cut]
        if cut is not None:
            break
    return flags


def _files_run_by_sudo() -> list[Path]:
    """Every NOPASSWD helper both policies grant, plus the files they source."""
    files: list[Path] = []
    for tree in TREES.values():
        for policy in sorted((tree / "etc" / "sudoers.d").iterdir()):
            for abs_path in SUDOERS_HELPER_RE.findall(policy.read_text(encoding="utf-8")):
                helper = tree / abs_path.lstrip("/")
                for path in [helper, *(tree / p.lstrip("/") for p in _sourced_paths(helper))]:
                    if path not in files:
                        files.append(path)
    return files


def test_helpers_run_by_sudo_import_nothing_from_the_callers_directory():
    """Every `python3` a root helper runs carries `-I`.

    sudo keeps the caller's working directory, and `python3 -c` puts that
    directory first on `sys.path`: a `json.py` left there by the `milo` or
    `milo-client` account runs as root on the next `import json`, before the
    helper has checked anything. On a satellite that walks around the
    argument-scoped sudoers policy. `-I` drops the current directory, the user
    site and `PYTHON*` from the environment, and changes neither argv nor stdin.
    Fails naming `file:line` for each invocation without it. Consumers: the
    server's setup wizard and Hardware page (`milo-apply-hardware`), the
    satellite's `POST /api/hardware/reboot` (`milo-client-apply-hardware`).
    """
    files = _files_run_by_sudo()
    assert set(HARDWARE_HELPERS.values()) <= set(files), f"sourced helpers not extracted: {files}"

    calls = [
        (path, number, match.group(1))
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if number > 1 and not line.lstrip().startswith("#")
        for match in PYTHON_CALL_RE.finditer(line)
    ]
    assert len(calls) >= 4, f"python invocations not extracted: {calls}"

    unisolated = [
        f"{path.relative_to(REPO_ROOT)}:{number}: python3{options}"
        for path, number, options in calls
        if "I" not in _python_flags(options)
    ]
    assert not unisolated, "python run by sudo without -I:\n" + "\n".join(unisolated)


@pytest.mark.parametrize("tree_name", sorted(HARDWARE_HELPERS))
def test_hardware_helpers_ignore_a_json_module_in_the_working_directory(tree_name, tmp_path):
    """`read_json` and `validate_hardware_json` load the stdlib json, whatever the cwd holds.

    The static check above trusts that `-I` is the flag that closes this; this
    one runs the helpers from a directory holding a planted `json.py`. Fails
    when that module runs.
    """
    marker = tmp_path / "planted-ran"
    (tmp_path / "json.py").write_text(f"open({str(marker)!r}, 'a').write('ran\\n')\n", encoding="utf-8")
    (tmp_path / "hardware.json").write_text('{"audio": {"overlay": "hifiberry-dac"}}', encoding="utf-8")
    (tmp_path / "config.txt").write_text("", encoding="utf-8")

    result = subprocess.run(
        [
            "bash", "-c",
            f'source {shlex.quote(str(HARDWARE_HELPERS[tree_name]))}; '
            'HARDWARE_FILE=hardware.json; CONFIG_FILE=config.txt; '
            'read_json audio.overlay; validate_hardware_json',
        ],
        cwd=tmp_path, capture_output=True, text=True, timeout=30, check=False,
    )

    assert not marker.exists(), f"the planted json.py ran: {marker.read_text()!r}"
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "hifiberry-dac"
