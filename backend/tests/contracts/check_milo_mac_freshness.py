#!/usr/bin/env python3
"""Freshness check: does milo_mac_contract.json still match Milo-Mac's real source?

The offline test (test_milo_mac_contract.py) guards the BACKEND side: it fails if
the backend drops something the manifest declares. This script guards the OTHER
direction — it re-extracts what Milo-Mac actually consumes from its Swift source
and diffs it against the manifest, so the manifest can't silently fall behind the
app it protects.

  * Milo-Mac consumes something ABSENT from the manifest  -> ERROR (exit 1):
    the backend could delete it with the offline test still green.
  * Manifest declares something Milo-Mac no longer uses    -> WARNING (exit 0):
    dead contract entry, safe to prune on the next conscious update.

Heuristic Swift parsing — intentionally. This runs in a NON-BLOCKING, scheduled
CI job (see .github/workflows/lint.yml), never gating a merge; if Milo-Mac
refactors its networking and the regexes drift, the job warns and a human
re-syncs the extractor + manifest. No new dependency, stdlib only.

Usage:
    python check_milo_mac_freshness.py /path/to/milo-mac/checkout
"""
import json
import re
import sys
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parent / "milo_mac_contract.json"


def _shape(path: str) -> str:
    """Canonical method-agnostic path shape: strip query, collapse params to {}."""
    path = path.split("?", 1)[0]
    path = re.sub(r"\\\(\w+\)", "{}", path)        # Swift interpolation \(x) -> {}
    path = re.sub(r"\{[^}]+\}", "{}", path)        # named template {x} -> {}
    return path.rstrip("/")


def extract_rest(api_swift: str) -> set[tuple[str, str]]:
    """{(METHOD, path_shape)} consumed by MiloAPIService.swift."""
    out: set[tuple[str, str]] = set()
    # Bound each buildURL(path:) to its enclosing func so the httpMethod we read
    # belongs to the same request.
    func_spans = [m.start() for m in re.finditer(r"\bfunc\s+\w+", api_swift)]
    func_spans.append(len(api_swift))
    for m in re.finditer(r'buildURL\(path:\s*"([^"]+)"\)', api_swift):
        pos = m.start()
        end = next((s for s in func_spans if s > pos), len(api_swift))
        body = api_swift[pos:end]
        method_m = re.search(r'httpMethod\s*=\s*"(\w+)"', body)
        method = method_m.group(1) if method_m else "GET"   # data(from:) defaults to GET
        out.add((method.upper(), _shape(m.group(1))))
    return out


def extract_ws(ws_swift: str) -> set[tuple[str, str]]:
    """{(category, type)} handled by WebSocketService.swift (incl. pre-switch ping)."""
    out: set[tuple[str, str]] = set()

    # Pre-switch keepalive guard: `category == "x" && eventType == "y"`.
    for cat, typ in re.findall(
        r'category\s*==\s*"(\w+)"\s*&&\s*eventType\s*==\s*"(\w+)"', ws_swift
    ):
        out.add((cat, typ))

    # The `switch category { case "x": ... eventType == "y" ... }` block.
    switch_m = re.search(r"switch\s+category\s*\{", ws_swift)
    if switch_m:
        region = ws_swift[switch_m.end():]
        # Split into case blocks keyed by category literal.
        cases = list(re.finditer(r'case\s+"(\w+)"\s*:', region))
        for i, c in enumerate(cases):
            cat = c.group(1)
            body_end = cases[i + 1].start() if i + 1 < len(cases) else len(region)
            body = region[c.end():body_end]
            for typ in re.findall(r'eventType\s*==\s*"(\w+)"', body):
                out.add((cat, typ))
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    checkout = Path(argv[1])
    api_swift = (checkout / "Milo Mac" / "MiloAPIService.swift").read_text()
    ws_swift = (checkout / "Milo Mac" / "WebSocketService.swift").read_text()

    manifest = json.loads(MANIFEST_PATH.read_text())

    # --- REST diff ---
    manifest_rest = {(e["method"].upper(), _shape(e["path"])) for e in manifest["rest"]}
    actual_rest = extract_rest(api_swift)

    # --- WS diff (enforced + consumer-side-only are both "known") ---
    manifest_ws_enforced = {(e["category"], e["type"]) for e in manifest["ws"]["events"]}
    manifest_ws_known = manifest_ws_enforced | {
        (e["category"], e["type"])
        for e in manifest["ws"].get("consumer_side_only_NOT_enforced", [])
    }
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

    print(f"Milo-Mac surface: {len(actual_rest)} REST, {len(actual_ws)} WS event handlers.")
    for w in warnings:
        print(f"::warning::[milo-mac-freshness] {w}")
    for e in errors:
        print(f"::error::[milo-mac-freshness] {e}")

    if errors:
        print(
            "\nMilo-Mac consumes surface the manifest does not track. Update "
            f"{MANIFEST_PATH.name} (and the backend, if the route/event is new) "
            "so the offline contract test protects it. See CLAUDE.md §'External "
            "API clients — Milo-Mac'."
        )
        return 1

    print("OK: manifest covers everything Milo-Mac consumes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
