"""Structural guardrail: a fork's CI checks its own tree, and spends and writes nothing else.

Anyone can fork the repository, and a fork runs its workflows with its own
Actions. Three things here act on the owner's behalf rather than check a tree:
the weekly schedules (a run nobody in the fork asked for), the Milo-Mac
freshness job (it opens and closes issues) and the image build's release (a
draft GitHub release, which only the repository units take their releases
from may publish). Each is guarded by `github.repository`; the checks a pull
request is judged by are not, so a fork still runs them on a push, a pull
request and a manual run.

GitHub cannot be asked what a condition does, so this file reads every `if:`
the workflows carry and evaluates it, as a fork and as this repository, for
every event each workflow is triggered by — called workflows through the job
that calls them. A value no event fixes (a job's output, a step's outcome) is
tried at every value it could take: "may run" means for one of them, "runs"
for all.

The image half is the same question asked of a build: `stage-milo` used to
clone a repository written into it, so a fork's image was this repository's,
and so was the origin every unit flashed from it fetches its updates' tags
from. The builders now name the repository they build from, through one
helper, which is run here for real.

Doctrine note: every extractor asserts its own output is non-trivial first,
and the evaluator is shown to discriminate before it is trusted.
"""
import itertools
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from backend.core.updates.catalog import PROGRAMS
from backend.tests.architecture.test_rootfs_deployment import STAGE_BUILDERS, STAGE_DIR

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))
LINT = WORKFLOW_DIR / "lint.yml"
BUILD_IMAGE = WORKFLOW_DIR / "build-image.yml"

# The repository whose releases every unit is offered: the one allowed to publish.
CANONICAL = PROGRAMS["milo"]["repo"]
FORK = "someone-else/Milo"


# --------------------------------------------------------------------------- #
# A reader for GitHub's expression language, the subset the workflows use.
# --------------------------------------------------------------------------- #

TOKEN_RE = re.compile(r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<op>&&|\|\||==|!=|!|\(|\))|(?P<name>[A-Za-z_][\w.-]*))")
LITERALS = {"true": True, "false": False, "null": None}
# What an unfixed value is tried at: empty, the two outputs, the two outcomes.
DOMAIN = ("", "true", "false", "success", "failure")


