# Manual verification checklist

**Audience:** whoever changed the code. Not an end-user document — the user manual is
[manual_en.md](manual_en.md).

Most of what Milō *is* cannot be seen by CI. ALSA device selection, CamillaDSP in the path,
snapcast sync, the rotary encoder, the IR receiver, the screen, ROC from a Mac, a real
Spotify Connect handoff: none of it runs in `pytest`. A refactor can be fully green and have
silently removed AirPlay artwork — no error, no log line, the feature simply stops existing.
This file is the net for exactly that class of failure.

Each check is written as an **observable**, not a procedure: something anyone can look at and
answer yes/no without knowing the implementation.

## How to use it

| Set | When to run | Cost |
|---|---|---|
| **smoke** | after *any* change that touches the backend, the frontend build, or the audio path — before the commit is considered done | **~10 min** |
| **targeted** | only when that subsystem was touched | minutes per subsystem |

Run on a real unit, against the **production build** (nginx serving `dist/`), not the Vite
dev server — see `CLAUDE.md` § *Dev-only vs production bugs*. Reboot once before starting a
smoke run if the previous session left services restarted by hand.

### The smoke set (~10 min)

1. Unit boots to the kiosk UI, dock renders, `systemctl --failed` is empty.
2. Radio: a favourite plays, station logo + name + bitrate visible, play/pause works.
3. Spotify: hand off from the phone, artwork + title + artist appear, progress advances.
4. Volume: rotary knob and on-screen +/- both move the level and the audible output.
5. Equalizer: a preset change is audible and survives a source switch.
6. Source switch (radio → spotify → radio) leaves no stuck audio and no ghost state.
7. Screen: sleep timeout turns it off, touch wakes it, brightness setting applies.
8. Background the browser tab ~60 s, return: state matches reality (resync, not stale).
9. `journalctl -u milo-backend --since -10min | grep -E "ERROR:|WARNING:"` shows nothing new.
   **Not `-p warning`** — see the note under *Boot and process health*.

If a change touched only one subsystem and none of the shared layers (state machine, WS,
volume, routing), the smoke set can be cut to items 1, 6, 9 plus that subsystem's targeted
table. Say so explicitly in the commit — an unstated shortcut is indistinguishable from a
skipped check.

### Known blind spots

These cannot be verified on a single developer unit and any phase touching them is **risky**
by default (README Rule 3):

| Area | Needs |
|---|---|
| Multiroom sync, zones, crossover, pending clients, Wi-Fi adoption | a **second Pi** running as client |
| Mac source (ROC) | a **Mac** with the ROC sender app |
| CD | a **USB drive + audio discs**, including one absent from the online catalogue |
| Qobuz Connect | a **paid Qobuz account** |
| Podcasts | **Podcast Index API credentials** with monthly quota left |
| IR remote | an **Apple Remote A1156** + wired IR receiver |
| Bluetooth remote | an **ANTICATER VK-01** (or compatible BT HID remote) |
| Music Library over SMB/NFS | a **NAS/share**; USB-only covers half the source |
| Hardware variants | other **HiFiBerry cards**, the 7" USB vs 8" DSI screens |
| First boot | a **blank SD card** — the AP + captive portal + wizard path is never re-exercised on a configured unit |
| Updates | an actual **published release** newer than the installed one |

Anything below marked ⚠ depends on one of these.

### Code with no automated guard at all

Classifying the backend suite (phase 3) surfaced the areas `pytest` did **not** cover and
that the tables below do not routinely exercise either. Phase 8 closed most of them; what
this section is *for* is naming what is left, so keep it honest — overstating the gap is
what made two of its original four rows wrong.

