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

# The files that carry the surface. GLOB patterns, not a fixed list, and that
# is the whole point of the pattern: the list used to read exactly
# ("MiloAPIClient.swift", "Models.swift") while the app grew its push routes in
# MiloAPIClient+Push.swift, so the extractor saw a surface that had not changed
# and this script printed "vendored snapshot matches upstream" while Milo-iOS
# had gained two routes. The contract passed by describing an app that no
# longer existed — the exact rot the manifest's own _broken_calls.why warns
# about, arrived for real (Milo-iOS 09b9789b, 2026-09-19).
#
# A glob is still a bet on a naming convention, so it is not left as one:
# `unvendored_surface()` below reads EVERY .swift in a checkout and fails on
# any route literal living outside these patterns.
SOURCE_FILES = ("MiloAPIClient*.swift", "Models.swift")

# Where a route literal can appear at all. Used only by the completeness guard.
_ROUTE_LITERAL = re.compile(r'"(/api/[^"]*)"')


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


def matching_files(root: Path) -> list[Path]:
    """Every file under `root` matching SOURCE_FILES, deduplicated and ordered.

    All matches, not the first: `MiloAPIClient.swift` and
    `MiloAPIClient+Push.swift` both carry routes, and taking one of them is how
    two routes went missing in silence.
    """
    found: set[Path] = set()
    for pattern in SOURCE_FILES:
        found.update(root.rglob(pattern))
    return sorted(found)


def _read_source(root: Path) -> str:
    """Concatenate the tracked Swift files from a checkout (or the vendor dir)."""
    matches = matching_files(root)
    if not matches:
        raise SystemExit(f"no file matching {SOURCE_FILES} found under {root}")
    return "\n".join(m.read_text() for m in matches)


def unvendored_surface(root: Path) -> dict[str, set[str]]:
    """{file: {route literals}} for route literals OUTSIDE the vendored patterns.

    The completeness guard, and the reason a glob is acceptable where a fixed
    list was not. A pattern is a bet that the app keeps naming its client files
    a certain way; this measures the bet instead of trusting it. Anything it
    reports is either a file to vendor or a pattern to widen — never something
    to leave, because a route the snapshot cannot see is a route the backend
    believes nobody calls.

    Deliberately crude: it greps for `"/api/…"` anywhere, so a path in a comment
    is reported too. That direction is safe — a false positive is a line to
    read, a false negative is the defect this exists for.
    """
    vendored = set(matching_files(root))
    escaped: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.swift")):
        if path in vendored:
            continue
        literals = set(_ROUTE_LITERAL.findall(path.read_text()))
        if literals:
            escaped[str(path.relative_to(root))] = literals
    return escaped


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2

    checkout = Path(argv[1])
    upstream = _read_source(checkout)
    vendored = _read_source(VENDOR_DIR)

    # Completeness first: a surface that agrees is worth nothing if the files
    # compared are not the files that carry it.
    escaped = unvendored_surface(checkout)
    if escaped:
        print("Milo-iOS: route literals live OUTSIDE the vendored patterns "
              f"{SOURCE_FILES} — the comparison below cannot see them.")
        for name, literals in escaped.items():
            print(f"  {name}: {', '.join(sorted(literals))}")
        print("\nVendor that file too, or widen SOURCE_FILES. Do not ignore this: "
              "a route the snapshot cannot see reads as a route nobody calls.")
        return 1

    if extract_rest(upstream) == extract_rest(vendored):
        print(f"Milo-iOS: vendored snapshot matches upstream "
              f"({len(matching_files(checkout))} files, "
              f"{len(extract_rest(upstream))} routes).")
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
