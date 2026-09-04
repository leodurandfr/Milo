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
import re
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


def test_the_update_service_neither_pulls_a_branch_nor_builds_a_frontend():
    """The two halves of the old chain, read straight out of the source.

    A behaviour test covers the install that runs (test_update_service.py); this
    covers the one that does not — a helper reintroduced beside it, reachable
    from some other path, would pass every behaviour test in the suite.
    """
    source = UPDATE_SERVICE.read_text()
    assert "_update_milo_app" in source, "reading the wrong file"

    assert not re.search(r'"pull"', source), "the milo install pulls a branch again"
    assert not re.search(r'^\s*"npm"', source, re.MULTILINE), \
        "the unit builds a frontend again"
    # The ref it checks out has to come from the offer, not from a constant.
    assert 'status["latest"]["tag_name"]' in source, \
        "the install no longer takes its ref from the release it was offered"


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
    assert workflow["build"]["needs"] == ["vars", "frontend"], workflow["build"]["needs"]
    assert "download-artifact" in _steps(workflow["build"])


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
