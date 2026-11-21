"""Structural guardrail: what is offered is what installs, and it is built once.

Milō's own version used to travel a chain that lost track of itself at every
joint, and all of it was measurable on the appliance:

  * the offer came from `https://api.github.com/repos/{repo}/releases/latest`
    while the install ran `git pull origin main`, so what landed was main at the
    moment of the click — a tree nobody tagged, tested or published, different
    for every unit that pressed the button on a different day, and impossible to
    withhold because there was nothing to withhold;
  * the frontend was then rebuilt on the Pi with `npm install`, resolving the
    dependency ranges against whatever the registry offered that day, with
    whatever Node `n stable` had left on that unit — so no two units ran the same
    bytes, and a bad hour at the registry blocked the whole fleet from updating;
  * the image built a third `dist/` the same way, inside the pi-gen chroot.

The chain now has one artefact per release and one ref per install. Nothing in
CI can see a pi-gen build or an appliance, so these are the only checks that
stand between that and the three build paths growing back — each of them a bash
file or a YAML file no test suite otherwise reads.

Per this directory's doctrine, every extractor asserts its own output is
non-trivial before asserting anything about it: a guardrail that silently found
nothing to look at is worse than no guardrail.
"""
import ast
import hashlib
import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest
import yaml

from backend.core.updates.catalog import PROGRAMS
# The comment stripper is written once, next door, with the two spellings of `#`
# that a naive split corrupts already accounted for. A second copy here would be
# a second chance to get it wrong, in the direction that under-reports.
from backend.tests.architecture.test_dependency_manifest import _strip_comments

REPO_ROOT = Path(__file__).resolve().parents[3]
UPDATE_SERVICE = REPO_ROOT / "backend" / "core" / "updates" / "update.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-image.yml"
LINT_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "lint.yml"
STAGE_DIR = REPO_ROOT / "pi-gen" / "stage-milo"
LOCAL_BUILDER = REPO_ROOT / "pi-gen" / "build.sh"

# The one name the artefact is known by on the way into an image, chosen to
# carry no version: the stage installs whatever it is handed.
STAGE_TARBALL = "frontend-dist.tar.gz"


@pytest.fixture(scope="module")
def workflow():
    """The image workflow, parsed. Its jobs are the release channel's CI half."""
    doc = yaml.safe_load(WORKFLOW.read_text())
    jobs = doc.get("jobs", {})
    assert {"vars", "frontend", "build"} <= set(jobs), (
        f"the workflow no longer has the three jobs the channel is built from: {list(jobs)}"
    )
    return jobs


@pytest.fixture(scope="module")
def stage_scripts():
    """Every runnable script in the pi-gen stage."""
    scripts = sorted(STAGE_DIR.rglob("*.sh"))
    assert len(scripts) >= 6, f"the stage has almost no scripts left: {scripts}"
    return scripts


def _steps(job) -> str:
    """One job's steps, flattened to text — `run:` bodies included."""
    return yaml.safe_dump(job.get("steps", []))


def test_the_catalog_names_a_release_asset_and_no_branch():
    """A branch in the install layout is the old chain by another name.

    `git_branch: "main"` was the key `_update_milo_app` pulled from. What
    replaces it is the frontend asset URL — the artefact a release publishes —
    and it is keyed by the release *tag*, because the tag is what gets checked
    out and a bare version is not a ref.
    """
    milo = PROGRAMS["milo"]

    assert "git_branch" not in milo, "the install must name a release, never a branch"
    assert "{tag}" in milo["frontend_asset_url"], milo["frontend_asset_url"]
    assert milo["frontend_asset_url"].startswith(
        f"https://github.com/{milo['repo']}/releases/download/"
    ), milo["frontend_asset_url"]


# The programs a frontend build or install is launched with, by basename.
_FRONTEND_BUILDERS = {"npm", "npx", "pnpm", "yarn", "corepack", "vite"}


