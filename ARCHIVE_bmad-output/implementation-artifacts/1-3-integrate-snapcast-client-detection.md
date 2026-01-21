# Story 1.3: Integrate Snapcast Client Detection

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user**,
I want **the system to automatically detect when my Milo devices connect or disconnect**,
So that **I always see the accurate online/offline status of my devices**.

## Acceptance Criteria

1. **AC1: Client Connection Detection**
   - **Given** ClientRegistryService is implemented (Story 1-2: done)
   - **When** a Snapcast client connects
   - **Then** ClientRegistryService receives the event and marks the client as `online: true`
   - **And** a WebSocket event `client_state_changed` is broadcast to frontend

2. **AC2: Client Disconnection Detection**
   - **Given** a client is registered and online
   - **When** the Snapcast client disconnects
   - **Then** ClientRegistryService marks the client as `online: false`
   - **And** a WebSocket event `client_state_changed` is broadcast to frontend

3. **AC3: Auto-Registration of New Clients**
   - **Given** a new unknown client connects via Snapcast
   - **When** its MAC address is not in the registry
   - **Then** the client is auto-registered with default values:
     - `name`: from Snapcast (hostname or config name)
     - `speaker_type`: `'bookshelf'` (DEFAULT_SPEAKER_TYPE)
     - `volume_db`: `-60.0` (DEFAULT_VOLUME_DB)
     - `online`: `true`
     - `zone_id`: `null` (standalone)
   - **And** a WebSocket event `client_connected` is broadcast

4. **AC4: WebSocket Event Format**
   - **Given** any client state change
   - **When** an event is broadcast
   - **Then** it follows the format:
     ```json
     {
       "category": "registry",
       "type": "client_connected|client_disconnected|client_updated",
       "data": {
         "mac_id": "dc:a6:32:7e:d3:43",
         "client": { /* full client state */ }
       }
     }
     ```

5. **AC5: Event Timing (NFR2)**
   - **Given** a client connects or disconnects
   - **When** the event is processed
   - **Then** the WebSocket event reaches frontend within 100ms

## Tasks / Subtasks

