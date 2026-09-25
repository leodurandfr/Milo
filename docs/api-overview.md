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
| System | `/api/system` | Reboot, poweroff, status, host checks, updates, diagnostic report |
| Setup | `/api/setup` | First-boot wizard (`/complete`, `/become-client`) |
| Errors | `/api/errors` | Receive frontend error reports → `errors.log` |
| Health | `/api/health`, `/api/ping`, `/api/initial-state` | Liveness + initial-state snapshot |
| Hardware | `/api/bt-remote`, `/api/ir-remote`, `/api/fan` | Bluetooth/IR remote + fan control |
| Sources | `/api/radio`, `/api/podcast`, `/api/cd`, `/api/airplay`, `/api/qobuz`, `/api/music-library` | Source-specific endpoints (browsing, favorites, binary/proxied artwork, scan status…) |

**Every source command travels on `/api/audio/control/{source}`**, whatever its family — a
per-source command route would only add a second failure contract to keep in sync. A source
router therefore holds what is *not* a command: catalog browsing, favorites, binary/proxied
artwork. The two exceptions are documented in [CLAUDE.md](../CLAUDE.md) § *Audio sources*:
a route that composes several commands in one request (`/api/radio/play`)
and a route Milo-Mac pins.

So Bluetooth, Mac, Spotify and Tidal have no router at all; Qobuz's only surface is the
`/api/qobuz/account/*` one-time-login relay; `/api/airplay` serves proxied
artwork; `/api/cd` serves disc covers. Music Library is the richest (Subsonic-backed browsing,
cover-art proxy, storage spaces, share wizard). API conventions (verbs, the `status` envelope, the
per-layer error policy) are spelled out in [CLAUDE.md](../CLAUDE.md) and the
[Developer Guide](development.md).

### The diagnostic report

`POST /api/system/diagnostic` builds one plain-text report — versions, audio path, sources,
multiroom, storage, network, settings, the tail of `errors.log`, per-unit journal tails, and one
block per satellite fetched over `GET /diagnostic` on `CLIENT_API_PORT`. It answers
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

On connect the client receives `system/initial_state`, whose `state` key is the whole audio state
(below), then every change as it happens.

### The audio state

The audio wire is **one object**, `AudioState`
([audio_wire.py](../backend/core/models/audio_wire.py)), and it is the same everywhere:
`GET /api/audio/state` returns it, the `data` of `source/state` *is* it, and `system/initial_state`
carries it under `state` (next to `setup_completed` and `hotspot_active`; `GET /api/initial-state`
is the HTTP fallback). Every field is always present — an absent value is `null`, never a missing
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
| `details` | `null` or a union on `kind` | The source's own content: `radio` (station, recognized track), `podcast` (episode, speed), `music_library` (queue, index, shuffle, ids), `cd` (disc, current track, `artwork_pending`), `airplay` (the cover's width) |
| `multiroom_enabled`, `equalizer_effects_enabled` | bool | The two global flags |

An empty string is never published: it is `null`.

**The playhead is an anchor, not a tick.** `session.position` is `{ms, at, rate}`: the playhead
stood at `ms` at the instant `at`, and moves at `rate` (a podcast's speed, 1.0 elsewhere) only while
the phase is `playing`. Every client applies the same formula —
`ms + (phase == "playing" ? (now − at) × 1000 × rate : 0)`, bounded to `[0, duration_ms]` — and
nothing sends a position on a timer. It is `null` for a session with no playhead (radio, Mac).

| Event | `data` | When |
|---|---|---|
| `source/state` | the whole state | whenever any field but the playhead changes, and only then |
| `source/position` | `{source, session_id, position}` | on a discontinuity only: a seek or a skip, a speed change, or a reading more than 2 s from the anchor. A client ignores a `session_id` that is not its state's |
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
a sender Milō cannot call playing or paused — an AirPlay Realtime stream, a Bluetooth device with no
AVRCP player, every Mac — and the card reads "Connected to X" from `senders`, unless the session
names a cover, a title and an artist, which the full player draws instead (D14).

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
level with the source's own `NETWORK_REQUIREMENT` (`none` / `lan` / `internet`), so a router with no
route out gives `no_internet` to Spotify and `null` to AirPlay, which only needs the LAN — and `null`
to Bluetooth or CD, which need nothing. Then the source's own reason: `no_account` (Qobuz);
`no_drive`, `no_disc`, `reading_disc`, `unreadable_disc`, `ejecting` (CD); `no_storage`,
`catalog_unavailable` (Music Library). `system/connectivity_changed` carries the level alone
(`unknown | none | portal | limited | full`); what it does to the sources arrives as a new
`source/state`. See [Architecture](architecture.md#unavailable-which-is-not-a-state) for where each
reason comes from and the CTAs.

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
