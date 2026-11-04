"""Where a WsEvent class is emitted, shared by the two guards that ask.

`test_ws_events.py` (every concrete event has a production emitter) and
`test_milo_mac_contract.py` (every WS pair Milo-Mac listens for is broadcast)
ask the same question. They used to answer it with two scans that counted any
mention of the class name, and a consumer mentions an event too: the push
service's `TRIGGERS` tuple and its `isinstance` check held `volume/volume_changed`
and `source/state` green with no emitter left in the tree.

A class counts as emitted where it is
  (a) called: `VolumeChanged(...)`, `ws_events.VolumeChanged(...)`;
  (b) handed to a call other than `isinstance`/`issubclass`, positionally or by
      keyword: `progress_event_cls=SatelliteUpdateProgress`,
      `_broadcast_status(FanStatusChanged)`;
  (c) a value of a dict literal: a factory table such as
      `REGISTRY_EVENT_CLASSES`, built later by `event_cls(**data)`.
A tuple or list element, an annotation, an `isinstance` argument, an import and
any other mention (`event.__class__ is VolumeChanged`) never count: each of them
is how a consumer names an event.
"""
import ast
from collections.abc import Collection
from pathlib import Path

_TYPE_CHECKS = {"isinstance", "issubclass"}


def _name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def emitted(source: str, names: Collection[str]) -> set[str]:
    """The members of `names` that `source` emits by (a), (b) or (c)."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            callee = _name(node.func)
            found.add(callee)
            if callee in _TYPE_CHECKS:
                continue
            found.update(_name(arg) for arg in node.args)
            found.update(_name(keyword.value) for keyword in node.keywords)
        elif isinstance(node, ast.Dict):
            found.update(_name(value) for value in node.values)
    return found & set(names)


def emitted_in_backend(backend_root: Path, names: Collection[str]) -> set[str]:
    """The members of `names` emitted by production code: `backend/` without its
    tests and without `core/models/ws_events.py`, where the classes are declared."""
    ws_events = backend_root / "core" / "models" / "ws_events.py"
    found: set[str] = set()
    for py in backend_root.rglob("*.py"):
        if "/tests/" in py.as_posix() or py == ws_events:
            continue
        found |= emitted(py.read_text(encoding="utf-8"), names)
    return found
