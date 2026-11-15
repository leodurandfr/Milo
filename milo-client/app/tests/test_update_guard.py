"""`milo-client-update-guard`, run for real against a satellite laid out under tmp_path.

The guard decides how a pushed app version's trial ends. Before it, a version
that died on start crashlooped for good under `Restart=always`: the tree it
replaced was deleted before the restart, the room stayed mute, and the update
API that could have repaired it was served by the very app that no longer
started. Consumers: milo-client.service (`ExecStartPre=-… start`), the updater
(`restore`, app/services/app_update.py) and the new process (`commit`, main.py);
the server reads the outcome from `GET /status` → `app.last_update`.

The outside world here is systemd's journal, stood in for by a `journalctl`
that prints canned lines.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

GUARD = (
    Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-client-update-guard"
)

JOURNAL = [
    "Starting app update deployment (payload: NEW, version: v0.2.0)",
    "Traceback (most recent call last):",
    '  File "/home/milo-client/repo/milo-client/app/main.py", line 15, in <module>',
    "ModuleNotFoundError: No module named 'zeroconf'",
    "milo-client.service: Main process exited, code=exited, status=1/FAILURE",
    "milo-client.service: Failed with result 'exit-code'.",
]


class Satellite:
    """A satellite's disk as the updater leaves it during a trial of NEW over OLD."""

    def __init__(self, root: Path):
        self.repo = root / "repo"
        self.state = root / "state"
        self.real_venv = root / "real" / "venv"
        self.venv = root / "venv"
        journal = root / "journal.txt"
        journal.write_text("\n".join(JOURNAL) + "\n")
        stub = root / "journalctl"
        stub.write_text(f'#!/bin/sh\ncat "{journal}"\n')
        stub.chmod(0o755)
        self.env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "MILO_CLIENT_REPO_DIR": str(self.repo),
            "MILO_CLIENT_STATE_DIR": str(self.state),
            "MILO_CLIENT_VENV": str(self.venv),
            "MILO_CLIENT_JOURNALCTL": str(stub),
        }
        self.state.mkdir()
        self.real_venv.parent.mkdir()
        self.venv.symlink_to(self.real_venv)

    def live(self):
        """The version a trial replaces, as it sits on a unit before any push."""
        for tree in ("app", "configs"):
            self._tree(self.repo / tree, "OLD")
        self._tree(self.real_venv, "OLD")
        (self.state / "app-version").write_text("v0.1.0")
        (self.state / "app-payload").write_text("OLD")
        return self

    def on_trial(self, venv_kept=True):
        """What the updater leaves once NEW is swapped in and restarting."""
        for tree in ("app", "configs"):
            os.rename(self.repo / tree, self.repo / f"{tree}.old")
            self._tree(self.repo / tree, "NEW")
        if venv_kept:
            os.rename(self.real_venv, self.real_venv.with_name("venv.old"))
            self._tree(self.real_venv, "NEW")
        for name in ("app-version", "app-payload"):
            (self.state / f"{name}.old").write_text((self.state / name).read_text())
        (self.state / "app-version").write_text("v0.2.0")
        (self.state / "app-payload").write_text("NEW")
        (self.state / "update-trial").write_text("NEW\n")
        (self.state / "update-trial.attempts").write_text("0\n")
        return self

    @staticmethod
    def _tree(path: Path, marker: str):
        path.mkdir(parents=True)
        (path / "marker").write_text(marker)

    def guard(self, *args):
        return subprocess.run(
            [str(GUARD), *args], env=self.env, capture_output=True, text=True, timeout=30, check=False
        )

    def runs(self, tree: str) -> str:
        return (self.repo / tree / "marker").read_text()

    def venv_runs(self) -> str:
        return (self.real_venv / "marker").read_text()

    def last_update(self):
        path = self.state / "last-update.json"
        return json.loads(path.read_text()) if path.exists() else None

    def trial_open(self) -> bool:
        return (self.state / "update-trial").exists()

    def leftovers(self):
        return sorted(
            str(p.relative_to(p.parents[1])) for p in self.repo.parent.rglob("*")
            if p.name.endswith((".old", ".new", ".discard", ".tmp"))
        )


@pytest.fixture
def satellite(tmp_path):
    return Satellite(tmp_path).live()


def test_a_start_with_no_trial_changes_nothing(satellite):
    """Every start of the unit runs the guard; a unit that was never pushed, or
    whose last push committed, must start exactly as before."""
    result = satellite.guard("start")

    assert result.returncode == 0
    assert result.stderr == ""
    assert satellite.runs("app") == "OLD"
    assert not (satellite.state / "update-trial.attempts").exists()
    assert satellite.last_update() is None


