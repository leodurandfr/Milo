# Architecture Overview

> Generated: 2026-01-09 | Project: Milo Multiroom Audio System
> This document provides a comprehensive architectural overview for AI-assisted development.

---

## Executive Summary

**Milo** is a multiroom audio system for Raspberry Pi that transforms the device into a smart speaker hub with:
- 5 audio sources (Spotify Connect, Bluetooth, Mac streaming, Radio, Podcasts)
- Synchronized multiroom playback via Snapcast
- 10-band parametric equalizer via CamillaDSP
- Touch-screen interface with Vue 3 SPA
- FastAPI async backend

**Architecture Pattern:** Feature-Based Architecture + Plugin System

---

## System Composition

| Component | Technology | Location | Purpose |
|-----------|------------|----------|---------|
| **Backend** | FastAPI (Python) | `backend/` | API, state management, plugins |
| **Frontend** | Vue 3 + Pinia | `frontend/` | Touch-optimized SPA |
| **Satellite** | FastAPI (Python) | `milo-client/` | Multiroom speakers |
| **DSP Engine** | CamillaDSP | systemd | Audio processing |
| **Multiroom** | Snapcast | systemd | Audio distribution |

---

## Architectural Principles

### 1. Single Source of Truth
`UnifiedAudioStateMachine` is THE authoritative source for all audio state. Never modify state directly - always use state machine methods.

### 2. Plugin Architecture
All audio sources implement the `AudioSourcePlugin` interface, enabling consistent behavior and easy addition of new sources.

### 3. Async-First
All I/O operations use async/await. No blocking calls allowed in the main event loop.

### 4. WebSocket State Sync
All state changes broadcast via WebSocket. Frontend never polls - it reacts to server events.

### 5. Service Registry
All services use a simple dict-based Service Registry with lazy singleton creation (`dependencies.py`).

---

## Backend Architecture

### Feature-Based Structure

```
backend/
├── core/                      # Core infrastructure
│   ├── models/               # Domain models (AudioSource, PluginState, Volume)
│   ├── state.py              # AudioStateMachine (single source of truth)
│   ├── audio_source.py       # AudioSourceProtocol interface
│   ├── settings.py           # SettingsService
│   ├── systemd.py            # SystemdServiceManager
│   ├── volume/               # Volume service + handlers
│   ├── dsp/                  # CamillaDSP service + proxy + sync
│   └── multiroom/            # Snapcast + routing + crossover
├── features/                  # Audio source implementations
│   ├── spotify/              # SpotifySource + routes
│   ├── mac/                  # MacSource + routes
│   ├── bluetooth/            # BluetoothSource + routes
│   ├── radio/                # RadioSource + routes + browser_api
│   └── podcast/              # PodcastSource + routes + taddy_api
├── api/                       # REST API routes
├── ws/                        # WebSocket server + manager
├── hardware/                  # Hardware controllers (rotary, screen)
├── shared/                    # Shared utilities (MpvController)
├── config/                    # Constants
└── dependencies.py            # Service Registry (lazy singletons)
```

### Key Services

| Service | File | Responsibility |
|---------|------|----------------|
| `AudioStateMachine` | `core/state.py` | Central state + event broadcast |
| `SettingsService` | `core/settings.py` | Persistent settings |
| `VolumeService` | `core/volume/service.py` | Volume control orchestration |
| `AudioRoutingService` | `core/multiroom/routing.py` | Direct/multiroom switching |
| `SnapcastService` | `core/multiroom/snapcast.py` | Snapcast JSON-RPC |
| `CamillaDSPService` | `core/dsp/service.py` | DSP WebSocket control |
| `ClientRegistryService` | `core/multiroom/registry.py` | Multiroom client/zone registry |

### Plugin System

```python
# Interface (core/audio_source.py)
class AudioSourceProtocol(Protocol):
    async def initialize(self) -> bool: ...
    async def start(self) -> bool: ...
    async def stop(self) -> bool: ...
    async def get_status(self) -> Dict[str, Any]: ...
    async def handle_command(self, command: str, data: Dict) -> Dict[str, Any]: ...
```

**Registered Plugins** (in `features/`):

| Plugin | Location | Service | IPC |
|--------|----------|---------|-----|
| SpotifySource | `features/spotify/` | milo-spotify (go-librespot) | HTTP API |
| BluetoothSource | `features/bluetooth/` | milo-bluealsa | D-Bus |
| MacSource | `features/mac/` | milo-mac (ROC) | Systemd |
| RadioSource | `features/radio/` | milo-radio (mpv) | Unix socket |
| PodcastSource | `features/podcast/` | milo-podcast (mpv) | Unix socket |

---

## Frontend Architecture

### Component Hierarchy

```
App.vue (root)
├── Dock.vue (bottom navigation)
├── VolumeBar.vue (overlay)
├── AudioPlayer.vue
│   └── AudioSourceView.vue
│       ├── SpotifySource.vue
│       ├── RadioSource.vue
│       ├── PodcastSource.vue
│       └── ...
├── SettingsModal.vue
│   └── SettingsCategory.vue
│       ├── DspSettings.vue
│       ├── VolumeSettings.vue
│       └── ...
└── MultiroomModal.vue
```

