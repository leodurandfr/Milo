#!/usr/bin/env python3
"""Freshness check: does Milo-iOS' real source still match what we vendor + enforce?

Same two-guard shape as `check_milo_mac_freshness.py`, for the OTHER external
client (github.com/leodurandfr/Milo-iOS — an iOS app whose surface is a widget
extension and App Intents):

  * OFFLINE, BLOCKING (test_milo_ios_contract.py, every pytest run):
      - backend  ⊇ manifest             (no route the manifest declares has been
        removed from the backend);
      - manifest == snapshot            (the surface extracted from the
        committed snapshot matches `rest` — plus `_broken_calls` while such a
        section exists — exactly).
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

Three call shapes, and all three must be read or a route goes missing in silence:

    get(path: "/api/volume/state")                     # private helper, method
    post(path: "/api/audio/source/\\(name)")            # is the function name
    URL(string: baseURL() + "/api/volume/adjust")      # hand-built, method is a
    request.httpMethod = "POST"                        # nearby assignment
    writeVolume(path: "/api/volume/global", …)         # the app's own wrapper,
    request.httpMethod = "PATCH"                       # method in ITS body

`fireAdjustVolume` uses the second form for its fire-and-forget path, and
`be15c1f` moved both volume writes behind the third — a helper that builds the
URL from `baseURL() + path` inside itself. Each time, an extractor modelling one
shape fewer reports a smaller surface with no error, and a missing route reads
as "the app does not use it", which is the exact silence this whole contract
exists to break. The third cost the most: it lost a route the manifest ALREADY
pinned, so the loss would have read as the app having dropped it.
`test_the_extractor_reads_every_call_shape` and
`test_a_wrapped_helper_takes_the_method_its_own_body_assigns` pin all three
against the vendored snapshot.

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
# longer existed — the exact rot the manifest's own _no_tolerance_sections
# warns about, arrived for real (Milo-iOS 09b9789b, 2026-09-19).
#
# The third pattern is a filename and not a glob: the Now Playing bridge is not
# a client file by name, but it calls `MiloAPIClient.get(path:)` directly and is
# the only declaration of GET /api/multiroom/state. It was found by the guard
# below rather than by reading, which is the guard working.
#
# The fourth carries no route at all, and is here for the payload invariants:
# MiloAudioState.swift is the one decoder of GET /api/audio/state (shared byte
# for byte with Milo-Mac, since 39b0de28), and every key the schema invariant
# pins is read there. Left out, the vendored corpus mentions none of them and
# test_an_invariant_names_a_key_the_app_mentions has nothing to check against.
#
# A glob is still a bet on a naming convention, so it is not left as one:
# `unvendored_surface()` below reads EVERY .swift in a checkout and fails on
# any route literal living outside these patterns.
SOURCE_FILES = (
    "MiloAPIClient*.swift", "Models.swift", "MiloNowPlayingBridge.swift", "MiloAudioState.swift",
)

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
_VERB_HELPERS = ("get", "post", "patch", "put", "delete")
_HELPER_CALL = re.compile(
    r'\b(' + "|".join(_VERB_HELPERS) + r')\(\s*path:\s*"([^"]+)"',
    re.IGNORECASE,
)
# Any OTHER function taking `path:` with a route literal — a helper of the app's
# own, wrapping the transport for a family of calls. `writeVolume(path:body:label:)`
# is the first: it builds the URL itself from `baseURL() + path` and sets
# `httpMethod` in its own body, so neither shape above sees it. Measured at
# Milo-iOS be15c1f: modelling only the two shapes returned a surface missing BOTH
# volume writes, one of which the manifest already pinned — the extractor would
# have reported the app as no longer calling a route it calls on every gesture.
_WRAPPED_CALL = re.compile(r'\b(\w+)\(\s*path:\s*"(/api/[^"]*)"')
# `func name(… path: String …)` — where such a helper is declared.
_HELPER_DEF = re.compile(r'\bfunc\s+(\w+)\s*\([^)]*\bpath:\s*String')
# URL(string: … "/api/…") with `request.httpMethod = "…"` somewhere after it.
# `[^"]*?` and not `[^)]*?`: the prefix is an expression, and the real call is
# `URL(string: baseURL() + "/api/volume/adjust")` — a class excluding `)` stops
# at baseURL()'s own parenthesis and never reaches the literal, so the route
# vanished from the extracted surface without any error.
_HAND_BUILT = re.compile(r'URL\(\s*string:[^"]*?"(/api/[^"]*)"')
_HTTP_METHOD = re.compile(r'httpMethod\s*=\s*"(\w+)"')
_NEXT_FUNC = re.compile(r'\bfunc\s')
# How far past a hand-built URL to look for its method. The assignment sits in
# the same short function body; a window keeps one call's method from being read
# off the next one's.
_METHOD_WINDOW = 400


def helper_methods(api_swift: str) -> dict[str, str]:
    """{helper name: METHOD} for every function that takes a `path:`.

    The method is the `httpMethod` the helper's own body assigns, and GET when
    it assigns none — that is URLRequest's rule, not a convention of this app,
    so it holds for a helper nobody has written yet. The body is read up to the
    next `func`, so one helper's method can never be taken off the next one's.

    A name declared twice with two methods raises rather than picking one: two
    spellings of one route's verb disagreeing in silence is the failure this
    whole file exists to make loud.
    """
    found: dict[str, str] = {}
    for match in _HELPER_DEF.finditer(api_swift):
        rest = api_swift[match.end():]
        stop = _NEXT_FUNC.search(rest)
        body = rest[: stop.start()] if stop else rest
        verb = _HTTP_METHOD.search(body)
        method = (verb.group(1) if verb else "GET").upper()
        name = match.group(1)
        if found.get(name, method) != method:
            raise ValueError(
                f"helper {name!r} is declared with two methods "
                f"({found[name]} and {method}) — which one a call site takes "
                f"cannot be decided from the source"
            )
        found[name] = method
    return found


def extract_rest(api_swift: str) -> set[tuple[str, str]]:
    """{(METHOD, path_shape)} the vendored Swift declares, all three call shapes."""
    out: set[tuple[str, str]] = set()

    for method, path in _HELPER_CALL.findall(api_swift):
        out.add((method.upper(), _shape(path)))

    methods = helper_methods(api_swift)
    for name, path in _WRAPPED_CALL.findall(api_swift):
        if name.lower() in _VERB_HELPERS:
            continue                                   # read above, by name
        if name not in methods:
            raise ValueError(
                f"{name}(path: {path!r}) targets a route through a helper this "
                f"snapshot does not declare — vendor the file that defines it, "
                f"or the route's method is a guess"
            )
        out.add((methods[name], _shape(path)))

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

    ABSENT is the normal state and means the empty set, not a malformed manifest:
    the section is data about a defect, so it exists only while one does. It held
    GET /api/settings/dock-apps until Milo-iOS 741f8dc1 deleted the call, then
    went with it. `.get` here is not a compatibility fallback — nothing older is
    being absorbed — it is the section's own lifecycle, and the manifest's
    `_no_tolerance_sections` records what may never come back in its place.
    """
    return {(e["method"].upper(), _shape(e["path"]))
            for e in manifest.get("_broken_calls", {}).get("routes", [])}


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


def _is_test_target(relative: Path) -> bool:
    """Does this path sit inside one of the app's test targets?"""
    return any(part.endswith(("Tests", "UITests")) for part in relative.parts[:-1])


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

    The app's own test targets are the one exception, and it is stated on the
    DIRECTORY rather than on a route: a test target is not client code, and its
    `/api/…` literals are fixtures (Milo-iOS' `Milo_iOSTests.swift` builds radio
    artwork URLs). Excusing a route here instead would be the tolerance
    section the manifest's `_no_tolerance_sections` refuses.
    """
    vendored = set(matching_files(root))
    escaped: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.swift")):
        if path in vendored or _is_test_target(path.relative_to(root)):
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
