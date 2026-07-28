# backend/tests/architecture/test_settings_defaults.py
"""Structural guardrail over the settings defaults — the backend's most
safety-critical parse, and the one that had three declarations of every value.

Why it exists: `SettingsService.defaults` did not hold the defaults. Every one
of its 28 values was restated inline inside `_validate_and_merge`, forty lines
below the dict that names them, so mutating the dict alone changed nothing and
which declaration applied depended on how the settings were reached. They had
already diverged (`dock`), a fourth copy of the `mac` values lived in
`GET /bulk`, and 25 of that route's 29 fallbacks could not fire at all.

The rules read the production objects — the real `SettingsService`, the real
`get_bulk_settings` source — never a fixture, so a section added, renamed or
made conditional surfaces here rather than as a stale value on a settings page.

Rule 2 is the load-bearing one: it does not check that a default *exists*, it
checks that it is *effective*, by mutating it and requiring the validator to
follow. That is what makes a second declaration impossible rather than merely
detectable. It also asserts its own sentinel discriminates, so a clamp that
swallows the mutation fails the rule instead of passing it vacuously.
"""
import ast
import copy
from pathlib import Path

import pytest

from backend.core.settings import SettingsService

BACKEND = Path(__file__).resolve().parents[2]
SETTINGS_ROUTES = BACKEND / "api" / "settings.py"


@pytest.fixture
def service():
    return SettingsService()


def leaves(node, prefix=()):
    """Every scalar/list leaf of the defaults tree, as a dotted path."""
    for key, value in node.items():
        if isinstance(value, dict):
            yield from leaves(value, prefix + (key,))
        else:
            yield prefix + (key,), value


def read_path(tree, path):
    for key in path:
        tree = tree[key]
    return tree


def write_path(tree, path, value):
    for key in path[:-1]:
        tree = tree[key]
    tree[path[-1]] = value


def sentinels(value):
    """Candidate replacements for a default, ordered cheapest first.

    Several are offered because a single one can be eaten by the validator's
    own clamp (a step of 2.0 nudged to 9.0 comes back as 6.0 either way); the
    rule only needs *one* that moves the output.
    """
    if isinstance(value, bool):
        return [not value]
    if isinstance(value, int):
        return [value + 1, value - 1]
    if isinstance(value, float):
        return [value + 1.0, value - 1.0]
    if isinstance(value, str):
        return ["ZZ", "gradual", "manual", "french", "not-a-valid-value"]
    if isinstance(value, list):
        return [
            ["radio"],
            [{"temp_c": 60, "percent": 30}, {"temp_c": 80, "percent": 90}],
        ]
    raise AssertionError(f"no sentinel for {type(value).__name__} — extend this table")


# --------------------------------------------------------------------------
# The `/bulk` route source, extracted once
# --------------------------------------------------------------------------

def bulk_function():
    """The AST of `get_bulk_settings`, wherever it is nested."""
    tree = ast.parse(SETTINGS_ROUTES.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "get_bulk_settings":
            return node
    raise AssertionError(
        "get_bulk_settings not found in api/settings.py — the extractor is broken"
    )


BULK = bulk_function()


def test_extractors_parsed_a_plausible_surface(service):
    """Guards every rule below: an empty parse would make them vacuous."""
    paths = [p for p, _ in leaves(service.defaults)]
    assert len(paths) >= 25, f"parsed {len(paths)} defaults — the extractor is broken"
    assert ("volume", "limit_min_db") in paths
    assert ("mac", "latency_profile") in paths

    source = ast.unparse(BULK)
    assert "volume_limits" in source and "mac_roc" in source, (
        "get_bulk_settings no longer builds the bulk payload — the extractor is broken"
    )


def test_defaults_are_a_fixed_point_of_the_validator(service):
    """`_validate_and_merge(defaults)` must return `defaults` unchanged.

    When it does not, the two declarations disagree and the dict that *names*
    the defaults is not the one that produces them — how `fan.target_temp_c`
    came to exist only on the validator's side.
    """
    validated = service._validate_and_merge(copy.deepcopy(service.defaults))
    assert validated == service.defaults


def test_every_declared_section_is_emitted_unconditionally(service):
    """A section declared in `defaults` must survive a merge of `{}`.

    A conditionally-emitted section can be absent from settings.json, which is
    what forced `GET /bulk` to carry fallbacks: `mac` was declared everywhere
    except here, so it was never written and its fallbacks were the only live
    ones on a real unit.
    """
    emitted = service._validate_and_merge({})
    missing = sorted(set(service.defaults) - set(emitted))
    assert missing == []


@pytest.mark.parametrize(
    "path",
    [p for p, _ in leaves(SettingsService().defaults)],
    ids=lambda p: ".".join(p),
)
def test_each_default_is_effective(path):
    """Changing a default in `SettingsService.defaults` must change the output.

    This is the rule that makes a second declaration impossible: if
    `_validate_and_merge` keeps its own copy of a value, the merged result is
    pinned no matter what the dict says, and this goes red.
    """
    reference = SettingsService()
    baseline = read_path(reference._validate_and_merge({}), path)
    original = read_path(reference.defaults, path)

    tried = []
    for sentinel in sentinels(original):
        service = SettingsService()
        write_path(service.defaults, path, copy.deepcopy(sentinel))
        got = read_path(service._validate_and_merge({}), path)
        tried.append((sentinel, got))
        if got != baseline:
            return

    raise AssertionError(
        f"{'.'.join(path)} is restated inside _validate_and_merge: the merged value "
        f"stayed {baseline!r} for every sentinel tried ({tried!r})"
    )


def test_bulk_route_restates_no_default():
    """`GET /bulk` must read the stored settings, never supply a default.

    Every key it serves is guaranteed by the validator, so a fallback here can
    only ever disagree with `SettingsService.defaults` — silently, in the
    direction of showing a stale default as if it were the stored value. This
    is the backend twin of the frontend's `settingsBulkContract.test.js` rule.
    """
    offenders = []
    for node in ast.walk(BULK):
        # `x.get(key, default)` — a two-argument get is a fallback
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) == 2
        ):
            offenders.append(ast.unparse(node))
        # `x or default` / `x if cond else default` are the same thing spelled differently
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            offenders.append(ast.unparse(node))
        elif isinstance(node, ast.IfExp) and not isinstance(node.test, ast.Compare):
            offenders.append(ast.unparse(node))

    assert offenders == []
