# Story 1.2: Implement ClientRegistryService

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **system**,
I want **a central ClientRegistryService that manages all client state**,
So that **the backend is the single source of truth for client information**.

## Acceptance Criteria

1. **AC1: Service Location and Structure**
   - **Given** the backend codebase
   - **When** I implement ClientRegistryService in `core/multiroom/registry.py`
   - **Then** the service is a class with async methods
   - **And** the file location is exactly `backend/core/multiroom/registry.py`

2. **AC2: Client Storage**
   - **Given** the ClientRegistryService
   - **When** managing client state
   - **Then** the service maintains a dict of clients keyed by `mac_id`
   - **And** `mac_id` format is with colons (e.g., `dc:a6:32:7e:d3:43`) or `"local"` for main device
   - **And** the dict uses `Client` dataclass from `models.py`

3. **AC3: Required Methods**
   - **Given** the ClientRegistryService
   - **When** implementing public methods
   - **Then** the service provides:
     - `get_client(mac_id: str) -> Optional[Client]` - Returns client or None
     - `get_all_clients() -> List[Client]` - Returns all registered clients
     - `update_client(mac_id: str, updates: Dict[str, Any]) -> Client` - Updates and returns client
     - `register_client(client: Client) -> Client` - Registers new client
   - **And** all methods that modify state are async

4. **AC4: Persistence via SettingsService**
   - **Given** any client state change (register, update)
   - **When** the change is applied
   - **Then** changes are persisted to `settings.json` via `SettingsService`
   - **And** persistence uses `await settings_service.set_setting()` (NEVER direct file access)
   - **And** client data is stored under `multiroom.clients` key in settings

5. **AC5: Service Registration**
   - **Given** the ClientRegistryService is implemented
   - **When** the backend starts
   - **Then** the service is registered in `dependencies.py` as a lazy singleton
   - **And** the service is accessible via `get_service('client_registry_service')`

6. **AC6: Thread Safety**
   - **Given** concurrent operations on client state
   - **When** multiple async operations run simultaneously
   - **Then** an `asyncio.Lock()` protects shared state operations
   - **And** no race conditions occur during read/modify/write operations

7. **AC7: Initialization**
   - **Given** the backend starts
   - **When** ClientRegistryService initializes
   - **Then** it loads existing clients from settings.json
   - **And** the `initialize()` async method is called during startup
   - **And** runtime fields (`online`) default to `False` for all loaded clients

## Tasks / Subtasks

