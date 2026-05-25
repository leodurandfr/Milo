# Vendored go-librespot documentation

Verbatim upstream documentation for **go-librespot**, the Spotify Connect daemon
Milō drives for its Spotify source ([backend/sources/spotify/](../../../backend/sources/spotify/)).
Kept in-repo so any session can verify the API/config contract **without network
access**, and so the contract is pinned to the exact version Milō targets.

## Pinned version

| | |
|---|---|
| Upstream | https://github.com/devgianlu/go-librespot |
| Version | **v0.7.3** (latest release as of vendoring) |
| Commit | `c191a43c548e698c1dfc83590665f455fc54f084` |
| Released | 2026-05-25 |
| Vendored on | 2026-05-26 |

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

## Milō compatibility notes (v0.7.3)

- **REST**: `api-spec.yml` was byte-identical v0.6.1 → v0.7.1; v0.7.2 added two
  additive things (`POST /player/stop` + a `playback_ready` boolean on `GET /`);
  v0.7.3 added one more, also additive — `POST /set_device_name` (runtime device
  rename; Milō does not call it). Every endpoint Milō calls
  (`/player/{play,pause,playpause,seek,next,prev}`, `GET /status`, `GET /`) is
  unchanged.
- **WebSocket**: `API.md` is identical v0.7.1 → v0.7.3. The 8 events Milō handles
  (`active`, `inactive`, `playing`, `paused`, `metadata`, `seek`, `stopped`,
  `not_playing`) are intact. Documented-but-unhandled events (`will_play`,
  `volume`, `shuffle_context`, `repeat_context`, `repeat_track`) are ignored on
  purpose — volume is owned by ALSA/CamillaDSP (`external_volume`).
- **SIGTERM-hang regression (v0.7.2) — fixed upstream in v0.7.3, validated by
  Milō**: v0.7.2 did not exit on SIGTERM (systemd waited the full
  `TimeoutStopSec`, then SIGKILLed) — session-dependent, biting only once a phone
  had an authenticated AP/dealer session. The earlier "call `POST /player/stop`
  first" lever was DISPROVEN (the daemon still hung after a successful
  `/player/stop`). v0.7.3 commit `c191a43` ("proper shutdown sequence on
  interrupt") wires `ctx.Done()` → `currentPlayer.Close()` + `app.Close()`;
  validated on the Pi 2026-05-26 (live session, direct SIGTERM → graceful exit in
  59ms). `system/milo-spotify.service` therefore uses systemd's default SIGTERM,
  keeping `TimeoutStopSec=5` only as a backstop. Not an API break.

## How to refresh (when the pinned version changes)

```bash
C=<new-commit-sha>   # or a tag like v0.7.3
BASE="https://raw.githubusercontent.com/devgianlu/go-librespot/$C"
for f in API.md README.md api-spec.yml config_schema.json; do
  curl -fsSL "$BASE/$f" -o "docs/vendor/go-librespot/$f"
done
# then update the "Pinned version" table above + the compatibility notes.
```
