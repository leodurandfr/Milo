# Cleanup Guide - Prompts & Phases

> This folder contains all artifacts from the full codebase cleanup.
> Each phase produces its own reports. Corrections are applied from Phase 3.

---

## Phase 1 — Static Analysis (automated scans)

Run these prompts **in order**. Each one produces a report in `cleanup/reports/`.

---

### Prompt 1.1 — Python: dead code & imports

> Install `vulture` and `ruff` in a temporary venv, then scan the entire backend and milo-client Python code.
> - `vulture`: detect unused functions, variables, classes, imports, unreachable code
> - `ruff`: detect unused imports, undefined names, code style issues
>
> Save raw outputs to `cleanup/reports/python-vulture.md` and `cleanup/reports/python-ruff.md`.
> Filter out false positives (Protocol methods, __dunder__, pytest fixtures).
> Don't fix anything yet.

---

### Prompt 1.2 — JavaScript/Vue: dead code, exports & dependencies

> Install `knip` (detects unused files, exports, dependencies, types) in the frontend.
> Also run `eslint` with `no-unused-vars` and `no-unreachable` rules across all `.js` and `.vue` files.
>
> Save raw outputs to `cleanup/reports/js-knip.md` and `cleanup/reports/js-eslint.md`.
> Don't fix anything yet.

---

### Prompt 1.3 — Cross-stack: endpoints, WebSocket events & i18n

> Do a cross-referencing analysis:
>
> 1. **API endpoints**: list every backend route (from all `routes.py` and `backend/api/*.py`), then grep the frontend for each endpoint path. Flag any endpoint that has zero frontend callers.
> 2. **WebSocket events**: list every `broadcast_event(category=..., type=...)` in the backend, then grep frontend stores for each `category`+`type` pair. Flag unhandled events.
> 3. **i18n keys**: extract all keys from `frontend/src/locales/english.json`, then grep all `.vue` files for each key. Flag keys that are never referenced.
> 4. **Vue components**: list every `.vue` file in `components/`, then grep for its import or tag usage across all other `.vue` files and `.js` files. Flag components never imported.
>
> Save results to `cleanup/reports/cross-stack-endpoints.md`, `cleanup/reports/cross-stack-ws-events.md`, `cleanup/reports/cross-stack-i18n.md`, `cleanup/reports/cross-stack-components.md`.
> Don't fix anything yet.

---

### Prompt 1.4 — Shell scripts & system files

> Run `shellcheck` (if available, otherwise analyze manually) on:
> - `install.sh`, `install/*.sh`
> - `rootfs/usr/local/bin/*`
> - `milo-client/install-client.sh`, `milo-client/rootfs/usr/local/bin/*`
> - `pi-gen/stage-milo/**/*.sh`
>
> Also check:
> - Systemd services (`system/*.service`, `milo-client/system/*.service`): verify referenced binaries/paths exist
> - Rootfs files: check for dead config entries
>
> Save results to `cleanup/reports/shell-scripts.md` and `cleanup/reports/systemd-services.md`.
> Don't fix anything yet.

---

## Phase 2 — Consolidated Report

### Prompt 2.1 — Build the master report

> Read all reports in `cleanup/reports/`. Consolidate into a single `cleanup/REPORT.md` with these sections:
>
> 1. **Dead code** (confirmed unused — no reference anywhere)
>    - Python: functions, classes, methods, variables, imports
>    - JavaScript/Vue: components, functions, exports, imports
>    - Group by file, sorted by impact (most lines removable first)
>
> 2. **Dead assets & config**
>    - Unused i18n keys
>    - Unused CSS classes/variables
>    - Unused image/icon assets
>    - Unused npm/pip dependencies
>    - Orphan systemd services or dead script paths
>
> 3. **Duplicated logic** (same pattern implemented differently in multiple places)
>    - Candidates for centralization
>    - Estimated complexity to consolidate
>
> 4. **Inconsistent patterns** (same thing done 3 different ways)
>    - Error handling
>    - API call patterns
>    - State management
>    - Naming conventions
>
> 5. **Action plan** — For each finding, tag with:
>    - `[REMOVE]` — Safe to delete, no impact
>    - `[REFACTOR]` — Needs rewrite/consolidation
>    - `[VERIFY]` — Might be used dynamically, needs manual check
>
> Don't fix anything yet.

---

## Phase 3 — Corrections (apply fixes)

Run these in order. Each prompt corresponds roughly to the original batch plan, but now guided by the Phase 2 report.

---

### Prompt 3.1 — Backend: remove confirmed dead code

> Open `cleanup/REPORT.md`. For every `[REMOVE]`-tagged Python item:
> - Delete the dead function/class/method/import/variable
> - Run `python -m pytest backend/tests/ -x -q` after each logical group of deletions to verify nothing breaks
>
> Commit after each logical group (e.g., "remove dead code from backend/core", "remove dead code from backend/sources/radio").

---

### Prompt 3.2 — Frontend: remove confirmed dead code

> Open `cleanup/REPORT.md`. For every `[REMOVE]`-tagged JavaScript/Vue item:
> - Delete unused components, functions, exports, imports, i18n keys, CSS
> - Run `npm run build` in `frontend/` after each group to verify no build errors
>
> Commit after each logical group.

---

### Prompt 3.3 — System: remove dead scripts & config

> Open `cleanup/REPORT.md`. For every `[REMOVE]`-tagged system item:
> - Remove dead shell script sections, unused service configs, orphan assets
>
> Commit.

---

### Prompt 3.4 — Refactor: consolidate duplicated logic

> Open `cleanup/REPORT.md`. For every `[REFACTOR]`-tagged item:
> - Consolidate duplicated patterns into shared utilities
> - Harmonize inconsistent patterns to use the dominant/best approach
> - Run both `python -m pytest backend/tests/ -x -q` and `npm run build` after changes
>
> Commit after each refactor.

---

### Prompt 3.5 — Verify: manually check dynamic usage

> Open `cleanup/REPORT.md`. For every `[VERIFY]`-tagged item:
> - Check if it's used dynamically (string interpolation, getattr, computed component names, etc.)
> - If confirmed dead, remove. If used, mark as resolved in the report.
>
> Commit.

---

### Prompt 3.6 — Tests & docs: align with cleaned code

> - Remove/update tests that reference deleted code
> - Run full test suite: `python -m pytest backend/tests/ -v`
> - Update `CLAUDE.md` if any structural changes were made
> - Update docs if they reference removed features
>
> Final commit.

---

## File Structure

```
cleanup/
├── CLEANUP_GUIDE.md          ← this file (prompts & process)
├── REPORT.md                 ← Phase 2: master report (created in 2.1)
└── reports/                  ← Phase 1: raw scan outputs
    ├── python-vulture.md
    ├── python-ruff.md
    ├── js-knip.md
    ├── js-eslint.md
    ├── cross-stack-endpoints.md
    ├── cross-stack-ws-events.md
    ├── cross-stack-i18n.md
    ├── cross-stack-components.md
    ├── shell-scripts.md
    └── systemd-services.md
```