def _frontend_build_sites(source: str) -> tuple[int, list[str]]:
    """The `_run_local` calls naming their program literally, and every string
    in the code that names a frontend builder.

    Every string literal is read, not only an argv's head: the program can sit
    behind `sudo -u milo`, inside a `bash -c` line, or in a constant the call
    reads, and quoting and line breaks are gone once parsed. Docstrings are
    prose about the old chain and are skipped.
    """
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and ast.get_docstring(node, clean=False) is not None
    }
    literal_launches = 0
    sites = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, (ast.Attribute, ast.Name))
                and getattr(node.func, "attr", getattr(node.func, "id", None)) == "_run_local"
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            literal_launches += 1
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings
                and any(Path(word).name in _FRONTEND_BUILDERS
                        for word in re.findall(r"[\w./-]+", node.value))):
            sites.append(f"line {node.lineno}: {node.value!r}")
    return literal_launches, sites


def test_the_update_service_neither_pulls_a_branch_nor_builds_a_frontend():
    """The two halves of the old chain, read straight out of the source.

    A behaviour test covers the install that runs (test_update_service.py); this
    covers the one that does not — a helper reintroduced beside it, reachable
    from some other path, would pass every behaviour test in the suite.
    """
    source = UPDATE_SERVICE.read_text()
    assert "_update_milo_app" in source, "reading the wrong file"

    assert not re.search(r'"pull"', source), "the milo install pulls a branch again"
    literal_launches, build_sites = _frontend_build_sites(source)
    assert literal_launches >= 1, "no `_run_local` names its program: the walk reads nothing"
    assert not build_sites, f"the unit builds a frontend again: {build_sites}"
    # The ref it checks out has to come from the offer, not from a constant.
    assert 'status["latest"]["tag_name"]' in source, \
        "the install no longer takes its ref from the release it was offered"


@pytest.mark.parametrize("body", [
    "ok, out = await self._run_local('npm', 'ci', timeout=600)",
    'ok, out = await self._run_local(\n'
    '        "npm",\n'
    '        "ci",\n'
    '    )',
    "await self._run_local('sudo', '-u', 'milo', 'npm', 'ci')",
    "await self._run_local('bash', '-c', 'npm ci && npm run build')",
    "await self._run_local('rm', '-rf', x)\n"
    "    await asyncio.create_subprocess_exec('npm', 'install')",
    "await self._run_local('rm', '-rf', x)\n"
    "    argv = ['/usr/bin/npx', 'vite', 'build']",
    "await self._run_local('rm', '-rf', x)\n"
    "    NPM = shutil.which('npm')",
], ids=["one-line", "split-call", "behind-sudo", "shell-line", "subprocess-exec",
        "argv-list", "constant"])
def test_the_npm_check_goes_red_on_a_one_line_call(body):
    """Each way update.py launches a program, with a frontend builder in it.

    The check above used to see npm only at the start of a line, which is not
    how `_run_local` is called: an `npm ci` reintroduced in the rollback, one
    line long, would have the unit rebuild a frontend on every rollback while
    this file stayed green.
    """
    source = f"async def rollback(self, x):\n    {body}\n"

    literal_launches, sites = _frontend_build_sites(source)

    assert literal_launches == 1
    assert sites, f"a frontend build went unseen in:\n{source}"


def test_the_npm_check_flags_no_other_program():
    """A check that refused every program would be red on everything, docstrings
    about the old chain included."""
    source = 'async def f(self, x):\n    """Once ran `npm install`."""\n' \
        '    await self._run_local("rm", "-rf", x, "node_modules")\n'

    assert _frontend_build_sites(source) == (1, [])


