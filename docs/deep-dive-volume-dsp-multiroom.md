# Deep-Dive: Volume, DSP & Multiroom Architecture

> Generated: 2026-01-09 | Analysis Level: Exhaustive

## Executive Summary

Milo implements a sophisticated volume and DSP control system with multiroom capabilities. The architecture follows a **Single Source of Truth (SSOT)** pattern where:

- **`ClientRegistryService`** is the SSOT for client/zone state
- **`VolumeService`** orchestrates all volume operations
- **`CamillaDSPService`** handles DSP processing and volume control via WebSocket
- **`CrossoverService`** manages speaker types and crossover filters
- **WebSocket events** synchronize state between backend and frontend

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FRONTEND (Vue 3)                               │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────────────────────┐ │
│  │ dspStore.js    │  │ unifiedAudio   │  │ clientRegistryStore.js         │ │
│  │ - filters      │  │ Store.js       │  │ - clients (Map)                │ │
│  │ - compressor   │  │ - volumeState  │  │ - zones (Map)                  │ │
│  │ - loudness     │  │ - systemState  │  │ - WebSocket sync               │ │
│  │ - delay        │  │                │  │                                │ │
│  │ - zoneCrossover│  │                │  │                                │ │
│  └───────┬────────┘  └───────┬────────┘  └───────────────┬────────────────┘ │
│          │                   │                           │                  │
│          └───────────────────┼───────────────────────────┘                  │
│                              │ WebSocket Events                             │
└──────────────────────────────┼──────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              BACKEND (FastAPI)                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    ClientRegistryService (SSOT)                      │   │
│  │  - _clients: Dict[str, RegisteredClient]                             │   │
│  │  - _zones: Dict[str, Zone]                                           │   │
│  │  - Emits: AVAILABILITY_CHANGED, SPEAKER_TYPE_CHANGED, ZONE_*         │   │
│  └────────────────────────────────┬─────────────────────────────────────┘   │
│                                   │ subscribe()                             │
│            ┌──────────────────────┴──────────────────────┐                  │
│            │                                             │                  │
│            ▼                                             ▼                  │
│  ┌─────────────────────┐                    ┌─────────────────────────────┐ │
│  │   VolumeService     │                    │    CrossoverService         │ │
│  │   - set_volume_db() │                    │    - speaker_types          │ │
│  │   - set_mute()      │                    │    - crossover_frequency    │ │
│  │   - sync_client()   │                    │    - zone crossover logic   │ │
│  └──────────┬──────────┘                    └──────────────┬──────────────┘ │
│             │                                              │                │
│             │              ┌───────────────────────────────┘                │
│             │              │                                                │
│             ▼              ▼                                                │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                      CamillaDSPService                                 │ │
│  │  - WebSocket client to CamillaDSP daemon (port 1234)                   │ │
│  │  - set_volume(dB), set_mute(), set_crossover_filter()                  │ │
│  │  - Filters: EQ bands, loudness, compressor, delay                      │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                   │                                         │
│                                   │ HTTP proxy for remote clients           │
│                                   ▼                                         │
│  ┌────────────────────────────────────────────────────────────────────────┐ │
│  │                       Milo-Client (Satellite)                          │ │
│  │  Port 8001 - FastAPI with local CamillaDSP                             │ │
│  │  Endpoints: /dsp/volume, /dsp/mute, /dsp/crossover, /dsp/lowpass       │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AUDIO LAYER                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────────┐  │
│  │   CamillaDSP    │    │    Snapcast     │    │      ALSA Routing       │  │
│  │   Port 1234     │◄──►│  Server: 1780   │◄──►│  milo_{source}_{mode}   │  │
│  │   WebSocket     │    │  Stream: 1704   │    │  direct/multiroom       │  │
│  │                 │    │                 │    │  _eq suffix for DSP     │  │
│  └─────────────────┘    └─────────────────┘    └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Domain Models

### VolumeState (`backend/domain/volume_state.py`)

```python
@dataclass
class VolumeState:
    """Single source of truth for all volume data."""
    mode: Literal['direct', 'multiroom']
    global_volume_db: float          # Main volume in dB (-60 to 0)
    global_mute: bool
    clients: Dict[str, ClientVolume] # Per-client volumes (multiroom)
    zones: Dict[str, ZoneVolume]     # Zone aggregates

@dataclass
class ClientVolume:
    volume_db: float      # Volume in dB
    offset_db: float      # Per-client offset from global
    mute: bool
    available: bool       # Connection status
```

