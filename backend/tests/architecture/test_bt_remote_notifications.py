"""Structural guardrail: changing the BT remote's connection always tells the UI.

The BT-remote panel writes `connected` and `discovering` optimistically the
moment the user touches the toggle or the Search button, and has no other way
back to the truth than `bt_remote_status_changed`. So every path that changes
which evdev nodes are monitored owes the UI a status broadcast — and the
history of this file is the history of that debt going unpaid:

  * `c41c6e23` ("stale connected state … and UI inconsistencies") settled the
    class almost entirely **on the frontend** — optimistic store writes plus a
    `btRemoteConnected` computed that ignores `connected` while discovering.
    That is a compensation, not a fix, and it could only cover the paths its
    author enumerated.
  * Two survived. Enabling with a bonded remote asleep produced no event at
    all, so `discovering` stayed true and the Search CTA — which binds both
    :loading and :disabled to it — became a permanently spinning, unclickable
    button. Disabling produced none either, so every *other* open surface
    (phone and kiosk at once is the normal usage) kept showing a remote that
    had just been disconnected.

Both had the same shape: `_stop_scanning()` was the one mutator of the
monitored set that notified nobody. Rule 2 is that fact, generalised.

The rules are structural on purpose. A behavioural test only covers the paths
someone thought to write one for; these fail on a *fifth* path added later that
mutates the monitored set and stays quiet. Behaviour is covered separately in
`tests/test_bt_remote.py`.

What Rule 2 does *not* reach: it asks whether a path contains the broadcast, not
whether that broadcast is reachable, so a call left behind a guard that is always
false still counts. Proving otherwise needs dataflow. `tests/test_bt_remote.py`
covers it instead — its toggle tests go red on exactly that mutation.

Two corollaries, measured, both covered by name in `tests/test_bt_remote.py`.
The rule is *per method*, so a method carrying a broadcast on one branch is not
guarded branch by branch: `_scan_devices` broadcasts for "a new MAC appeared",
which keeps Rule 2 satisfied with its "a monitored node vanished" broadcast
deleted. And the rule only applies to methods that write NODE_STATE directly,
so `forget_remote` — which writes none of the three containers, and whose
explicit broadcast is the only thing an asleep remote's unpairing produces — is
outside it entirely.

Doctrine note (as in the other architecture guardrails): every extractor asserts
its own output is non-trivial first, so a broken parse fails loudly instead of
passing on an empty surface.
"""
import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
BT_REMOTE_PATH = BACKEND_ROOT / "hardware" / "bt_remote.py"

# The per-device bookkeeping. Three parallel containers keyed by evdev path that
# only mean anything together: a path in `_monitored_paths` with no
# `_device_info` entry is a device the status cannot name, and one with no
# `_monitor_tasks` entry is a device nobody is reading.
NODE_STATE = frozenset({"_monitored_paths", "_device_info", "_monitor_tasks"})

BROADCASTER = "_broadcast_status"
STATUS_EVENT = "BtRemoteStatusChanged"

# Shutdown is the one caller that owes the UI nothing: the process is going
# away, there is no next state to report, and the WS manager is being torn down.
NOTIFY_EXEMPT_CALLERS = frozenset({"cleanup"})

# Populating the containers for the first time is not a state change.
STATE_RULE_EXEMPT = frozenset({"__init__"})


def _writes(fn) -> set:
    """Which NODE_STATE containers this function mutates directly."""
    written = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                # self._device_info[path] = ...
                if isinstance(target, ast.Subscript):
                    attr = target.value
                    if (isinstance(attr, ast.Attribute) and isinstance(attr.value, ast.Name)
                            and attr.value.id == "self" and attr.attr in NODE_STATE):
                        written.add(attr.attr)
                # self._monitored_paths = ...
                if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name)
                        and target.value.id == "self" and target.attr in NODE_STATE):
                    written.add(target.attr)
        # self._monitored_paths.add(...) / .discard / .pop / .clear
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name)
                    and owner.value.id == "self" and owner.attr in NODE_STATE
                    and node.func.attr in {"add", "discard", "pop", "clear", "remove",
                                           "update", "setdefault"}):
                written.add(owner.attr)
    return written


def _self_calls(fn) -> set:
    """Names of methods this function calls on self."""
    return {
        node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name) and node.func.value.id == "self"
    }


def _spawned(fn) -> set:
    """Names of methods this function starts as a background task."""
    spawned = set()
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_task"):
            continue
        for arg in node.args:
            if (isinstance(arg, ast.Call) and isinstance(arg.func, ast.Attribute)
                    and isinstance(arg.func.value, ast.Name) and arg.func.value.id == "self"):
                spawned.add(arg.func.attr)
    return spawned