def test_the_workflow_publishes_the_asset_the_backend_downloads(workflow):
    """One spelling of the asset name, on both sides of a network call.

    The backend builds the URL from the catalog template; the workflow names the
    file it uploads. Nothing but this test connects them, and a mismatch is not a
    build failure — it is every unit's update failing at the download, after the
    release was published and announced.
    """
    assert workflow["vars"]["outputs"]["frontend_asset"], "the vars job publishes no name"

    # Read out of the file rather than out of the parsed dump: the shell body is
    # a YAML scalar, and re-dumping it re-escapes the quoting around the literal.
    literal = re.search(r'frontend_asset=(\S+)" >> "\$GITHUB_OUTPUT"', WORKFLOW.read_text())
    assert literal, "the vars job stopped writing the asset name to its output"

    # The shell expansion of the ref reduced to the placeholder the catalog uses.
    workflow_name = literal.group(1).replace("${REF}", "{tag}")
    catalog_name = PROGRAMS["milo"]["frontend_asset_url"].rsplit("/", 1)[-1]

    assert workflow_name == catalog_name, (
        f"the workflow publishes {workflow_name!r} and the backend downloads "
        f"{catalog_name!r}"
    )


def test_the_release_carries_the_asset_and_its_checksum(workflow):
    """A truncated download extracts into a tree that is *almost* a frontend,
    and nginx serves it. The sidecar is what the install verifies against, so a
    release published without one cannot be installed at all.
    """
    release_steps = [
        step for step in workflow["build"]["steps"]
        if "action-gh-release" in str(step.get("uses", ""))
    ]
    assert len(release_steps) == 1, f"expected one release step, found {release_steps}"

    files = release_steps[0]["with"]["files"]
    assert "frontend_asset }}\n" in files or "frontend_asset }}" in files, files
    assert ".sha256" in files, f"the checksum sidecar is not published: {files}"


def test_a_suffixed_tag_is_published_as_a_prerelease(workflow):
    """The half of the pre-release guarantee that lives in CI.

    `releases/latest` — the endpoint the update flow offers from — returns the
    most recent release that is *not* marked pre-release. So marking it is what
    keeps a release candidate off every appliance in the field; publishing an
    rc as an ordinary release would hand it to the whole fleet the moment the
    build went green.

    The backend refuses a non-plain tag on its own side too
    (`helpers.is_stable_release`, covered in test_version_service.py). One
    guarantee spelled twice on purpose: each half still holds when the other is
    changed by accident, and neither is expensive.
    """
    published = _steps(workflow["vars"])
    assert "is_prerelease=" in published, "the vars job no longer decides it"

    # The shape it keys on: a tag carrying a suffix — v0.2.0-rc1, v0.3.0-beta.2.
    assert '"${REF}" == *-*' in WORKFLOW.read_text(), (
        "the pre-release test no longer keys on the tag's suffix"
    )
    assert 'is_prerelease=true' in published and 'is_prerelease=false' in published

    release_step = next(
        step for step in workflow["build"]["steps"]
        if "action-gh-release" in str(step.get("uses", ""))
    )
    assert "is_prerelease" in str(release_step["with"].get("prerelease", "")), (
        "the release is published without the pre-release flag the vars job computed"
    )


def test_the_publishing_job_asks_for_the_write_it_needs(workflow):
    """Without it the whole channel is a three-hour build that publishes nothing.

    A job declaring no `permissions:` block inherits the repository default, and
    this repository's is read-only — proven by the one job that does declare a
    block (`milo-mac-freshness`, `issues: write`) being the only one that has
    ever written anything. The v0.2.0 build measured the other side of it: image
    built, inspection passed, `Create GitHub Release` 403, nothing published.

    It could not have been caught before it happened. This step only runs on a
    `v*` tag, and the previous tag was never built — the same "a build that only
    runs on a tag is a build nobody runs" this workflow already carries a comment
    about, one step further down.
    """
    permissions = workflow["build"].get("permissions")
    assert permissions, (
        "the publishing job declares no permissions, so it inherits the "
        "repository default and cannot create a release"
    )
    assert permissions.get("contents") == "write", permissions


def test_the_frontend_is_built_once_in_ci_from_the_lockfile(workflow):
    """`npm ci` installs the lockfile; `npm install` re-resolves the ranges in
    package.json. That is the whole difference between "the tree this release
    was tested with" and "whatever was current the day the job ran".
    """
    steps = _steps(workflow["frontend"])
    assert "npm ci" in steps, "the frontend job no longer installs the lockfile"
    assert "npm install" not in steps, "the frontend job re-resolves its dependencies"
    assert "sha256sum" in steps, "the artefact is published without a checksum"

    # And the image consumes that artefact rather than making its own.
    assert "frontend" in workflow["build"]["needs"], workflow["build"]["needs"]
    assert "download-artifact" in _steps(workflow["build"])