- [x] **Task 1: Review existing registry.py implementation** (AC: #1)
  - [x] Read current `backend/core/multiroom/registry.py` to understand existing code
  - [x] Identify what needs to be added/modified to meet acceptance criteria
  - [x] Note any existing methods that already satisfy requirements

- [x] **Task 2: Implement/verify client storage structure** (AC: #2)
  - [x] Verify `_clients: Dict[str, Client]` internal dict exists
  - [x] Verify mac_id is used as key consistently
  - [x] Ensure Client dataclass is imported from models.py

- [x] **Task 3: Implement required public methods** (AC: #3)
  - [x] Implement/verify `get_client(mac_id: str) -> Optional[Client]`
  - [x] Implement/verify `get_all_clients() -> List[Client]`
  - [x] Implement/verify `async update_client(mac_id: str, updates: Dict[str, Any]) -> Client`
  - [x] Implement/verify `async register_client(client: Client) -> Client`

- [x] **Task 4: Implement persistence via SettingsService** (AC: #4)
  - [x] Inject SettingsService dependency in constructor
  - [x] Implement `async _persist_clients()` private method
  - [x] Call `_persist_clients()` after every state modification
  - [x] Use `settings.multiroom.clients` key path for storage
  - [x] Serialize clients using `client.to_dict()` method

- [x] **Task 5: Register service in dependencies.py** (AC: #5)
  - [x] Add ClientRegistryService to `_create_service()` function
  - [x] Ensure lazy singleton pattern is followed
  - [x] Verify service is accessible via `get_service('client_registry_service')`

- [x] **Task 6: Implement thread safety** (AC: #6)
  - [x] Add `_lock: asyncio.Lock` to service
  - [x] Wrap all state-modifying operations in `async with self._lock:`
  - [x] Ensure read operations are safe (dict reads are atomic in Python)

- [x] **Task 7: Implement initialization** (AC: #7)
  - [x] Implement `async initialize()` method
  - [x] Load clients from `settings.multiroom.clients`
  - [x] Deserialize using `Client.from_dict()`
  - [x] Set `online=False` for all loaded clients (runtime state)

- [x] **Task 8: Write/update unit tests** (AC: all)
  - [x] Test `get_client()` with existing and non-existing mac_id
  - [x] Test `get_all_clients()` returns all registered clients
  - [x] Test `update_client()` modifies and persists client
  - [x] Test `register_client()` adds new client and persists
  - [x] Test thread safety with concurrent operations
  - [x] Test initialization loads clients from settings

## Dev Notes

### Architecture Context

This story implements the **central ClientRegistryService** that is the backbone of the multiroom/DSP refactoring. Per architecture document:

> "ClientRegistryService is the central service for all client/zone state management"

**Key architectural decisions:**
1. **Backend = Single Source of Truth (SSOT)** - All client state managed here
2. **Service Registry pattern** - Lazy singletons via `dependencies.py`
3. **Async everywhere** - All I/O operations must be async

### Previous Story Intelligence (1-1)

From Story 1-1 completion:
- `Client` dataclass is in `backend/core/multiroom/models.py`
- `to_dict()` excludes runtime field `online` (fixed in 1-1)
- `from_dict()` handles missing fields gracefully
- Volume default: `-60.0 dB` from `DEFAULT_VOLUME_DB` constant
- MAC format: with colons (`dc:a6:32:7e:d3:43`) or `"local"` for main device

### DSP Settings Note (CRITICAL)

Per architecture, DSP settings are NOT part of Client directly:
- **STANDALONE clients**: DSP stored in `standalone_dsp[mac_id]` dict (to be added in later story)
- **IN_ZONE clients**: DSP source of truth is `zone.dsp_settings`

This story only handles basic client registry. DSP settings management is in Epic 4.

### Existing Implementation Status

The `ClientRegistryService` class likely already exists in `backend/core/multiroom/registry.py`. This story is about **verifying and completing** the implementation to meet all acceptance criteria.

**Expected existing elements:**
- Class definition with `_clients` dict
- Some CRUD methods
- Possibly SettingsService integration

**May need additions:**
- `asyncio.Lock()` for thread safety
- Complete persistence logic
- `initialize()` method for startup loading
- Registration in `dependencies.py`

### Project Structure Notes

**File location:** `backend/core/multiroom/registry.py`

**Related files:**
- `backend/core/multiroom/models.py` - Client dataclass (from story 1-1)
- `backend/dependencies.py` - Service registration
- `backend/core/settings.py` - SettingsService for persistence

**Settings structure expected:**
```json
{
  "multiroom": {
    "clients": {
      "dc:a6:32:7e:d3:43": {
        "mac_id": "dc:a6:32:7e:d3:43",
        "name": "Salon",
        "ip": "192.168.1.100",
        "zone_id": null,
        "volume_db": -60.0,
        "mute": false,
        "speaker_type": "bookshelf"
      }
    }
  }
}
```

### Thread Safety Pattern

```python
class ClientRegistryService:
    def __init__(self, settings_service: SettingsService):
        self._clients: Dict[str, Client] = {}
        self._settings_service = settings_service
        self._lock = asyncio.Lock()

    async def update_client(self, mac_id: str, updates: Dict[str, Any]) -> Client:
        async with self._lock:
            client = self._clients.get(mac_id)
            if not client:
                raise ValueError(f"Client {mac_id} not found")
            # Apply updates
            for key, value in updates.items():
                if hasattr(client, key):
                    setattr(client, key, value)
            await self._persist_clients()
            return client
```

### Persistence Pattern

```python
async def _persist_clients(self) -> None:
    """Persist all clients to settings.json."""
    clients_data = {
        mac_id: client.to_dict()
        for mac_id, client in self._clients.items()
    }
    await self._settings_service.set_setting('multiroom.clients', clients_data)
```

### Initialization Pattern

```python
async def initialize(self) -> None:
    """Load clients from settings on startup."""
    clients_data = await self._settings_service.get_setting('multiroom.clients', {})
    for mac_id, data in clients_data.items():
        client = Client.from_dict(data)
        client.online = False  # Runtime state, always starts offline
        self._clients[mac_id] = client
```

### Service Registration Pattern

In `dependencies.py`:
```python
def _create_service(service_name: str) -> Any:
    if service_name == 'client_registry_service':
        settings_service = get_service('settings_service')
        return ClientRegistryService(settings_service)
    # ... other services
```

### Git Intelligence

Recent commits show:
- Volume sync work in multiroom mode (`9a31e2f`)
- Feature-based architecture documentation updates (`f7cf915`)
- Volume default to -60dB implemented (`f9967a6`)

These indicate active work on multiroom subsystem - align with existing patterns.

### References

- [Source: _bmad-output-v2/planning-artifacts/architecture.md - Section "Core Architectural Decisions"]
- [Source: _bmad-output-v2/planning-artifacts/epics.md - Story 1.2]
- [Source: _bmad-output-v2/implementation-artifacts/1-1-define-registered-client-model.md - Previous story learnings]
- [Source: _bmad-output-v2/project-context.md - Critical implementation rules]
- [Source: backend/core/multiroom/models.py - Client dataclass]
- [Source: backend/dependencies.py - Service registration pattern]

### Testing Checklist

Per project-context.md:
- Tests in `backend/tests/test_core_multiroom.py` or new file `test_client_registry.py`
- Use `@pytest.mark.asyncio` for async tests
- Mock SettingsService for unit tests
- Integration tests can use actual SettingsService with temp file

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

None - implementation was already complete

### Completion Notes List

- **2026-01-18**: Verified existing `ClientRegistryService` implementation in `backend/core/multiroom/registry.py`
- All 7 Acceptance Criteria were already satisfied by the existing implementation:
  - AC1: Service located at correct path with async methods
  - AC2: `_clients: Dict[str, Client]` storage with `mac_id` as key
  - AC3: All required methods exist (with richer signatures than specified)
  - AC4: Persistence via `_persist_clients()` using SettingsService
  - AC5: Registered in `dependencies.py` as lazy singleton
  - AC6: Thread safety via `_lock = asyncio.Lock()`
  - AC7: `initialize()` loads clients with `online=False`
- Added 3 new unit tests to cover:
  - Thread safety with concurrent operations (AC6)
  - Persistence verification on register (AC4)
  - Initialization loads clients offline (AC7)
- All multiroom tests pass (3 new tests added for AC4, AC6, AC7)

### File List

- `backend/core/multiroom/registry.py` - Main implementation (existing, verified)
- `backend/core/multiroom/models.py` - Client dataclass (existing, verified)
- `backend/dependencies.py` - Service registration (existing, verified)
- `backend/tests/test_core_multiroom.py` - Unit tests (modified, added 3 new tests)
- `backend/api/registry.py` - API routes (modified, added `_client_with_online()` helper to include runtime `online` status in API responses)

### Implementation Note: Backward Compatibility

The service includes deprecated method aliases for transition from old API:
- `update_availability()` → use `set_client_online()` instead
- `get_available_clients()` → use `get_online_clients()` instead
- `is_client_available()` → use `is_client_online()` instead
- `get_available_client_ids()` → use `get_online_client_ids()` instead

These aliases will be removed in a future refactoring once all callers are migrated.

## Change Log

- **2026-01-18**: Story 1-2 implementation verified and completed. Added tests for thread safety, persistence verification, and initialization behavior. Fixed API to include `online` runtime field in client responses.
- **2026-01-18**: [Code Review] Updated File List, added backward compatibility documentation, corrected test counts.

