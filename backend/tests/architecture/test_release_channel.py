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
import shutil
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
    and nginx serves it. A signed release checks its frontend against its
    manifest's `frontend_sha256`, never the sidecar; the sidecar is what the
    releases from before signing (v0.1.4 and earlier) install the next one by,
    so a release published without one is a release none of them can install.
    Frozen by invariant 9 for them.
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
        assert workflow[name]["with"]["ref"] == "${{ needs.vars.outputs.milo_commit }}", name
    # The checks run on the tree being built, not on the commit that triggered
    # the run: the two differ on a manual dispatch of another ref.
    for job in ("backend", "frontend"):
        checkout = next(
            step for step in lint["jobs"][job]["steps"]
            if step.get("uses", "").startswith("actions/checkout")
        )
        assert checkout.get("with", {}).get("ref") == "${{ inputs.ref }}", job


MILO_COMMIT = "${{ needs.vars.outputs.milo_commit }}"


def test_every_job_that_reads_the_tree_reads_the_commit_being_built(workflow):
    """One run, one tree. On a manual dispatch a checkout with no `ref:` takes
    the branch the run was launched from, not the ref it names: the build job
    took the stage, dependencies.env, system-manifest.env and the tree the
    inspection compares against from main, while the image cloned the ref and
    installed that ref's frontend — three hours for an image of two trees,
    judged against the wrong one. And a branch's name is read anew by each
    reader, hours apart, while main moves under them: so every reader takes the
    one commit the vars job resolved, the chroot's clone included. Fails when
    the frontend, checks or build job, or the clone, reads any other ref.
    """
    checkouts = {
        job: [step for step in workflow[job]["steps"]
              if str(step.get("uses", "")).startswith("actions/checkout@")]
        for job in ("frontend", "build")
    }
    assert all(checkouts.values()), f"a job that reads the tree no longer checks it out: {checkouts}"
    wrong = {
        job: [step.get("with", {}).get("ref") for step in steps]
        for job, steps in checkouts.items()
        if any(step.get("with", {}).get("ref") != MILO_COMMIT for step in steps)
    }
    assert not wrong, f"checkouts at another ref than {MILO_COMMIT}: {wrong}"
    # The checks job checks out inside lint.yml, which the test above holds to
    # `inputs.ref`; what it is handed is held here.
    assert workflow["checks"]["with"]["ref"] == MILO_COMMIT, workflow["checks"]
    # The commit is resolved once, by the vars job, and is what the image clones.
    assert workflow["vars"]["outputs"].get("milo_commit") == "${{ steps.set.outputs.milo_commit }}", (
        workflow["vars"]["outputs"])
    configure = [step for step in workflow["build"]["steps"] if "checkout-env.sh" in str(step.get("run", ""))]
    assert len(configure) == 1, f"expected one step writing what the image clones, found {len(configure)}"
    assert configure[0].get("env", {}).get("MILO_COMMIT") == MILO_COMMIT, configure[0].get("env")
    assert re.search(r'checkout-env\.sh\s+"\$\{MILO_COMMIT\}"', configure[0]["run"]), (
        "the image clones another ref than the commit being built")