### RegisteredClient (`backend/domain/client_registry.py`)

```python
@dataclass
class RegisteredClient:
    dsp_id: str           # Primary ID ('local' or hostname/IP)
    snapcast_id: str      # Snapcast internal ID
    name: str             # Display name
    host: str             # Hostname
    ip: str               # IP address
    available: bool       # Connection status
    speaker_type: SpeakerType  # 'satellite', 'bookshelf', 'tower', 'subwoofer'
    crossover_frequency: int   # Hz (80 default)
    volume_db: float
    mute: bool

@dataclass
class Zone:
    id: str
    name: str
    client_ids: List[str]
    crossover_frequency: int
    crossover_enabled: bool
```

---

## Service Layer Analysis

### 1. ClientRegistryService (SSOT)

**Location:** `backend/infrastructure/services/client_registry_service.py`

**Purpose:** Central registry for all multiroom clients and zones. This is the **Single Source of Truth** for:
- Client list with complete metadata
- Zone configuration
- Availability status
- Speaker types

**Key Methods:**
```python
# Client management
async def register_client(client_data: Dict) -> RegisteredClient
async def update_availability(dsp_id: str, available: bool)
async def update_volume(dsp_id: str, volume_db: float, mute: bool)
async def update_speaker_type(dsp_id: str, speaker_type: str, crossover_freq: int)

# Zone management
async def create_zone(zone_id: str, name: str, client_ids: List[str]) -> Zone
async def add_client_to_zone(zone_id: str, dsp_id: str)
async def remove_client_from_zone(zone_id: str, dsp_id: str)
```

**Event Types:**
```python
class RegistryEventType:
    CLIENT_REGISTERED = "client_registered"
    AVAILABILITY_CHANGED = "availability_changed"
    VOLUME_CHANGED = "volume_changed"
    SPEAKER_TYPE_CHANGED = "speaker_type_changed"
    ZONE_CREATED = "zone_created"
    ZONE_UPDATED = "zone_updated"
    ZONE_DELETED = "zone_deleted"
    ZONE_CLIENT_ADDED = "zone_client_added"
    ZONE_CLIENT_REMOVED = "zone_client_removed"
```

### 2. VolumeService

**Location:** `backend/infrastructure/services/volume/volume_service.py`

**Purpose:** Orchestrates all volume operations across direct and multiroom modes.

**Key Responsibilities:**
- Volume conversion (dB ↔ percent ↔ UI slider)
- Coordinating CamillaDSP volume control
- Broadcasting volume state changes
- Handling multiroom volume sync

**Volume Flow (Direct Mode):**
```
User Slider → API → VolumeService → CamillaDSPService → CamillaDSP Daemon
                         ↓
                  WebSocket Broadcast → Frontend stores
```

**Volume Flow (Multiroom Mode):**
```
User Slider → API → VolumeService → CamillaDSPService (local)
                                  → HTTP Proxy → milo-client → CamillaDSP
                         ↓
                  WebSocket Broadcast → Frontend stores
```

### 3. CamillaDSPService

**Location:** `backend/infrastructure/services/dsp/camilladsp_service.py`

**Purpose:** WebSocket client to CamillaDSP daemon. Manages all DSP processing.

**Connection:** `ws://127.0.0.1:1234/jsonrpc`

**Capabilities:**
```python
# Volume control (always active)
async def set_volume(volume: float) -> bool  # -100 to 0 dB
async def set_mute(muted: bool) -> bool

# Parametric EQ
async def set_filter(filter_id, freq, gain, q, filter_type)
async def reset_filters()  # Flat EQ

# Advanced DSP
async def set_compressor(enabled, threshold, ratio, attack, release, makeup_gain)
async def set_loudness(enabled, reference_level, high_boost, low_boost)
async def set_delay(enabled, left, right)  # Channel delay in ms

# Crossover filters (for subwoofer integration)
async def set_crossover_filter(enabled, frequency, q)  # Highpass
async def set_lowpass_filter(enabled, frequency, q)    # For subwoofer
```

**DSP States:**
```python
class DspState(Enum):
    DISCONNECTED = "disconnected"
    INACTIVE = "inactive"   # Connected, no audio
    RUNNING = "running"     # Processing audio
    PAUSED = "paused"
```

