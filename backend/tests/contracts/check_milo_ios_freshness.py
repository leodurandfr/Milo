#!/usr/bin/env python3
"""Freshness check: does Milo-iOS' real source still match what we vendor + enforce?

Same two-guard shape as `check_milo_mac_freshness.py`, for the OTHER external
client (github.com/leodurandfr/Milo-iOS — an iOS app whose surface is a widget
extension and App Intents):

  * OFFLINE, BLOCKING (test_milo_ios_contract.py, every pytest run):
      - backend  ⊇ manifest             (no route the manifest declares has been
        removed from the backend);
      - manifest == snapshot            (the surface extracted from the
        committed snapshot matches `rest` + `_broken_calls` exactly).
  * NETWORK, MANUAL (this script, run by hand):
      compares the vendored snapshot against a real checkout and says whether it
      has fallen behind. Deliberately NOT a CI job, unlike the Milo-Mac twin: a
      six-route app with one author, changed by the same person who would fix
      the drift, does not earn a clone on every push to main plus a weekly cron.
      Run it whenever Milo-iOS changes — the offline test cannot see upstream,
      so nothing else will tell you the snapshot is stale.

Milo-iOS has NO WebSocket surface: every consumer is a process that lives for
seconds (a WidgetKit timeline entry, an App Intent), and none can hold a socket
open. So the contract is REST-only and this script has no `extract_ws` twin.

Two call shapes, and both must be read or a route goes missing in silence:

    get(path: "/api/volume/state")                     # private helper, method
    post(path: "/api/audio/source/\\(name)")            # is the function name
    URL(string: baseURL() + "/api/volume/adjust")      # hand-built, method is a
    request.httpMethod = "POST"                        # nearby assignment

`fireAdjustVolume` uses the second form for its fire-and-forget path, so an
extractor modelling only the helper reports 5 routes instead of 6 — and a
missing route reads as "the app does not use it", which is the exact silence
this whole contract exists to break. `test_the_extractor_reads_both_call_shapes`
pins both against the vendored snapshot.

Path canonicalisation (`_shape`, `_collapse_interpolation`) is imported from the
Milo-Mac freshness script rather than copied: Swift string interpolation is one
problem with one right answer, and two spellings of it would drift the way the
two copies of a sudoers policy did.

Usage:
    python check_milo_ios_freshness.py /path/to/milo-ios/checkout
"""
import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR_DIR = HERE / "vendor" / "milo-ios"

# The two files that carry the surface: one names the routes, the other names
# the response fields the app decodes. Mirrors the Mac's REST+WS pair.
SOURCE_FILES = ("MiloAPIClient.swift", "Models.swift")


def _load_shared():
    """Swift path canonicalisation, from the Milo-Mac script — one implementation.

    Loaded by file path for the same reason that script is: it resolves
    identically whether or not pytest treats this directory as a package.
    """
    path = HERE / "check_milo_mac_freshness.py"
    spec = importlib.util.spec_from_file_location("milo_mac_freshness", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_SHARED = _load_shared()
_shape = _SHARED._shape

# get(path: "…") / post(path: "…") — the method IS the helper's name. The helper
# *definitions* take `path: String`, a parameter and not a string literal, so
# they do not match.
_HELPER_CALL = re.compile(
    r'\b(get|post|patch|put|delete)\(\s*path:\s*"([^"]+)"',
    re.IGNORECASE,
)
# URL(string: … "/api/…") with `request.httpMethod = "…"` somewhere after it.
# `[^"]*?` and not `[^)]*?`: the prefix is an expression, and the real call is
# `URL(string: baseURL() + "/api/volume/adjust")` — a class excluding `)` stops
# at baseURL()'s own parenthesis and never reaches the literal, so the route
# vanished from the extracted surface without any error.
_HAND_BUILT = re.compile(r'URL\(\s*string:[^"]*?"(/api/[^"]*)"')
_HTTP_METHOD = re.compile(r'httpMethod\s*=\s*"(\w+)"')
# How far past a hand-built URL to look for its method. The assignment sits in
# the same short function body; a window keeps one call's method from being read
# off the next one's.
_METHOD_WINDOW = 400


def extract_rest(api_swift: str) -> set[tuple[str, str]]:
    """{(METHOD, path_shape)} consumed by MiloAPIClient.swift, both call shapes."""
    out: set[tuple[str, str]] = set()

    for method, path in _HELPER_CALL.findall(api_swift):
        out.add((method.upper(), _shape(path)))

    for match in _HAND_BUILT.finditer(api_swift):
        window = api_swift[match.end(): match.end() + _METHOD_WINDOW]
        found = _HTTP_METHOD.search(window)
        out.add(((found.group(1) if found else "GET").upper(), _shape(match.group(1))))

    return out


def manifest_surface(manifest: dict) -> set[tuple[str, str]]:
    """{(METHOD, path_shape)} the manifest declares Milo-iOS depends on."""
    return {(e["method"].upper(), _shape(e["path"])) for e in manifest["rest"]}


def broken_surface(manifest: dict) -> set[tuple[str, str]]:
    """{(METHOD, path_shape)} the app calls that the backend does not serve.

    Known client-side defects, listed so they are neither declared (the route
    does not exist) nor silent (the client is already broken).
    """
    return {(e["method"].upper(), _shape(e["path"]))
            for e in manifest["_broken_calls"]["routes"]}


def compute_diff(manifest: dict, api_swift: str):
    """(undeclared, unexplained) — the two ways manifest and snapshot disagree.

    `undeclared`: the app declares a call the manifest does not account for, in
        `rest` or in `_broken_calls`. The backend is free to delete a route
        nothing declares, so this is always a defect.
    `unexplained`: the manifest lists a route the app no longer declares. Either
        Milo-iOS dropped it (prune the entry) or the snapshot is stale.

    Empty on both sides is the whole contract: manifest == snapshot.
    """
    accounted = manifest_surface(manifest) | broken_surface(manifest)
    called = extract_rest(api_swift)
    return called - accounted, accounted - called


def _read_source(root: Path) -> str:
    """Concatenate the tracked Swift files from a checkout (or the vendor dir)."""
    chunks = []
    for name in SOURCE_FILES:
        matches = sorted(root.rglob(name)) if root != VENDOR_DIR else [root / name]
        if not matches:
            raise SystemExit(f"{name} not found under {root}")
        chunks.append(matches[0].read_text())
    return "\n".join(chunks)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2

    upstream = _read_source(Path(argv[1]))
    vendored = _read_source(VENDOR_DIR)

    if extract_rest(upstream) == extract_rest(vendored):
        print("Milo-iOS: vendored snapshot matches upstream.")
        return 0

    gained = extract_rest(upstream) - extract_rest(vendored)
    lost = extract_rest(vendored) - extract_rest(upstream)
    print("Milo-iOS has moved past the vendored snapshot.")
    for method, path in sorted(gained):
        print(f"  + upstream now calls  {method} {path}")
    for method, path in sorted(lost):
        print(f"  - upstream no longer calls  {method} {path}")
    print(
        "\nRefresh backend/tests/contracts/vendor/milo-ios/ AND milo_ios_contract.json "
        "together, in one commit, then re-run pytest backend/tests/contracts/."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
