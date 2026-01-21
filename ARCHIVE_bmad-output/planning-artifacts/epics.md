---
stepsCompleted: [1, 2, 3, 4]
status: complete
completedAt: '2026-01-18'
totalEpics: 6
totalStories: 35
frCoverage: '28/28 (100%)'
inputDocuments:
  - "_bmad-output-v2/planning-artifacts/prd-multiroom-dsp.md"
  - "_bmad-output-v2/planning-artifacts/architecture.md"
  - "_bmad-output-v2/analysis/brainstorming-multiroom-dsp-complet.md"
---

# Milo Multiroom/DSP Refactoring - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for Milo Multiroom/DSP Refactoring, decomposing the requirements from the PRD, Architecture, and Brainstorming documents into implementable stories.

## Requirements Inventory

### Functional Requirements

**Client Registry (3 FRs)**
- FR1: System maintains a registry of clients identified by MAC address
- FR2: System tracks client state (online/offline, volume_db, mute, speaker_type)
- FR18: System detects client connection/disconnection events via Snapcast

**Zone Management (4 FRs)**
- FR3: User can create/delete zones with minimum 2 clients (online or offline)
- FR4: Zone stores and shares DSP settings among all member clients
- FR14: Client leaving a zone retains current DSP settings as standalone
- FR15: Client joining a zone adopts zone's DSP settings (overwrites current)

**Volume Control (4 FRs)**
- FR5: User can adjust volume independently for each client
- FR6: User can adjust zone volume (delta applied to all ONLINE clients, preserving relative offsets)
- FR11: System auto-updates startup_volume_db when restore_last_volume=true
- FR12: System applies startup_volume_db on backend restart

**Client Reconnection (4 FRs)**
- FR7: Client reconnecting IN_ZONE with others ONLINE receives zone average volume and zone DSP
- FR8: Client reconnecting IN_ZONE with all others OFFLINE receives startup_volume_db and zone DSP
- FR9: Client reconnecting STANDALONE with others ONLINE receives global volume and saved DSP
- FR10: Client reconnecting STANDALONE alone receives startup_volume_db and saved DSP

**DSP Management (4 FRs)**
- FR16: DSP changes to zone update zone.dsp_settings and apply to all ONLINE clients
- FR19: User can modify DSP filters (EQ bands: frequency, gain, Q) for a client or zone
- FR20: User can enable/disable compressor with configurable parameters for a client or zone
- FR21: User can enable/disable loudness compensation for a client or zone

**DSP Presets (2 FRs)**
- FR22: User can apply a pre-defined preset to a client or zone
- FR23: System auto-saves AND selects "Manual" preset on any filter modification

**Crossover (4 FRs)**
- FR13: System automatically activates crossover when subwoofer is ONLINE in zone
- FR26: System applies highpass filter to satellites/bookshelf/tower based on speaker_type
- FR27: System applies lowpass filter to subwoofer
- FR28: System deactivates crossover when subwoofer goes OFFLINE

**Real-Time Communication (3 FRs)**
- FR17: System broadcasts state changes via WebSocket in real-time
- FR29: Frontend displays current state of all clients, zones, and DSP settings
- FR30: Frontend updates immediately on WebSocket events without polling

### Non-Functional Requirements

**Performance (5 NFRs)**
- NFR1: Volume changes are applied within 100ms of user action
- NFR2: WebSocket state updates reach frontend within 100ms
- NFR3: DSP filter changes are applied to CamillaDSP within 200ms
- NFR4: Client reconnection sync completes within 1 second
- NFR5: Crossover activation/deactivation completes within 500ms

**Reliability (4 NFRs)**
- NFR6: Backend service recovers automatically after crash (systemd restart)
- NFR7: System state persists across backend restarts (settings.json)
- NFR8: No data loss on unexpected shutdown (atomic writes)
- NFR9: WebSocket reconnects automatically on connection loss

**Integration (4 NFRs)**
- NFR10: Compatible with CamillaDSP v2.0+
- NFR11: Compatible with Snapcast server/client
- NFR12: Works with ALSA only (no Pipewire/PulseAudio dependency)
- NFR13: Supports HiFiBerry DAC cards

**Security (3 NFRs)**
- NFR14: API accessible only from local network (CORS restricted)
- NFR15: No authentication required (trusted home network assumption)
- NFR16: No sensitive data stored (no encryption required)

**Maintainability (4 NFRs)**
- NFR17: Code follows Python async/await patterns throughout
- NFR18: All state changes go through central state machine
- NFR19: No legacy/compatibility code retained
- NFR20: Tests cover all FR scenarios (FR1-FR30)

### Additional Requirements

**From Architecture Document:**
- Brownfield refactoring - existing stack conserved (FastAPI, Vue 3, CamillaDSP, Snapcast)
- ClientRegistryService as central service for all client/zone state management
- MAC address format: with colons for storage (`dc:a6:32:7e:d3:43`), without for URLs (`dca6327ed343`)
- Zone ID format: UUID
- WebSocket events with explicit identifiers in `data` field
- Volume zone is calculated by backend (average ONLINE), readonly in frontend
- Zone slider applies delta to each client, preserving relative offsets
- CamillaDSP failure: fail silently, log warning, set `dsp_ready: false`
- Zone creation requires minimum 2 clients (zone persists even if all clients go offline)
- `speaker_type` enum: `satellite | bookshelf | tower | subwoofer`
- `zone.name` max 15 characters UTF-8
- `volume_db` with min/max from settings