**Important:** Volume always works via CamillaDSP even when DSP effects are disabled. The `bypass_effects()` method only bypasses EQ/compressor/loudness, not volume.

### 4. CrossoverService

**Location:** `backend/infrastructure/services/dsp/crossover_service.py`

**Purpose:** Manages speaker types and automatic crossover filter application for subwoofer integration.

**Speaker Types:**
```python
SPEAKER_TYPES = ['satellite', 'bookshelf', 'tower', 'subwoofer']

DEFAULT_CROSSOVER_FREQUENCIES = {
    'satellite': 120,   # Small speakers
    'bookshelf': 80,    # THX standard
    'tower': 50,        # Full-range
    'subwoofer': None   # No highpass
}
```

**Zone Crossover Logic:**
When a zone contains a subwoofer:
1. Non-subwoofer clients get a **highpass filter** (removes bass)
2. Subwoofer gets a **lowpass filter** (only bass)
3. Crossover frequency = MIN of all speaker frequencies in zone

```python
async def apply_zone_crossover(zone_id: str):
    """
    If zone has subwoofer:
    - Speakers: highpass at crossover_frequency
    - Subwoofer: lowpass at crossover_frequency
    If no subwoofer:
    - All clients: no crossover filters
    """
```

**Event Subscription:**
CrossoverService subscribes to ClientRegistryService events:
```python
def set_registry(self, registry: ClientRegistryService):
    registry.subscribe(self._handle_registry_event)
    # Listens for: AVAILABILITY_CHANGED, SPEAKER_TYPE_CHANGED, ZONE_*
```

---

## Multiroom Architecture

### Snapcast Integration

**Components:**
- **Snapserver:** Port 1780 (control), Port 1704 (audio stream)
- **Snapclient:** Receives audio from server
- **Groups:** Logical groupings for zone playback

**SnapcastWebSocketService** (`backend/infrastructure/services/snapcast_websocket_service.py`):
- WebSocket connection to `ws://localhost:1780/jsonrpc`
- Handles client connect/disconnect events
- Volume passthrough: Snapcast volume always 100%, real volume via CamillaDSP

**Client Initialization Flow:**
```
1. Client connects to Snapserver
2. Snapcast sends Client.OnConnect notification
3. SnapcastWebSocketService receives event
4. Register client in ClientRegistryService
5. Set client to Multiroom group
6. Set Snapcast volume to 100% (passthrough)
7. Sync DSP volume from VolumeService
8. Apply pending settings (if any queued while offline)
```

### Satellite Client (milo-client)

**Location:** `milo-client/app/main.py`

**Purpose:** Run on satellite Raspberry Pis for distributed multiroom playback.

**API Endpoints (Port 8001):**
```
PUT /dsp/volume      - Set volume in dB
PUT /dsp/mute        - Set mute state
PUT /dsp/crossover   - Set highpass filter
PUT /dsp/lowpass     - Set lowpass filter (subwoofer)
GET /dsp/status      - Get DSP status
GET /health          - Health check
```

**Communication:** Main Milo server proxies requests to satellites via HTTP.

### Zone Management

**Zone Creation Flow:**
```
1. User links clients in UI
2. POST /api/dsp/links { client_ids, source_client, name }
3. ClientRegistryService.create_zone()
4. CrossoverService receives ZONE_CREATED event
5. apply_zone_crossover() applies filters based on speaker types
6. WebSocket broadcasts zone_created to frontend
```

**Zone Volume Control:**
```python
# Atomic zone delta (prevents race conditions)
POST /api/volume/zone/{zone_id}/delta { delta_db }

# Old approach (3 parallel requests = flicker):
# for client in zone: set_volume(client, new_volume)

# New approach (1 request, backend parallel):
# Backend updates all clients in parallel, broadcasts once
```

---

## Frontend State Management

### clientRegistryStore.js

**Purpose:** Single source of truth for clients and zones in frontend.

**State:**
```javascript
const clients = ref(new Map());  // dsp_id -> client data
const zones = ref(new Map());    // zone_id -> zone data
```

**WebSocket Sync:**
```javascript
function handleRegistryEvent(event) {
    switch (event.type) {
        case 'client_registered':
        case 'availability_changed':
        case 'speaker_type_changed':
        case 'zone_created':
        case 'zone_updated':
        // Update local state from backend events
    }
}
```

### dspStore.js

