# backend/tests/architecture/test_wire_conventions.py
"""Whole-surface conformance for the REST wire contract.

The REST API is the most public part of Milō — two client apps consume it and
Milo-Mac has no versioning — and the conventions CLAUDE.md states for it
(kebab-case paths, one spelling per resource, snake_case Pydantic fields, the
`status` envelope) had never been checked across all of it. A convention nobody
enforces holds until the first hurried route.

These tests derive the surface from the live FastAPI app and from the AST of
`backend/`, never from a hand-written list, and each extractor asserts its own
output is non-trivial before anything is asserted about it: a broken scan must
fail loudly, not pass on an empty surface.

Scope note: they check what is mechanically decidable. Whether a verb *matches
its semantics*, or whether an endpoint is `/status`-style enough to earn the
HTTP-200-error resilience pattern, is a judgement call and stays a review
concern.
"""
import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


# --------------------------------------------------------------------------- #
# Route surface, from the live app.
# --------------------------------------------------------------------------- #

def _route_table():
    """(method, path) for every /api route, prefixes resolved.

    Taken from the OpenAPI schema for the same reason the Milo-Mac contract test
    does: FastAPI 0.137 stopped flattening `include_router()` into `app.routes`,
    so iterating that tree silently sees zero routes, while OpenAPI paths are
    fully prefix-resolved.
    """
    from backend.main import app

    table = []
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith("/api"):
            continue
        for method in {m.upper() for m in operations} & HTTP_METHODS:
            table.append((method, path))
    return sorted(table)


ROUTES = _route_table()
assert len(ROUTES) > 150, f"only {len(ROUTES)} API routes found — extractor broken?"


@pytest.mark.parametrize("method,path", ROUTES, ids=[f"{m} {p}" for m, p in ROUTES])
def test_path_segments_are_kebab_case(method, path):
    """A `_` in a path is the one casing slip that is invisible until a client
    404s on the wrong guess. All 167 routes are kebab-case; keep it that way."""
    literal = [s for s in path.strip("/").split("/") if not s.startswith("{")]
    offenders = [s for s in literal if "_" in s]
    assert not offenders, f"{method} {path}: use kebab-case, not {offenders}"


def test_a_resource_has_one_spelling():
    """`GET /server-config` + `POST /server/config` was one resource under two
    names — the read and the write drifted apart because nothing tied them.

    Heuristic: no path may be the parent of another that differs only by turning
    the last kebab word into a segment (`a/b-c` vs `a/b/c`).
    """
    paths = {p for _, p in ROUTES}
    collisions = []
    for path in paths:
        head, _, last = path.rpartition("/")
        if "-" not in last or last.startswith("{"):
            continue
        for split in range(1, last.count("-") + 1):
            parts = last.split("-")
            alias = f"{head}/{'-'.join(parts[:split])}/{'-'.join(parts[split:])}"
            if alias in paths:
                collisions.append((path, alias))
    assert not collisions, f"same resource under two spellings: {collisions}"


def test_no_source_status_or_restart_routes():
    """Both are explicitly forbidden: status is WS-only, restart is systemd's job.

    `/api/system/*` and the hardware routers are exempt — those are the appliance
    and its peripherals, not audio sources.
    """
    source_prefixes = (
        "/api/radio", "/api/podcast", "/api/cd", "/api/airplay", "/api/dlna",
        "/api/qobuz", "/api/music-library", "/api/spotify", "/api/bluetooth",
        "/api/mac",
    )
    offenders = [
        (m, p) for m, p in ROUTES
        if p.startswith(source_prefixes) and p.rsplit("/", 1)[-1] in {"status", "restart"}
    ]
    assert not offenders, f"forbidden source route(s): {offenders}"


# --------------------------------------------------------------------------- #
# Pydantic surface, from the AST of backend/.
# --------------------------------------------------------------------------- #

