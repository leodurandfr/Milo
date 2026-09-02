# Physical verification — what only a human in the room can check

Everything the cleanup programme changed that **no session, no test and no probe can reach**.
It exists because eight cleanup passes each ended with *"on-Pi smoke not run"*, and a ninth
(phase 5) then verified everything a machine could — 44 scenarios, 8 live bugs found and fixed
— leaving exactly this.

**How to run it:** start a fresh conversation with

> **"exécute docs/manual/physical-verification.md"**

and be in the room. Do one group per sitting; groups are independent, order inside a group is
not. This file is a **backlog, not a checklist**: strike what passes, and when a group is empty
delete it. When the file is empty, delete the file — anything worth keeping goes to
[verification-checklist.md](verification-checklist.md) instead, which is the standing one.

> **Why this is not verification-checklist.md.** That file is the *standing* regression net,
> run on every change that touches audio or hardware. This one is a **one-time debt**: each
> line verifies a specific change this programme made and will never need running again once
> it passes. A scenario that *fails* here has earned its place in the standing checklist —
> move it there, do not leave it here.

---

## Pre-flight — five minutes, and it has failed in 5 sessions out of 6

Do not skip it. Two of these have failed with a **silent pass**, which is worse than failing.

```bash
cd /home/milo/milo

# P1 — is the backend running the code you are about to judge?
#      Never redirect stderr here: a probe whose error message is hidden reads as a pass.
find backend -name '*.py' -newer /proc/$(systemctl show milo-backend -p MainPID --value) -print
#   → prints FILES, not a verdict. A hit only matters if the backend imports that file
#     (tests/ and tools/ do not). Any real hit → sudo systemctl restart milo-backend

# P2 — does dist/ match HEAD? Re-check before EACH block, not only at the start:
#      another session can rebuild under you.
git log -1 --format=%cd --date=iso -- frontend/src && stat -c '%y' frontend/dist/index.html
#   → dist older than the newest src commit → cd frontend && npm run build

# P3 — suites green
venv/bin/python -m pytest backend/ -q | tail -1
cd frontend && npm run test:run 2>&1 | tail -3; cd ..

# P4 — are the satellites running HEAD? A fleet lag is invisible to CI: both sides ship in
#      one commit, so the contract test passes while the deployed satellite diverges.
#      Run from the repo root — the shell's cwd persists between commands, and a wrong cwd
#      makes the pathspec match nothing and print nothing, which reads as "up to date".
git log --oneline -3 -- milo-client/          # control: this MUST print something
#   then compare each satellite's version; force with
#   POST /api/programs/satellites/{mac}/update-app

# P5 — clean tree
git status --porcelain
```