**From Brainstorming Document:**
- Zone = DSP settings shared, volume independent per client
- Client = IN_ZONE OR STANDALONE (never both)
- Client = ONLINE OR OFFLINE (mutually exclusive)
- Backend Milo = single source of truth (no frontend storage)
- "local" = client like others (same rules, no special treatment)
- No pending queue architecture - zone.dsp_settings always up to date
- Reconnection reads source of truth directly (zone or standalone)
- Crossover recalculation on every ONLINE status change or speaker_type change

### FR Coverage Map

| FR | Epic | Description |
|----|------|-------------|
| FR1 | Epic 1 | Registre clients par MAC address |
| FR2 | Epic 1 | État client (online/offline, volume_db, mute, speaker_type) |
| FR18 | Epic 1 | Détection connexion/déconnexion via Snapcast |
| FR3 | Epic 2 | Créer/supprimer zones (min 2 clients) |
| FR4 | Epic 2 | Zone partage DSP settings |
| FR14 | Epic 2 | Client quittant zone garde DSP comme standalone |
| FR15 | Epic 2 | Client rejoignant zone adopte DSP de la zone |
| FR5 | Epic 3 | Volume indépendant par client |
| FR6 | Epic 3 | Volume zone = delta (préserve écarts relatifs) |
| FR11 | Epic 3 | Auto-update startup_volume_db si restore=true |
| FR12 | Epic 3 | Backend restart → applique startup_volume_db |
| FR16 | Epic 4 | DSP changes → update zone.dsp_settings + apply ONLINE |
| FR19 | Epic 4 | Modifier filtres EQ (frequency, gain, Q) |
| FR20 | Epic 4 | Enable/disable compressor |
| FR21 | Epic 4 | Enable/disable loudness |
| FR22 | Epic 4 | Apply pre-defined preset |
| FR23 | Epic 4 | Auto-save + select "Manual" on modification |
| FR7 | Epic 5 | Reconnexion IN_ZONE + autres ONLINE → vol=moyenne, dsp=zone |
| FR8 | Epic 5 | Reconnexion IN_ZONE + tous OFFLINE → vol=startup, dsp=zone |
| FR9 | Epic 5 | Reconnexion STANDALONE + autres ONLINE → vol=global, dsp=saved |
| FR10 | Epic 5 | Reconnexion STANDALONE seul → vol=startup, dsp=saved |
| FR13 | Epic 5 | Crossover auto si subwoofer ONLINE |
| FR26 | Epic 5 | Highpass pour satellites/bookshelf/tower |
| FR27 | Epic 5 | Lowpass pour subwoofer |
| FR28 | Epic 5 | Crossover désactivé si subwoofer OFFLINE |
| FR17 | Epic 6 | WebSocket broadcasts temps réel |
| FR29 | Epic 6 | Frontend affiche état clients/zones/DSP |
| FR30 | Epic 6 | Updates immédiats sans polling |

## Epic List

### Epic 1: Client Registry & Identification
En tant qu'utilisateur, je peux voir et identifier tous mes appareils Milo sur le réseau.

**User Outcome:** L'utilisateur voit tous ses appareils Milo, leur état de connexion, et peut les identifier/renommer.

**FRs covered:** FR1, FR2, FR18

---

### Epic 2: Zone Management
En tant qu'utilisateur, je peux grouper mes clients en zones pour une gestion audio commune.

**User Outcome:** L'utilisateur peut organiser ses appareils logiquement (Salon, Cuisine, etc.) et les clients partagent leurs settings DSP au sein d'une zone.

**FRs covered:** FR3, FR4, FR14, FR15

---

### Epic 3: Volume Control
En tant qu'utilisateur, je peux contrôler le volume de chaque client et de chaque zone.

**User Outcome:** Contrôle précis du volume par client, contrôle delta par zone (préserve les écarts relatifs), et restauration intelligente au démarrage.

**FRs covered:** FR5, FR6, FR11, FR12

---

### Epic 4: DSP Filters & Presets
En tant qu'utilisateur, je peux ajuster l'égaliseur, compresseur, loudness et appliquer des préréglages.

**User Outcome:** Personnalisation audio complète avec filtres EQ, compresseur, loudness. Presets pré-définis applicables, preset "Manual" auto-sauvegardé à chaque modification.

**FRs covered:** FR16, FR19, FR20, FR21, FR22, FR23

---

### Epic 5: Reconnection Sync & Crossover
En tant qu'utilisateur, mes clients se synchronisent automatiquement à la reconnexion et le crossover s'active avec mon subwoofer.

**User Outcome:** Expérience "plug and play" - les clients retrouvent leur volume et DSP à la reconnexion selon leur contexte (zone ou standalone). Le crossover s'active/désactive automatiquement selon la présence d'un subwoofer ONLINE.

**FRs covered:** FR7, FR8, FR9, FR10, FR13, FR26, FR27, FR28

---

### Epic 6: Real-Time Frontend
En tant qu'utilisateur, l'interface reflète toujours l'état exact du système en temps réel.

**User Outcome:** Interface réactive et fiable - tous les changements d'état (clients, zones, volume, DSP) sont reflétés immédiatement via WebSocket, sans polling.

**FRs covered:** FR17, FR29, FR30

---

## Epic 1: Client Registry & Identification

En tant qu'utilisateur, je peux voir et identifier tous mes appareils Milo sur le réseau.

### Story 1.1: Define RegisteredClient Model

As a **developer**,
I want **a well-defined RegisteredClient model with all required properties**,
So that **I have a consistent data structure for client state throughout the system**.

**Acceptance Criteria:**

**Given** the backend codebase
**When** I create the RegisteredClient model in `core/multiroom/models.py`
**Then** the model includes: mac_id (str, primary key), name (str), ip (str), online (bool), zone_id (Optional[str]), volume_db (float), mute (bool), speaker_type (Enum: satellite|bookshelf|tower|subwoofer), dsp_settings (DspSettings)
**And** the model uses Pydantic for validation
**And** mac_id format is validated (with colons: `dc:a6:32:7e:d3:43`)