def _tokens(expr: str) -> list[tuple[str, str]]:
    expr = expr.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2]
    out, pos = [], 0
    while pos < len(expr.rstrip()):
        m = TOKEN_RE.match(expr, pos)
        if not m or m.end() == pos:
            raise ValueError(f"cannot read {expr!r} at {pos}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


def parse(expr: str):
    """An AST of tuples; precedence as GitHub's: `!`, then `==`/`!=`, `&&`, `||`."""
    tokens = _tokens(expr)
    pos = 0

    def peek():
        return tokens[pos][1] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def or_():
        left = and_()
        while peek() == "||":
            take()
            left = ("or", left, and_())
        return left

    def and_():
        left = eq()
        while peek() == "&&":
            take()
            left = ("and", left, eq())
        return left

    def eq():
        left = unary()
        while peek() in ("==", "!="):
            op = take()[1]
            left = (op, left, unary())
        return left

    def unary():
        if peek() == "!":
            take()
            return ("not", unary())
        return primary()

    def primary():
        if pos >= len(tokens):
            raise ValueError(f"{expr!r} ends early")
        kind, text = take()
        if text == "(":
            inner = or_()
            if take()[1] != ")":
                raise ValueError(f"unbalanced {expr!r}")
            return inner
        if kind == "str":
            return ("lit", text[1:-1].replace("''", "'"))
        if kind == "name":
            if peek() == "(":
                # success(), always(), contains()… would each need a model here.
                raise ValueError(f"{expr!r} calls {text}(), which this reader does not model")
            return ("lit", LITERALS[text]) if text in LITERALS else ("name", text)
        raise ValueError(f"unexpected {text!r} in {expr!r}")

    tree = or_()
    if pos != len(tokens):
        raise ValueError(f"trailing tokens in {expr!r}")
    return tree


def _names(tree) -> set[str]:
    if tree[0] == "name":
        return {tree[1]}
    if tree[0] == "lit":
        return set()
    return set().union(*(_names(child) for child in tree[1:]))


def _truthy(value) -> bool:
    return value not in ("", None, False, 0)


def _evaluate(tree, ctx: dict):
    op = tree[0]
    if op == "lit":
        return tree[1]
    if op == "name":
        return ctx[tree[1]]
    if op == "not":
        return not _truthy(_evaluate(tree[1], ctx))
    if op == "and":
        return _truthy(_evaluate(tree[1], ctx)) and _truthy(_evaluate(tree[2], ctx))
    if op == "or":
        return _truthy(_evaluate(tree[1], ctx)) or _truthy(_evaluate(tree[2], ctx))
    left, right = _evaluate(tree[1], ctx), _evaluate(tree[2], ctx)
    if isinstance(left, str) and isinstance(right, str):
        # GitHub compares strings ignoring case.
        same = left.casefold() == right.casefold()
    else:
        same = left == right
    return same if op == "==" else not same


@dataclass(frozen=True)
class Run:
    may: bool   # for one value of what the event leaves open
    must: bool  # for every one

    def __and__(self, other: "Run") -> "Run":
        return Run(self.may and other.may, self.must and other.must)


ALWAYS = Run(True, True)


def condition(expr, ctx: dict) -> Run:
    if expr is None:
        return ALWAYS
    if isinstance(expr, bool):
        return Run(expr, expr)
    tree = parse(str(expr))
    unknown = sorted(_names(tree) - ctx.keys())
    results = [
        _truthy(_evaluate(tree, {**ctx, **dict(zip(unknown, values))}))
        for values in itertools.product(DOMAIN, repeat=len(unknown))
    ]
    return Run(any(results), all(results))


# --------------------------------------------------------------------------- #
# The workflows, run on paper.
# --------------------------------------------------------------------------- #

def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def triggers(path: Path) -> list[str]:
    doc = _load(path)
    on = doc.get("on", doc.get(True))  # PyYAML reads the bare key `on` as True
    return list(on) if isinstance(on, dict) else [on] if isinstance(on, str) else list(on)


def _needs(job: dict) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def simulate(path: Path, event: str, repository: str, ref: str = "", gate: Run = ALWAYS, via: str = ""):
    """{(workflow, job): Run} and {(workflow, job, step): Run} for one event.

    `ref` is `inputs.ref`: empty unless a workflow calls this one, `gate` whether
    the calling job runs, `via` the caller, prefixed to a called workflow's
    name (`build-image.yml:checks>lint.yml`) so its jobs keep their own keys.
    """
    jobs = _load(path)["jobs"]
    ctx = {"github.event_name": event, "github.repository": repository, "inputs.ref": ref}
    job_runs: dict[str, Run] = {}

    def run_of(name: str) -> Run:
        if name not in job_runs:
            run = gate
            for need in _needs(jobs[name]):
                run = run & run_of(need)
            job_runs[name] = run & condition(jobs[name].get("if"), ctx)
        return job_runs[name]

    workflow = via + path.name
    out_jobs, out_steps = {}, {}
    for name, job in jobs.items():
        run = run_of(name)
        out_jobs[(workflow, name)] = run
        for index, step in enumerate(job.get("steps", [])):
            out_steps[(workflow, name, _label(step, index))] = run & condition(step.get("if"), ctx)
        called = job.get("uses", "")
        if called.startswith("./"):
            nested_jobs, nested_steps = simulate(
                REPO_ROOT / called[2:], event, repository, ref="<the ref being built>", gate=run,
                via=f"{workflow}:{name}>",
            )
            out_jobs.update(nested_jobs)
            out_steps.update(nested_steps)
    return out_jobs, out_steps


def _label(step: dict, index: int) -> str:
    return step.get("name") or step.get("uses") or f"step {index}"


def _own(key: tuple) -> tuple:
    """A key as the workflow that declares it spells it, whoever called it."""
    return (key[0].rsplit(">", 1)[-1], *key[1:])


def events(path: Path) -> list[str]:
    """What starts this workflow on its own; a call is evaluated through its caller."""
    return [e for e in triggers(path) if e != "workflow_call"]


GH_WRITE_RE = re.compile(r"\bgh\s+(?:issue|release|label|pr|api)\b")


def writes_to_github(step: dict) -> bool:
    """A step that changes something on GitHub besides the run's own artifacts."""
    return "action-gh-release" in str(step.get("uses", "")) or bool(GH_WRITE_RE.search(step.get("run", "")))


def writing_steps() -> set[tuple[str, str, str]]:
    found = set()
    for path in WORKFLOWS:
        for name, job in _load(path)["jobs"].items():
            for index, step in enumerate(job.get("steps", [])):
                if writes_to_github(step):
                    found.add((path.name, name, _label(step, index)))
    return found


# --------------------------------------------------------------------------- #
# The tests.
# --------------------------------------------------------------------------- #

def test_the_reader_reads_github_expressions_as_github_does():
    """Every assertion below is as good as this evaluator, so it must
    discriminate first — precedence, negation of an empty input, the
    case-insensitive comparison — and it must read every condition the
    workflows carry rather than skip one it does not understand."""
    ctx = {"a": "", "b": "x", "github.repository": CANONICAL}
    assert condition("!a", ctx) == ALWAYS
    assert condition("!b", ctx) == Run(False, False)
    assert condition("!a == b", ctx).must is False  # (!a) == b: true == 'x'
    assert condition("a == '' && b == 'X'", ctx) == ALWAYS
    assert condition(f"github.repository == '{CANONICAL.upper()}'", ctx) == ALWAYS
    assert condition("c == 'true' || b != 'x'", ctx) == Run(True, False)
    with pytest.raises(ValueError):
        condition("always()", ctx)

    conditions = [
        str(cond)
        for path in WORKFLOWS
        for job in _load(path)["jobs"].values()
        for cond in [job.get("if"), *(step.get("if") for step in job.get("steps", []))]
        if cond is not None
    ]
    assert len(conditions) >= 6, f"only {len(conditions)} conditions read from {[p.name for p in WORKFLOWS]}"
    for cond in conditions:
        parse(cond)


def test_the_extractors_see_the_workflows_and_their_writes():
    assert {LINT, BUILD_IMAGE} <= set(WORKFLOWS), WORKFLOWS
    assert "schedule" in triggers(LINT) and "schedule" in triggers(BUILD_IMAGE)
    assert {"push", "pull_request"} <= set(triggers(LINT)), triggers(LINT)
    writes = writing_steps()
    assert any(w[0] == BUILD_IMAGE.name for w in writes), f"no release step found: {writes}"
    assert sum(w[0] == LINT.name for w in writes) >= 2, f"the freshness job's issue steps are not seen: {writes}"
    assert not writes_to_github({"run": "gh --version"}) and writes_to_github({"run": "gh issue list"})


@pytest.mark.parametrize("path", [p for p in WORKFLOWS if "schedule" in triggers(p)], ids=lambda p: p.name)
def test_a_fork_spends_no_schedule(path):
    """Fails when a scheduled run starts a job on a fork: a weekly run, or a
    three-hour image build, its owner never asked for, on their minutes."""
    jobs, _ = simulate(path, "schedule", FORK)
    assert jobs, f"no job read from {path.name}"
    running = sorted(f"{wf}:{job}" for (wf, job), run in jobs.items() if run.may)
    assert not running, f"on a fork's schedule these jobs still run: {running}"


@pytest.mark.parametrize("path", [p for p in WORKFLOWS if "schedule" in triggers(p)], ids=lambda p: p.name)
def test_this_repository_keeps_its_schedule(path):
    """The guard must name this repository, as the catalog does: spelled any
    other way, it switches the owner's own schedule off, with nothing to say so.
    Every job runs on it except where a value no event fixes decides."""
    jobs, _ = simulate(path, "schedule", CANONICAL)
    own = [key for key in jobs if key[0] == path.name]
    assert own, f"no job read from {path.name}"
    # A called workflow's own guards (an image build skips the freshness job) are its business.
    skipped = sorted(f"{wf}:{job}" for (wf, job) in own if not jobs[(wf, job)].may)
    assert not skipped, f"on this repository's schedule these jobs never run: {skipped}"


@pytest.mark.parametrize("event", sorted(set(events(LINT)) - {"schedule"}))
def test_a_fork_still_runs_the_checks(event):
    """Fails when a fork's push or pull request skips a check: the checks are
    what a pull request from that fork is judged by. Every lint.yml job that
    writes nothing to GitHub is a check."""
    lint = _load(LINT)["jobs"]
    checks = [name for name, job in lint.items() if not any(writes_to_github(s) for s in job.get("steps", []))]
    assert {"backend", "frontend"} <= set(checks), checks
    jobs, _ = simulate(LINT, event, FORK)
    skipped = [name for name in checks if not jobs[(LINT.name, name)].must]
    assert not skipped, f"a fork's {event} does not run {skipped}"


def test_a_fork_writes_nothing_to_github():
    """Fails when a step that opens an issue or publishes a release can run on
    a fork, for any event: the issue would track a snapshot only this
    repository refreshes, and a draft release beside a fork's tags looks like
    one of the releases units are offered."""
    writes = writing_steps()
    may_write = sorted(
        f"{event}: {'/'.join(key)}"
        for path in WORKFLOWS
        for event in events(path)
        for key, run in simulate(path, event, FORK)[1].items()
        if _own(key) in writes and run.may
    )
    assert not may_write, "on a fork:\n" + "\n".join(may_write)


def test_this_repository_still_writes_what_it_writes():
    """The other half: a guard that disables a write outright passes the test
    above. Each writing step must still be reachable here, for some event."""
    writes = writing_steps()
    reachable = {
        _own(key)
        for path in WORKFLOWS
        for event in events(path)
        for key, run in simulate(path, event, CANONICAL)[1].items()
        if _own(key) in writes and run.may
    }
    assert writes - reachable == set(), f"never run on {CANONICAL}: {sorted(writes - reachable)}"


GUARD_RE = re.compile(r"github\.repository\s*[!=]=\s*'([^']*)'")


def test_every_guard_names_the_repository_the_catalog_does():
    """Fails when a guard spells the repository another way. The schedule and
    write tests above catch it on the jobs they cover; a guard on a job that
    neither schedules nor writes is held only here."""
    guards = [
        (path.name, name)
        for path in WORKFLOWS
        for cond in re.findall(r"^\s*(?:-\s*)?if:\s*(.+)$", path.read_text(), re.MULTILINE)
        for name in GUARD_RE.findall(cond)
    ]
    assert len(guards) >= 6, f"only {len(guards)} repository guards read: {guards}"
    wrong = sorted({g for g in guards if g[1].lower() != CANONICAL.lower()})
    assert not wrong, f"guards naming another repository than {CANONICAL}: {wrong}"


# --------------------------------------------------------------------------- #
# What the image clones.
# --------------------------------------------------------------------------- #

CHECKOUT_ENV = "milo-checkout.env"
HELPER = REPO_ROOT / "pi-gen" / "checkout-env.sh"
INSTALLER = STAGE_DIR / "02-install-milo" / "00-run.sh"
URL_RE = re.compile(r"https?://\S+|git@[\w.-]+:\S+")


def _code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_the_stage_names_no_repository_of_its_own():
    """Fails when stage-milo writes a repository into itself again: every
    fork's image would clone that one instead of the fork, and keep it as the
    origin every unit flashed from it fetches its updates' tags from."""
    code = _code(INSTALLER.read_text())
    assert "git clone $(printf '%q' \"${MILO_REPO}\")" in code, "the installer no longer clones MILO_REPO"
    assert f'/../{CHECKOUT_ENV}"' in code, f"the installer does not read MILO_REPO from {CHECKOUT_ENV}"
    urls = sorted({url for script in STAGE_DIR.rglob("*.sh") for url in URL_RE.findall(_code(script.read_text()))
                   if "github.com" in url and "Milo" in url})
    assert not urls, f"stage-milo names a Milō repository itself: {urls}"


@pytest.mark.parametrize("builder", sorted(STAGE_BUILDERS))
def test_every_builder_writes_what_the_image_clones_with_the_one_helper(builder):
    """One helper decides the repository and refuses what a unit could not
    fetch from; a builder spelling its own `MILO_REPO=` line skips that."""
    text = _code(STAGE_BUILDERS[builder].read_text())
    calls = [line for line in text.splitlines() if "checkout-env.sh" in line]
    assert calls, f"{builder} does not call pi-gen/checkout-env.sh"
    # Written once, whatever the builder holds the helper's answer in meanwhile.
    writes = re.findall(r">\s*\S*stage-milo/" + re.escape(CHECKOUT_ENV), text)
    assert len(writes) == 1, f"{builder} writes stage-milo/{CHECKOUT_ENV} {len(writes)} times"
    assert not re.search(r"MILO_(?:REPO|BRANCH)=[^\n]*>>[^\n]*config", text), (
        f"{builder} still hands the stage a variable through pi-gen's config, which pi-gen never exports"
    )


def test_ci_names_the_repository_it_runs_in():
    """The workflow's repository argument is the run's own repository, never a
    literal: a fork's image is the fork's."""
    text = _code(BUILD_IMAGE.read_text())
    call = re.search(r'checkout-env\.sh\s+"\$\{MILO_REF\}"\s+"([^"]+)"', text)
    assert call, "the workflow's call to checkout-env.sh is not the expected shape"
    assert call.group(1) == "${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}.git", call.group(1)


def _helper_in(tmp_path: Path, origin: str | None) -> Path:
    """The helper inside a checkout whose origin is `origin`."""
    checkout = tmp_path / "checkout"
    (checkout / "pi-gen").mkdir(parents=True)
    shutil.copy2(HELPER, checkout / "pi-gen" / HELPER.name)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True, capture_output=True, timeout=30)
    if origin is not None:
        subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin", origin],
                       check=True, capture_output=True, timeout=30)
    return checkout / "pi-gen" / HELPER.name


