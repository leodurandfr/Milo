# Physical verification — what only a human in the room can check

Everything the cleanup programme changed that **no session, no test and no probe can reach**.
It exists because eight cleanup passes each ended with *"on-Pi smoke not run"*, and a ninth
(phase 5) then verified everything a machine could — 44 scenarios, 8 live bugs found and fixed
— leaving exactly this.

**How to run it:** start a fresh conversation with

> **"exécute docs/manual/physical-verification.md"**

and be in the room for the groups that need eyes, ears or hands. Everything else can be driven
from a Mac on the LAN: the server through `ssh milo`, each satellite through
`ssh milo ~/.local/bin/sat …` (including `sat exec`), and a reboot is observed from outside —
that is how the session of 2026-11-24 ran the reboot, satellite, library and streaming checks.
Do one group per sitting; groups are independent, order inside a group is not. This file is a
**backlog, not a checklist**: strike what passes, and when a group is empty delete it. When the file is empty, delete the file — anything worth keeping goes to
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

**Where it stands (2026-11-25, session C3):** groups 1, 3, 4, 7 and 9 ran on the units and
are struck; so is all of group 6 but its day-long line, all of group 5 but F-CD7, and F3 (the
remote's HID node is re-created by BlueZ a few times after a wake, with the link up and every
knob step taken; no continuous loop). What a result found went to
[verification-checklist.md](verification-checklist.md): the CD cover download (*Cover of a disc
named now*) and the name a re-configured speaker keeps (B20, *A re-configured speaker keeps the
name it is given*). Five lines are left, each waiting on something the session did not have.

---

## What is left

| # | Observable | Waiting on |
|---|---|---|
| F-CD7 | A disc **no lookup identifies** plays with the player's big title reading the track in the interface's language (`Morceau 1`, then `Morceau 2` on skip, in French) instead of "Unknown Title", "Unknown Artist" (« Artiste inconnu ») underneath and the disc placeholder as the cover. Covers `02ee1526`, which accepted a title-only metadata snapshot for `cd` alone | a disc MusicBrainz does not know: the burned CD tried on 2026-11-25 (St Germain, *Tourist*) was named in 4 s |
| F-BT6 | Over a day: the BT remote's battery drain unchanged. A **no-regression** check, not a validation | a day of ordinary use |
| F-UP1 (rollback half) | A **forced** install failure of go-librespot / CamillaDSP / Navidrome **rolls back** to the binary it replaced, and CamillaDSP comes back up. The other halves passed on 2026-11-25: an older go-librespot (0.10.6) and CamillaDSP (4.1.2) installed by hand, then updated through `POST /api/programs/<key>/update`; go-librespot, stopped, stayed stopped; CamillaDSP restarted, the backend reconnected and re-applied the volume, and a station was heard | a way to make one install fail on a unit without editing the code (the update flow's rollback is covered by its tests) |
| F-TD2 | Tidal's first activation **downloads its runtime**: on a server card flashed from an image that no longer carries `/opt/milo/tidal-connect`, enabling Tidal in the Dock shows it *unavailable: installing* while `milo-deploy-update install-tidal-runtime` runs, then a cast from the Tidal app plays within 5 min. On 2026-11-25 the activation ran on a unit whose runtime dated from 2026-08-04, so nothing was downloaded | the next fresh server card (the spare one used for the first-boot checks will do) |
| F-TD1 | Tidal: casting from the phone starts audio with artwork and progress, and Milō's own pause / next act on it. A free account (30 s previews) cast from the Mac on 2026-11-25: audio, artwork and title came, but the first cast was cut by the 8 s no-sound watchdog and the account cannot go past a preview | a paid Tidal account |

---

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