---

### Story 1.2: Implement ClientRegistryService

As a **system**,
I want **a central ClientRegistryService that manages all client state**,
So that **the backend is the single source of truth for client information**.

**Acceptance Criteria:**

**Given** the RegisteredClient model exists
**When** I implement ClientRegistryService in `core/multiroom/registry.py`
**Then** the service maintains a dict of clients keyed by mac_id
**And** the service provides methods: `get_client(mac_id)`, `get_all_clients()`, `update_client(mac_id, updates)`, `register_client(client)`
**And** all changes are persisted to settings.json via SettingsService
**And** the service is registered in dependencies.py as a lazy singleton

---

### Story 1.3: Integrate Snapcast Client Detection

As a **user**,
I want **the system to automatically detect when my Milo devices connect or disconnect**,
So that **I always see the accurate online/offline status of my devices**.

**Acceptance Criteria:**

**Given** ClientRegistryService is implemented
**When** a Snapcast client connects
**Then** ClientRegistryService receives the event and marks the client as `online: true`
**And** a WebSocket event `client_state_changed` is broadcast

**Given** a client is registered and online
**When** the Snapcast client disconnects
**Then** ClientRegistryService marks the client as `online: false`
**And** a WebSocket event `client_state_changed` is broadcast

**Given** a new unknown client connects via Snapcast
**When** its MAC address is not in the registry
**Then** the client is auto-registered with default values (name from Snapcast, speaker_type: bookshelf)

---

### Story 1.4: API Endpoints for Client Registry

As a **frontend application**,
I want **REST API endpoints to retrieve and update client information**,
So that **I can display and manage clients in the UI**.

**Acceptance Criteria:**

**Given** ClientRegistryService is implemented
**When** I call `GET /api/multiroom/clients`
**Then** I receive a list of all registered clients with their current state

**Given** a valid client mac_id
**When** I call `PATCH /api/multiroom/clients/{mac_id}` with `{"name": "New Name"}`
**Then** the client name is updated in the registry
**And** changes are persisted to settings.json
**And** a WebSocket event `client_state_changed` is broadcast

**Given** a valid client mac_id
**When** I call `PATCH /api/multiroom/clients/{mac_id}` with `{"speaker_type": "subwoofer"}`
**Then** the client speaker_type is updated
**And** changes are persisted and broadcast

**Given** an invalid mac_id format in URL
**When** I call any client endpoint
**Then** I receive a 400 Bad Request with validation error

---

### Story 1.5: Frontend Client Registry Display

As a **user**,
I want **to see all my Milo devices listed in the settings interface**,
So that **I can identify and manage my audio devices**.

**Acceptance Criteria:**

**Given** the frontend loads
**When** multiroomStore initializes
**Then** it fetches clients from `GET /api/multiroom/clients`
**And** stores them in reactive state

**Given** clients are loaded in multiroomStore
**When** I open MultiroomSettings
**Then** I see a list of all clients with their name, status (online/offline indicator), and speaker_type

**Given** clients are displayed
**When** a WebSocket `client_state_changed` event is received
**Then** the client list updates immediately without page refresh

**Given** I click to edit a client
**When** I change its name or speaker_type in ClientEdit.vue
**Then** the change is sent via PATCH API and reflected in the UI

---

## Epic 2: Zone Management

En tant qu'utilisateur, je peux grouper mes clients en zones pour une gestion audio commune.

### Story 2.1: Define Zone Model

As a **developer**,
I want **a well-defined Zone model with all required properties**,
So that **I have a consistent data structure for zone management throughout the system**.

**Acceptance Criteria:**

**Given** the backend codebase
**When** I create the Zone model in `core/multiroom/models.py`
**Then** the model includes: zone_id (str, UUID), name (str, max 15 chars), client_ids (List[str]), dsp_settings (DspSettings)
**And** the model uses Pydantic for validation
**And** zone_id is auto-generated as UUID on creation

---

### Story 2.2: Implement Zone CRUD in ClientRegistryService

As a **system**,
I want **zone management methods in ClientRegistryService**,
So that **zones can be created, retrieved, and deleted with proper persistence**.

**Acceptance Criteria:**

**Given** the Zone model exists
**When** I implement zone methods in ClientRegistryService
**Then** the service provides: `create_zone(name, client_ids)`, `delete_zone(zone_id)`, `get_zone(zone_id)`, `get_all_zones()`

**Given** I call `create_zone(name, client_ids)`
**When** client_ids contains at least 2 valid mac_ids
**Then** a new Zone is created with UUID, clients are updated with zone_id, and zone is persisted to settings.json

**Given** I call `create_zone(name, client_ids)`
**When** client_ids contains less than 2 clients
**Then** a validation error is raised

**Given** I call `delete_zone(zone_id)`
**When** the zone exists
**Then** all member clients have their zone_id set to None, and the zone is removed from persistence

---

### Story 2.3: Zone Client Membership Management

As a **user**,
I want **clients to properly handle DSP settings when joining or leaving zones**,
So that **my audio settings are preserved correctly during zone transitions**.

**Acceptance Criteria:**

**Given** a standalone client with custom dsp_settings
**When** the client joins a zone (FR15)
**Then** the client's dsp_settings is overwritten with zone.dsp_settings
**And** the client's zone_id is set to the zone's ID
**And** a WebSocket event `zone_changed` is broadcast

