# Integration Architecture

> Generated: 2026-01-09 | Project: Milo Multiroom Audio System

## Overview

Milo is a multi-part system with three main components communicating via REST API, WebSocket, and audio streaming protocols.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER DEVICES                                    │
│   (Browser, iOS app, Android app, macOS app)                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                            HTTP/WebSocket
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         MILO MAIN UNIT                                       │
│  ┌─────────────┐        ┌─────────────┐        ┌─────────────┐              │
│  │  Frontend   │◄──────►│   Backend   │◄──────►│  Services   │              │
│  │  (Vue 3)    │  REST  │  (FastAPI)  │  IPC   │  (systemd)  │              │
│  │  Port:5173  │   WS   │  Port:8000  │        │             │              │
│  └─────────────┘        └──────┬──────┘        └─────────────┘              │
│                                │                                             │
│         ┌──────────────────────┼──────────────────────┐                     │
│         │                      │                      │                     │
│         ▼                      ▼                      ▼                     │
│  ┌─────────────┐        ┌─────────────┐        ┌─────────────┐              │
│  │  CamillaDSP │        │  Snapcast   │        │   Plugins   │              │
│  │  Port:1234  │        │  Port:1704  │        │  (5 types)  │              │
│  └─────────────┘        └──────┬──────┘        └─────────────┘              │
│                                │                                             │
└────────────────────────────────┼────────────────────────────────────────────┘
                                 │
                          Audio Stream
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SATELLITE CLIENTS                                    │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐          │
│  │ milo-client-01  │    │ milo-client-02  │    │ milo-client-N   │          │
│  │   Port:8001     │    │   Port:8001     │    │   Port:8001     │          │
│  │ + Snapclient    │    │ + Snapclient    │    │ + Snapclient    │          │
│  │ + CamillaDSP    │    │ + CamillaDSP    │    │ + CamillaDSP    │          │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Part Integration Map

| From | To | Protocol | Purpose |
|------|-----|----------|---------|
| Frontend | Backend | REST API | CRUD operations, commands |
| Frontend | Backend | WebSocket | Real-time state sync |
| Backend | CamillaDSP | WebSocket | DSP configuration |
| Backend | Snapcast | JSON-RPC | Client management |
| Backend | Plugins | IPC/Systemd | Audio source control |
| Backend | Satellites | REST API | DSP proxy, updates |
| Snapcast | Satellites | TCP Stream | Audio distribution |

---

## Communication Protocols

### 1. REST API (Frontend → Backend)

**Base URLs:**
- Development: `http://localhost:8000/api`
- Production: `http://milo.local/api`

**Key Endpoints:**
```
/api/audio/*        - Audio source control
/api/volume/*       - Volume management
/api/dsp/*          - DSP configuration
/api/routing/*      - Multiroom routing
/api/snapcast/*     - Snapcast client control
/api/registry/*     - Client/zone registry
/api/settings/*     - System settings
/api/radio/*        - Radio stations
/api/podcast/*      - Podcast subscriptions
/spotify/*          - Spotify control
/bluetooth/*        - Bluetooth control
/roc/*              - Mac streaming control
```

### 2. WebSocket (Frontend ↔ Backend)

**Endpoint:** `ws://milo.local/ws` (or `wss://` for HTTPS)

**Event Categories:**

| Category | Events | Description |
|----------|--------|-------------|
| `system` | `initial_state`, `state_changed`, `ping` | Core audio state |
| `volume` | `volume_changed` | Volume updates |
| `plugin` | `state_changed`, `metadata` | Plugin status |
| `settings` | `language_changed`, `dock_apps_changed`, etc. | Settings sync |
| `radio` | `favorite_added`, `favorite_removed` | Radio state |
| `snapcast` | `client_*` events | Multiroom clients |
| `dsp` | `filter_*`, `preset_*`, `links_changed` | DSP state |
| `routing` | `multiroom_*` | Routing state |
| `registry` | `client_*`, `zone_*` | Registry changes |

**Message Format:**
```json
{
  "category": "system",
  "type": "state_changed",
  "source": "spotify",
  "data": {
    "full_state": {...}
  }
}
```

### 3. CamillaDSP WebSocket (Backend → DSP)

**Endpoint:** `ws://localhost:1234`

**Commands:**
- Get/Set configuration
- Get audio levels
- Apply filters
- Reload configuration

### 4. Snapcast JSON-RPC (Backend → Snapcast)

**Endpoint:** `tcp://localhost:1705`

**Operations:**
- List clients
- Set client volume
- Mute/unmute clients
- Rename clients
- Get server status

### 5. Satellite REST API (Backend → Clients)

**Endpoint:** `http://{hostname}:8001/api`

**Operations:**
- DSP configuration proxy
- Software updates
- Status checks

---

## Data Flow Examples

### Audio Source Change

