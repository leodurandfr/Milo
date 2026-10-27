#!/usr/bin/env python3
"""Write the "API and integration changes" section of a release's notes.

    python tools/release/api_diff.py v0.1.1 v0.2.0
    python tools/release/api_diff.py v0.1.1 HEAD

Run from the repository root, with the backend's virtualenv. Each ref is
extracted with `git archive` into a temporary directory and read in a process
of its own — two versions of `backend` cannot share one interpreter: its REST
surface from the app's OpenAPI schema, its WebSocket events from `ws_events.py`,
its source commands from each source's `COMMANDS`. The two are compared, and
every difference is listed under **Contracted** when Milo-Mac or Milo-iOS relies
on it (the operations their manifests pin, the schemas those operations and the
pinned events carry, the events the Mac manifest pins, the commands and
parameters the apps send), else under **Internal**. Descriptions and summaries are not compared: only what a client
sends or reads.
"""
import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# What extraction reads of a tree: the backend imports nothing else.
ARCHIVED = ("backend", "dependencies.env")

for _path in (REPO, REPO / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from api_surface import (  # noqa: E402
    HTTP_METHODS,
    body as _body,
    commands_the_apps_send,
    manifests as _manifests,
    pinned_events,
    schema_name as _ref_name,
    source_classes,
    success_response,
    ws_event_classes,
)


# --------------------------------------------------------------------------- #
# Extraction — runs inside the extracted tree, in a child process
# --------------------------------------------------------------------------- #

def _model(schema: dict) -> dict:
    return {"fields": _schema_fields(schema), "values": schema.get("enum", []),
            "refs": sorted(_refs(schema))}


def _schema_fields(schema: dict) -> dict:
    required = set(schema.get("required", []))
    return {
        name: {"type": _ref_name(prop), "required": name in required}
        for name, prop in schema.get("properties", {}).items()
    }


def extract(tree: Path) -> dict:
    """The surface of the tree at `tree`, as plain JSON."""
    # The tree's `backend`, never this checkout's.
    sys.path[:] = [p for p in sys.path if Path(p).resolve() != REPO]
    sys.path.insert(0, str(tree))
    from backend.main import app

    spec = app.openapi()
    operations = {}
    for path, item in spec["paths"].items():
        for method in HTTP_METHODS:
            if method not in item:
                continue
            operation = item[method]
            code, success = success_response(operation)
            operations[f"{method.upper()} {path}"] = {
                "status": code,
                "params": sorted(
                    f"{p['in']}:{p['name']}{'' if p.get('required') else '?'}"
                    f" ({_ref_name(p.get('schema', {}))})"
                    for p in operation.get("parameters", [])
                ),
                "request": _body(operation.get("requestBody", {}).get("content", {})),
                "response": _body(success.get("content", {})),
                "refs": sorted(_refs(operation)),
            }
    schemas = {
        name: _model(schema)
        for name, schema in spec.get("components", {}).get("schemas", {}).items()
    }

    # Models an event or a command carries, besides the REST components.
    events, models = {}, {}
    for cls in ws_event_classes():
        # Serialization, as FastAPI renders its responses: what is sent.
        schema = cls.model_json_schema(ref_template="#/{model}", mode="serialization")
        event = events.setdefault(f"{cls.CATEGORY}/{cls.TYPE}", {"variants": [], "refs": set()})
        event["variants"].append(_schema_fields(schema))
        event["refs"] |= _refs(schema.get("properties", {}))
        for name, model in schema.get("$defs", {}).items():
            models[name] = _model(model)

    commands = {}
    for source, cls in source_classes():
        for name, params in cls.COMMANDS.items():
            schema = params.model_json_schema(ref_template="#/{model}") if params else {}
            commands[f"{source}/{name}"] = {
                "fields": _schema_fields(schema), "refs": sorted(_refs(schema.get("properties", {}))),
            }
            for model_name, model in schema.get("$defs", {}).items():
                models[model_name] = _model(model)
    sent_commands, sent_params = commands_the_apps_send(tree)

    if not (operations and events and commands):
        raise SystemExit(f"read almost nothing of {tree}: {len(operations)} operations, "
                         f"{len(events)} events, {len(commands)} commands")
    return {
        "commands": commands,
        "sent_commands": sorted(sent_commands),
        "sent_params": sorted(sent_params),
        "operations": operations,
        "schemas": schemas,
        "events": {pair: _event(e, schemas) for pair, e in events.items()},
        "models": models,
    }


def _event(event: dict, schemas: dict) -> dict:
    """An event's field table — or, when it is a REST answer's model too (the
    audio state: `source/state` subclasses it), the name of that model, so one
    change is listed once, where the route lists it."""
    fields = _merge_variants(event["variants"])
    same_as = next((name for name, model in schemas.items() if model["fields"] == fields), None)
    return {"fields": fields, "refs": sorted(event["refs"]), "same_as": same_as}


def _merge_variants(variants: list) -> dict:
    """One field table for a pair two classes share (radio's and podcast's
    `source/favorite_added`): a field's types joined, required only where every
    variant requires it."""
    merged = {}
    for name in dict.fromkeys(n for fields in variants for n in fields):
        present = [fields[name] for fields in variants if name in fields]
        merged[name] = {
            "type": " | ".join(sorted({f["type"] for f in present})),
            "required": len(present) == len(variants) and all(f["required"] for f in present),
        }
    return merged


def _refs(node) -> set:
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.add(value.rsplit("/", 1)[-1])
            else:
                found |= _refs(value)
    elif isinstance(node, list):
        for value in node:
            found |= _refs(value)
    return found


# --------------------------------------------------------------------------- #
# Comparison — pure, in the parent process
# --------------------------------------------------------------------------- #

def contracted_operations(operations: dict, manifests: list) -> set:
    """What the manifests pin, matched as the contract test matches it."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from backend.tests.contracts.test_milo_mac_contract import _segments_match

    pinned = set()
    for manifest in manifests:
        for entry in manifest.get("rest", []):
            for key in operations:
                method, path = key.split(" ", 1)
                if method == entry["method"] and _segments_match(entry["path"], path):
                    pinned.add(key)
    return pinned


def _reachable(start: set, schemas: dict) -> set:
    seen, todo = set(), list(start)
    while todo:
        name = todo.pop()
        if name in seen or name not in schemas:
            continue
        seen.add(name)
        todo += schemas[name]["refs"]
    return seen


def _field_changes(label: str, old: dict, new: dict) -> list:
    lines = []
    for name in sorted(old.keys() - new.keys()):
        lines.append(f"{label}: field `{name}` removed")
    for name in sorted(new.keys() - old.keys()):
        lines.append(f"{label}: field `{name}` added ({new[name]['type']}"
                     f"{', required' if new[name]['required'] else ''})")
    for name in sorted(old.keys() & new.keys()):
        if old[name]["type"] != new[name]["type"]:
            lines.append(f"{label}: field `{name}` is now {new[name]['type']} "
                         f"(was {old[name]['type']})")
        if old[name]["required"] != new[name]["required"]:
            state = "required" if new[name]["required"] else "optional"
            lines.append(f"{label}: field `{name}` is now {state}")
    return lines


def diff(old: dict, new: dict, manifests: list) -> dict:
    """{"contracted": [...], "internal": [...]}, each a list of one-line changes."""
    contracted_ops = (contracted_operations(old["operations"], manifests)
                      | contracted_operations(new["operations"], manifests))
    contracted_schemas = (
        _reachable({r for k in contracted_ops & old["operations"].keys()
                    for r in old["operations"][k]["refs"]}, old["schemas"])
        | _reachable({r for k in contracted_ops & new["operations"].keys()
                      for r in new["operations"][k]["refs"]}, new["schemas"])
    )
    pinned = pinned_events(manifests)
    # A command is the apps' when either tree's snapshot spells it: a rename then
    # reads as the old name removed, under Contracted.
    sent = set(old.get("sent_commands", [])) | set(new.get("sent_commands", []))
    sent_params = set(old.get("sent_params", [])) | set(new.get("sent_params", []))
    old_cmds, new_cmds = old.get("commands", {}), new.get("commands", {})

    def app_sent(key: str) -> bool:
        return key.split("/", 1)[1] in sent

    # A model an event or a command carries is compared once, with the REST
    # schemas when it is one of them, and is contracted when a pinned event or a
    # command the apps send reaches it.
    old_models = {**old.get("models", {}), **old["schemas"]}
    new_models = {**new.get("models", {}), **new["schemas"]}
    for surface, models, cmds in ((old, old_models, old_cmds), (new, new_models, new_cmds)):
        roots = {r for pair in pinned & surface["events"].keys() for r in surface["events"][pair]["refs"]}
        roots |= {r for key, cmd in cmds.items() if app_sent(key) for r in cmd["refs"]}
        contracted_schemas |= _reachable(roots, models)
    out = {"contracted": [], "internal": []}

    def put(contracted: bool, line: str) -> None:
        out["contracted" if contracted else "internal"].append(line)

    old_ops, new_ops = old["operations"], new["operations"]
    for key in sorted(old_ops.keys() - new_ops.keys()):
        put(key in contracted_ops, f"`{key}` removed")
    for key in sorted(new_ops.keys() - old_ops.keys()):
        put(key in contracted_ops, f"`{key}` added")
    for key in sorted(old_ops.keys() & new_ops.keys()):
        a, b = old_ops[key], new_ops[key]
        if a.get("status") != b.get("status"):
            put(key in contracted_ops, f"`{key}`: answers {b.get('status')} (was {a.get('status')})")
        for part in ("request", "response"):
            if a[part] != b[part]:
                put(key in contracted_ops,
                    f"`{key}`: {part} is now {b[part] or 'empty'} (was {a[part] or 'empty'})")
        for param in sorted(set(a["params"]) - set(b["params"])):
            put(key in contracted_ops, f"`{key}`: parameter `{param}` removed")
        for param in sorted(set(b["params"]) - set(a["params"])):
            put(key in contracted_ops, f"`{key}`: parameter `{param}` added")

    for name in sorted(old_models.keys() - new_models.keys()):
        put(name in contracted_schemas, f"`{name}` removed")
    for name in sorted(new_models.keys() - old_models.keys()):
        put(name in contracted_schemas, f"`{name}` added")
    for name in sorted(old_models.keys() & new_models.keys()):
        a, b = old_models[name], new_models[name]
        for line in _field_changes(f"`{name}`", a["fields"], b["fields"]):
            put(name in contracted_schemas, line)
        for value in [v for v in a.get("values", []) if v not in b.get("values", [])]:
            put(name in contracted_schemas, f"`{name}`: value {json.dumps(value)} removed")
        for value in [v for v in b.get("values", []) if v not in a.get("values", [])]:
            put(name in contracted_schemas, f"`{name}`: value {json.dumps(value)} added")

    old_events, new_events = old["events"], new["events"]
    for pair in sorted(old_events.keys() - new_events.keys()):
        put(pair in pinned, f"WebSocket `{pair}` removed")
    for pair in sorted(new_events.keys() - old_events.keys()):
        put(pair in pinned, f"WebSocket `{pair}` added")
    for pair in sorted(old_events.keys() & new_events.keys()):
        same_as = old_events[pair].get("same_as")
        if same_as and same_as == new_events[pair].get("same_as") and (
            pair not in pinned or same_as in contracted_schemas
        ):
            continue
        for line in _field_changes(f"WebSocket `{pair}`", old_events[pair]["fields"],
                                   new_events[pair]["fields"]):
            put(pair in pinned, line)

    for key in sorted(old_cmds.keys() - new_cmds.keys()):
        put(app_sent(key), f"command `{key}` removed")
    for key in sorted(new_cmds.keys() - old_cmds.keys()):
        put(app_sent(key), f"command `{key}` added")
    for key in sorted(old_cmds.keys() & new_cmds.keys()):
        a, b = old_cmds[key]["fields"], new_cmds[key]["fields"]
        for field in sorted(a.keys() | b.keys()):
            # The apps break on a field they send that moved, and on a field
            # they do not send becoming required: every press then answers 400.
            newly_required = b.get(field, {}).get("required") and not a.get(field, {}).get("required")
            for line in _field_changes(f"command `{key}`", {field: a[field]} if field in a else {},
                                       {field: b[field]} if field in b else {}):
                put(app_sent(key) and (field in sent_params or bool(newly_required)), line)
    return out


def render(old_ref: str, new_ref: str, changes: dict) -> str:
    """`new_ref` pins the stability link: a tag, or the commit about to be tagged."""
    stability = f"https://github.com/leodurandfr/Milo/blob/{new_ref}/docs/integration.md#stability"
    lines = ["## API and integration changes", ""]
    if not changes["contracted"] and not changes["internal"]:
        return "\n".join(lines + [
            f"No change to the REST API, the WebSocket events or the source commands since {old_ref}.", ""
        ])
    lines += [f"Compared with {old_ref}.", ""]
    lines += ["### Contracted", "", "What Milo-Mac and Milo-iOS rely on; see "
              f"[Stability]({stability}).", ""]
    lines += [f"- {line}" for line in changes["contracted"]] or ["- None."]
    lines += ["", "### Internal", ""]
    lines += [f"- {line}" for line in changes["internal"]] or ["- None."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def _pinned_ref(ref: str) -> str:
    """A tag as named, any other ref as its commit, so a link to it never moves."""
    try:
        subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{ref}"],
                       cwd=REPO, check=True, capture_output=True)
        return ref
    except subprocess.CalledProcessError:
        pass
    result = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                            cwd=REPO, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"not a commit or a tag here: {ref}")
    return result.stdout.strip()


def _archive(ref: str, into: Path) -> None:
    into.mkdir(parents=True)
    with subprocess.Popen(["git", "archive", "--format=tar", ref, *ARCHIVED],
                          cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                tar.extractall(into, filter="data")
        except tarfile.ReadError:
            pass
        error = proc.stderr.read().decode().strip()
    if proc.returncode != 0:
        raise SystemExit(f"git archive {ref} failed: {error}")


def _surface(tree: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--extract", str(tree)],
        cwd=tree, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"could not read the API of {tree.name}")
    return json.loads(result.stdout.splitlines()[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("old", nargs="?", help="the previous release tag")
    parser.add_argument("new", nargs="?", help="the tag (or ref) being released")
    parser.add_argument("--extract", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.extract:
        surface = extract(args.extract.resolve())
        print(json.dumps(surface))
        return 0
    if not (args.old and args.new):
        parser.error("two refs are required")

    old_ref, new_ref = _pinned_ref(args.old), _pinned_ref(args.new)
    with tempfile.TemporaryDirectory(prefix="milo-api-diff-") as tmp:
        old_tree, new_tree = Path(tmp) / "old", Path(tmp) / "new"
        _archive(old_ref, old_tree)
        _archive(new_ref, new_tree)
        old, new = _surface(old_tree), _surface(new_tree)
        if not (old["sent_commands"] or new["sent_commands"]):
            raise SystemExit("neither tree's snapshots name a command the apps send")
        changes = diff(old, new, _manifests(old_tree) + _manifests(new_tree))
    sys.stdout.write(render(args.old, new_ref, changes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