**Known state, so you do not chase it:** `ANTICATER_MINI` (the BT remote) reconnects in a loop
— see [F3](#f3--the-bt-remote-reconnects-in-a-loop) — and a disc (*The Best of Sade*) is
already in the drive, so the CD group needs no hardware brought in **except F-CD7**, which needs
a disc no lookup identifies and is the reason that group is not self-contained.

---

## Group 1 — you and the unit's screen · *nothing else* · ~15 min

The standing checklist's smoke set. **It has never been run end-to-end**, which is phase 0's
own open item — running it also replaces its estimated ~10-minute budget with a measured one.

| # | Observable |
|---|---|
| A1 | Kiosk shows the UI and the dock — no blank page, no Vite banner |
| A2 | Radio: the station logo, name and bitrate are visible; play/pause responds |
| A4 | Volume: the rotary knob **and** the on-screen +/− both move the level and the audible output |
| A5 | Equalizer: a preset change (Flat → Bass Boost) is **audible immediately** and survives a source switch |
| A6 | Source switch radio → spotify → radio: the old source stops within ~1 s, no ghost audio, no ghost metadata |
| A7 | Screen: the sleep delay turns it off, touch wakes it to the same view, brightness applies immediately |
| A8 | Background the tab ≥60 s, return: volume, active source, metadata and multiroom state all match reality |

**A8 is worth more than usual:** phase 2 rewrote the boot path so `resyncStores()` is the only
recipe. Watch the backend's request log while it runs — the claim is a *strict superset* of the
old requests with **no refetch storm**.

Also here, because they need the panel and nothing else:

| # | Observable |
|---|---|
| C6 | Turn **Écran**, **Ventilateur** and **Multiroom** off in turn → the matching tile disappears and the remaining sections keep their spacing. (The Sources rows and the spacing half are already ✅; these three gates are not cheap — multiroom stops snapcast, the other two are hardware-derived) |
| ~~D1~~ | **No longer an eye call — measured, and it fails.** The warm filter is not a CSS `filter`, it is a plain DOM overlay (`.color-filter-overlay`, `rgba(255,119,0,0.286)`), so a headless render reproduces it exactly. `.fan-warning` carries `text-mono-medium`, so its WCAG floor is **4.5:1**; composited through the overlay it measures **1.57–1.72:1** depending on the section background — and phase 4's move from `--color-brand` to `--color-warning` made it **worse** (brand: 1.97–2.16). Phase 4's reasoning was semantic and right on its own terms; nobody had measured contrast. **This now needs a colour decision, not a look.** See *Design decisions* below |
| D3 | The virtual keyboard's press popup vs accent popup — they differ **only in alpha** (.15 vs .20), same offset and blur. Do they read as two depths, or should one move? **A design call, not a bug** |
| D7 | Podcasts → the genre grid and a podcast card shrink on tap. The machinery is proved (held → `pressed`, 40 px move → cleared); only **how it looks on glass** is open |
| E | The virtual keyboard's accent popup — how it *looks*. Its behaviour is fully verified |

---

## Group 2 — something that can be rebooted · *and someone to watch it*

**Structural constraint, found the hard way:** an agent session runs *on the unit*, so a reboot
kills the observer. These need you, or a session started *after* you reboot.

| # | Observable |
|---|---|
| C9 | **Cold boot** → every settings panel shows its **stored** value, not a default |
| C10 | A cold boot with a configured satellite → speakers, zones, volumes and EQ all present |
| C14 | A changed setting (rotary step, custom station) survives a **reboot** — the `systemctl restart` half is already ✅ |
| C7 | The power menu's **second tap** (the actual reboot) and the failed-request path. Arming and disarming are already ✅ |
| C15 | Set the fan to **manual at a distinctly audible speed** (say 80 %), restart the backend → the fan comes back **at that speed**, not at a default; then switch mode manual → target → auto from the UI and hear it follow each time |

C9 is the one that matters most now: phase 6 made `settings.json` gain a `mac` section and
changed how every default resolves. The restart path is verified; the cold-boot path is not.

C15 is phase 7's: `FanController._load_config_from_settings` stopped re-validating what
`SettingsService` already resolved, and it runs **only at controller init**, so a backend
restart is the trigger. Proved equivalent over 233 validator outputs including this unit's real
`settings.json` (0 differing) and guarded by `tests/test_fan_controller.py`, but it is a thermal
path and no CI test hears a fan. `reload_config` — the `PUT` route's partial-payload path — was
deliberately not touched, which is what the second half of the row checks.

---

## Group 3 — a satellite · ⚠ *second unit*

| # | Observable |
|---|---|
| B12 | Change a client's speaker type to **subwoofer** in a zone → the crossover recalculates. **Needs ears**: the satellite exposes `crossover`/`lowpass` as PUT-only, with no `GET` and nothing in `/equalizer/status` |
| B13 | A zone **crossover** change still applies — the DSP path, and the only backend change phase 1c made. **Needs ears**, same blind spot |
| B17 | A snapclient buffer change and the client **still plays**. Everything else about B17 is ✅, including 4 rejected values → HTTP 502 with the stored value untouched |

**Run these last in this group — they destroy the pairing.** Re-pairing goes through the
wizard, which writes the audio overlay and reboots the satellite, so budget for it:

| # | Observable |
|---|---|
| B8 | Select a satellite in the EQ page, close it, **forget that client** from Réglages, reopen → the page lands on Milō and accepts writes, **no 404** |
| B9 | A **two-client zone**, forget one member → the survivor's EQ still accepts a write and is **not** shown as zoned |
| B10 | Same, then **reboot** → the survivor is still standalone |

> **There is no shell on the satellite** — no SSH key, and password auth is refused for
> `milo` / `milo-client` / `pi`. Its HTTP surface on `:8001` is the whole of what can be
> observed remotely, which is why B12/B13 need ears rather than a probe.

---

## Group 4 — a real power cut · ⚠ *two units*

| # | Observable |
|---|---|
| B19 | **Cut power to both units and restore them together**, so the satellite's snapclient reaches snapserver before its API is up → the speaker comes back **audible**, not muted-but-online |
| B20 | Configure a satellite in the wizard and toggle multiroom **while it reboots** → it comes back under the chosen name, not "Milō Client" |

**B19 is the highest-value scenario in this file.** It is the only path for a client that
connected before the backend did — i.e. **every satellite after a power cut** — and the bug it
covers left the speaker silent while shown online with working controls, unretryably. Nothing
but a real power cut reproduces it.

---

## Group 5 — the CD drive · *a disc is already in it*

| # | Observable |
|---|---|
| F-CD1 | Open CD with a disc and, **without pressing play**, drag the progress bar → nothing becomes audible and the bar sits at the target |
| F-CD2 | Pause mid-track, drag the bar, then press play → playback resumes at the **dragged** position |
| F-CD3 | Seek during normal playback → unchanged, audio continues |
| F-CD4 | Let the **auto-stop** timeout fire, then press play → the disc resumes the same track at the same position |
| F-CD5 | Insert a disc → it becomes playable (the TOC latch now retries 3×; a disc that used to stick on the spinner should not) |
| F-CD6 | Transport: play/pause, next; **previous** early in a track goes back, late in a track restarts it. Eject releases the disc and leaves the source cleanly |
| F-CD7 | ⚠ **needs a disc no lookup identifies** — a burned CD or an obscure pressing. The player's big title reads the track (`Track 1`, then `Track 2` on skip) instead of "Unknown Title", with "Unknown Artist" underneath and the disc placeholder as the cover |

**F-CD7 is the one line in this group that the disc already in the drive cannot answer.** *The
Best of Sade* is identified, as are all three discs in `cd_data.json`, so it renders the path
that already worked. The scenario covers `02ee1526`, which accepted a title-only metadata
snapshot for `cd` alone: before it, an unidentified disc left the player's cache on its empty
seed, so the title never changed between tracks. "Unknown Artist" is **correct** here and is not
what the fix is about — the artist genuinely is unknown, and `CDSource.vue` shows the same in its
header. The owner waived this check on 2026-08-17 rather than hold the plan for it, so the fix
ships unobserved: if the CD player ever shows a wrong title, this is the first suspect.

---

## Group 6 — the BT remote · ⚠ *ANTICATER_MINI*

**Start with F3 below.** The remote already flaps, so any result in this group taken before F3
is understood may be measuring the loop rather than the change.

| # | Observable |
|---|---|
| F-BT1 | With the remote connected, disable the BT remote from Réglages **on the phone** → the kiosk's panel stops showing it connected, and vice-versa |
| F-BT2 | With the remote **asleep**, toggle the feature off then on → "Recherche" becomes clickable again instead of spinning forever, and pressing it still runs a discovery |
| F-BT3 | Enable while the remote is awake → it reconnects and the panel shows connected with a battery reading |
| F-BT4 | Press Recherche while the remote is **already connected** → the button settles rather than spins |
| F-BT5 | Let the remote sleep, wake it by turning the knob → volume still responds and the panel follows |
| F-BT6 | Over a day: battery drain unchanged. A **no-regression** check, not a validation |

### F3 — the BT remote reconnects in a loop

**Observed 2026-07-27, never investigated.** `backend.hardware.bt_remote` cycles *found →
monitoring → disconnected (`[Errno 19] No such device`)* for `ANTICATER_MINI`
(`f5:cc:0a:83:e2:89`) roughly **every 12 seconds, continuously** — 189 disconnects in 37 minutes
of an otherwise idle backend. It is `INFO`, so it raises no banner; it was only seen while
grepping for something else.

**It is visible in the UI too:** the Télécommandes list read « Connectée · 92 % » and, three
minutes later with nobody touching the remote, « Non connectée ».

Whether this is the remote sleeping (F-BT5 treats sleep as normal) or a real reconnect loop is
undecided — but a healthy sleep should not re-open the device every 12 s forever.

---

## Group 7 — a USB key and a NAS · ⚠ *USB, NAS*

| # | Observable |
|---|---|
| F-ML1 | Play an album, pause, let the **idle auto-stop** fire, switch to Spotify, come back → it restores the same track at the same position, paused (needs a non-zero auto-stop delay in Réglages) |
| F-ML2 | The same **without** the auto-stop (switch away while playing) → unchanged |
| F-ML3 | Plug a USB key in while **another source is playing** → it is still indexed and appears in the grid |
| F-ML4 | Add, edit and delete an **SMB share** from the wizard → mounts, remounts and unmounts, with the `mounted` badge correct |
| F-ML5 | Reboot with the NAS powered **off**, then power it on → the share reconnects within ~2 min with no manual re-save |

---

## Group 8 — a phone and the streaming accounts

| # | Observable | Needs |
|---|---|---|
| A3 | Spotify: Milō appears on the phone; handoff starts audio with artwork, title, artist; progress advances | phone |
| F-B1 | AirPlay: artwork, title and artist appear within ~2 s of a track change — the historical silent-failure point | ⚠ iPhone |
| F-B2 | DLNA: pushing a track starts audio with artwork, title, artist, album, and **no** transport buttons | — |
| F-B3 | Qobuz: playback with artwork and progress, no transport buttons; sign-in survives a reboot | ⚠ paid acct |
| F-PC1 | Podcast: pause / resume / speed change, and the speed persists across pause-resume | ⚠ PI creds |
| F-PC2 | Podcast: leaving mid-episode and returning resumes near where it stopped, **after a reboot too** | ⚠ PI creds |

---

## Group 9 — an update that can actually run · ⚠ *release*

| # | Observable |
|---|---|
| F-UP1 | Install an **older** go-librespot / CamillaDSP / Navidrome binary by hand, then update from the UI → the always-on pair comes back up (**CamillaDSP is in the live audio path**), go-librespot left stopped **stays stopped**, and a forced install failure **rolls back** |

The trick this needs: the flow only runs when an update is genuinely available, so the older
binary has to be installed first. This is phase 0's *release* blind spot.

---

## Design decisions — surfaced here, but decidable at a desk

None of these needs the unit. They are recorded here because the sweep that produced this file
is what found them, and because each one changes something you will then want to *look* at.

| # | Decision | Where it is recorded |
|---|---|---|
| ~~K1~~ | ✅ **decided 2026-07-28 — no second affordance.** The existing verb (shrink 4 px, fade to 60 %, hold 150 ms) is the app's whole press vocabulary, and the 15 surfaces that carry no feedback stay as they are. Press applies to the mouse too, so there is no `@media (hover: hover)` split to make — and the hover half of the question was answered by measurement: `:hover` exists **exactly once** in all of `src/`, on `.dismiss-btn`. The list is no longer a pending bucket: it is `NO_PRESS_BY_DECISION`, and adding to it is now a stated choice to ship a control that does not acknowledge a tap | [`pressFeedback.test.js`](../../frontend/tests/architecture/pressFeedback.test.js) |
| ~~K4~~ | ✅ **closed 2026-07-28 — accepted.** The warm overlay is a **user-tunable screen-temperature correction** for a panel that reads too blue, not a fixed design condition, so measuring a contrast ratio "through the filter" measures a setting rather than the design. The owner accepts the result. Recorded for whoever reads the numbers later: without the overlay `--color-warning` on the section background is still 2.01:1 against a `text-mono-medium` floor of 4.5:1, so this is an accepted deviation, not a passing measurement | [`FanSettings.vue:292`](../../frontend/src/components/settings/categories/FanSettings.vue#L292) |
| ~~K5~~ | ✅ **decided 2026-07-28 — left as it is.** The `-webkit-line-clamp` recipe is written 12 times across 7 files with its 3 core declarations byte-identical in 11 of them, i.e. **zero drift** — where every duplication this programme collapsed (`update.py`'s flows, the four EQ pushes, the settings defaults, the three `_persist_*`) had *already* produced a defect. The utility would have to be applied in templates, and 3 sites are `:deep()` rules styling slot content, so the clamp would stop being guaranteed by the component that owns it and become a class someone can forget with no possible guardrail. Recorded where someone would go to add it | [`design-system.css`](../../frontend/src/assets/styles/design-system.css), below the utility classes |
| **K3** | ✅ **closed 2026-07-28** — the four files phase 2 handed over (`AudioPlayer.vue`, `MultiroomItem.vue`, `Dock.vue`, `musicLibraryStore.js`) were swept mechanically and in a live prod render: 0 dead CSS class, 0 dead script symbol of 194, 0 unused prop of 15, 0 emit never emitted of 14, 82/82 store exports consumed, the 6 hex literals are mask channels correctly whitelisted per-rule, and the live render shows 0 horizontal overflow and 0 clipped text. K4 and K5 are what it found | — |

## What to do with a result

1. **Passes** → strike the row. Do **not** copy it into the standing checklist: it verifies a
   change this programme made, not a future regression.
2. **Fails** → that is a live bug. Fix it, and **add the scenario to
   [verification-checklist.md](verification-checklist.md)** — it has proven it catches
   something.
3. **Cannot be run** (hardware absent) → leave it and say so. An unrunnable check is not a
   passing one.

Two habits worth carrying over from the sessions that produced this file:

- **Make the baseline discriminating first.** Four false passes were caught this way: a
  scenario compared against a target that was *already* in the expected state cannot fail.
  Before running one, ask what would have to be true for it to fail — if the answer is
  "nothing", change the setup.
- **A probe was wrong before the code five times.** When something reads as a pass on the
  first try, check the probe reached what it claims to: wrong port, wrong path, wrong unit
  name, stderr redirected away.