**Purpose:** Manages DSP state, filters, and multiroom DSP operations.

**Key State:**
```javascript
const filters = ref([]);              // EQ bands
const compressor = ref({...});        // Compressor settings
const loudness = ref({...});          // Loudness settings
const delay = ref({...});             // Channel delay
const zoneCrossover = ref({});        // Zone crossover settings
const selectedTarget = ref('local');  // Current DSP target
const linkedGroups = computed(() => registryStore.zoneList);
const clientTypes = computed(() => /* from registryStore.clients */);
```

**Volume Methods:**
```javascript
// Individual client volume
async function updateClientDspVolume(hostname, volumeDb)

// Atomic zone delta (new approach)
async function applyZoneDelta(zoneId, deltaDb)

// Volume queries (from unifiedAudioStore)
function getClientDspVolume(hostname)
function getClientDspMute(hostname)
```

---

## Data Flow: Volume Change Example

### Direct Mode (Single Client)

```
1. User drags volume slider
2. VolumeBar.vue emits change event
3. unifiedAudioStore.setVolume(percent)
4. POST /api/volume { volume_percent }
5. VolumeService.set_volume_percent()
   └── Convert percent → dB
   └── CamillaDSPService.set_volume(dB)
       └── WebSocket to CamillaDSP daemon
6. _broadcast_volume_state()
7. WebSocket event: volume:volume_changed
8. Frontend stores update reactively
9. UI reflects new volume
```

### Multiroom Mode (Zone)

```
1. User drags zone volume slider
2. MultiroomItem.vue detects zone
3. dspStore.applyZoneDelta(zoneId, deltaDb)
4. POST /api/volume/zone/{zoneId}/delta { delta_db }
5. VolumeService processes:
   └── For each client in zone (parallel):
       └── local: CamillaDSPService.set_volume()
       └── remote: HTTP PUT /dsp/client/{host}/volume
6. _broadcast_volume_state() with zone aggregate
7. WebSocket event: volume:volume_changed
8. Frontend receives single update (no flicker)
```

---

## Crossover Integration Example

### Scenario: Adding Subwoofer to Zone

```
1. User sets client speaker_type = 'subwoofer'
2. PUT /api/dsp/client/{id}/speaker-type
3. ClientRegistryService.update_speaker_type()
4. Emits: SPEAKER_TYPE_CHANGED event
5. CrossoverService receives event
6. _recalculate_zones_for_client(dsp_id)
7. apply_zone_crossover(zone_id):
   - Zone has subwoofer → apply crossover
   - For bookshelf speakers: highpass at 80Hz
   - For subwoofer: lowpass at 80Hz
8. For each client:
   - local: CamillaDSPService.set_crossover_filter()
   - remote: HTTP PUT /dsp/crossover
9. WebSocket broadcast: crossover:zone_crossover_changed
```

---

## Error Handling & Resilience

### Pending Settings Queue

When a satellite client is offline, settings are queued:

```python
# CrossoverService
async def queue_pending_settings(client_id, setting_type, settings):
    self._pending_settings[client_id][setting_type] = settings

# When client reconnects (via AVAILABILITY_CHANGED event):
async def apply_pending_settings(client_id):
    pending = self._pending_settings.pop(client_id)
    # Apply crossover, lowpass, volume, mute settings
```

### Availability Detection

```python
# SnapcastWebSocketService handles:
# - Client.OnConnect → register_client, update_availability(True)
# - Client.OnDisconnect → update_availability(False)
# - Server.OnUpdate → detect availability changes via lastSeen

# CrossoverService recalculates zones when subwoofer disconnects:
# If subwoofer offline → remove highpass from speakers
```

---

## Performance Optimizations

### Volume Throttling (Frontend)

```javascript
const THROTTLE_DELAY = 50;   // 50ms between updates
const FINAL_DELAY = 200;     // 200ms final update after drag ends
```

### Atomic Zone Updates

Old approach caused slider flicker:
```javascript
// 3 parallel requests → 3 broadcasts → 3 state updates
for (const client of zoneClients) {
    await updateClientDspVolume(client, volume);  // BAD
}
```

New approach:
```javascript
// 1 request → backend parallelizes → 1 broadcast
await applyZoneDelta(zoneId, deltaDb);  // GOOD
```

### Cache with Local Storage

