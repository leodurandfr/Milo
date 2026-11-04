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


def _resolve(schema: dict, components: dict) -> dict:
    """Follow `$ref`s into `components/schemas` until a schema with a body is reached."""
    while "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def _is_opaque(schema: dict, components: dict) -> bool:
    """True if the schema tells an integrator nothing about the answer's shape.

    The empty schema is FastAPI's default for an untyped route; a bare `object`
    is what a `-> Dict[str, Any]` annotation without a `response_model` prints.
    A union, an array or a map is opaque when what it holds is.
    """
    schema = _resolve(schema, components)
    if not schema:
        return True
    for combinator in ("oneOf", "anyOf", "allOf"):
        if combinator in schema:
            branches = [b for b in schema[combinator] if b.get("type") != "null"]
            return all(_is_opaque(branch, components) for branch in branches)
    if schema.get("type") == "array":
        return _is_opaque(schema.get("items", {}), components)
    if schema.get("type") != "object" or "properties" in schema:
        return False
    values = schema.get("additionalProperties")
    return not isinstance(values, dict) or _is_opaque(values, components)


def test_the_opacity_check_discriminates():
    """Red on every opaque shape, through a `$ref`, a union or an array; green on a typed one."""
    components = {
        "Opaque": {"type": "object", "additionalProperties": True},
        "Typed": {"type": "object", "properties": {"status": {"type": "string"}}},
    }
    opaque = {"$ref": "#/components/schemas/Opaque"}
    typed = {"$ref": "#/components/schemas/Typed"}
    for schema in (
        {},
        {"type": "object", "additionalProperties": True},
        opaque,
        {"anyOf": [opaque, {"type": "null"}]},
        {"type": "array", "items": opaque},
    ):
        assert _is_opaque(schema, components), schema
    for schema in (
        typed,
        {"anyOf": [typed, {"type": "null"}]},
        {"type": "array", "items": typed},
        {"type": "object", "additionalProperties": typed},
        {"type": "string"},
    ):
        assert not _is_opaque(schema, components), schema


def test_every_contracted_operation_documents_its_answer(spec):
    """An integrator reads the shape of a contracted answer from the schema.

    FastAPI's default for an untyped route is `application/json` with an empty
    schema, which is also what it printed for the image routes, whose body is
    never JSON. A route annotated `-> Dict[str, Any]` with no `response_model`
    publishes a bare `object` instead, which documents just as little — the
    radio answer's missing `status` was invisible in the published reference.
    Only the answer's own level is held: a Subsonic object proxied whole under a
    declared key (`artist`, `album`) stays a raw dict on purpose.
    """
    components = spec.get("components", {}).get("schemas", {})
    undocumented = []
    examined = 0
    for method, path, operation in _operations(spec):
        if operation.get("x-stability") != CONTRACTED["x-stability"]:
            continue
        content = operation["responses"]["200"].get("content", {})
        if not content:
            undocumented.append(f"{method.upper()} {path}")
            continue
        if "application/json" not in content:
            continue
        examined += 1
        if _is_opaque(content["application/json"].get("schema", {}), components):
            undocumented.append(f"{method.upper()} {path}")
    assert examined > 20, "almost no contracted JSON answer was examined — extractor drift?"
    assert undocumented == []