**Given** a client is member of a zone
**When** the client leaves the zone (FR14)
**Then** the client retains a copy of the current zone.dsp_settings as its standalone dsp_settings
**And** the client's zone_id is set to None
**And** if zone has less than 2 clients remaining, the zone persists (clients may be offline)
**And** a WebSocket event `zone_changed` is broadcast

**Given** ClientRegistryService
**When** I implement `add_client_to_zone(mac_id, zone_id)` and `remove_client_from_zone(mac_id)`
**Then** both methods handle the DSP transition logic as specified above

---

### Story 2.4: API Endpoints for Zone Management

As a **frontend application**,
I want **REST API endpoints to create, manage, and delete zones**,
So that **I can provide zone management functionality in the UI**.

**Acceptance Criteria:**

**Given** ClientRegistryService zone methods are implemented
**When** I call `GET /api/multiroom/zones`
**Then** I receive a list of all zones with their members and dsp_settings

**Given** valid zone data
**When** I call `POST /api/multiroom/zones` with `{"name": "Salon", "client_ids": ["mac1", "mac2"]}`
**Then** a new zone is created and returned with its UUID
**And** a WebSocket event `zone_changed` is broadcast

**Given** a valid zone_id
**When** I call `DELETE /api/multiroom/zones/{zone_id}`
**Then** the zone is deleted, clients become standalone
**And** a WebSocket event `zone_changed` is broadcast

**Given** a valid zone_id
**When** I call `POST /api/multiroom/zones/{zone_id}/clients` with `{"mac_id": "new_client_mac"}`
**Then** the client joins the zone with DSP adoption (FR15)

**Given** a valid zone_id and client mac_id
**When** I call `DELETE /api/multiroom/zones/{zone_id}/clients/{mac_id}`
**Then** the client leaves the zone with DSP retention (FR14)

---

### Story 2.5: Frontend Zone Management UI

As a **user**,
I want **to create, edit, and delete zones in the settings interface**,
So that **I can organize my audio devices into logical groups**.

**Acceptance Criteria:**

**Given** zones are loaded in multiroomStore
**When** I open MultiroomSettings
**Then** I see a list of all zones with their member clients

**Given** I click "Create Zone"
**When** I select at least 2 clients and provide a name (max 15 chars)
**Then** the zone is created via POST API and appears in the list

**Given** I view a zone in ZoneEdit.vue
**When** I add a client to the zone
**Then** the client joins via API and UI updates to show the new member

**Given** I view a zone in ZoneEdit.vue
**When** I remove a client from the zone
**Then** the client leaves via API and UI updates accordingly

**Given** I click "Delete Zone"
**When** I confirm the deletion
**Then** the zone is deleted via DELETE API and removed from the list

**Given** zones are displayed
**When** a WebSocket `zone_changed` event is received
**Then** the zone list updates immediately without page refresh

---

## Epic 3: Volume Control

En tant qu'utilisateur, je peux contrôler le volume de chaque client et de chaque zone.

### Story 3.1: Client Volume Control

As a **user**,
I want **to adjust the volume of each client independently**,
So that **I can set different volume levels for different rooms**.

**Acceptance Criteria:**

**Given** a client is ONLINE
**When** I set its volume via VolumeService.set_client_volume(mac_id, volume_db)
**Then** the volume is applied to CamillaDSP within 100ms (NFR1)
**And** client.volume_db is updated in ClientRegistry
**And** changes are persisted to settings.json
**And** a WebSocket event `client_state_changed` is broadcast

**Given** a client is OFFLINE
**When** I set its volume
**Then** client.volume_db is updated and persisted
**And** the volume will be applied when client comes back ONLINE

**Given** VolumeService
**When** I implement `set_client_volume(mac_id, volume_db)` and `get_client_volume(mac_id)`
**Then** volume_db is validated against min/max from settings
**And** volume is applied via CamillaDSPProxy

---

### Story 3.2: Zone Volume Delta

As a **user**,
I want **to adjust zone volume with a single slider that preserves relative client volumes**,
So that **I can quickly raise or lower volume for an entire zone without losing individual balances**.

**Acceptance Criteria:**

**Given** a zone with clients at different volumes (e.g., Client A: -20dB, Client B: -25dB)
**When** I adjust the zone volume slider by +5dB
**Then** delta +5dB is applied to each ONLINE client (Client A: -15dB, Client B: -20dB)
**And** relative offsets are preserved

**Given** a zone with mixed ONLINE/OFFLINE clients
**When** I adjust zone volume
**Then** only ONLINE clients receive the volume change immediately
**And** OFFLINE clients are not modified (they sync on reconnection)

**Given** VolumeService
**When** I implement `set_zone_volume_delta(zone_id, delta_db)`
**Then** the method calculates and applies delta to each ONLINE member
**And** each client's volume_db is updated and persisted
**And** WebSocket events are broadcast for each affected client

**Given** the frontend requests zone volume
**When** I call `get_zone_volume(zone_id)`
**Then** the backend returns the average volume_db of ONLINE clients (readonly, calculated)

---

### Story 3.3: Startup Volume Management

As a **user**,
I want **my system to restore appropriate volume levels after restart**,
So that **I don't get surprised by unexpected volume levels when turning on my audio system**.

**Acceptance Criteria:**

**Given** settings contain `restore_last_volume: true`
**When** any client volume changes
**Then** `startup_volume_db` is auto-updated to current global average volume (FR11)
**And** the new value is persisted to settings.json

**Given** settings contain `restore_last_volume: false`
**When** client volumes change
**Then** `startup_volume_db` remains unchanged (manual configuration)

**Given** the backend starts/restarts
**When** clients are initialized
**Then** `startup_volume_db` from settings is applied to all clients (FR12)
**And** this happens before any user interaction

