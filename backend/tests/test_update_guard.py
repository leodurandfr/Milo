"""`milo-update-guard`, run as a program against a server laid out under tmp_path.

The guard is the only rollback of a Milō app update: the release that leaves
writes a journal (backend/core/updates/milo_journal.py), and the guard reads it
before every start of milo-backend.service. Before it, an update cut by a power
loss or a release that died on start left the unit on a half-installed tree,
with the API that could have repaired it served by the app that no longer
started. Consumers: milo-backend.service (`ExecStartPre=-… start`), the
arriving backend (`commit`), and the Milō row of Settings › Updates, which
reads the outcome the guard records in last-update.json.

The outside world here: a real git repository holding two releases, and
stand-ins for sudo (which records the argv it was handed and runs nothing) and
journalctl (canned lines). PATH holds nothing else, so no test can reach the
real sudo, whose grant would install system files as root.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.core.updates import milo_journal as mj

GUARD = Path(__file__).resolve().parents[2] / "rootfs" / "usr" / "local" / "bin" / "milo-update-guard"

JOURNAL_LINES = [
    "milo-update-guard: trial of v0.2.0: start 1 of 2",
    "INFO:__main__:Waiting for services initialization to complete...",
    "Traceback (most recent call last):",
    '  File "/home/milo/milo/backend/main.py", line 15, in <module>',
    "ModuleNotFoundError: No module named 'zeroconf'",
    "milo-backend.service: Main process exited, code=exited, status=1/FAILURE",
    "milo-backend.service: Failed with result 'exit-code'.",
]

GIT = shutil.which("git")
CAT = shutil.which("cat")
RM = shutil.which("rm")

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="the guard refuses to run as root")


class Server:
    """A server's disk as `_update_milo_app` leaves it, step by step, updating
    v0.1.0 (OLD) to v0.2.0 (NEW)."""

    def __init__(self, root: Path):
        self.root = root
        self.repo = root / "repo"
        self.state = root / "state"
        self.backup = root / "backups" / "milo-app"
        self.data_dir = root / "var-lib-milo"
        self.bin = root / "bin"
        self.calls = root / "calls"
        for path in (self.repo, self.state, self.data_dir, self.bin, self.calls):
            path.mkdir(parents=True)
        self._stub("sudo", f'printf "%s\\n" "$*" >> "{self.calls}/sudo"\n'
                           f'if [ -f "{self.calls}/sudo.fail" ]; then\n'
                           f'  echo "sudo: a password is required" >&2; exit 1\nfi\n'
                           'echo "System files sync complete"\n')
        self._stub("git", f'printf "%s\\n" "$*" >> "{self.calls}/git"\n'
                          f'case "$*" in *"rev-parse --verify --quiet HEAD")\n'
                          f'  if [ -f "{self.calls}/head.fail" ]; then\n'
                          f'    "{RM}" "{self.calls}/head.fail"; echo "fatal: timed out" >&2; exit 128\n'
                          f'  fi ;;\nesac\n'
                          f'exec "{GIT}" "$@"\n')
        journal_text = root / "journalctl.txt"
        journal_text.write_text("\n".join(JOURNAL_LINES) + "\n")
        self._stub("journalctl", f'printf "%s\\n" "$*" >> "{self.calls}/journalctl"\n"{CAT}" "{journal_text}"\n')
        self.env = {
            "PATH": str(self.bin),
            "HOME": str(root),
            "MILO_UPDATE_REPO_DIR": str(self.repo),
            "MILO_UPDATE_STATE_DIR": str(self.state),
            "MILO_UPDATE_BACKUP_DIR": str(self.backup),
            "MILO_UPDATE_JOURNALCTL": str(self.bin / "journalctl"),
        }
        self.data = {
            "settings": self.data_dir / "settings.json",
            "podcast": self.data_dir / "podcast_data.json",
            "lyrics": self.data_dir / "lyrics_index.json",
        }

    def _stub(self, name: str, body: str) -> None:
        stub = self.bin / name
        stub.write_text("#!/bin/sh\n" + body)
        stub.chmod(0o755)

    # -- the outside world ------------------------------------------------- #

    def git(self, *args: str) -> str:
        return subprocess.run(
            [GIT, "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t",
             "-c", "commit.gpgsign=false", "-c", "advice.detachedHead=false", *args],
            capture_output=True, text=True, check=True,
            env={"PATH": os.environ["PATH"], "HOME": str(self.root), "GIT_CONFIG_NOSYSTEM": "1"},
        ).stdout.strip()

    def guard(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(GUARD), *args], env=self.env, capture_output=True, text=True, timeout=60, check=False,
        )

    def head_unreadable_once(self) -> None:
        """The next read of HEAD fails, as on a card that stalls past its timeout."""
        (self.calls / "head.fail").write_text("")

    def calls_to(self, name: str) -> list[str]:
        path = self.calls / name
        return path.read_text().splitlines() if path.exists() else []

    # -- the two releases --------------------------------------------------- #

    def releases(self):
        """v0.1.0 then v0.2.0, the second adding a module; HEAD back on v0.1.0,
        detached as on a unit."""
        self.git("init", "-q", "-b", "main")
        (self.repo / ".gitignore").write_text("venv/\n/frontend/dist/\n")
        (self.repo / "backend").mkdir()
        (self.repo / "frontend").mkdir()
        (self.repo / "frontend" / "package.json").write_text("{}\n")
        (self.repo / "backend" / "main.py").write_text("OLD\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "v0.1.0")
        self.git("tag", "v0.1.0")
        (self.repo / "backend" / "main.py").write_text("NEW\n")
        (self.repo / "backend" / "added.py").write_text("NEW\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "v0.2.0")
        self.git("tag", "v0.2.0")
        self.origin = self.git("rev-parse", "v0.1.0^{commit}")
        self.target = self.git("rev-parse", "v0.2.0^{commit}")
        self.git("checkout", "-q", "v0.1.0")
        return self

    def live(self):
        """The release the update replaces, as it runs before any update."""
        self.releases()
        self._tree(self.repo / "venv", "OLD")
        self._tree(self.repo / "frontend" / "dist", "OLD")
        self.data["settings"].write_text('{"v": "OLD"}')
        self.data["podcast"].write_text('{"v": "OLD"}')
        return self

    @staticmethod
    def _tree(path: Path, marker: str) -> None:
        path.mkdir(parents=True)
        (path / "marker").write_text(marker)

    # -- what the updater does, in its order -------------------------------- #

    def open_journal(self, step="started", failure=None):
        self.backup.mkdir(parents=True, exist_ok=True)
        snapshot = self.backup / mj.DATA_SNAPSHOT_NAME
        snapshot.mkdir(exist_ok=True)
        files = []
        for index, (name, path) in enumerate(self.data.items()):
            copy = None
            if path.exists():
                copy = f"{index:02d}-{path.name}"
                shutil.copy2(path, snapshot / copy)
            files.append({mj.KEY_DATA_PATH: str(path), mj.KEY_DATA_COPY: copy})
        (snapshot / mj.DATA_MANIFEST_NAME).write_text(json.dumps({mj.KEY_DATA_FILES: files}))
        self.write_journal(step, failure)
        return self

    def write_journal(self, step, failure=None):
        (self.state / mj.JOURNAL_NAME).write_text(json.dumps({
            mj.KEY_ORIGIN_COMMIT: self.origin,
            mj.KEY_ORIGIN_TAG: "v0.1.0",
            mj.KEY_TARGET_TAG: "v0.2.0",
            mj.KEY_TARGET_COMMIT: self.target,
            mj.KEY_STEP: step,
            mj.KEY_FAILURE: failure,
            mj.KEY_VENV_KEPT: (self.backup / mj.KEPT_VENV_NAME).is_dir(),
        }))

    def check_out_target(self):
        self.git("checkout", "-q", "--force", "v0.2.0")

    def install(self, venv_kept=True):
        if venv_kept:
            shutil.copytree(self.repo / "venv", self.backup / mj.KEPT_VENV_PARTIAL_NAME, symlinks=True)
            os.rename(self.backup / mj.KEPT_VENV_PARTIAL_NAME, self.backup / mj.KEPT_VENV_NAME)
            (self.repo / "venv" / "marker").write_text("NEW")

    def swap_dist(self):
        os.rename(self.repo / "frontend" / "dist", self.backup / mj.KEPT_DIST_NAME)
        self._tree(self.repo / "frontend" / "dist", "NEW")

    def run_new_release(self):
        """What the new release writes once it runs: its data, and a file the
        previous release never had."""
        self.data["settings"].write_text('{"v": "NEW"}')
        self.data["lyrics"].write_text('{"v": "NEW"}')

    def up_to(self, step: str, venv_kept=True):
        """The disk once the update has recorded `step`, as it does after each."""
        reached = mj.STEPS.index(step)
        self.open_journal("started")
        if reached >= mj.STEPS.index("checked_out"):
            self.check_out_target()
        if reached >= mj.STEPS.index("installed"):
            self.install(venv_kept)
        if reached >= mj.STEPS.index("swapped"):
            self.swap_dist()
        self.write_journal(step)
        return self

    def on_trial(self, venv_kept=True):
        self.up_to(mj.TRIAL_STEP, venv_kept)
        self.run_new_release()
        return self

    # -- what is there ------------------------------------------------------ #

    def head(self) -> str:
        return self.git("rev-parse", "HEAD")

    def runs(self) -> tuple[str, str, str]:
        """(code, venv, frontend) — which release each one is."""
        return (
            (self.repo / "backend" / "main.py").read_text().strip(),
            (self.repo / "venv" / "marker").read_text(),
            (self.repo / "frontend" / "dist" / "marker").read_text(),
        )

    def journal(self) -> dict | None:
        path = self.state / mj.JOURNAL_NAME
        return json.loads(path.read_text()) if path.exists() else None

    def last_update(self) -> dict | None:
        path = self.state / mj.LAST_UPDATE_NAME
        return json.loads(path.read_text()) if path.exists() else None

    def attempts(self) -> int | None:
        path = self.state / mj.ATTEMPTS_NAME
        return int(path.read_text()) if path.exists() else None

    def status(self) -> str:
        return self.git("status", "--porcelain")


@pytest.fixture
def server(tmp_path):
    return Server(tmp_path).live()


def assert_rolled_back(server: Server, step: str) -> dict:
    assert server.head() == server.origin
    assert server.runs() == ("OLD", "OLD", "OLD")
    assert server.status() == ""
    assert server.journal() is None
    assert server.attempts() is None
    record = server.last_update()
    assert (record[mj.KEY_FROM], record[mj.KEY_TO]) == ("v0.1.0", "v0.2.0")
    assert (record[mj.KEY_OUTCOME], record[mj.KEY_STEP]) == ("rolled_back", step)
    assert server.calls_to("sudo") == ["-n /usr/local/bin/milo-deploy-update sync-system-files"], (
        "the previous release's system files are put back after the checkout back"
    )
    return record


# --------------------------------------------------------------------------- #
# Nothing to judge.
# --------------------------------------------------------------------------- #

def test_a_start_with_no_journal_changes_nothing(server):
    """Every start of the backend runs the guard: a unit that was never
    updated, or whose last update closed, must start exactly as before, and
    without even asking git."""
    result = server.guard("start")

    assert result.returncode == 0
    assert result.stderr == ""
    assert server.runs() == ("OLD", "OLD", "OLD")
    assert server.calls_to("git") == [] and server.calls_to("sudo") == []
    assert server.last_update() is None


def test_a_journal_naming_neither_side_of_head_is_closed_and_the_tree_left_alone(server):
    """Something moved the checkout since the journal was written (a hand
    checkout, an updater that writes none): what the update kept describes
    neither side, and putting it back would undo that move."""
    server.up_to("installed")
    server.git("checkout", "-q", "-b", "dev", "v0.2.0")
    (server.repo / "backend" / "dev.py").write_text("DEV\n")
    server.git("add", "-A")
    server.git("commit", "-q", "-m", "dev")
    (server.repo / "backend" / "main.py").write_text("WORK IN PROGRESS\n")

    result = server.guard("start")

    assert result.returncode == 0, result.stderr
    assert server.journal() is None
    assert server.git("rev-parse", "--abbrev-ref", "HEAD") == "dev"
    assert (server.repo / "backend" / "main.py").read_text() == "WORK IN PROGRESS\n"
    assert (server.repo / "venv" / "marker").read_text() == "NEW"
    assert server.calls_to("sudo") == []
    assert server.last_update() is None


def test_a_committed_journal_is_left_to_the_backend(server):
    """After the commit the backend finishes the dependency set and the
    satellites, then closes the journal; a start meanwhile changes nothing."""
    server.on_trial()
    server.write_journal(mj.COMMITTED_STEP)

    result = server.guard("start")

    assert result.returncode == 0, result.stderr
    assert server.runs() == ("NEW", "NEW", "NEW")
    assert server.journal()[mj.KEY_STEP] == mj.COMMITTED_STEP
    assert server.attempts() is None


# --------------------------------------------------------------------------- #
# An install that never reached its reboot.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("step", mj.STEPS[:mj.STEPS.index(mj.TRIAL_STEP)])
def test_an_install_cut_before_its_reboot_is_put_back_at_the_next_start(server, step):
    """A power cut, an OOM kill or a crash of the updating backend at any step:
    the next start runs the guard before any code, and it puts the previous
    release back whole instead of starting whatever half the tree holds."""
    server.up_to(step)

    result = server.guard("start")

    assert result.returncode == 0, result.stderr
    record = assert_rolled_back(server, step)
    assert step in record[mj.KEY_DETAIL]
    assert "ERROR: milo-update-guard: rolled back v0.2.0 -> v0.1.0" in result.stderr


def test_a_failure_the_updater_recorded_is_put_back_with_its_line(server):
    """The updater records a failed step and restarts itself; the row then
    shows the updater's own line, never a guess."""
    server.up_to("installed")
    server.write_journal("installed", failure="pip install failed: no matching distribution for zeroconf")

    server.guard("start")

    record = assert_rolled_back(server, "installed")
    assert record[mj.KEY_DETAIL] == "pip install failed: no matching distribution for zeroconf"


