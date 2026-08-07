"""Structural guardrail: the registry's internal event bus has to line up.

`ClientRegistryService` is the only producer of `RegistryEventType` events;
three services subscribe to them (`CrossoverService`, `VolumeStateStore` and
`SnapcastWebSocketService`, the last of which turns each one into a typed WS
broadcast). Nothing connected the two ends, and every kind of mismatch that is
possible had happened by the time this test was written:

  * **An event nobody emits.** `SPEAKER_TYPE_CHANGED` was declared, mapped to a
    WS class, and produced only by `update_speaker_type` — which had no
    production caller. `ZONE_CLIENT_ADDED` was declared and *handled twice*,
    with no producer at all.
  * **A key the producer never sends.** `VolumeStateStore`'s zone-membership
    arms read `data.get("camilladsp_id")` after that identifier was renamed to
    `mac_id`, so the branch bodies could not execute. Nothing failed: `.get()`
    returned None and the arm skipped itself.
  * **A payload the WS class cannot take.** `_broadcast_registry_event` does
    `event_cls(**data)`, so a payload key that is not a field of the mapped
    model is a runtime error on a broadcast path, reachable only with a second
    physical unit.
  * **A hand-built event that skipped the bus.** `CrossoverService` constructs
    `MultiroomZoneChanged` itself and broadcasts it, deliberately, to avoid
    re-entering the registry handler. The `_emit_event` walk cannot see that
    site, so a newly required field would land there as a ValidationError on a
    broadcast path — again only on a second physical unit.

None of that is visible in CI or in dev without this test: no import error, no
failing route, just a command that quietly did nothing.

Doctrine note (same as the Milo-Mac / milo-client contract tests and the
frontend guardrails): every extractor asserts its own output is non-trivial
first, so a broken parse fails loudly instead of passing on an empty surface.
"""
import ast
from pathlib import Path

import pytest

from backend.core.multiroom.client_registry import REGISTRY_EVENT_CLASSES
from backend.core.multiroom.models import RegistryEventType

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = BACKEND_ROOT / "core" / "multiroom" / "client_registry.py"

# Declared event types, read off the constants class rather than a hand-copied list.
DECLARED = {
    name: value
    for name, value in vars(RegistryEventType).items()
    if not name.startswith("_") and isinstance(value, str)
}


def _tree(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text())
    except SyntaxError as exc:  # pragma: no cover - a broken tree is a real failure
        raise AssertionError(f"cannot parse {path}: {exc}") from exc


def _event_type_of(node: ast.AST, scope: ast.AST) -> set:
    """Resolve the event-type argument of an _emit_event call to constant values.

    Either a direct ``RegistryEventType.X``, or a local name assigned one or
    more of them earlier in the same function (``register_client`` and
    ``set_client_online`` both pick their type in a branch).
    """
    if isinstance(node, ast.Attribute) and node.attr in DECLARED:
        return {DECLARED[node.attr]}
    if isinstance(node, ast.Name):
        found = set()
        for sub in ast.walk(scope):
            targets = []
            if isinstance(sub, ast.Assign):
                targets = sub.targets
            elif isinstance(sub, ast.AnnAssign):
                targets = [sub.target]
            if any(isinstance(t, ast.Name) and t.id == node.id for t in targets):
                for value in ast.walk(sub.value):
                    if isinstance(value, ast.Attribute) and value.attr in DECLARED:
                        found.add(DECLARED[value.attr])
        return found
    return set()


def _producer_sites() -> list:
    """[(event value, site label, payload keys)] — one entry per _emit_event call.

    Kept per call site, not merged: two producers emit CLIENT_DISCONNECTED with
    different payloads (`unregister_client` deliberately omits `client`), and a
    union would hide a site that drops a *required* field behind a sibling that
    sends it. Same lesson as the milo-client contract test's per-producer bodies.
    """
    tree = _tree(REGISTRY_PATH)
    out = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(func):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "_emit_event"):
                continue
            assert len(call.args) == 2, (
                f"{func.name}: _emit_event must be called with (event_type, payload dict); "
                "the extractor cannot resolve anything else"
            )
            values = _event_type_of(call.args[0], func)
            assert values, f"{func.name}: cannot resolve the event type of an _emit_event call"
            payload = call.args[1]
            assert isinstance(payload, ast.Dict), (
                f"{func.name}: _emit_event payload must be a dict literal so it can be checked"
            )
            keys = set()
            for key in payload.keys:
                assert isinstance(key, ast.Constant) and isinstance(key.value, str), (
                    f"{func.name}: non-literal key in an _emit_event payload"
                )
                keys.add(key.value)
            for value in values:
                out.append((value, f"{func.name}:{call.lineno}", keys))
    return out


