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

The secret scan is the same case once more: a binary downloaded from a release,
which runs only once it matches the sha256 pinned beside its version, and whose
findings reach the log redacted.

A full commit hash names content. Nothing else can see these files: a workflow
is not imported, and its symptom is a green build that ran something nobody
here chose.

Doctrine note (as in the other guardrails here): every extractor asserts its own
output is non-trivial first, so a broken parse fails loudly instead of passing on
an empty surface.
"""
import re
import tomllib
from pathlib import Path

import pytest
import yaml

from backend.tests.architecture.test_ci_forks import CANONICAL, FORK, simulate
from backend.tests.architecture.test_ci_forks import events as fork_events
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


LINT = REPO_ROOT / ".github" / "workflows" / "lint.yml"
GITLEAKS_CONFIG = REPO_ROOT / ".gitleaks.toml"
GITLEAKS_RUN_RE = re.compile(r'\$\{?GITLEAKS\}?"?\s+(?:git|dir)\b[^\n]*')


def _secrets_job() -> dict:
    jobs = yaml.safe_load(LINT.read_text())["jobs"]
    scanning = [job for job in jobs.values() if any(
        GITLEAKS_RUN_RE.search(step.get("run", "")) for step in job.get("steps", [])
    )]
    assert len(scanning) == 1, f"lint.yml: expected one job running gitleaks, found {len(scanning)}"
    return scanning[0]


def _is_redacted(invocation: str) -> bool:
    words = invocation.split()
    return (
        "--redact" in words
        and "--ignore-gitleaks-allow" in words
        and "--config .gitleaks.toml" in invocation
    )


def test_the_secret_scan_runs_the_gitleaks_its_job_pins():
    """Fails when lint.yml stops scanning for secrets, or runs a gitleaks that
    no sha256 holds: a release asset its owner replaced would run in a job
    that reads the whole history, and the next secret pushed would be judged
    by whatever it then was. One pin, beside its version, checked before the
    archive is unpacked.
    """
    job = _secrets_job()
    # Every event lint.yml runs on, here and on a fork — a fork's schedule
    # apart, which test_ci_forks.py holds to running nothing.
    name = next(n for n, j in yaml.safe_load(LINT.read_text())["jobs"].items() if j == job)
    for event in fork_events(LINT):
        for repository in (CANONICAL, FORK):
            if (event, repository) == ("schedule", FORK):
                continue
            jobs, _ = simulate(LINT, event, repository)
            assert jobs[(LINT.name, name)].must, f"the secret scan does not run on {repository}'s {event}"
    assert not job.get("continue-on-error"), "a scan that may fail without failing the run blocks nothing"
    assert not [s for s in job["steps"] if s.get("continue-on-error")], "a secrets step may not fail open"
    env = job.get("env", {})
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(env.get("GITLEAKS_VERSION", ""))), env
    assert re.fullmatch(r"[0-9a-f]{64}", str(env.get("GITLEAKS_SHA256", ""))), env

    script = "\n".join(step.get("run", "") for step in job["steps"])
    download = script.find("releases/download/v${GITLEAKS_VERSION}/")
    check = script.find('echo "${GITLEAKS_SHA256}  ${asset}" | sha256sum -c -')
    unpack = script.find('tar -xzf "$asset"')
    first_run = GITLEAKS_RUN_RE.search(script).start()
    assert -1 < download < check < unpack < first_run, (
        "the gitleaks archive must be downloaded by its pinned version and checked "
        "against GITLEAKS_SHA256 before it is unpacked and run"
    )

    others = [p.name for p in WORKFLOWS if p != LINT and "gitleaks" in p.read_text()]
    assert not others, f"gitleaks is pinned in lint.yml's secrets job only, also found in {others}"


def test_every_gitleaks_run_is_redacted_and_reads_the_repository_config():
    """Fails when a gitleaks invocation drops `--redact`, which prints the value
    of every finding into a log anyone with read access keeps for 90 days,
    stops reading .gitleaks.toml, whose allowlist the tree's public names need,
    or honors inline `gitleaks:allow` comments, which would quiet a line
    without that file ever seeing it. Fails too when a .gitleaksignore is
    tracked: gitleaks applies one it finds beside the scan, by fingerprint.
    """
    runs = [m.group(0) for step in _secrets_job()["steps"] for m in GITLEAKS_RUN_RE.finditer(step.get("run", ""))]
    assert len(runs) >= 2, f"expected a range scan and a tree scan, found {runs}"
    assert all(_is_redacted(run) for run in runs), runs

    ignores = [p.relative_to(REPO_ROOT) for p in REPO_ROOT.rglob(".gitleaksignore") if "node_modules" not in p.parts]
    assert not ignores, f".gitleaks.toml is the only allowlist, found {ignores}"

    # The checks must discriminate before they are trusted.
    assert not _is_redacted('"$GITLEAKS" git . --config .gitleaks.toml --ignore-gitleaks-allow --verbose')
    assert not _is_redacted('"$GITLEAKS" git . --redact --ignore-gitleaks-allow')
    assert not _is_redacted('"$GITLEAKS" git . --config .gitleaks.toml --redact')
    assert GITLEAKS_RUN_RE.search('$GITLEAKS dir "$tree"')
    assert GITLEAKS_RUN_RE.search("${GITLEAKS} git .")
    assert not GITLEAKS_RUN_RE.search('echo "GITLEAKS=$RUNNER_TEMP/gitleaks" >> "$GITHUB_ENV"')


# Strings an allowlist regex must never match: anything a real secret could
# look like. Built at run time, so the scan itself never reads a token here.
_LEAK_SHAPES = ("Q7vX2mP9" * 5, "ghp_" + "Zr8kW3" * 6, "s3cr3t-" + "0a1b2c3d" * 4)


def _allowlist_problems(config: dict) -> list[str]:
    problems = []
    if set(config) - {"title", "extend", "allowlists"}:
        # `[[rules]]` redefines a default rule by its id, a global `[allowlist]`
        # quiets every rule.
        problems.append(f"unexpected top-level keys {sorted(set(config) - {'title', 'extend', 'allowlists'})}")
    if config.get("extend") != {"useDefault": True}:
        # `disabledRules` drops a rule whole, `path` extends another config.
        problems.append(f"[extend] must be exactly useDefault = true, found {config.get('extend')}")
    for entry in config.get("allowlists", []):
        name = entry.get("description")
        if not entry.get("targetRules"):
            problems.append(f"an allowlist quiets every rule: {name}")
        if not entry.get("regexes") or set(entry) - {"description", "targetRules", "regexes"}:
            problems.append(f"an allowlist may hold regexes only (no paths, commits, stopwords): {name}")
        for regex in entry.get("regexes", []):
            if not (regex.startswith("^") and regex.endswith("$")):
                problems.append(f"an allowlist regex must be anchored: {name}")
            elif any(re.search(regex, shape) for shape in _LEAK_SHAPES):
                problems.append(f"an allowlist regex lets a secret's shape through: {name}")
    return problems


def test_the_gitleaks_allowlist_stays_narrow():
    """Fails when .gitleaks.toml drops gitleaks' default rules, or quiets a
    whole rule, a path or a commit. The job reads only what an event adds, so a
    past finding needs no entry; a broad one would let the next real secret
    through, unseen, in the very file or rule it names.
    """
    config = tomllib.loads(GITLEAKS_CONFIG.read_text())
    assert config.get("allowlists"), "the extractor found no allowlist: is .gitleaks.toml still read this way?"
    assert _allowlist_problems(config) == []

    # The check must discriminate before it is trusted: each way gitleaks has
    # to silence a rule is refused.
    entry = {"description": "x", "targetRules": ["generic-api-key"], "regexes": ["^a$"]}
    narrow = {"title": "t", "extend": {"useDefault": True}, "allowlists": [entry]}
    assert _allowlist_problems(narrow) == []
    for drift in (
        {**narrow, "extend": {"useDefault": True, "disabledRules": ["generic-api-key"]}},
        {**narrow, "extend": {"useDefault": True, "path": "other.toml"}},
        {**narrow, "extend": {}},
        {**narrow, "rules": [{"id": "github-pat", "regex": "a^"}]},
        {**narrow, "allowlist": {"regexes": ["^a$"]}},
        {**narrow, "allowlists": [{**entry, "targetRules": []}]},
        {**narrow, "allowlists": [{**entry, "paths": ["backend/"]}]},
        {**narrow, "allowlists": [{**entry, "commits": ["0" * 40]}]},
        {**narrow, "allowlists": [{**entry, "regexes": ["a"]}]},
        {**narrow, "allowlists": [{**entry, "regexes": ["^.*$"]}]},
    ):
        assert _allowlist_problems(drift), drift
