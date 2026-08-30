#!/usr/bin/env python3
"""Freshness check: does Milo-Mac's real source still match what we vendor + enforce?

Two guards protect the Milo-Mac wire contract:

  * OFFLINE, BLOCKING (test_milo_mac_contract.py, every pytest run):
      - backend  ⊇ manifest                    (no route/event the manifest
        declares has been removed from the backend);
      - manifest == vendored Milo-Mac surface  (the surface extracted from the
        committed snapshot under vendor/milo-mac/ matches the manifest exactly).
    Both run with NO network — the vendored snapshot is the deterministic
    stand-in for the real app, and the extractors here are the shared parser.

  * NETWORK, NON-BLOCKING (this script, scheduled CI job):
      re-clones the real Milo-Mac and warns if the VENDORED SNAPSHOT (and thus
      the manifest) has fallen behind upstream. A human then refreshes
      vendor/milo-mac/ + the manifest in one conscious commit.

Heuristic Swift parsing — intentionally, stdlib only. Milo-Mac funnels REST
through `send()/fetchJSON()` helpers and dispatches WS via a
`switch (category, eventType)` tuple; the extractors model exactly those shapes.
If Milo-Mac refactors its networking the regexes drift — the offline test fails
loudly (exact-match against the vendored snapshot) and a human re-syncs them.

Usage:
    python check_milo_mac_freshness.py /path/to/milo-mac/checkout
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "milo_mac_contract.json"
VENDOR_DIR = HERE / "vendor" / "milo-mac"


def _collapse_interpolation(path: str) -> str:
    """Replace every Swift `\\(...)` with `{}`, nested parentheses included.

    Not a regex: the interpolated expression is arbitrary Swift, so
    `\\(Self.macForURL(macId))` closes on its SECOND `)`. A `\\(\\w+\\)` pattern
    matched a bare identifier only and left the tail of the call inside the
    path, which no FastAPI template can ever match — the two client-volume
    routes then read as missing from the manifest however the manifest spelled
    them.
    """
    out, i = [], 0
    while i < len(path):
        if path.startswith("\\(", i):
            depth, j = 1, i + 2
            while j < len(path) and depth:
                depth += {"(": 1, ")": -1}.get(path[j], 0)
                j += 1
            if depth:                          # unbalanced: not ours to rewrite
                out.append(path[i:])
                break
            out.append("{}")
            i = j
        else:
            out.append(path[i])
            i += 1
    return "".join(out)


def _shape(path: str) -> str:
    """Canonical method-agnostic path shape: strip query, collapse params to {}."""
    path = path.split("?", 1)[0]
    path = _collapse_interpolation(path)
    path = re.sub(r"\{[^}]+\}", "{}", path)        # named template {x} -> {}
    return path.rstrip("/")


def extract_rest(api_swift: str) -> set[tuple[str, str]]:
    """{(METHOD, path_shape)} consumed by MiloAPIService.swift.

    Every request funnels through two helpers:
        send("<path>", method: "<M>", ...)   # method omitted -> GET
        fetchJSON("<path>")                   # always GET (wraps send)
    so we read each call site's literal path and its optional method:. The helper
    *definitions* (`func send(_ path: String, ...)`) take a variable, not a string
    literal, so they don't match.
    """
    out: set[tuple[str, str]] = set()
    for m in re.finditer(
        r'\b(?:send|fetchJSON)\(\s*"([^"]+)"(?:\s*,\s*method:\s*"(\w+)")?',
        api_swift,
    ):
        method = (m.group(2) or "GET").upper()
        out.add((method, _shape(m.group(1))))
    return out


def extract_ws(ws_swift: str) -> set[tuple[str, str]]:
    """{(category, type)} handled by WebSocketService.swift.

    Dispatch is a tuple switch whose arms are literal pairs:
        switch (category, eventType) {
        case ("system", "state_changed"),
             ("source", "state_changed"): ...
        default: return
        }
    Capture every ("<cat>", "<type>") pair before the default arm.
    """
    out: set[tuple[str, str]] = set()
    sw = re.search(r"switch\s*\(\s*category\s*,\s*eventType\s*\)\s*\{", ws_swift)
    if not sw:
        return out
    region = ws_swift[sw.end():]
    cut = re.search(r"\bdefault\s*:", region)
    if cut:
        region = region[: cut.start()]
    for cat, typ in re.findall(r'\(\s*"(\w+)"\s*,\s*"(\w+)"\s*\)', region):
        out.add((cat, typ))
    return out


def manifest_surface(manifest: dict) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """(rest_shapes, ws_enforced) the manifest declares Milo-Mac depends on."""
    rest = {(e["method"].upper(), _shape(e["path"])) for e in manifest["rest"]}
    ws = {(e["category"], e["type"]) for e in manifest["ws"]["events"]}
    return rest, ws


def compute_diff(manifest: dict, api_swift: str, ws_swift: str):
    """(errors, warnings) comparing the manifest to a Milo-Mac source pair.

    error   = Milo-Mac consumes surface the manifest does NOT track (the backend
              could delete it with the offline test still green) -> must fix.
    warning = manifest tracks surface Milo-Mac no longer uses -> prunable on the
              next conscious snapshot refresh.
    """
    manifest_rest, manifest_ws_enforced = manifest_surface(manifest)
    manifest_ws_known = manifest_ws_enforced | {
        (e["category"], e["type"])
        for e in manifest["ws"].get("consumer_side_only_NOT_enforced", [])
    }
    actual_rest = extract_rest(api_swift)
    actual_ws = extract_ws(ws_swift)

    errors, warnings = [], []
    for method, path in sorted(actual_rest - manifest_rest):
        errors.append(f"REST {method} {path} is consumed by Milo-Mac but MISSING from the manifest.")
    for method, path in sorted(manifest_rest - actual_rest):
        warnings.append(f"REST {method} {path} is in the manifest but Milo-Mac no longer consumes it.")
    for cat, typ in sorted(actual_ws - manifest_ws_known):
        errors.append(f"WS {cat}/{typ} is consumed by Milo-Mac but MISSING from the manifest.")
    for cat, typ in sorted(manifest_ws_enforced - actual_ws):
        warnings.append(f"WS {cat}/{typ} is in the manifest but Milo-Mac no longer consumes it.")
    return errors, warnings


def _read_pair(root: Path) -> tuple[str, str]:
    """(api_swift, ws_swift) from a Milo-Mac checkout root (… / 'Milo Mac' / *.swift)."""
    base = root / "Milo Mac"
    return (
        (base / "MiloAPIService.swift").read_text(),
        (base / "WebSocketService.swift").read_text(),
    )


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    api_swift, ws_swift = _read_pair(Path(argv[1]))
    manifest = json.loads(MANIFEST_PATH.read_text())

    upstream = (extract_rest(api_swift), extract_ws(ws_swift))
    vendored = (
        extract_rest((VENDOR_DIR / "MiloAPIService.swift").read_text()),
        extract_ws((VENDOR_DIR / "WebSocketService.swift").read_text()),
    )
    snapshot_stale = upstream != vendored

    errors, warnings = compute_diff(manifest, api_swift, ws_swift)
    if snapshot_stale:
        warnings.append(
            "Vendored snapshot under vendor/milo-mac/ has fallen behind upstream "
            "Milo-Mac — refresh the two .swift files AND milo_mac_contract.json in "
            "one conscious commit (the offline test enforces manifest == snapshot)."
        )

    print(f"Upstream Milo-Mac surface: {len(upstream[0])} REST, {len(upstream[1])} WS event handlers.")
    for w in warnings:
        print(f"::warning::[milo-mac-freshness] {w}")
    for e in errors:
        print(f"::error::[milo-mac-freshness] {e}")

    # Either condition means a human must refresh the snapshot + manifest. Exit
    # non-zero so the (non-blocking) CI step flips to `failure` and opens the
    # tracking issue. `errors` is a subset of `snapshot_stale` in practice, but
    # both are reported for a precise human-readable diff.
    if errors or snapshot_stale:
        print(
            "\nThe vendored Milo-Mac snapshot (and thus the manifest) no longer "
            "matches upstream Milo-Mac. Refresh backend/tests/contracts/vendor/"
            "milo-mac/ + milo_mac_contract.json together. See CLAUDE.md §'External "
            "API clients — Milo-Mac'."
        )
        return 1

    print("OK: vendored snapshot + manifest are in sync with upstream Milo-Mac.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