### State Management (Pinia)

| Store | Purpose | Key State |
|-------|---------|-----------|
| `unifiedAudioStore` | Audio state | active_source, plugin_state, volume |
| `dspStore` | DSP state | filters, presets, zones, links |
| `radioStore` | Radio state | stations, favorites, playback |
| `podcastStore` | Podcast state | subscriptions, queue, progress |
| `multiroomStore` | Multiroom | clients, volumes |
| `clientRegistryStore` | Registry | clients, zones |
| `settingsStore` | Settings | language, dock_apps |

### Data Flow Pattern

```
WebSocket Event
      │
      ▼
App.vue (event router)
      │
      ▼
Pinia Store (state update)
      │
      ▼
Vue Reactivity (auto-update)
      │
      ▼
Component Re-render
```

---

## Critical Implementation Rules

### DO:
- Always use `state_machine.update_plugin_state()` for state changes
- Always use `state_machine._broadcast_event()` for notifications
- Always use `settings_service.set_setting()` for persistence
- Always use async/await for I/O operations
- Always register plugins in `dependencies.py` before `initialize_services()`

### DON'T:
- Don't modify `state_machine._state` directly
- Don't bypass SettingsService for settings
- Don't use blocking I/O
- Don't hardcode ALSA device names (use env vars)
- Don't modify `dependencies.py` initialization order without understanding circular dependencies

---

## Service Initialization Order

**CRITICAL:** The order in `dependencies.py::initialize_services()` must be preserved:

```python
# 1. Retrieve instances (triggers lazy creation via get_service())
# 2. Resolve circular dependencies via setters:
routing_service.set_plugin_callback()           # Access to plugins
routing_service.set_snapcast_websocket_service() # Lifecycle control
routing_service.set_state_machine()             # Event broadcasting
state_machine.routing_service = routing_service # Circular ref complete

# 3. Register plugins in state_machine (BEFORE async init)
# 4. Parallel async initialization via asyncio.gather()
```

---

## Error Handling Strategy

| Layer | Strategy |
|-------|----------|
| API Routes | Return JSON error response, log exception |
| Services | Raise domain exceptions, let caller handle |
| Plugins | Return error dict, update state to ERROR |
| State Machine | Transition lock with timeout, rollback on failure |
| WebSocket | Catch per-handler, continue serving other events |

---

## Testing Strategy

| Type | Tool | Location |
|------|------|----------|
| Backend Unit | pytest + pytest-asyncio | `backend/tests/` |
| Frontend Unit | Vitest + @vue/test-utils | `frontend/tests/` |
| Schema Validation | Zod | `frontend/src/schemas/` |

**Current coverage:** 40 backend tests passing

---

## Deployment Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    RASPBERRY PI (Main)                        │
│                                                               │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐  │
│  │   nginx     │──────│  Frontend   │      │   Backend   │  │
│  │   :80/:443  │      │   (built)   │      │   :8000     │  │
│  └─────────────┘      └─────────────┘      └──────┬──────┘  │
│                                                    │         │
│  ┌─────────────┐      ┌─────────────┐      ┌──────┴──────┐  │
│  │ HiFiBerry   │◄─────│ CamillaDSP  │◄─────│  Snapcast   │  │
│  │   DAC       │      │   :1234     │      │   :1704     │  │
│  └─────────────┘      └─────────────┘      └─────────────┘  │
│                                                               │
└──────────────────────────────────────────────────────────────┘
          │                                       │
          │ Audio                                 │ Stream
          ▼                                       ▼
     ┌─────────┐                          ┌─────────────┐
     │ Speaker │                          │ Satellites  │
     └─────────┘                          │ (N units)   │
                                          └─────────────┘
```

---

## Related Documentation

| Document | Description |
|----------|-------------|
| [API Contracts](api-contracts-backend.md) | Complete REST API reference |
| [Component Inventory](component-inventory-frontend.md) | Vue component catalog |
| [Data Models](data-models-backend.md) | Domain model reference |
| [Source Tree](source-tree-analysis.md) | Directory structure |
| [Development Guide](development-guide.md) | Developer setup |
| [Integration Architecture](integration-architecture.md) | Multi-part communication |

---

## Quick Reference

### Audio Sources
`none` | `spotify` | `bluetooth` | `mac` | `radio` | `podcast`

### Plugin States
`starting` | `ready` | `connected` | `error`

### Volume Range
`-60 dB` to `0 dB` (default step: 3 dB)

### Ports
- Frontend: 5173 (dev) / 80 (prod)
- Backend: 8000
- CamillaDSP: 1234
- Snapcast: 1704 (stream), 1705 (JSON-RPC)
- Satellites: 8001

### Key Files
- Backend entry: `backend/main.py`
- Service Registry: `backend/dependencies.py`
- State machine: `backend/core/state.py`
- Frontend entry: `frontend/src/main.js`
- Settings: `/var/lib/milo/settings.json`
