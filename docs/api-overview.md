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
| Sources | `/api/radio`, `/api/podcast`, `/api/cd`, `/api/airplay` | Source-specific endpoints (browsing, favorites, binary artwork…) |

Mute-receiver sources (Bluetooth, Mac) have **no** dedicated routes — they are driven entirely
through `/api/audio/control/{source}`. API conventions (verbs, the `status` envelope, the
per-layer error policy) are spelled out in [CLAUDE.md](../CLAUDE.md) and the
[Developer Guide](development.md).

## WebSocket

A single connection at **`/ws`** carries all state. The backend never asks the client to poll —
every state change is pushed.

**Envelope:** `{ category, type, origin, data, timestamp }`

**Categories:** `source`, `system`, `routing`, `equalizer`, `multiroom`, `volume`, `settings`,
`programs`, `network`.

On connect the client receives a `full_state` snapshot, then incremental deltas. The events the
companion apps rely on (the `full_state` envelope, `multiroom_changed`, `volume_changed`,
`settings/{volume_limits,dock_apps}_changed`) are pinned in the
[Milo-Mac contract](../backend/tests/contracts/milo_mac_contract.json) and checked on every test
run, so they cannot silently drift.

---

For how state flows end-to-end (backend change → `broadcast(WsEvent)` → WS → Pinia store →
reactive UI), see the [Architecture](architecture.md) doc.
