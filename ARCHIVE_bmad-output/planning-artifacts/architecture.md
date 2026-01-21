---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
inputDocuments:
  - "_bmad-output-v2/planning-artifacts/prd-multiroom-dsp.md"
  - "_bmad-output-v2/analysis/brainstorming-multiroom-dsp-complet.md"
workflowType: 'architecture'
project_name: 'milo'
user_name: 'Léo'
date: '2026-01-18'
status: 'complete'
completedAt: '2026-01-18'
---

# Architecture Decision Document

_This document builds collaboratively through step-by-step discovery. Sections are appended as we work through each architectural decision together._

## Project Context Analysis

### Requirements Overview

**Functional Requirements (30 FRs):**

Le PRD définit 30 Functional Requirements organisés en 8 domaines :

| Domaine | FRs | Description architecturale |
|---------|-----|---------------------------|
| Client Registry | FR1-FR2, FR18 | Modèle `RegisteredClient` avec MAC address comme clé primaire, états runtime (online, dsp_ready) |
| Zone Management | FR3-FR4, FR14-FR15 | Modèle `Zone` avec client_ids[] et dsp_settings partagés, logique de transition |
| Volume Control | FR5-FR6, FR11-FR12 | Service de volume avec delta zone, startup_volume_db, restore logic |
| Client Reconnection | FR7-FR10 | State machine pour 4 scénarios de reconnexion avec sources de vérité distinctes |
| DSP Management | FR16, FR19-FR21 | Service DSP avec propagation zone/standalone, interface CamillaDSP |
| DSP Presets | FR22-FR25 | Système de presets avec CRUD et application atomique |
| Crossover | FR13, FR26-FR28 | Détection automatique subwoofer, calcul filtres highpass/lowpass par speaker_type |
| Real-Time Sync | FR17, FR29-FR30 | WebSocket events, frontend réactif sans polling |

**Non-Functional Requirements (20 NFRs):**

| Catégorie | NFRs | Impact architectural |
|-----------|------|---------------------|
| Performance | NFR1-NFR5 | Latences < 100-500ms, async everywhere, no blocking I/O |
| Reliability | NFR6-NFR9 | Atomic writes, systemd recovery, WebSocket reconnect auto |
| Integration | NFR10-NFR13 | Interfaces CamillaDSP, Snapcast, ALSA, HiFiBerry |
| Security | NFR14-NFR16 | CORS local network only, no auth (trusted network) |
| Maintainability | NFR17-NFR20 | Central state machine, async patterns, zéro legacy |

**Scale & Complexity:**

- **Domaine principal** : IoT/Embedded + Web App (refactoring brownfield)
- **Niveau de complexité** : Moyenne
- **Composants architecturaux estimés** : 6-8 services backend, 3-4 stores frontend

### Technical Constraints & Dependencies

