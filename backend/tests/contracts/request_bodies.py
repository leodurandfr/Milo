"""The request bodies Milo-Mac and Milo-iOS send, shared by both contract tests.

A manifest entry that sends a body names its top-level keys in `body`, and the
values it sends for an enum field in `body_values`. Two sources of truth hold
that list, and neither is the test: the backend's OpenAPI schema (each key is a
property of the route's request model, each value one of its enum's) and the
vendored Swift (each key is a dict key the consumer function actually builds).

A body that carries a command (`/api/audio/control/*`) also names, in
`data_keys`, the keys of its `data` per command: they are validated by the
command's params model in the source, not by the route's request model, so the
OpenAPI schema cannot see them. They are resolved against `COMMANDS` and held
equal to the `"data": [...]` literals the Swift builds.

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
    data_keys = entry.get("data_keys")
    if ("data" in (body or [])) != (data_keys is not None):
        problems.append("`data_keys` goes with, and only with, a `body` that lists `data`")
    for command, keys in (data_keys or {}).items():
        if not command or not keys or len(set(keys)) != len(keys):
            problems.append(f"`data_keys.{command}` must name a command and a non-empty list of distinct keys")
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


_DATA_VALUE = re.compile(r'"data"\s*:\s*\[')
_COMMAND_LITERAL = re.compile(r'"command"\s*:\s*"(\w+)"')


def sent_data(function: str) -> list[tuple[str | None, set[str]]]:
    """(command, keys) for every `"data": [...]` a Swift function builds.

    `command` is the literal the same dict sends under `"command"`, or None
    when the name is a variable (Milo-iOS picks it per source). An empty
    `[String: Any]()` reads as no keys, which is what Milo-Mac sends with a
    transport command.
    """
    code = _COMMENT.sub("", function)
    found = []
    for match in _DATA_VALUE.finditer(code):
        start = match.end() - 1
        keys = set(_KEY.findall(_flatten(code[start + 1:_closing(code, start)])))
        depth, opening = 0, None
        for i in range(match.start() - 1, -1, -1):
            depth += {"]": 1, "[": -1}.get(code[i], 0)
            if depth < 0:
                opening = i
                break
        command = None
        if opening is not None:
            # The whole enclosing dict, nested literals removed: `"command"`
            # may sit after `"data"` as well as before it.
            command = _COMMAND_LITERAL.search(_flatten(code[opening + 1:_closing(code, opening)]))
        found.append((command.group(1) if command else None, keys))
    return found


def _closing(code: str, opening: int) -> int:
    """The index of the `]` that closes the `[` at `opening`."""
    depth = 0
    for i in range(opening, len(code)):
        depth += {"[": 1, "]": -1}.get(code[i], 0)
        if depth == 0:
            return i
    raise AssertionError("unbalanced literal in the vendored Swift")


def source_commands() -> dict:
    """Each source's path name → its `COMMANDS`, reached as dependencies.py does."""
    import importlib

    from backend.core.models.audio_state import AudioSource

    commands = {}
    for member in AudioSource:
        if member is AudioSource.NONE:
            continue
        package = importlib.import_module(f"backend.sources.{member.value}")
        commands[member.value] = getattr(package, package.__all__[0]).COMMANDS
    assert sum(1 for table in commands.values() if table) >= 8, f"{commands} — source reader drift?"
    return commands


def command_data_problems(entry: dict, commands: dict) -> list[str]:
    """Why a source would refuse the `data` this entry says the app sends.

    `commands` maps each source's path name to its `COMMANDS`. A path ending in
    a parameter reaches every source that declares the command (Milo-iOS sends
    `seek` and `skip` to whichever source the card names); a literal one, that
    source alone. Every key must be a field of the command's params model,
    which refuses any other (HTTP 400), and every field it requires must be sent.
    """
    target = entry["path"].rsplit("/", 1)[-1]
    reached = commands if target.startswith("{") else {target: commands[target]}
    problems = []
    for command, keys in (entry.get("data_keys") or {}).items():
        takers = {name: table[command] for name, table in reached.items() if command in table}
        if not takers:
            problems.append(f"no source the route reaches declares `{command}`")
        for name, model in takers.items():
            if model is None:
                problems.append(f"{name} `{command}` takes no params: the {keys} the app sends do nothing")
                continue
            fields = model.model_fields
            problems += [f"`{key}` is not a field of {name} `{command}`" for key in keys if key not in fields]
            problems += [
                f"{name} `{command}` requires `{field}`, which the app does not send"
                for field, info in fields.items() if info.is_required() and field not in keys
            ]
    return problems


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
    data = []
    for name in re.findall(r"\b\w+\.(\w+)\b", entry["consumer"]):
        function = swift_function(source, (built_by or {}).get(name, name))
        built |= sent_keys(function)
        data += sent_data(function)
    problems = []
    if built != set(entry.get("body", [])):
        problems.append(f"`body` {entry.get('body')} != the keys {entry['consumer']} sends {sorted(built)}")
    if "data" in built:
        pinned = entry.get("data_keys") or {}
        sent = set().union(*(keys for _, keys in data))
        if sent != set().union(*map(set, pinned.values())):
            problems.append(f"`data_keys` {pinned} != the `data` keys {entry['consumer']} sends {sorted(sent)}")
        problems += [
            f"`data_keys.{command}` {sorted(pinned.get(command, []))} != {sorted(keys)}, what the app sends with it"
            for command, keys in data
            if command is not None and keys != set(pinned.get(command, []))
        ]
        problems += [
            f"`data_keys` names `{command}`, which the vendored Swift never spells"
            for command in pinned if f'"{command}"' not in source
        ]
    problems += [
        f"`{key}={value}` is spelled nowhere in the vendored Swift"
        for key, values in entry.get("body_values", {}).items()
        for value in values
        if not _spelled(value, source)
    ]
    return problems
