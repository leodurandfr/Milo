"""Structural guardrail: a Milō app update is a trial the server can end on its own.

Three pieces share the job and no import connects them: the updater
(`backend/core/updates/update.py`) writes the journal whose names
`backend/core/updates/milo_journal.py` declares, the guard
(`rootfs/usr/local/bin/milo-update-guard`, a standalone stdlib script) reads it
and rolls back, and the unit (`system/milo-backend.service`) runs the guard
before every start. Each is read here from source.

What makes this worth a guardrail rather than a unit test: the updater of
release N hands the journal to the guard and unit of release N+1, so a name
moved on one side is a rollback that never happens, found on a unit that no
longer starts. The guard's behaviour is tests/test_update_guard.py's.

Doctrine note: every extractor asserts its own output is non-trivial first.
"""
import ast
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from backend.config.constants import DEPLOY_UPDATE_CMD
from backend.core.updates import milo_journal
from backend.core.updates.catalog import PROGRAMS

REPO_ROOT = Path(__file__).resolve().parents[3]
JOURNAL_MODULE = REPO_ROOT / "backend" / "core" / "updates" / "milo_journal.py"
GUARD = REPO_ROOT / "rootfs" / "usr" / "local" / "bin" / "milo-update-guard"
BACKEND_UNIT = REPO_ROOT / "system" / "milo-backend.service"
INHIBIT_UNIT = REPO_ROOT / "system" / "milo-update-inhibit.service"

# The one ExecStartPre line release N+1's unit must carry for release N's
# journal to be read at all. Frozen: the updater's tests and the docs name it.
GUARD_LINE = "ExecStartPre=-/usr/local/bin/milo-update-guard start"

# Contract names the guard does not restate, each with its reason.
NOT_THE_GUARDS = {
    "HOLD_S": "the arriving backend's clock: it calls `commit` once it has held",
    "GUARD": "the guard's own path, held against the unit and the tree below",
    "KEY_VENV_KEPT": "the guard trusts the kept copy on disk, which a cut copy cannot fake",
    "KEY_POST_COMMIT_RUNS": "the post-commit phase is the arriving backend's, past the guard's last act",
    "KEY_POST_COMMIT_AT": "the post-commit phase is the arriving backend's, past the guard's last act",
    "KEY_QOBUZ_RELOCKED": "the post-commit phase is the arriving backend's, past the guard's last act",
    "KEY_GUARD_RETRY_BOOT": "the backend's own record of having restarted into the guard once this boot",
    "KEY_DISMISSED": "the banner's state, set by the backend; a record the guard writes is new, so not dismissed",
}


def _constants(path: Path) -> dict:
    """Module-level `NAME = <literal>` assignments."""
    found = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                found[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return found


def _sections(unit: Path) -> dict[str, list[str]]:
    """Each section's non-comment lines, as written."""
    sections: dict[str, list[str]] = {}
    current = None
    for raw in unit.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        header = re.fullmatch(r"\[([^\]]+)\]", line)
        if header:
            current = sections.setdefault(header.group(1), [])
        elif current is not None:
            current.append(line)
    return sections


def _directives(lines: list[str], key: str) -> list[str]:
    return [line.split("=", 1)[1] for line in lines if line.split("=", 1)[0] == key]


def test_the_guard_reads_the_names_the_updater_writes():
    """Restated literally, since the guard imports nothing from the backend:
    a name or a step renamed on one side is a journal the other never reads."""
    contract = {
        name: value for name, value in _constants(JOURNAL_MODULE).items() if name.isupper()
    }
    guard = _constants(GUARD)
    assert len(contract) >= 30 and {"STEPS", "STATE_DIR", "MAX_ATTEMPTS"} <= set(contract), (
        f"milo_journal.py not read: {sorted(contract)}"
    )
    assert len(guard) >= 30, f"the guard's constants not read: {sorted(guard)}"
    stale = sorted(set(NOT_THE_GUARDS) - set(contract))
    assert not stale, f"NOT_THE_GUARDS excuses names the contract no longer declares: {stale}"

    shared = sorted(set(contract) - set(NOT_THE_GUARDS))
    missing = [name for name in shared if name not in guard]
    differ = {name: (contract[name], guard[name]) for name in shared if name in guard and guard[name] != contract[name]}
    assert not missing, f"the guard does not restate {missing}"
    assert differ == {}, f"contract vs guard: {differ}"


def test_the_guard_runs_every_restore_step():
    """RESTORE_STEPS names the steps a rollback_failed record can carry; the
    guard's table runs them, and nothing else, in that order."""
    table = None
    for node in ast.parse(GUARD.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "STEP_FUNCTIONS":
            table = [ast.literal_eval(key) for key in node.value.keys]
    assert table, "STEP_FUNCTIONS not read from the guard"

    assert tuple(table) == milo_journal.RESTORE_STEPS


def test_the_backend_unit_runs_the_guard_before_every_start():
    """Without it nothing reads the journal, and a release that dies on start
    crashloops under Restart=always for good. `-` so a failing guard can never
    keep the backend down."""
    service = _sections(BACKEND_UNIT).get("Service", [])
    assert _directives(service, "ExecStart"), f"[Service] not read: {service}"

    assert _directives(service, "ExecStartPre") == [GUARD_LINE.split("=", 1)[1]]
    assert GUARD_LINE == f"ExecStartPre=-{milo_journal.GUARD} start"


def test_the_backend_unit_is_started_only_once_it_listens():
    """A release that hangs before it listens must fail its start, or it is
    never counted and never rolled back: Type=notify (READY=1 from main.py's
    own process) bounded by TimeoutStartSec. No watchdog, by decision."""
    service = _sections(BACKEND_UNIT)["Service"]

    assert _directives(service, "Type") == ["notify"]
    assert _directives(service, "NotifyAccess") == ["main"]
    timeout = _directives(service, "TimeoutStartSec")
    assert len(timeout) == 1 and timeout[0].isdigit() and int(timeout[0]) > 0, timeout
    assert _directives(service, "WatchdogSec") == []


def test_the_guard_is_shipped_by_the_server_tree_and_runs_without_the_venv():
    """A unit naming a helper its tree does not carry fails its ExecStartPre
    (the `-` hides it, and no rollback ever runs). It runs from the unit as
    milo, so it must be executable; and it may have to put back the venv it
    would otherwise import from: the system python, isolated, stdlib only."""
    shipped = REPO_ROOT / "rootfs" / milo_journal.GUARD.lstrip("/")
    assert shipped == GUARD and GUARD.is_file(), f"{milo_journal.GUARD} is not in rootfs/"
    assert GUARD.stat().st_mode & 0o111, f"{GUARD} is not executable"

    text = GUARD.read_text(encoding="utf-8")
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(text))
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])
    }
    assert {"os", "json", "subprocess"} <= imported, f"imports not read: {imported}"
    assert text.splitlines()[0] == "#!/usr/bin/python3 -I"
    assert sorted(imported - set(sys.stdlib_module_names)) == []
    # compile(), not py_compile: that writes a __pycache__ into the rootfs tree,
    # which the next sync would install.
    compile(text, str(GUARD), "exec")