def test_a_new_version_gets_two_starts_and_the_third_runs_the_old_one(satellite):
    """MAX_ATTEMPTS = 3: the restart into the push and the one systemd makes
    after its crash run the new code; the start after that runs the old."""
    satellite.on_trial()

    for start in (1, 2):
        result = satellite.guard("start")
        assert result.returncode == 0, result.stderr
        assert satellite.runs("app") == "NEW", f"start {start} must still run the new version"
        assert satellite.trial_open()

    result = satellite.guard("start")

    assert result.returncode == 0, result.stderr
    assert (satellite.runs("app"), satellite.runs("configs"), satellite.venv_runs()) == ("OLD",) * 3
    assert (satellite.state / "app-payload").read_text() == "OLD"
    assert (satellite.state / "app-version").read_text() == "v0.1.0"
    assert not satellite.trial_open()
    assert not (satellite.state / "update-trial.attempts").exists()
    assert satellite.leftovers() == ["real/venv.discard", "repo/app.discard", "repo/configs.discard"], (
        "inside TimeoutStartSec the replaced trees are only renamed; the next update deletes them"
    )
    assert satellite.venv.is_symlink(), "the venv link the unit's ExecStart names must survive"


def test_a_rollback_records_why_from_the_journal(satellite):
    """The server shows this line under the satellite's row: the exception a
    traceback ends on, never the systemd summary printed after it."""
    satellite.on_trial()
    (satellite.state / "update-trial.attempts").write_text("2\n")

    result = satellite.guard("start")

    record = satellite.last_update()
    assert record["payload"] == "NEW"
    assert record["outcome"] == "rolled_back"
    assert record["detail"] == "ModuleNotFoundError: No module named 'zeroconf'"
    assert isinstance(record["at"], int)
    assert "ERROR:" in result.stderr and "rolled back from NEW" in result.stderr


def test_a_rollback_with_no_exception_names_what_systemd_saw(satellite):
    """A start that never got READY leaves no traceback: systemd's result line
    says it timed out, where the "Failed to start" printed after it names only
    the unit — which is what the row showed on Bureau before this rule."""
    satellite.on_trial()
    (satellite.state / "update-trial.attempts").write_text("2\n")
    (satellite.repo.parent / "journal.txt").write_text("\n".join([
        "milo-client.service: start operation timed out. Terminating.",
        "milo-client.service: State 'stop-sigterm' timed out. Killing.",
        "milo-client.service: Failed with result 'timeout'.",
        "Failed to start milo-client.service - Milo Client API Service.",
    ]) + "\n")

    satellite.guard("start")

    assert satellite.last_update()["detail"] == "milo-client.service: Failed with result 'timeout'."


def test_a_missing_app_is_put_back_at_the_first_start(satellite):
    """A power cut between the swap's two renames leaves no app/ at all: the
    unit cannot even run the new code once, so waiting for three starts is
    three failed starts of a mute room."""
    satellite.on_trial()
    os.rename(satellite.repo / "app", satellite.repo / "app.new")

    satellite.guard("start")

    assert satellite.runs("app") == "OLD"
    assert not satellite.trial_open()
    assert satellite.last_update()["outcome"] == "rolled_back"
    assert not [p for p in satellite.leftovers() if p.endswith(".old")]


def test_a_staging_tree_left_behind_is_put_back_at_the_first_start(satellite):
    """A `.new` tree on disk is a copy that never finished: the swap was cut."""
    satellite.on_trial()
    (satellite.repo / "configs.new").mkdir()

    satellite.guard("start")

    assert satellite.runs("configs") == "OLD"
    assert not satellite.trial_open()


def test_an_update_cut_before_its_swap_is_closed_at_the_first_start(satellite):
    """The trial is open before anything is swapped: cut there, the unit still
    runs the old code, and a trial left open would refuse every push (409)
    until two more starts."""
    (satellite.state / "update-trial").write_text("NEW\n")
    for name in ("app-version", "app-payload"):
        (satellite.state / f"{name}.old").write_text((satellite.state / name).read_text())

    satellite.guard("start")

    assert satellite.runs("app") == "OLD"
    assert not satellite.trial_open()
    assert satellite.last_update()["outcome"] == "rolled_back"


def test_an_update_cut_before_its_identity_is_put_back_at_the_first_start(satellite):
    """Cut during pip, the import or the root deploy, the new app/ is in place
    but the identity still names the old payload. The new process commits only
    the payload it reads there, so such a trial would never close: every push
    refused (409) until two more starts rolled back a version that may hold."""
    satellite.on_trial()
    for name in ("app-version", "app-payload"):
        (satellite.state / name).write_text((satellite.state / f"{name}.old").read_text())

    result = satellite.guard("start")

    assert result.returncode == 0, result.stderr
    assert (satellite.runs("app"), satellite.venv_runs()) == ("OLD", "OLD")
    assert not satellite.trial_open()
    record = satellite.last_update()
    assert record["outcome"] == "rolled_back"
    assert "identity" in record["detail"]