def test_a_release_is_built_only_from_a_tree_the_ci_checks_passed_on(workflow):
    """A unit judges a release only once it has installed it; the CI suite is
    the one check a release meets before any unit is offered it. If this fails,
    a tag cut on a commit CI fails is built and published.
    """
    lint = yaml.safe_load(LINT_WORKFLOW.read_text())
    triggers = lint.get("on", lint.get(True))  # PyYAML reads the bare key `on` as True
    assert "workflow_call" in triggers, "lint.yml cannot be called by another workflow"
    assert {"backend", "frontend"} <= set(lint["jobs"]), list(lint["jobs"])

    callers = [name for name, job in workflow.items()
               if job.get("uses") == "./.github/workflows/lint.yml"]
    assert callers, "build-image.yml never runs the CI checks"
    assert set(callers) <= set(workflow["build"]["needs"]), workflow["build"]["needs"]
    for name in callers:
        assert workflow[name]["with"]["ref"] == "${{ needs.vars.outputs.milo_ref }}", name
    # The checks run on the tree being built, not on the commit that triggered
    # the run: the two differ on a manual dispatch of another ref.
    for job in ("backend", "frontend"):
        checkout = next(
            step for step in lint["jobs"][job]["steps"]
            if step.get("uses", "").startswith("actions/checkout")
        )
        assert checkout.get("with", {}).get("ref") == "${{ inputs.ref }}", job


def test_the_image_stage_installs_the_frontend_instead_of_building_it(stage_scripts):
    """The third build path. It lived in the pi-gen chroot, where neither CI nor
    the appliance could see it, and it is the one that decided what a user
    flashes.
    """
    offenders = [
        script.relative_to(REPO_ROOT)
        for script in stage_scripts
        if re.search(r"\bnpm\b", _strip_comments(script.read_text()))
    ]
    assert not offenders, f"the image builds a frontend again: {offenders}"

    installer = STAGE_DIR / "02-install-milo" / "00-run.sh"
    body = installer.read_text()
    assert STAGE_TARBALL in body, f"{installer.name} no longer installs the artefact"
    assert "dist/index.html" in body, \
        f"{installer.name} does not check that what it extracted is a frontend"


def test_the_image_clone_can_still_reach_a_later_release(stage_scripts):
    """`git clone --branch <tag> --single-branch` leaves the refspec
    `+refs/tags/<tag>:refs/tags/<tag>` — measured. A unit flashed from a release
    image can then fetch nothing but the tag it was built from, and every later
    release is unreachable from the update button, for the life of the unit.
    """
    body = (STAGE_DIR / "02-install-milo" / "00-run.sh").read_text()
    assert "git clone" in body, "reading the wrong file"

    clone = re.search(r"git clone[^\n]*", body).group(0)
    assert "--single-branch" not in clone, clone
    assert "--branch" not in clone, clone
    assert "checkout --force" in body, \
        "the stage clones without then checking out the ref it was asked for"


def test_both_image_builders_hand_the_stage_the_same_file(stage_scripts):
    """`stage-milo/` is a third deployment tree in invariant 2's sense: it is
    built from a copy inside a cloned pi-gen checkout, often in Docker, which
    cannot reach this repo. Whatever the stage reads as a sibling has to be put
    there by whoever launched the build — and there are two of them.
    """
    local = LOCAL_BUILDER.read_text()
    workflow_text = WORKFLOW.read_text()
    assert "stage-milo" in local and "stage-milo" in workflow_text, "reading the wrong files"

    for name, body in (("pi-gen/build.sh", local), ("build-image.yml", workflow_text)):
        assert f"stage-milo/{STAGE_TARBALL}" in body, \
            f"{name} does not place {STAGE_TARBALL} beside the stage"

    # The local builder has no CI job to take the artefact from, so it builds it
    # — from the lockfile, like the workflow does.
    assert "npm ci" in local, "the local builder re-resolves its dependencies"



