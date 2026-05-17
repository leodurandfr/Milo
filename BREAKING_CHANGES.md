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

_(empty — add an entry here when bumping a `SCHEMA_VERSION` in a service)_
