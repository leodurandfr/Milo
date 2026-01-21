# Data Models - Backend

> Generated: 2026-01-09 | Scan Level: Deep

## Overview

Milo uses a feature-based architecture with domain models defined in `backend/core/models/`. No traditional database is used - state is persisted to JSON files in `/var/lib/milo/`.

---

## Domain Models

### AudioSource (Enum)
```python
class AudioSource(Enum):
    NONE = "none"
    SPOTIFY = "spotify"
    BLUETOOTH = "bluetooth"
    MAC = "mac"
    RADIO = "radio"
    PODCAST = "podcast"
```

### PluginState (Enum)
```python
class PluginState(Enum):
    STARTING = "starting"    # Plugin starting or restarting
    READY = "ready"          # Plugin started, waiting for connection
    CONNECTED = "connected"  # Plugin connected and operational
    ERROR = "error"          # Plugin in error state
```

### SystemAudioState (Dataclass)
```python
@dataclass
class SystemAudioState:
    active_source: AudioSource = AudioSource.NONE
    plugin_state: PluginState = PluginState.READY
    transitioning: bool = False
    metadata: Dict[str, Any] = None
    error: Optional[str] = None
    multiroom_enabled: bool = False
    dsp_effects_enabled: bool = False
```

**Purpose:** Complete audio system state combining active source, plugin state, metadata, and routing configuration.

---

### RegisteredClient (Dataclass)
```python
@dataclass
class RegisteredClient:
    dsp_id: str                    # Primary identifier ('local' or hostname)
    snapcast_id: str               # Snapcast's internal client ID
    name: str                      # Display name for UI
    host: str                      # Hostname
    ip: str                        # IP address
    available: bool = True         # Connection status
    speaker_type: SpeakerType      # 'satellite'|'bookshelf'|'tower'|'subwoofer'
    crossover_frequency: int = 80  # Hz
    volume_db: float = -60.0       # Current volume in dB
    mute: bool = False             # Mute status
    last_seen: datetime            # Last activity timestamp
```

**Purpose:** Single source of truth for multiroom client information.

---

### Zone (Dataclass)
```python
@dataclass
class Zone:
    id: str                        # Unique zone identifier
    name: str                      # Display name for UI
    client_ids: List[str]          # List of dsp_ids in zone
    crossover_frequency: int = 80  # Zone-wide crossover Hz
    crossover_enabled: bool = True # Crossover active
```

**Purpose:** Groups multiple clients for synchronized DSP and volume control.

---

### RegistryState (Dataclass)
```python
@dataclass
class RegistryState:
    clients: Dict[str, RegisteredClient]
    zones: Dict[str, Zone]
```

**Purpose:** Complete registry snapshot for persistence and sync.

---

### Volume Models (`domain/volume.py`, `domain/volume_state.py`)

```python
@dataclass
class VolumeState:
    mode: str                      # 'direct' or 'multiroom'
    global_volume_db: float        # Global volume (dB)
    global_mute: bool              # Global mute state
    clients: Dict[str, ClientVolume]  # Per-client volumes
    zones: Dict[str, ZoneVolume]   # Per-zone volumes
```

---

### Registry Event Types
```python
class RegistryEventType:
    CLIENT_REGISTERED = "client_registered"
    CLIENT_UNREGISTERED = "client_unregistered"
    CLIENT_UPDATED = "client_updated"
    AVAILABILITY_CHANGED = "availability_changed"
    VOLUME_CHANGED = "volume_changed"
    SPEAKER_TYPE_CHANGED = "speaker_type_changed"
    ZONE_CREATED = "zone_created"
    ZONE_DELETED = "zone_deleted"
    ZONE_UPDATED = "zone_updated"
    ZONE_CLIENT_ADDED = "zone_client_added"
    ZONE_CLIENT_REMOVED = "zone_client_removed"
```

---

## Persistence Layer

### File-based Storage (`/var/lib/milo/`)

| File | Description |
|------|-------------|
| `settings.json` | Central settings (language, volume, screen, routing, dock) |
| `hardware.json` | Hardware configuration (screen type/resolution) |
| `radio_data.json` | Radio favorites and custom stations |
| `radio_images/` | Uploaded station images |
| `podcast_data.json` | Subscriptions, favorites, progress, preferences |
| `routing.env` | ALSA routing environment (auto-generated) |
| `last_volume.json` | Last volume for restoration |
| `backups/` | Binary backups during updates |

### Settings Service

All settings modifications go through `SettingsService`:
- Atomic writes via `os.replace()`
- File locks for concurrent access
- Automatic backups on corruption

---

## State Machine

### UnifiedAudioStateMachine (`infrastructure/state/state_machine.py`)

Central state manager handling:
- Audio source transitions
- Plugin state management
- Event broadcasting
- State persistence

**Key methods:**
- `update_plugin_state(source, state, metadata)` - Update plugin state
- `_broadcast_event(category, type, source, data)` - Broadcast state changes
- `transition_lock` - Protects state transitions

---

## Plugin Architecture

### AudioSourcePlugin Interface
```python
class AudioSourcePlugin(ABC):
    async def initialize(self) -> bool
    async def start(self) -> bool
    async def stop(self) -> bool
    async def get_status(self) -> Dict[str, Any]
    async def handle_command(self, command: str, data: Dict) -> Dict[str, Any]
```

### Registered Plugins
| Source | Plugin | Service |
|--------|--------|---------|
| SPOTIFY | SpotifyPlugin | milo-spotify (go-librespot) |
| BLUETOOTH | BluetoothPlugin | milo-bluealsa |
| MAC | MacPlugin | milo-mac (ROC) |
| RADIO | RadioPlugin | milo-radio (mpv) |
| PODCAST | PodcastPlugin | milo-podcast (mpv) |

---

## Data Flow

```
User Action
    ↓
Frontend Store Action
    ↓
REST API Call
    ↓
Route Handler
    ↓
Service Layer
    ↓
State Machine (update + broadcast)
    ↓
WebSocket Event
    ↓
Frontend Store Update
    ↓
Reactive UI Update
```
