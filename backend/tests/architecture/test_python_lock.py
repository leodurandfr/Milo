"""Structural guardrail: what a unit installs into its venvs is the lock, and the
lock is what the requirements ask for.

`requirements.txt` holds floors (`fastapi>=0.115.0`). Installed as they stand,
every image carried what PyPI served the day it was built, and a unit updated a
month later kept another set: what a release was tested with and what a unit
ran were two different trees, for Python as for the frontend before it was
built once in CI. So each app has a lock beside its floors — `requirements.lock`
for the backend, `milo-client/app/requirements.lock` for the satellite —
compiled by `uv pip compile --universal --generate-hashes` (the command is the
lock's own header), and every install reads the lock with `--require-hashes`:
the image build, the server's app update, a satellite's app push, and CI.

Nothing can compile the lock again offline, so this file holds what can be read:

  * the lock covers its requirements — every one pinned, at a version its floor
    accepts, on every machine Milō runs on;
  * nothing in it outlives its reason — a package the lock says a requirement
    pulled in is still a requirement (the drift is a line removed from
    requirements.txt and left pinned, installed on every unit for nobody);
  * every pin is exact and every file named by its sha256, or `--require-hashes`
    refuses the whole install, on the unit, at the moment of an update;
  * a package pip must build on install is named, with why: what builds it is
    the one thing the hashes do not cover;
  * the satellite's lock pins what it shares with the server's at the server's
    version, because one CI venv runs both suites;
  * CI tests with the Python the locks were compiled for;
  * every shell or workflow install reads a lock with `--require-hashes`. The
    Python call sites are behavior, held in test_update_service.py and the
    satellite's test_services_app_update.py.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
from pathlib import Path

import pytest
from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

REPO_ROOT = Path(__file__).resolve().parents[3]

APPS = {
    "backend": (REPO_ROOT / "requirements.txt", REPO_ROOT / "requirements.lock"),
    "satellite": (
        REPO_ROOT / "milo-client" / "app" / "requirements.txt",
        REPO_ROOT / "milo-client" / "app" / "requirements.lock",
    ),
}

# The machines a lock must hold on, as PEP 508 sees them: a unit (a Pi, 64-bit
# Raspberry Pi OS trixie), the dev VM (also Linux aarch64), CI (x86_64), and a
# developer's Mac. A requirement true on one of them must be pinned for it.
ENVIRONMENTS = {
    "unit": {"platform_machine": "aarch64", "sys_platform": "linux", "platform_system": "Linux"},
    "ci": {"platform_machine": "x86_64", "sys_platform": "linux", "platform_system": "Linux"},
    "mac": {"platform_machine": "arm64", "sys_platform": "darwin", "platform_system": "Darwin"},
}
PYTHON = {"python_version": "3.13", "python_full_version": "3.13.5",
          "implementation_name": "cpython", "platform_python_implementation": "CPython"}

PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)\s*(?:;\s*([^\\]+?))?\s*\\?$")
HASH_RE = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def _requirements(path: Path) -> dict[str, Requirement]:
    """The floors, by canonical name."""
    parsed = {}
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            req = Requirement(line)
            parsed[canonicalize_name(req.name)] = req
    assert len(parsed) >= 5, f"only {len(parsed)} requirements read from {path}"
    return parsed


def _lock(path: Path) -> dict[str, dict]:
    """{canonical name: {"version", "marker", "hashes", "via"}} out of uv's format:
    a pin at column 0, its hashes on continuation lines, then `# via` either on
    one line or as an indented list."""
    entries: dict[str, dict] = {}
    current = None
    in_via = False
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "#")):
            match = PIN_RE.match(line)
            assert match, f"{path.name}: not a pin uv writes: {line!r}"
            name, version, marker = match.groups()
            current = entries.setdefault(canonicalize_name(name), {
                "version": version, "marker": marker, "hashes": [], "via": [],
            })
            in_via = False
            continue
        if current is None:
            continue  # the header
        stripped = line.strip()
        if stripped.startswith("--hash="):
            current["hashes"].extend(HASH_RE.findall(stripped))
        elif stripped == "# via":
            in_via = True
        elif stripped.startswith("# via "):
            current["via"].append(stripped[len("# via "):].strip())
            in_via = False
        elif in_via and stripped.startswith("#   "):
            current["via"].append(stripped[len("#   "):].strip())
    assert len(entries) >= 10, f"only {len(entries)} pins read from {path}"
    return entries


def _applies(marker: str | None, env: dict) -> bool:
    return marker is None or Marker(marker).evaluate({**PYTHON, **env})


@pytest.mark.parametrize("app", sorted(APPS))
def test_the_lock_covers_every_requirement(app):
    """Fails when requirements.txt asks for a package the lock does not pin, at a
    version its floor refuses, or for a machine its marker leaves out — a floor
    raised, or a dependency added, without compiling the lock again. The unit
    would install the lock regardless, and run without what the code imports.
    """
    requirements_txt, lock_path = APPS[app]
    lock = _lock(lock_path)

    uncovered = []
    for name, req in _requirements(requirements_txt).items():
        entry = lock.get(name)
        if entry is None:
            uncovered.append(f"{req} is not in {lock_path.name}")
            continue
        if not req.specifier.contains(entry["version"], prereleases=True):
            uncovered.append(f"{req} but the lock pins {entry['version']}")
        for where, env in ENVIRONMENTS.items():
            wanted = req.marker is None or req.marker.evaluate({**PYTHON, **env})
            if wanted and not _applies(entry["marker"], env):
                uncovered.append(f"{req} on the {where}, which the lock's marker {entry['marker']!r} excludes")
    assert not uncovered, f"{lock_path.relative_to(REPO_ROOT)} is behind its requirements:\n" + "\n".join(uncovered)


@pytest.mark.parametrize("app", sorted(APPS))
def test_nothing_in_the_lock_outlives_its_reason(app):
    """Fails when the lock pins a package for a requirement requirements.txt no
    longer lists, or for no reason at all. That is the shape of pytest and ruff,
    which left the production requirements for requirements-dev.txt: left in a
    lock, every unit installs them for nobody, and the lock no longer says what
    the appliance needs.
    """
    requirements_txt, lock_path = APPS[app]
    requirements = _requirements(requirements_txt)
    lock = _lock(lock_path)

    stale = []
    for name, entry in lock.items():
        packages = {canonicalize_name(v) for v in entry["via"] if not v.startswith("-")}
        direct = any(v.startswith("-r ") for v in entry["via"])
        if direct and name not in requirements:
            stale.append(f"{name} is pinned for requirements.txt, which no longer asks for it")
        if not direct and not packages & set(lock):
            stale.append(f"{name} is pinned for {entry['via'] or 'nothing'}, none of which is in the lock")
    assert not stale, f"{lock_path.relative_to(REPO_ROOT)} must be compiled again:\n" + "\n".join(stale)


@pytest.mark.parametrize("app", sorted(APPS))
def test_every_pin_is_exact_and_every_file_hashed(app):
    """`--require-hashes` refuses an install whole when one package has no hash
    or is not pinned with `==`, and it does so on the unit, mid-update. A lock
    compiled without `--generate-hashes`, or edited by hand, fails here instead.
    """
    _, lock_path = APPS[app]
    lock = _lock(lock_path)
    unhashed = sorted(name for name, entry in lock.items() if not entry["hashes"])
    assert not unhashed, f"{lock_path.relative_to(REPO_ROOT)} pins without a hash: {unhashed}"
    header = lock_path.read_text().split("\n", 3)[:3]
    assert any("--generate-hashes" in line and "--universal" in line for line in header), (
        f"{lock_path.relative_to(REPO_ROOT)} was not compiled by `uv pip compile --universal "
        f"--generate-hashes`; its header reads {header}"
    )


# The packages pip builds on the unit, from their sdist. `--require-hashes`
# checks that sdist, and not what builds it: pip installs the build backend
# into an isolated environment from PyPI, unpinned, and never checks a hash
# there (a TODO in pip's own build-environment installer). So these are
# compiled by whatever setuptools PyPI serves that day, on the image build and
# on an update whose lock moved them alike. Each is accepted here by name, with
# why; a new one is a decision, never a side effect of compiling a lock again.
BUILT_ON_INSTALL = {
    "backend": {
        "evdev": "publishes no wheel: its ecodes module is generated at build time "
                 "from the linux/input.h of the machine that builds it",
        "lgpio": "no cp313 wheel; built against the liblgpio-dev and swig the image installs",
    },
    "satellite": {},
}


@pytest.mark.parametrize("app", sorted(APPS))
def test_every_package_built_on_install_is_named(app):
    """Fails when a lock compiled again names a new package by a single file,
    an sdist and nothing else — one more package compiled on the unit by a
    build backend nothing checks — or when a package named here has left the
    lock, so the exception would outlive its reason.

    What it cannot see: the lock names each file by its digest alone, never by
    its platform, so a package with an sdist and wheels for other machines
    only, none for the Pi's Python, has several hashes here and is built on the
    unit all the same. lgpio is that case, named by hand once found; another
    one shows the same way, as a "Building wheel for" line in the image build.
    """
    _, lock_path = APPS[app]
    lock = _lock(lock_path)
    single_file = {name for name, entry in lock.items() if len(entry["hashes"]) == 1}
    accepted = set(BUILT_ON_INSTALL[app])

    unnamed = sorted(single_file - accepted)
    assert not unnamed, (
        f"{lock_path.relative_to(REPO_ROOT)} names {unnamed} by a single file, so pip builds "
        f"them on install with a build backend it does not hash-check; name each in "
        f"BUILT_ON_INSTALL with why it is accepted, or pick a version that ships a wheel"
    )
    outlived = sorted(accepted - set(lock))
    assert not outlived, f"BUILT_ON_INSTALL[{app!r}] names {outlived}, which the lock no longer pins"


def test_the_satellite_lock_pins_the_server_versions():
    """CI runs the satellite's suite in the server's venv, so a package both
    apps use is tested at the server lock's version. Fails when the satellite's
    lock pins another — it was compiled without `-c ../../requirements.lock`, or
    against an older server lock — and a satellite would run a version no test
    ever ran.
    """
    server = _lock(APPS["backend"][1])
    satellite = _lock(APPS["satellite"][1])
    shared = set(server) & set(satellite)
    assert len(shared) >= 10, f"only {sorted(shared)} shared"
    apart = {
        name: (server[name]["version"], satellite[name]["version"])
        for name in sorted(shared)
        if server[name]["version"] != satellite[name]["version"]
    }
    assert not apart, f"server and satellite locks disagree (server, satellite): {apart}"
    missing = sorted(set(satellite) - set(server))
    assert not missing, f"the satellite lock pins what CI's venv never installs: {missing}"


def test_ci_tests_with_the_python_the_locks_were_compiled_for():
    """A lock compiled for one Python resolves markers and wheels for it alone.
    Fails when CI's interpreter and the locks' `--python-version` part: the
    suite would pass on a set a unit never installs.
    """
    workflow = (REPO_ROOT / ".github" / "workflows" / "lint.yml").read_text()
    ci = set(re.findall(r"python-version:\s*'([0-9.]+)'", workflow))
    assert ci, "no python-version read from lint.yml"
    for app, (_, lock_path) in APPS.items():
        header = lock_path.read_text().split("\n", 3)[1]
        compiled = re.search(r"--python-version\s+([0-9.]+)", header)
        assert compiled, f"{app}: the lock's header names no --python-version: {header!r}"
        assert ci == {compiled.group(1)}, f"CI runs Python {sorted(ci)}, the {app} lock is for {compiled.group(1)}"


# An install from a requirements file, in a shell script or a workflow step.
PIP_INSTALL_RE = re.compile(r"\bpip3?\s+install\b[^\n]*?\s-r\s+(\S+)")


def test_every_scripted_install_reads_a_lock_with_hashes():
    """The image build and CI install a venv through pip in shell, where no test
    runs them. Fails when one of them installs a requirements file other than a
    lock, or a lock without `--require-hashes` — CI's requirements-dev.txt alone
    excepted, which holds the test runner and the linter and never reaches a
    unit.
    """
    sources = [
        *(REPO_ROOT / "pi-gen" / "stage-milo").rglob("*run.sh"),
        *(REPO_ROOT / "provisioning").glob("*.sh"),
        REPO_ROOT / ".github" / "workflows" / "lint.yml",
    ]
    installs = [
        (path, line.strip())
        for path in sources
        for line in path.read_text().splitlines()
        if not line.lstrip().startswith("#") and PIP_INSTALL_RE.search(line)
    ]
    assert len(installs) >= 3, f"only {installs} pip installs from a file read"

    wrong = [
        f"{path.relative_to(REPO_ROOT)}: {line}"
        for path, line in installs
        for target in (t.strip("\"'") for t in PIP_INSTALL_RE.findall(line))
        if not target.endswith("requirements-dev.txt")
        and not (target.endswith("requirements.lock") and "--require-hashes" in line)
    ]
    assert not wrong, "these installs read no lock, or read one without --require-hashes:\n" + "\n".join(wrong)