def test_a_frontend_swap_cut_before_its_step_was_recorded_is_put_back(server):
    """The swap moves the live dist aside before the journal says "swapped":
    cut there, the journal still says "synced" while the new frontend is live,
    and only the kept copy says what happened."""
    server.up_to("synced")
    server.swap_dist()

    server.guard("start")

    assert_rolled_back(server, "synced")


def test_a_cut_checkout_leaves_nothing_behind(server):
    """A checkout cut by a power loss leaves git's lock files, which refuse
    every later checkout, and files of the new release git never recorded.
    Both go, and the ignored venv and frontend stay."""
    server.up_to("started")
    (server.repo / "backend" / "main.py").write_text("NEW\n")
    (server.repo / "backend" / "added.py").write_text("NEW\n")
    git_dir = server.repo / ".git"
    for lock in ("index.lock", "HEAD.lock", "refs/tags/v0.2.0.lock"):
        (git_dir / lock).write_text("")

    result = server.guard("start")

    assert result.returncode == 0, result.stderr
    assert_rolled_back(server, "started")
    assert not (server.repo / "backend" / "added.py").exists()
    assert sorted(p.name for p in git_dir.rglob("*.lock")) == []


# --------------------------------------------------------------------------- #
# The trial.
# --------------------------------------------------------------------------- #