**Given** VolumeService
**When** I implement startup volume logic
**Then** `initialize()` applies startup_volume_db to all registered clients
**And** `_update_startup_volume()` is called on volume changes when restore=true

---

### Story 3.4: API Endpoints for Volume

As a **frontend application**,
I want **REST API endpoints to control volume for clients and zones**,
So that **I can provide volume control functionality in the UI**.

**Acceptance Criteria:**

**Given** VolumeService is implemented
**When** I call `PATCH /api/volume/client/{mac_id}` with `{"volume_db": -25.0}`
**Then** the client volume is set and response confirms the new value

**Given** a valid zone_id
**When** I call `PATCH /api/volume/zone/{zone_id}` with `{"delta_db": 5.0}`
**Then** delta is applied to all ONLINE clients in the zone
**And** response includes list of affected clients and their new volumes

**Given** a valid client mac_id
**When** I call `PATCH /api/volume/client/{mac_id}` with `{"mute": true}`
**Then** the client is muted (volume applied as -infinity or mute flag)
**And** a WebSocket event is broadcast

**Given** I call `GET /api/volume/settings`
**Then** I receive current startup_volume_db and restore_last_volume values

**Given** I call `PATCH /api/volume/settings` with `{"startup_volume_db": -30.0}`
**Then** the startup volume setting is updated and persisted

---

### Story 3.5: Frontend Volume Controls

As a **user**,
I want **volume sliders in the multiroom interface**,
So that **I can easily adjust volume for individual clients and zones**.

**Acceptance Criteria:**

**Given** MultiroomControl.vue displays clients and zones
**When** I view a client in MultiroomItem.vue
**Then** I see a volume slider showing current volume_db
**And** I see a mute toggle button

**Given** I drag a client volume slider
**When** I release the slider
**Then** the new volume is sent via PATCH API
**And** the slider reflects the confirmed value

**Given** I view a zone in MultiroomItem.vue
**When** the zone has ONLINE clients
**Then** I see a zone volume slider showing average volume
**And** the slider applies delta on change

**Given** volume changes occur (local or remote)
**When** a WebSocket `client_state_changed` event is received
**Then** all volume sliders update immediately to reflect new values

**Given** I toggle mute on a client
**When** I click the mute button
**Then** the mute state is toggled via API and reflected in UI

---

## Epic 4: DSP Filters & Presets

En tant qu'utilisateur, je peux ajuster l'égaliseur, compresseur, loudness et appliquer des préréglages.

### Story 4.1: Define DspSettings Model

As a **developer**,
I want **a well-defined DspSettings model with all DSP properties**,
So that **I have a consistent data structure for audio processing settings**.

**Acceptance Criteria:**

**Given** the backend codebase
**When** I create the DspSettings model in `core/multiroom/models.py`
**Then** the model includes:
- `enabled` (bool) - global DSP bypass toggle
- `filters` (List[EqFilter]) - EQ bands with id, frequency, gain, q, filter_type
- `compressor` (CompressorSettings) - enabled, threshold, ratio, attack, release
- `loudness` (LoudnessSettings) - enabled, reference_level
**And** the model uses Pydantic for validation
**And** default values create a "flat" configuration (no processing)

---

### Story 4.2: Implement DspService

As a **system**,
I want **a DspService that manages DSP settings for clients and zones**,
So that **DSP changes are properly propagated and applied to CamillaDSP**.

**Acceptance Criteria:**

**Given** DspSettings model exists
**When** I implement DspService in `core/dsp/service.py`
**Then** the service provides methods for zone and standalone clients

**Given** a client is IN_ZONE
**When** DSP changes are made to the zone
**Then** zone.dsp_settings is updated (source of truth)
**And** changes are applied to all ONLINE clients via CamillaDSPProxy (FR16)
**And** changes are persisted to settings.json
**And** OFFLINE clients will receive settings on reconnection

**Given** a client is STANDALONE
**When** DSP changes are made to the client
**Then** client.dsp_settings is updated
**And** changes are applied via CamillaDSPProxy
**And** changes are persisted to settings.json

**Given** CamillaDSP is unavailable
**When** DSP changes are requested
**Then** settings are saved but application fails silently with warning log
**And** client.dsp_ready is set to false

---

### Story 4.3: EQ Filter Management

As a **user**,
I want **to adjust equalizer bands for my audio**,
So that **I can shape the sound to my preferences and room acoustics**.

**Acceptance Criteria:**

**Given** a zone or standalone client
**When** I modify an EQ filter (frequency, gain, Q)
**Then** the filter is updated in dsp_settings
**And** changes are applied to CamillaDSP within 200ms (NFR3)
**And** a WebSocket event `dsp_changed` is broadcast

**Given** DspService
**When** I implement `set_filter(target_type, target_id, filter_id, frequency, gain, q)`
**Then** the method validates filter parameters
**And** applies to zone.dsp_settings or client.dsp_settings based on target_type
**And** propagates to ONLINE clients if target is zone

**Given** a 10-band parametric EQ
**When** filters are configured
**Then** each band has: id (0-9), frequency (20-20000 Hz), gain (-12 to +12 dB), Q (0.1-10)

---

### Story 4.4: Compressor & Loudness Control

As a **user**,
I want **to enable and configure compressor and loudness compensation**,
So that **I can have consistent volume levels and enhanced low-volume listening**.

**Acceptance Criteria:**

**Given** a zone or standalone client
**When** I enable/disable the compressor
**Then** compressor.enabled is toggled in dsp_settings
**And** changes are applied to CamillaDSP
**And** a WebSocket event `dsp_changed` is broadcast

**Given** compressor is enabled
**When** I adjust parameters (threshold, ratio, attack, release)
**Then** parameters are validated and applied
**And** changes are persisted and broadcast

