# Integrating with Milō

This guide is for anyone who drives a Milō unit from their own code: a Home Assistant
integration, a script on a home server, a hardware button wired to another machine. It covers
how to reach the unit, read what it is doing, follow it live, send commands, and set the
volume and multiroom — and which parts of the API you can rely on from one release to the next.

The [API overview](api-overview.md) maps the whole surface; the reference pages list it in full:
[REST operations](reference/rest.md), [WebSocket events](reference/ws-events.md),
[audio state](reference/audio-state.md) and [source commands](reference/commands.md).

## Reaching the unit

A unit answers on port 80 at **`http://milo.local`**, its mDNS name. If a second Milō server is on
the same network, the one that came second is `milo-2.local`; a unit is also reachable by its IP
address. Everything is under two paths: the REST API at `/api/…` and a WebSocket at `/ws`.

```sh
curl -s http://milo.local/api/audio/state
```

There is **no authentication**: being on the local network is what grants access. Do not expose a
unit to the internet, and do not put it behind a public reverse proxy.

What the unit does check is where a request comes from, so that a web page cannot drive it
through a browser that happens to be on your network:

- the **`Host`** header must be one of the unit's own names (`milo`, `milo.local`,
  `milo-<n>.local`, `localhost`) or a private IP address. A reverse proxy must forward
  `Host: milo.local`;
- an **`Origin`** header, when there is one, must be one of those same names or a private IP
  address. It is checked on `POST`, `PUT`, `PATCH` and `DELETE`, and on the WebSocket upgrade.

A request with no `Origin` passes — which is what curl, Python, Home Assistant's own HTTP client
and any server-side code send. So call Milō **from a server or a script, not from a web page**: a
dashboard card that fetches from the browser sends its own origin and is refused. A refused request
is answered `403 {"status": "error", "message": "Untrusted request origin"}`; a refused WebSocket
upgrade is a bare `403`.

Each unit serves its OpenAPI schema at `/api/openapi.json` and an interactive view of it at
`/api/docs` (which needs internet access to load its scripts). The schema's `info.version` is the
version the unit runs, for example `v0.1.1`.

## Stability

Two levels, and the reference pages mark each operation and event with its level.

- **Contracted** — what the Milō companion apps for macOS and iOS rely on: the REST operations,
  WebSocket events and source commands the reference pages mark contracted, and the whole audio
  state. Any change to one is listed under **API and integration changes** in the notes of the
  release that makes it, and every operation and event is checked by a test on every change (the
  commands are not yet). In OpenAPI the operations carry `"x-stability": "contracted"`.
- **Internal** — everything else. It is documented and it works, but it may change in any
  release, with no notice beyond the release notes.

There is no deprecation period: Milō does not keep an old shape next to a new one, so a change to
a contracted operation lands whole in one release. Build on the contracted surface where you can,
read the release notes before updating, and compare `info.version` with the release you tested
against.

