---
project_name: 'milo'
user_name: 'Léo'
date: '2026-01-20'
sections_completed: ['technology_stack', 'language_rules', 'framework_rules', 'testing_rules', 'code_quality', 'workflow_rules', 'critical_rules']
status: 'complete'
rule_count: 49
optimized_for_llm: true
---

# Project Context for AI Agents

_This file contains critical rules and patterns that AI agents must follow when implementing code in this project. Focus on unobvious details that agents might otherwise miss._

---

## Technology Stack & Versions

**Backend (Python):**
- FastAPI (async-first architecture)
- pytest with asyncio_mode=auto
- Feature-based architecture with Service Registry

**Frontend (JavaScript):**
- Vue 3.3.4 + Composition API
- Vite 6.2.0
- Pinia 2.1.6 (state management)
- Zod 4.3.5 (validation)
- Vitest 4.0.16 (tests)
- Axios 1.4.0 (HTTP client)

**Audio Infrastructure:**
- CamillaDSP (audio processing / volume control)
- Snapcast (multiroom audio distribution)
- mpv (radio/podcast playback)
- go-librespot (Spotify Connect)
- ALSA only (no PulseAudio/Pipewire - HiFiBerry compatibility)

**Platform:**
- Raspberry Pi target
- Systemd service management
- Local network only (milo.local)

---

## Critical Implementation Rules

### Language-Specific Rules

**Python (Backend):**
- ALL I/O operations MUST be async/await - no blocking calls
- Use `asyncio.Lock()` for shared state operations
- No sudo in code - use `SystemdServiceManager` (PolicyKit handles permissions)
- No root permissions - backend runs as `milo` user

**JavaScript/Vue (Frontend):**
- Use Composition API exclusively (no Options API)
- Path alias `@/` resolves to `./src/`
- Import Vue composables from `@/composables/`

**Documentation Language:**
- ALWAYS write comments in English (even if conversation is in French)
- Applies to: inline comments, docstrings, TODO/FIXME notes
- User-facing text (UI labels, i18n strings) can be localized

**Code Cleanliness:**
- NO migration/fallback code paths
- NO duplicated "old" and "new" versions of logic
- NO compatibility shims for unused APIs
- ALWAYS single optimized code path

### Framework-Specific Rules

**FastAPI Backend Architecture:**
- `AudioStateMachine` is the SINGLE SOURCE OF TRUTH for all audio state
- Service Registry pattern with lazy singletons in `dependencies.py`
- EventBus for decoupled communication between services
- All audio sources MUST implement `AudioSourceProtocol` interface
- Extend `UnifiedAudioSource` base class for common functionality

**State Management (CRITICAL):**
```python
# CORRECT - use state_machine methods
await state_machine.update_plugin_state(source, PluginState.READY, metadata)

# WRONG - bypasses locks and broadcasting
state_machine._state.active_source = source  # NEVER DO THIS
```

**WebSocket Broadcasting:**
- ALL state changes MUST broadcast via `state_machine._broadcast_event()`
- Categories: `plugin`, `system`, `routing`, `equalizer`

**Vue 3 + Pinia Frontend:**
- Central stores: `unifiedAudioStore`, `settingsStore`, `dspStore`, `multiroomStore`, `clientRegistryStore`
- State sync flow: Backend → WebSocket event → Pinia store → Reactive UI
- Composables in `@/composables/` for reusable logic
- Components organized by feature: `audio/`, `spotify/`, `radio/`, `podcasts/`, `multiroom/`

**API Prefix Convention (CRITICAL):**
- `/api/multiroom/` is the **canonical** prefix for all client/zone operations
- `/api/registry/` is **deprecated** - do NOT use for new code
- Canonical endpoints:
  - `GET /api/multiroom/state` → clients + zones (initial sync)
  - `PATCH /api/multiroom/clients/{mac_id}` → update client name/speaker_type
  - `GET/POST/PATCH/DELETE /api/multiroom/zones/*` → zone CRUD
- Some legacy endpoints still in `/api/registry/` pending migration (see TODOs in clientRegistryStore.js)

**Frontend Schema Convention:**
- `RegisteredClientSchema` → client metadata (mac_id, name, ip, online, zone_id, speaker_type)
- `VolumeClientSchema` → volume state (volume_db, offset_db, mute, **online** not "available")
- `MultiroomStateSchema` → response from `/api/multiroom/state`
- Legacy `SnapcastClientSchema` has been removed - do NOT recreate

**Settings Persistence:**
```python
# CORRECT - persisted to disk
await settings_service.set_setting('volume.alsa_max', 80)

# WRONG - not persisted
settings['volume']['alsa_max'] = 80  # NEVER DO THIS
```

### Testing Rules

**Backend (pytest):**
- `asyncio_mode = auto` - async tests auto-detected
- Use `@pytest.mark.asyncio` for explicitly async tests
- Available markers: `unit`, `integration`, `slow`, `asyncio`
- Mock dependencies via constructor injection
- Tests location: `backend/tests/` with `test_*.py` pattern
- Integration tests in `tests/integration/`

