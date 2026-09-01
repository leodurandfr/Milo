"""Structural guardrail: an install chain must be self-contained in what it reads.

`install.sh` and `milo-client/install-client.sh` are two installers sharing one
helper module. Each sources a closure of shell files, and every one of them
reads variables, calls functions and copies files out of a `rootfs/` tree by
literal path. Nothing bound those three things to the chain doing the reading.

That is how 1.1 shipped. `install-client.sh` called `configure_journald`, whose
only definition — in the shared `install/common.sh` — copied
`"$MILO_APP_DIR/rootfs/etc/systemd/journald.conf.d/99-milo-journald.conf"`:

  * `MILO_APP_DIR` is assigned in 13 server modules and no satellite one, so the
    path began at `/`;
  * the file was absent from `milo-client/rootfs/` anyway — that tree had no
    `etc/systemd` at all;
  * `set -e` is line 10, and the call sat at step 4 of 15, so **no satellite
    could be installed by script** — the run aborted before the account, the
    venv, the units or the sudoers policy existed.

Nothing else catches this class. The chains are bash, sourced on a machine CI
never touches: there is no import to fail and no route to 404, `bash -n` parses
an undefined variable and a missing file happily, and
`test_rootfs_deployment.py` walks the two `rootfs/` trees rather than the
installers that populate them — its `SOURCE_RE` matches an absolute path at line
start, so the relative `source` every module here uses is invisible to it.

**`install/common.sh` is deliberately shared, and is pinned rather than
forbidden.** The satellite modules source it across the tree boundary on
purpose: README documents that the client installer "reuses the server's install
modules, so it needs the same full clone", the file's own header says so, and
each module's standalone-run block resolves the same relative path. Copying it
into `milo-client/` would answer a cross-tree read with a third twin file — the
drift class `test_twin_files_have_not_drifted` exists to punish. So the rule
below is that it is the *only* server file the satellite chain reaches, which is
what makes a second one visible.

Not checked here, because it is not statically decidable: **ordering**. 1.1's
third fault was `configure_journald` running before `clone_milo_client_repo`, so
`$MILO_CLIENT_ROOTFS_DIR` named a path the sparse checkout had not created yet.
Assignment and existence both hold at parse time; only the run order is wrong.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# The two installers, each named by the half it provisions.
CHAINS = {
    "server": REPO_ROOT / "install.sh",
    "satellite": REPO_ROOT / "milo-client" / "install-client.sh",
}

# The files the satellite chain may take from outside `milo-client/`. See the
# module docstring: shared on purpose, named so an unlisted one cannot join them.
#   common.sh        the shared helper the docstring is about.
#   dependencies.env the validated dependency set. Both chains install the same
#                    snapclient and the same CamillaDSP, so both must read the
#                    same versions; copying it into `milo-client/` would answer
#                    a cross-tree read with a twin file, which is the drift
#                    `test_twin_files_have_not_drifted` exists to punish.
SHARED_FILES = {
    REPO_ROOT / "install" / "common.sh",
    REPO_ROOT / "dependencies.env",
}

# The `rootfs/` tree each variable names, and the chain allowed to name it.
ROOTFS_TREES = {
    "MILO_APP_DIR": (REPO_ROOT / "rootfs", "server"),
    "MILO_CLIENT_ROOTFS_DIR": (REPO_ROOT / "milo-client" / "rootfs", "satellite"),
}

# `source X` / `. X` at command position. The `(?!=)` guard rejects the
# `source = alsa:///...` lines inside install/snapcast.sh's snapserver heredoc.
SOURCE_RE = re.compile(r"^\s*(?:source|\.)\s+(?!=)(\S.*?)\s*$", re.MULTILINE)

# `$(dirname "$0")` and `$(dirname "${BASH_SOURCE[0]}")` both name the directory
# of the script doing the sourcing — the spelling exists so every module is also
# runnable standalone.
DIRNAME_RE = re.compile(r'\$\(dirname\s+"?\$\{?(?:0|BASH_SOURCE\[0\]\}?)"?\)')

VAR_RE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
ASSIGN_RE = re.compile(r"^\s*(?:export\s+|local\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", re.MULTILINE)
FUNC_RE = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", re.MULTILINE)

# A shell function invoked as a bare statement, as in test_provisioning_parity.
CALL_RE = re.compile(r"^\s*([a-z_][a-z0-9_]*)(?:\s|$)", re.MULTILINE)

# `$MILO_APP_DIR/rootfs/<path>` or `$MILO_CLIENT_ROOTFS_DIR/<path>`, stopping at
# the first quote or space so a trailing `"/*` glob resolves to its directory.
ROOTFS_REF_RE = re.compile(
    r"\$\{?MILO_APP_DIR\}?/rootfs(/[^\s\"'$]*)"
    r"|\$\{?MILO_CLIENT_ROOTFS_DIR\}?(/[^\s\"'$]*)"
)


def _strip_comments(text: str) -> str:
    """Drop `#` comments without touching a `#` that is not one.

    Both other spellings appear here and a line-wide `split("#")` corrupts them:
    `${script#"$MILO_CLIENT_ROOTFS_DIR"/}` is a prefix-strip expansion, and
    `'\\033[0;31m'` style literals carry `#` inside quotes elsewhere. A comment
    opens only at line start or after whitespace, outside quotes.
    """
    out = []
    for line in text.splitlines():
        kept: list[str] = []
        quote = None
        for ch in line:
            if quote:
                kept.append(ch)
                if ch == quote:
                    quote = None
            elif ch in "\"'":
                quote = ch
                kept.append(ch)
            elif ch == "#" and (not kept or kept[-1].isspace()):
                break
            else:
                kept.append(ch)
        out.append("".join(kept))
    return "\n".join(out)


def _expand(raw: str, script: Path, assigns: dict[str, str]) -> str | None:
    """Resolve a `source` operand against the script's own assignments.

    Derived rather than restated: `INSTALL_DIR` and `SCRIPT_DIR` are read out of
    the file that defines them, so renaming one follows the code.
    """
    text = raw
    for _ in range(5):
        before = text
        text = DIRNAME_RE.sub(str(script.parent), text)
        text = VAR_RE.sub(lambda m: assigns.get(m.group(1), m.group(0)), text)
        if text == before:
            break
    text = text.replace('"', "").replace("'", "")
    return None if "$" in text else text


def _walk(entry: Path) -> tuple[dict[Path, str], list[str]]:
    """Every file an entry point sources, transitively, with its stripped text."""
    files: dict[Path, str] = {}
    problems: list[str] = []
    pending = [entry]
    while pending:
        script = pending.pop()
        if script in files:
            continue
        code = _strip_comments(script.read_text(errors="ignore"))
        files[script] = code
        assigns = {m.group(1): m.group(2).strip() for m in ASSIGN_RE.finditer(code)}
        for raw in SOURCE_RE.findall(code):
            resolved = _expand(raw, script, assigns)
            if resolved is None:
                problems.append(
                    f"{script.relative_to(REPO_ROOT)} sources {raw!r}, which the "
                    "extractor cannot resolve — every rule below goes blind on it"
                )
                continue
            target = Path(resolved).resolve()
            if not target.is_file():
                problems.append(
                    f"{script.relative_to(REPO_ROOT)} sources {raw!r} -> "
                    f"{target}, which does not exist"
                )
                continue
            pending.append(target)
    return files, problems


@pytest.fixture(scope="module")
def chains() -> dict[str, dict]:
    parsed = {}
    for name, entry in CHAINS.items():
        files, problems = _walk(entry)
        code = "\n".join(files.values())
        parsed[name] = {
            "files": files,
            "problems": problems,
            "defines": set(FUNC_RE.findall(code)),
            "assigns": {m.group(1) for m in ASSIGN_RE.finditer(code)},
        }
    return parsed


def test_extractor_sees_both_chains(chains):
    """A chain that reads as empty would make every rule below pass vacuously."""
    for name, chain in chains.items():
        assert len(chain["files"]) >= 8, f"{name}: only {len(chain['files'])} files walked"
        assert len(chain["defines"]) >= 20, f"{name}: only {len(chain['defines'])} functions parsed"
        assert any(v.startswith("MILO_") for v in chain["assigns"]), f"{name}: no MILO_* assignment"


def test_the_rule_has_something_to_catch(chains):
    """The two chains must differ, or `defined elsewhere` can never be reached.

    If every function resolved in both, `test_every_function_called_is_defined_in_the_same_chain`
    would be green whatever the installers do.
    """
    server_only = chains["server"]["defines"] - chains["satellite"]["defines"]
    assert len(server_only) >= 10, f"only {len(server_only)} server-only functions: {server_only}"


@pytest.mark.parametrize("chain_name", sorted(CHAINS))
def test_every_sourced_file_resolves_and_exists(chain_name, chains):
    """A `source` of a moved or absent module is a `set -e` abort mid-install.

    It reaches nobody before hardware: the operator sees the failing line, not a
    diagnosis, and the unit is left half-provisioned.
    """
    assert not chains[chain_name]["problems"], "\n".join(chains[chain_name]["problems"])


def test_the_satellite_chain_reuses_only_the_named_shared_files(chains):
    """Two files are shared on purpose; a third is not.

    A satellite module reaching further into `install/` inherits code written
    against `MILO_APP_DIR` and the server's `rootfs/` — 1.1's exact shape, and
    the reason the dependency is pinned to named files instead of banned.
    """
    outside = sorted(
        str(f.relative_to(REPO_ROOT))
        for f in chains["satellite"]["files"]
        if REPO_ROOT / "milo-client" not in f.parents and f not in SHARED_FILES
    )
    allowed = sorted(str(f.relative_to(REPO_ROOT)) for f in SHARED_FILES)
    assert not outside, (
        f"the satellite installer now sources server files beyond {allowed}: {outside}"
    )


@pytest.mark.parametrize("chain_name", sorted(CHAINS))
def test_every_function_called_is_defined_in_the_same_chain(chain_name, chains):
    """Calling a function the chain never sources is `command not found` + `set -e`."""
    known = chains["server"]["defines"] | chains["satellite"]["defines"]
    foreign = [
        f"{path.relative_to(REPO_ROOT)}:{i} calls {name}(), "
        f"defined only in the other install chain"
        for path, code in chains[chain_name]["files"].items()
        for i, line in enumerate(code.splitlines(), 1)
        for name in CALL_RE.findall(line)
        if name in known and name not in chains[chain_name]["defines"]
    ]
    assert not foreign, "\n".join(foreign)


@pytest.mark.parametrize("chain_name", sorted(CHAINS))
def test_every_milo_variable_read_is_assigned_in_the_same_chain(chain_name, chains):
    """An unset `MILO_*` expands to the empty string, silently — 1.1's first fault.

    `$MILO_APP_DIR/rootfs/x` became `/rootfs/x`: a plausible absolute path, no
    warning, and the failure surfaces as whatever the next command says about a
    file it cannot find.
    """
    chain = chains[chain_name]
    read = {
        name
        for code in chain["files"].values()
        for name in VAR_RE.findall(code)
        if name.startswith("MILO_")
    }
    unset = sorted(read - chain["assigns"])
    assert not unset, (
        f"the {chain_name} install chain reads {unset} but never assigns them"
    )


@pytest.mark.parametrize("chain_name", sorted(CHAINS))
def test_every_referenced_rootfs_file_is_shipped_by_that_tree(chain_name, chains):
    """Copying a file the tree omits is 1.1's second fault, and aborts the same way.

    `milo-client/rootfs/` had no `etc/systemd` at all when a satellite install
    was told to copy a journald drop-in out of it.
    """
    missing = []
    for path, code in chains[chain_name]["files"].items():
        for i, line in enumerate(code.splitlines(), 1):
            for match in ROOTFS_REF_RE.finditer(line):
                var = "MILO_APP_DIR" if match.group(1) else "MILO_CLIENT_ROOTFS_DIR"
                tree, _owner = ROOTFS_TREES[var]
                rel = (match.group(1) or match.group(2)).strip("/")
                if not (tree / rel).exists():
                    missing.append(
                        f"{path.relative_to(REPO_ROOT)}:{i} reads ${var}/{rel}, "
                        f"which {tree.relative_to(REPO_ROOT)} does not ship"
                    )
    assert not missing, "\n".join(missing)


@pytest.mark.parametrize("chain_name", sorted(CHAINS))
def test_a_chain_reads_only_its_own_rootfs_tree(chain_name, chains):
    """The shared helper must stay tree-neutral, or it breaks one caller in two.

    This is 1.1 stated at its origin: a `rootfs/` path written into
    `install/common.sh` resolves for the installer it was written for and for
    nobody else. The fix was to pass the file in — `configure_journald <path>` —
    so the helper names no tree at all.
    """
    foreign = [
        f"{path.relative_to(REPO_ROOT)}:{i} reads ${var}, which names the "
        f"{owner} tree while it is sourced by the {chain_name} installer"
        for path, code in chains[chain_name]["files"].items()
        for i, line in enumerate(code.splitlines(), 1)
        for match in ROOTFS_REF_RE.finditer(line)
        for var in ["MILO_APP_DIR" if match.group(1) else "MILO_CLIENT_ROOTFS_DIR"]
        for owner in [ROOTFS_TREES[var][1]]
        if owner != chain_name
    ]
    assert not foreign, "\n".join(foreign)


def test_every_chain_file_is_tracked_by_git(chains):
    """An untracked module works for its author and for nobody who clones.

    Both installers must be run from a full clone (README: "it must be run from
    a clone of the repository"), so a file git does not carry is a `source` that
    resolves here and dies on every real install — while the rule above, which
    reads the working tree, stays green.
    """
    walked = {
        str(f.relative_to(REPO_ROOT)) for chain in chains.values() for f in chain["files"]
    }
    tracked = set(
        subprocess.run(
            ["git", "ls-files", *sorted(walked)],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
    )
    untracked = sorted(walked - tracked)
    assert not untracked, "these install modules are not in git: " + ", ".join(untracked)


# --------------------------------------------------------------------------- #
# What a chain may abort on.
# --------------------------------------------------------------------------- #
#
# Every module here runs under `set -e`, so any unguarded command that fails ends
# the install. For a dependency that is *pinned* — a URL built from a
# `*_VERSION` that `dependencies.env` declares — that is right and is the
# doctrine: a Milō without go-librespot is not a Milō, and failing loudly beats
# a half-installed unit.
#
# An **unpinned** fetch is a different thing. There is exactly one, the Waveshare
# 8" DSI driver: a third-party vendor site, for a panel most units do not have,
# reached at step 159 of `install.sh`'s 166 — i.e. after everything is installed
# and *before* `enable_services`. Unguarded, a vendor outage left the whole stack
# on disk with nothing enabled, no `graphical.target` and a black screen; it also
# sat ahead of the backlight udev rule, so it took the 7" screen down with it.
# `pi-gen` already tolerated it and the installer did not — the drift class this
# directory exists for.

# A fetch in *command position*: at the start of a statement, or after a
# separator or a condition keyword. Anchored this way so `if wget …` counts —
# missing the guarded form is what makes the rule read as having nothing to
# check — while `log_info "downloading with wget"` does not.
FETCH_RE = re.compile(
    r"(?:^|\bif\s+|\belif\s+|\bwhile\s+|\buntil\s+|\bthen\s+|&&\s*|\|\|\s*|;\s*|\|\s*)"
    r"\s*(?:sudo\s+)?(?:wget|curl)\b"
)

VERSION_RE = re.compile(r"\$\{?([A-Za-z0-9_]+)\}?")

FETCH_TREES = {
    "install": [REPO_ROOT / "install.sh", *sorted((REPO_ROOT / "install").glob("*.sh"))],
    "pi-gen": sorted((REPO_ROOT / "pi-gen" / "stage-milo").rglob("*run.sh")),
}


def _declared_versions() -> set[str]:
    text = (REPO_ROOT / "dependencies.env").read_text()
    names = set(re.findall(r"^([A-Z0-9_]+_VERSION)=", text, re.MULTILINE))
    assert len(names) >= 5, f"dependencies.env yielded only {names}"
    return names


def _statements(path: Path) -> list[str]:
    """Logical statements: backslash continuations joined, comments dropped."""
    text = re.sub(r"\\\n\s*", " ", path.read_text(encoding="utf-8"))
    return [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]


def _fetches() -> list[tuple[str, Path, str]]:
    return [
        (tree, path, line)
        for tree, paths in FETCH_TREES.items()
        for path in paths
        for line in _statements(path)
        if FETCH_RE.search(line)
    ]


# `local version="$CAMILLADSP_VERSION"` — a fetch may reach the pinned value
# through a local alias rather than naming it, so the alias counts as pinned too.
ALIAS_RE = re.compile(r"^\s*(?:local\s+)?([a-zA-Z_][a-zA-Z0-9_]*)=\"?\$\{?([A-Z0-9_]+_VERSION)\}?", re.MULTILINE)


def _pinned_names(path: Path, declared: set[str]) -> set[str]:
    """Names that carry a pinned version in this file: the constants and their aliases."""
    text = path.read_text(encoding="utf-8")
    aliases = {alias for alias, source in ALIAS_RE.findall(text) if source in declared}
    return declared | aliases


def _is_pinned(line: str, names: set[str]) -> bool:
    return any(name in names for name in VERSION_RE.findall(line))


def _is_guarded(line: str) -> bool:
    """Can this statement fail without ending the chain?

    Either it is a condition (`if`/`while`/`until` — `set -e` is suspended
    there), or its failure is handled with `||`.
    """
    stripped = line.strip()
    return stripped.startswith(("if ", "elif ", "while ", "until ")) or "||" in stripped


def test_the_fetch_extractor_sees_both_kinds():
    """Both arms of the rule must have something to stand on.

    If every fetch read as pinned, the rule below would be vacuous; if none did,
    it would be wrong about the dependency downloads it deliberately exempts.
    """
    declared = _declared_versions()
    fetches = _fetches()
    assert len(fetches) >= 6, f"only {len(fetches)} wget/curl statements extracted"

    pinned = [f for f in fetches if _is_pinned(f[2], _pinned_names(f[1], declared))]
    unpinned = [f for f in fetches if not _is_pinned(f[2], _pinned_names(f[1], declared))]
    assert len(pinned) >= 4, f"only {len(pinned)} pinned fetches recognised"
    assert unpinned, "no unpinned fetch found — the rule below has nothing to check"

    # The alias arm must be exercised too, or a `local version="$X_VERSION"`
    # indirection would silently read as unpinned and the rule would demand a
    # guard on a download that should abort.
    common = REPO_ROOT / "install" / "common.sh"
    assert "version" in _pinned_names(common, declared) - declared, (
        "no local version alias resolved in install/common.sh; the alias arm is dead"
    )

    # ...and the guard test must discriminate, not rubber-stamp.
    assert _is_guarded("if wget -q http://x/f.zip; then")
    assert _is_guarded("wget http://x/f.zip || true")
    assert not _is_guarded("wget http://x/f.zip")


def test_an_unpinned_download_cannot_abort_an_install_chain():
    """A vendor outage must cost a feature, never the whole provisioning run.

    Pinned dependency downloads are exempt on purpose: those *should* abort.
    """
    declared = _declared_versions()
    unguarded = [
        f"{tree}: {path.relative_to(REPO_ROOT)}: {line.strip()[:90]}"
        for tree, path, line in _fetches()
        if not _is_pinned(line, _pinned_names(path, declared)) and not _is_guarded(line)
    ]
    assert not unguarded, (
        "these downloads are not pinned by dependencies.env and can end the "
        "install under `set -e`; guard them so the failure costs only the "
        "feature:\n" + "\n".join(unguarded)
    )


# `apt install … || true` turns a package that no longer exists into a silent
# no-op. `install/common.sh` carried one for months: it named `libflac12t64`
# with a `libflac12` fallback, neither of which trixie has, so the call failed
# and took `libavahi-client3`/`libavahi-common3` down with it — and said
# nothing. The first CI image build is what surfaced it, in a log nobody reads
# when the build is green.
#
# A fallback onto a *different package name* is a different thing and stays
# allowed: `chromium || chromium-browser` is real cross-distro variance, and it
# is not silenced — if both fail, `set -e` ends the run, which is right.

APT_INSTALL_RE = re.compile(r"apt(?:-get)?\s+install\b")


def test_no_apt_install_is_silenced():
    """A package that vanished upstream must end the run, not be swallowed."""
    silenced = []
    for tree, paths in FETCH_TREES.items():
        for path in paths:
            for line in _statements(path):
                if not APT_INSTALL_RE.search(line):
                    continue
                # Only the trailing `|| true` silences it; `|| apt install <other>`
                # is a fallback that still fails loudly when both arms fail.
                if re.search(r"\|\|\s*true\s*$", line.strip()):
                    silenced.append(f"{tree}: {path.relative_to(REPO_ROOT)}: {line.strip()[:90]}")
    assert not silenced, (
        "these apt installs are swallowed, so a package that no longer exists "
        "becomes a silent no-op:\n" + "\n".join(silenced)
    )


def test_the_apt_extractor_sees_the_installs():
    """The rule above is vacuous if no `apt install` is extracted at all."""
    found = [
        line
        for paths in FETCH_TREES.values()
        for path in paths
        for line in _statements(path)
        if APT_INSTALL_RE.search(line)
    ]
    assert len(found) >= 10, f"only {len(found)} apt install statements extracted"
    # ...and the matcher must discriminate.
    assert re.search(r"\|\|\s*true\s*$", "apt install -y x || true")
    assert not re.search(r"\|\|\s*true\s*$", "apt install -y x || apt install -y y")
