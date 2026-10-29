# API overview

Milō's backend is a [FastAPI](https://fastapi.tiangolo.com/) app (`backend/`). The Vue
frontend, the on-device kiosk, and the companion apps ([Milo-Mac](https://github.com/leodurandfr/Milo-Mac)
and [Milo-iOS](https://github.com/leodurandfr/Milo-iOS)) all drive a unit over the **same wire
surface: a REST API + a single WebSocket**.

This page maps that surface at the *router-group* level — a quick mental model of the app.
To drive a unit from your own code, start with the [integration guide](integration.md). The
exhaustive listings are generated from the code at each release and checked against it by a test:

| Reference | Where |
|---|---|
| REST operations, with their stability | [REST reference](reference/rest.md) |
| WebSocket events and their payloads | [WebSocket events](reference/ws-events.md) |
| The audio state | [Audio state](reference/audio-state.md) |
| Source commands and their parameters | [Source commands](reference/commands.md) |
| Interactive docs (Swagger UI) | `http://milo.local/api/docs` (ReDoc: `/api/redoc`) |
| OpenAPI schema (JSON) | `http://milo.local/api/openapi.json` |
| Milo-Mac wire contract (pinned) | [`backend/tests/contracts/milo_mac_contract.json`](https://github.com/leodurandfr/Milo/blob/main/backend/tests/contracts/milo_mac_contract.json) |
| Milo-iOS wire contract (pinned) | [`backend/tests/contracts/milo_ios_contract.json`](https://github.com/leodurandfr/Milo/blob/main/backend/tests/contracts/milo_ios_contract.json) |

> Swagger UI loads its scripts from a CDN, so `/api/docs` needs the internet;
> `/api/openapi.json` does not.

## REST

Every endpoint lives under `/api` (nginx proxies `/api/` and `/ws` to the backend; everything
else is the static SPA).

| Group | Prefix | Purpose |
|---|---|---|
| Audio | `/api/audio` | Current playback state; switch source (`/source/{source}`); generic playback commands (`/control/{source}`) |
| Volume | `/api/volume` | Get/set volume, mute, limits |
| Equalizer | `/api/equalizer` | Per-target EQ (`local` · `<mac>` · `zone:<id>`), presets, compressor, loudness |
| Lyrics | `/api/lyrics` | Synced/plain lyrics for the now-playing track (LRCLIB, disk-cached) |
| Routing | `/api/routing` | Multiroom on/off (`PUT /multiroom`); Snapcast control under `/api/routing/snapcast` |
| Multiroom | `/api/multiroom` | Zones, clients, per-client delay, gain and EQ; satellite registration |
| Programs | `/api/programs` | Installed-source/program state; multiroom satellite (client) OTA updates |
| Discovery | `/api/discovery` | Find & adopt Wi-Fi speakers (multiroom client onboarding) |
| Settings | `/api/settings` | Read/write app settings |
| Network | `/api/network` | Wi-Fi scan / connect / saved networks |
| System | `/api/system` | Reboot, poweroff, status, host checks, diagnostic report |
| Setup | `/api/setup` | First-boot wizard (`/complete`, `/become-client`) |
| Errors | `/api/errors` | Receive frontend error reports → `errors.log` |
| Health | `/api/health`, `/api/ping`, `/api/initial-state` | Liveness + initial-state snapshot |
| Push | `/api/push` | APNs device tokens and the Now Playing sessions a device still holds (Milo-iOS) |
| Hardware | `/api/bt-remote`, `/api/ir-remote`, `/api/fan` | Bluetooth/IR remote + fan control |
| Sources | `/api/radio`, `/api/podcast`, `/api/cd`, `/api/airplay`, `/api/qobuz`, `/api/music-library`, `/api/spotify` | Source-specific endpoints (browsing, favorites, binary/proxied artwork, scan status…) |

**Every source command travels on `/api/audio/control/{source}`**, whatever its family — a
per-source command route would only add a second failure contract to keep in sync. A source
router therefore holds what is *not* a command: catalog browsing, favorites, binary/proxied
artwork. There are two exceptions: a route that composes several commands in one request
(`POST /api/radio/play`), and a route Milo-Mac's contract pins (`POST /api/radio/stop`).

So Bluetooth, Mac and Tidal have no router at all; Qobuz's only surface is the
`/api/qobuz/account/*` one-time-login relay; `/api/airplay` serves proxied
artwork; `/api/cd` serves disc covers. `/api/spotify` is the browser's: the signed-in account's
home sections, a context's tracks (`/contexts/{uri}?after=N`, the tracks past the N the
browser has as go-librespot describes them, until `complete`), an artist's page (`/artists/{uri}`:
Spotify's own sections, the discography in place of its popular releases), a track's radio
(`/tracks/{uri}/radio`), the account's playlists written from a row's ⋯ menu (`GET
/tracks/{uri}/playlists` for the picker, `POST /contexts/{uri}/tracks` to add, `DELETE
/contexts/{uri}/tracks/{item}?position=N` to remove one entry — Liked Songs included), and the profiles Milō keeps (forget,
`PUT /active-profile` to sign the daemon in as one) — playback itself is the `play_context`
command. Music Library is the richest (Subsonic-backed browsing,
cover-art proxy, storage spaces, share wizard).

### Conventions

- **Paths** are kebab-case (`/api/music-library`, `/api/settings/screen-theme`); a resource has one
  path, and its read and its write differ only by verb. **Fields** are `snake_case`, in requests and
  responses alike.
- **Verbs:** `GET` reads, `PUT` replaces (settings, routing), `PATCH` updates part of a resource
  (volume, zone, client), `POST` runs an action or creates, `DELETE` removes.
- **Success** answers carry `"status": "success"`. A real failure is an HTTP error status with
  FastAPI's `{"detail": …}` body (422 for a body that does not validate).
- Music Library is the one router that keeps **Subsonic's** naming (`/album/{id}`, `star`,
  `unstar`), so it reads against the Subsonic documentation.

## Access and security

There is **no authentication**: Milō is a LAN appliance, and being on the network is what grants
access. Every request is answered by the same backend whether it comes from the screen, a phone or
a script. Two checks guard against a *foreign web page* driving a browser that happens to be on the
LAN ([`backend/api/middleware.py`](https://github.com/leodurandfr/Milo/blob/main/backend/api/middleware.py));
both answer `403 {"status": "error", "message": "Untrusted request origin"}`:

- **`Host`, on every HTTP request.** It must be a name or address the unit is reached by: `localhost`,
  `milo`, `milo.local`, `milo-2.local` (the name Avahi falls back to when a second server claims
  `milo`), or any private IP address. A reverse proxy that forwards another host name is refused —
  have it forward `Host: milo.local`.
- **`Origin`, on `POST`/`PUT`/`PATCH`/`DELETE`.** A request that carries one must come from one of
  those same names, from **any private IP address**, or from the CORS allowlist
  (`http(s)://milo.local`, the dev server on port 5173). A request with **no** `Origin` — curl, a
  script, a native app — passes. So a page served from another machine of the LAN by its IP address
  can drive the unit: the checks stop pages from the internet, not the local network.

The **`/ws` upgrade** passes the same `Host` check, and the `Origin` check whatever it asks for:
a browser always names the page in `Origin` when it opens a socket, and CORS does not cover a
WebSocket, so this is what keeps a page from the internet from reading the state. A refused upgrade
is answered with an HTTP `403`: the socket is never opened. Like curl over HTTP, a client that sends no `Origin` passes.

The API is served on port 80 by nginx, which proxies `/api/` and `/ws` to the backend. The backend
also listens on port **8000** on every interface, because satellites register with the server
there; the same two checks apply on that port.

### The diagnostic report

`POST /api/system/diagnostic` builds one plain-text report — versions, audio path, sources,
multiroom, storage, network, settings, the tail of `errors.log`, per-unit journal tails, and one
block per satellite fetched over `GET /diagnostic` on the satellite's API port (8001). It answers
`{ status, data: { report, unavailable } }`; `unavailable` names each section that could not be
collected and why, and the same lines appear in the file under `NOT COLLECTED`.

Two properties are load-bearing and are guarded by tests rather than by convention. The report is
capped at 60 000 bytes so it fits a GitHub issue body, with the journal filled round-robin across
units so a chatty one cannot starve the rest. And what it may contain is a **whitelist**, declared
once in `backend/core/system/diagnostic/whitelist.py`: every field of every persisted model is
either allowed or excluded-with-a-reason, `backend/tests/contracts/test_diagnostic_redaction.py`
goes red when a model gains a field nobody decided about, and the excluded values are substituted
out of the free-text sections too. Local IP and MAC addresses are deliberately kept — a multiroom
fault cannot be read without them.

## WebSocket

A single connection at **`/ws`** carries all state. The backend never asks the client to poll —
every state change is pushed.

**Envelope:** `{ category, type, origin, data, timestamp }`

**Categories:** `source`, `system`, `routing`, `equalizer`, `multiroom`, `volume`, `settings`,
`programs`, `network`.

**Handshake.** Changes are pushed from the moment the socket opens. To receive the current state,
the client sends one text frame, `{"type": "ready"}`, once its handlers are in place; the server
answers with `system/initial_state`, whose `state` key is the whole audio state (below), then
`volume/volume_changed`. The server reads nothing else a client sends, and every 30 s it sends
`{"category": "system", "type": "ping", "timestamp": …}`, which carries no `data`.

Every `(category, type)` pair is listed in the [WebSocket reference](reference/ws-events.md);
this page describes the audio ones. Each pair is
one class in [`backend/core/models/ws_events.py`](https://github.com/leodurandfr/Milo/blob/main/backend/core/models/ws_events.py),
whose fields are exactly the event's `data`.

### The audio state

The audio wire is **one object**, `AudioState`
([audio_wire.py](https://github.com/leodurandfr/Milo/blob/main/backend/core/models/audio_wire.py)), and it is the same everywhere:
`GET /api/audio/state` returns it, the `data` of `source/state` *is* it, and `system/initial_state`
carries it under `state` (next to `setup_completed`, `hotspot_active` and `server_instance` — an id
drawn per backend process, so a client can tell a restarted backend from a reconnect to the same
one; `GET /api/initial-state` is the HTTP fallback). Every field is always present — an absent value is `null`, never a missing
key. Durations and positions are integer milliseconds; instants are UTC seconds (float).

| Field | Values | Meaning |
|---|---|---|
| `source` | `none` or one of the ten sources | The selected source |
| `switching` | bool | A source transition is in flight, or a whole multiroom switch (up to the end of its volume sync) |
| `service` | `stopped`, `starting`, `running`, `failed` | `stopped` under `none`; `starting` during a start or a switch; `failed` after a failed start, until a start succeeds |
| `service_error` | `null` or `{reason, message}` | Set iff `service` is `failed`. `reason` is `start_timeout` (the 15 s budget) or `start_failed`; `message` is for the journal, never displayed |
| `availability` | all ten sources, always | Why each source cannot work right now, or `null` when it can ([below](#availability)) |
| `session` | `null` or object | The selected source's live session: `id`, `phase` (`loading`, `playing`, `paused`, `connected`), `title`, `artist`, `album`, `artwork`, `senders` (display names, `[]` when none), `duration_ms`, `position` |
| `controls` | command names | What `POST /api/audio/control/{source}` accepts *now* and would act on. `[]` under `none`, while `switching`, and whenever `service` is not `running`. `skip` (`{"seconds": ±n}`, relative, bounded to `[0, duration_ms]`) is listed wherever `seek` is |
| `resume` | `null` or object | What a play press would bring back; set only while `session` is `null` |
| `details` | `null` or a union on `kind` | The source's own content: `radio` (station, recognized track), `podcast` (the episode), `music_library` (queue, index, shuffle, ids), `cd` (disc, current track, `artwork_pending`), `airplay` (the cover's width, the sender's shuffle and repeat), `spotify` (the signed-in account, what context plays, the Jam) |
| `multiroom_enabled`, `equalizer_effects_enabled` | bool | The two global flags |

An empty string is never published: it is `null`.

**The playhead is an anchor, not a tick.** `session.position` is `{ms, at, rate}`: the playhead
stood at `ms` at the instant `at`, and moves at `rate` (1.0 for every source: nothing plays faster or slower) only while
the phase is `playing`. Every client applies the same formula —
`ms + (phase == "playing" ? (now − at) × 1000 × rate : 0)`, bounded to `[0, duration_ms]` — and
nothing sends a position on a timer. It is `null` for a session with no playhead (radio, Mac).

| Event | `data` | When |
|---|---|---|
| `source/state` | the whole state | whenever any field but the playhead changes, and only then |
| `source/position` | `{source, session_id, position}` | on a discontinuity only: a seek or a skip, or a reading more than 2 s from the anchor. A client ignores a `session_id` that is not its state's |
| `source/session_ended` | `{source, session_id, reason}` | at every end of a session, before the state that follows; `reason` is an `EndReason` (`eof`, `user_stop`, `idle_timeout`, `source_switch`, `reroute`, `sender_left`, `daemon_died`, `load_failed`, `stream_lost`, `storage_gone`) |
| `source/error`, `source/error_cleared` | `{source, reason}`, `{source}` | an operation failed, or no longer is ([below](#two-kinds-of-error)) |

An end is never a flag on the next state: an episode played to its end is a `source/session_ended`
with `reason: "eof"`. The frontend validates the state with a **strict** Zod schema
(`AudioStateSchema`): a state that does not parse is refused whole and the last good one kept.

### Session, resume point, or nothing

`session` is `null` whenever nothing is live, which is not the same as "nothing to show". A source
that stopped with something to come back to publishes it in `resume` — the station
`resume_playback` would re-tune, the episode and second an auto-stop left, the saved queue, the
track a loaded disc would play — with its content in `details`. Only when there is genuinely nothing
to come back to (an episode played to its end, a source that never played) are both `null`. Four
sources keep a resume point, Radio, Podcast, Music Library and CD; for the six a daemon holds,
`resume` is always `null`.

The phase is what the player or the sender announced, never what a command guessed. `connected` is
a sender Milō cannot call playing or paused — an AirPlay Realtime stream with no item, a Bluetooth device with no
AVRCP player, every Mac — and the card reads "Connected to X" from `senders`, unless the session
names a cover, a title and an artist, which the full player draws instead.

### Two kinds of error

The word "error" covers two different facts, and they travel on two different mechanisms — never
both, so neither can be mistaken for the other:

| Kind | Example | Mechanism | UI |
|---|---|---|---|
| The **source** will not start | go-librespot won't start, transition timeout | `service: "failed"` + `service_error` | Status card error + a retry CTA that re-posts `POST /api/audio/source/{source}` |
| An **operation** failed, the source survives | a radio station won't tune, a command failed | `source/error` (+ `source/error_cleared`) | Notification banner only; the source's own screen is untouched |

A failed start leaves the source **selected** with `service: "failed"` rather than resetting to
`none`, which is what makes re-selecting it the retry. And `failed` is sticky: no publish of the
source lifts it, only a start that succeeds.

### Availability

`availability` answers for every source, selected or not: `null` when it can work now, else the first
reason that applies. Connectivity comes first. The backend crosses NetworkManager's connectivity
level with the source's own `NETWORK_REQUIREMENT` (`none` / `lan` / `internet`): `no_network`
without any network, `no_internet` without a route out. So a router with no route out gives
`no_internet` to Spotify and `null` to AirPlay, which only needs the LAN — and `null` to Bluetooth
or CD, which need nothing. Then the source's own reason: `no_account` (Qobuz);
`no_drive`, `no_disc`, `reading_disc`, `unreadable_disc`, `ejecting` (CD); `no_storage`,
`catalog_unavailable` (Music Library). A connectivity change arrives as a new `source/state`. See [Architecture](architecture.md#unavailable-which-is-not-a-state) for where each
reason comes from and the CTAs.

## What the companion apps pin

The routes and events Milo-Mac and Milo-iOS depend on are listed in two manifests, checked on every
`pytest` run against the backend: the [Milo-Mac contract](https://github.com/leodurandfr/Milo/blob/main/backend/tests/contracts/milo_mac_contract.json)
(REST routes, `(category, type)` pairs across `source`, `volume`, `routing`, `settings` and
`multiroom`, and the payload fields it reads) and the [Milo-iOS contract](https://github.com/leodurandfr/Milo/blob/main/backend/tests/contracts/milo_ios_contract.json)
(REST only — a widget cannot hold a socket — and the response fields it reads). **Read the
manifests for the list**; a summary here would be a second copy that drifts.

Their union is the **contracted** surface: the REST operations they pin, which carry
`"x-stability": "contracted"` in the OpenAPI schema (a test holds the mark equal to the manifests),
the WebSocket events the Milo-Mac manifest pins, and the audio state. A change to any of it is listed under *API and integration
changes* in the release notes; everything else is internal. See [Stability](integration.md#stability).

---

For how state flows end-to-end (backend change → `broadcast(WsEvent)` → WS → Pinia store →
reactive UI), see the [Architecture](architecture.md) doc.
