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

## 2026-05-17 — equalizer.json schema_version → 2

- Reason: removal of `_migrate_from_settings` (legacy fold of `equalizer.*` keys from `settings.json` into `equalizer.json`) and inline migration of `equalizer.effects_enabled → routing.equalizer_effects_enabled`. Pre-existing `equalizer.json` files lack the `schema_version` field, so the load now fails loud.
- Action: `rm /var/lib/milo/equalizer.json && sudo systemctl restart milo-backend`
- Impact: equalizer state resets to factory defaults — custom presets, filters, compressor, loudness, mono ratio all reset. Snapshot the file before upgrade if you want to retype your settings: `cat /var/lib/milo/equalizer.json`.

## 2026-05-17 — settings.json schema_version → 2

- Reason: removal of the `auto_disconnect_delay` fold (legacy `spotify.auto_disconnect_delay` / `airplay.auto_disconnect_delay` keys folded into `audio.auto_disconnect_delay` on every load). Pre-existing `settings.json` files lack the `schema_version` field, so the load now fails loud.
- Action: `rm /var/lib/milo/settings.json && sudo systemctl restart milo-backend`
- Impact: full reset to factory defaults — language, volume limits + startup volume + step sizes, screen (timeout, brightness, screensaver, color filter, UI scale), dock layout, routing (multiroom toggle, equalizer effects toggle), Mac ROC latency profile, audio auto-disconnect delay, radio Shazam toggle, WiFi country, podcast Taddy credentials, BT/IR remote pairing, multiroom client overrides — all reset. Snapshot the file before upgrade if you want to retype your settings: `cat /var/lib/milo/settings.json`.