def test_a_new_release_gets_two_starts_and_the_third_runs_the_previous_one(server):
    """MAX_ATTEMPTS = 3: the boot into the update and the restart after its
    crash run the new code; the start after that runs the previous release."""
    server.on_trial()

    for start in (1, 2):
        result = server.guard("start")
        assert result.returncode == 0, result.stderr
        assert server.runs() == ("NEW", "NEW", "NEW"), f"start {start} must still run the new release"
        assert server.attempts() == start
        assert f"start {start} of 2" in result.stderr

    result = server.guard("start")

    assert result.returncode == 0, result.stderr
    record = assert_rolled_back(server, mj.TRIAL_STEP)
    assert record[mj.KEY_DETAIL] == "ModuleNotFoundError: No module named 'zeroconf'", (
        "the row shows the exception the starts ended on, read from the unit's journal for this boot"
    )
    assert "-u milo-backend.service -b" in server.calls_to("journalctl")[0]


def test_a_start_that_cannot_be_counted_puts_the_previous_release_back(server):
    """A full card: run uncounted, a release that keeps dying would never
    reach the start that puts the previous one back."""
    server.on_trial()
    (server.state / f"{mj.ATTEMPTS_NAME}.tmp").mkdir()

    server.guard("start")

    record = assert_rolled_back(server, mj.TRIAL_STEP)
    assert "could not be counted" in record[mj.KEY_DETAIL]


