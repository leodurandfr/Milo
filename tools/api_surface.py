"""What tools/docs/generate_reference.py and tools/release/api_diff.py read the same way.

The reference pages say what is contracted and the release notes list what
moved in it, so the two must name a schema, and decide what the companion apps
rely on, in one way. Nothing here imports `backend` at module level: api_diff
runs these functions inside a tree extracted from another tag.
"""
import importlib
import json
import re
from pathlib import Path

CONTRACTS = Path("backend") / "tests" / "contracts"
VENDOR = CONTRACTS / "vendor"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
SUCCESS_CODES = ("200", "201", "204")


def schema_name(schema: dict) -> str:
    """An OpenAPI or JSON schema as a type name, its values included: a Literal or
    an enum is its values, because adding one breaks a decoder that refuses the
    unknown. Unions and values are sorted, so a reordering is not a change."""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(sorted(json.dumps(v) for v in schema["enum"]))
    if schema.get("type") == "array":
        return schema_name(schema.get("items", {})) + "[]"
    for union in ("anyOf", "oneOf"):
        if union in schema:
            return " | ".join(sorted(schema_name(s) for s in schema[union]))
    if len(schema.get("allOf", [])) == 1:
        return schema_name(schema["allOf"][0])
    return schema.get("type", "any")


def body(content: dict) -> str:
    """A request or response body: its schema's name, or its media types."""
    if not content:
        return ""
    if "application/json" in content:
        schema = content["application/json"].get("schema", {})
        return schema_name(schema) if schema else "untyped JSON"
    return ", ".join(sorted(content))


def success_response(operation: dict) -> tuple:
    """(code, response) of an operation's success answer, or (None, {})."""
    responses = operation.get("responses", {})
    return next(((c, responses[c]) for c in SUCCESS_CODES if c in responses), (None, {}))


def manifests(tree: Path) -> list:
    """The companion apps' manifests in `tree` — refused empty, since no manifest
    would file every change under Internal."""
    found = [json.loads(p.read_text()) for p in sorted((tree / CONTRACTS).glob("milo_*_contract.json"))]
    if not found:
        raise SystemExit(f"no companion-app manifest under {tree / CONTRACTS}")
    return found


def pinned_events(manifest_list: list) -> set:
    """`category/type` of every WebSocket event a manifest pins."""
    return {
        f"{e['category']}/{e['type']}"
        for manifest in manifest_list
        for e in manifest.get("ws", {}).get("events", [])
    }


def ws_event_classes() -> list:
    """Every concrete event class: one per `(category, type)`, two for a pair two
    sources share."""
    from backend.core.models import ws_events

    found = []

    def walk(cls):
        for sub in cls.__subclasses__():
            if sub.__module__ == ws_events.__name__ and "TYPE" in vars(sub):
                found.append(sub)
            walk(sub)

    walk(ws_events.WsEvent)
    return found


def source_classes() -> list:
    """(id, class) for every source, in `AudioSource` order."""
    from backend.core.models.audio_state import AudioSource

    found = []
    for source in AudioSource:
        if source is AudioSource.NONE:
            continue
        module = importlib.import_module(f"backend.sources.{source.value}")
        found.append((source.value, getattr(module, module.__all__[0])))
    return found


def commands_the_apps_send(tree: Path) -> tuple:
    """(command names, parameter names) the companion apps send to the control route.

    Read from the vendored Swift snapshots: every quoted lowercase literal that is
    also a command some source declares, and of those commands' parameters, every
    one the apps also spell. A literal that happens to match counts too — a
    command marked contracted that no app sends is the safe side of the error.
    Empty for a snapshot that names its commands only through variables (v0.1.0's),
    so a caller refuses an empty answer where it has no other tree to rely on.
    """
    literals = set()
    for path in (tree / VENDOR).rglob("*.swift"):
        literals |= set(re.findall(r'"([a-z_]+)"', path.read_text()))
    commands, params = set(), set()
    for _, cls in source_classes():
        for name, model in cls.COMMANDS.items():
            if name in literals:
                commands.add(name)
                if model is not None:
                    params |= set(model.model_fields) & literals
    return commands, params
