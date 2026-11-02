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
| Tidal Connect | a **paid Tidal account** |
| Podcasts | nothing — Apple and the publishers' feeds are both keyless |
| IR remote | an **Apple Remote A1156** + wired IR receiver |
| Bluetooth remote | an **ANTICATER VK-01** (or compatible BT HID remote) |
| Music Library over SMB/NFS | a **NAS/share**; USB-only covers half the source |
| Hardware variants | other **HiFiBerry cards**, the 7" USB vs 8" DSI screens |
| First boot | a **blank SD card** — the AP + captive portal + wizard path is never re-exercised on a configured unit |
| Kiosk with no screen | the **panel physically disconnected**. `milo-apply-hardware` now disables `milo-kiosk.service` when `screen.type` is `none`, which is correct whatever cage does with no connected DRM output — but the symptom it fixes (a restart loop every 3 s, plus the 339 MB Chromium prewarm at every boot) has **not** been observed on a unit, only reasoned about |
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
| Selection right after a restart | While nothing plays, `sudo systemctl restart milo-backend`: once the journal shows `Milo backend startup completed`, the state reads `source: none` and `systemctl is-active` answers `inactive` for every source unit. Select Spotify: `avahi-browse -rt _spotify-connect._tcp` lists the unit, and so does the phone's Connect list. A source selected before the boot's lingering-unit sweep (an IR MENU press while the services initialize) once had its unit stopped behind it: the screen said Spotify, the speaker was gone from the phone, and re-selecting Spotify answered "Already on source" | targeted |
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
| Back to a player source | In direct mode at the lowest volume, through the API: play a station, select another source 0–200 ms later (varied each time), then Radio again — 20 times. Every return reaches `playing` within the 30 s loading watchdog and the capture carries signal; the journal never shows `Playback ended: load_failed`. A stop landing just as mpv announced something once left the source deaf to mpv: every later station sat loading until the watchdog ended it, until the backend restarted | targeted |
| Stop that fails before its unit | `sudo chattr +i /var/lib/milo/podcast_data.json` (undone by `chattr -i` in a trap) makes the podcast's progress save fail first. Play an episode ≥10 s, then select Music Library, in direct mode then in multiroom: the podcast falls silent in every room, `systemctl is-active milo-podcast` answers `inactive`, errors.log carries "podcast would not stop; stopping its unit". With a `/run` drop-in setting `RefuseManualStop=yes` instead, both stops are refused: errors.log carries that line and "unit refused its stop too", and Music Library still starts | targeted |
| Progress bar | Advances in real time and matches the audio (±1 s) for spotify, cd, podcast, music_library, airplay, qobuz, tidal | targeted |
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
| Auto-disconnect | With the setting on, a pause past the configured delay ends the session: the phone lets go of Milō, no banner | targeted |
| Multiroom toggle | Mid-play, the track keeps playing across the toggle; the card never shows "paused" meanwhile | targeted |
| Daemon death | `kill -9` of go-librespot mid-play: the card leaves "playing" at once with a banner; the phone can reconnect ~5 s later | targeted |

### Radio (C)

| Check | Expected observable | Set |
|---|---|---|
| Favourites | The favourites grid renders with logos | smoke |
| Playback | Tapping a station plays within a few seconds; play/pause works | smoke |
| Station info | Name, genre, codec and bitrate shown under the player | smoke |
| Transport | The full player and the kiosk's playing bar show one labelled button, "Stop" ("Play" once stopped), with the favorite toggle (⊕ / ✓) across from it; the phone's mini-bar shows the stop glyph alone. Rotary double/triple-click and the IR remote's next/prev keys do nothing | targeted |
| Track recognition | With recognition on, a recognised track's title + artist appear within ~30 s and update on track change | targeted |
| Custom station | An added/customised station keeps its name, image and stream URL after a backend restart | targeted |
| Custom station, added | Réglages → Webradio: add a station → it appears in the favorites grid at once, its ✓ shown and disabled; the catalog search does not list it | targeted |
| Custom station, edited | Rename the added station, then **reload the page** — the list shows the new name, not the one it was created with (in-session the WS delta hides a stale record; only a reload re-reads it) | targeted |
| Custom station, deleted | Delete an **edited** custom station → its card disappears from "Stations ajoutées" and from the grid, and neither comes back on reload | targeted |
| Favorite released | Change a playing favorite's image, release its ✓ → the image stays on the player and in the search, the card stays under "Stations modifiées"; favorite it again with ⊕ → it comes back with its image; restore it from Réglages → the catalog logo replaces it everywhere at once, the player included | targeted |
| Automatic full-screen player | Playing, untouched for the configured delay → the full player opens with station + track info; a tap on its cover returns to the same page of the navigation | targeted |

### Podcast (C) ⚠ API credentials

| Check | Expected observable | Set |
|---|---|---|
| Catalogue | Home/search/genres return results (not an empty state) | targeted |
| Playback | Episode plays; artwork + episode title shown | targeted |
| Transport | −15 s / +30 s buttons move playback by that amount; progress bar seek works | targeted |
| Normal pace | An episode plays at its normal pace, including after a resume; neither the bar nor the full player shows a speed control | targeted |
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
| Shuffle / repeat | Shuffle toggled mid-track does not interrupt it, and at its end a track other than the next one plays; repeat-one plays the track again; repeat-all goes from the last track back to the first; both survive leaving the source and coming back, and are off for a newly inserted disc | targeted |
| Eject | The eject control releases the disc and the UI leaves the CD source cleanly | targeted |

