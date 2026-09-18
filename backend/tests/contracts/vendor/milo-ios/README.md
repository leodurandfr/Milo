# Vendored Milo-iOS snapshot

The two files from [Milo-iOS](https://github.com/leodurandfr/Milo-iOS) that carry
the whole wire surface, committed verbatim so `test_milo_ios_contract.py` can
check the manifest against the real app **with no network**:

| File | What it pins |
|---|---|
| `MiloAPIClient.swift` | every route the app calls |
| `Models.swift` | every response field the app decodes by name |

Every call goes through `MiloAPIClient` — the three App Intents and the widget's
timeline provider build no URL of their own — so these two files are the surface,
not a sample of it.

**Refresh them and `../../milo_ios_contract.json` together, in one commit.** The
two must describe the same surface exactly, in both directions — a snapshot ahead
of the manifest and a manifest ahead of the snapshot both fail
`test_manifest_matches_the_vendored_surface_exactly`. The one route the app
targets that the backend does not serve is named in `_broken_calls`, and that
entry deletes itself as soon as either side moves.

Captured from upstream `bf969c4ffecb` on 2026-09-18. The non-blocking
`milo-ios-freshness` CI job re-clones the app weekly and opens a tracking issue
when this snapshot falls behind.