@pytest.mark.parametrize("event, ref", [
    ("push", "v1.2.3"), ("workflow_dispatch", "v1.2.3"), ("workflow_dispatch", "main"), ("schedule", "main"),
])
def test_the_commit_being_built_is_the_refs_own(workflow, tmp_path, event, ref):
    """The vars job's script, run as the runner runs it, against a stand-in of
    the repository the image clones: a tag push builds the commit it pushed, and
    a dispatch or the schedule builds the commit the ref names on that
    repository — an annotated tag peeled to its commit — never the one the run
    was launched from (GITHUB_SHA, main's head, on a dispatch of a tag). Fails
    when the commit the whole run is pinned to is another one."""
    bare, work = tmp_path / "remote.git", tmp_path / "work"
    url = "https://github.com/someone/Milo.git"
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"url.file://{bare}.insteadOf", "GIT_CONFIG_VALUE_0": url,
    }
    _git_in(env, tmp_path, "init", "-q", "-b", "main", str(work))
    _git_in(env, work, "commit", "-q", "--allow-empty", "-m", "the release")
    _git_in(env, work, "tag", "-a", "v1.2.3", "-m", "v1.2.3")
    tagged = _git_in(env, work, "rev-parse", "HEAD")
    _git_in(env, work, "commit", "-q", "--allow-empty", "-m", "main moved on")
    head = _git_in(env, work, "rev-parse", "HEAD")
    _git_in(env, tmp_path, "clone", "-q", "--bare", str(work), str(bare))

    steps = [step for step in workflow["vars"]["steps"] if step.get("id") == "set"]
    assert len(steps) == 1, "the vars job's `set` step is gone"
    script = steps[0]["run"].replace("${{ github.event_name }}", event).replace(
        "${{ github.event.inputs.branch || 'main' }}", ref)
    assert "${{" not in script, f"the vars script reads another expression: {script}"
    out = tmp_path / "out"
    result = subprocess.run(["bash", "-e", "-c", script], capture_output=True, text=True, timeout=30, env={
        **env, "GITHUB_OUTPUT": str(out), "GITHUB_REF": f"refs/tags/{ref}",
        "GITHUB_SHA": tagged if event == "push" else head,
        "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "someone/Milo"})
    assert result.returncode == 0, result.stderr
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert outputs["milo_ref"] == ref, outputs
    assert outputs["milo_commit"] == (tagged if ref == "v1.2.3" else head), outputs


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


# A local build, run for real up to the point where it would fetch pi-gen: a
# throwaway checkout holding the builder and one file in each place it copies
# from, cloned by the image from a bare repository behind an https URL. Named by
# MILO_REPO, not as the checkout's origin: git rewrites an origin's URL through
# `insteadOf` when it is read, and checkout-env.sh would refuse the file:// URL.
LOCAL_ORIGIN = "https://github.com/fork/Milo.git"
LOCAL_BUILD_INPUTS = (
    "dependencies.env", "system-manifest.env", "pi-gen/config",
    "pi-gen/stage-milo/00-install-deps/00-run.sh", "frontend/package.json",
)
FETCHING_PI_GEN = "Fetching pi-gen"


def _git_in(env, cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          timeout=30, env=env).stdout.strip()


@pytest.fixture
def local_checkout(tmp_path):
    """A checkout at v1.1.0 = main, pushed, with v1.0.0 one commit behind, and
    the environment that runs its builder offline."""
    bare = tmp_path / "remote.git"
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith(("MILO_", "GIT_"))},
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.file://{bare}.insteadOf", "GIT_CONFIG_VALUE_0": LOCAL_ORIGIN,
        "MILO_REPO": LOCAL_ORIGIN,
    }
    checkout = tmp_path / "checkout"
    (checkout / "pi-gen").mkdir(parents=True)
    for name in ("build.sh", "checkout-env.sh"):
        shutil.copy2(LOCAL_BUILDER.parent / name, checkout / "pi-gen" / name)
    # pi-gen from nowhere: a build that gets past the check stops at the fetch.
    (checkout / "pi-gen" / "upstream.env").write_text(
        f"PIGEN_REPO={tmp_path / 'no-pi-gen'}\nPIGEN_COMMIT={'0' * 40}\n")
    for rel in LOCAL_BUILD_INPUTS:
        (checkout / rel).parent.mkdir(parents=True, exist_ok=True)
        (checkout / rel).write_text("v1.0.0\n")
    _git_in(env, tmp_path, "init", "-q", "-b", "main", str(checkout))
    _git_in(env, checkout, "add", "-A")
    _git_in(env, checkout, "commit", "-q", "-m", "one")
    _git_in(env, checkout, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    (checkout / "system-manifest.env").write_text("v1.1.0\n")
    _git_in(env, checkout, "commit", "-q", "-am", "two")
    _git_in(env, checkout, "tag", "-a", "v1.1.0", "-m", "v1.1.0")
    _git_in(env, tmp_path, "clone", "-q", "--bare", str(checkout), str(bare))
    # The fixture's own premise: the origin answers, through the URL the image clones.
    assert _git_in(env, checkout, "ls-remote", LOCAL_ORIGIN, "v1.1.0"), "the stand-in origin does not answer"
    return checkout, env


def _build_locally(local_checkout, ref: str) -> subprocess.CompletedProcess:
    checkout, env = local_checkout
    return subprocess.run(["bash", str(checkout / "pi-gen" / "build.sh"), "--native"], capture_output=True,
                          text=True, timeout=60, env={**env, "MILO_BRANCH": ref})


def _refused(result: subprocess.CompletedProcess) -> bool:
    if FETCHING_PI_GEN in result.stdout:
        return False
    assert result.returncode != 0 and "Build from the ref you want" in result.stderr, (
        f"the build stopped before the fetch for another reason: {result.stdout}{result.stderr}")
    return True


@pytest.mark.parametrize("ref", ["main", "v1.1.0", "head-sha"])
def test_a_local_build_of_the_checked_out_ref_goes_ahead(local_checkout, ref):
    """The check refuses only what it must: a build of the ref this checkout
    sits on, as its origin holds it, reaches the pi-gen fetch as before."""
    if ref == "head-sha":
        ref = _git_in(local_checkout[1], local_checkout[0], "rev-parse", "--short=12", "HEAD")
    result = _build_locally(local_checkout, ref)
    assert not _refused(result), result.stderr
    # And the image clones the commit checked, by its hash: the branch's name,
    # read again by the chroot hours later, may have moved.
    head = _git_in(local_checkout[1], local_checkout[0], "rev-parse", "HEAD")
    assert f"MILO_BRANCH={head}" in result.stdout, result.stdout
    assert not _git_in(local_checkout[1], local_checkout[0], "for-each-ref", "refs/milo-build-check"), (
        "the check left its refs behind in the checkout")


@pytest.mark.parametrize("ref, change", [
    ("v1.0.0", None),
    ("v9.9.9", None),
    ("main", "unpushed"),
    ("main", "system-manifest.env"),
    ("main", "pi-gen/stage-milo/04-new/00-run.sh"),
    ("main", "frontend/src/App.vue"),
    ("head-sha", "unpushed"),
])
def test_a_local_build_of_another_tree_than_the_checkout_is_refused(local_checkout, ref, change):
    """The stage installs the packages of this checkout's system-manifest.env,
    then applies the unit states of the cloned ref's: built from a checkout of
    main with `MILO_BRANCH=v1.0.0`, the image carries a package its release does
    not declare, or fails `systemctl enable` hours in. Fails when such a build —
    another ref, a branch with commits its origin lacks, or a copied file left
    uncommitted — gets as far as fetching pi-gen."""
    checkout, env = local_checkout
    if change == "unpushed":
        (checkout / "dependencies.env").write_text("local\n")
        _git_in(env, checkout, "commit", "-q", "-am", "not pushed")
    elif change:
        (checkout / change).parent.mkdir(parents=True, exist_ok=True)
        (checkout / change).write_text("uncommitted\n")
    if ref == "head-sha":
        ref = _git_in(env, checkout, "rev-parse", "--short=12", "HEAD")
    result = _build_locally(local_checkout, ref)
    assert _refused(result), f"MILO_BRANCH={ref} ({change}) was built: {result.stdout}"
    assert not (checkout / "pi-gen" / "pi-gen-upstream").exists(), "refused after pi-gen was fetched"


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


def _sidecar_digest(text: str):
    """The digest a sidecar holds, read as the releases before signing read it:
    the first word, if it is a sha256. Restated here because no code in this
    tree reads a sidecar any more — a signed release checks its frontend
    against its manifest — while every earlier release still does."""
    words = text.split()
    return words[0] if words and re.fullmatch(r"[0-9a-f]{64}", words[0]) else None


def test_the_sidecar_is_the_line_the_releases_before_signing_read(workflow, tmp_path):
    """The workflow's own packing step, run for real, writes the sidecar a
    release from before signing parses the digest out of. Fails when it moves:
    those releases install the next one by it, and a sidecar they cannot read
    is a release none of them can install.
    """
    sidecar, digest = FROZEN_SIDECAR
    assert _sidecar_digest(sidecar) == digest
    assert _sidecar_digest("<html>Not Found</html>") is None, "the parse no longer refuses"

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
    assert _sidecar_digest(written) == actual, written

    release = next(
        step for step in workflow["build"]["steps"] if "action-gh-release" in str(step.get("uses", ""))
    )["with"]["files"]
    assert f"frontend_asset }}}}{FROZEN_SIDECAR_SUFFIX}" in release, release


# --------------------------------------------------------------------------- #
# The signature: what makes a release one a unit installs.
# --------------------------------------------------------------------------- #
#
# CI drafts the release with a manifest; the owner signs it off GitHub, on the
# Mac where the key lives (tools/release/sign-release.sh), and publishes; a
# unit verifies it against the keys of the release it runs before it checks
# anything out (release_signature.py). Everything below holds one of those
# joints: the names, crossing a release boundary like the assets above; the
# manifest CI writes against the one a unit reads; the owner's command against
# the unit's check; and the check against the install path it guards.

FROZEN_RELEASE_MANIFEST_URL = (
    "https://github.com/leodurandfr/Milo/releases/download/{tag}/milo-release-{tag}.manifest"
)
FROZEN_SIGNATURE_SUFFIX = ".sig"
FROZEN_SIGNING_NAMESPACE = "milo-release"
FROZEN_SIGNER_IDENTITY = "milo-release"
# The keys a unit reads out of a manifest. Append-only: release N requires
# these of release N+1's manifest, so every later CI writes them.
FROZEN_MANIFEST_KEYS = ("tag", "commit", "frontend_sha256")

# The manifest's grammar, frozen apart from the parser that reads it today —
# restated, not imported, for the reason _sidecar_digest is: the parser in this
# tree is the *next* release's, and the one that reads N+1's manifest is N's.
# One `key=value` per line, each key once, a key in [a-z0-9_]; the commit and
# the frontend's digest full lowercase hex. The value is printable ASCII, which
# is narrower than any parser so far accepts and what CI has to write within.
FROZEN_MANIFEST_LINE = re.compile(r"(?P<key>[a-z0-9_]+)=(?P<value>[\x20-\x7e]*)")
FROZEN_FIELD_SHAPES = {"commit": re.compile(r"[0-9a-f]{40}"), "frontend_sha256": re.compile(r"[0-9a-f]{64}")}
# A manifest as the first signed release's CI writes it, byte for byte. A
# withdrawal makes newer code read an older release's manifest, so every later
# parser must still take this one.
FROZEN_MANIFEST = (
    b"tag=v1.0.0\n"
    b"commit=0123456789abcdef0123456789abcdef01234567\n"
    b"frontend=milo-frontend-v1.0.0.tar.gz\n"
    b"frontend_sha256=" + b"ab" * 32 + b"\n"
    b"image=2026-11-21-milo.img.xz\n"
    b"image_sha256=" + b"cd" * 32 + b"\n"
)


def _frozen_grammar_findings(manifest: bytes) -> list[str]:
    """What in `manifest` lies outside the frozen grammar: what some unit, on
    some signed release, may refuse. Empty when every one of them takes it."""
    try:
        lines = manifest.decode("ascii").split("\n")
    except UnicodeDecodeError:
        return ["not ASCII text"]
    if lines and lines[-1] == "":
        lines.pop()
    findings, fields = [], {}
    for number, line in enumerate(lines, start=1):
        matched = FROZEN_MANIFEST_LINE.fullmatch(line)
        if matched is None:
            findings.append(f"line {number} is not key=value: {line!r}")
            continue
        if matched["key"] in fields:
            findings.append(f"{matched['key']} is written twice")
        fields[matched["key"]] = matched["value"]
    for key in FROZEN_MANIFEST_KEYS:
        if not fields.get(key):
            findings.append(f"no {key}")
        elif key in FROZEN_FIELD_SHAPES and not FROZEN_FIELD_SHAPES[key].fullmatch(fields[key]):
            findings.append(f"{key} is not in its frozen shape: {fields[key]!r}")
    return findings

TREE_KEYS = REPO_ROOT / "rootfs" / "etc" / "milo" / "release-signing.pub"
SIGN_SCRIPT = REPO_ROOT / "tools" / "release" / "sign-release.sh"
MANIFEST_STEP = "Write the release manifest"


def _release_step(workflow) -> dict:
    steps = [step for step in workflow["build"]["steps"] if "action-gh-release" in str(step.get("uses", ""))]
    assert len(steps) == 1, f"expected one release step, found {steps}"
    return steps[0]


def test_what_a_unit_verifies_a_release_by_is_frozen(workflow):
    """Release N verifies release N+1 with N's code: the manifest it downloads,
    the signature beside it, the namespace and identity it verifies under.
    Renamed on both sides in one commit, every check at the same HEAD stays
    green and every unit on N refuses N+1, and every release after it, for
    good — the update is the only way N's code changes."""
    from backend.core.updates import release_signature

    assert PROGRAMS["milo"]["release_manifest_url"] == FROZEN_RELEASE_MANIFEST_URL
    assert release_signature.SIGNATURE_SUFFIX == FROZEN_SIGNATURE_SUFFIX
    assert release_signature.SIGNING_NAMESPACE == FROZEN_SIGNING_NAMESPACE
    assert release_signature.SIGNER_IDENTITY == FROZEN_SIGNER_IDENTITY
    assert set(release_signature.REQUIRED_KEYS) <= set(FROZEN_MANIFEST_KEYS), (
        "a unit now requires a manifest key nobody froze. Every release signed before this one "
        "lacks it, so a unit running this code refuses a withdrawal to any of them; appending it "
        "to FROZEN_MANIFEST_KEYS does not change that (FROZEN_MANIFEST will say so)"
    )

    literal = re.search(r'release_manifest=(\S+)" >> "\$GITHUB_OUTPUT"', WORKFLOW.read_text())
    assert literal, "the vars job no longer writes the manifest's name"
    published = literal.group(1).replace("${REF}", "{tag}")
    assert published == FROZEN_RELEASE_MANIFEST_URL.rsplit("/", 1)[-1], published
    assert workflow["vars"]["outputs"]["release_manifest"], "the vars job publishes no manifest name"


def test_ci_drafts_the_release_with_its_manifest_and_signs_nothing(workflow):
    """A published release is offered to every unit, and an immutable one
    takes no asset after publication: so CI leaves a draft, which
    `releases/latest` never names, carrying the manifest for the owner to
    sign. CI holds no key — an account that can run it must not be able to
    make a release a unit accepts — so no signature is published from here.
    The order is append-only: release N downloads by these names."""
    release = _release_step(workflow)["with"]
    assert release.get("draft") is True, f"the release is published by CI, unsigned: {release}"

    files = [line.strip() for line in release["files"].splitlines() if line.strip()]
    assert files[:3] == [
        "${{ env.compressed_image }}",
        "frontend-artefact/${{ needs.vars.outputs.frontend_asset }}",
        "frontend-artefact/${{ needs.vars.outputs.frontend_asset }}.sha256",
    ], files
    assert "${{ needs.vars.outputs.release_manifest }}" in files, files
    assert not [name for name in files if name.endswith(FROZEN_SIGNATURE_SUFFIX)], files

    for path in sorted((REPO_ROOT / ".github").rglob("*.yml")):
        assert not re.search(r"ssh-keygen[^\n]*-Y\s+sign", path.read_text()), f"{path.name} signs a release"


def _run_manifest_step(workflow, root: Path, tag: str) -> Path:
    """The workflow's own manifest step, run in `root` — a checkout at the tag
    with the frontend artifact and an image beside it — as the runner runs it."""
    steps = [step for step in workflow["build"]["steps"] if step.get("name") == MANIFEST_STEP]
    assert len(steps) == 1, f"expected one {MANIFEST_STEP!r} step, found {len(steps)}"
    assert steps[0].get("if") == "needs.vars.outputs.is_release == 'true'", steps[0].get("if")
    names = {
        "needs.vars.outputs.milo_ref": tag,
        "needs.vars.outputs.frontend_asset": f"milo-frontend-{tag}.tar.gz",
        "needs.vars.outputs.release_manifest": f"milo-release-{tag}.manifest",
        "env.compressed_image": "deploy/2026-11-21-milo.img.xz",
    }
    script = steps[0]["run"]
    for expression, value in names.items():
        script = script.replace("${{ " + expression + " }}", value)
    assert "${{" not in script, f"the manifest step reads another expression: {script}"

    (root / "frontend-artefact").mkdir(exist_ok=True)
    (root / "frontend-artefact" / names["needs.vars.outputs.frontend_asset"]).write_bytes(b"the frontend")
    (root / "deploy").mkdir(exist_ok=True)
    (root / names["env.compressed_image"]).write_bytes(b"the image")
    result = subprocess.run(["bash", "-e", "-c", script], cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return root / names["needs.vars.outputs.release_manifest"]


def _git(root: Path) -> list[str]:
    return ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def _git_repo(root: Path, tag: str, keys: str) -> str:
    """A checkout holding `keys` as its release key file, at `tag`; its commit."""
    git = _git(root)
    (root / TREE_KEYS.relative_to(REPO_ROOT)).parent.mkdir(parents=True, exist_ok=True)
    (root / TREE_KEYS.relative_to(REPO_ROOT)).write_text(keys)
    subprocess.run([*git, "init", "-q"], check=True, capture_output=True, timeout=30)
    for args in (["add", "rootfs"], ["commit", "-q", "-m", "release"], ["tag", tag]):
        subprocess.run([*git, *args], check=True, capture_output=True, timeout=30)
    return subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
                          timeout=30).stdout.strip()


def test_the_manifest_ci_writes_is_the_one_a_unit_reads(workflow, tmp_path):
    """Run for real, the step writes what the unit's parser takes: the tag, the
    commit the build checked out, the frontend it published — and within the
    frozen grammar, so the units on every earlier signed release take it too.
    The current parser alone would not say so: loosened in the commit that
    changes CI to match (a header line, a key spelled `image-sha256`), it
    accepts what every unit in the field refuses, for every release after."""
    from backend.core.updates.release_signature import parse_manifest

    commit = _git_repo(tmp_path, "v9.8.7", "# no key\n")
    manifest = _run_manifest_step(workflow, tmp_path, "v9.8.7").read_bytes()

    parsed = parse_manifest(manifest)
    assert parsed.tag == "v9.8.7"
    assert parsed.commit == commit
    assert parsed.frontend_sha256 == hashlib.sha256(b"the frontend").hexdigest()
    assert _frozen_grammar_findings(manifest) == [], manifest.decode()
    # Whoever flashes a card verifies the image by the same signature.
    assert f"image_sha256={hashlib.sha256(b'the image').hexdigest()}" in manifest.decode()


def test_a_manifest_signed_today_still_parses():
    """The other direction: a withdrawal offers the units on N+1 release N back,
    and N+1's code reads N's manifest. A parser that came to require a key, or
    to refuse a line, that the first signed releases wrote refuses every
    withdrawal to one of them — and a withdrawal is the one-gesture way back
    from a bad release."""
    from backend.core.updates.release_signature import parse_manifest

    assert _frozen_grammar_findings(FROZEN_MANIFEST) == [], "the frozen sample left the frozen grammar"
    parsed = parse_manifest(FROZEN_MANIFEST)
    assert (parsed.tag, parsed.commit, parsed.frontend_sha256) == (
        "v1.0.0", "0123456789abcdef0123456789abcdef01234567", "ab" * 32,
    )


@pytest.mark.parametrize("drift, reason", [
    (lambda m: b"# Milo release manifest\n" + m, "not key=value"),
    (lambda m: m.replace(b"image_sha256=", b"image-sha256="), "not key=value"),
    (lambda m: m + b"tag=v1.0.1\n", "tag is written twice"),
    (lambda m: m.replace(b"commit=0123456789abcdef0123456789abcdef01234567", b"commit=0123456"),
     "commit is not in its frozen shape"),
    (lambda m: m.replace(b"frontend_sha256=", b"frontend_digest="), "no frontend_sha256"),
    (lambda m: m.replace(b"\n", b"\r\n"), "not key=value"),
    (lambda m: m.replace(b"tag=v1.0.0", "tag=v1.0.0-é".encode()), "not ASCII"),
], ids=["header", "hyphen", "repeated", "short-commit", "renamed-key", "crlf", "non-ascii"])
def test_the_frozen_grammar_goes_red_on_each_drift(drift, reason):
    findings = _frozen_grammar_findings(drift(FROZEN_MANIFEST))
    assert any(reason in finding for finding in findings), findings


def test_the_tree_lists_the_key_releases_are_signed_with():
    """The allowed-signers file every unit verifies the next release with. A
    tree listing no key is a unit that refuses every release; a line in another
    shape is a key ssh-keygen never matches. Installed at the path the backend
    reads, by the sync every update runs (rootfs/ to /)."""
    lines = [line for line in TREE_KEYS.read_text().splitlines() if line.strip() and not line.startswith("#")]
    shape = re.compile(
        rf'^{FROZEN_SIGNER_IDENTITY} namespaces="{FROZEN_SIGNING_NAMESPACE}" ssh-ed25519 (AAAAC3NzaC1lZDI1NTE5[A-Za-z0-9+/]+=*)( .*)?$'
    )
    assert lines, f"{TREE_KEYS.relative_to(REPO_ROOT)} lists no key: every unit refuses every release"
    for line in lines:
        assert shape.match(line), f"not an ed25519 allowed-signers line for {FROZEN_SIGNER_IDENTITY}: {line!r}"
    assert PROGRAMS["milo"]["release_signing_key"] == "/" + str(TREE_KEYS.relative_to(REPO_ROOT / "rootfs"))


# The install path, read out of update.py: the verification it calls, where the
# frontend's digest comes from, and what the checkout is given.

def _install_path_findings(source: str) -> list[str]:
    """What is wrong with how `_update_milo_app` uses the signature: empty when
    it verifies the release before it stages the frontend, stages it against
    the verified manifest's digest, compares the tag's commit with the
    manifest's, and checks out that commit — in that order."""
    function = next(
        (node for node in ast.walk(ast.parse(source))
         if isinstance(node, ast.AsyncFunctionDef) and node.name == "_update_milo_app"),
        None,
    )
    if function is None:
        return ["no _update_milo_app"]

    def calls(name):
        return [node for node in ast.walk(function)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == name]

    findings = []
    verified = None
    for node in ast.walk(function):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Await)
                and isinstance(node.value.value, ast.Call)
                and getattr(node.value.value.func, "attr", None) == "verify_release"):
            verified = node
    if verified is None or not isinstance(verified.targets[0], ast.Name):
        return ["the install keeps no verified manifest (`x = await self.verify_release(tag)`)"]
    manifest = verified.targets[0].id

    stages = calls("_stage_release_frontend")
    checkouts = [node for node in calls("_run_git")
                 if node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == "checkout"]
    compared = [node for node in ast.walk(function)
                if isinstance(node, ast.Compare)
                and any(isinstance(side, ast.Attribute) and side.attr == "commit"
                        and isinstance(side.value, ast.Name) and side.value.id == manifest
                        for side in (node.left, *node.comparators))]
    if len(stages) != 1:
        findings.append(f"expected one frontend stage, found {len(stages)}")
    elif not (len(stages[0].args) == 2 and isinstance(stages[0].args[1], ast.Attribute)
              and isinstance(stages[0].args[1].value, ast.Name) and stages[0].args[1].value.id == manifest):
        findings.append("the frontend is not staged against the verified manifest's digest")
    elif stages[0].lineno < verified.lineno:
        findings.append("the frontend is staged before the release is verified")
    if len(checkouts) != 1:
        findings.append(f"expected one checkout, found {len(checkouts)}")
        return findings
    checkout = checkouts[0]
    if checkout.lineno < verified.lineno:
        findings.append("the checkout runs before the release is verified")
    if not compared or min(node.lineno for node in compared) > checkout.lineno:
        findings.append("the tag's commit is not compared with the signed one before the checkout")
    target = checkout.args[-1] if len(checkout.args) > 1 else None
    if not (isinstance(target, ast.Name) and any(
            isinstance(side, ast.Name) and side.id == target.id
            for node in compared for side in (node.left, *node.comparators))):
        findings.append("the checkout is not given the commit compared with the signed one")
    return findings