def _pydantic_fields():
    """(file, class, field) for every annotated field on a BaseModel subclass.

    AST rather than imports so it covers models in modules the test never loads,
    including ones no route references yet.
    """
    out = []
    for py in sorted(BACKEND_ROOT.rglob("*.py")):
        if "/tests/" in str(py) or "__pycache__" in str(py):
            continue
        try:
            tree = ast.parse(py.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if not any("BaseModel" in ast.unparse(b) for b in node.bases):
                continue
            rel = str(py.relative_to(BACKEND_ROOT))
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    name = stmt.target.id
                    if not name.isupper():  # ClassVar constants are not wire fields
                        out.append((rel, node.name, name))
    return out


PYDANTIC_FIELDS = _pydantic_fields()
assert len(PYDANTIC_FIELDS) > 300, f"only {len(PYDANTIC_FIELDS)} model fields found — extractor broken?"


def test_no_pydantic_field_is_camel_case():
    """Every wire field is snake_case, request and response alike.

    Pydantic serializes the field name verbatim, so one camelCase field ships a
    key no documented consumer reads — and `alias=` to paper over it would mean
    two spellings for one field.
    """
    offenders = [
        f"{f}::{cls}.{field}"
        for f, cls, field in PYDANTIC_FIELDS
        if any(c.isupper() for c in field)
    ]
    assert not offenders, f"non-snake_case wire fields: {offenders}"


def test_settings_category_shapes_live_in_one_module():
    """The `*Config` payload of a settings category has exactly one home.

    Its shape travels on two surfaces (`GET /api/settings/bulk` and the
    `settings/<name>_changed` event). It used to be declared three times —
    request, response, event — so adding a field meant three edits and tolerated
    two. `core/models/settings_config.py` is now the single home; a second
    declaration of the same name anywhere else is that drift coming back.
    """
    home = "core/models/settings_config.py"
    canonical = {cls for f, cls, _ in PYDANTIC_FIELDS if f == home}
    assert len(canonical) > 10, f"settings_config.py holds only {canonical} — moved or renamed?"

    duplicates = {
        f"{f}::{cls}"
        for f, cls, _ in PYDANTIC_FIELDS
        if cls in canonical and f != home
    }
    assert not duplicates, (
        f"{sorted(duplicates)} redeclare a shape owned by {home} — import it instead."
    )


# --------------------------------------------------------------------------- #
# Router placement and prefix ownership.
# --------------------------------------------------------------------------- #

def _declared_routers():
    """(repo-relative file, prefix) for every APIRouter constructed in backend/.

    AST, not the live app: an unmounted router is still a router someone will
    mount, and the point is where the file lives.
    """
    found = []
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(), str(path))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "APIRouter"):
                continue
            prefix = ""
            for kw in node.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    prefix = kw.value.value
            found.append((str(path.relative_to(BACKEND_ROOT.parent)), prefix))
    return found


ROUTERS = _declared_routers()
assert len(ROUTERS) > 20, f"only {len(ROUTERS)} routers found — extractor broken?"


def test_routers_live_in_api_or_beside_their_subsystem():
    """A router belongs to `api/`, to a source, or to a hardware feature.

    Those are the three homes the backend actually uses, and each owns its whole
    prefix. `core/` is infrastructure: it held one router, serving a sub-prefix
    of `api/routing.py`'s namespace from a different layer, and it was also the
    closing edge of an import cycle `api/route_helpers` had to be worked around
    for. Nothing about `core/` makes a router impossible to add there again.
    """
    strays = [
        f
        for f, _ in ROUTERS
        if not (
            f.startswith("backend/api/")
            or (f.startswith("backend/sources/") and f.endswith("/routes.py"))
            or (f.startswith("backend/hardware/") and f.endswith("_routes.py"))
        )
    ]
    assert not strays, (
        f"routers outside the three homes: {sorted(set(strays))} — put it in "
        f"backend/api/, or next to its source/hardware subsystem."
    )


def test_no_two_routers_split_one_prefix():
    """One prefix, one owner.

    `/api/routing` used to be served by two routers in two layers, and a quarter
    of the commits touching either touched both. A prefix that is a strict
    parent of another's is the same surface edited from two files.

    `/api` itself is exempt: the health router deliberately sits at the root.
    """
    prefixes = {p for _, p in ROUTERS if p and p != "/api"}
    nested = sorted(
        (child, parent)
        for parent in prefixes
        for child in prefixes
        if child != parent and child.startswith(parent + "/")
    )
    assert not nested, (
        f"one router's prefix is nested inside another's: {nested} — merge them "
        f"into the file that owns the namespace."
    )


# --------------------------------------------------------------------------- #
# Response envelope.
# --------------------------------------------------------------------------- #

def _route_handler_sources():
    """(method, path, handler source) for every /api route."""
    import inspect

    from backend.main import app

    def walk(router, prefix=""):
        found = []
        for route in getattr(router, "routes", []):
            if type(route).__name__ == "_IncludedRouter":
                inner_prefix = getattr(route.include_context, "prefix", "") or ""
                found.extend(walk(route.original_router, prefix + inner_prefix))
                continue
            methods = sorted(set(getattr(route, "methods", ())) & HTTP_METHODS)
            if not methods or getattr(route, "endpoint", None) is None:
                continue
            path = prefix + route.path
            if not path.startswith("/api"):
                continue
            try:
                src = inspect.getsource(route.endpoint)
            except (OSError, TypeError):
                src = ""
            found.append(("/".join(methods), path, src))
        return found

    return walk(app)