**Given** a zone or standalone client
**When** I enable/disable loudness compensation
**Then** loudness.enabled is toggled in dsp_settings
**And** changes are applied to CamillaDSP

**Given** loudness is enabled
**When** I adjust reference_level
**Then** the parameter is validated and applied
**And** changes are persisted and broadcast

---

### Story 4.5: Global DSP Bypass

As a **user**,
I want **to quickly enable/disable all DSP processing (except crossover)**,
So that **I can compare processed vs flat sound or temporarily bypass all effects**.

**Acceptance Criteria:**

**Given** a zone or standalone client
**When** I disable global DSP (dsp_settings.enabled = false)
**Then** all EQ filters are bypassed in CamillaDSP
**And** compressor is bypassed
**And** loudness is bypassed
**And** crossover remains active (managed separately in Epic 5)
**And** a WebSocket event `dsp_changed` is broadcast

**Given** global DSP is disabled
**When** I enable global DSP (dsp_settings.enabled = true)
**Then** all EQ filters, compressor, and loudness are restored to their configured state
**And** changes are applied to CamillaDSP

**Given** DspService
**When** I implement `set_dsp_enabled(target_type, target_id, enabled)`
**Then** the method toggles the bypass state for all non-crossover DSP
**And** underlying settings are preserved (not reset)

---

### Story 4.6: DSP Presets System

As a **user**,
I want **to apply pre-defined audio presets and have my manual changes auto-saved**,
So that **I can quickly switch between sound profiles**.

**Acceptance Criteria:**

**Given** the system has pre-defined presets (e.g., "Flat", "Jazz", "Rock", "Classical", "Bass Boost")
**When** I apply a preset to a zone or client (FR22)
**Then** dsp_settings is overwritten with preset values
**And** changes are applied to CamillaDSP
**And** the active preset name is stored
**And** a WebSocket event `dsp_changed` is broadcast

**Given** a preset is currently active
**When** I modify any filter parameter (FR23)
**Then** the active preset automatically switches to "Manual"
**And** the current settings are auto-saved as the "Manual" preset
**And** WebSocket event reflects preset change to "Manual"

**Given** DspService
**When** I implement `apply_preset(target_type, target_id, preset_name)` and `get_available_presets()`
**Then** presets are loaded from configuration
**And** "Manual" preset is always available and stores last custom settings

**Given** I call `get_available_presets()`
**Then** I receive list of pre-defined presets + "Manual"

---

### Story 4.7: API Endpoints for DSP

As a **frontend application**,
I want **REST API endpoints to control DSP settings for clients and zones**,
So that **I can provide DSP control functionality in the UI**.

**Acceptance Criteria:**

**Given** DspService is implemented
**When** I call `PATCH /api/dsp/zone/{zone_id}/filter/{filter_id}` with `{"gain": 3.0}`
**Then** the filter is updated for the zone and applied to ONLINE clients
**And** response includes updated dsp_settings and list of applied clients

**Given** a valid zone_id
**When** I call `PATCH /api/dsp/zone/{zone_id}/compressor` with `{"enabled": true, "threshold": -20}`
**Then** compressor settings are updated and applied

**Given** a valid zone_id
**When** I call `PATCH /api/dsp/zone/{zone_id}/loudness` with `{"enabled": true}`
**Then** loudness is enabled and applied

**Given** a valid zone_id
**When** I call `PATCH /api/dsp/zone/{zone_id}/enabled` with `{"enabled": false}`
**Then** global DSP bypass is activated (except crossover)

**Given** a valid zone_id
**When** I call `POST /api/dsp/zone/{zone_id}/preset` with `{"preset": "Jazz"}`
**Then** the preset is applied to the zone

**Given** a standalone client mac_id
**When** I call equivalent endpoints at `/api/dsp/client/{mac_id}/...`
**Then** DSP changes are applied to the standalone client only

**Given** I call `GET /api/dsp/presets`
**Then** I receive list of available presets with their configurations

---

### Story 4.8: Frontend DSP Controls

As a **user**,
I want **a comprehensive DSP interface to adjust audio settings**,
So that **I can fine-tune my audio experience visually**.

**Acceptance Criteria:**

**Given** I open DspSettings.vue
**When** ItemSelector.vue loads
**Then** I can select a target: zone or standalone client from multiroomStore
**And** current dsp_settings for selected target are displayed

**Given** a target is selected
**When** I view ParametricEQ.vue
**Then** I see 10 EQ bands with frequency, gain, Q controls
**And** I can adjust each band via sliders or input fields
**And** changes are sent via API on adjustment

**Given** a target is selected
**When** I view AdvancedDsp.vue
**Then** I see compressor controls (enable, threshold, ratio, attack, release)
**And** I see loudness controls (enable, reference_level)
**And** I see global DSP enable/disable toggle

**Given** a target is selected
**When** I view preset selector
**Then** I see dropdown with available presets
**And** selecting a preset applies it via API
**And** current preset name is highlighted

**Given** DSP changes occur
**When** a WebSocket `dsp_changed` event is received
**Then** all DSP controls update immediately to reflect new values

---

## Epic 5: Reconnection Sync & Crossover

En tant qu'utilisateur, mes clients se synchronisent automatiquement à la reconnexion et le crossover s'active avec mon subwoofer.

### Story 5.1: Reconnection Context Detection

As a **system**,
I want **to detect the reconnection context when a client comes back online**,
So that **I can apply the correct volume and DSP settings based on the situation**.

**Acceptance Criteria:**

**Given** a client reconnects (Snapcast event)
**When** ClientRegistryService receives the connection event
**Then** the system determines if client is IN_ZONE or STANDALONE (based on zone_id)