def test_a_rolled_back_update_is_never_tried_again_by_a_start(server):
    """One rollback per update: once the previous release is back, its own
    starts are not counted against anything."""
    server.on_trial()
    server.guard("restore", "the new release never answered")

    for _ in range(3):
        assert server.guard("start").returncode == 0

    assert server.runs() == ("OLD", "OLD", "OLD")
    assert server.attempts() is None
    assert server.last_update()[mj.KEY_DETAIL] == "the new release never answered"
    assert len(server.calls_to("sudo")) == 1


# --------------------------------------------------------------------------- #
# What the rollback puts back.
# --------------------------------------------------------------------------- #

def test_what_the_update_never_replaced_is_left_alone(server):
    """No venv is kept when requirements did not move, no dist before the swap:
    the rollback must not take the live ones with it, nor invent a copy."""
    server.up_to("synced", venv_kept=False)

    server.guard("start")

    assert server.runs() == ("OLD", "OLD", "OLD")
    assert not (server.backup / mj.DISCARD_NAME).exists()


def test_the_data_snapshot_is_put_back_and_files_the_new_release_created_removed(server):
    """A release that wrote its data in a schema the previous one does not
    take would have the previous one set it aside and start from its defaults
    (no migration): the data the update began with comes back, and a file it
    did not have goes."""
    server.on_trial()
    (server.state / mj.ATTEMPTS_NAME).write_text("2\n")

    server.guard("start")

    assert server.data["settings"].read_text() == '{"v": "OLD"}'
    assert server.data["podcast"].read_text() == '{"v": "OLD"}'
    assert not server.data["lyrics"].exists()


