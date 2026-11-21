"""Structural guardrail: a satellite app update is a trial the unit can end on its own.

A pushed version that died on start used to crashloop for good: the tree it
replaced was deleted before the restart, `Restart=always` retried the broken
code forever, the room stayed mute, and the update API that could have repaired
it was served by the app that no longer started. Three pieces now share that
job and live in three files no import connects — the updater
(`milo-client/app/services/app_update.py`) opens the trial, the guard
(`milo-client/rootfs/usr/local/bin/milo-client-update-guard`) counts starts and
rolls back, the unit (`milo-client/system/milo-client.service`) runs the guard
before every start. Each is read here from source.

What makes this worth a guardrail rather than a unit test: the updater of
release N hands the trial to the guard and unit of release N+1, so a state file
renamed on one side is a rollback that never happens, found on a mute speaker.
And the rollback cannot reach the root layer (units, sudoers, helpers), which
is protected upstream instead: every shell script both rootfs trees ship parses.

Doctrine note: every extractor asserts its own output is non-trivial first.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CLIENT = REPO_ROOT / "milo-client"
UPDATER = CLIENT / "app" / "services" / "app_update.py"
GUARD = CLIENT / "rootfs" / "usr" / "local" / "bin" / "milo-client-update-guard"
APP_UNIT = CLIENT / "system" / "milo-client.service"
ROOTFS_TREES = (REPO_ROOT / "rootfs", CLIENT / "rootfs")
STATUS_ROUTE = CLIENT / "app" / "routes" / "health.py"
SERVER_UPDATER = REPO_ROOT / "backend" / "core" / "updates" / "satellite.py"

# The keys of `/status`'s `app` object the server follows a push by. The server
# of release N+1 reads them off a satellite still on release N — the push it is
# waiting on is the one that carries N+1 — so they are frozen like the state
# names: `trial` and `last_update` are the verdict, `update_in_progress` the
# deploy that holds the ceiling open, `payload` and `started_at` the restart into
# it, `version` the row. A key added later is read as absent from every
# satellite that predates it; a renamed one is lost for good.
FROZEN_APP_STATUS_KEYS = frozenset({
    "version", "payload", "started_at", "update_in_progress", "trial", "last_update",
})

# The names both sides must spell alike: the state files, the suffixes of what
# a trial keeps, and the trees it swaps.
SHARED_NAMES = (
    "TRIAL_NAME", "ATTEMPTS_NAME", "LAST_UPDATE_NAME", "VERSION_NAME", "PAYLOAD_NAME",
    "OLD_SUFFIX", "STAGING_SUFFIX", "DISCARD_SUFFIX", "SYNCED_TREES",
)


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


def _path_constants(path: Path) -> dict:
    """Module-level `NAME = Path("<literal>")` assignments."""
    found = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if (
            isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call) and getattr(node.value.func, "id", None) == "Path"
            and node.value.args and isinstance(node.value.args[0], ast.Constant)
        ):
            found[node.targets[0].id] = node.value.args[0].value
    return found


def _guard_env_defaults() -> dict:
    """`os.environ.get("VAR", "default")` in the guard: what it reads, and where."""
    found = {}
    for node in ast.walk(ast.parse(GUARD.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and ast.unparse(node.func.value) == "os.environ"
            and len(node.args) == 2
        ):
            found[ast.literal_eval(node.args[0])] = ast.literal_eval(node.args[1])
    return found


def _updater_guard_env() -> dict:
    """The env the updater hands the guard: `{"VAR": str(CONSTANT)}` → {VAR: CONSTANT}."""
    for node in ast.parse(UPDATER.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef) and node.name == "_guard_env":
            mapping = {}
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    for key, value in zip(sub.keys, sub.values):
                        if key is not None and isinstance(value, ast.Call):
                            mapping[ast.literal_eval(key)] = ast.unparse(value.args[0])
            return mapping
    return {}


def _service_lines(unit: Path) -> list[str]:
    match = re.search(r"^\[Service\]\n(.*?)(?=^\[|\Z)", unit.read_text(encoding="utf-8"), re.M | re.S)
    assert match, f"no [Service] section in {unit.name}"
    return [line.strip() for line in match.group(1).splitlines() if line.strip() and not line.startswith("#")]


def test_the_app_unit_runs_the_guard_before_every_start():
    """Without it nothing counts the starts of a version on trial, and a broken
    one crashloops exactly as before. The `-` keeps a failing guard from
    keeping the app down. Consumer: milo-client.service on every satellite."""
    lines = _service_lines(APP_UNIT)
    assert any(line.startswith("ExecStart=") for line in lines), f"[Service] not read: {lines}"
    updater = _constants(UPDATER)

    assert f"ExecStartPre=-{updater['GUARD']} start" in lines


def test_a_missing_app_directory_does_not_stop_the_guard():
    """A power cut inside the swap can leave no app/. Without the `-`, systemd
    fails the start on the missing WorkingDirectory (200/CHDIR) before any
    ExecStartPre runs, and the guard that would put app/ back never does."""
    working = [line for line in _service_lines(APP_UNIT) if line.startswith("WorkingDirectory=")]
    assert len(working) == 1, working

    assert working[0].startswith("WorkingDirectory=-/")


def test_the_guard_the_unit_runs_is_shipped_by_the_satellite_tree():
    """A unit naming a helper the satellite's own tree does not carry is a
    start that fails 203/EXEC — invariant 2, for this helper. The guard runs
    as milo-client from the unit and from the app, so it must be executable."""
    guard_path = _constants(UPDATER)["GUARD"]
    shipped = CLIENT / "rootfs" / guard_path.lstrip("/")

    assert shipped == GUARD
    assert shipped.is_file(), f"{guard_path} is not in milo-client/rootfs/"
    assert shipped.stat().st_mode & 0o111, f"{shipped} is not executable"


def test_the_guard_runs_without_the_venv():
    """It may have to put back the venv it would otherwise import from: the
    system python, isolated (`-I`, no cwd or user site on sys.path), and the
    standard library alone."""
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
    # compile(), not py_compile: that writes a __pycache__ into the rootfs
    # tree, which the next push would deploy.
    compile(text, str(GUARD), "exec")


def test_the_updater_and_the_guard_name_the_same_state():
    """The updater of one release opens a trial the guard of the next closes:
    a file renamed on one side is a trial nobody ever ends, or a rollback that
    looks for a tree under a name nothing wrote."""
    updater, guard = _constants(UPDATER), _constants(GUARD)
    for side, found in (("updater", updater), ("guard", guard)):
        missing = [name for name in SHARED_NAMES if name not in found]
        assert not missing, f"{side}: {missing} not read"

    differ = {name: (updater[name], guard[name]) for name in SHARED_NAMES if updater[name] != guard[name]}
    assert differ == {}, f"updater vs guard: {differ}"


def test_the_updater_hands_the_guard_its_own_paths():
    """The guard's rollback must act on the trees the updater kept. The unit
    runs it with its defaults, the updater with its own constants: both must
    name the same directories, and every path the guard reads must be one the
    updater sets."""
    defaults = _guard_env_defaults()
    handed = _updater_guard_env()
    paths = _path_constants(UPDATER)
    assert len(defaults) >= 3 and len(handed) >= 3, (defaults, handed)

    path_vars = {var for var, default in defaults.items() if default.startswith("/")}
    assert sorted(path_vars - set(handed)) == [], "read by the guard, never handed by the updater"
    for var in path_vars:
        assert paths.get(handed[var]) == defaults[var], (
            f"{var}: guard default {defaults[var]!r}, updater {handed[var]} = {paths.get(handed[var])!r}"
        )


def _interpreter(path: Path) -> str | None:
    try:
        with open(path, "rb") as f:
            head = f.readline(200).decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not head.startswith("#!"):
        return None
    words = head[2:].split()
    if not words:
        return None
    name = Path(words[0]).name
    if name == "env" and len(words) > 1:
        name = words[1]
    return name


def _shell_scripts() -> list[tuple[Path, str]]:
    scripts = []
    for tree in ROOTFS_TREES:
        for path in sorted(tree.rglob("*")):
            if path.is_file():
                shell = _interpreter(path)
                if shell in ("bash", "sh", "dash"):
                    scripts.append((path, shell))
    return scripts


SHELL_SCRIPTS = _shell_scripts()


def test_the_shell_script_extractor_sees_both_trees():
    names = {path.name for path, _ in SHELL_SCRIPTS}
    assert len(SHELL_SCRIPTS) >= 20, f"only {len(SHELL_SCRIPTS)} shell scripts read"
    assert {"milo-client-deploy-update", "milo-deploy-update"} <= names, names


@pytest.mark.parametrize(
    "script,shell", SHELL_SCRIPTS, ids=[str(p.relative_to(REPO_ROOT)) for p, _ in SHELL_SCRIPTS]
)
def test_every_rootfs_shell_script_parses(script, shell):
    """A rollback restores app/, configs/ and the venv, never what the root
    deploy installed: a helper or deploy script that does not parse reaches
    every unit and stays there, the next update's deploy script included.
    `bash -n` / `sh -n` by shebang — the interpreter the unit will run it with."""
    checker = "bash" if shell == "bash" else "sh"
    result = subprocess.run([checker, "-n", str(script)], capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stderr


def test_the_parse_check_rejects_a_broken_script(tmp_path):
    """The floor under the check above: a parse that accepts anything is green
    on every tree."""
    broken = tmp_path / "broken"
    broken.write_text("#!/bin/bash\nif true; then\n  echo unterminated\n")

    result = subprocess.run(["bash", "-n", str(broken)], capture_output=True, text=True, check=False)

    assert result.returncode != 0
    assert re.search(r"syntax error|unexpected end", result.stderr)


def _served_app_status_keys() -> set[str]:
    """The keys of the `app` object the satellite's `/status` answers, read from its literal."""
    for node in ast.walk(ast.parse(STATUS_ROUTE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "app" and isinstance(value, ast.Dict):
                    return {k.value for k in value.keys if isinstance(k, ast.Constant)}
    return set()


def _is_app_object(node: ast.AST) -> bool:
    """`app`, or `<x>.get("app", …)`: the two ways the server holds the object."""
    if isinstance(node, ast.Name):
        return node.id == "app"
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get" and bool(node.args)
        and isinstance(node.args[0], ast.Constant) and node.args[0].value == "app"
    )


def _server_app_status_reads(source: str) -> set[str]:
    """Every `<app object>.get("<key>")` and `<app object>["<key>"]` in `source`."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and _is_app_object(node.func.value)
            and node.args and isinstance(node.args[0], ast.Constant)
        ):
            found.add(node.args[0].value)
        elif (
            isinstance(node, ast.Subscript) and _is_app_object(node.value)
            and isinstance(node.slice, ast.Constant)
        ):
            found.add(node.slice.value)
    return found


def test_the_status_read_extractor_sees_both_spellings():
    """Each of the two shapes the server reads the object in is seen."""
    assert _server_app_status_reads(
        'app = x\napp.get("a")\ndata.get("app", {}).get("b")\napp["c"]\nother.get("d")'
    ) == {"a", "b", "c"}


def test_the_app_status_keys_the_server_follows_a_push_by_are_frozen():
    """What breaks when this fails: a push the server can no longer follow, on
    every satellite in the field. `update_in_progress` renamed on the satellite
    alone passed every other test, and so would the same rename made on both
    sides at once: the server of the next release, reading the new name off a
    satellite still on the old one, loses the deploy that extends its wait,
    calls a long pip run `did_not_return` and holds the rest of the fleet back.
    """
    served = _served_app_status_keys()
    read = _server_app_status_reads(SERVER_UPDATER.read_text(encoding="utf-8"))
    assert {"trial", "last_update"} <= served, f"/status's app object was not read from {STATUS_ROUTE.name}: {served}"
    assert {"trial", "last_update"} <= read, (
        f"the server's reads of the app object were not seen in {SERVER_UPDATER.name}: {read}"
    )

    assert not FROZEN_APP_STATUS_KEYS - served, (
        f"the satellite's /status no longer answers app.{sorted(FROZEN_APP_STATUS_KEYS - served)}, "
        "which every server from an earlier release reads to follow a push"
    )
    assert not read - FROZEN_APP_STATUS_KEYS, (
        f"the server reads app.{sorted(read - FROZEN_APP_STATUS_KEYS)} off /status, which a "
        "satellite from an earlier release does not answer: the push that brings it is "
        "followed by a read that comes back absent. Freeze it here only with the server "
        "reading that absence as a satellite on the release before."
    )