def test_the_guard_acts_on_the_checkout_and_the_helper_the_backend_uses():
    """The guard checks out, cleans and syncs the tree the unit runs, the one
    the updater updates, through the helper the backend's grant names."""
    guard = _constants(GUARD)
    service = _sections(BACKEND_UNIT)["Service"]
    assert {"REPO_DIR", "DEPLOY_UPDATE", "UNIT"} <= set(guard), sorted(guard)

    assert _directives(service, "WorkingDirectory") == [guard["REPO_DIR"]]
    assert PROGRAMS["milo"]["git_path"] == guard["REPO_DIR"]
    assert guard["DEPLOY_UPDATE"] == DEPLOY_UPDATE_CMD
    assert guard["UNIT"] == BACKEND_UNIT.name


@pytest.mark.skipif(os.geteuid() == 0, reason="the guard refuses to run as root")
def test_the_guard_never_touches_a_tree_without_a_journal(tmp_path):
    """A development checkout never has a journal, and is where a guard that
    acts without one would do its damage: a dirty tree on a branch, run
    through all three commands, must come out exactly as it went in — without
    git or sudo even being asked."""
    bin_dir, calls, repo = tmp_path / "bin", tmp_path / "calls", tmp_path / "repo"
    for path in (bin_dir, calls, repo):
        path.mkdir()
    for name in ("git", "sudo", "journalctl"):
        stub = bin_dir / name
        stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{calls}/{name}"\nexit 1\n')
        stub.chmod(0o755)
    git = [shutil.which("git"), "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
           "-c", "commit.gpgsign=false"]
    git_env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run([*git, "init", "-q", "-b", "dev"], check=True, env=git_env)
    (repo / "tracked.py").write_text("committed\n")
    subprocess.run([*git, "add", "-A"], check=True, env=git_env)
    subprocess.run([*git, "commit", "-q", "-m", "c"], check=True, env=git_env)
    (repo / "tracked.py").write_text("work in progress\n")
    (repo / "untracked.py").write_text("new\n")
    (tmp_path / "state").mkdir()
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    env = {
        "PATH": str(bin_dir),
        "HOME": str(tmp_path),
        "MILO_UPDATE_REPO_DIR": str(repo),
        "MILO_UPDATE_STATE_DIR": str(tmp_path / "state"),
        "MILO_UPDATE_BACKUP_DIR": str(tmp_path / "backups"),
        "MILO_UPDATE_JOURNALCTL": str(bin_dir / "journalctl"),
    }
    for command in ("start", "restore", "commit"):
        result = subprocess.run(
            [str(GUARD), command], env=env, capture_output=True, text=True, timeout=30, check=False
        )
        assert result.returncode == 0, f"{command}: {result.stderr}"

    assert sorted(calls.iterdir()) == [], "git, sudo or journalctl was run with no journal"
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


def test_the_update_inhibitor_is_bound_to_the_backend_and_started_by_nothing_else():
    """Holds off a shutdown while an update installs. Bound to the backend, so
    an updater that dies releases it; never ordered after it, because the
    backend (Type=notify) starts it, and a start ordered after a backend not yet
    READY waits for it forever; never enabled, since only an update may start
    it; and bounded, so the power key cannot stay dead if nothing stops it."""
    sections = _sections(INHIBIT_UNIT)
    unit, service = sections.get("Unit", []), sections.get("Service", [])
    exec_start = _directives(service, "ExecStart")
    assert exec_start, f"[Service] not read: {service}"

    assert _directives(unit, "BindsTo") == [BACKEND_UNIT.name]
    assert not any(BACKEND_UNIT.name in value.split() for value in _directives(unit, "After"))
    assert "Install" not in sections
    assert _directives(service, "User") == [], "logind grants a shutdown block only to root here"
    assert _directives(service, "RuntimeMaxSec")
    argv = exec_start[0].split()
    assert argv[0] == "/usr/bin/systemd-inhibit" and "--mode=block" in argv, argv
    what = next(arg for arg in argv if arg.startswith("--what=")).removeprefix("--what=").split(":")
    assert {"shutdown", "handle-power-key"} <= set(what), what
