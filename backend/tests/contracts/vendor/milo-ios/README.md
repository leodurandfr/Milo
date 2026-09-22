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
| `MiloNowPlayingBridge.swift` | the lock screen's device list (`/api/multiroom/state`) **and** the rule that decides whether the card exists |

Every call goes through `MiloAPIClient` — the three App Intents and the widget's
timeline provider build no URL of their own — but it is **no longer one file**,
and the vendored set is matched by the patterns
`("MiloAPIClient*.swift", "Models.swift", "MiloNowPlayingBridge.swift")` rather
than a frozen list.

The third is a filename, not a glob, and it is the guard's own find: the Now
Playing bridge is not named like a client file, yet it calls
`MiloAPIClient.get(path:)` directly and is the **only** declaration of
`GET /api/multiroom/state`, which it reads for one field — the per-room name the
lock screen's sliders carry.

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

**Three call shapes, not two.** `be15c1f` moved both volume writes behind
`writeVolume(path:body:label:)`, a private helper that builds its URL from
`baseURL() + path` and sets `httpMethod` in its own body. The two-shape extractor
lost them both — including `PATCH /api/volume/client/mac/{}`, which the manifest
*already pinned*, so the script would have reported the app as having dropped a
route it calls on every gesture. `helper_methods()` now resolves any
`name(path: "/api/…")` through the method its helper's body assigns, GET when it
assigns none: URLRequest's own rule, so it holds for a helper nobody has written
yet, and an unresolvable one raises rather than defaulting.

**Refresh them and `../../milo_ios_contract.json` together, in one commit.** The
two must describe the same surface exactly, in both directions — a snapshot ahead
of the manifest and a manifest ahead of the snapshot both fail
`test_manifest_matches_the_vendored_surface_exactly`. The one route the app
targets that the backend does not serve is named in `_broken_calls`, and that
entry deletes itself as soon as either side moves.

Captured from upstream `3018b8ffe5d9419214daad5f084837517824a0dd` on 2026-09-22.

**This line was two refreshes stale when 4b9e73f was taken**, and it is worth saying
where: it still named `be15c1f` after `741f8dc1 -> 0edead3` and again after
`0edead3 -> 4b9e73f`. Nothing checks it — `check_milo_ios_freshness.py` reads the
Swift, and `test_milo_ios_contract.py` reads the manifest; neither opens this file.
The hash that is enforced lives in `milo_ios_contract.json::_snapshot.upstream_commit`,
so this one is prose, and prose left behind is exactly the `would mislead a reader`
failure these refreshes exist to prevent. Update it in the same commit as the files.

**`4b9e73f` is the first refresh taken because the BACKEND moved.** `95293764` made a
stopped mpv source publish what a play press would resume, so `source_state: ready`
stopped meaning *nothing to show*. Until then `MiloNowPlayingBridge` ended the Lock
Screen session on `sourceState != "active"` — so stopping a station closed the card
instead of leaving it paused on what `resume_playback` would bring back. The vendored
copy stated that rule on the one file a backend reader would open to check it. No
route moved, four of the five files came back byte-identical, and the script answered
*matches upstream* on both sides of the change.

**`be15c1f` is the refresh that showed what not running this costs.** Eighteen commits
separated it from `a84400a9`, and across them the app had gained **four** routes the
manifest named nowhere: `PATCH /api/volume/global`, `GET /api/multiroom/state`,
`POST /api/push/sessions`, `GET /api/radio/stations`. Nothing said so, because the offline
test compares the manifest to the *snapshot*, and the snapshot was the stale thing —
`manifest == snapshot` stayed true while both described an app that had moved. The only
trigger this check has is someone deciding the app changed, so decide on the commit that
changes the wire.

`a84400a9` was the first refresh that moved the surface — two routes, in a **fourth** file the
glob found on its own. That is the patterns earning their keep: a frozen list would have
reported an unchanged surface for the second time.

None of the three refreshes before it moved a route — only
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
