"""Structural guardrail: the CI runs code named by its commit, and holds its token to itself.

The image workflow builds what every unit installs as root and publishes it
with a token that can write releases; lint.yml is the gate that build waits on.
Three things in them were names someone else could move:

  * every action was a major tag (`actions/checkout@v6`). A tag is a pointer its
    owner can repoint, and the next run executes whatever it then names, in the
    job that holds `contents: write` and builds the image, with no change here;
  * every checkout persisted the job's token for git (actions/checkout's
    default; since v6 in a file under `$RUNNER_TEMP` that the repository's git
    config includes), where every later step — a build script, an npm package
    hook, a pi-gen stage run as root — can read it;
  * pi-gen was cloned from the head of its `arm64` branch, so two images of one
    Milō tag built a week apart could differ in everything stage0-2 lays down.

A full commit hash names content. Nothing else can see these files: a workflow
is not imported, and its symptom is a green build that ran something nobody
here chose.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
from pathlib import Path

import pytest
import yaml

from backend.tests.architecture.test_rootfs_deployment import STAGE_BUILDERS

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.yml"))
PIGEN_PIN = REPO_ROOT / "pi-gen" / "upstream.env"

# `uses: owner/repo@ref  # comment` — the line as written, comment included,
# since YAML drops the comment that names the release.
USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s+(?P<target>\S+?)(?:\s+#\s*(?P<comment>.*?))?\s*$")
PINNED_RE = re.compile(r"^(?P<action>[\w.-]+/[\w./-]+)@(?P<sha>[0-9a-f]{40})$")
RELEASE_RE = re.compile(r"^v\d+\.\d+\.\d+$")


def _uses_lines(path: Path) -> list[tuple[str, str]]:
    return [
        (m.group("target"), m.group("comment") or "")
        for line in path.read_text().splitlines()
        if (m := USES_RE.match(line))
    ]


def _parsed_steps(path: Path) -> list[tuple[str, dict]]:
    jobs = yaml.safe_load(path.read_text()).get("jobs", {})
    return [(name, step) for name, job in jobs.items() for step in job.get("steps", [])]


def _parsed_uses(path: Path) -> list[str]:
    """Every `uses:` the workflow runs, read by YAML: steps and called workflows."""
    jobs = yaml.safe_load(path.read_text()).get("jobs", {})
    return [job["uses"] for job in jobs.values() if "uses" in job] + [
        step["uses"] for _, step in _parsed_steps(path) if "uses" in step
    ]


def _is_pinned(target: str, comment: str) -> bool:
    """A local workflow, or an action named by a full commit with its release beside it."""
    return target.startswith("./") or bool(PINNED_RE.match(target) and RELEASE_RE.match(comment))


def test_the_extractors_see_every_action_the_workflows_run():
    """The line reader and YAML must agree, or an action the reader misses is unchecked."""
    assert len(WORKFLOWS) >= 2, f"workflows found: {WORKFLOWS}"
    for path in WORKFLOWS:
        assert sorted(t for t, _ in _uses_lines(path)) == sorted(_parsed_uses(path)), path.name

    actions = {t.split("@")[0] for path in WORKFLOWS for t, _ in _uses_lines(path)}
    assert {
        "actions/checkout", "actions/setup-node", "actions/setup-python",
        "actions/upload-artifact", "actions/download-artifact", "softprops/action-gh-release",
    } <= actions, sorted(actions)

    # The pin test must discriminate before it is trusted.
    sha = "d23441a48e516b6c34aea4fa41551a30e30af803"
    assert _is_pinned(f"actions/checkout@{sha}", "v6.1.0")
    assert _is_pinned("./.github/workflows/lint.yml", "")
    assert not _is_pinned("actions/checkout@v6", "")
    assert not _is_pinned("actions/checkout@v6.1.0", "")
    assert not _is_pinned(f"actions/checkout@{sha}", "")
    assert not _is_pinned(f"actions/checkout@{sha[:12]}", "v6.1.0")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_runs_the_commit_its_line_names(path):
    """Fails when a step names an action by a tag or a branch, or by a commit
    with no release beside it. A moved tag runs new code in the job that builds
    the image and can publish it; a bare hash cannot be checked against the
    release it was meant to be, and is the next pin nobody dares move.
    """
    unpinned = [
        f"{target}  # {comment}".rstrip(" #")
        for target, comment in _uses_lines(path)
        if not _is_pinned(target, comment)
    ]
    assert not unpinned, (
        f"{path.name} runs these actions by a name that can move, or with no "
        f"release named beside the commit:\n" + "\n".join(unpinned)
    )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_checkout_persists_the_token(path):
    """Fails when a checkout keeps actions/checkout's default, which writes the
    job's token where every later step can read it — in the image build, a token
    that can write releases, readable by each pi-gen stage it runs as root.
    """
    checkouts = [
        (job, step) for job, step in _parsed_steps(path)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]
    assert checkouts, f"no checkout found in {path.name}"
    keeping = [job for job, step in checkouts if step.get("with", {}).get("persist-credentials") is not False]
    assert not keeping, f"{path.name}: these jobs' checkouts keep the token: {keeping}"


def _pigen_pin() -> dict[str, str]:
    return dict(re.findall(r'^(PIGEN_\w+)="([^"]*)"$', PIGEN_PIN.read_text(), re.MULTILINE))


def _code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


PIGEN_CLONE_RE = re.compile(r"git\s+clone\b[^\n]*pi-gen|--branch\b|origin/")
PIGEN_FETCH_RE = re.compile(r'git\b[^\n]*\bfetch\b[^\n]*"\$\{?PIGEN_REPO\}?"\s+"\$\{?PIGEN_COMMIT\}?"')
PIGEN_CHECKOUT_RE = re.compile(r'git\b[^\n]*\bcheckout\b[^\n]*--detach\s+"\$\{?PIGEN_COMMIT\}?"')


def test_pi_gen_is_pinned_to_a_commit():
    """The one declaration both builders read names a full commit, not a branch or a tag."""
    pin = _pigen_pin()
    assert re.fullmatch(r"[0-9a-f]{40}", pin.get("PIGEN_COMMIT", "")), pin
    assert pin.get("PIGEN_REPO", "").startswith("https://github.com/"), pin


@pytest.mark.parametrize("builder", sorted(STAGE_BUILDERS))
def test_every_image_builder_builds_on_the_pinned_pi_gen(builder):
    """Fails when a builder clones pi-gen by a branch, or fetches anything but
    the pinned commit: the image would then be built on whatever that branch
    held the day it ran, and the CI and a local build could differ.
    """
    text = _code(STAGE_BUILDERS[builder].read_text())
    assert "pi-gen" in text and "stage-milo" in text, f"reading the wrong file for {builder}"

    assert "upstream.env" in text, f"{builder} does not read pi-gen/upstream.env"
    assert PIGEN_FETCH_RE.search(text), f"{builder} does not fetch $PIGEN_COMMIT from $PIGEN_REPO"
    assert PIGEN_CHECKOUT_RE.search(text), f"{builder} does not check out $PIGEN_COMMIT"
    assert not PIGEN_CLONE_RE.search(text), (
        f"{builder} still takes pi-gen by a branch: {PIGEN_CLONE_RE.search(text).group(0)!r}"
    )

    # The branch test must discriminate before it is trusted.
    assert PIGEN_CLONE_RE.search("git clone --branch arm64 --depth 1 https://github.com/RPi-Distro/pi-gen.git x")
    assert PIGEN_CLONE_RE.search('git reset --hard "origin/${PIGEN_BRANCH}"')