The contracted surface covers reading the audio state, switching source, sending a playback
command, the volume controls listed under [Volume](#volume), reading the multiroom layout and switching multiroom on and off.
Following the unit live is contracted for the events the [WebSocket reference](reference/ws-events.md)
marks — the state, the volume, the multiroom layout and the multiroom switch's failure, and three
settings. The handshake's reply (`system/initial_state`), `source/position` and
`source/session_ended` are internal, because no companion app reads them: a client that uses them
finds their changes in the release notes' internal list.

## Reading the state

`GET /api/audio/state` answers the whole audio state. With nothing playing:

```json
{
  "source": "none",
  "switching": false,
  "service": "stopped",
  "service_error": null,
  "availability": {"radio": null, "podcast": null, "music_library": null, "cd": "no_disc",
                   "spotify": null, "tidal": null, "qobuz": null, "airplay": null,
                   "bluetooth": null, "mac": null},
  "session": null,
  "controls": [],
  "resume": null,
  "details": null,
  "multiroom_enabled": true,
  "equalizer_effects_enabled": true
}
```

The fields worth knowing first:

- `source` is the selected source, and `service` whether its program is `starting`, `running`,
  `stopped` or `failed` to start.
- `session` is what plays now, or `null`. Its `phase` is `loading`, `playing`, `paused` or
  `connected` (a sender is attached but nothing says whether it plays, as with a Mac); it carries
  `title`, `artist`, `album`, `artwork` (a URL, relative to the unit when it starts with `/`), and
  the playhead as an anchor, `position: {ms, at, rate}`. The position now is
  `ms + (now − at) × 1000 × rate` while `playing`, and `ms` otherwise — the unit does not send a
  position every second, so compute it.
- `resume` is what "play" would bring back when no session is live.
- `controls` lists the commands the source takes right now.
- `availability` says, for every source, why it cannot work now (`no_internet`, `no_disc`…), or
  `null` when it can.

Every field is always present; an absent value is `null`. The [audio state reference](reference/audio-state.md)
lists every field, including each source's `details`.

In Python, with the standard library only:

```python
import json
from urllib.request import urlopen

with urlopen("http://milo.local/api/audio/state", timeout=5) as response:
    state = json.load(response)

session = state["session"]
if session:
    print(f"{state['source']}: {session['phase']} — {session['title']} / {session['artist']}")
else:
    print(f"{state['source']}: nothing playing")
print("commands available now:", state["controls"])
```

## Following the unit live

Polling works, but the unit pushes every change on its WebSocket, `ws://milo.local/ws`. Each frame
is a JSON envelope:

```json
{"category": "source", "type": "state", "origin": "radio", "data": {…}, "timestamp": 1761580800.5}
```

The handshake is one message. Changes are pushed from the moment the socket opens; to receive the
current state as well, send `{"type": "ready"}` as your **first** frame, once you are ready to
handle frames. The unit answers with `system/initial_state`, whose `data.state` is the audio state,
then `volume/volume_changed`. Only that first frame is read: any other JSON object sent first gets no
`initial_state`, and a first frame that is not a JSON object closes the socket. Nothing sent after it is read, and every 30 seconds it sends a
`{"category": "system", "type": "ping"}` frame with no `data`.

The events you need for playback and volume:

| Event | `data` |
|---|---|
| `source/state` | the whole audio state, sent whenever any of it changes |
| `source/position` | `{source, session_id, position}` — the playhead jumped (a seek, a skip) |
| `source/session_ended` | `{source, session_id, reason}` — a session ended, and why |
| `volume/volume_changed` | `{state, show_bar, multiroom_enabled}` — `state` is what `GET /api/volume/state` answers in `data` |

Every event is in the [WebSocket reference](reference/ws-events.md). With the
[`websockets`](https://pypi.org/project/websockets/) package:

```python
import asyncio
import json

import websockets  # pip install websockets


async def follow():
    async with websockets.connect("ws://milo.local/ws") as ws:
        await ws.send(json.dumps({"type": "ready"}))
        async for frame in ws:
            event = json.loads(frame)
            kind = (event["category"], event["type"])
            if kind == ("system", "initial_state"):
                state = event["data"]["state"]
            elif kind == ("source", "state"):
                state = event["data"]
            elif kind == ("volume", "volume_changed"):
                print("volume:", round(event["data"]["state"]["global_volume_db"], 1), "dB")
                continue
            else:
                continue
            session = state["session"]
            print(state["source"], session["phase"] if session else "idle")


asyncio.run(follow())
```

Reconnect when the socket closes — a unit restarting its backend closes it — and send `ready`
again: events missed while disconnected are not replayed, and `initial_state` is how you catch up.

## Sending commands

**Switching source** is `POST /api/audio/source/{source}`, where `source` is one of `spotify`,
`bluetooth`, `radio`, `podcast`, `airplay`, `mac`, `cd`, `qobuz`, `tidal`, `music_library`, or
`none` to stop everything:

```sh
curl -s -X POST http://milo.local/api/audio/source/radio
```

It answers `{"status": "success"}` once the source has started, or `{"status": "error"}` with HTTP
200 when it could not; the state then reads `service: "failed"`.

**Every playback command** goes through one route, `POST /api/audio/control/{source}`, with the
command's name and its parameters:

```sh
curl -s -X POST http://milo.local/api/audio/control/spotify \
  -H 'Content-Type: application/json' \
  -d '{"command": "pause", "data": {}}'
```

It answers `{"status": "success"}` when the source took the command; what changed arrives as the
next `source/state`. A command the source does not know, or parameters that do not validate, is
answered `400` with `{"detail": "…"}`. Send the commands `controls` lists: they are the ones the
source takes now, by their exact names (`resume`, not `play`). The commands of each source and
their parameters are in the [command reference](reference/commands.md). The route is contracted;
of the commands, only those the companion apps send are relied on — the reference lists them — and
no test checks them yet.

## Volume

`GET /api/volume/state` answers the volume of the unit and of every multiroom speaker:

```sh
curl -s http://milo.local/api/volume/state
```

```json
{
  "status": "success",
  "data": {
    "mode": "multiroom",
    "global_volume_db": -43.9,
    "global_volume": 0.4865,
    "global_mute": false,
    "limit_min_db": -78.0,
    "limit_max_db": -8.0,
    "volume_control": true,
    "any_volume_control": true,
    "clients": {
      "dc:a6:32:7e:d3:43": {"volume_db": -44.0, "volume": 0.4857, "mute": false,
                            "available": true, "volume_control": true}
    },
    "zones": {
      "ec51dad4-1cfd-4d94-88f8-b8be3606e716": {"id": "ec51dad4-1cfd-4d94-88f8-b8be3606e716",
        "name": "Living room", "client_ids": ["dc:a6:32:7e:d3:43"],
        "average_volume_db": -44.0, "all_muted": false}
    }
  }
}
```

Volume has two scales: decibels (`volume_db`), and `volume`, 0 to 1 between the limits the owner
set in the unit's settings (`limit_min_db`, `limit_max_db`). The writes, all contracted:

| Request | Body | Effect |
|---|---|---|
| `POST /api/volume/adjust` | `{"delta_db": 2}` | Moves the volume by a step, from where it is when the request lands. Answers the new level on both scales. |
| `PATCH /api/volume/global` | `{"volume": 0.5}` | Sets the volume, on the 0–1 scale. |
| `PATCH /api/volume/client/mac/{mac}` | `{"volume_db": -40}` or `{"volume": 0.5}` | Sets one speaker. `{mac}` is written **without colons** (`dca6327ed343`); a `volume_db` outside the limits is answered `400`. |
| `PATCH /api/volume/client/mac/{mac}/mute` | `{"mute": true}` | Mutes or unmutes one speaker. |
| `PATCH /api/volume/zone/{zone_id}` | `{"delta_db": -3}` or `{"volume_db": -40}` | Moves every speaker of a zone together. |

```sh
curl -s -X POST http://milo.local/api/volume/adjust \
  -H 'Content-Type: application/json' -d '{"delta_db": -2}'
```

Each change is broadcast as `volume/volume_changed`.

## Multiroom

In multiroom, the unit plays in sync on its own output and on every satellite speaker. The
layout — speakers and the zones that group them — is `GET /api/multiroom/state`:

```json
{
  "clients": {
    "dc:a6:32:7e:d3:43": {"mac_id": "dc:a6:32:7e:d3:43", "name": "Sofa", "ip": "192.168.1.153",
                          "zone_id": "ec51dad4-1cfd-4d94-88f8-b8be3606e716", "online": true,
                          "is_local": false, "volume_control": true, …}
  },
  "zones": {
    "ec51dad4-1cfd-4d94-88f8-b8be3606e716": {"id": "ec51dad4-1cfd-4d94-88f8-b8be3606e716",
      "name": "Living room", "client_ids": ["dc:a6:32:7e:d3:43"], "online_client_count": 1, …}
  }
}
```

A speaker is identified by its MAC address everywhere; `is_local` marks the unit's own output.
Changes arrive as `multiroom/client_state_changed` and `multiroom/zone_changed`.

Multiroom is switched on and off with `PUT /api/routing/multiroom`:

```sh
curl -s -X PUT http://milo.local/api/routing/multiroom \
  -H 'Content-Type: application/json' -d '{"enabled": true}'
```

The switch reroutes the audio and takes several seconds; the request returns when it is over,
with `{"status": "success", "multiroom_enabled": true, "source": "…"}`. Meanwhile the audio
state's `switching` is `true`, and a switch that fails sends `routing/multiroom_error`. The state's
`multiroom_enabled` is the mode in force.

## Errors

A request that fails mostly answers an HTTP error status with FastAPI's body, `{"detail": "…"}`:
`400` for a request the unit refuses, `404` for something that does not exist, `422` for a body
that does not validate (with the offending fields in `detail`), `502` or `503` when something the
unit depends on did not answer — a satellite, the music catalog, an online directory — and `500`
for a failure on the unit. The origin check's `403` carries `{"status": "error", "message": …}`
instead (see [Reaching the unit](#reaching-the-unit)).

Two more exceptions are kept on purpose. A few status reads, `GET /api/volume/state` among them, answer
a failure as HTTP 200 with `{"status": "error", "message": "…"}`, and `POST /api/audio/source/{source}`
answers a source that would not start the same way. So check `status` as well as the HTTP code
wherever an answer carries one. Writes answer `"status": "success"` when they worked; reads such as
`GET /api/audio/state` or `GET /api/multiroom/state` return their object with no `status` key.
