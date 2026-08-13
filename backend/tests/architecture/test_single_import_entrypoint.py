"""Structural guardrail: `backend/main.py` must be imported exactly once.

`milo-backend.service` runs the file directly (`python3 backend/main.py`), so
the module executes as `__main__`. Hand uvicorn the string `"backend.main:app"`
and it imports the module a *second* time under its real name: everything at
module level runs twice in the one process — `logging.basicConfig`, the
`RotatingFileHandler` on `errors.log`, the `WebSocketLogHandler` on the
`backend` logger, and the whole `get_service(...)` block.

Measured on a unit before the fix: every warning landed in `errors.log` as two
identical lines, same millisecond, halving the effective size of a 2 MB x 3
rotating log — and every ERROR was broadcast to the frontend twice, since the
WS handler was attached twice too. The service graph itself was unharmed
(`dependencies._services` memoises and that module is imported once), which is
exactly why nothing ever failed and the duplication went unnoticed.

Nothing else catches it: both forms start a working server, no test imports the
`__main__` block, and the damage is only visible by counting lines in a log
file on the running appliance.
"""
import ast
from pathlib import Path

MAIN_PY = Path(__file__).resolve().parents[2] / "main.py"


def _uvicorn_run_calls(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "run":
            if isinstance(func.value, ast.Name) and func.value.id == "uvicorn":
                yield node


def test_uvicorn_is_handed_the_app_object_not_an_import_string():
    tree = ast.parse(MAIN_PY.read_text())
    calls = list(_uvicorn_run_calls(tree))

    # The extractor must not pass by finding nothing.
    assert calls, f"no uvicorn.run(...) call found in {MAIN_PY} — did the entrypoint move?"

    for call in calls:
        assert call.args, "uvicorn.run(...) called with no positional app argument"
        target = call.args[0]
        assert not isinstance(target, ast.Constant), (
            "uvicorn.run() was handed the import string "
            f"{target.value!r}: that re-imports backend.main and runs every "
            "module-level statement a second time. Pass the app object."
        )
        assert isinstance(target, ast.Name), (
            f"unexpected first argument to uvicorn.run(): {ast.dump(target)}"
        )