SATELLITE_UPDATES = REPO_ROOT / "backend" / "core" / "updates" / "satellite.py"
IMAGE_INSTALLER = STAGE_DIR / "02-install-milo" / "00-run.sh"


def _format_flag(argv, where) -> str:
    flags = [arg for arg in argv if isinstance(arg, str) and arg.startswith(("--format", "--pretty"))]
    assert len(flags) == 1, f"{where}: expected one --format flag, found {flags} in {argv}"
    return flags[0]


def test_the_payload_fingerprint_is_spelled_once_on_both_sides():
    """A satellite is pushed (and its speaker restarted) when the payload it
    reports differs from the server's, compared as strings. Two programs write
    that string: the backend for a pushed unit, the image stage for a flashed
    one. Any difference between the two spellings offers a freshly flashed
    satellite a push of the code it already runs.

    `%h` is refused on either side: its length follows `core.abbrev=auto`, so
    the same commit reads one character longer once the object store passes
    65 535 objects, and every unit in the field is offered a push at the fetch
    that crosses it. That the backend's `log`, `status` and `diff` all read
    `PAYLOAD_PATHSPEC` is pinned in test_satellite_updates.py; what only this
    file can see is the bash copy.
    """
    from backend.core.updates.satellite import PAYLOAD_PATHSPEC

    tree = ast.parse(SATELLITE_UPDATES.read_text(encoding="utf-8"))
    function = next(
        (node for node in ast.walk(tree)
         if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_client_payload_version"),
        None,
    )
    assert function, f"get_client_payload_version was not found in {SATELLITE_UPDATES.name}"
    logs = [
        [arg.value for arg in node.args if isinstance(arg, ast.Constant)]
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_git" and node.args
        and isinstance(node.args[0], ast.Constant) and node.args[0].value == "log"
    ]
    assert len(logs) == 1, f"expected the payload's one `git log`, found {logs}"
    python_format = _format_flag(logs[0], SATELLITE_UPDATES.name)

    line = re.search(r"^PAYLOAD=\$\((.*)\)\s*$", IMAGE_INSTALLER.read_text(), re.MULTILINE)
    assert line, f"{IMAGE_INSTALLER.name} no longer seeds the payload"
    shell = shlex.split(line.group(1))
    assert "log" in shell and "--" in shell, f"the seed is not a `git log` any more: {shell}"
    shell_format = _format_flag(shell, IMAGE_INSTALLER.name)

    for side, spelled in ((SATELLITE_UPDATES.name, python_format), (IMAGE_INSTALLER.name, shell_format)):
        assert spelled == "--format=%H", f"{side} names the commit by {spelled}, not its full hash"
    assert shell[shell.index("--") + 1:] == list(PAYLOAD_PATHSPEC), (
        f"the image seeds a payload over other trees than the backend: {shell}"
    )


INSPECTOR = "inspect-image.sh"


def _inspector_argv(workflow, tag: str, image: str) -> list[str]:
    """The workflow's own call to the image inspector, with the two values only a
    runner knows filled in: the tag the release is published under and the image."""
    inspect = [step for step in workflow["build"].get("steps", []) if step.get("id") == "inspect"]
    assert len(inspect) == 1, f"expected one step with id `inspect`, found {len(inspect)}"
    lines = [line for line in inspect[0].get("run", "").splitlines() if INSPECTOR in line]
    assert len(lines) == 1, f"the inspect step no longer calls {INSPECTOR} once: {lines}"
    argv = shlex.split(lines[0])
    assert "--img" in argv and "--ref" in argv, f"the workflow no longer names an image and a ref: {argv}"

    # Whatever runs the inspector with privileges is the runner's business; the
    # argv the script parses starts at the script.
    start = next((i for i, arg in enumerate(argv) if arg.endswith(INSPECTOR)), None)
    assert start is not None, f"no argument of the inspect step is the script itself: {argv}"
    argv = argv[start:]
    expressions = [arg for arg in argv if arg.startswith("${{")]
    assert len(expressions) == 1, f"expected the ref as the one workflow expression: {argv}"
    return [tag if arg in expressions else image if arg == "$IMAGE" else arg for arg in argv]


def _parse_only(argv: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", *argv],
        cwd=REPO_ROOT,
        env={**os.environ, "INSPECT_PARSE_ONLY": "1"},
        capture_output=True, text=True, timeout=30,
    )


def test_the_image_build_passes_a_ref_inspect_image_reads(workflow, tmp_path):
    """The inspector's `--ref` check is the only one that ties a published image
    to the tag it is published under: the checkout inside must sit exactly on
    it, and the satellite identity must be seeded with it. Two tags on one
    commit (it happened: v0.0.1 and v0.1.0) make `describe` answer the other,
    and every card flashed from that image is a development build offered
    nothing. If this fails, the workflow passes a ref the script never reads —
    the check is skipped without a line, and the release is published anyway.

    Replayed in both orders, since nothing about an option list says which.
    """
    tag, image = "v9.8.7", str(tmp_path / "milo.img")
    as_called = _inspector_argv(workflow, tag, image)
    script = as_called[0]
    for argv in (as_called, [script, "--ref", tag, "--img", image]):
        result = _parse_only(argv)
        assert result.returncode == 0, f"{argv} was refused: {result.stderr}"
        assert f"REF={tag}" in result.stdout.splitlines(), (
            f"the inspector did not read the ref from {argv}: {result.stdout!r}"
        )
        assert f"IMG={image}" in result.stdout.splitlines(), result.stdout


def test_inspect_image_refuses_what_it_cannot_parse(tmp_path):
    """An option the parser does not know used to be taken for a root to
    inspect, which is how a misplaced `--ref` vanished. Refused, it fails the
    image build instead; so do a ref with no value (the next option would be
    read as the ref and the image as a root), a repeated option, and an image
    and a root at once, each of which leaves the script to guess what was meant.
    """
    image = str(tmp_path / "milo.img")
    script = str(REPO_ROOT / "pi-gen" / INSPECTOR)
    # Each case names the rule that must refuse it, so a case that another rule
    # happens to catch cannot stand in for a rule that was deleted: an unknown
    # option alone is the only argument that could become a root.
    for argv, reason in (
        ([script, "--frobnicate"], "unknown option: --frobnicate"),
        ([script, "--ref", "v9.8.7", "--frobnicate"], "unknown option: --frobnicate"),
        ([script, "--img", image, "--frobnicate"], "unknown option: --frobnicate"),
        ([script, "--ref", "--img", image], "--ref needs a value"),
        ([script, "--img", image, "--ref"], "--ref needs a value"),
        ([script, "--ref", "v9.8.7", "--img", image, "--ref", "main"], "--ref given twice"),
        ([script, "--img", image, str(tmp_path)], "--img and a root are exclusive"),
    ):
        result = _parse_only(argv)
        assert result.returncode != 0, f"{argv} was accepted: {result.stdout!r}"
        assert "REF=" not in result.stdout, f"{argv} was parsed: {result.stdout!r}"
        assert reason in result.stderr, (
            f"{argv} was not refused for {reason!r}: {result.stderr!r}"
        )


def _inspect_root(root: Path, ref: str) -> list[str]:
    """The inspector's lines about `ref`, run over a provisioned-looking root."""
    result = subprocess.run(
        ["bash", str(REPO_ROOT / "pi-gen" / INSPECTOR), "--ref", ref, str(root)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
    lines = [" ".join(line.split()) for line in plain.splitlines() if ref in line]
    assert lines, f"the inspector printed nothing about {ref}: {plain[-2000:]!r}"
    return lines


@pytest.mark.parametrize("tag", ["v9.8.7", "v0.2.0-rc1"])
def test_inspect_image_ties_every_published_tag_to_its_checkout(tmp_path, tag):
    """The workflow publishes a release for every `v*` tag, a pre-release one
    included, and the stage seeds `describe --tags --always` — exactly the tag
    there too. A check that skips the suffixed tags publishes a release
    candidate whose checkout and satellite identity nobody looked at: the image
    a test unit is flashed with to try that candidate before the fleet.
    """
    checkout = tmp_path / "home" / "milo" / "milo"
    checkout.mkdir(parents=True)
    git = ["git", "-C", str(checkout), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "release"], ["tag", tag]):
        subprocess.run([*git, *args], check=True, capture_output=True, timeout=30)
    identity = tmp_path / "var" / "lib" / "milo-client" / "app-version"
    identity.parent.mkdir(parents=True)
    identity.write_text(tag)

    assert _inspect_root(tmp_path, tag) == [
        f"ok checkout sits exactly on {tag}",
        f"ok satellite identity seeded with {tag}",
    ]

    identity.write_text(f"{tag}-1-g0000000")
    assert f"FAIL satellite identity seeded with {tag}" in _inspect_root(tmp_path, tag)


def test_inspect_image_skips_a_ref_that_is_not_a_tag(tmp_path):
    """A manual build from a branch has no tag to sit on: checking it would fail
    every such image for a contract it never made."""
    assert _inspect_root(tmp_path, "main") == ["skip checkout ref (main is not a release tag)"]


# --------------------------------------------------------------------------- #
# The published shape: frozen, append-only.
# --------------------------------------------------------------------------- #
#
# Release N installs release N+1 by reading what N+1's build published, with N's
# code: the endpoint it asks which release is current, the asset names it builds
# from that release's tag, the sidecar it parses the digest out of. Every check
# above compares the workflow with the backend *at the same HEAD*, so a rename on
# both sides in one commit keeps them green — and strands every unit on N, which
# asks for a name N+1 no longer publishes and is refused at every press, for good:
# the update is the only way N's code changes. The same crossing as the update
# journal (test_server_update_trial.py) and the satellite's `POST /app/update`
# form (test_milo_client_contract.py). Adding an asset, a field or a line is
# free; renaming or removing one costs a reflash of every unit.

FROZEN_REPO = "leodurandfr/Milo"
FROZEN_LATEST_ENDPOINT = "https://api.github.com/repos/{repo}/releases/latest"
FROZEN_FRONTEND_ASSET_URL = (
    "https://github.com/leodurandfr/Milo/releases/download/{tag}/milo-frontend-{tag}.tar.gz"
)
FROZEN_SIDECAR_SUFFIX = ".sha256"
# A sidecar as every published release carries it: `sha256sum`'s line.
FROZEN_SIDECAR = (
    "8d0bd1bc3d8a4fbd5a9a3c2f1e7b6d5c4b3a2918f7e6d5c4b3a29180f7e6d5c4  milo-frontend-v0.2.0.tar.gz\n",
    "8d0bd1bc3d8a4fbd5a9a3c2f1e7b6d5c4b3a2918f7e6d5c4b3a29180f7e6d5c4",
)
# pi-gen's IMG_NAME: the image is published as `<build date>-milo.img.xz`, the
# name README.md and docs/development.md send whoever flashes a card to.
FROZEN_IMAGE_NAME = "milo"
VERSION_SERVICE = REPO_ROOT / "backend" / "core" / "updates" / "version.py"
PI_GEN_CONFIG = REPO_ROOT / "pi-gen" / "config"


def _fstring_templates(path: Path) -> list[str]:
    """Every f-string of a module, its fields written back as `{name}`."""
    templates = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.JoinedStr):
            parts = []
            for value in node.values:
                if isinstance(value, ast.Constant):
                    parts.append(str(value.value))
                elif isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
                    parts.append("{" + value.value.id + "}")
                else:
                    parts.append("{?}")
            templates.append("".join(parts))
    return templates


def test_the_endpoint_and_the_asset_names_a_unit_asks_for_are_frozen(workflow):
    """What the release installed on a unit asks GitHub for, and the names it
    then downloads, are the names every later release publishes."""
    milo = PROGRAMS["milo"]
    assert milo["repo"] == FROZEN_REPO, (
        f"the offer now asks {milo['repo']!r}: every unit on an earlier release keeps asking "
        f"{FROZEN_REPO!r}, and is offered whatever is published there"
    )
    templates = _fstring_templates(VERSION_SERVICE)
    assert len(templates) >= 5, f"f-strings not read from {VERSION_SERVICE.name}: {templates}"
    assert FROZEN_LATEST_ENDPOINT in templates, (
        f"version.py no longer asks {FROZEN_LATEST_ENDPOINT}: the unit's own answer to "
        "'which release is current' would come from elsewhere than every earlier unit's"
    )
    assert milo["frontend_asset_url"] == FROZEN_FRONTEND_ASSET_URL, (
        "the frontend asset moved: every unit on an earlier release downloads "
        f"{FROZEN_FRONTEND_ASSET_URL}, which the next release would no longer publish"
    )

    # And what the workflow publishes, read out of the file as the same-HEAD
    # check above reads it.
    literal = re.search(r'frontend_asset=(\S+)" >> "\$GITHUB_OUTPUT"', WORKFLOW.read_text())
    assert literal, "the vars job stopped writing the asset name to its output"
    published = literal.group(1).replace("${REF}", "{tag}")
    assert published == FROZEN_FRONTEND_ASSET_URL.rsplit("/", 1)[-1], published

    names = re.findall(r'^IMG_NAME="([^"]+)"$', PI_GEN_CONFIG.read_text(), re.MULTILINE)
    assert names == [FROZEN_IMAGE_NAME], f"pi-gen/config names the image {names}"
    files = next(
        step for step in workflow["build"]["steps"] if "action-gh-release" in str(step.get("uses", ""))
    )["with"]["files"]
    lines = [line.strip() for line in files.splitlines() if line.strip()]
    assert lines[0] == "${{ env.compressed_image }}", f"the image is no longer the first file published: {lines}"


def _pack_step(workflow) -> str:
    steps = [step for step in workflow["frontend"]["steps"] if "sha256sum" in step.get("run", "")]
    assert len(steps) == 1, f"expected the one step that packs the asset with its checksum: {steps}"
    return steps[0]["run"]


def test_the_sidecar_is_the_line_every_release_installed_reads(workflow, tmp_path):
    """The workflow's own packing step, run for real, writes a sidecar the
    installed release parses the digest out of; and a sidecar of the frozen
    shape still parses. Fails when either side moves: a release whose sidecar
    the units in the field cannot read is a release none of them can install.
    """
    from backend.core.updates import update

    assert update.FRONTEND_SIDECAR_SUFFIX == FROZEN_SIDECAR_SUFFIX
    sidecar, digest = FROZEN_SIDECAR
    assert update.sidecar_digest(sidecar) == digest
    assert update.sidecar_digest("<html>Not Found</html>") is None, "the parse no longer refuses"

    asset = "milo-frontend-v9.8.7.tar.gz"
    script = _pack_step(workflow).replace("${{ needs.vars.outputs.frontend_asset }}", asset)
    assert "${{" not in script, f"the pack step reads another expression: {script}"
    assert f'"${{ASSET}}{FROZEN_SIDECAR_SUFFIX}"' in script, f"the sidecar is no longer the asset's name + suffix: {script}"
    (tmp_path / "frontend" / "dist").mkdir(parents=True)
    (tmp_path / "frontend" / "dist" / "index.html").write_text("<!doctype html>")
    result = subprocess.run(["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr

    written = (tmp_path / f"{asset}{FROZEN_SIDECAR_SUFFIX}").read_text()
    actual = hashlib.sha256((tmp_path / asset).read_bytes()).hexdigest()
    assert update.sidecar_digest(written) == actual, written

    release = next(
        step for step in workflow["build"]["steps"] if "action-gh-release" in str(step.get("uses", ""))
    )["with"]["files"]
    assert f"frontend_asset }}}}{FROZEN_SIDECAR_SUFFIX}" in release, release
