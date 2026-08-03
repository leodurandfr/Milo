# Milō API Overview

Milō's backend is a [FastAPI](https://fastapi.tiangolo.com/) app (`backend/`). The Vue
frontend, the on-device kiosk, and the companion apps (Milo-Mac, iOS, Android) all drive a
unit over the **same wire surface: a REST API + a single WebSocket**.

This page maps that surface at the *router-group* level — a quick mental model of the app.
It is **not** an endpoint-by-endpoint listing (~130 endpoints move too often to hand-maintain).
For exact, always-current request/response shapes, use the live sources of truth:

| Source of truth | Where |
|---|---|
| Interactive docs (Swagger UI) | backend only — dev: `http://localhost:8000/docs` |
| OpenAPI schema (JSON) | backend only — dev: `http://localhost:8000/openapi.json` |
| Milo-Mac wire contract (pinned) | [`backend/tests/contracts/milo_mac_contract.json`](../backend/tests/contracts/milo_mac_contract.json) |

> Swagger / OpenAPI are served by the backend (uvicorn :8000) directly. nginx on a production
> unit only proxies `/api/` and `/ws`, so `milo.local/docs` is **not** exposed — read the schema
> on the device itself or against a dev server.

## REST

Every endpoint lives under `/api` (nginx proxies `/api/` and `/ws` to the backend; everything
else is the static SPA).

| Group | Prefix | Purpose |
|---|---|---|
| Audio | `/api/audio` | Current playback state; switch source (`/source/{source}`); generic playback commands (`/control/{source}`) |
| Volume | `/api/volume` | Get/set volume, mute, limits |
| Equalizer | `/api/equalizer` | Per-target EQ (`local` · `<mac>` · `zone:<id>`), presets, compressor, loudness |
| Lyrics | `/api/lyrics` | Synced/plain lyrics for the now-playing track (LRCLIB, disk-cached) |
| Routing | `/api/routing` | Output device + `direct`/`multiroom` mode; Snapcast control under `/api/routing/snapcast` |
| Multiroom | `/api/multiroom` | Zones, clients, per-client volume |
| Programs | `/api/programs` | Installed-source/program state; multiroom satellite (client) OTA updates |
| Discovery | `/api/discovery` | Find & adopt Wi-Fi speakers (multiroom client onboarding) |
| Settings | `/api/settings` | Read/write app settings |
| Network | `/api/network` | Wi-Fi scan / connect / saved networks |
| System | `/api/system` | Reboot, poweroff, status, host checks, updates |
| Setup | `/api/setup` | First-boot wizard (`/complete`, `/become-client`) |
| Errors | `/api/errors` | Receive frontend error reports → `errors.log` |
| Health | `/api/health`, `/api/ping`, `/api/initial-state` | Liveness + initial-state snapshot |
| Hardware | `/api/bt-remote`, `/api/ir-remote`, `/api/fan` | Bluetooth/IR remote + fan control |
| Sources | `/api/radio`, `/api/podcast`, `/api/cd`, `/api/airplay`, `/api/dlna`, `/api/qobuz`, `/api/music-library` | Source-specific endpoints (browsing, favorites, binary/proxied artwork, scan status…) |

**Every source command travels on `/api/audio/control/{source}`**, whatever its family — a
per-source command route would only add a second failure contract to keep in sync. A source
router therefore holds what is *not* a command: catalog browsing, favorites, binary/proxied
artwork. The two exceptions are documented in [CLAUDE.md](../CLAUDE.md) § *Audio sources*:
a route that composes several commands in one request (`/api/radio/play`, `/api/podcast/play`)
and a route Milo-Mac pins.

So Bluetooth, Mac and Spotify have no router at all; Qobuz's only surface is the
`/api/qobuz/account/*` one-time-login relay; `/api/airplay` and `/api/dlna` serve proxied
artwork; `/api/cd` serves disc covers. Music Library is the richest (Subsonic-backed browsing,
cover-art proxy, storage spaces, share wizard). API conventions (verbs, the `status` envelope, the
per-layer error policy) are spelled out in [CLAUDE.md](../CLAUDE.md) and the
[Developer Guide](development.md).

## WebSocket

A single connection at **`/ws`** carries all state. The backend never asks the client to poll —
every state change is pushed.

**Envelope:** `{ category, type, origin, data, timestamp }`

**Categories:** `source`, `system`, `routing`, `equalizer`, `multiroom`, `volume`, `settings`,
`programs`, `network`.

On connect the client receives a `full_state` snapshot, then incremental deltas.

### Source states, and the two kinds of error

`full_state.source_state` carries one of **four** members of `SourceState`, all reachable:

| State | Meaning |
|---|---|
| `starting` | The transition to this source is under way |
| `ready` | Engine up, nothing in session |
| `active` | A session or content exists — a *paused* radio is still `active` |
| `error` | The source is not operational |

`ready` rather than `connected`: nothing connects to Radio, CD or the Music Library. The split
between `ready` and `active` is "is there a session", not "is audio coming out".

The word "error" covers two different facts, and they travel on two different mechanisms — never
both, so neither can be mistaken for the other:

| Kind | Example | Mechanism | UI |
|---|---|---|---|
| The **source** is not operational | go-librespot won't start, transition timeout | `source_state: "error"` in `full_state` + `full_state.error` | Status card `[source, "Error"]` + a retry CTA that re-posts `POST /api/audio/source/{source}` |
| An **operation** failed, the source survives | a radio station won't tune, a command failed | `source/error` (+ `source/error_cleared`) | Notification banner only; the source's own screen is untouched |

A failed transition leaves the source **selected** in `error` rather than resetting to `none`, which
is what makes re-selecting it the retry.

The subset Milo-Mac relies on — `(category, type)` pairs across `system`, `source`, `volume`,
`routing` and `settings`, plus `payload_invariants` naming the exact fields it reads — is pinned in
the [Milo-Mac contract](../backend/tests/contracts/milo_mac_contract.json) and verified on every
`pytest` run, so it cannot silently drift. **Read the manifest for the list**; any summary here
would be a second, drifting copy. Note in particular `routing/multiroom_error`, whose invariant is
*presence only, no payload field is read* — it looks unreferenced from every angle and is the
easiest entry to delete by accident.

---

For how state flows end-to-end (backend change → `broadcast(WsEvent)` → WS → Pinia store →
reactive UI), see the [Architecture](architecture.md) doc.