def test_a_snapshot_without_its_manifest_puts_no_data_back(server):
    """The manifest is written last: without it the snapshot never finished,
    and a partial copy would put back some files of one moment over others."""
    server.on_trial()
    (server.backup / mj.DATA_SNAPSHOT_NAME / mj.DATA_MANIFEST_NAME).unlink()

    server.guard("restore", "held nothing")

    assert server.runs() == ("OLD", "OLD", "OLD")
    assert server.data["settings"].read_text() == '{"v": "NEW"}'
    assert server.last_update()[mj.KEY_OUTCOME] == "rolled_back"


# --------------------------------------------------------------------------- #
# A rollback that cannot finish, and one that is cut.
# --------------------------------------------------------------------------- #

def test_a_rollback_that_fails_is_recorded_and_never_retried(server):
    """sudo refused: the code, venv, frontend and data are back, the system
    files are not. That is said, and the journal is closed, so the unit runs
    whatever starts rather than trying again on every start."""
    server.on_trial()
    (server.calls / "sudo.fail").write_text("")

    result = server.guard("restore", "the new release never answered")

    assert result.returncode == 1
    assert server.runs() == ("OLD", "OLD", "OLD")
    assert server.journal() is None
    record = server.last_update()
    assert (record[mj.KEY_OUTCOME], record[mj.KEY_STEP]) == ("rollback_failed", "system_files")
    assert "a password is required" in record[mj.KEY_DETAIL]

    server.guard("start")
    server.guard("start")
    assert len(server.calls_to("sudo")) == 1


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through a read-only directory")
def test_a_checkout_back_that_fails_stops_the_rollback_there(server):
    """Nothing after the checkout applies to a tree git could not put back:
    putting the previous venv under the new code would break both. A directory
    the account cannot write is how it happens (a git run as root)."""
    server.on_trial()
    backend = server.repo / "backend"
    backend.chmod(0o555)
    try:
        server.guard("restore", "the new release never answered")
    finally:
        backend.chmod(0o755)

    assert server.runs() == ("NEW", "NEW", "NEW")
    record = server.last_update()
    assert (record[mj.KEY_OUTCOME], record[mj.KEY_STEP]) == ("rollback_failed", "checkout")
    assert server.journal() is None
    assert server.calls_to("sudo") == []


def test_a_cut_rollback_is_resumed_not_counted(server):
    """A power cut inside the rollback: the live venv already moved aside, the
    kept one not yet back. Counting this start would run the new code on that
    half; the marker makes it finish the rollback, with the cause it began
    with."""
    server.on_trial()
    (server.state / mj.ATTEMPTS_NAME).write_text("1\n")
    server.git("checkout", "-q", "--force", "v0.1.0")
    (server.backup / mj.DISCARD_NAME).mkdir()
    os.rename(server.repo / "venv", server.backup / mj.DISCARD_NAME / "venv")
    (server.state / mj.ROLLING_BACK_NAME).write_text(json.dumps(
        {"detail": "the new release never answered", "runs": 1, "step": "venv"}
    ))

    result = server.guard("start")

    assert result.returncode == 0, result.stderr
    record = assert_rolled_back(server, mj.TRIAL_STEP)
    assert record[mj.KEY_DETAIL] == "the new release never answered"
    assert not (server.state / mj.ROLLING_BACK_NAME).exists()