def test_the_install_verifies_the_release_before_it_checks_it_out():
    """The unit installs as root what it checks out, so the check stands on the
    path itself: verified first, the frontend held to the signed digest, the
    tag's commit held to the signed commit, and that commit — not the tag, a
    ref anyone who can push moves — checked out. The behavior is driven in
    test_update_service.py; this is what keeps a second install path, or a
    reordering, from slipping past it."""
    assert _install_path_findings(UPDATE_SERVICE.read_text()) == []


_SIGNED_INSTALL = """
async def _update_milo_app(self, status):
    tag = status["latest"]["tag_name"]
    manifest = await self.verify_release(tag)
    staging = await self._stage_release_frontend(tag, manifest.frontend_sha256)
    target_commit = await self._git_output("rev-parse", tag)
    if target_commit != manifest.commit:
        raise Exception("moved")
    ok, out = await self._run_git("checkout", "--force", target_commit)
"""


@pytest.mark.parametrize("drift, reason", [
    (lambda s: s.replace("    manifest = await self.verify_release(tag)\n", "")
     .replace("    ok, out", "    manifest = await self.verify_release(tag)\n    ok, out"),
     "before the release is verified"),
    (lambda s: s.replace("manifest.frontend_sha256", "sidecar"), "verified manifest's digest"),
    (lambda s: s.replace('"--force", target_commit', '"--force", tag'), "given the commit compared"),
    (lambda s: s.replace("    if target_commit != manifest.commit:\n        raise Exception(\"moved\")\n", ""),
     "not compared"),
    (lambda s: s.replace("manifest = await self.verify_release(tag)", "await self.verify_release(tag)"),
     "keeps no verified manifest"),
], ids=["verified-late", "sidecar-digest", "checkout-by-tag", "no-commit-check", "result-dropped"])
def test_the_install_path_check_goes_red_on_each_drift(drift, reason):
    assert _install_path_findings(_SIGNED_INSTALL) == [], "the reference install is not clean"
    findings = _install_path_findings(drift(_SIGNED_INSTALL))
    assert any(reason in finding for finding in findings), findings