def _subscribers() -> dict:
    """{module: {event value: set of top-level `data` keys the arm reads}}.

    Finds each `registry.subscribe(self._handler)` under core/, then walks that
    handler's `event_type == ...` branches.
    """
    out = {}
    modules = sorted(
        p for p in (BACKEND_ROOT / "core").rglob("*.py") if "__pycache__" not in p.parts
    )
    for path in modules:
        tree = _tree(path)
        handlers = {
            call.args[0].attr
            for call in ast.walk(tree)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "subscribe" and len(call.args) == 1
            and isinstance(call.args[0], ast.Attribute)
        }
        if not handlers:
            continue
        for func in ast.walk(tree):
            if not (isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and func.name in handlers):
                continue
            arms = out.setdefault(path.name, {})
            for branch in ast.walk(func):
                if not (isinstance(branch, ast.If) and isinstance(branch.test, ast.Compare)):
                    continue
                left, comparators = branch.test.left, branch.test.comparators
                if not (isinstance(left, ast.Name) and left.id == "event_type" and comparators):
                    continue
                target = comparators[0]
                if isinstance(target, ast.Attribute):
                    value = DECLARED.get(target.attr, f"<unknown:{target.attr}>")
                elif isinstance(target, ast.Constant):
                    value = target.value
                else:
                    continue
                keys = set()
                for node in ast.walk(ast.Module(body=branch.body, type_ignores=[])):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "get"
                            and isinstance(node.func.value, ast.Name)
                            and node.func.value.id == "data"
                            and node.args and isinstance(node.args[0], ast.Constant)):
                        keys.add(node.args[0].value)
                    elif (isinstance(node, ast.Subscript)
                          and isinstance(node.value, ast.Name) and node.value.id == "data"
                          and isinstance(node.slice, ast.Constant)):
                        keys.add(node.slice.value)
                arms.setdefault(value, set()).update(keys)
    return out


REGISTRY_WS_CLASSES = {cls.__name__: cls for cls in REGISTRY_EVENT_CLASSES.values()}


def _direct_construction_sites() -> list:
    """[(class name, site label, keyword names)] — every mapped WS event that a
    service under core/ builds by hand instead of emitting on the registry bus.

    `_producer_sites()` walks `_emit_event` calls only, so it is structurally
    blind to these. Keywords only: a pydantic model takes no positional args,
    and `**unpacking` would make the site unreadable — both fail here rather
    than passing on a surface the extractor cannot see.
    """
    out = []
    modules = sorted(
        p for p in (BACKEND_ROOT / "core").rglob("*.py") if "__pycache__" not in p.parts
    )
    for path in modules:
        tree = _tree(path)
        for call in ast.walk(tree):
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id in REGISTRY_WS_CLASSES):
                continue
            site = f"{path.name}:{call.lineno}"
            assert not call.args, (
                f"{site}: {call.func.id} built with positional arguments; "
                "a pydantic model takes keywords only"
            )
            names = {kw.arg for kw in call.keywords}
            assert None not in names, (
                f"{site}: {call.func.id} built with **unpacking; the extractor "
                "cannot tell which fields it carries"
            )
            out.append((call.func.id, site, names))
    return out


PRODUCER_SITES = _producer_sites()
DIRECT_SITES = _direct_construction_sites()
# Union per event: what a subscriber may legitimately see arrive.
PRODUCERS = {}
for _event, _site, _keys in PRODUCER_SITES:
    PRODUCERS.setdefault(_event, set()).update(_keys)
SUBSCRIBERS = _subscribers()


def test_extraction_is_not_trivial():
    """A broken parse must fail loudly, not pass on an empty surface."""
    assert len(DECLARED) >= 8, DECLARED
    assert len(PRODUCERS) >= 8, sorted(PRODUCERS)
    assert len(PRODUCER_SITES) >= 12, PRODUCER_SITES
    assert set(PRODUCERS) <= set(DECLARED.values())
    # The three known subscriber modules must all be found, each with arms.
    assert set(SUBSCRIBERS) >= {"crossover.py", "state.py"}, sorted(SUBSCRIBERS)
    assert sum(len(arms) for arms in SUBSCRIBERS.values()) >= 8, SUBSCRIBERS
    # At least one payload key per event, or the payload walk silently missed them.
    assert all(keys for keys in PRODUCERS.values()), PRODUCERS
    # The hand-built broadcasts the _emit_event walk cannot see.
    assert len(DIRECT_SITES) >= 2, DIRECT_SITES
    assert all(names for _, _, names in DIRECT_SITES), DIRECT_SITES