def test_a_trial_left_by_an_updater_without_trials_is_closed_and_nothing_put_back(satellite):
    """A release from before the guard, pushed back and then updated by its own
    updater: that one deployed NEWER and wrote its identity without touching
    the trial. Rolling back would put the stale kept trees over that deploy."""
    satellite.on_trial()
    (satellite.state / "update-trial").write_text("RETRACTED\n")
    (satellite.state / "app-payload").write_text("NEWER")

    result = satellite.guard("start")

    assert result.returncode == 0, result.stderr
    assert (satellite.runs("app"), satellite.venv_runs()) == ("NEW", "NEW")
    assert (satellite.state / "app-payload").read_text() == "NEWER"
    assert not satellite.trial_open()
    assert satellite.last_update() is None


def test_a_start_that_cannot_be_counted_puts_the_previous_version_back(satellite):
    """A full card: run uncounted, a version that keeps dying would never
    reach the start that rolls it back, with app.old right there."""
    satellite.on_trial()
    (satellite.state / "update-trial.attempts.tmp").mkdir()

    result = satellite.guard("start")

    assert result.returncode == 0, result.stderr
    assert satellite.runs("app") == "OLD"
    assert not satellite.trial_open()
    assert "could not be counted" in satellite.last_update()["detail"]


def test_a_commit_that_cannot_be_recorded_still_ends_the_trial(satellite):
    """Recorded first, a full card kept the trial open over a version that
    held — refusing every push and rolled back two restarts later — and kept
    the copies that filled the card."""
    satellite.on_trial()
    (satellite.state / "last-update.json.tmp").mkdir()

    satellite.guard("commit")

    assert satellite.runs("app") == "NEW"
    assert not satellite.trial_open()
    assert [p for p in satellite.leftovers() if not p.endswith(".tmp")] == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root renames through a read-only directory")
def test_an_incomplete_rollback_is_finished_by_the_next_start(satellite):
    """A tree that could not be put back leaves the trial open for the next
    start to retry. Had the identity gone back meanwhile, that start would read
    a trial naming neither side of it, close it as left by an older updater,
    and keep the half-restored install for good."""
    satellite.on_trial()
    (satellite.state / "update-trial.attempts").write_text("2\n")
    satellite.real_venv.parent.chmod(0o555)
    try:
        assert satellite.guard("start").returncode == 1
    finally:
        satellite.real_venv.parent.chmod(0o755)
    assert satellite.trial_open()
    assert (satellite.state / "app-payload").read_text() == "NEW"

    satellite.guard("start")

    assert (satellite.runs("app"), satellite.venv_runs()) == ("OLD", "OLD")
    assert (satellite.state / "app-payload").read_text() == "OLD"
    assert not satellite.trial_open()
    assert satellite.last_update()["outcome"] == "rolled_back"


def test_restore_puts_back_what_the_updater_kept_with_its_detail(satellite):
    """The updater's own rollback: a step that failed after the swap."""
    satellite.on_trial()

    result = satellite.guard("restore", "pip install failed:", "no matching distribution")

    assert result.returncode == 0, result.stderr
    assert (satellite.runs("app"), satellite.venv_runs()) == ("OLD", "OLD")
    assert satellite.last_update()["detail"] == "pip install failed: no matching distribution"
    assert satellite.leftovers() == [], "no unit start to hurry: the replaced trees are deleted"


def test_a_venv_the_update_never_touched_is_left_alone(satellite):
    """No venv.old is kept when requirements.txt did not change; the rollback
    must not take the live venv with it."""
    satellite.on_trial(venv_kept=False)

    satellite.guard("restore", "import failed")

    assert satellite.venv_runs() == "OLD"
    assert satellite.runs("app") == "OLD"


def test_commit_drops_what_was_kept_and_keeps_the_new_version(satellite):
    """Committed is what lets the server call the push done, and what frees
    the disk of a unit nobody watches."""
    satellite.on_trial()

    result = satellite.guard("commit")

    assert result.returncode == 0, result.stderr
    assert (satellite.runs("app"), satellite.runs("configs"), satellite.venv_runs()) == ("NEW",) * 3
    assert (satellite.state / "app-payload").read_text() == "NEW"
    assert not satellite.trial_open()
    assert satellite.leftovers() == []
    record = satellite.last_update()
    assert (record["payload"], record["outcome"], record["detail"]) == ("NEW", "committed", None)


def test_a_start_after_a_commit_changes_nothing(satellite):
    """A reboot the day after: the trial is over, the new version stays."""
    satellite.on_trial()
    satellite.guard("commit")

    for _ in range(3):
        satellite.guard("start")

    assert satellite.runs("app") == "NEW"
    assert satellite.last_update()["outcome"] == "committed"


def test_commit_without_a_trial_writes_nothing(satellite):
    """The push that installs the guard is applied by the updater before it,
    which opens no trial: the new process's commit must not invent one."""
    result = satellite.guard("commit")

    assert result.returncode == 0
    assert satellite.last_update() is None
    assert satellite.runs("app") == "OLD"