# The owner's command, against the unit's check.

_FAKE_GH = r'''#!/bin/bash
set -e
d="$FAKE_GH"
echo "$*" >> "$d/calls"
case "$1 $2" in
  "release download")
    shift 3; dir=.; pats=()
    while [ $# -gt 0 ]; do
      case "$1" in --pattern) pats+=("$2"); shift 2 ;; --dir) dir=$2; shift 2 ;; *) shift ;; esac
    done
    for p in "${pats[@]}"; do
      [ -f "$d/assets/$p" ] || { echo "no asset $p" >&2; exit 1; }
      cp "$d/assets/$p" "$dir/"
    done ;;
  "release view")
    [ ! -f "$d/unreachable" ] || { echo "HTTP 502: Bad Gateway" >&2; exit 1; }
    [ -s "$d/latest" ] || { echo "release not found" >&2; exit 1; }
    cat "$d/latest" ;;
  "release upload") cp "$4" "$d/uploaded/" ;;
  "release edit") echo "$3 $4" >> "$d/edited" ;;
  *) echo "unexpected: gh $*" >&2; exit 1 ;;
esac
'''


class _Draft:
    """A checkout at a tag the owner pushed, its release drafted by CI — the
    manifest written by the workflow's own step — and a `gh` that serves the
    draft and records what the script does to it."""

    def __init__(self, workflow, root: Path, key, tag="v9.8.7"):
        self.tag, self.key = tag, key
        self.repo = root / "checkout"
        self.repo.mkdir()
        self.commit = _git_repo(self.repo, tag, key.allowed_signers.read_text())
        self.gh = root / "gh"
        for name in ("assets", "uploaded", "bin"):
            (self.gh / name).mkdir(parents=True)
        manifest = _run_manifest_step(workflow, self.repo, tag)
        self.manifest = self.gh / "assets" / manifest.name
        manifest.rename(self.manifest)
        (self.repo / "frontend-artefact" / f"milo-frontend-{tag}.tar.gz").rename(
            self.gh / "assets" / f"milo-frontend-{tag}.tar.gz")
        (self.gh / "bin" / "gh").write_text(_FAKE_GH)
        (self.gh / "bin" / "gh").chmod(0o755)

    def fleet(self, key, tag="v9.8.6") -> None:
        """The release the units run now: a tag of this checkout listing `key`."""
        (self.repo / TREE_KEYS.relative_to(REPO_ROOT)).write_text(key.allowed_signers.read_text())
        git = _git(self.repo)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-am", "the fleet's keys"], check=True, timeout=30)
        subprocess.run([*git, "tag", tag], check=True, timeout=30)

    def sign(self, *, latest: str = "", rotate: bool = False,
             unreachable: bool = False) -> subprocess.CompletedProcess:
        (self.gh / "latest").write_text(latest)
        if unreachable:
            (self.gh / "unreachable").touch()
        return subprocess.run(
            ["bash", str(SIGN_SCRIPT), *(["--rotate"] if rotate else []), self.tag, str(self.key.private)],
            cwd=self.repo, capture_output=True, text=True, timeout=60,
            env={**os.environ, "FAKE_GH": str(self.gh), "PATH": f"{self.gh / 'bin'}:{os.environ['PATH']}"},
        )

    def uploaded(self) -> list[str]:
        return sorted(path.name for path in (self.gh / "uploaded").iterdir())

    def published(self) -> bool:
        return (self.gh / "edited").is_file() and f"{self.tag} --draft=false" in (self.gh / "edited").read_text()