@pytest.mark.parametrize("name,value", sorted(DECLARED.items()))
def test_every_declared_event_has_a_producer(name, value):
    """A declared event nobody emits is dead weight that grows handler arms."""
    assert value in PRODUCERS, (
        f"RegistryEventType.{name} = {value!r} is declared but never emitted by "
        f"ClientRegistryService. Delete it, or emit it."
    )


@pytest.mark.parametrize("module", sorted(SUBSCRIBERS))
def test_every_handled_event_is_emitted(module):
    """An arm for an event that is never emitted cannot run."""
    unknown = sorted(set(SUBSCRIBERS[module]) - set(PRODUCERS))
    assert not unknown, (
        f"{module} handles registry events that nothing emits: {unknown}. "
        f"Emitted: {sorted(PRODUCERS)}"
    )


@pytest.mark.parametrize("module", sorted(SUBSCRIBERS))
def test_every_key_a_subscriber_reads_is_one_a_producer_sends(module):
    """The camilladsp_id bug: `.get()` on an absent key skips the arm in silence."""
    problems = []
    for event, keys in SUBSCRIBERS[module].items():
        sent = PRODUCERS.get(event)
        if sent is None:
            continue  # covered by test_every_handled_event_is_emitted
        for key in sorted(keys - sent):
            problems.append(f"{event}: reads {key!r}, producers send {sorted(sent)}")
    assert not problems, f"{module} reads keys no producer sends:\n  " + "\n  ".join(problems)


def test_emitted_and_mapped_events_agree():
    """`_broadcast_registry_event` drops an unmapped event; a mapping with no
    producer is a WS event class kept alive by nothing."""
    mapped, emitted = set(REGISTRY_EVENT_CLASSES), set(PRODUCERS)
    assert not emitted - mapped, (
        f"emitted but not in REGISTRY_EVENT_CLASSES (broadcast would be dropped): "
        f"{sorted(emitted - mapped)}"
    )
    assert not mapped - emitted, (
        f"mapped in REGISTRY_EVENT_CLASSES but never emitted: {sorted(mapped - emitted)}"
    )


@pytest.mark.parametrize(
    "event,site,sent",
    [(e, s, frozenset(k)) for e, s, k in PRODUCER_SITES],
    ids=[f"{e}@{s}" for e, s, _ in PRODUCER_SITES],
)
def test_payload_is_constructible_as_the_mapped_ws_event(event, site, sent):
    """`event_cls(**data)` — an extra key raises on the broadcast path, and so
    does a missing required field. Checked per call site: one producer omitting
    a required field is invisible in a union with its siblings."""
    event_cls = REGISTRY_EVENT_CLASSES[event]
    fields = set(event_cls.model_fields)
    assert not sent - fields, (
        f"{site} emits {event}: payload keys {sorted(sent - fields)} are not fields of "
        f"{event_cls.__name__} ({sorted(fields)}) — event_cls(**data) would raise"
    )
    required = {
        name for name, field in event_cls.model_fields.items() if field.is_required()
    }
    assert required <= sent, (
        f"{site} emits {event} without {sorted(required - set(sent))}, "
        f"required by {event_cls.__name__}"
    )


@pytest.mark.parametrize(
    "cls_name,site,passed",
    [(c, s, frozenset(k)) for c, s, k in DIRECT_SITES],
    ids=[f"{c}@{s}" for c, s, _ in DIRECT_SITES],
)
def test_hand_built_event_carries_every_required_field(cls_name, site, passed):
    """A mapped WS event built outside the bus gets none of the checks above.
    `MultiroomZoneChanged.action` is required precisely so a consumer stops
    guessing what happened from which optional field is present — a site that
    omits it raises at broadcast time, on a path only real hardware reaches."""
    event_cls = REGISTRY_WS_CLASSES[cls_name]
    fields = set(event_cls.model_fields)
    assert not passed - fields, (
        f"{site} passes {sorted(passed - fields)} to {cls_name}, which has no such "
        f"field ({sorted(fields)})"
    )
    required = {
        name for name, field in event_cls.model_fields.items() if field.is_required()
    }
    assert required <= passed, (
        f"{site} builds {cls_name} without {sorted(required - passed)}, "
        f"which the model requires"
    )
