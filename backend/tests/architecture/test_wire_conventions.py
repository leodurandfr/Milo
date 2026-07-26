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


def test_no_route_returns_a_bare_success_flag():
    """`{"success": bool}` is not an envelope — it is a failure a consumer misses.

    The documented envelope is `"status": "success"` plus HTTPException on a real
    failure. Four podcast routes used to answer `{"success": <always True>}`, a
    flag whose False branch was unreachable and which two of its three callers
    already ignored. `success` inside a *command result* (`run_source_command`'s
    return value) is a different, internal contract and is unaffected.
    """
    offenders = [
        f"{m} {p}" for m, p, src in HANDLERS
        if 'return {"success"' in src or "return {'success'" in src
    ]
    assert not offenders, (
        f"routes returning a bare success flag: {offenders} — use "
        f'{{"status": "success"}} and raise on failure.'
    )
