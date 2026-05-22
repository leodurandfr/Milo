# Vendored go-librespot documentation

Verbatim upstream documentation for **go-librespot**, the Spotify Connect daemon
Milō drives for its Spotify source ([backend/sources/spotify/](../../../backend/sources/spotify/)).
Kept in-repo so any session can verify the API/config contract **without network
access**, and so the contract is pinned to the exact version Milō targets.

## Pinned version

| | |
|---|---|
| Upstream | https://github.com/devgianlu/go-librespot |
| Version | **v0.7.2** (latest release as of vendoring) |
| Commit | `0a710cc81b6b1c83ea985a8487c21195d63ec700` |
| Released | 2026-05-21 |
| Vendored on | 2026-05-23 |

This must stay in sync with the version pinned in
[install/go-librespot.sh](../../../install/go-librespot.sh) and targeted by the
update flow ([backend/core/updates/update.py](../../../backend/core/updates/update.py)).

## Files (verbatim upstream copies)

| File | What it documents |
|---|---|
| `API.md` | REST + **WebSocket event** contract (the event types `source.py` dispatches on) |
| `api-spec.yml` | OpenAPI spec for the REST endpoints (`/player/*`, `/status`, `/`) |
| `config_schema.json` | Every `config.yml` option go-librespot accepts |
| `README.md` | Overview, build, configuration, usage |

## Milō compatibility notes (v0.7.2)

- **REST**: `api-spec.yml` was byte-identical from v0.6.1 through v0.7.1; v0.7.2
  added exactly two things, both additive — `POST /player/stop` ("Stop playback
  and disconnect session") and a `playback_ready` boolean on `GET /`. Every
  endpoint Milō calls (`/player/{play,pause,playpause,seek,next,prev}`) is
  unchanged.
- **WebSocket**: `API.md` is identical across v0.7.1 → v0.7.2. The 8 events Milō
  handles (`active`, `inactive`, `playing`, `paused`, `metadata`, `seek`,
  `stopped`, `not_playing`) are intact. Documented-but-unhandled events
  (`will_play`, `volume`, `shuffle_context`, `repeat_context`, `repeat_track`)
  are ignored on purpose — volume is owned by ALSA/CamillaDSP (`external_volume`).
- **Known v0.7.2 regression**: go-librespot does not exit on SIGTERM (systemd
  waits the full `TimeoutStopSec` then SIGKILLs). Suspected fix lever: call
  `POST /player/stop` before stopping the unit. Not an API break.

## How to refresh (when the pinned version changes)

```bash
C=<new-commit-sha>   # or a tag like v0.7.3
BASE="https://raw.githubusercontent.com/devgianlu/go-librespot/$C"
for f in API.md README.md api-spec.yml config_schema.json; do
  curl -fsSL "$BASE/$f" -o "docs/vendor/go-librespot/$f"
done
# then update the "Pinned version" table above + the compatibility notes.
```
