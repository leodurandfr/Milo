# Vendored Milo-Mac snapshot

Committed copies of the three source files that define Milo-Mac's wire
dependency on this backend:

| File | Defines |
|---|---|
| `MiloAPIService.swift` | every REST route Milo-Mac calls (`send()`/`sendCommand()`/`fetchJSON()` helpers, and any other `/api/` literal) |
| `WebSocketService.swift` | every WS `(category, type)` Milo-Mac handles (`switch (category, eventType)`) |
| `MiloAudioState.swift` | every key of the audio state it decodes — byte-identical to Milo-iOS' `Shared/MiloAudioState.swift` |

**Source:** `github.com/leodurandfr/Milo-Mac`, path `Milo Mac/`, at the commit
`../milo_mac_contract.json` names in `_snapshot.upstream_commit` (the enforced
record; this line is prose).

## Why it's here

The offline contract test (`../test_milo_mac_contract.py`) extracts the surface
these files consume and asserts `../milo_mac_contract.json` matches it **exactly**
— with no network — on every `pytest` run. This snapshot is the deterministic
stand-in for the real (separate, possibly-private) app, so the manifest can't
silently drift from what Milo-Mac depends on, and a broken extractor fails loudly
instead of passing on an empty surface.

## Refreshing (conscious update only)

When Milo-Mac changes its REST/WS surface:

1. Re-download the three files from upstream into this directory, and bump
   `_snapshot.upstream_commit`.
2. Re-run `pytest backend/tests/contracts/` and follow the drift message to
   update `../milo_mac_contract.json` (and the backend, if a route/event is new).
3. Commit the refreshed snapshot **and** the manifest together.

The non-blocking weekly CI job `check_milo_mac_freshness.py` re-clones upstream
and warns when this snapshot has fallen behind — that's the signal to refresh.
