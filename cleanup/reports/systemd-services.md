# Systemd Services Audit Report

> Generated 2026-03-26

---

## Summary

| Severity | Count | Description |
|----------|-------|-------------|
| **Medium** | 2 | Missing `Requires=sound.target` on audio services |
| **Low** | 2 | Missing Group directive, stale commented-out placeholder |
| **Info** | 3 | Hardcoded UID, hardcoded interface name, consistency observations |

All referenced repo scripts exist. External binaries (go-librespot, camilladsp, shairport-sync) are correctly not in the repo — installed at setup time.

---

## Issues Found

### milo-mac.service — Missing `Requires=sound.target`

**Severity: Medium**

Line 4: Lists `sound.target` in `After=` but does not `Requires=` it. The service can start before the audio subsystem is ready. Inconsistent with `milo-radio`, `milo-podcast`, and `milo-cd` which all have `Requires=sound.target`.

```ini
# Current
After=network.target sound.target milo-backend.service milo-camilladsp.service

# Should add
Requires=sound.target
```

---

### milo-airplay.service — Missing `Requires=sound.target`

**Severity: Medium**

Same issue as `milo-mac.service`. Lists `sound.target` in `After=` but never requires it.

```ini
# Current
After=network-online.target sound.target milo-backend.service milo-camilladsp.service nqptp.service
Requires=nqptp.service

# Should add
Requires=sound.target
```

---

### milo-bluealsa-aplay.service — Missing `Group=` directive

**Severity: Low**

Line 10: Only specifies `User=milo`, no `Group=`. All other audio-playing services specify either `Group=audio` or `Group=milo`. Works in practice (milo user is in audio group) but inconsistent.

---

### milo-backend.service — Stale commented-out placeholder

**Severity: Low**

Line 14: `#Environment="GITHUB_TOKEN=ADD_TOKEN_HERE"` — Dead configuration hint. Should be in docs or removed.

---

## Consistency Analysis

### `Requires=sound.target` Pattern

| Service | Has `Requires=sound.target` | Notes |
|---------|:---------------------------:|-------|
| milo-radio | Yes | mpv-based |
| milo-podcast | Yes | mpv-based |
| milo-cd | Yes | mpv-based |
| milo-camilladsp | Yes | Core DSP |
| milo-mac | **No** | roc-recv — should have it |
| milo-airplay | **No** | shairport-sync — should have it |
| milo-spotify | No | go-librespot — may be intentional (connects to Spotify first) |
| milo-bluealsa | No | Bluetooth service layer |
| milo-bluealsa-aplay | No | Bluetooth audio output |

### `BindsTo=milo-backend.service` Pattern

| Service | Has `BindsTo` | Has `PartOf` | Notes |
|---------|:------------:|:------------:|-------|
| milo-spotify | Yes | - | Stops with backend |
| milo-airplay | Yes | - | Stops with backend |
| milo-radio | Yes | - | Stops with backend |
| milo-podcast | Yes | - | Stops with backend |
| milo-cd | Yes | - | Stops with backend |
| milo-mac | Yes | - | Stops with backend |
| milo-bluealsa | Yes | - | Stops with backend |
| milo-bluealsa-aplay | Yes | - | Also BindsTo milo-bluealsa |
| milo-camilladsp | - | Yes | Weaker coupling (PartOf) |
| milo-snapclient-multiroom | - | Yes | Weaker coupling (PartOf) |
| milo-snapserver-multiroom | - | - | Independent |

### Group Specification

| Service | User | Group | Notes |
|---------|------|-------|-------|
| milo-backend | milo | milo | |
| milo-kiosk | milo | milo | |
| milo-radio | milo | milo | |
| milo-podcast | milo | milo | |
| milo-cd | milo | milo | |
| milo-spotify | milo | audio | |
| milo-mac | milo | audio | |
| milo-bluealsa | milo | audio | |
| milo-snapserver-multiroom | milo | audio | |
| milo-snapclient-multiroom | milo | audio | |
| milo-camilladsp | milo | milo | |
| milo-airplay | milo | milo | |
| milo-bluealsa-aplay | milo | **missing** | Should specify Group= |
| milo-disable-wifi-power-management | root | root | Intentional (needs iw privileges) |
| milo-readiness | root | root | Runs as root (Type=oneshot) |
| milo-first-boot | root | root | Runs as root (system setup) |