@pytest.mark.asyncio
async def test_the_owners_signature_is_one_a_unit_accepts(workflow, tmp_path):
    """The two halves never meet anywhere else: the namespace, identity and
    file names the owner's command signs with against the ones a unit
    verifies with. A drift is a release every unit refuses, found the first
    time a tag is cut. Run end to end with the real ssh-keygen: CI's step
    writes the manifest, the script signs and publishes the draft, and the
    unit's own verifier takes the signature."""
    from backend.core.updates.release_signature import verified_manifest
    from backend.tests.release_signing import make_key

    draft = _Draft(workflow, tmp_path, make_key(tmp_path / "keys"))
    result = draft.sign()

    assert result.returncode == 0, result.stderr
    assert draft.uploaded() == [f"{draft.manifest.name}{FROZEN_SIGNATURE_SUFFIX}"]
    assert draft.published()
    calls = (draft.gh / "calls").read_text().splitlines()
    assert [c.split()[1] for c in calls if c.split()[1] in ("upload", "edit")] == ["upload", "edit"], (
        "the draft is published before its signature is attached; an immutable release takes none after"
    )
    signature = (draft.gh / "uploaded" / draft.uploaded()[0]).read_bytes()
    parsed = await verified_manifest(
        draft.manifest.read_bytes(), signature, draft.repo / TREE_KEYS.relative_to(REPO_ROOT), draft.tag,
    )
    assert parsed.commit == draft.commit


