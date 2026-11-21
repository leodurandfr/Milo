"""Structural guardrail: what the image build may abort on, and what it may swallow.

Milō is provisioned one way: a flash of the pi-gen image built by
`pi-gen/stage-milo/`, which sources the shared modules under `provisioning/` for the
work it does not restate. Every one of those files runs under `set -e` inside a
chroot, so an unguarded command that fails ends the build — and a command whose
failure is *silenced* ends nothing and ships an image missing a package.

Two rules:

  * **Every download names its pin.** A fetch built from a `*_VERSION` that
    `dependencies.env` declares *should* abort: a Milō without go-librespot is
    not a Milō, and failing loudly beats a half-provisioned unit. The one fetch
    that named nothing was Waveshare's Brightness.zip, taken from the vendor's
    site unverified and its `install.sh` run as root under `|| true`, so
    whatever that URL served the day of the build went into every image. It
    installed a Qt desktop slider the kiosk never shows, and the panel's
    brightness never needed it (the backend writes the sysfs backlight
    `99-milo-screen.rules` opens), so it is gone, and the inspection checks
    that the image no longer carries it. A file with no version to name would
    need its sha256 checked in the same statement, and an arm here that reads it.
  * **An `apt install` must not be silenced** (measured on 2026-09-02).
    `provisioning/common.sh` carried an `apt install … || true` for months: it
    named `libflac12t64` with a `libflac12` fallback, neither of which trixie
    has, so the call failed and took `libavahi-client3`/`libavahi-common3` down
    with it — and said nothing.
    The first CI image build is what surfaced it, in a log nobody reads when the
    build is green. A fallback onto a *different package name* is a different
    thing and stays allowed: `chromium || chromium-browser` is real cross-distro
    variance, and it is not silenced — if both arms fail, `set -e` ends the run,
    which is right.

The first rule has a second half, measured later: **a pinned download has no
unpinned fallback.** "Should abort" means nothing if a failed pinned fetch falls
through to installing the same package from the distribution. The snapcast block
did exactly that — its `.deb` fetches failing fell through to `apt-get install
snapserver snapclient`, trixie's 0.31.0 under a manifest that says 0.35.0, and the
build stayed green.

Nothing else catches any of these. These are shell files run in a chroot on a
builder CI reaches only through a three-hour job; there is no import to fail and
no route to 404, and every symptom is a green build that produced a wrong image.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
import shutil
import subprocess
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
    as unpinned, so the rule below would refuse a download that is pinned. That
    is a loud false positive, never a hidden defect, and no file does it: the two
    that did were the script installers' binary downloads.
    """
    return any(name in declared for name in VERSION_RE.findall(line))


def test_the_fetch_extractor_sees_the_pinned_downloads():
    """The rule below is vacuous if no fetch is extracted, and decorative if
    the pin test reads every URL as pinned.
    """
    declared = _declared_versions()
    pinned = [f for f in _fetches() if _is_pinned(f[2], declared)]
    assert len(pinned) >= 4, f"only {len(pinned)} pinned fetches recognised"

    # The extractor must see the guarded form the vendor fetch took, and the pin
    # test must tell a declared version from a URL that names none.
    assert FETCH_RE.search("if wget -q https://x/f.zip 2>/dev/null; then")
    assert not FETCH_RE.search('log_info "downloading with wget"')
    assert not _is_pinned("if wget -q https://files.waveshare.com/wiki/common/Brightness.zip; then", declared)
    assert not _is_pinned('wget -q "https://x/v${UNDECLARED_VERSION}/x.tgz"', declared)
    assert _is_pinned('wget -q "https://x/v${CAMILLADSP_VERSION}/x.tgz"', declared)


def test_every_download_in_the_image_build_names_its_pin():
    """What a fetch lands in the image is what its URL served that day, as root.

    Fails when a stage script or a module it sources downloads something whose
    statement names no version `dependencies.env` declares: nothing then says
    which file the image was built from, and two builds of one tag can differ.
    """
    declared = _declared_versions()
    unpinned = [
        f"{tree}: {path.relative_to(REPO_ROOT)}: {line.strip()[:90]}"
        for tree, path, line in _fetches()
        if not _is_pinned(line, declared)
    ]
    assert not unpinned, (
        "these downloads name no version dependencies.env declares, so the "
        "image installs whatever the URL serves on the day of the build:\n"
        + "\n".join(unpinned)
    )


INSPECTOR = REPO_ROOT / "pi-gen" / "inspect-image.sh"
SCREEN_CHECKS = (
    "no vendor brightness tool in /usr/share/applications",
    "the backlight is granted by 99-milo-screen.rules alone",
)


