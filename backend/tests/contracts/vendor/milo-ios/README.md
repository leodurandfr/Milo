# Vendored Milo-iOS snapshot

The files from [Milo-iOS](https://github.com/leodurandfr/Milo-iOS) that carry the
whole wire surface, committed verbatim so `test_milo_ios_contract.py` can check
the manifest against the real app **with no network**:

| File | What it pins |
|---|---|
| `MiloAPIClient.swift` | the volume / settings / audio routes |
| `MiloAPIClient+Push.swift` | the APNs token registration routes |
| `MiloAPIClient+Media.swift` | the Now Playing transport + per-room volume routes |
| `Models.swift` | every response field the app decodes by name |

Every call goes through `MiloAPIClient` — the three App Intents and the widget's
timeline provider build no URL of their own — but it is **no longer one file**,
and the vendored set is matched by the glob patterns
`("MiloAPIClient*.swift", "Models.swift")` rather than a frozen list.

**Why patterns.** The list used to name exactly two files. Milo-iOS `09b9789b`
added `POST /api/push/tokens` and `DELETE /api/push/tokens/{}` in a third, the
extractor read an unchanged surface, and the freshness script printed
*"vendored snapshot matches upstream"* while the app had gained two routes — the
contract passing by describing an app that no longer exists, which is the one
rot mode this directory exists to prevent. Now Playing will add more
`MiloAPIClient+*.swift`.

**A glob is still a bet on a naming convention, so it is measured.**
`check_milo_ios_freshness.py::unvendored_surface` reads *every* `.swift` in a
checkout and refuses to compare at all when a `"/api/…"` literal sits outside
these patterns. Vendor that file or widen the patterns — never ignore it.

**Refresh them and `../../milo_ios_contract.json` together, in one commit.** The
two must describe the same surface exactly, in both directions — a snapshot ahead
of the manifest and a manifest ahead of the snapshot both fail
`test_manifest_matches_the_vendored_surface_exactly`. The one route the app
targets that the backend does not serve is named in `_broken_calls`, and that
entry deletes itself as soon as either side moves.

Captured from upstream `a84400a9dab7795adc61df347f77fa81241d91d1` on 2026-09-19.

`a84400a9` is the first refresh that moved the surface — two routes, in a **fourth** file the
glob found on its own. That is the patterns earning their keep: a frozen list would have
reported an unchanged surface for the second time.

None of the three refreshes since `09b9789b` moved a route — only
`MiloAPIClient+Push.swift` changed each time, and only in what the app *sends*, how it *reads
a reply*, or how it guards a retry. All three were taken anyway, each because the vendored
copy would have taught something measured to be wrong: the version that forwarded Apple's raw
`development` entitlement value and took a 422; the version that could not tell that 422 from
a network failure; and the version whose retry guard let two call sites both through. The
rule is not "refresh per commit" — it is whether the vendored content would mislead someone
reading it, or whether the surface moved.

Nothing checks this automatically — there is no `milo-ios-freshness` CI job, on
purpose. When Milo-iOS changes, run:

    python backend/tests/contracts/check_milo_ios_freshness.py /path/to/milo-ios

A snapshot stale in its CONTENT still fails nothing: `manifest == snapshot` stays
true while both describe an app that no longer exists, which is the one way this
contract can rot. The completeness guard only covers the other half — a route
that escaped the vendored FILES — so running the command above is still the only
thing that catches a route whose shape changed inside one of them.