@pytest.fixture(scope="module")
def controller():
    """Parsed BtRemoteController: methods, what each writes, and what each calls."""
    tree = ast.parse(BT_REMOTE_PATH.read_text())
    cls = next(
        (n for n in tree.body
         if isinstance(n, ast.ClassDef) and n.name == "BtRemoteController"),
        None,
    )
    assert cls is not None, f"BtRemoteController not found in {BT_REMOTE_PATH}"

    methods = {
        n.name: n for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert len(methods) > 20, f"only {len(methods)} methods parsed — extractor is broken"
    assert BROADCASTER in methods, f"{BROADCASTER}() is gone — this guardrail names it"

    writes = {name: _writes(fn) for name, fn in methods.items()}
    calls = {name: _self_calls(fn) for name, fn in methods.items()}

    mutators = {name for name, w in writes.items() if w and name not in STATE_RULE_EXEMPT}
    assert len(mutators) >= 3, (
        f"only {sorted(mutators)} mutate the node state — extractor is broken")

    callers = {name: {other for other, c in calls.items() if name in c} for name in methods}

    # A method started as a background task outlives the call that started it, so
    # its spawner's broadcast describes the adoption, never the later teardown.
    # Such a method has to notify on its own — it inherits no coverage.
    spawned = set().union(*(_spawned(fn) for fn in methods.values()))
    assert spawned, "no background task spawned — extractor is broken"
    for name in spawned:
        callers[name] = set()

    return {"methods": methods, "writes": writes, "calls": calls, "spawned": spawned,
            "mutators": mutators, "callers": callers, "cls": cls}


def test_every_mutator_of_the_monitored_set_notifies_the_ui(controller):
    """A path that changes which devices are monitored either broadcasts a status
    itself, or is only ever reached from paths that do.

    This is the rule the shipped bug broke: `_stop_scanning()` empties all three
    containers and notifies nobody, so disabling the remote from Réglages left
    every other open surface showing it as connected, and enabling left the
    Search button spinning forever.
    """
    calls, callers = controller["calls"], controller["callers"]
    broadcasts = {name for name, c in calls.items() if BROADCASTER in c}
    assert broadcasts, "nothing calls the broadcaster — extractor is broken"

    resolving, verdicts = set(), {}

    def notifies(name):
        if name in verdicts:
            return verdicts[name]
        if name in broadcasts:
            verdicts[name] = True
            return True
        if name in resolving:          # cycle: it is not the one notifying
            return False
        resolving.add(name)
        parents = callers.get(name, set()) - NOTIFY_EXEMPT_CALLERS - {name}
        verdict = bool(parents) and all(notifies(parent) for parent in parents)
        resolving.discard(name)
        verdicts[name] = verdict
        return verdict

    silent = sorted(name for name in controller["mutators"] if not notifies(name))
    assert not silent, (
        f"{silent} change which BT-remote devices are monitored without any "
        f"caller broadcasting {STATUS_EVENT}. The settings panel sets "
        f"`connected`/`discovering` optimistically and cannot recover on its own — "
        f"call self.{BROADCASTER}() on this path, or reach it only from one that does."
    )


def test_the_three_node_containers_are_always_mutated_together(controller):
    """`_monitored_paths`, `_device_info` and `_monitor_tasks` are keyed by the
    same evdev path and are only meaningful together.

    Touching one without the others is how a device ends up monitored but
    unnamed, or reported but unread. It is also why adding a per-device field
    used to take three commits — the reason `_drop_node` exists.
    """
    offenders = {
        name: sorted(written)
        for name, written in controller["writes"].items()
        if written and written != set(NODE_STATE) and name not in STATE_RULE_EXEMPT
        and "_drop_node" not in controller["calls"][name]
    }
    assert not offenders, (
        f"{offenders} mutate part of the per-device bookkeeping. Write all of "
        f"{sorted(NODE_STATE)}, or delegate to _drop_node()."
    )


def test_the_status_event_has_a_single_construction_site(controller):
    """`BtRemoteStatusChanged` is built in one place, so its payload cannot
    diverge between the paths that report a connection and the paths that report
    a disconnection — the divergence Rule 2 exists to prevent, one layer down.
    """
    builders = sorted(
        name for name, fn in controller["methods"].items()
        if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == STATUS_EVENT for n in ast.walk(fn))
    )
    assert builders == [BROADCASTER], (
        f"{STATUS_EVENT} is constructed in {builders}; it must be built only by "
        f"{BROADCASTER}(), which every notifying path already goes through."
    )