| Area | State | What would catch a regression |
|---|---|---|
| `core/network/` — the D-Bus signal tier | The nmcli read path, the fail-open contract and the hotspot transition are now covered by `test_network_service.py`. The ~300 lines that subscribe to NetworkManager property-changed signals and re-anchor the IP4/AP object paths are **still bare**: they only run against a live NM, and their main path is the ⚠ blank-SD blind spot above | the First boot ⚠ row, on a blank card |
| Privileged-exec argv vs the sudoers policy | **covered** — `tests/contracts/test_privileged_exec_contract.py` extracts both sides and asserts them in both directions, for the backend and the satellite | — |
| `shared/decorators.py` (`@handle_errors`) | **covered** — `test_decorators.py`, including that it does not swallow `CancelledError` | — |
| WS keepalive and dead-connection reaping | **covered** — `test_send_ping` and `test_send_ping_stops_on_error` drive the real ping task; removal is asserted exactly by `test_websocket_server.py::test_broadcast_dict_removes_dead_connections` and, through the real `connect()` path, by `test_websocket_events.py::test_manager_removes_dead_connections` | — |

---

## Boot and process health

**Filter the journal on the level *prefix*, never on `-p`.** The backend logs through Python's
`logging` to stdout, so journald files every line — `INFO`, `WARNING`, `ERROR` alike — at the
default priority, and the level lives in the message text. `journalctl -p warning` therefore
returns *no entries* on this appliance whatever is happening, which is indistinguishable from a
clean run. Three verification passes recorded it as evidence before anyone noticed.

| Check | Expected observable | Set |
|---|---|---|
| Cold boot | Kiosk reaches the dock without a manual refresh; no blank page, no Vite banner | smoke |
| Units | `systemctl --failed` empty; `milo-backend`, `milo-camilladsp`, `milo-kiosk` active | smoke |
| Source units | Only the units for *enabled* dock sources are running; `BindsTo` holds (stopping `milo-backend` stops them) | targeted |
| Warnings | `journalctl -u milo-backend --since -10min \| grep -E "ERROR:\|WARNING:"` shows nothing new | smoke |
| Schema versions | No fail-loud banner in the journal (`SchemaVersionMismatch` → the unit restart-loops) | smoke |
| Dock | Every source enabled in Settings > Dock appears, in the configured order; disabled ones absent | targeted |

## Shared player and state machine

These are shared by every source; they break for all of them at once.

| Check | Expected observable | Set |
|---|---|---|
| Source switch | radio → spotify → radio: the previous source stops audibly within ~1 s; no two sources audible at once | smoke |
| No ghost state | After switching away, the old source's artwork/title never reappears behind the new one | smoke |
| Transition drop | Switching sources rapidly 4–5 times ends on the source last selected, not on an intermediate one | targeted |
| Progress bar | Advances in real time and matches the audio (±1 s) for spotify, cd, podcast, music_library, airplay, dlna, qobuz | targeted |
| Seek | Dragging the bar moves the audio to that point (spotify, cd, podcast, music_library) | targeted |
| WS resync | Background the tab ≥60 s, then return: volume, active source, metadata and multiroom state all match reality | smoke |
| Reconnect | `sudo systemctl restart milo-backend` with the UI open: the UI reconnects on its own and shows the real state, no reload needed | targeted |

## Sources

### Spotify (C — active player)

| Check | Expected observable | Set |
|---|---|---|
| Discovery | "Milō" appears in the Spotify app's device list | smoke |
| Handoff | Selecting it starts audio on Milō within a few seconds | smoke |
| Metadata | Album artwork, title, artist appear and change with the track | smoke |
| Transport | Play/pause, next/previous from the Milō UI are reflected in the phone app, and vice versa | targeted |
| Seek | Progress bar drag moves playback | targeted |
| Auto-disconnect | With the setting on, the source releases after the configured delay of inactivity | targeted |

### Radio (C)

| Check | Expected observable | Set |
|---|---|---|
| Favourites | The favourites grid renders with logos | smoke |
| Playback | Tapping a station plays within a few seconds; play/pause works | smoke |
| Station info | Name, genre, codec and bitrate shown under the player | smoke |
| Track recognition | With recognition on, a recognised track's title + artist appear within ~30 s and update on track change | targeted |
| Custom station | An added/customised station keeps its name, image and stream URL after a backend restart | targeted |
| Custom station, edited | Réglages → Webradio: add a station, rename it, then **reload the page** — the list shows the new name, not the one it was created with (in-session the WS delta hides a stale record; only a reload re-reads it) | targeted |
| Custom station, deleted | Delete an **edited** custom station → its card disappears from "Stations ajoutées" and does not come back on reload (it lives in two stores; dropping one left an un-deletable ghost) | targeted |
| Screensaver | After the configured delay, full-screen station + track info; touch returns to the UI | targeted |

