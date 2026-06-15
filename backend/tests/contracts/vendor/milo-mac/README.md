# Vendored Milo-Mac snapshot

Committed copies of the two source files that define Milo-Mac's wire dependency
on this backend:

| File | Defines |
|---|---|
| `MiloAPIService.swift` | every REST route Milo-Mac calls (`send()`/`fetchJSON()` helpers) |
| `WebSocketService.swift` | every WS `(category, type)` Milo-Mac handles (`switch (category, eventType)`) |

**Source:** `github.com/leodurandfr/Milo-Mac`, branch `main`, path `Milo Mac/`.
**Retrieved:** 2026-06-15.

## Why it's here

The offline contract test (`../test_milo_mac_contract.py`) extracts the surface
these files consume and asserts `../milo_mac_contract.json` matches it **exactly**
— with no network — on every `pytest` run. This snapshot is the deterministic
stand-in for the real (separate, possibly-private) app, so the manifest can't
silently drift from what Milo-Mac depends on, and a broken extractor fails loudly
instead of passing on an empty surface.

## Refreshing (conscious update only)

When Milo-Mac changes its REST/WS surface:

1. Re-download both files from upstream `main` into this directory.
2. Re-run `pytest backend/tests/contracts/` and follow the drift message to
   update `../milo_mac_contract.json` (and the backend, if a route/event is new).
3. Commit the refreshed snapshot **and** the manifest together.

The non-blocking weekly CI job `check_milo_mac_freshness.py` re-clones upstream
and warns when this snapshot has fallen behind — that's the signal to refresh.