```javascript
// clientRegistryStore.js
const CACHE_KEY = 'client_registry_cache';

function loadCache() {
    const cached = localStorage.getItem(CACHE_KEY);
    // Load for instant UI, then fetch fresh from backend
}
```

---

## Configuration Persistence

### Settings Locations

| Setting | Location | Service |
|---------|----------|---------|
| Client speaker types | `multiroom.client_types` | ClientRegistryService |
| Zones (linked groups) | `multiroom.linked_groups` | ClientRegistryService |
| DSP filters | `dsp.filters` | CamillaDSPService |
| Compressor | `dsp.compressor` | CamillaDSPService |
| Loudness | `dsp.loudness` | CamillaDSPService |
| Delay | `dsp.delay` | CamillaDSPService |
| Volume | `last_volume.json` | VolumeService |

### Settings File

All settings stored in `/var/lib/milo/settings.json`:
```json
{
  "multiroom": {
    "linked_groups": [
      { "id": "zone_1", "name": "Living Room", "client_ids": ["local", "milo-client-01"] }
    ],
    "client_types": {
      "local": { "type": "bookshelf", "crossover": 80 },
      "milo-client-01": { "type": "subwoofer", "crossover": null }
    }
  },
  "dsp": {
    "filters": [...],
    "compressor": { "enabled": false, "threshold": -20, ... },
    "loudness": { "enabled": false, ... },
    "delay": { "enabled": false, "left": 0, "right": 0 }
  }
}
```

---

## API Reference (Volume/DSP/Multiroom)

### Volume Endpoints

```
GET  /api/volume                    # Get current volume state
PUT  /api/volume                    # Set global volume (percent)
PUT  /api/volume/db                 # Set global volume (dB)
PUT  /api/volume/mute               # Set global mute
POST /api/volume/zone/{id}/delta    # Atomic zone volume delta
```

### DSP Endpoints

```
GET  /api/dsp/status                # Get DSP status
GET  /api/dsp/filters               # Get EQ filters
PUT  /api/dsp/filter/{id}           # Update single filter
POST /api/dsp/reset                 # Reset all filters to flat

PUT  /api/dsp/compressor            # Update compressor
PUT  /api/dsp/loudness              # Update loudness
PUT  /api/dsp/delay                 # Update delay
PUT  /api/dsp/mute                  # Set DSP mute

GET  /api/dsp/enabled               # Get DSP effects enabled state
PUT  /api/dsp/enabled               # Enable/disable DSP effects

# Client DSP (proxied to satellites)
PUT  /api/dsp/client/{id}/volume    # Set client volume
PUT  /api/dsp/client/{id}/mute      # Set client mute
PUT  /api/dsp/client/{id}/speaker-type
PUT  /api/dsp/client/{id}/crossover-frequency
```

### Registry Endpoints

```
GET  /api/registry/state            # Get full registry state
GET  /api/registry/clients          # List all clients
GET  /api/registry/zones            # List all zones
POST /api/registry/zones            # Create zone
PUT  /api/registry/zones/{id}       # Update zone
DELETE /api/registry/zones/{id}     # Delete zone
```

### Linking Endpoints

```
POST   /api/dsp/links               # Link clients (create zone)
DELETE /api/dsp/links/{client_id}   # Unlink client from zone
DELETE /api/dsp/links/group/{id}    # Delete entire zone
PUT    /api/dsp/links/{id}/name     # Rename zone
GET    /api/dsp/links/{id}/crossover      # Get zone crossover
PUT    /api/dsp/links/{id}/crossover      # Set zone crossover frequency
POST   /api/dsp/links/{id}/crossover/apply # Apply crossover to all clients
GET    /api/dsp/links/{id}/auto-crossover  # Get auto-calculated frequency
```

---

## Conclusion

The Milo volume/DSP/multiroom architecture demonstrates several good practices:

1. **Single Source of Truth:** ClientRegistryService centralizes client/zone state
2. **Event-Driven Updates:** WebSocket events propagate changes to frontend
3. **Resilient Design:** Pending settings queue handles offline clients
4. **Atomic Operations:** Zone delta prevents race conditions
5. **Separation of Concerns:** Clear service boundaries (Volume, DSP, Crossover, Registry)

The architecture effectively handles complex multiroom scenarios including:
- Independent client volumes with zone linking
- Automatic crossover management with subwoofer detection
- Graceful handling of client disconnections
- Settings persistence and restoration