---

## Per-Service Detail (No Issues)

### milo-kiosk.service — OK

- ExecStart: `/usr/bin/cage -- /usr/bin/chromium ...` (system packages)
- After/Requires: `milo-readiness.service` (in repo), `seatd.service` (system)
- Note: Hardcodes UID 1000 in `RuntimeDirectory=user/1000` and `XDG_RUNTIME_DIR=/run/user/1000`

### milo-podcast.service — OK

- ExecStart: `/usr/bin/mpv ...` (system package)
- EnvironmentFile: `/var/lib/milo/routing.env` (generated)

### milo-backend.service — OK (aside from stale comment)

- ExecStart: `/home/milo/milo/venv/bin/python3 backend/main.py` (repo + venv)
- WorkingDirectory: `/home/milo/milo`

### milo-readiness.service — OK

- ExecStart: `/usr/local/bin/milo-wait-ready.sh` — exists in `rootfs/usr/local/bin/`

### milo-bluealsa.service — OK

- ExecStart: `/usr/bin/bluealsa -S -p a2dp-sink` (system package)

### milo-spotify.service — OK

- ExecStart: `/usr/local/bin/go-librespot` — third-party binary installed at setup

### milo-radio.service — OK

- ExecStart: `/usr/bin/mpv ...`

### milo-disable-wifi-power-management.service — OK

- ExecStart: `/sbin/iw dev wlan0 set power_save off`
- Note: Hardcodes `wlan0` interface name

### milo-snapserver-multiroom.service — OK

- ExecStart: `/usr/bin/snapserver -c /etc/snapserver.conf`

### milo-camilladsp.service — OK

- ExecStart: `/usr/local/bin/camilladsp ...` — third-party binary installed at setup

### milo-snapclient-multiroom.service — OK

- ExecStart: `/bin/sh -c '/usr/bin/snapclient ...'`

### milo-cd.service — OK

- ExecStart: `/usr/bin/mpv ...`

### milo-first-boot.service — OK

- ExecStart: `/usr/local/bin/milo-first-boot` — exists in `rootfs/usr/local/bin/`
- ConditionPathExists: `!/var/lib/milo/.mode-configured` (only runs on fresh install)

---

## Client Services

### milo-client.service — OK

- ExecStart: `/home/milo-client/venv/bin/python3 main.py`
- WorkingDirectory: `/home/milo-client/repo/milo-client/app`
- EnvironmentFile: `/var/lib/milo-client/env`

### milo-client-snapclient.service — OK

- ExecStart: `/bin/sh -c '... /usr/bin/snapclient ...'`
- Requires/BindsTo: `milo-client.service`

### milo-client-camilladsp.service — OK

- ExecStart: `/bin/sh -c '... /usr/local/bin/camilladsp ...'`
- Requires/BindsTo: `milo-client.service`

---

## External Binaries (Not in Repo — Expected)

These are third-party binaries downloaded/compiled during installation:

| Binary | Service | Installed by |
|--------|---------|-------------|
| `/usr/local/bin/go-librespot` | milo-spotify | install.sh (GitHub release) |
| `/usr/local/bin/camilladsp` | milo-camilladsp, milo-client-camilladsp | install.sh / install-client.sh (GitHub release) |
| `/usr/local/bin/shairport-sync` | milo-airplay | install/airplay.sh (compiled from source) |
| `nqptp.service` | Required by milo-airplay | install/airplay.sh (compiled from source) |

All repo-managed scripts referenced by services (`milo-wait-ready.sh`, `milo-first-boot`) exist at their expected paths.
