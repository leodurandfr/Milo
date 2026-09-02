"""Structural guardrail: what the image build may abort on, and what it may swallow.

Milō is provisioned one way: a flash of the pi-gen image built by
`pi-gen/stage-milo/`, which sources the shared modules under `provisioning/` for the
work it does not restate. Every one of those files runs under `set -e` inside a
chroot, so an unguarded command that fails ends the build — and a command whose
failure is *silenced* ends nothing and ships an image missing a package.

Two rules, both from defects measured on 2026-09-02:

  * **An unpinned download must be guarded.** A fetch built from a `*_VERSION`
    that `dependencies.env` declares *should* abort: a Milō without go-librespot
    is not a Milō, and failing loudly beats a half-provisioned unit. There is
    exactly one unpinned fetch — the Waveshare 8" DSI brightness driver, from a
    third-party vendor site, for a panel most units do not have. Unguarded, a
    vendor outage costs the whole run instead of one optional feature.
  * **An `apt install` must not be silenced.** `provisioning/common.sh` carried an
    `apt install … || true` for months: it named `libflac12t64` with a
    `libflac12` fallback, neither of which trixie has, so the call failed and
    took `libavahi-client3`/`libavahi-common3` down with it — and said nothing.
    The first CI image build is what surfaced it, in a log nobody reads when the
    build is green. A fallback onto a *different package name* is a different
    thing and stays allowed: `chromium || chromium-browser` is real cross-distro
    variance, and it is not silenced — if both arms fail, `set -e` ends the run,
    which is right.

Nothing else catches either class. These are shell files run in a chroot on a
builder CI reaches only through a three-hour job; there is no import to fail and
no route to 404, and both symptoms are a green build that produced a wrong image.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# A fetch in *command position*: at the start of a statement, or after a
# separator or a condition keyword. Anchored this way so `if wget …` counts —
# missing the guarded form is what makes the rule read as having nothing to
# check — while `log_info "downloading with wget"` does not.
FETCH_RE = re.compile(
    r"(?:^|\bif\s+|\belif\s+|\bwhile\s+|\buntil\s+|\bthen\s+|&&\s*|\|\|\s*|;\s*|\|\s*)"
    r"\s*(?:sudo\s+)?(?:wget|curl)\b"
)

VERSION_RE = re.compile(r"\$\{?([A-Za-z0-9_]+)\}?")

# The two halves of the one provisioning path: the stage scripts, and the shared
# modules they source. Both are read, because a stage block and the module it
# sources are the same run and abort the same way.
FETCH_TREES = {
    "provisioning": sorted((REPO_ROOT / "provisioning").glob("*.sh")),
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


def _is_pinned(line: str, declared: set[str]) -> bool:
    """Does this fetch name a version `dependencies.env` declares?

    The constant has to appear in the statement itself. An indirection —
    `local v="$CAMILLADSP_VERSION"` a few lines above, then `.../$v/...` — reads
    as unpinned, so the rule below would demand a guard on a download that should
    abort. That is a loud false positive, never a hidden defect, and no file does
    it: the two that did were the script installers' binary downloads.
    """
    return any(name in declared for name in VERSION_RE.findall(line))


def _is_guarded(line: str) -> bool:
    """Can this statement fail without ending the run?

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

    # No total floor: the two counts below are what it would be the sum of, and a
    # broken FETCH_RE reads as zero pinned.
    pinned = [f for f in fetches if _is_pinned(f[2], declared)]
    unpinned = [f for f in fetches if not _is_pinned(f[2], declared)]
    assert len(pinned) >= 4, f"only {len(pinned)} pinned fetches recognised"
    assert unpinned, "no unpinned fetch found — the rule below has nothing to check"

    # The guard test must discriminate, not rubber-stamp.
    assert _is_guarded("if wget -q http://x/f.zip; then")
    assert _is_guarded("wget http://x/f.zip || true")
    assert not _is_guarded("wget http://x/f.zip")


def test_an_unpinned_download_cannot_abort_the_image_build():
    """A vendor outage must cost a feature, never the whole provisioning run.

    Pinned dependency downloads are exempt on purpose: those *should* abort.
    """
    declared = _declared_versions()
    unguarded = [
        f"{tree}: {path.relative_to(REPO_ROOT)}: {line.strip()[:90]}"
        for tree, path, line in _fetches()
        if not _is_pinned(line, declared) and not _is_guarded(line)
    ]
    assert not unguarded, (
        "these downloads are not pinned by dependencies.env and can end the "
        "image build under `set -e`; guard them so the failure costs only the "
        "feature:\n" + "\n".join(unguarded)
    )


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
    assert len(found) >= 5, f"only {len(found)} apt install statements extracted"
    # ...and the matcher must discriminate.
    assert re.search(r"\|\|\s*true\s*$", "apt install -y x || true")
    assert not re.search(r"\|\|\s*true\s*$", "apt install -y x || apt install -y y")
