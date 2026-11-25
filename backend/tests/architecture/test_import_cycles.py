"""Structural guardrail: every backend module imports first, in a fresh interpreter.

A circular import only breaks when the cycle is entered from one particular
side, and the suite enters it from whichever side its collection order reaches
first. `shared/fanout.py` imported `core.equalizer.client_proxy` at the top
while `core.equalizer`'s package imported `fanout` back (multiroom_service):
every test passed, because the suite reached `core.equalizer` first, and the
backend did not start on the unit, because `main.py` reaches `fanout` first
(api.models -> core.multiroom -> crossover -> fanout). Measured 2026-11-25:
the service restart-looped on an ImportError until the next commit.

So each module is imported with every `backend.*` module dropped from
`sys.modules` before it — the order a fresh interpreter starting at that module
would see. Third-party packages stay cached, which keeps it to a few seconds.
Only a partially initialized module is reported: a module that cannot import on
a development host for another reason (a Linux-only errno on macOS) is not a
cycle and is someone else's to say.

`main.py` is left out: importing it instantiates every service. Every module
it imports is in the walk, each as a first import.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND = REPO_ROOT / "backend"

_PROBE = r"""
import importlib, sys
cycles = []
for name in sys.argv[1:]:
    for loaded in [m for m in sys.modules if m == "backend" or m.startswith("backend.")]:
        del sys.modules[loaded]
    try:
        importlib.import_module(name)
    except BaseException as exc:
        if "partially initialized" in str(exc):
            cycles.append(f"{name}: {type(exc).__name__}: {exc}")
print("\n".join(cycles))
"""


def backend_modules() -> list[str]:
    modules = []
    for path in sorted(BACKEND.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT)
        if "tests" in rel.parts or "venv" in rel.parts or rel == Path("backend/main.py"):
            continue
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        modules.append(".".join(parts))
    return modules


def test_the_walk_covers_the_backend():
    modules = backend_modules()
    assert len(modules) > 150, modules
    assert "backend.shared.fanout" in modules
    assert "backend.api.audio" in modules


def test_no_module_is_partially_initialized_when_imported_first():
    """A cycle here is a backend that does not start, whatever the suite says."""
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, *backend_modules()],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", result.stdout