def test_a_rollback_cut_on_every_run_is_given_up(server):
    """A step that outlasts TimeoutStartSec is cut on every start: after
    MAX_ATTEMPTS runs the rollback ends as failed instead of looping."""
    server.on_trial()
    (server.state / mj.ROLLING_BACK_NAME).write_text(json.dumps(
        {"detail": "the new release never answered", "runs": mj.MAX_ATTEMPTS, "step": "system_files"}
    ))

    result = server.guard("start")

    assert result.returncode == 1
    assert server.journal() is None
    record = server.last_update()
    assert (record[mj.KEY_OUTCOME], record[mj.KEY_STEP]) == ("rollback_failed", "system_files")
    assert server.calls_to("sudo") == []


@pytest.mark.parametrize("step, failure", [
    ("installed", None), ("rebooting", "pip check failed"),
], ids=["before-the-trial", "failure-recorded"])
def test_a_head_that_cannot_be_read_does_not_stop_a_rollback(server, step, failure):
    """A stalled card at the boot after a power cut: left open, the journal
    would hold the update screen over the half-installed tree the backend then
    starts on, with nothing ever acting on it again. The restore checks out the
    origin by hash and needs no HEAD."""
    server.up_to(step)
    server.write_journal(step, failure)
    server.head_unreadable_once()

    result = server.guard("start")

    assert result.returncode == 0, result.stderr
    assert_rolled_back(server, step)


def test_a_head_that_cannot_be_read_still_counts_a_trial_start(server):
    """Run uncounted, a release that keeps dying on a card that keeps stalling
    would never reach the start that puts the previous one back."""
    server.on_trial()
    server.head_unreadable_once()

    result = server.guard("start")

    assert result.returncode == 0, result.stderr
    assert server.attempts() == 1
    assert server.runs() == ("NEW", "NEW", "NEW")


def test_a_trial_is_not_committed_while_head_cannot_be_read(server):
    """Committing needs to see the release on trial in place; the backend
    tries again a minute later."""
    server.on_trial()
    server.head_unreadable_once()

    result = server.guard("commit")

    assert result.returncode == 1
    assert server.journal()[mj.KEY_STEP] == mj.TRIAL_STEP
    assert (server.backup / mj.KEPT_VENV_NAME).is_dir()


def test_an_unreadable_journal_is_closed_and_said(server):
    """A damaged card: left open, it would keep every power route refused
    (an update in progress) for good, and there is no origin to go back to."""
    server.up_to("installed")
    (server.state / mj.JOURNAL_NAME).write_text('{"origin_commit": ')

    result = server.guard("start")

    assert result.returncode == 1
    assert server.journal() is None
    assert server.last_update()[mj.KEY_OUTCOME] == "rollback_failed"
    assert server.runs() == ("NEW", "NEW", "OLD")
    assert "ERROR: milo-update-guard:" in result.stderr


# --------------------------------------------------------------------------- #
# The commit.
# --------------------------------------------------------------------------- #

def test_commit_keeps_the_new_release_and_drops_what_was_kept(server):
    """The arriving backend commits once it has held: the journal says so
    first, then the kept venv, frontend and data copy free the card."""
    server.on_trial()
    server.guard("start")

    result = server.guard("commit")

    assert result.returncode == 0, result.stderr
    assert server.runs() == ("NEW", "NEW", "NEW")
    assert server.journal()[mj.KEY_STEP] == mj.COMMITTED_STEP
    assert server.attempts() is None
    assert sorted(p.name for p in server.backup.iterdir()) == []
    assert server.last_update() is None, "the backend records the outcome once its post-commit phase ends"

    for _ in range(3):
        server.guard("start")
    assert server.runs() == ("NEW", "NEW", "NEW")


def test_commit_outside_a_trial_changes_nothing(server):
    """The release that installs this guard is applied by an updater that
    writes no journal, and an install still in progress is not on trial."""
    assert server.guard("commit").returncode == 0
    assert server.journal() is None

    server.up_to("installed")
    server.guard("commit")

    assert server.journal()[mj.KEY_STEP] == "installed"
    assert (server.backup / mj.KEPT_VENV_NAME).is_dir()