def _sourced(env_text: str) -> dict[str, str]:
    """The file as the stage reads it: sourced by bash."""
    result = subprocess.run(
        ["bash", "-euc", 'eval "$1"; printf "%s\\n%s\\n" "$MILO_REPO" "$MILO_BRANCH"', "_", env_text],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    repo, branch = result.stdout.splitlines()
    return {"MILO_REPO": repo, "MILO_BRANCH": branch}


def _run(helper: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(helper), *args], capture_output=True, text=True, timeout=30,
                          env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"})


@pytest.mark.parametrize("origin, cloned", [
    ("git@github.com:someone/Fork.git", "https://github.com/someone/Fork.git"),
    ("ssh://git@github.com/someone/Fork.git", "https://github.com/someone/Fork.git"),
    ("https://github.com/someone/Fork.git", "https://github.com/someone/Fork.git"),
])
def test_a_local_build_clones_the_checkouts_own_repository(tmp_path, origin, cloned):
    """`./build.sh` with nothing set builds from the repository the checkout
    came from — over https, which the chroot and every flashed unit can fetch
    without a key."""
    result = _run(_helper_in(tmp_path, origin), "v9.8.7")
    assert result.returncode == 0, result.stderr
    assert _sourced(result.stdout) == {"MILO_REPO": cloned, "MILO_BRANCH": "v9.8.7"}


CLONE_BLOCK_RE = re.compile(r"^on_chroot << CHROOT\n(?:(?!^CHROOT$).)*?git clone.*?^CHROOT$", re.MULTILINE | re.DOTALL)


def _cloned_by_the_stage(tmp_path: Path, env_text: str) -> list[list[str]]:
    """The argv each `sudo` in the stage's clone block receives, the block run
    as pi-gen runs it: expanded by the stage's shell, then handed as text to
    the chroot's `bash -e` (on_chroot's `capsh ... -- -e`)."""
    blocks = CLONE_BLOCK_RE.findall(INSTALLER.read_text())
    assert len(blocks) == 1, f"the installer's clone block is not found once: {blocks}"
    (tmp_path / CHECKOUT_ENV).write_text(env_text)
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "sudo").write_text('#!/bin/bash\nprintf \'%s\\037\' "$@" >> "$SUDO_LOG"; printf \'\\036\' >> "$SUDO_LOG"\n')
    (stub / "sudo").chmod(0o755)
    log = tmp_path / "sudo.log"
    script = f'set -e\nsource "$1"\non_chroot() {{ bash -e; }}\n{blocks[0]}\n'
    result = subprocess.run(
        ["bash", "-c", script, "_", str(tmp_path / CHECKOUT_ENV)], capture_output=True, text=True, timeout=30,
        env={**os.environ, "PATH": f"{stub}:{os.environ['PATH']}", "SUDO_LOG": str(log)},
    )
    assert result.returncode == 0, result.stderr
    return [call.split("\037")[:-1] for call in log.read_text().split("\036")[:-1]]