**Given** a client is IN_ZONE
**When** determining reconnection context
**Then** the system checks if other zone members are ONLINE or all OFFLINE

**Given** a client is STANDALONE
**When** determining reconnection context
**Then** the system checks if any other clients are ONLINE globally

**Given** ClientRegistryService
**When** I implement `_get_reconnection_context(mac_id)`
**Then** the method returns one of 4 contexts:
- `IN_ZONE_OTHERS_ONLINE` (FR7)
- `IN_ZONE_ALL_OFFLINE` (FR8)
- `STANDALONE_OTHERS_ONLINE` (FR9)
- `STANDALONE_ALONE` (FR10)

---

### Story 5.2: IN_ZONE Reconnection Sync

As a **user**,
I want **my zone clients to automatically sync with the correct volume and DSP on reconnection**,
So that **they seamlessly rejoin the zone audio experience**.

**Acceptance Criteria:**

**Given** a client reconnects with context `IN_ZONE_OTHERS_ONLINE` (FR7)
**When** `syncClientOnReconnect(mac_id)` is called
**Then** volume is set to the average of other ONLINE zone members
**And** DSP settings are loaded from zone.dsp_settings
**And** both are applied to CamillaDSP
**And** sync completes within 1 second (NFR4)

**Given** a client reconnects with context `IN_ZONE_ALL_OFFLINE` (FR8)
**When** `syncClientOnReconnect(mac_id)` is called
**Then** volume is set to startup_volume_db (first client of the day)
**And** DSP settings are loaded from zone.dsp_settings (persisted)
**And** both are applied to CamillaDSP

**Given** sync is complete
**When** settings are applied
**Then** a WebSocket event `client_state_changed` is broadcast
**And** client state shows updated volume and dsp_ready status

---

### Story 5.3: STANDALONE Reconnection Sync

As a **user**,
I want **my standalone clients to automatically restore their settings on reconnection**,
So that **I have consistent audio experience when devices come back online**.

**Acceptance Criteria:**

**Given** a client reconnects with context `STANDALONE_OTHERS_ONLINE` (FR9)
**When** `syncClientOnReconnect(mac_id)` is called
**Then** volume is set to the global average of all ONLINE clients
**And** DSP settings are loaded from client.dsp_settings (saved standalone settings)
**And** both are applied to CamillaDSP

**Given** a client reconnects with context `STANDALONE_ALONE` (FR10)
**When** `syncClientOnReconnect(mac_id)` is called
**Then** volume is set to startup_volume_db (no reference available)
**And** DSP settings are loaded from client.dsp_settings
**And** both are applied to CamillaDSP

**Given** sync is complete
**When** settings are applied
**Then** a WebSocket event `client_state_changed` is broadcast
**And** client state shows updated volume and dsp_ready status

---

### Story 5.4: Crossover Service Implementation

As a **developer**,
I want **a CrossoverService that calculates crossover filters based on speaker types**,
So that **the system can automatically configure bass management for zones with subwoofers**.

**Acceptance Criteria:**

**Given** the backend codebase
**When** I implement CrossoverService in `core/multiroom/crossover.py`
**Then** the service provides methods to calculate and apply crossover filters

**Given** a zone with speaker_type configuration
**When** I call `calculate_crossover_filters(zone_id)`
**Then** the service returns appropriate filters for each client based on speaker_type:
- `satellite` → highpass (configurable frequency, e.g., 150Hz)
- `bookshelf` → highpass (configurable frequency, e.g., 80Hz)
- `tower` → highpass (configurable frequency, e.g., 60Hz)
- `subwoofer` → lowpass (configurable frequency)

**Given** CrossoverService
**When** crossover settings are stored
**Then** they are persisted in zone.dsp_settings.crossover with:
- `enabled` (bool)
- `frequency` (Hz)
- `filters` (map of mac_id → filter_type)

---

### Story 5.5: Automatic Crossover Activation

As a **user**,
I want **crossover to automatically activate when my subwoofer comes online**,
So that **I get optimal bass management without manual configuration**.

**Acceptance Criteria:**

**Given** a zone without crossover active
**When** a client with speaker_type `subwoofer` comes ONLINE (FR13)
**Then** crossover is automatically activated for the zone
**And** crossover filters are calculated for all zone members
**And** filters are applied within 500ms (NFR5)
**And** a WebSocket event `crossover_changed` is broadcast

**Given** a zone with crossover active
**When** the subwoofer goes OFFLINE (FR28)
**Then** crossover is automatically deactivated
**And** all highpass/lowpass filters are bypassed
**And** clients return to full-range playback
**And** a WebSocket event `crossover_changed` is broadcast

**Given** ClientRegistryService receives ONLINE/OFFLINE events
**When** any client state changes
**Then** CrossoverService.recalculate_zone_crossover(zone_id) is called if client is IN_ZONE

**Given** a client's speaker_type is changed
**When** the change is persisted
**Then** CrossoverService recalculates crossover if client is IN_ZONE

---

### Story 5.6: Crossover Filter Application

As a **system**,
I want **to apply the correct crossover filters to each client via CamillaDSP**,
So that **bass frequencies are properly distributed between main speakers and subwoofer**.

**Acceptance Criteria:**

**Given** crossover is active in a zone (FR26, FR27)
**When** filters are applied to a satellite/bookshelf/tower client
**Then** a highpass filter is configured in CamillaDSP
**And** the cutoff frequency is based on speaker_type configuration

**Given** crossover is active in a zone
**When** filters are applied to a subwoofer client
**Then** a lowpass filter is configured in CamillaDSP
**And** the cutoff frequency matches the zone crossover setting