_REFUSALS = {
    "tag-moved": "built from",
    "key-not-listed": "v9.8.7 does not list the key that signs it",
    "fleet-does-not-list-it": "keys v9.8.6 lists",
    "github-unreachable": "GitHub did not say which release the units run",
    "fleet-tag-not-fetched": "no tag v9.8.5 in this checkout",
    "rotate-with-no-fleet": "--rotate hands the units over",
    "rotate-that-rotates-nothing": "nothing rotates",
}


@pytest.mark.parametrize("case", sorted(_REFUSALS))
def test_the_owners_command_refuses_before_it_uploads_anything(workflow, tmp_path, case):
    """What the script checks is what would otherwise be found by every unit
    refusing a published — so immutable, so unfixable — release: a draft built
    from another commit than the tag names on the Mac, a key the release's
    tree does not list, a key the release the units run now does not list.
    And what it cannot check is a refusal too, never a skipped step: a GitHub
    that does not say which release the units run, a tag of that release this
    checkout lacks, a --rotate with nothing to hand over from or to."""
    from backend.tests.release_signing import make_key

    key = make_key(tmp_path / "keys")
    draft = _Draft(workflow, tmp_path, key)
    sign = {}
    if case == "tag-moved":
        subprocess.run([*_git(draft.repo), "commit", "-q", "--allow-empty", "-m", "after"], check=True, timeout=30)
        subprocess.run([*_git(draft.repo), "tag", "-f", draft.tag], check=True, capture_output=True, timeout=30)
    elif case == "key-not-listed":
        draft.key = make_key(tmp_path / "other", name="other")
    elif case == "fleet-does-not-list-it":
        draft.fleet(make_key(tmp_path / "fleet", name="fleet"))
        sign = {"latest": "v9.8.6"}
    elif case == "github-unreachable":
        sign = {"unreachable": True}
    elif case == "fleet-tag-not-fetched":
        sign = {"latest": "v9.8.5"}
    elif case == "rotate-with-no-fleet":
        draft.key = make_key(tmp_path / "other", name="other")
        sign = {"rotate": True}
    else:
        draft.fleet(key)
        sign = {"latest": "v9.8.6", "rotate": True}

    result = draft.sign(**sign)

    assert result.returncode != 0, result.stdout
    assert _REFUSALS[case] in result.stderr, result.stderr
    assert draft.uploaded() == [] and not draft.published()