### Podcast (C) ⚠ API credentials

| Check | Expected observable | Set |
|---|---|---|
| Catalogue | Home/search/genres return results (not an empty state) | targeted |
| Playback | Episode plays; artwork + episode title shown | targeted |
| Transport | −15 s / +30 s buttons move playback by that amount; progress bar seek works | targeted |
| Speed | Changing speed changes the audible rate and persists across pause/resume | targeted |
| Resume | Leaving mid-episode and returning resumes within a few seconds of where it stopped, after a reboot too | targeted |
| Quota | Settings > Podcasts shows a plausible request count and reset date | targeted |

### CD (C) ⚠ drive + discs

| Check | Expected observable | Set |
|---|---|---|
| Detection | Inserting a disc is noticed within a couple of seconds; tracklist builds | targeted |
| Lookup | Album title, artist, cover art and track names appear for a catalogued disc | targeted |
| Fallback | A disc absent from the online catalogue still plays with generic track names — no error state | targeted |
| Transport | Play/pause, next, seek work; **previous** early in a track goes to the previous track, late in a track restarts the current one | targeted |
| Progress | Position matches the audio and survives pause/resume | targeted |
| Eject | The eject control releases the disc and the UI leaves the CD source cleanly | targeted |

### Music Library (C) ⚠ USB and/or share

| Check | Expected observable | Set |
|---|---|---|
| USB mount | Plugging a drive is detected, mounted, and indexing starts on its own | targeted |
| Indexing UX | "Building library…" with a live, increasing track count; the UI stays usable | targeted |
| Catalogue | Artists / Albums / Genres / Playlists browse and search all return results with artwork | targeted |
| Queue | Tapping an album plays it back-to-back in order, gapless | targeted |
| Transport | Play/pause, next/previous, seek, progress bar | targeted |
| Share | An SMB/NFS share added in Settings mounts and indexes; wrong credentials produce a *named* error, not a silent empty library | targeted |
| Unplug | Removing the USB drive leaves the UI in a coherent state (no spinner forever) | targeted |

### AirPlay (B — passive player)

| Check | Expected observable | Set |
|---|---|---|
| Discovery | Milō appears as an AirPlay target from iPhone and from a Mac | targeted |
| Audio | Audio starts within a couple of seconds of selecting it | targeted |
| Metadata | **Artwork**, title and artist appear in the full player within ~2 s of a track change | targeted |
| Device name | The sender's name ("Léo's iPhone") is shown | targeted |
| No controls | No transport buttons are rendered (control is sender-side) | targeted |
| Progress | Position advances and re-syncs after a sender-side seek | targeted |

> Artwork is the historical silent-failure point here (shairport-sync 5.1 shipped without
> metadata and nothing detected it). Never skip the metadata row.

### DLNA (B)

| Check | Expected observable | Set |
|---|---|---|
| Discovery | "Milo" appears as a renderer in a UPnP controller | targeted |
| Playback | Pushing a track starts audio | targeted |
| Metadata | Artwork, title, artist, album shown; no transport buttons | targeted |

### Qobuz Connect (B) ⚠ paid account

| Check | Expected observable | Set |
|---|---|---|
| Sign-in | Settings > Qobuz shows the signed-in state and survives a reboot | targeted |
| Discovery | Milō appears in the Qobuz app's device list | targeted |
| Playback + metadata | Audio plays; artwork, title, artist, album shown; progress advances; no transport buttons | targeted |

### Bluetooth (A — mute receiver)

| Check | Expected observable | Set |
|---|---|---|
| Pairing | Milō is discoverable and pairs from a phone | targeted |
| State | UI shows "Connected to [device name]" with a Disconnect button; "Ready" when idle | targeted |
| Audio | Playback from the phone is audible; no metadata is expected | targeted |
| Disconnect | The button drops the link and the UI returns to "Ready" | targeted |

### Mac / ROC (A) ⚠ Mac required