**Contraintes hard du système existant :**
- ALSA uniquement (compatibilité HiFiBerry, pas de Pipewire/PulseAudio)
- Local network only (pas de cloud, pas d'auth)
- Backend = Single Source of Truth (SSOT)
- Async/await obligatoire pour toutes les opérations I/O
- Intégration avec CamillaDSP v2+ et Snapcast existants

**Dépendances externes :**
- CamillaDSP : Volume et filtres DSP (EQ, compressor, loudness, crossover)
- Snapcast : Synchronisation audio multiroom, détection ONLINE/OFFLINE
- systemd : Gestion des services, recovery automatique

### Cross-Cutting Concerns Identified

| Concern | Impact | Composants affectés |
|---------|--------|---------------------|
| **State Synchronization** | Tous les changements d'état doivent être broadcast via WebSocket | ClientRegistry, Zone, DSP, Volume → Frontend stores |
| **Reconnection Handling** | 4 scénarios distincts avec sources de vérité différentes | ClientRegistry, Zone, DSP, Volume |
| **DSP Propagation** | Zone vs Standalone logic pour appliquer les settings | DSP Service, Zone Service, CamillaDSP proxy |
| **Atomic Persistence** | Toutes les modifications doivent être persistées atomiquement | SettingsService, settings.json |
| **Crossover Dynamics** | Recalcul automatique à chaque changement d'état client | ClientRegistry, DSP Service, Zone |

## Starter Template Evaluation

### Primary Technology Domain

**Brownfield Refactoring** - Stack technique existant conservé

Ce projet est un refactoring architectural d'un système existant fonctionnel. Aucun nouveau starter template n'est nécessaire.

### Existing Technology Stack (Conservé)

| Composant | Technologie | Version | Rôle |
|-----------|-------------|---------|------|
| Backend Framework | FastAPI | 0.100+ | API REST, WebSocket, async |
| Backend Language | Python | 3.11+ | Async/await, type hints |
| Frontend Framework | Vue | 3.x | Composition API, SFC |
| Frontend State | Pinia | 2.x | Stores réactifs |
| Frontend Build | Vite | 5.x | Dev server, HMR, build |
| Audio DSP | CamillaDSP | 2.0+ | Volume, EQ, compressor, loudness |
| Multiroom | Snapcast | 0.27+ | Sync audio, client detection |
| Audio Routing | ALSA | - | Direct hardware (pas de Pipewire) |
| Service Manager | systemd | - | Process lifecycle, recovery |
| Persistence | JSON files | - | settings.json, atomic writes |

### Architectural Patterns Already Established

**Backend (Python/FastAPI):**
- Service Registry pattern (lazy singletons via `dependencies.py`)
- EventBus pour communication découplée
- AudioStateMachine comme state machine centrale
- Async/await pour toutes les opérations I/O
- Pydantic models pour validation

**Frontend (Vue 3):**
- Composition API avec stores Pinia
- WebSocket pour sync temps réel
- Pas de polling, réactivité pure

**Note:** Ce refactoring conserve ces patterns et les renforce pour le sous-système multiroom/DSP.

## Core Architectural Decisions

### Decision Priority Analysis

**Décisions critiques (bloquent l'implémentation) :**
- Structure ClientRegistry (clients, zones, dsp_settings)
- Logique de reconnexion centralisée
- Propagation DSP zone vs standalone
- API endpoints structure

**Décisions importantes (façonnent l'architecture) :**
- Crossover dynamique par speaker_type
- WebSocket events structure
- Séparation responsabilités services

### Data Architecture

**ClientRegistry Structure :**

| Entité | Clé | Propriétés |
|--------|-----|------------|
| `RegisteredClient` | `mac_id` | name, ip, online, zone_id?, volume_db, mute, speaker_type, dsp_settings (standalone) |
| `Zone` | `zone_id` | name, client_ids[], dsp_settings, volume_db (calculé) |
| `DspSettings` | - | filters[], compressor{}, loudness{}, crossover{} |

**Décisions :**
- **Identifiant client** : MAC address (stable, unique, fourni par Snapcast)
- **DSP storage** : Dans Zone (partagé) ET dans Client (standalone uniquement)
- **Volume client** : Stocké par client (`client.volume_db`)
- **Volume zone** : Calculé par backend (moyenne ONLINE), readonly frontend
- **Slider zone** : Applique delta à chaque client, préserve écarts relatifs

### State Machine - Reconnexion

**Décision** : Toute la logique centralisée dans `ClientRegistryService`

**Méthode** : `syncClientOnReconnect(mac_id)` qui :
1. Détecte le contexte (IN_ZONE/STANDALONE × autres ONLINE/OFFLINE)
2. Détermine source volume (moyenne zone, startup_volume_db, ou global)
3. Détermine source DSP (zone.dsp_settings ou client.dsp_settings)
4. Applique via DspService/VolumeService
5. Recalcule crossover si nécessaire

### DSP Propagation

**Décision** : Séparation des responsabilités

```
ClientRegistryService (QUOI)
    ├── VolumeService.set_volume(mac_id, db) → CamillaDSPProxy
    └── DspService.apply_filters(mac_id, settings) → CamillaDSPProxy
```

**Endpoints séparés** zone vs client :
- `/api/dsp/zone/{zone_id}/...`
- `/api/dsp/client/{mac_id}/...`

**Granularité** : Un endpoint par filtre/paramètre (modifications atomiques)

### Crossover

**Décision** : Fréquence configurable par `speaker_type`

| speaker_type | Filtre | Fréquence |
|--------------|--------|-----------|
| satellite | highpass | configurable (ex: 150Hz) |
| bookshelf | highpass | configurable (ex: 80Hz) |
| tower | highpass | configurable (ex: 60Hz) |
| subwoofer | lowpass | configurable |

**Stockage** : `zone.dsp_settings.crossover`

**Déclenchement recalcul** : `ClientRegistryService` sur changement ONLINE/OFFLINE, join/leave zone, modification speaker_type

### API Design

**Préfixes séparés par domaine :**
- `/api/dsp/` - filtres, compressor, loudness
- `/api/volume/` - contrôle volume
- `/api/multiroom/` - clients, zones, configuration

**MAC address dans URLs** : Sans séparateurs (`dca6327ed343`)
- Stockage/affichage : `dc:a6:32:7e:d3:43`
- URLs : `dca6327ed343`

**Réponse endpoints zone :**
```json
{
  "status": "success",
  "applied_to": ["dca6327ed343"],
  "offline_clients": ["112233445566"],
  "zone_settings_updated": true
}
```

### WebSocket Events

**Structure :**
```json
{
  "category": "multiroom",
  "type": "client_state_changed",
  "data": { /* état complet */ }
}
```

**Events groupés par domaine :**

| Event | Déclencheurs |
|-------|--------------|
| `client_state_changed` | ONLINE/OFFLINE, volume, mute, speaker_type |
| `zone_changed` | Création, modification, suppression, join/leave |
| `dsp_changed` | Filtres, compressor, loudness |
| `crossover_changed` | Activation/désactivation auto |

**Payload** : État complet (frontend remplace, pas de merge)

## Implementation Patterns & Consistency Rules

### Patterns existants (conservés)

**Backend Python :**
- Fonctions/variables : `snake_case`
- Classes : `PascalCase`
- Fichiers : `snake_case.py`

**Frontend Vue :**
- Variables/fonctions : `camelCase`
- Composants : `PascalCase.vue`
- Stores : `camelCase.js`

**API REST :**
- JSON fields : `snake_case`
- Endpoints : `/api/{domain}/{resource}`

### Patterns spécifiques multiroom/DSP

#### Identifiants

| Type | Format | Exemple |
|------|--------|---------|
| MAC address (stockage) | Avec deux-points | `dc:a6:32:7e:d3:43` |
| MAC address (URLs) | Sans séparateurs | `dca6327ed343` |
| Zone ID | UUID | `550e8400-e29b-41d4-a716-446655440000` |

#### Structure backend

```
backend/core/multiroom/
├── registry.py      # ClientRegistryService + syncClientOnReconnect
├── models.py        # RegisteredClient, Zone, DspSettings
├── snapcast.py      # SnapcastService
├── websocket.py     # MultiroomWebSocketService
└── crossover.py     # CrossoverService
```

#### Structure WebSocket events

**Format standard :**
```json
{
  "category": "multiroom",
  "type": "client_state_changed",
  "data": {
    "mac_id": "dc:a6:32:7e:d3:43",
    "client": { /* état complet */ }
  }
}
```

**Events avec cible explicite :**
```json
{
  "category": "multiroom",
  "type": "dsp_changed",
  "data": {
    "target_type": "zone",
    "target_id": "uuid-...",
    "dsp_settings": { ... }
  }
}
```

#### Gestion des erreurs

| Erreur | Comportement |
|--------|--------------|
| CamillaDSP indisponible | Fail silently, log warning, `dsp_ready: false` |
| Zone < 2 clients total | Zone supprimée, client → standalone |

#### Validations

| Champ | Contrainte |
|-------|------------|
| `speaker_type` | Enum strict : `satellite \| bookshelf \| tower \| subwoofer` |
| `zone.name` | Max 15 caractères, UTF-8 |
| `volume_db` | Min/max configurables dans settings |

#### Structure frontend

**Store centralisé :**
- `multiroomStore.js` : clients, zones, volume zone, WebSocket events
- `dspStore.js` : presets, UI DSP (séparé)

### Règles obligatoires pour les agents AI

**MUST :**
- Utiliser MAC avec deux-points partout sauf URLs
- Générer UUID pour les zone_id
- Inclure identifiant explicite dans `data` des events WebSocket
- Centraliser logique multiroom dans `ClientRegistryService`
- Utiliser `multiroomStore.js` pour tout état multiroom frontend

**MUST NOT :**
- Créer de nouveaux stores pour clients/zones
- Utiliser format MAC inconsistant
- Omettre `target_type`/`target_id` dans events DSP
- Gérer reconnexion ailleurs que dans `ClientRegistryService`

### Impact sur composants DSP frontend existants

| Composant | Impact | Action requise |
|-----------|--------|----------------|
| `ItemSelector.vue` | **ÉLEVÉ** | Migrer de `dspStore.availableTargets` vers `multiroomStore.clients/zones` |
| `DspSettings.vue` | Moyen | Adapter WebSocket events au nouveau format |
| `AdvancedDsp.vue` | Faible | API calls vers nouveaux endpoints `/api/dsp/zone|client/` |
| `ParametricEQ.vue` | Faible | Idem |
| `EQBand.vue` | Aucun | Composant UI pur, pas de changement |
| `LevelMeters.vue` | Faible | Utiliser `multiroomStore` pour client IDs |

**Note :** `ItemSelector.vue` actuellement récupère les zones depuis `dspStore.getLinkedClientIds()`. Après refactoring, cette logique sera dans `multiroomStore` qui expose directement `clients` et `zones` depuis le backend.

### Impact sur composants Multiroom frontend existants

| Composant | Impact | Action requise |
|-----------|--------|----------------|
| `MultiroomControl.vue` | **ÉLEVÉ** | Migrer zone logic de `dspStore.linkedGroups` vers `multiroomStore.zones` |
| `MultiroomItem.vue` | Moyen | Adapter props pour nouveau format Zone (zone_id UUID, volume_db) |
| `MultiroomModal.vue` | Faible | Aucun changement majeur (wrapper) |
| `MultiroomSettings.vue` | Moyen | Utiliser nouveaux endpoints `/api/multiroom/` |
| `ClientEdit.vue` | Moyen | Adapter formulaire au nouveau modèle `RegisteredClient` |
| `ZoneEdit.vue` | **ÉLEVÉ** | Adapter au nouveau modèle `Zone` avec `zone_id` UUID |

**Note :** `MultiroomControl.vue` actuellement utilise :
- `dspStore.linkedGroups` pour les zones
- `dspStore.getClientDspVolume()` pour les volumes
- `dspStore.applyZoneDelta()` pour le volume zone

Après refactoring, tout ceci sera centralisé dans `multiroomStore` qui expose `clients`, `zones`, et les méthodes de volume.

## Project Structure & Boundaries

### Structure impactée par le refactoring

```
backend/
├── core/
│   ├── multiroom/                    # REFACTORING PRINCIPAL
│   │   ├── __init__.py
│   │   ├── registry.py               # ClientRegistryService (FR1-FR10, FR14-FR18)
│   │   ├── models.py                 # RegisteredClient, Zone, DspSettings
│   │   ├── snapcast.py               # SnapcastService (adapté)
│   │   ├── websocket.py              # MultiroomWebSocketService
│   │   └── crossover.py              # CrossoverService (FR13, FR26-FR28)
│   │
│   ├── dsp/                          # ADAPTÉ
│   │   ├── service.py                # DspService (FR16, FR19-FR21)
│   │   ├── proxy.py                  # CamillaDSPProxy
│   │   ├── presets.py                # PresetsService (FR22-FR25)
│   │   └── sync.py                   # DSP sync logic
│   │
│   ├── volume/                       # ADAPTÉ
│   │   ├── service.py                # VolumeService (FR5-FR6, FR11-FR12)
│   │   └── state.py                  # VolumeState
│   │
│   └── settings.py                   # SettingsService
│
├── api/                              # NOUVEAUX/ADAPTÉS ENDPOINTS
│   ├── multiroom.py                  # /api/multiroom/...
│   ├── dsp.py                        # /api/dsp/zone/..., /api/dsp/client/...
│   └── volume.py                     # /api/volume/...
│
└── tests/
    ├── test_client_registry.py
    ├── test_zone_management.py
    ├── test_crossover.py
    └── integration/
        └── test_reconnection.py

frontend/
├── src/
│   ├── stores/
│   │   ├── multiroomStore.js         # REFACTORISÉ (centralisé)
│   │   └── dspStore.js               # ADAPTÉ
│   │
│   ├── components/
│   │   ├── multiroom/                    # ADAPTÉS (composants multiroom principal)
│   │   │   ├── MultiroomControl.vue      # Liste clients/zones avec volume sliders
│   │   │   ├── MultiroomItem.vue         # Item client/zone (volume, mute, expand)
│   │   │   └── MultiroomModal.vue        # Modal wrapper avec toggle enable
│   │   │
│   │   ├── settings/categories/
│   │   │   ├── DspSettings.vue           # ADAPTÉ (wrapper DSP principal)
│   │   │   │
│   │   │   ├── dsp/                      # ADAPTÉS (composants DSP)
│   │   │   │   ├── ItemSelector.vue      # Zone/client tabs → utiliser multiroomStore
│   │   │   │   ├── ParametricEQ.vue      # 10-band EQ display
│   │   │   │   ├── EQBand.vue            # Individual EQ band
│   │   │   │   ├── AdvancedDsp.vue       # Compressor, Loudness
│   │   │   │   └── LevelMeters.vue       # VU meters
│   │   │   │
│   │   │   └── multiroom/                # ADAPTÉS (settings multiroom)
│   │   │       ├── MultiroomSettings.vue # Page settings multiroom
│   │   │       ├── ClientEdit.vue        # Édition client (name, speaker_type)
│   │   │       └── ZoneEdit.vue          # Édition zone (name, clients)
│   │   │
│   │   └── ...
│   │
│   └── schemas/
│       └── api.js                    # Schémas Zod
```

### Requirements to Structure Mapping

| FR | Fichier(s) backend | Fichier(s) frontend |
|----|-------------------|---------------------|
| FR1-FR2 (Client Registry) | `core/multiroom/registry.py`, `models.py` | `stores/multiroomStore.js` |
| FR3-FR4 (Zone Management) | `core/multiroom/registry.py` | `stores/multiroomStore.js`, `components/multiroom/*.vue`, `settings/categories/multiroom/*.vue` |
| FR5-FR6 (Volume) | `core/volume/service.py` | `stores/multiroomStore.js`, `components/multiroom/MultiroomControl.vue`, `MultiroomItem.vue` |
| FR7-FR10 (Reconnection) | `core/multiroom/registry.py` | - |
| FR11-FR12 (Startup Volume) | `core/volume/service.py` | - |
| FR13, FR26-FR28 (Crossover) | `core/multiroom/crossover.py` | `components/multiroom/` |
| FR14-FR15 (Zone Transitions) | `core/multiroom/registry.py` | `stores/multiroomStore.js` |
| FR16, FR19-FR21 (DSP) | `core/dsp/service.py` | `stores/dspStore.js`, `components/settings/categories/DspSettings.vue`, `dsp/*.vue` |
| FR17, FR29-FR30 (WebSocket) | `ws/`, `core/multiroom/websocket.py` | `services/websocket.js` |
| FR18 (Snapcast Events) | `core/multiroom/snapcast.py` | - |
| FR22-FR25 (Presets) | `core/dsp/presets.py` | `stores/dspStore.js` |

### Architectural Boundaries

**Service Boundaries :**

| Service | Responsabilité | Dépend de |
|---------|----------------|-----------|
| `ClientRegistryService` | État clients/zones, logique reconnexion | VolumeService, DspService |
| `VolumeService` | Volume CamillaDSP | CamillaDSPProxy |
| `DspService` | Filtres DSP | CamillaDSPProxy |
| `CrossoverService` | Calcul crossover automatique | ClientRegistryService |
| `SnapcastService` | Détection ONLINE/OFFLINE | ClientRegistryService |

**Data Boundaries :**

| Donnée | Persistance | Runtime |
|--------|-------------|---------|
| `clients` | settings.json | ClientRegistryService |
| `zones` | settings.json | ClientRegistryService |
| `online`, `dsp_ready` | - | Mémoire (via Snapcast) |
| `zone.volume_db` | - | Calculé par backend |

### Integration Points

**Snapcast → Backend :**
- Event `client.connected` → `ClientRegistryService.on_client_connected()`
- Event `client.disconnected` → `ClientRegistryService.on_client_disconnected()`

**Backend → CamillaDSP :**
- `VolumeService` → `CamillaDSPProxy.set_volume()`
- `DspService` → `CamillaDSPProxy.set_filter()`

**Backend → Frontend (WebSocket) :**
- `client_state_changed`, `zone_changed`, `dsp_changed`, `crossover_changed`

## Architecture Validation Results

### Coherence Validation ✅

**Decision Compatibility :**
Toutes les décisions technologiques sont compatibles - stack existant conservé (FastAPI, Vue 3, CamillaDSP, Snapcast). Aucun conflit entre composants.

**Pattern Consistency :**
- Backend : snake_case cohérent
- Frontend : camelCase cohérent
- API JSON : snake_case cohérent
- WebSocket events : structure unifiée

**Structure Alignment :**
Structure projet alignée avec les décisions - ClientRegistryService central, séparation Volume/DSP, boundaries claires.

### Requirements Coverage Validation ✅

**Functional Requirements (30 FRs) :**
Tous les FRs sont mappés à des composants spécifiques dans la structure projet. Voir section "Requirements to Structure Mapping".

**Non-Functional Requirements :**
- Performance < 100ms : Async everywhere
- Reliability : Atomic writes via SettingsService
- Integration : Interfaces CamillaDSP et Snapcast documentées
- Maintainability : SSOT, patterns clairs

### Implementation Readiness Validation ✅

**Decision Completeness :**
- ✅ Toutes les décisions critiques documentées
- ✅ Rationale fourni pour chaque décision
- ✅ Versions technos spécifiées

**Structure Completeness :**
- ✅ Fichiers spécifiques nommés (pas de placeholders)
- ✅ Boundaries service-to-service définies
- ✅ Integration points mappés

**Pattern Completeness :**
- ✅ Naming conventions complètes
- ✅ Event structures avec exemples JSON
- ✅ Error handling patterns
- ✅ MUST/MUST NOT rules

### Gap Analysis Results

**Critical Gaps :** Aucun

**Important Gaps :**
- DSP Presets : Détails d'implémentation à définir dans les stories
- Test patterns : Suivre les patterns pytest existants

**Minor Gaps :**
- Diagrammes de séquence optionnels

### Architecture Completeness Checklist

**✅ Requirements Analysis**
- [x] Project context analysé (30 FRs, 20 NFRs)
- [x] Complexité évaluée (Moyenne)
- [x] Contraintes techniques identifiées (ALSA, SSOT, async)
- [x] Cross-cutting concerns mappés

**✅ Architectural Decisions**
- [x] Data architecture (ClientRegistry, Zone, DspSettings)
- [x] State machine reconnexion (4 scénarios)
- [x] DSP propagation (zone vs standalone)
- [x] API design (préfixes séparés)
- [x] WebSocket events (groupés, état complet)

**✅ Implementation Patterns**
- [x] Naming conventions (MAC, zone_id, snake/camel)
- [x] Structure patterns (fichiers backend/frontend)
- [x] Communication patterns (events explicites)
- [x] Error handling patterns (fail silently, zone < 2)
- [x] Validation patterns (speaker_type enum, zone name max)

**✅ Project Structure**
- [x] Structure complète définie
- [x] Boundaries établies
- [x] Integration points mappés
- [x] FRs to structure mapping

### Architecture Readiness Assessment

**Overall Status :** READY FOR IMPLEMENTATION

**Confidence Level :** HIGH

**Key Strengths :**
- SSOT clairement défini (backend)
- Patterns cohérents backend/frontend
- 4 scénarios reconnexion documentés
- Crossover automatique bien spécifié

**Areas for Future Enhancement :**
- DSP Presets : détails dans stories
- Monitoring/observability (post-MVP)

## Architecture Completion Summary

### Workflow Completion

**Architecture Decision Workflow:** COMPLETED ✅
**Total Steps Completed:** 8
**Date Completed:** 2026-01-18
**Document Location:** `_bmad-output-v2/planning-artifacts/architecture.md`

### Final Architecture Deliverables

**📋 Complete Architecture Document**

- All architectural decisions documented with specific versions
- Implementation patterns ensuring AI agent consistency
- Complete project structure with all files and directories
- Requirements to architecture mapping
- Validation confirming coherence and completeness

**🏗️ Implementation Ready Foundation**

- 12+ architectural decisions made (data architecture, reconnection, DSP, crossover, API, WebSocket)
- 15+ implementation patterns defined (naming, structure, events, error handling)
- 6 architectural service boundaries specified
- 30 functional requirements fully supported

**📚 AI Agent Implementation Guide**

- Technology stack verified (FastAPI, Vue 3, CamillaDSP, Snapcast)
- Consistency rules that prevent implementation conflicts
- Project structure with clear boundaries
- Integration patterns and communication standards

### Implementation Handoff

**For AI Agents:**
This architecture document is your complete guide for implementing the Milo multiroom/DSP refactoring. Follow all decisions, patterns, and structures exactly as documented.

**First Implementation Priority:**
`ClientRegistryService` in `backend/core/multiroom/registry.py` - the central service for all client/zone state management.

**Development Sequence:**

1. Implement core models (`RegisteredClient`, `Zone`, `DspSettings`)
2. Implement `ClientRegistryService` with reconnection logic
3. Adapt `VolumeService` and `DspService` to use new structure
4. Implement `CrossoverService` for automatic crossover calculation
5. Create/adapt API endpoints (`/api/multiroom/`, `/api/dsp/`, `/api/volume/`)
6. Refactor `multiroomStore.js` frontend store
7. Update WebSocket events to new structure

### Quality Assurance Checklist

**✅ Architecture Coherence**

- [x] All decisions work together without conflicts
- [x] Technology choices are compatible (brownfield - existing stack)
- [x] Patterns support the architectural decisions
- [x] Structure aligns with all choices

**✅ Requirements Coverage**

- [x] All 30 functional requirements are supported
- [x] All 20 non-functional requirements are addressed
- [x] Cross-cutting concerns are handled
- [x] Integration points are defined

**✅ Implementation Readiness**

- [x] Decisions are specific and actionable
- [x] Patterns prevent agent conflicts
- [x] Structure is complete and unambiguous
- [x] Examples are provided for clarity

### Project Success Factors

**🎯 Clear Decision Framework**
Every technology choice was made collaboratively with clear rationale, ensuring all stakeholders understand the architectural direction.

**🔧 Consistency Guarantee**
Implementation patterns and rules ensure that multiple AI agents will produce compatible, consistent code that works together seamlessly.

**📋 Complete Coverage**
All 30 project requirements are architecturally supported, with clear mapping from business needs to technical implementation.

**🏗️ Solid Foundation**
The brownfield refactoring builds on the existing stable stack while introducing clean architectural boundaries for multiroom/DSP.

---

**Architecture Status:** READY FOR IMPLEMENTATION ✅

**Next Phase:** Begin implementation using the architectural decisions and patterns documented herein.

**Document Maintenance:** Update this architecture when major technical decisions are made during implementation.

