# backend/tests/contracts/test_api_stability.py
"""The `x-stability: contracted` marks in OpenAPI are exactly the companion apps' routes.

The published REST reference (docs/reference/rest.md) and every integrator reading
`/api/openapi.json` take the mark as the promise that an operation is checked
against Milo-Mac and Milo-iOS and that a change to it is announced in the release
notes. A mark missing from a pinned route under-promises; a mark left on a route
no manifest lists promises a check that does not exist.
"""
import json
from pathlib import Path

import pytest

from backend.api.route_helpers import CONTRACTED
from backend.tests.contracts.test_milo_mac_contract import _segments_match

CONTRACTS_DIR = Path(__file__).parent
MANIFESTS = ("milo_mac_contract.json", "milo_ios_contract.json")
_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


@pytest.fixture(scope="module")
def spec():
    from backend.main import app

    return app.openapi()


def _manifest_entries():
    return [
        (name, entry["method"].lower(), entry["path"])
        for name in MANIFESTS
        for entry in json.loads((CONTRACTS_DIR / name).read_text())["rest"]
    ]


def _operations(spec):
    return [
        (method, path, operation)
        for path, item in spec["paths"].items()
        for method, operation in item.items()
        if method in _HTTP_METHODS
    ]


def _pinned_operations(spec) -> set:
    pinned = set()
    for name, method, path in _manifest_entries():
        hits = [
            (m, p) for m, p, _ in _operations(spec)
            if m == method and _segments_match(path, p)
        ]
        assert len(hits) == 1, f"{name}: {method.upper()} {path} resolves to {hits}"
        pinned |= set(hits)
    return pinned


def test_the_contracted_marks_are_the_manifests(spec):
    """Both directions: every pinned operation is marked, and nothing else is."""
    pinned = _pinned_operations(spec)
    marked = {
        (m, p) for m, p, op in _operations(spec)
        if op.get("x-stability") == CONTRACTED["x-stability"]
    }
    assert len(pinned) > 20, "the manifests resolved to almost nothing — extractor drift?"

    assert pinned - marked == set(), "pinned by a manifest but not marked contracted"
    assert marked - pinned == set(), "marked contracted but pinned by no manifest"


def test_no_other_stability_value_is_published(spec):
    """`internal` is the absence of the mark; a third value would be a tier nobody defined."""
    values = {op["x-stability"] for _, _, op in _operations(spec) if "x-stability" in op}
    assert values == {CONTRACTED["x-stability"]}


def test_every_contracted_operation_documents_its_answer(spec):
    """An integrator reads the shape of a contracted answer from the schema.

    FastAPI's default for an untyped route is `application/json` with an empty
    schema, which is also what it printed for the image routes, whose body is
    never JSON.
    """
    undocumented = []
    for method, path, operation in _operations(spec):
        if operation.get("x-stability") != CONTRACTED["x-stability"]:
            continue
        content = operation["responses"]["200"].get("content", {})
        if not content or content.get("application/json") == {"schema": {}}:
            undocumented.append(f"{method.upper()} {path}")
    assert undocumented == []