| Check | Expected observable | Set |
|---|---|---|
| Detection | Starting the ROC sender switches Milō to the Mac source on its own | targeted |
| State | UI shows "Connected to [Mac name]"; "Ready to stream" when idle | targeted |
| Audio | Continuous audio, no dropouts, at the configured latency profile | targeted |

## Volume and CamillaDSP

CamillaDSP is always in the path — if it drops out, audio often still plays, which is what
makes this silent.

| Check | Expected observable | Set |
|---|---|---|
| Rotary encoder | Each detent moves the level by the configured step; the on-screen level follows | smoke ⚠ |
| Touch +/- | Same, at the touch step | smoke |
| Curve | The perceived loudness change is smooth across the range — no jump, no dead zone at the ends | targeted |
| Limits | The level cannot go below the min or above the max configured in Settings > Volume | targeted |
| Startup volume | After a reboot, the level matches the configured policy (restore last / fixed) | targeted |
| DSP in path | `/proc/asound/card0/pcm0p/sub0/status` reads `RUNNING` while playing (not `PAUSED` — see the silence-pause failure mode) | targeted |
| Remote steps | BT/IR remote volume uses its own per-click step ⚠ | targeted |

## Equalizer

| Check | Expected observable | Set |
|---|---|---|
| Preset | Switching preset (e.g. Flat → Bass Boost) is **audible** immediately | smoke |
| Persistence | The preset survives a source switch and a reboot | smoke |
| Bands | Moving a single band is audible and the curve is reflected in the UI | targeted |
| Loudness | Enabling loudness at low volume audibly lifts bass/treble | targeted |
| Compressor | Enabling it audibly narrows the loud/quiet gap | targeted |
| Bypass | Effects toggle off/on without a gap or click in the audio | targeted |
| Per-client EQ ⚠ | A remote client's EQ applies to that client only; a zone shows no EQ of its own | targeted |
| Curve while loading | Switching target, re-enabling after a bypass, or reopening the page must **never** show a flat curve on the way to the real one — until the record lands the figures read "—" and the sliders are hidden. A flat curve is indistinguishable from a real `flat` preset, and this is a race, so repeat it ~5× before calling it clean | targeted |
| Master bypass in a zone ⚠ | Turning the **dock's** Equalizer app off while the appliance is a zone member must silence the effects on **every** member, satellites included — not only the local DAC. The flag lives in two domains (settings.json locally, the per-client record remotely), so a local-only write leaves satellites audibly out of step with nothing to repair it. Check the satellite itself (`GET :8001/equalizer/status` → `equalizer_enabled`), not just what the page reports | targeted |

## Multiroom ⚠ second unit

| Check | Expected observable | Set |
|---|---|---|
| Enable | The main switch activates within a few seconds; audio continues | targeted |
| Boot with multiroom already on | After a reboot, each satellite comes back **at its own stored level**, not at the local one — the boot-time push (`_delayed_multiroom_sync`) waits on snapserver answering, then re-sends. Nothing in CI executes its body: the suite only ever reaches its give-up branch | targeted |
| Sync | Two speakers playing the same track show no audible echo or drift over ≥5 min | targeted |
| Client volume | The per-speaker slider and mute affect only that speaker | targeted |
| Zone | A zone groups speakers; its global volume and mute act on all members | targeted |
| Pending client | A freshly installed client appears under "Pending speakers" without a reload | targeted |
| Pairing reboot | Finishing the wizard on a new client **actually reboots it** (it drops off the network, then returns with the chosen card working). The server only warns on a failed reboot, so a wizard that reports success while the satellite never restarted is the silent failure to watch for | targeted |
| Crossover | With a subwoofer online in a zone, the badge shows the frequency and the mains lose their bass; taking the sub offline restores full-range | targeted |
| Disable | Turning multiroom off returns to direct mode with audio intact (ALSA device switch) | targeted |
| Offline client | Unplugging a client leaves the others playing and marks it offline | targeted |
| Server config write | Changing codec or buffer in Settings > Multiroom restarts snapserver, every client reconnects and plays, and the new values are still shown after a reload. A **rejected** value must surface as an error, not a silent success | targeted |

## Hardware controls

