"""A test file run alone must not be blamed for the app's import-time writes.

`python -m pytest tests/<one file>.py` is the invocation CLAUDE.md documents, and
the one a developer reaches for when a contract goes red. A verdict that depends
on which other files were collected sends them hunting for a defect that is not
there.
"""
import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
PROBE_FILE = "tests/test_request_origin_gate.py"
PROBE_TEST = "test_the_gate_is_installed_on_the_app"


def test_a_file_run_alone_is_not_blamed_for_importing_the_app():
    """Importing `backend.main` writes (errors.log, the lyrics cache dir), and the
    appliance-write guard blames whatever test is running when that happens. If
    the import is left to a test body, a file run alone ends in "1 passed, 1
    error" naming /var/lib/milo/errors.log — a false red on a healthy contract,
    and a developer off looking for a store that writes where it should not.
    The child runs one test that imports the app only in its body.
    """
    probe_source = (BACKEND_DIR / PROBE_FILE).read_text()
    assert f"def {PROBE_TEST}():" in probe_source, (
        f"{PROBE_FILE}::{PROBE_TEST} moved: point PROBE_* at another test that "
        "imports backend.main only in its body"
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"{PROBE_FILE}::{PROBE_TEST}"],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, check=False,
    )
    output = proc.stdout + proc.stderr
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert lines, f"the child pytest printed nothing:\n{output}"
    summary = lines[-1]
    assert proc.returncode == 0, output
    assert "1 passed" in summary and "error" not in summary, output