**Given** CrossoverService
**When** I implement `apply_crossover_to_client(mac_id, filter_config)`
**Then** the method sends filter configuration to CamillaDSPProxy
**And** crossover filters are separate from EQ filters (not affected by DSP bypass)

**Given** crossover is deactivated
**When** filters are removed
**Then** CamillaDSP crossover filters are bypassed
**And** speakers return to full-range operation

**Given** a client reconnects to a zone with active crossover
**When** sync is performed
**Then** crossover filters are also applied based on current zone crossover state

---

## Epic 6: Real-Time Frontend

En tant qu'utilisateur, l'interface reflète toujours l'état exact du système en temps réel.

### Story 6.1: WebSocket Event Broadcasting

As a **backend system**,
I want **to broadcast all state changes via WebSocket in real-time**,
So that **connected frontends can stay synchronized with the current system state**.

**Acceptance Criteria:**

**Given** any client state change (online/offline, volume, mute, speaker_type)
**When** the change is persisted
**Then** a `client_state_changed` WebSocket event is broadcast within 100ms (NFR2)
**And** the event includes the complete updated client object

**Given** any zone change (create, delete, membership, dsp_settings)
**When** the change is persisted
**Then** a `zone_changed` WebSocket event is broadcast
**And** the event includes the complete updated zone object

**Given** any DSP change (filters, compressor, loudness, preset, enabled)
**When** the change is applied
**Then** a `dsp_changed` WebSocket event is broadcast
**And** the event includes target_type, target_id, and updated dsp_settings

**Given** crossover state changes (activated/deactivated)
**When** crossover filters are applied or removed
**Then** a `crossover_changed` WebSocket event is broadcast
**And** the event includes zone_id and crossover state

**Given** WebSocket event structure
**When** events are broadcast
**Then** all events follow the format: `{"category": "multiroom", "type": "{event_type}", "data": {...}}`

---

### Story 6.2: Frontend WebSocket Integration

As a **frontend application**,
I want **a robust WebSocket service that maintains connection and handles reconnection**,
So that **I always receive real-time updates from the backend**.

**Acceptance Criteria:**

**Given** the frontend application loads
**When** WebSocket service initializes
**Then** it connects to the backend WebSocket endpoint
**And** registers handlers for all event types

**Given** WebSocket connection is established
**When** the connection is lost unexpectedly
**Then** the service automatically attempts reconnection (NFR9)
**And** reconnection uses exponential backoff
**And** UI indicates connection status if disconnected

**Given** WebSocket reconnects successfully
**When** connection is restored
**Then** the frontend fetches fresh state from REST API
**And** stores are synchronized with current backend state

**Given** WebSocket service in `services/websocket.js`
**When** events are received
**Then** they are dispatched to appropriate store handlers
**And** event parsing handles malformed messages gracefully

---

### Story 6.3: Multiroom Store Real-Time Sync

As a **frontend application**,
I want **multiroomStore to automatically update on WebSocket events**,
So that **client and zone state is always current without polling**.

**Acceptance Criteria:**

**Given** multiroomStore is initialized
**When** a `client_state_changed` event is received
**Then** the corresponding client in `clients` state is updated
**And** Vue reactivity triggers UI updates automatically

**Given** multiroomStore is initialized
**When** a `zone_changed` event is received
**Then** the corresponding zone in `zones` state is updated (or added/removed)
**And** Vue reactivity triggers UI updates automatically

**Given** multiroomStore is initialized
**When** a `crossover_changed` event is received
**Then** the crossover state for the zone is updated
**And** any crossover UI indicators reflect the new state

**Given** multiroomStore
**When** implementing event handlers
**Then** handlers use `$patch` or direct state mutation for optimal reactivity
**And** no polling or periodic refresh is used (FR30)

---

### Story 6.4: DSP Store Real-Time Sync

As a **frontend application**,
I want **dspStore to automatically update on WebSocket events**,
So that **DSP settings displayed are always current**.

**Acceptance Criteria:**

**Given** dspStore is initialized
**When** a `dsp_changed` event is received for a zone
**Then** the zone's dsp_settings in state are updated
**And** if the zone is currently selected in UI, controls reflect new values

**Given** dspStore is initialized
**When** a `dsp_changed` event is received for a standalone client
**Then** the client's dsp_settings in state are updated
**And** if the client is currently selected in UI, controls reflect new values

**Given** dspStore tracks active preset
**When** a `dsp_changed` event includes preset change to "Manual"
**Then** the preset selector updates to show "Manual" as active

**Given** another user changes DSP settings
**When** local UI is displaying the same target
**Then** local UI updates immediately to show the remote changes
**And** no conflict or overwrite occurs

---

### Story 6.5: UI Real-Time Updates

As a **user**,
I want **the interface to update instantly when system state changes**,
So that **I always see accurate information without refreshing**.

**Acceptance Criteria:**

**Given** MultiroomControl.vue is displayed
**When** a client goes ONLINE or OFFLINE
**Then** the client's status indicator updates immediately
**And** no page refresh or manual action is required

**Given** MultiroomItem.vue displays a client volume
**When** volume is changed from another device or client
**Then** the volume slider updates to the new value immediately

**Given** DspSettings.vue is displayed with a target selected
**When** DSP settings change for that target (from any source)
**Then** all EQ bands, compressor, loudness controls update immediately

**Given** ZoneEdit.vue is displayed
**When** a client joins or leaves the zone from another interface
**Then** the member list updates immediately

**Given** any component displaying multiroom/DSP state
**When** WebSocket events arrive
**Then** updates appear within 100ms (NFR2)
**And** UI remains responsive during updates
**And** no flickering or visual glitches occur