| Check | Expected observable | Set |
|---|---|---|
| Rotary knob | Turn changes volume; press acts as play/pause | smoke ⚠ |
| IR remote ⚠ | Pairing detects the remote within the countdown; volume, play/pause, next/prev act on the current source; Menu switches source in dock order, double-press stops, hold turns the screen off | targeted |
| IR identity ⚠ | A *different* Apple Remote in the room does not control the unit | targeted |
| BT remote ⚠ | Pairs, shows battery level, buttons act; unpair forgets it | targeted |
| BT remote lost ⚠ | **Switch a paired, connected remote off.** Settings > BT remote returns to disconnected on its own, with no reload. The panel writes `connected` optimistically and has no other route back to the truth, so only the scan's own broadcast can correct it | targeted |
| BT remote unpaired asleep ⚠ | **Unpair a remote that is asleep** (bonded, not connected, no evdev node). Every open surface — phone and kiosk at once — shows it unpaired, with no reload. Nothing else broadcasts on this path: the scan drops no node, so the explicit broadcast is the only event | targeted |
| Fan | Runs and is controlled by temperature (no permanent full speed, no permanent off under load) | targeted |

## Screen and kiosk

| Check | Expected observable | Set |
|---|---|---|
| Brightness | The setting changes the panel intensity immediately | smoke |
| Sleep | The screen turns off after the configured inactivity delay; touch wakes it to the same view | smoke |
| Screensaver | Appears after its delay on radio/podcast with current info; touch returns to the UI | targeted |
| Scale | Small / Normal / Large change the layout without clipping | targeted |
| Kiosk memory | `systemctl status milo-kiosk` shows no recent restart loop after ~1 h of use (past OOM regression) | targeted |

## Network and setup ⚠ blank SD

| Check | Expected observable | Set |
|---|---|---|
| Wi-Fi list | Settings > WiFi lists known and nearby networks; connect and forget work | targeted |
| Status | Connected SSID, signal and IP shown and correct | targeted |
| mDNS | `http://milo.local` resolves from another machine on the LAN | targeted |
| First boot ⚠ | Blank SD: the open "Milō" AP appears, the captive portal opens, the wizard completes and reboots into a working unit | targeted |
| Hostname conflict | Two units on the LAN do not silently share a name | targeted |
| First-boot splash ⚠ | Blank SD, **no ethernet**: `milo-first-boot` spends its full carrier + DHCP + mDNS budget, so the bar creeps on its own for far longer than on a warm unit. It must keep advancing, ever more slowly, and never park — that creep is the only thing on screen for up to two minutes. The journal is not enough here; this one needs eyes | targeted |
| Role auto-detection ⚠ | Blank SD, ethernet, with a Milō server already answering on `milo.local`: the unit detects it, converts itself and reboots as a satellite. `avahi-daemon` must be **active**, not 203/EXEC — the conversion installs `milo-client-apply-avahi-iface`, and its absence is silent everywhere but there | targeted |
| Converted satellite's grants | After that conversion, on the unit: `/etc/sudoers.d/milo-client` matches the repo and carries its `PASSWD: ALL` rule *above* the grants. Without it the argument-scoped policy is decorative and the satellite quietly holds the image's blanket NOPASSWD | targeted |

## Settings, updates, system

| Check | Expected observable | Set |
|---|---|---|
| Persistence | Any changed setting survives `sudo systemctl restart milo-backend` **and** a reboot | smoke |
| Language | Switching language changes the UI immediately, with no untranslated key visible | targeted |
| Hardware page | Changing card/screen/encoder/IR offers "Apply and reboot", and the unit comes back with the new config applied | targeted |
| Updates ⚠ | An available release is listed; applying it deploys and restarts the unit, which comes back healthy on the new version | targeted |
| Programs ⚠ | A per-program update completes and that source still plays afterwards | targeted |
| Information | Version, IP, CPU temperature and CPU/RAM usage are plausible and refresh | targeted |
| Power | Restart and shutdown both ask for confirmation and do what they say | targeted |

---

## Notes for the cleanup programme

- A check here that turns out to be automatable is **input for phase 3**, not a test to write
  now (README anti-goals).
- A phase touching a ⚠ area is `risky` by default: propose, do not apply without explicit
  sign-off and an on-unit verification plan.