@pytest.mark.parametrize("ref", ["feature/it's $(odd) ref", 'feat/a"b$(echo INJECTED)`id`'])
def test_a_named_repository_and_ref_survive_the_round_trip(tmp_path, ref):
    """The CI's argument wins over the checkout's origin, and a ref reaches
    git exactly as given, whatever it holds — through the file and through the
    stage's second shell, where a ref pasted bare would be run as code."""
    result = _run(_helper_in(tmp_path, "git@github.com:owner/Milo.git"), ref, "https://github.com/fork/Milo.git")
    assert result.returncode == 0, result.stderr
    assert _sourced(result.stdout) == {"MILO_REPO": "https://github.com/fork/Milo.git", "MILO_BRANCH": ref}
    assert _cloned_by_the_stage(tmp_path, result.stdout) == [
        ["-u", "milo", "git", "clone", "https://github.com/fork/Milo.git", "/home/milo/milo"],
        ["-u", "milo", "git", "-C", "/home/milo/milo", "checkout", "--force", ref],
    ]


@pytest.mark.parametrize("origin, args", [
    ("https://x-access-token:" + "abc123" * 4 + "@github.com/someone/Fork.git", ["main"]),
    ("https://someone@github.com/someone/Fork.git", ["main"]),
    ("git@gitlab.example:someone/Fork.git", ["main"]),
    ("/srv/git/milo.git", ["main"]),
    (None, ["main"]),
    ("https://github.com/someone/Fork.git", [""]),
    ("https://github.com/someone/Fork.git", ["main", "http://github.com/someone/Fork.git"]),
])
def test_what_a_unit_could_not_fetch_from_is_refused(tmp_path, origin, args):
    """A token in the URL would be the origin of every unit flashed from the
    image; a key-only remote, a path on the build host or no origin at all is
    a clone that fails mid-build. Refused before anything is fetched, and
    nothing is printed — a refused URL is never echoed back."""
    result = _run(_helper_in(tmp_path, origin), *args)
    assert result.returncode != 0, f"{origin} {args} was accepted: {result.stdout}"
    assert result.stdout == ""
    assert "abc123" not in result.stderr