@pytest.mark.asyncio
async def test_a_rotation_is_one_release_signed_with_the_key_it_drops(workflow, tmp_path):
    """A key that leaked is rotated in one release: its tree lists the new key
    alone, and it is signed with the old one. The units on the release before
    list the old key, so they take it; once on it they trust the new key alone,
    so the leaked one signs nothing they install from then on. Without
    --rotate the script refuses that same release — a tree that drops the key
    signing it is otherwise a mistake that strands its units."""
    from backend.core.updates.release_signature import verified_manifest
    from backend.tests.release_signing import make_key

    old, new = make_key(tmp_path / "old", name="old"), make_key(tmp_path / "new", name="new")
    draft = _Draft(workflow, tmp_path, new)
    draft.key = old
    draft.fleet(old)

    refused = draft.sign(latest="v9.8.6")
    assert refused.returncode != 0 and "sign with --rotate" in refused.stderr, refused.stderr
    assert draft.uploaded() == []

    result = draft.sign(latest="v9.8.6", rotate=True)

    assert result.returncode == 0, result.stderr
    assert draft.published()
    fleet_keys = tmp_path / "fleet.allowed"
    shown = subprocess.run([*_git(draft.repo), "show", f"v9.8.6:{TREE_KEYS.relative_to(REPO_ROOT)}"],
                           check=True, capture_output=True, timeout=30)
    fleet_keys.write_bytes(shown.stdout)
    signature = (draft.gh / "uploaded" / draft.uploaded()[0]).read_bytes()
    parsed = await verified_manifest(draft.manifest.read_bytes(), signature, fleet_keys, draft.tag)
    assert parsed.commit == draft.commit
