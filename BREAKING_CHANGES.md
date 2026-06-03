# Breaking Changes — Persisted Data

Each entry documents a bump of a persisted file's `schema_version` and the action required from the user during upgrade. Triggered by a `SchemaVersionMismatch` at boot (see [backend/shared/persistence.py](backend/shared/persistence.py) and CLAUDE.md §"Development & Coding Guidelines §2").

When you bump a `SCHEMA_VERSION` in a service, add an entry here with the file path, version bump, reason, exact `rm` command, and the impact on user state.

## Format

```markdown
## YYYY-MM-DD — <file>.json schema_version A → B
- Reason: <what changed in the schema and why a migration is not provided>
- Action: `rm /var/lib/milo/<file>.json && sudo systemctl restart milo-backend`
- Impact: <what the user loses / has to re-configure>
```

## Upcoming

## 2026-06-03 — client_equalizer.json removed (standalone client EQ unified into settings.json)

- Reason: standalone (non-zone) client equalizer state had **two** sources of truth — `client_equalizer.json` (written by the per-axis `/api/equalizer/client/{mac}/…` routes) and `multiroom.standalone_equalizer` in `settings.json` (written by preset/apply). Loading a preset wrote one store while reconnect sync read the other, so per-client preset gains were lost on reconnect. The `EqualizerSettingsSyncService` + `client_equalizer.json` store has been removed; the registry `standalone_equalizer` (in `settings.json`) is now the single source of truth. This file is no longer read or written.
- Action: `rm -f /var/lib/milo/client_equalizer.json` (optional cleanup — the file is simply ignored after upgrade; no restart needed for this file alone).
- Impact: per-standalone-client EQ tuning that lived **only** in `client_equalizer.json` (per-band edits not also captured under `settings.json` `multiroom.standalone_equalizer`) resets to flat/defaults for standalone clients. Zone EQ is unaffected. Re-tune standalone client EQ from the equalizer UI if needed.

## 2026-05-19 — settings.json schema_version 2 → 3

- Reason: rename `audio.auto_disconnect_delay` → `audio.auto_stop_delay`. The setting controlled an "auto-stop after pause/silence" timer (default 120s), not a device-level disconnect. The new name reflects the actual behavior (per-source playback stop in place, `active_source` preserved). All backend symbols (`auto_disconnect_enabled`, `_on_auto_disconnect`, `AUTO_DISCONNECT_SETTINGS_KEY`, …), the API endpoint (`/api/settings/audio-disconnect` → `/audio-stop`), and the WS event type (`audio_disconnect_changed` → `audio_stop_changed`) follow the same rename.
- Action: `rm /var/lib/milo/settings.json && sudo systemctl restart milo-backend`
- Impact: full reset to factory defaults (same scope as the 2026-05-17 entry below) — language, volume limits + startup volume + step sizes, screen (timeout, brightness, screensaver, color filter, UI scale), dock layout, routing (multiroom toggle, equalizer effects toggle), Mac ROC latency profile, audio auto-stop delay, radio Shazam toggle, WiFi country, podcast Taddy credentials, BT/IR remote pairing, multiroom client overrides — all reset. Snapshot the file before upgrade if you want to retype your settings: `cat /var/lib/milo/settings.json`.

## 2026-05-17 — equalizer.json schema_version → 2

- Reason: removal of `_migrate_from_settings` (legacy fold of `equalizer.*` keys from `settings.json` into `equalizer.json`) and inline migration of `equalizer.effects_enabled → routing.equalizer_effects_enabled`. Pre-existing `equalizer.json` files lack the `schema_version` field, so the load now fails loud.
- Action: `rm /var/lib/milo/equalizer.json && sudo systemctl restart milo-backend`
- Impact: equalizer state resets to factory defaults — custom presets, filters, compressor, loudness, mono ratio all reset. Snapshot the file before upgrade if you want to retype your settings: `cat /var/lib/milo/equalizer.json`.

## 2026-05-17 — settings.json schema_version → 2

- Reason: removal of the `auto_disconnect_delay` fold (legacy `spotify.auto_disconnect_delay` / `airplay.auto_disconnect_delay` keys folded into `audio.auto_disconnect_delay` on every load). Pre-existing `settings.json` files lack the `schema_version` field, so the load now fails loud.
- Action: `rm /var/lib/milo/settings.json && sudo systemctl restart milo-backend`
- Impact: full reset to factory defaults — language, volume limits + startup volume + step sizes, screen (timeout, brightness, screensaver, color filter, UI scale), dock layout, routing (multiroom toggle, equalizer effects toggle), Mac ROC latency profile, audio auto-disconnect delay, radio Shazam toggle, WiFi country, podcast Taddy credentials, BT/IR remote pairing, multiroom client overrides — all reset. Snapshot the file before upgrade if you want to retype your settings: `cat /var/lib/milo/settings.json`.

## 2026-05-17 — podcast_data.json schema_version → 1

- Reason: removal of `_ensure_structure` (auto-injected the `subscriptions`, `playback_progress`, `cache`, `settings` top-level keys + re-saved silently) and the dead `cache.{episodes,podcasts}` / `settings.safe_mode` blocks the loader had to seed. The loader now validates required keys (`subscriptions`, `playback_progress`, `settings`) fail-loud — pre-existing `podcast_data.json` files lack the `schema_version` field, so the load fails loud on first boot after upgrade.
- Action: `rm /var/lib/milo/podcast_data.json && sudo systemctl restart milo-backend`
- Impact: all podcast subscriptions, playback progress (resume positions, completed flags), and per-podcast settings (playback speed) are reset — the user has to re-subscribe to each podcast and restart any in-progress episode from the beginning. Snapshot the file before upgrade if you want to retype your subscriptions: `cat /var/lib/milo/podcast_data.json`.

## 2026-05-17 — hardware.json schema_version → 2

- Reason: removal of `_migrate_legacy_format` + `_resolve_audio_id` (~80 lines) — they folded the pre-`type`-field screen format (`{"screen": {"waveshare_8_dsi": {...}}}` → `{"screen": {"type": "waveshare_8_dsi", ...}}`) and back-resolved the `audio.id` field on first load. Removal of `_ensure_defaults` — it silently injected the `ir_remote` block on every load via `setdefault`, violating the loader doctrine. Pre-existing `hardware.json` files lack the `schema_version` field, so the load now fails loud.
- Action: `rm /var/lib/milo/hardware.json && sudo systemctl restart milo-backend`
- Impact: hardware config resets — screen type, screen resolution, audio card selection, rotary encoder pins + enabled flag, IR remote enabled flag + GPIO pin all reset. The setup wizard (`/setup`) must be re-run to re-select the audio card and screen; rotary encoder and IR remote toggles fall back to defaults via getters until re-saved through the Hardware settings page.
