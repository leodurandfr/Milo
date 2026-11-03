"""The routes `main.py` mounts are the routes `routes/` declares.

The server-side contract (`backend/tests/contracts/test_milo_client_contract.py`)
reads the `@router.<verb>` decorators in `routes/*.py` and never `main.py`, the
only list of `include_router` calls. So dropping one mount together with its
import passes ruff (no F401 left behind) and that contract alike. The push that
carries such a commit is still received by the old handler; after it, the
satellite answers 404 on what it no longer mounts: with `create_app_update_router`
gone no update reaches the fleet again and each unit needs SSH, with
`create_probe_router` the multiroom calibration fails, with `create_system_router`
the standby WiFi, the SSH switch and the password do.

Consumer: the server, through `EqualizerClientProxyService` and
`SatelliteUpdateService`.
"""
import ast
from pathlib import Path

import main

ROUTES_DIR = Path(__file__).resolve().parents[1] / "routes"
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _router_prefix(path, tree):
    """The `prefix=` of the file's one `APIRouter(...)`, or "" without one."""
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == "APIRouter"
    ]
    assert len(calls) <= 1, f"{path.name} builds {len(calls)} APIRouters; one prefix per file is assumed"
    if not calls:
        return ""
    for keyword in calls[0].keywords:
        if keyword.arg == "prefix":
            assert isinstance(keyword.value, ast.Constant), f"{path.name}: prefix is not a literal"
            return keyword.value.value
    return ""


def _declared_routes():
    """(method, path) for every `@router.<verb>("…")` in routes/*.py."""
    routes = []
    for path in sorted(ROUTES_DIR.glob("*.py")):
        tree = ast.parse(path.read_text())
        prefix = _router_prefix(path, tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
                    continue
                method = dec.func.attr.upper()
                assert dec.func.attr != "api_route", f"{path.name}:{dec.lineno}: api_route is not read"
                if method not in HTTP_METHODS:
                    continue
                arg = dec.args[0] if dec.args else None
                assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), (
                    f"{path.name}:{dec.lineno}: route path is not a positional literal"
                )
                routes.append((method, prefix + arg.value))
    duplicates = sorted({route for route in routes if routes.count(route) > 1})
    assert duplicates == [], f"declared twice, so one mount can go unnoticed: {duplicates}"
    return set(routes)


def _served_routes():
    """(method, path) for every operation the built app publishes.

    Read from the OpenAPI document, not `app.routes`: FastAPI 0.138 lists the
    included routers there as `_IncludedRouter` entries with no `.methods`, and
    CI installs whatever FastAPI is newest.
    """
    return {
        (method.upper(), path)
        for path, operations in main.app.openapi()["paths"].items()
        for method in operations
        if method.upper() in HTTP_METHODS
    }


def test_every_declared_route_is_served_by_the_app():
    declared = _declared_routes()
    served = _served_routes()
    for name, surface in (("declared", declared), ("served", served)):
        assert len(surface) >= 20, f"only {len(surface)} {name} routes read: the extractor is broken"
    for anchor in (("POST", "/app/update"), ("GET", "/probe")):
        assert anchor in declared, f"{anchor} not read from routes/: the extractor is broken"

    assert sorted(declared - served) == [], "declared in routes/ but not mounted by main.py"
    assert sorted(served - declared) == [], "served by main.app but declared outside routes/"
