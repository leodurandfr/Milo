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

Captured from upstream `bf969c4ffecb` on 2026-09-18.

Nothing checks this automatically — there is no `milo-ios-freshness` CI job, on
purpose. When Milo-iOS changes, run:

    python backend/tests/contracts/check_milo_ios_freshness.py /path/to/milo-ios

A stale snapshot does not fail anything: `manifest == snapshot` stays true while
both describe an app that no longer exists, which is the one way this contract
can rot.