def _screen_verdicts(root: Path) -> dict[str, str]:
    """The inspector's verdict on each screen check, run over `root`."""
    result = subprocess.run(
        ["bash", str(INSPECTOR), str(root)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    verdicts = {}
    for line in plain.splitlines():
        words = line.split(maxsplit=1)
        if len(words) == 2 and words[1] in SCREEN_CHECKS:
            verdicts[words[1]] = words[0]
    assert set(verdicts) == set(SCREEN_CHECKS), f"the inspector printed {verdicts}: {plain[-1500:]!r}"
    return verdicts


def test_the_inspection_refuses_an_image_carrying_the_vendor_tool(tmp_path):
    """The image built without the vendor step passes, and one that still runs
    it fails: its slider in /usr/share/applications and its second backlight
    rule are what the old block left on every image. If this fails, a stage that
    brings the download back builds and publishes green.
    """
    rules = tmp_path / "etc" / "udev" / "rules.d"
    rules.mkdir(parents=True)
    for rule in (REPO_ROOT / "rootfs" / "etc" / "udev" / "rules.d").glob("*.rules"):
        shutil.copy(rule, rules / rule.name)
    applications = tmp_path / "usr" / "share" / "applications"
    applications.mkdir(parents=True)

    assert _screen_verdicts(tmp_path) == dict.fromkeys(SCREEN_CHECKS, "ok")

    (applications / "brightness64bit").write_bytes(b"\x7fELF")
    (rules / "99-backlight.rules").write_text(
        'SUBSYSTEM=="backlight", RUN+="/bin/chmod 0666 /sys/class/backlight/%k/brightness"\n'
    )
    assert _screen_verdicts(tmp_path) == dict.fromkeys(SCREEN_CHECKS, "FAIL")


# `apt-get -o Acquire::Retries=3 install`, the stage's own spelling, included.
APT_INSTALL_RE = re.compile(r"apt(?:-get)?\s+(?:-\S+\s+(?:\S+=\S+\s+)?)*install\b")


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
    assert len(found) >= 2, f"only {len(found)} apt install statements extracted"
    assert any("SYSTEM_APT_PACKAGES" in line for line in found), "the manifest's install was not read"
    # ...and the matcher must discriminate.
    assert re.search(r"\|\|\s*true\s*$", "apt install -y x || true")
    assert not re.search(r"\|\|\s*true\s*$", "apt install -y x || apt install -y y")


# The package a pinned URL delivers, read from its basename: `snapserver_0.35.0-1_…`,
# `go-librespot_linux_arm64…`, `camilladsp-linux-aarch64…` — the name stops at the
# `_`/`-` before the version variable or the platform.
PINNED_PACKAGE_RE = re.compile(
    r"/([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*?)[_-](?:linux|\$)"
)

# The system manifest's packages are installed by the image and by every update,
# one name per word, with no version: a pinned package listed there is the same
# fallback by another path.
SYSTEM_MANIFEST = REPO_ROOT / "system-manifest.env"


def _manifest_packages() -> list[str]:
    out = subprocess.run(
        ["bash", "-c", f'source "{SYSTEM_MANIFEST}"; printf "%s\\n" "${{SYSTEM_APT_PACKAGES[@]}}"'],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert len(out) >= 50, f"only {out} read from {SYSTEM_MANIFEST.name}"
    return out


def _pinned_packages(declared: set[str]) -> set[str]:
    names = {
        name
        for _tree, _path, line in _fetches()
        if _is_pinned(line, declared)
        for name in PINNED_PACKAGE_RE.findall(line)
    }
    assert len(names) >= 4 and {"snapserver", "snapclient"} <= names, (
        f"pinned package names extracted: {sorted(names)}"
    )
    return names


def _unpinned_install_of(line: str, packages: set[str], declared: set[str]) -> set[str]:
    """The pinned packages this statement's `apt install`s take from the distribution.

    Read per command, so `pinned || unpinned` on one line is still caught: an
    install that names a declared `*_VERSION` is the pinned `.deb` itself, one
    that names a pinned package bare installs whatever version the mirror has.
    """
    found: set[str] = set()
    for command in re.split(r"\|\||&&|[;|]", line):
        if APT_INSTALL_RE.search(command) and not _is_pinned(command, declared):
            found |= {arg.strip("\"'") for arg in command.split()} & packages
    return found


def test_a_pinned_dependency_has_no_unpinned_install_fallback():
    """A pinned fetch that fails must end the image build, not swap in Debian's.

    The image would ship a version `dependencies.env` never validated, on the
    server and on every satellite flashed from it, with a green build and a
    passing inspection.
    """
    declared = _declared_versions()
    packages = _pinned_packages(declared)

    # The rule must discriminate before it is trusted.
    assert _unpinned_install_of("apt-get install -y snapserver snapclient", packages, declared)
    assert not _unpinned_install_of(
        'apt-get install -y "./snapserver_${SNAPCAST_VERSION}-1_arm64_x.deb"', packages, declared
    )
    assert not _unpinned_install_of("apt-get install -y libportaudio2", packages, declared)
    assert _unpinned_install_of(
        'apt-get install -y "./snapserver_${SNAPCAST_VERSION}-1_arm64_x.deb" '
        "|| apt-get install -y snapserver",
        packages,
        declared,
    )

    fallbacks = [
        f"{tree}: {path.relative_to(REPO_ROOT)}: {line.strip()[:90]}"
        for tree, paths in FETCH_TREES.items()
        for path in paths
        for line in _statements(path)
        if _unpinned_install_of(line, packages, declared)
    ]
    fallbacks += [
        f"system-manifest.env: {name}" for name in sorted(set(_manifest_packages()) & packages)
    ]
    assert not fallbacks, (
        "these installs take a package dependencies.env pins from the "
        "distribution, at whatever version it carries; the pinned fetch must "
        "abort the build instead:\n" + "\n".join(fallbacks)
    )


# A Debian release spelled out in a build statement. The image's own is read from
# its /etc/os-release, so a literal can only be a default or a second attempt.
DEBIAN_RELEASE_RE = re.compile(r"\b(?:buster|bullseye|bookworm|trixie|forky|sid)\b")

# Another way to put a package on the image than the one `apt-get install ./x.deb`:
# `dpkg -i` then a repair, which satisfies a Depends it cannot meet by removing the
# package it just unpacked.
SECOND_METHOD_RE = re.compile(
    r"\bdpkg\s+(?:-i|--install)\b|--fix-broken\b|\bapt(?:-get)?\s+-f\s+install\b"
)

LATEST_RE = re.compile(r"releases/latest\b|/latest/download\b")

# What puts a pinned dependency's source or package on the image. `git clone` is
# here and not in FETCH_RE: an unpinned clone (the Milō checkout itself) must abort
# like a pinned fetch, so the guard rule above has nothing to say about it.
ACQUISITION_RE = re.compile(rf"{FETCH_RE.pattern}|\bgit\s+clone\b|{APT_INSTALL_RE.pattern}")


def _install_fallbacks(line: str, declared: set[str]) -> list[str]:
    """What in this statement lets a pinned install land something else."""
    found = []
    if DEBIAN_RELEASE_RE.search(line):
        found.append("names a Debian release instead of reading the image's")
    if SECOND_METHOD_RE.search(line):
        found.append("a second install method")
    if FETCH_RE.search(line) and LATEST_RE.search(line):
        found.append("fetches 'latest'")
    if _is_pinned(line, declared) and ACQUISITION_RE.search(line) and "||" in line:
        found.append("a pinned fetch or install with an alternative")
    return found


def test_a_pinned_dependency_installs_one_way_for_one_system():
    """A pinned install lands exactly its pin, built for this image, or the build fails.

    The snapcast block tried the image's codename, then bookworm, then fell back
    from `apt-get install` to `dpkg -i` and a repair — each arm one more way to
    ship a package the manifest never validated, for a system it does not run
    on, from a build that stayed green.
    """
    declared = _declared_versions()

    pinned_installs = [
        line
        for paths in FETCH_TREES.values()
        for path in paths
        for line in _statements(path)
        if _is_pinned(line, declared) and APT_INSTALL_RE.search(line)
    ]
    assert pinned_installs, "no pinned apt install extracted — the rule has nothing to check"

    # The rule must discriminate before it is trusted.
    assert _install_fallbacks('for V in "$C" "bookworm"; do', declared)
    assert _install_fallbacks("CODENAME=${CODENAME:-trixie}", declared)
    assert _install_fallbacks('dpkg -i "x.deb" && apt-get --fix-broken install -y', declared)
    assert _install_fallbacks(
        'apt-get install -y "./snapserver_${SNAPCAST_VERSION}.deb" || dpkg -i x.deb', declared
    )
    assert _install_fallbacks("wget https://github.com/a/b/releases/latest/download/x.tgz", declared)
    assert not _install_fallbacks(
        'apt-get install -y "./snapserver_${SNAPCAST_VERSION}-1_arm64_${CODENAME}.deb"', declared
    )
    assert not _install_fallbacks("apt-get install -y chromium || apt-get install -y chromium-browser", declared)
    # A pinned fetch or clone has no alternative either: another version, another
    # architecture, or the default branch is still not the pin for this image.
    assert _install_fallbacks(
        "wget https://x/v${GO_LIBRESPOT_VERSION}/go-librespot_linux_arm64.tar.gz "
        "|| wget https://x/v0.8.0/go-librespot_linux_arm64.tar.gz",
        declared,
    )
    assert _install_fallbacks(
        "wget https://x/snapserver_${SNAPCAST_VERSION}-1_arm64_${CODENAME}.deb "
        "|| wget https://x/snapserver_${SNAPCAST_VERSION}-1_armhf_${CODENAME}.deb",
        declared,
    )
    assert _install_fallbacks(
        "git clone --branch ${NQPTP_VERSION} --depth 1 https://github.com/mikebrady/nqptp.git "
        "|| git clone --depth 1 https://github.com/mikebrady/nqptp.git",
        declared,
    )
    assert not _install_fallbacks(
        "git clone --branch ${NQPTP_VERSION} --depth 1 https://github.com/mikebrady/nqptp.git", declared
    )

    fallbacks = [
        f"{tree}: {path.relative_to(REPO_ROOT)}: {reason}: {line.strip()[:80]}"
        for tree, paths in FETCH_TREES.items()
        for path in paths
        for line in _statements(path)
        for reason in _install_fallbacks(line, declared)
    ]
    assert not fallbacks, (
        "these statements let a pinned dependency install something other than "
        "its pin for this image; the build must fail instead:\n" + "\n".join(fallbacks)
    )