**Frontend (Vitest):**
- Use `@vue/test-utils` with `happy-dom` environment
- Run tests: `npm run test` or `npm run test:run`
- Coverage: `npm run test:coverage`

**Test Organization:**
- Unit tests: `test_{feature}.py` at `tests/` root
- Integration tests: `tests/integration/test_{feature}.py`
- Name pattern: `test_{component}_{feature}.py`

### Code Quality & Style Rules

**Backend File Organization:**
```
backend/features/{source}/
├── source.py      # AudioSourceProtocol implementation
├── routes.py      # FastAPI routes
└── __init__.py    # Exports
```

**Frontend File Organization:**
```
frontend/src/components/{feature}/
├── {Feature}Source.vue    # Main component
├── {SubView}.vue          # Sub-views
└── Skeleton*.vue          # Loading states
```

**Naming Conventions:**
- Python: snake_case for functions/variables, PascalCase for classes
- Vue components: PascalCase (e.g., `RadioSource.vue`, `PodcastDetails.vue`)
- Pinia stores: camelCase with `Store` suffix (e.g., `unifiedAudioStore`)
- Composables: `use` prefix (e.g., `useNavigationStack`, `useSettingsAPI`)

**Import Conventions:**
- Frontend: use `@/` alias for imports from `src/`
- Backend: relative imports within features, absolute for core modules

### Development Workflow Rules

**Systemd Services:**
- All plugins `BindsTo=milo-backend` - they stop if backend stops
- Main services: `milo-backend`, `milo-spotify`, `milo-radio`, `milo-podcast`
- Multiroom: `milo-snapserver-multiroom`, `milo-snapclient-multiroom`

**Development Commands:**
```bash
# Backend
cd backend && python main.py    # Dev server on :8000
python -m pytest                # Run tests

# Frontend
cd frontend && npm run dev      # Dev server on :5173 (proxies to backend)
npm run build                   # Production build to dist/

# Logs
sudo journalctl -u milo-backend -f    # Live backend logs
```

**Data Persistence (all in /var/lib/milo/):**
- `settings.json` - Central configuration
- `hardware.json` - Hardware configuration
- `radio_data.json`, `podcast_data.json` - User data
- `routing.env` - ALSA routing variables (auto-generated)

**ALSA Dynamic Routing:**
- Device pattern: `milo_{source}_direct` and `milo_{source}_multiroom`
- CamillaDSP is ALWAYS in the audio path for volume control
- DSP effects toggled via `bypass_effects()` / `restore_effects()`, NOT via routing

---

## Critical Don't-Miss Rules

### Service Initialization Order (CRITICAL)

The order in `dependencies.py::initialize_services()` is **CRITICAL** due to circular dependencies:

1. Retrieve instances (triggers lazy creation via `get_service()`)
2. Resolve circular dependencies via setters:
   - `routing_service.set_plugin_callback()`
   - `routing_service.set_snapcast_websocket_service()`
   - `routing_service.set_state_machine()`
   - `state_machine.routing_service = routing_service`
3. Register plugins in state_machine (BEFORE async init)
4. Parallel async initialization via `asyncio.gather()`

**⚠️ DO NOT modify this order without understanding circular dependencies documented in `dependencies.py:227-348`**

### Anti-Patterns to Avoid

- ❌ Modify `state_machine._state` directly - use `update_plugin_state()`
- ❌ Write to `settings.json` directly - use `SettingsService`
- ❌ Use blocking I/O - always async/await
- ❌ Hardcode ALSA devices - use environment variable pattern
- ❌ Skip plugin registration before `init_async()`
- ❌ Add migration/fallback code paths
- ❌ Use `/api/registry/` for new frontend code - use `/api/multiroom/` instead
- ❌ Create `SnapcastClientSchema` or use `available` instead of `online` for client status

### Security Constraints

- CORS restricted to `milo.local` and `localhost:5173`
- Local network only - no external access
- No root permissions required - runs as `milo` user

### Adding New Audio Source Plugins

1. Define enum in `backend/core/models/audio_state.py::AudioSource`
2. Create feature module in `backend/features/{source}/`
3. Register in `dependencies.py::_create_service()`
4. Add ALSA devices with direct/multiroom variants
5. Register plugin in `initialize_services()` BEFORE `init_async()`
6. Register routes in `backend/main.py`
7. Create Vue component in `frontend/src/components/{source}/`

---

## Usage Guidelines

**For AI Agents:**
- Read this file before implementing any code
- Follow ALL rules exactly as documented
- When in doubt, prefer the more restrictive option
- Update this file if new patterns emerge

**For Humans:**
- Keep this file lean and focused on agent needs
- Update when technology stack changes
- Review quarterly for outdated rules
- Remove rules that become obvious over time

---

_Last Updated: 2026-01-20_
