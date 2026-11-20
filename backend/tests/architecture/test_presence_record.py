"""The system half of a proof of presence: what a `milo` process cannot touch.

`core/system/presence.py` takes a power cut as the gesture on a unit with
neither an encoder nor a Pi 5's power button, and a start that finds a recorded
challenge from another boot as that power cut. It is a proof only because no
`milo` process can make the trace: the record is written by a root helper
(`milo-presence-record`) where only root writes, and cleared by the backend
unit's root `ExecStopPost=` at every stop, which systemd runs whatever ended
the process. A record any `milo` daemon could write — under /var/lib/milo, as
the first version had it — is a proof a compromised radio stream forges, with
a token of its choosing; a clean-stop marker the backend wrote itself is one a
process that kills it withholds.

The Pi 5's button is a proof only while logind holds the power key off: the
inhibitor unit's start is held to the lock being listed, or the press the
dialog asks for powers the unit off.

The helper is run here, as bash, against a scratch directory: its paths are
the only lines changed.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest

from backend.config.constants import MILO_DATA_DIR, PRESENCE_RECORD_CMD, PRESENCE_RECORD_FILE
from backend.tests.architecture.test_unit_hardening import milo_units, service_section

REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "rootfs" / PRESENCE_RECORD_CMD.lstrip("/")
BACKEND_UNIT = REPO_ROOT / "system" / "milo-backend.service"
INHIBIT_UNIT = REPO_ROOT / "system" / "milo-presence-inhibit.service"

DIGEST = "ab" * 32
BOOT = "0f7c1c58-5a8f-4a4e-9a1c-2d6b8e9f0a11"


def _assignment(text: str, name: str) -> str:
    match = re.search(rf'^{name}="([^"]+)"$', text, re.MULTILINE)
    assert match, f"{HELPER.name} declares no {name}= — the extractor is broken"
    return match.group(1)


@pytest.fixture
def helper(tmp_path):
    """The helper, its record directory and boot id moved under tmp_path."""
    text = HELPER.read_text()
    record_dir = tmp_path / "milo-presence"
    boot_id = tmp_path / "boot_id"
    boot_id.write_text(BOOT + "\n")
    text = text.replace(f'RECORD_DIR="{_assignment(text, "RECORD_DIR")}"', f'RECORD_DIR="{record_dir}"')
    text = text.replace(f'BOOT_ID_FILE="{_assignment(text, "BOOT_ID_FILE")}"', f'BOOT_ID_FILE="{boot_id}"')
    script = tmp_path / "milo-presence-record"
    script.write_text(text)

    def run(*args, stdin=""):
        return subprocess.run(
            ["bash", str(script), *args], input=stdin, capture_output=True, text=True, timeout=10,
        )

    run.record = record_dir / "challenge.json"
    return run


def test_the_helper_and_the_backend_name_one_record():
    text = HELPER.read_text()
    record = Path(_assignment(text, "RECORD_DIR")) / Path(
        _assignment(text, "RECORD").replace("$RECORD_DIR/", "")
    )
    assert record == PRESENCE_RECORD_FILE


def test_arm_records_the_challenge_with_the_boot_it_reads_itself(helper):
    result = helper("arm", stdin=f"Xy_9-abcdEFG ssh_open {DIGEST}\n")

    assert result.returncode == 0, result.stderr
    assert json.loads(helper.record.read_text()) == {
        "id": "Xy_9-abcdEFG", "action": "ssh_open", "token_sha256": DIGEST, "boot_id": BOOT,
    }


@pytest.mark.parametrize("line", [
    f'Xy_9-abcdEFG ssh_open {DIGEST} {BOOT}',          # a boot id of the caller's choosing
    f'Xy_9-abcdEFG ssh_open {DIGEST[:-1]}',             # not a digest
    f'Xy_9-abcdEFG ssh_open", "boot_id": "x {DIGEST}',  # JSON smuggled in a field
    f'Xy_9-abcdEFG Ssh_Open {DIGEST}',
    "",
])
def test_arm_writes_nothing_it_did_not_validate(helper, line):
    result = helper("arm", stdin=line + "\n")

    assert result.returncode != 0
    assert not helper.record.exists()


def test_clear_removes_the_record_and_any_other_argument_is_refused(helper):
    assert helper("arm", stdin=f"Xy_9-abcdEFG password {DIGEST}\n").returncode == 0

    assert helper("arm", "extra", stdin=f"Xy_9-abcdEFG password {DIGEST}\n").returncode != 0
    assert helper("clear", "now").returncode != 0
    assert helper("wipe").returncode != 0
    assert helper.record.exists()

    assert helper("clear").returncode == 0
    assert not helper.record.exists()
    assert helper("clear").returncode == 0, "nothing to clear is not a failure of the stop"


def test_every_stop_of_the_backend_clears_the_record_as_root():
    """`+` is root, whatever `User=` says: a hook as `milo` could not touch a
    record only root writes. ExecStopPost, not ExecStop: it runs after a crash
    or a SIGKILL too, which is what keeps a killed backend from reading, at its
    restart, like a power cut."""
    hooks = service_section(BACKEND_UNIT).get("ExecStopPost", [])

    assert f"+{PRESENCE_RECORD_CMD} clear" in hooks, hooks


def test_the_record_lives_where_no_milo_unit_can_write():
    """Every directory a `milo` unit is handed — its state, its HOME, the
    shared /var/lib/milo — is one a compromised daemon writes too."""
    bases = {"StateDirectory": "/var/lib", "RuntimeDirectory": "/run",
             "CacheDirectory": "/var/cache", "LogsDirectory": "/var/log"}
    writable = {MILO_DATA_DIR, Path("/home/milo"), Path("/tmp"), Path("/var/tmp"), Path("/run/milo")}
    for directives in milo_units().values():
        for key, base in bases.items():
            for value in directives.get(key, []):
                writable |= {Path(base) / name for name in value.split()}
        for value in directives.get("ReadWritePaths", []) + directives.get("WorkingDirectory", []):
            writable |= {Path(path.lstrip("-")) for path in value.split()}
    assert Path("/var/lib/milo") in writable and len(writable) > 6, "the extractor is broken"

    reachable = sorted(str(path) for path in writable if PRESENCE_RECORD_FILE.is_relative_to(path))
    assert not reachable, f"{PRESENCE_RECORD_FILE} sits under {reachable}, which `milo` processes write"


def test_the_inhibitor_starts_only_once_logind_lists_its_lock():
    """`systemctl start` of a simple service returns once it is forked: what
    the start waits on is the ExecStartPost, so it has to look for the very
    lock ExecStart asks for, by the words ExecStart gives it."""
    directives = service_section(INHIBIT_UNIT)
    start = " ".join(directives["ExecStart"])
    why = re.search(r'"--why=([^"]+)"', start)
    assert why and "--what=handle-power-key" in start, start

    posts = directives.get("ExecStartPost", [])
    assert posts, "nothing holds the start to the lock being taken"
    phrase = re.search(r'grep -F "([^"]+)"', posts[0])
    assert phrase, posts[0]
    assert phrase.group(1) in why.group(1)
    assert "handle-power-key" in posts[0] and "--list" in posts[0]
