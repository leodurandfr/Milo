"""The request bodies Milo-Mac and Milo-iOS send, shared by both contract tests.

A manifest entry that sends a body names its top-level keys in `body`, and the
values it sends for an enum field in `body_values`. Two sources of truth hold
that list, and neither is the test: the backend's OpenAPI schema (each key is a
property of the route's request model, each value one of its enum's) and the
vendored Swift (each key is a dict key the consumer function actually builds).

A route test cannot see any of this. Pydantic ignores a key it does not declare
and a renamed field answers 422, so a coordinated rename of a request model and
the web UI's call leaves `test_rest_route_exists` green while every app request
on that route is refused.

Loaded by file path from the two tests, like the freshness scripts, so it
resolves whether or not pytest treats backend/tests/contracts as a package.
"""
import re


def resolve(node: dict, spec: dict) -> dict:
    """Follow `$ref` and unwrap the Optional `anyOf` FastAPI emits."""
    while True:
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            node = spec["components"]["schemas"][name]
            continue
        if "anyOf" in node:
            concrete = [b for b in node["anyOf"] if b.get("type") != "null"]
            if len(concrete) == 1:
                node = concrete[0]
                continue
        return node


def operation(spec: dict, entry: dict, segments_match) -> dict:
    """The one OpenAPI operation a manifest entry reaches."""
    method = entry["method"].lower()
    paths = [p for p, ops in spec["paths"].items() if method in ops and segments_match(entry["path"], p)]
    assert len(paths) == 1, f"{entry['method']} {entry['path']} matches {paths}"
    return spec["paths"][paths[0]][method]


def format_problems(entry: dict) -> list[str]:
    """What is malformed in an entry's `body` / `body_values`, if anything."""
    problems = []
    body = entry.get("body")
    if body is not None:
        if not body or len(set(body)) != len(body) or not all(isinstance(k, str) and k for k in body):
            problems.append(f"`body` must be a non-empty list of distinct keys, got {body!r}")
    values = entry.get("body_values", {})
    for key, sent in values.items():
        if key not in (body or []):
            problems.append(f"`body_values` names `{key}`, which `body` does not list")
        if not sent or len(set(sent)) != len(sent):
            problems.append(f"`body_values.{key}` must be a non-empty list of distinct values")
    return problems


def acceptance_problems(entry: dict, operation: dict, spec: dict) -> list[str]:
    """Why the backend would not take the body this entry says the app sends.

    A route that declares a required request body needs a `body` here, and a
    `body` on a route that declares none describes a request the backend never
    reads. Every key must be a property of the request model, every property
    the model requires must be sent, and every enum value must be one the model
    accepts.
    """
    request_body = operation.get("requestBody")
    body = entry.get("body")
    if request_body is None:
        return [f"`body` {body} on a route that declares no request body"] if body else []
    if not body:
        return (
            ["the route requires a request body and the entry names none of its keys"]
            if request_body.get("required") else []
        )

    model = resolve(request_body["content"]["application/json"]["schema"], spec)
    properties = model.get("properties", {})
    assert properties, f"request model with no properties: {model} — schema reader drift?"
    problems = [f"`{key}` is not a field of the request model" for key in body if key not in properties]
    problems += [
        f"the request model requires `{key}`, which the app does not send"
        for key in model.get("required", []) if key not in body
    ]
    for key, sent in entry.get("body_values", {}).items():
        if key not in properties:
            continue
        accepted = resolve(properties[key], spec).get("enum")
        if accepted is None:
            problems.append(f"`{key}` is pinned by value but the model declares no enum for it")
            continue
        problems += [
            f"`{key}={value}` is not one of the values the model accepts {accepted}"
            for value in sent if value not in accepted
        ]
    return problems


def swift_function(source: str, name: str) -> str:
    """The body of the one `func <name>` in the vendored Swift, braces included."""
    starts = [m.end() for m in re.finditer(rf"\bfunc {re.escape(name)}\s*[<(]", source)]
    assert len(starts) == 1, f"`func {name}` found {len(starts)} times in the vendored Swift"
    opening = source.index("{", starts[0])
    depth = 0
    for i in range(opening, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[opening:i + 1]
    raise AssertionError(f"`func {name}`: unbalanced braces")


_DICT_LITERAL = re.compile(r'\[\s*"\w+"\s*:')
_KEY = re.compile(r'(?:^|,)\s*"(\w+)"\s*:')
_SUBSCRIPT_WRITE = re.compile(r'\b\w+\??\["(\w+)"\]\s*=(?!=)')
_COMMENT = re.compile(r"/\*.*?\*/|(?<![:\"])//[^\n]*", re.S)


def sent_keys(function: str) -> set[str]:
    """The top-level keys of every dict literal a Swift function builds.

    The app writes its bodies in several shapes — `body: [...]` handed to a
    helper, a named `bodyDict`, `withJSONObject: [...]` inline, and a key added
    later by `body["session_id"] = …` — so this reads the dict literal and the
    subscript write rather than any one of the names around them. A nested dict
    (`"data": [...]`) is a value, not a key, and is skipped whole. A dict the
    function builds and never sends would be read too, and fail the equality
    loudly rather than shrink it.
    """
    code = _COMMENT.sub("", function)
    keys = set(_SUBSCRIPT_WRITE.findall(code))
    position = 0
    while match := _DICT_LITERAL.search(code, position):
        depth = 0
        for end in range(match.start(), len(code)):
            depth += {"[": 1, "]": -1}.get(code[end], 0)
            if depth == 0:
                break
        else:
            raise AssertionError("unbalanced dict literal in the vendored Swift")
        keys |= set(_KEY.findall(_flatten(code[match.start() + 1:end])))
        position = end + 1
    return keys


def _flatten(segment: str) -> str:
    """`segment` with every nested `[...]` removed, so only its own keys remain."""
    while (flat := re.sub(r"\[[^\[\]]*\]", "", segment)) != segment:
        segment = flat
    return segment


def _spelled(value: str, source: str) -> bool:
    """Whether the Swift spells an enum value: a string literal, or a case whose
    raw value is implicit (`case widget` sends "widget")."""
    return f'"{value}"' in source or re.search(rf"\bcase {re.escape(value)}\b", source) is not None


def snapshot_problems(entry: dict, source: str, built_by: dict | None = None) -> list[str]:
    """Where the entry's `body` and `body_values` disagree with the vendored Swift.

    `body` must be exactly the top-level keys the consumer functions build —
    none for a route they send no body to. `built_by` maps a consumer to the
    function that builds its dict when it does not build one itself.
    """
    built = set()
    for name in re.findall(r"\b\w+\.(\w+)\b", entry["consumer"]):
        built |= sent_keys(swift_function(source, (built_by or {}).get(name, name)))
    problems = []
    if built != set(entry.get("body", [])):
        problems.append(f"`body` {entry.get('body')} != the keys {entry['consumer']} sends {sorted(built)}")
    problems += [
        f"`{key}={value}` is spelled nowhere in the vendored Swift"
        for key, values in entry.get("body_values", {}).items()
        for value in values
        if not _spelled(value, source)
    ]
    return problems