### Music Library (C) ⚠ USB and/or share

| Check | Expected observable | Set |
|---|---|---|
| USB mount | Plugging a drive is detected, mounted, and indexing starts on its own | targeted |
| Indexing UX | "Building library…" with a live, increasing track count; the UI stays usable | targeted |
| Catalogue | Artists / Albums / Genres / Playlists browse and search all return results with artwork | targeted |
| Queue | Tapping an album plays it back-to-back in order, gapless | targeted |
| Transport | Play/pause, next/previous, seek, progress bar | targeted |
| Shuffle | Shuffle toggled in the last second of a track of a long playlist: the next track plays and it is the one shown, and the queue view lists the order mpv plays (`get_property playlist` on its IPC socket). At the end of the queue the source stops — never PLAYING over silence. The race with mpv's own advance and a lost IPC reply are pinned in CI on the measured mpv simulation (2026-11-01, mpv 0.40 on a null audio output) | targeted |
| Repeat | On a short album: **queue** — after the last track the first one follows with no gap, its title replaces the last one's and the bar restarts at 0; on the last track, next goes to the first. **Track** — the track starts over with no gap and the bar back at 0, and a second pass past half the track adds a play to it in Navidrome (`type=frequent`). **Off** — the album stops after its last track and the player closes, with no audio after. The mode survives a detour to another source and back; a new album starts with repeat off. mpv's loop events were measured on a null audio output (2026-10-04); a gap at the seam can only be heard | targeted |
| Share | An SMB/NFS share added in Settings mounts and indexes; wrong credentials produce a *named* error, not a silent empty library | targeted |
| Unplug | Removing the USB drive leaves the UI in a coherent state (no spinner forever) | targeted |
| Playlist edit, key unplugged | A playlist holding tracks of the share and of a USB key, the key unplugged: in the playlist view, remove a share track and drag two others into a new order; then, from the ⋯ of a share track, uncheck the playlist in "Add to playlist". Read the playlist raw from Navidrome (Subsonic `getPlaylist`, not Milō's filtered `GET /playlist/{id}`) after each edit: every key track is still there, in its place, and only the tracks edited moved or left. With `milo-navidrome` stopped, the uncheck fails with an error and writes nothing | targeted |
| Share liveness deadline ⚠ | With the NAS's cable pulled, `statvfs` on its mountpoint **returns before `_LIVENESS_TIMEOUT_S`**. Measured 2026-09-02: the kernel answered `EHOSTDOWN` after 10.18 s, against a 15 s deadline. This is an environmental fact no unit test can assert, and it decides whether the probe can tell a dead NAS from a merely slow one: if the kernel is slower than the deadline, every negative arrives as a timeout and a slow NAS is hidden like a dead one, stopping the music | targeted |

### AirPlay (B — passive player, the sender's transport)

| Check | Expected observable | Set |
|---|---|---|
| Discovery | Milō appears as an AirPlay target from iPhone and from a Mac | targeted |
| Audio | Audio starts within a couple of seconds of selecting it | targeted |
| Metadata | **Artwork**, title and artist appear in the full player within ~2 s of a track change | targeted |
| Artwork under fast skips | Three `next` about 1 s apart over tracks from different albums: after each, the `session.artwork` URL of `GET /api/audio/state` answers exactly the bytes of the file shairport-sync wrote (sha256 of the `ArtworkPath` in `NowPlayingInformation`) with `immutable`; the previous step's URL answers 404, never another cover; the bare `/api/airplay/artwork` answers `no-store`. The kiosk and the lock screen show the cover of the track on screen | targeted |
| Only shairport-sync is listened to | During a session, a well-formed `PropertiesChanged` of `Client.NowPlayingInformation` emitted from another connection (`gdbus emit --system --object-path /org/gnome/ShairportSync --signal org.freedesktop.DBus.Properties.PropertiesChanged …`) naming `/var/lib/milo/settings.json` as its picture changes nothing: the title, the artist and the cover's `v` stay, and `/api/airplay/artwork` still answers shairport-sync's file. The covers shairport-sync wrote sit in `/tmp/shairport-sync/.cache/coverart` (`COVER_ART_DIR`), the only directory a cover is read from: one anywhere else is logged `AirPlay artwork unreadable` and never shown | targeted |
| Device name | The sender's name ("Léo's iPhone") is shown | targeted |
| Controls | Pause, resume, next and prev from Milō act on the iPhone and on a Mac; the main button never flashes play during a next/prev; shuffle and repeat follow both ways | targeted |
| Volume | The iPhone's volume moves Milō's, and Milō's moves the iPhone's slider; muting Milō does not drop the house to its floor | targeted |
| Volume under a fast turn | Six knob steps up in under a second, then the sender's slider moved while the knob turns (iPhone or Mac): the level never steps back during the climb, the kiosk bar and the sender's slider settle on the last gesture and nothing moves 2 s later — no back-and-forth. Each step shairport-sync writes back once it has answered, so an earlier step's write-back lands after the next one | targeted |
| Progress | Position advances, returns to 0:00 after a prev that restarts the track, and re-syncs after a sender-side seek | targeted |
| Progress on a unit clock that is off | NTP off and the unit's clock moved 10 min behind, then 10 min ahead (`timedatectl set-ntp false; date -s …`, put back by a trap that turns NTP on again), each time with a session opened after the move: the bar follows the sender's position within 2 s, does not go back to 0:00 on a volume step or a next, and a pause then play from the sender publishes no PAUSED once PLAYING is back. 10 min ahead, the bar is right only from the second stamp that agrees, so pause and play once before reading it. With the unit 10 min behind, turning NTP back on while that session plays steps the clock forward: the next volume step leaves the bar on the sender's position, not 10 min on. With the clock synced again, one more session behaves the same. The journal names the offset once per session (`This unit's clock is … s behind/ahead of the AirPlay sender's`) | targeted |

> Artwork is the historical silent-failure point here (shairport-sync 5.1 shipped without
> metadata and nothing detected it). Never skip the metadata row.

### Qobuz Connect (B) ⚠ paid account

| Check | Expected observable | Set |
|---|---|---|
| Sign-in | Settings > Qobuz shows the signed-in state and survives a reboot | targeted |
| Discovery | Milō appears in the Qobuz app's device list | targeted |
| Playback + metadata | Audio plays; artwork, title, artist, album shown; progress advances; no transport buttons | targeted |

### Tidal Connect (C)

A free account casts from the desktop app (30 s previews, a track change every 30 s without a
hand on the sender); the phone app needs a paid one.

| Check | Expected observable | Set |
|---|---|---|
| Discovery | Milō appears in the Tidal app's device list | targeted |
| Playback + metadata | Audio plays; artwork, title, artist, album shown; progress advances | targeted |
| Transport | Pause/resume and prev/next from Milō act on the audio and the Tidal app follows | targeted |
| No seek | The progress bar cannot be dragged (the protocol exposes none) | targeted |
| Session recovery | Restarting milo-tidal, then casting again from the phone, starts a session — the daemon wedges for good if `startService` was missed | targeted |
| Pause auto-stop | Paused past the configured delay, the sender lets go of Milō (the daemon is restarted: no tisoc command ends a session), no banner | targeted |
| Daemon death | `kill -9` of the daemon mid-play: the card leaves "playing" at once with a banner | targeted |

### Bluetooth (C — active player)

Two feeds, and only one is guaranteed: BlueALSA answers *who is linked* and *whether its
stream flows*, BlueZ AVRCP answers *what is playing*. A sender may publish an empty track or no
player at all, so the card and the player are both expected screens — see
`sources/bluetooth/avrcp.py`. The phase is the player's `paused`, or `playing` with the stream
flowing; anything else is "connected" (a Mac publishes no state for its first 100 s).

| Check | Expected observable | Set |
|---|---|---|
| Pairing | Milō is discoverable and pairs from a phone | targeted |
| State | UI shows "Connected to [device name]" with a Disconnect button; "Ready" when idle | targeted |
| Audio | Playback from the phone is audible | targeted |
| Metadata | Title and artist appear and change with the track; transport buttons work. A sender publishing neither keeps the status card, which is correct | targeted |
| Cover | The slot fills within a few seconds — resolved from the track text, not carried by AVRCP — or stays on the source glyph. Never a broken image | targeted |
| Remote session | An iPhone controlling *another* device (a Mac, a HomePod) publishes the iOS card's two lines. The player must show title and artist, never "Now playing on …" | targeted |
| Disconnect | The button drops the link and the UI returns to "Ready". **Watch for a few seconds**: a second paired device takes the freed link on its own, which looks like a button that did nothing. `journalctl -u milo-backend | grep "Disconnect requested"` says which sender was asked | targeted |
| One sender at a time | While one device holds the link, the others are `Blocked` and Milō disappears from their output lists. Expected, not a fault | targeted |
| Output switched away | A Mac kept connected but switched to its own speakers: the session leaves `playing` at once (the stream stopped) for `connected`, and the card reads "Connected to [Mac name]"; back to `playing` when switched back. Never `playing` over silence | targeted |
| bluetoothd killed | `sudo systemctl kill -s KILL bluetooth.service` while playing: the session ends (the card reads ready) and the "stream interrupted" banner shows at once; the new bluetoothd is visible again by itself (`busctl get-property org.bluez /org/bluez/hci0 org.bluez.Adapter1 Discoverable` → true); the phone reconnects without reselecting the source and the banner goes | targeted |
| BlueALSA killed | `sudo systemctl kill -s KILL milo-bluealsa.service` while playing: the session ends (the card reads ready) and the banner shows at once; systemd restarts it and `milo-bluealsa-aplay` is active again (`systemctl is-active`); a reconnected phone is audible | targeted |
| Backend restart under a phone | `sudo systemctl restart milo-backend` with a phone connected: afterwards Milō is not discoverable and every audio peer is `Blocked` (the phone is dropped, not left connected and silent); no ERROR/WARNING from `source.bluetooth` | targeted |

### Mac / ROC (A) ⚠ Mac required

| Check | Expected observable | Set |
|---|---|---|
| Detection | Starting the ROC sender switches Milō to the Mac source on its own | targeted |
| State | UI shows "Connected to [Mac name]"; "Ready to stream" when idle | targeted |
| Audio | Continuous audio, no dropouts, at the configured latency profile | targeted |
| Left while off | A Mac that picked another output while Milō played another source is not shown when Mac is selected again (`session: null`, the card reads ready) | targeted |
| roc-recv killed | `kill -9` its MainPID mid-stream: the session ends (the card reads ready) and the banner shows at once; systemd restarts it ~5 s later and the Mac is back on screen by itself, banner gone | targeted |
| Apply mid-stream | With Mac the active source, change a "Reception (Milō)" value (the target latency) in the Mac settings and Apply — a "Sending (Mac)" value alone restarts nothing: roc-recv is active again afterwards (`systemctl is-active milo-mac`), and `GET /api/audio/state` reads `service: running` with no `service_error` and `switching: false`; the Mac's audio resumes. A `service: failed` here means the receiver did not come back — selecting Mac again is the retry | targeted |

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
| DAC flag round trip | With nothing playing, note the stored local level, then Settings > Hardware > "Volume managed by Milō" off and back on (`PATCH /api/volume/volume-control` `false`, then `true`), leaving it off for a few seconds only — off pins CamillaDSP at 0 dB, full scale on an amplifier card. After the "on", the daemon's own `GetVolume` (ws `127.0.0.1:1234`) reads the stored level, never `0.0`, and the next knob step moves it by one step. The router decides unity or level from this flag when each command leaves, so a flag moved after the level is re-applied leaves the room at full scale while every screen shows the stored level | targeted |
| DAC mute | With nothing playing in any room, mute the server's room **first**, then turn "Volume managed by Milō" off (it pins CamillaDSP at 0 dB). The daemon's `GetMute` (ws `127.0.0.1:1234`) reads `true`, equal to `global_mute` of `GET /api/volume/state`, and still does after `sudo systemctl restart milo-camilladsp`: a mute is not a level, so no DAC pin (the flip, the reconnect, the boot) unmutes. Then, muted in multiroom, turn multiroom off: `GetMute` and `global_mute` both read `false` — the way to direct unmutes the only speaker left, on the daemon as on the screens. That step opens a DSP pinned at 0 dB, so do it last and turn volume control back on right after it; otherwise turn it back on before unmuting | targeted |
| CamillaDSP restart unmutes last | With every room at ≤ -50 dB and radio playing on the main unit, `sudo systemctl restart milo-camilladsp` while polling the daemon's ws (`127.0.0.1:1234`) at ~20 Hz for `GetVolume`, `GetMute` and the capture and playback peaks. The backend's journal shows `Reconnected: restoring equalizer effects` (or `effects disabled, bypassing`) before `Re-applied volume after CamillaDSP reconnect`, and `GetMute` turns `false` only once the graph is back; the applied gain (playback peak minus capture peak, from the same sample) never exceeds the stored level plus the trim by more than 1 dB, and the final `GetVolume` equals the stored level. Then the same over `sudo systemctl restart milo-backend`, which restarts CamillaDSP with it (`PartOf=`): `Startup state applied` comes after the `Reconnected:` lines, never between them. An unmute ahead of the graph plays the room off its trim, or full range under a subwoofer, for as long as the restore takes; CamillaDSP's unmute ramp is what hides it | targeted |

## Equalizer

| Check | Expected observable | Set |
|---|---|---|
| Preset | Switching preset (e.g. Flat → Bass Boost) is **audible** immediately | smoke |
| Persistence | The preset survives a source switch and a reboot | smoke |
| Bands | Moving a single band is audible and the curve is reflected in the UI | targeted |
| Loudness | Enabling loudness at low volume audibly lifts bass/treble | targeted |
| Compressor | Enabling it audibly narrows the loud/quiet gap | targeted |
| Mono across a reconnect | With nothing playing, `PUT /api/equalizer/target/local/mono` `{"enabled": true}`: the daemon's `GetConfigJson` (ws `127.0.0.1:1234`) names its first `Mixer` step `mono`, and still does once `sudo systemctl restart milo-camilladsp` has logged `Reconnected:`. Then `{"enabled": false}`: `stereo`, before and after another restart. Leave it on stereo. The record, the screen and the daemon must agree in both directions — a room left summed under a stereo screen is reproducible only from the daemon's graph | targeted |
| Bypass | Effects toggle off/on without a gap or click in the audio | targeted |
| Per-client EQ ⚠ | A remote client's EQ applies to that client only; a zone shows no EQ of its own | targeted |
| Curve while loading | Switching target, re-enabling after a bypass, or reopening the page must **never** show a flat curve on the way to the real one — until the record lands the figures read "—" and the sliders are hidden. A flat curve is indistinguishable from a real `flat` preset, and this is a race, so repeat it ~5× before calling it clean | targeted |
| Master bypass in a zone ⚠ | Turning the **dock's** Equalizer app off while the appliance is a zone member must silence the effects on **every** member, satellites included — not only the local DAC. The flag lives in two domains (settings.json locally, the per-client record remotely), so a local-only write leaves satellites audibly out of step with nothing to repair it. Check the satellite itself (`GET :8001/equalizer/status` → `equalizer_enabled`), not just what the page reports | targeted |

## Multiroom ⚠ second unit

| Check | Expected observable | Set |
|---|---|---|
| Enable | The main switch activates within a few seconds; audio continues | targeted |
| Boot with multiroom already on | After a reboot, each satellite comes back **at its own stored level**, not at the local one — the boot-time push (`_delayed_multiroom_sync`) waits on snapserver answering, then re-sends. Nothing in CI executes its body: the suite only ever reaches its give-up branch | targeted |
| Satellite startup floor ⚠ | Restart a satellite's CamillaDSP alone (`sat restart <t> dsp`) and push `PUT :8001/equalizer/mute {"muted": false}` within a second: it must answer **400**, and the daemon's own `GetVolume` must read the unit's `--gain` floor, muted. A 200 here is a speaker that opens at whatever the fader happens to hold — measured 2026-09-20, 3 s at 0 dB in a room set to -52.8 dB. Run it on a silent stream: the floor sits inside this fleet's listening range, it is not inaudible | targeted |
| Satellite request gate | After pushing the app to a satellite, from the server: `curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Origin: https://evil.example' http://<satellite-ip>:8001/definitely-not-a-route` answers **403** (a 404 means the gate is missing; the route does not exist, so the request does nothing either way), and `curl … -H 'Host: rebind.example' http://<satellite-ip>:8001/health` answers 403 while the same request without the header answers 200. Then check that the server's own traffic still gets through: a volume step and an EQ band set from the kiosk reach that room, Settings > Updates shows the satellite's version, and `sat logs <t> app 200` holds no ` 403` access line. A gate that is too strict cuts the server off the satellite, update push included, and can then only be undone over SSH | targeted |
| Satellite app crash | With nothing playing to that room, kill the satellite app outright (`systemctl show -p MainPID milo-client`, then `kill -KILL <pid>` as `milo-client`). `sat journal <t> --since <T> -u app -u dsp -u snap` shows the `Scheduled restart job` for `milo-client`, then `Started` for both `milo-client-camilladsp` and `milo-client-snapclient`; `sat status <t>` shows all three active, and within ~30 s the satellite is back online in the UI at its stored level. Both units are `BindsTo=` the app, so a crash stops them and only the app's own start brings them back: without it the room stays mute and offline, out of reach of an update push, until a reboot. If they are not back within 60 s, `sat restart <t> dsp` and `sat restart <t> snap` | targeted |
| Satellite state read-back | After pushing the app to a satellite whose compressor was never set (the shipped config has no `processors` section, which CamillaDSP returns as null): `sat logs <t> app 200` shows `Connected to CamillaDSP` and no `Could not load state from config` after it, and the satellite's `GET :8001/equalizer/status` reports the `loudness`, `mono` and `equalizer_enabled` its record on the server holds. A read-back that stops early leaves those caches at their defaults while the daemon keeps the stored config, so the next bare enable drops the EQ headroom and the boosted bands clip | targeted |
| Satellite first connect | After pushing the app to a satellite with nothing playing to that room (note the time T): `sat journal <t> --since <T> -u app -u dsp` shows `Connected to CamillaDSP` less than 1 s after the daemon's own `CamillaDSP version` line, and the server's `/var/lib/milo/errors.log` since T holds at most one `SYNC_RECONNECT: <mac> hardware apply returned False` for that satellite and no `(attempt 2/6)`. The DSP unit is ordered After= the app, so the app's first connect is refused by construction; retried after the full 5 s backoff, the room stayed muted at its floor for ~4.8 s and the server's first two admission syncs were answered 400 | targeted |
| Satellite levels poll | After pushing the app to a satellite, open the Equalizer on that speaker (or a zone holding it) for ~10 s with something playing to it at a low level: the meters move, `curl -s http://<satellite-ip>:8001/equalizer/levels` from the server carries `output_peak` and no `input_peak`, and `sat logs <t> app 100` holds no `GET /equalizer/levels` access line. The server polls that route at 10 Hz while the modal targets the satellite and for 15 s after, so one access line per poll filled the satellite's only log surface in seconds (143 lines in 15 s, measured) and buried the request being investigated | targeted |
| Satellite boots on the cable ⚠ | On a wired satellite that also holds the server's WiFi standby (`nmcli connection show` lists it; `systemctl is-enabled NetworkManager-wait-online` answers `masked` on an image-flashed unit), cut the power and bring it back, three times. After each boot, `sat journal <t> -u snap | grep 'My MAC'` names the unit's **eth0** MAC (`/sys/class/net/eth0/address`), never wlan0's, and `ss -tn '( sport = :1704 )'` on the server shows its wired address; the launcher logs `NetworkManager startup complete after <n> s` (how long it held snapclient), and the unit's `NetworkManager` journal shows `manager: startup complete` after eth0's `Activation: successful`. The image masks wait-online, so `network-online.target` orders nothing: before the launcher waited, a satellite's snapclient connected from its WiFi address and kept it for 5 h 20 (measured 2026-10-17), starting the second eth0's carrier came up. The launcher waits for NetworkManager's startup (`nm-online -s`, capped at 60 s like wait-online), not for one read of eth0's state, because a cable still negotiating reads "no carrier" (20) for seconds like a missing one (3 s on the server's boot of 2026-10-29). Without a cable, the boot waits at most NetworkManager's carrier wait (6 s) while wlan0 comes up; an app restart or a push does not wait at all. Remaining limit: a PHY slower than that carrier wait is missed, as it was by wait-online | targeted |
| Source switch | In multiroom with every room muted, switch Radio → Podcast → Music Library → Radio and back. Right after each selection, `Server.GetStatus` (`POST http://localhost:1780/jsonrpc`) lists every group's `stream_id` as the new source's stream, and the backend journal shows `SNAPCAST_SET_STREAM: group=… -> <stream>` at that moment. Taking t0 as the new source's Loopback playback substream turning `RUNNING` in `/proc/asound`, CamillaDSP's `GetCaptureSignalPeak` (taken before volume and mute) reads non-zero within the snapclient buffer + 700 ms of t0 on the main unit and on each satellite. Before, the rooms stayed on the previous source's stream until it had been silent for `idle_threshold`: 3 to 5 s of the new source lost everywhere while the screen showed it playing | targeted |
| Sync | Two speakers playing the same track show no audible echo or drift over ≥5 min | targeted |
| Client volume | The per-speaker slider and mute affect only that speaker | targeted |
| Client delay | With nothing playing, note an online satellite's `delay_ms` (`GET /api/multiroom/clients/<mac>`), then `PATCH /api/multiroom/clients/<mac>/delay` `{"delay_ms": 40}`: it answers 200, and snapserver's own `Server.GetStatus` (`POST http://localhost:1780/jsonrpc`) lists that client's `config.latency` at 40, as `sat sync <t>` does. Put the noted value back the same way. A delay snapserver refuses answers 502 and records nothing, so the edit panel's slider returns to the old value — a recorded delay snapserver never took leaves the room out of step under a screen that shows it aligned | targeted |
| Zone | A zone groups speakers; its global volume and mute act on all members | targeted |
| Pending client | A freshly installed client appears under "Pending speakers" without a reload | targeted |
| Pairing reboot | Finishing the wizard on a new client **actually reboots it** (it drops off the network, then returns with the chosen card working). The server only warns on a failed reboot, so a wizard that reports success while the satellite never restarted is the silent failure to watch for | targeted |
| Crossover | With a subwoofer online in a zone, the badge shows the frequency and the mains lose their bass; taking the sub offline restores full-range | targeted |
| Level trim | A trim in Settings > Multiroom > a speaker changes that speaker's audible level **without moving its volume slider** — on a satellite and on the main unit alike. The other speakers do not move | targeted |
| Trim under bypass | With a trim set, switching the equalizer's master toggle off leaves the balance untouched. It is not an effect, and nothing in CI can hear that it was stripped | targeted |
| Trim and crossover after a CamillaDSP update | Update CamillaDSP from Settings > Updates (it stops and starts `milo-camilladsp` alone, so the daemon reloads a pristine graph): the main unit's trim **and** its highpass must both come back. Before the reconnect restore existed, a speaker returned full range under a subwoofer still playing bass, silently | targeted |
| Trim leaving multiroom | Turning multiroom off clears the main unit's trim, so the only speaker left plays at its nominal level; turning it back on re-applies it on admission | targeted |
| Disable | Turning multiroom off returns to direct mode with audio intact (ALSA device switch) | targeted |
| Offline client | Unplugging a client leaves the others playing and marks it offline | targeted |
| Deleting a speaker mid-admission | With the main room muted and nothing playing, take a satellite whose snapclient is connected but whose app refuses, so its admission keeps retrying (the server's journal shows `SYNC_RECONNECT: <mac> retrying in`). A fake one works: `snapclient --hostID 02:00:00:00:00:a7 -h <server LAN IPv4>`, never 127.0.0.1, which the server keys as its own MAC whatever `--hostID` says. Delete it during a retry wait: for 30 s the server's CamillaDSP `GetVolume` and `GetMute` do not move, the journal shows `removed during admission, abandoning` and no further attempt, and once the snapclient is stopped `settings.json` holds the mac in neither `multiroom.clients` nor `multiroom.client_equalizer`. Then in direct mode, a volume step from the UI and from the knob still moves the main room. Before, the retry after the delete sent the satellite's startup level and an unmute to the server's own CamillaDSP: the muted main room came back unmuted while every screen showed it muted | targeted |
| Muting during an admission | With nothing playing to that room, note a satellite's mute and level, follow snapserver's notifications on `127.0.0.1:1705`, restart the satellite app (`sat restart <t> app`), and on its `Client.OnConnect` mute its zone (or the client) through the API at once. Once the server's journal shows `Client <mac> online status: True`, the satellite's CamillaDSP `GetMute` reads true and `GET /api/volume/state` shows it muted. The mute must have landed before that line (compare its timestamp with the request's): if it did not, the run is inconclusive, never a pass. Before, the admission had already pushed the old mute and the write was only stored, so the room played unmuted while every screen showed it muted. Restore the mute and the level afterwards | targeted |
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
| Automatic full-screen player | With the setting on, a browser source (radio, podcast, music library, Spotify) **playing** and the screen untouched for its delay opens its full player; a touch before the delay counts it again from zero. Never while paused, never over open lyrics, and a track change does not restart the count (an album with a delay longer than one track still opens it); never on CD/TIDAL/AirPlay/Bluetooth/Qobuz/Mac. A pause leaves the player on screen; a tap on its cover returns to the same page and scroll of the navigation, and the player opens again after another delay if still playing | targeted |
| Scale | Small / Normal / Large change the layout without clipping | targeted |
| Kiosk memory | `systemctl status milo-kiosk` shows no recent restart loop after ~1 h of use (past OOM regression) | targeted |

## Network and setup ⚠ blank SD

| Check | Expected observable | Set |
|---|---|---|
| Wi-Fi list | Settings > WiFi lists known and nearby networks; connect and forget work | targeted |
| Wi-Fi country | Changing it in Settings > Network applies **without a reboot**: `iw reg get` follows immediately, and a refresh shows the channels the new domain allows. The apply-and-reboot sequence that used to wrap this control is gone — `milo-set-wifi-country` runs `iw reg set` there and then | targeted |
| Recovery access point ⚠ | With no link at all (cable out, no Wi-Fi profile that associates), the open `Milō` AP comes up ~45 s later (~75 s with a Wi-Fi profile saved, which is given 30 s to autoconnect first) and `milo.local` resolves over it; plugging the cable back in takes it down again. This is what a wrong Wi-Fi password at first boot lands on — the case that used to leave a headless unit unreachable for good | targeted |
| … with the Wi-Fi radio switched off | Same test, but turn Wi-Fi **off** in Settings > Network first. The radio must come back on by itself (`WiFi radio is off and the unit has no link` in the journal) before the AP is raised — measured 2026-09-04, NM refuses the activation otherwise with *No suitable device found … device eth0 not available*, which names the wrong interface and reads like a profile bug. With a saved network in range the radio joins it instead (`connecting` in `GET /api/network/status`, then connected) and no AP is raised; it stays on while it carries the link, lease pending included. With the cable back, the radio returns to **off** — unless the user switched it on or picked a network in the meantime, which ends the override | targeted |
| … across a backend restart ⚠ | Ethernet only, no Wi-Fi profile: pull the cable for more than 45 s (the `Milō` AP comes up), `sudo systemctl restart milo-backend`, then plug the cable back. Within 10 s `nmcli -t -f NAME connection show --active` no longer lists `Milō` and `curl -s http://localhost/api/initial-state` answers `"hotspot_active": false` (the journal says `adopted` at the restart). Then the radio variant: Wi-Fi **off** in Settings > Network, cable out 60 s (the radio comes back on for the AP), restart the backend, plug the cable back: `nmcli radio wifi` returns to `disabled`. Both effects outlive the process that made them — NM keeps the AP up and the radio on — so without the read-back at startup the new process owned neither and left them for good | targeted |
| … on a Wi-Fi-only unit ⚠ | Cable out, a station profile saved and joined. Cut the box's Wi-Fi for more than 90 s: the `Milō` AP comes up (the journal says `access point 'Milō' raised`). Bring the box back: within 6 min `nmcli -t -f NAME,DEVICE connection show --active` shows the station profile on `wlan0`, `Milō` is gone and `milo.local` answers again (`Home network '…' joined again`). The AP holds the only radio and NM never autoconnects over an active profile, so before the retry the unit stayed on its own hotspot until a reboot. While the box is still away, each try every 5 min drops the AP for about 30 s at most and raises it again (`still out of reach`); while a phone has the setup page open over it, a try is held off (`Home network not tried: a client is using the access point`), twice in a row at most | targeted |
| … and what it may not open ⚠ | Ethernet only, no Wi-Fi profile, setup done. Read SSH's state first, `curl -s http://localhost/api/system/ssh` (`enabled`, as V), and send V back below, never a change: a gate that failed would otherwise toggle SSH. Pull the cable for more than 45 s, join a phone or a laptop to `Milō`, then `curl -s -o /dev/null -w '%{http_code}' -X PUT -H 'Content-Type: application/json' -d '{"enabled":V}' http://10.42.0.1/api/system/ssh` → 403, and again with `-H 'X-Real-IP: 127.0.0.1' -H 'X-Forwarded-For: 127.0.0.1'` → still 403 (journal: `a client of the recovery access point`). The same request from the kiosk (`http://localhost`) → 200. `POST /api/system/password` and `/reset-setup` share the gate. Plug the cable back (the AP goes), and forget `Milō` on the laptop so it does not rejoin it at the next outage. The AP is open, so before the gate anyone in range could open SSH and set the `milo` password, sudo's, and both outlived the outage | targeted |
| Wi-Fi back after the cable | Cable out with Wi-Fi off, then Wi-Fi turned on: `milo.local` must resolve on the Wi-Fi address within a few seconds of its DHCP lease. Measured 2026-10-03: NM fires `up` before the lease, and with only `up` heard the name stayed published nowhere | targeted |
| Access point in domain 00 ⚠ | Blank SD: the AP must actually come up with `cfg80211.ieee80211_regdom=00` (2.4 GHz, channel 6) before any country has been chosen. It is the only thing between a stranger and the wizard | targeted |
| Captive portal ⚠ | Join the `Milō` AP **from an iPhone** and let the captive assistant open by itself. It must land on `milo.local` — the address bar says so — and the page must have real data: the language list, the WiFi scan, the country selector. Measured 2026-09-04: without the probe redirect it ran under `captive.apple.com`, nginx served the app, and every `/api` call was refused 403 by the request gate, leaving the wizard on screen with nothing in it and a Finish button that 403s. Android and Windows probe their own URLs and take the same redirect | targeted |
| Status | Connected SSID, signal and IP shown and correct | targeted |
| Internet verdict vs NetworkManager ⚠ | Start a webradio, pull the **ethernet cable**, wait ~40 s, plug it back. The source card must say *No internet access* within ~5 s and keep saying it for the whole outage, then clear within ~5 s of the cable returning. Cross-check in the journal that `nmcli -g CONNECTIVITY general` still answers **`full`** throughout: NM's probe is address-family agnostic and wlan0 carries its own IPv6 default (`proto ra`, metric 600), so NM never notices. Measured 2026-09-20: without the IPv4 confirmation the appliance reported a working internet for 43 s of total outage, and during the 14:29 incident it did so for 18 minutes while every podcast feed logged `Connect call failed` | targeted |
| … the grey failure, link up and gateway dead ⚠ | The faithful repro of the 2026-09-20 incident, which pulling the cable does **not** reproduce — carrier and DHCP lease both stay valid: `sudo ip neigh replace <gateway> dev eth0 lladdr 00:00:00:00:00:01`, undo with `sudo ip neigh del <gateway> dev eth0`. **Nothing signals here**: NM logged not one line in 100 s and not one in 7 minutes, because its own probe keeps succeeding over IPv6 and it signals only on change. The verdict can only come from Milō's own probe loop, and a blackholed path answers it with silence (TimeoutError), not with a refusal — so it takes three strikes at 60 s to conclude. Measured 2026-09-20: blackhole 17:47:10, `Connectivity (IPv4 silent x3): full → limited (NM=4)` at **17:49:36** with the card following 11 s later; undo 17:57:35, `Connectivity (IPv4 recovered)` at **17:58:12**, lifted by the loop alone with no backend restart. Allow **12 minutes** for the window and expect ~2.5 min to detect, ≤2 min to clear. `nmcli -g CONNECTIVITY general` must answer `full` the whole way through — that divergence is the feature | targeted |
| … no IPv4 failover exists ⚠ | Known gap, measured 2026-09-20, not a regression: with the cable out the unit has **no IPv4 default route at all** for the whole outage. NM elects wlan0 (`policy: set … (wlan0) as default for IPv4`) but installs nothing, because wlan0's applied config has no gateway — both interfaces sit on one subnet behind one gateway and NM installs that subnet once. IPv6 does fail over, which is what makes the unit look healthy. avahi does follow to wlan0 in ~5 s, so `milo.local` still resolves. Fixing this means active-backup bonding with ARP monitoring; nothing in the tree attempts it yet | targeted |
| mDNS | `http://milo.local` resolves from another machine on the LAN | targeted |
| WiFi standby route ⚠ | `ip -4 route show dev wlan0` must contain `default via <gateway> … metric 600` alongside eth0's metric 100. That second default is the *entire* ethernet→WiFi failover: with it, pulling the cable moves the route in ~7 s with no script and playback continues (measured 2026-09-20, 18:39:46 → 18:39:53); without it the unit has **no** IPv4 default route at all for the whole outage. It was missing once, on the morning of 2026-09-20, and three boot scenarios failed to reproduce that — so check it rather than assume it. `nmcli connection up <wifi-profile>` restores it; `nmcli device reapply` reports success and does nothing | targeted |
| First boot ⚠ | Blank SD: the open "Milō" AP appears, the captive portal opens, the wizard completes and reboots into a working unit | targeted |
| Hostname conflict | Two units on the LAN do not silently share a name | targeted |
| First-boot splash ⚠ | Blank SD, **no ethernet**: `milo-first-boot` spends its full carrier + DHCP + mDNS budget, so the bar creeps on its own for far longer than on a warm unit. It must keep advancing, ever more slowly, and never park — that creep is the only thing on screen for up to two minutes. The journal is not enough here; this one needs eyes | targeted |
| Role auto-detection ⚠ | Blank SD, ethernet, with a Milō server already answering on `milo.local`: the unit detects it, converts itself and reboots as a satellite. `avahi-daemon` must be **active**, not 203/EXEC — the conversion installs `milo-client-apply-avahi-iface`, and its absence is silent everywhere but there | targeted |
| Converted satellite's grants | After that conversion, on the unit: `/etc/sudoers.d/milo-client` matches the repo and carries its `PASSWD: ALL` rule *above* the grants. Without it the argument-scoped policy is decorative and the satellite quietly holds the image's blanket NOPASSWD | targeted |

## Settings, updates, system

| Check | Expected observable | Set |
|---|---|---|
| Persistence | Any changed setting survives `sudo systemctl restart milo-backend` **and** a reboot | smoke |
| Remote access | Settings > Device > System: the SSH switch is inert while the notice about the password is shown; setting a password clears it; the switch then starts `ssh.service` and `ssh milo@milo.local` accepts the new password. `sudo` on the unit accepts it too | targeted |
| Time zone | A phone opening `milo.local` on a unit still at `Etc/UTC` sets the zone (`timedatectl` follows); the Language and region page changes it by hand and the change survives a reboot | targeted |
| Audio card missing ⚠ | With a card configured that is not fitted, the banner names it at boot and points at the Hardware page. Needs a unit whose HAT can be removed | targeted |
| Reset configuration | Settings > Device > System > Reset: the unit reboots, re-runs role detection and shows the wizard; radio favourites, podcast subscriptions and shares are still there afterwards | targeted |
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