- [x] **Task 1: Verify existing Snapcast integration** (AC: #1, #2)
  - [x] Read `backend/core/multiroom/websocket.py` - verify `SnapcastWebSocketService` handles `Client.OnConnect` and `Client.OnDisconnect`
  - [x] Verify `_handle_client_connect()` calls `registry.register_client()` and `registry.set_client_online(mac_id, True)`
  - [x] Verify `_handle_client_disconnect()` calls `registry.set_client_online(mac_id, False)`
  - [x] Verify events are broadcast via `_broadcast_snapcast_event()`

- [x] **Task 2: Verify auto-registration with defaults** (AC: #3)
  - [x] Verify `register_client()` in `ClientRegistryService` creates new clients with correct defaults
  - [x] Verify `compute_mac_id()` is used consistently for client identification
  - [x] Verify `DEFAULT_SPEAKER_TYPE` and `DEFAULT_VOLUME_DB` are applied to new clients

- [x] **Task 3: Verify WebSocket event format** (AC: #4)
  - [x] Verify `_emit_event()` in `ClientRegistryService` broadcasts to state_machine and EventBus
  - [x] Verify event structure includes `category`, `type`, and `data` with `mac_id` and `client` dict
  - [x] Verify `RegistryEventType` enum values match expected event types

- [x] **Task 4: Verify initialization of existing clients** (AC: #1, #3)
  - [x] Verify `_initialize_existing_clients()` in SnapcastWebSocketService syncs clients at startup
  - [x] Verify already-connected clients are registered and marked online during initialization
  - [x] Verify no duplicate registration occurs for known clients

- [x] **Task 5: Write/update unit tests** (AC: all)
  - [x] Test client connect event triggers `register_client()` and `set_client_online(True)`
  - [x] Test client disconnect event triggers `set_client_online(False)`
  - [x] Test new client auto-registration with default values
  - [x] Test WebSocket event format matches specification
  - [x] Test existing clients are synced on WebSocket service initialization

- [x] **Task 6: Write integration test** (AC: #1, #2, #5)
  - [x] Create test file `backend/tests/integration/test_snapcast_detection.py`
  - [x] Test end-to-end: simulated Snapcast event -> registry update -> WebSocket broadcast
  - [x] Verify timing meets NFR2 (< 100ms)

## Dev Notes

### Implementation Status: MOSTLY COMPLETE

Based on codebase analysis, the Snapcast integration is **already largely implemented**. The main work is:
1. **Verification** that all acceptance criteria are met
2. **Testing** to confirm behavior
3. **Minor fixes** if any gaps are found

### Existing Implementation

**SnapcastWebSocketService** (`backend/core/multiroom/websocket.py`):
- Handles `Client.OnConnect` via `_handle_client_connect()` - calls `registry.register_client()` and `set_client_online(True)`
- Handles `Client.OnDisconnect` via `_handle_client_disconnect()` - calls `registry.set_client_online(False)`
- Handles `Server.OnUpdate` for bulk state changes
- `_initialize_existing_clients()` syncs clients at startup

**SnapcastService** (`backend/core/multiroom/snapcast.py`):
- `get_clients()` returns current Snapcast clients with `mac_id` computed via `ClientRegistryService.compute_mac_id()`
- Deduplicates clients by MAC address

**ClientRegistryService** (`backend/core/multiroom/registry.py`):
- `register_client(mac_id, name, ip, speaker_type)` - creates/updates client
- `set_client_online(mac_id, online)` - updates online status
- `_emit_event()` - broadcasts via state_machine and EventBus
- `compute_mac_id()` - static method for stable client identification

### MAC Address Identification

Per architecture document, clients are identified by `mac_id`:
- **Format**: With colons for storage/display (`dc:a6:32:7e:d3:43`)
- **Special case**: `"local"` for main device (127.0.0.1)
- **Computed from**: hostname + IP via `ClientRegistryService.compute_mac_id()`

```python
@staticmethod
def compute_mac_id(hostname: str, ip: str) -> str:
    if ip == "127.0.0.1":
        return "local"
    if hostname and hostname.startswith("milo-client"):
        return hostname
    if hostname:
        return f"{hostname}-{ip.replace('.', '-')}"
    return ip.replace(".", "-")
```

### WebSocket Event Flow

```
Snapcast Server
    │
    ▼ (WebSocket notification)
SnapcastWebSocketService._handle_client_connect()
    │
    ▼ (calls)
ClientRegistryService.register_client() + set_client_online()
    │
    ▼ (emits)
ClientRegistryService._emit_event()
    │
    ├─► state_machine.broadcast_event() → Frontend WebSocket
    └─► event_bus.emit() → Internal subscribers
```

### Event Types (RegistryEventType)

From `backend/core/multiroom/models.py`:
- `CLIENT_CONNECTED` - Client came online (also for new registrations)
- `CLIENT_DISCONNECTED` - Client went offline
- `CLIENT_UPDATED` - Client properties changed

### Previous Story Learnings (1-1, 1-2)

From Story 1-1:
- `Client` dataclass uses Python `@dataclass` (not Pydantic)
- `to_dict()` excludes runtime field `online`
- `from_dict()` handles missing fields gracefully

From Story 1-2:
- `ClientRegistryService` is registered in `dependencies.py` as lazy singleton
- Thread safety via `asyncio.Lock()`
- Persistence via `SettingsService.set_setting()`

### Git Intelligence

Recent commits show:
- `fa167e4`: Added client deletion and offline handling - offline state UI in ClientEdit
- `99a98b7`: Compute `crossover_enabled` dynamically based on subwoofer availability
- Volume sync work in multiroom mode (`9a31e2f`)

### Project Structure Notes

**Files to verify/test:**
- `backend/core/multiroom/websocket.py` - SnapcastWebSocketService
- `backend/core/multiroom/snapcast.py` - SnapcastService
- `backend/core/multiroom/registry.py` - ClientRegistryService
- `backend/core/multiroom/models.py` - RegistryEventType enum

**Test locations:**
- `backend/tests/test_core_multiroom.py` - Unit tests for multiroom
- `backend/tests/integration/test_snapcast_detection.py` - New integration test

### Testing Strategy

**Unit Tests:**
- Mock SnapcastService for WebSocket event simulation
- Mock EventBus and state_machine for event verification
- Test each event handler in isolation

**Integration Tests:**
- Use actual services with in-memory state
- Simulate Snapcast JSON-RPC notifications
- Verify end-to-end event flow

### Performance Requirement (NFR2)

> "WebSocket state updates reach frontend within 100ms"

This is achieved by:
1. Direct async calls (no blocking I/O)
2. No persistence on online status changes (runtime only)
3. Immediate broadcast via state_machine

### References

- [Source: _bmad-output/planning-artifacts/architecture.md - Section "WebSocket Events"]
- [Source: _bmad-output/planning-artifacts/epics.md - Story 1.3]
- [Source: _bmad-output/implementation-artifacts/1-1-define-registered-client-model.md - Previous story]
- [Source: _bmad-output/implementation-artifacts/1-2-implement-client-registry-service.md - Previous story]
- [Source: backend/core/multiroom/websocket.py - SnapcastWebSocketService]
- [Source: backend/core/multiroom/snapcast.py - SnapcastService]
- [Source: backend/core/multiroom/registry.py - ClientRegistryService]

### Code Patterns to Follow

**Event emission pattern:**
```python
await self._emit_event(RegistryEventType.CLIENT_CONNECTED, {
    "mac_id": mac_id,
    "client": client.to_dict()
})
```

**Client registration pattern:**
```python
if self.registry:
    await self.registry.register_client(mac_id, client_name, client_ip)
    await self.registry.set_client_online(mac_id, True)
```

**MAC ID computation:**
```python
mac_id = ClientRegistryService.compute_mac_id(hostname, ip)
```

## Dev Agent Record

### Agent Model Used

Claude Opus 4.5 (claude-opus-4-5-20251101)

### Debug Log References

None - implementation was already complete, story focused on verification and testing.

### Completion Notes List

1. **Verification Complete**: All acceptance criteria verified against existing implementation:
   - AC1: `_handle_client_connect()` correctly calls `registry.register_client()` and `set_client_online(True)` (websocket.py:491-493)
   - AC2: `_handle_client_disconnect()` correctly calls `registry.set_client_online(False)` (websocket.py:540-541)
   - AC3: New clients auto-registered with defaults: `speaker_type='bookshelf'`, `volume_db=-60.0`, `zone_id=None` (registry.py:114-124)
   - AC4: Event format verified: `{category: "registry", type: "...", data: {mac_id, client}}` (registry.py:838-846)
   - AC5: Async event processing ensures <100ms timing (verified via integration tests)

2. **Unit Tests Added**: 13 new tests in `TestSnapcastClientDetection` class covering:
   - Client connect/disconnect detection
   - Auto-registration with defaults
   - WebSocket event format compliance
   - `compute_mac_id()` for various client types
   - Async event emission performance

3. **Integration Tests Added**: 9 new tests in `test_snapcast_detection.py` covering:
   - End-to-end connect/disconnect flow
   - NFR2 timing verification (<100ms)
   - Multiple clients and rapid connect/disconnect cycles
   - Local client detection (127.0.0.1 -> "local")

4. **Total Test Count**: 141 tests pass in multiroom/API test files (22 new for this story)

### File List

- `backend/core/multiroom/websocket.py` - SnapcastWebSocketService with `_handle_client_connect()` and `_handle_client_disconnect()` (verified, existing)
- `backend/core/multiroom/registry.py` - ClientRegistryService integration (verified, existing)
- `backend/tests/test_core_multiroom.py` - Added `TestSnapcastClientDetection` class with 13 unit tests
- `backend/tests/integration/test_snapcast_detection.py` - NEW: 9 integration tests for Story 1-3

### Change Log

| Date | Change | Author |
|------|--------|--------|
| 2026-01-18 | Story 1-3: Verified existing Snapcast client detection implementation | Claude Opus 4.5 |
| 2026-01-18 | Added 13 unit tests for Snapcast client detection (AC1-AC4) | Claude Opus 4.5 |
| 2026-01-18 | Added 9 integration tests including NFR2 timing verification | Claude Opus 4.5 |