```
User clicks "Spotify"
        │
        ▼
Frontend: POST /api/audio/source/spotify
        │
        ▼
Backend: state_machine.change_source()
        │
        ├─► Stop current plugin
        │
        ├─► Start Spotify plugin
        │       │
        │       └─► systemctl start milo-spotify
        │
        └─► Broadcast via WebSocket
                │
                ▼
Frontend: Updates UI via store
```

### Volume Change

```
User adjusts volume slider
        │
        ▼
Frontend: POST /api/volume/adjust {delta_db: 3}
        │
        ▼
Backend: volume_service.adjust()
        │
        ├─► Direct mode: ALSA volume
        │
        └─► Multiroom mode:
                │
                ├─► Update local CamillaDSP
                │
                └─► Forward to satellites via HTTP
                        │
                        ▼
                Satellite: Apply to local CamillaDSP
        │
        ▼
Backend: Broadcast volume_changed via WebSocket
        │
        ▼
Frontend: Update volume bar display
```

### Multiroom Enable

```
User enables multiroom
        │
        ▼
Frontend: POST /api/routing/multiroom/true
        │
        ▼
Backend: routing_service.set_multiroom(true)
        │
        ├─► Update MILO_MODE=multiroom in routing.env
        │
        ├─► Start Snapcast server
        │       systemctl start milo-snapserver-multiroom
        │
        ├─► Start Snapcast client
        │       systemctl start milo-snapclient-multiroom
        │
        ├─► Restart active source plugin (to use multiroom ALSA device)
        │
        ├─► Initialize satellite connections
        │
        └─► Broadcast routing_changed via WebSocket
```

---

## Service Dependencies

### Backend Service Graph

```
milo-backend
    │
    ├── milo-camilladsp (DSP processing)
    │
    ├── milo-spotify (BindsTo backend)
    │
    ├── milo-bluetooth (BindsTo backend)
    │   ├── milo-bluealsa
    │   └── milo-bluealsa-aplay
    │
    ├── milo-mac (BindsTo backend)
    │
    ├── milo-radio (BindsTo backend)
    │
    ├── milo-podcast (BindsTo backend)
    │
    └── [Multiroom mode only]
        ├── milo-snapserver-multiroom
        └── milo-snapclient-multiroom
```

### Satellite Service Graph

```
milo-client (FastAPI on port 8001)
    │
    ├── milo-snapclient (receives audio from main)
    │
    └── milo-camilladsp (local DSP processing)
```

---

## Audio Routing

### Direct Mode (Single Speaker)

```
Audio Source → ALSA Device → HiFiBerry DAC → Speaker
```

### Multiroom Mode

```
Audio Source
      │
      ▼
ALSA Loopback Device
      │
      ▼
Snapcast Server (main unit)
      │
      ├──────────────────┬──────────────────┐
      │                  │                  │
      ▼                  ▼                  ▼
Snapclient           Snapclient        Snapclient
(main unit)          (client-01)       (client-02)
      │                  │                  │
      ▼                  ▼                  ▼
CamillaDSP           CamillaDSP        CamillaDSP
(EQ, volume)         (EQ, volume)      (EQ, volume)
      │                  │                  │
      ▼                  ▼                  ▼
HiFiBerry            HiFiBerry         HiFiBerry
      │                  │                  │
      ▼                  ▼                  ▼
  Speaker              Speaker            Speaker
```

---

## ALSA Device Naming Convention

Each audio source has 4 ALSA device variants:

```
milo_{source}_direct          # Direct to amplifier
milo_{source}_direct_eq       # Direct with equalizer
milo_{source}_multiroom       # To Snapcast loopback
milo_{source}_multiroom_eq    # To Snapcast with equalizer
```

**Selection via environment variables:**
```bash
# /var/lib/milo/routing.env
MILO_MODE=direct        # or "multiroom"
MILO_EQUALIZER=         # or "_eq"
```

---

## Client Registry

The `ClientRegistryService` maintains a centralized registry of all multiroom clients:

```python
RegistryState:
  clients: Dict[dsp_id, RegisteredClient]
    - dsp_id: "local" | hostname
    - snapcast_id: Snapcast internal ID
    - speaker_type: satellite|bookshelf|tower|subwoofer
    - crossover_frequency: Hz
    - volume_db: Current volume
    - available: Connection status

  zones: Dict[zone_id, Zone]
    - id: Unique identifier
    - name: Display name
    - client_ids: List of clients in zone
    - crossover_frequency: Zone-wide setting
```

---

## Error Handling & Recovery

### WebSocket Reconnection

```javascript
// Exponential backoff: 1s, 2s, 4s, 8s, 16s, max 30s
reconnectAttempts++;
delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 30000);
```

### Service Recovery

- Plugins are `BindsTo` backend → restart together
- Routing service has automatic rollback on failure
- Settings service has atomic writes + backups

### Satellite Offline Handling

- Registry tracks `available` status per client
- Volume commands skip unavailable clients
- DSP proxy returns cached state for offline clients