HANDLERS = _route_handler_sources()
assert len(HANDLERS) > 150, f"only {len(HANDLERS)} handlers found — extractor broken?"
assert all(src for _, _, src in HANDLERS), "a handler's source could not be read — extractor broken?"


def _returns_a_success_dict(fn: ast.AST) -> bool:
    """True if `fn` has a `return` of a dict literal carrying a "success" key."""
    return any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Dict)
        and any(
            isinstance(key, ast.Constant) and key.value == "success"
            for key in node.value.keys
        )
        for node in ast.walk(fn)
    )


def _success_dict_producers():
    """Every backend function name that can return a dict with a "success" key.

    Names, not qualified symbols: a route reaches its service through an
    instance attribute (`source.station_data.add_custom_station`), which no
    offline resolver can bind to a class. Matching on the method name
    over-approximates — two same-named functions make the check stricter, never
    laxer — and that is the right direction for a guardrail.
    """
    producers = set()
    for path in BACKEND_ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _returns_a_success_dict(node):
                producers.add(node.name)
    return producers


SUCCESS_PRODUCERS = _success_dict_producers()
assert "add_custom_station" in SUCCESS_PRODUCERS, "producer scan broken?"


def _handler_success_flag(src: str) -> str | None:
    """Describe how `src` answers with a `success` flag, or None if it doesn't.

    Two ways a route can do it: build the dict itself, or hand back one a
    service built. The second is the one the string-matching version missed —
    `return result` reads as clean at the route and is the forbidden envelope on
    the wire.
    """
    import textwrap

    fn = ast.parse(textwrap.dedent(src)).body[0]
    if _returns_a_success_dict(fn):
        return "builds it"

    returned = {
        node.value.id for node in ast.walk(fn)
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Name)
    }
    if not returned:
        return None

    for node in ast.walk(fn):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id in returned):
            continue
        value = node.value.value if isinstance(node.value, ast.Await) else node.value
        if not isinstance(value, ast.Call):
            continue
        func = value.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in SUCCESS_PRODUCERS:
            return f"returns {name}()'s dict"
    return None


def test_the_success_flag_extractor_discriminates():
    """Both directions of the check above, on hand-written handlers.

    A guardrail that only ever runs against a clean tree proves nothing: this
    pins that it still catches the literal form, that it follows a value one hop
    into the service that built it, and that it leaves the two legitimate shapes
    alone — the documented envelope, and a command result handed back through
    `run_source_command`.
    """
    caught = {
        "literal": 'async def r():\n    return {"success": True}\n',
        "via a service": (
            "async def r():\n"
            "    result = await source.station_data.add_custom_station(name=name)\n"
            "    return result\n"
        ),
    }
    allowed = {
        "envelope": 'async def r():\n    return {"status": "success", "station": s}\n',
        "command result": (
            "async def r():\n"
            "    result = await run_source_command(source, 'play', {})\n"
            "    return result\n"
        ),
    }

    assert {k: bool(_handler_success_flag(v)) for k, v in caught.items()} == {
        "literal": True,
        "via a service": True,
    }
    assert {k: _handler_success_flag(v) for k, v in allowed.items()} == {
        "envelope": None,
        "command result": None,
    }


def test_no_route_returns_a_bare_success_flag():
    """`{"success": bool}` is not an envelope — it is a failure a consumer misses.

    The documented envelope is `"status": "success"` plus HTTPException on a real
    failure. Four podcast routes used to answer `{"success": <always True>}`, a
    flag whose False branch was unreachable and which two of its three callers
    already ignored. `success` inside a *command result* (`run_source_command`'s
    return value) is a different, internal contract and is unaffected — it
    reaches this check as a `return` of a name bound to `run_source_command`,
    which builds no dict of its own and is therefore not a producer.

    The check follows the value one hop into the service that built it:
    `POST /api/radio/custom/add` answered `{"success": True, "station": …}` for
    a year under a literal-only extractor, because the route said `return
    result`.
    """
    offenders = [
        f"{m} {p} ({how})" for m, p, src in HANDLERS
        if (how := _handler_success_flag(src))
    ]
    assert not offenders, (
        f"routes answering with a success flag: {offenders} — use "
        f'{{"status": "success"}} and raise on failure.'
    )
