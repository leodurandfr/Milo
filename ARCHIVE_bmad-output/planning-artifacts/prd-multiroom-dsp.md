---
stepsCompleted: [1, 2, 3, 4, 7, 8, 9, 10, 11]
inputDocuments:
  - "_bmad-output-v2/analysis/brainstorming-multiroom-dsp-complet.md"
workflowType: 'prd'
lastStep: 11
documentCounts:
  brief: 0
  research: 0
  brainstorming: 1
  projectContext: 0
  projectDocs: 0
---

# Product Requirements Document - Milo Multiroom/DSP Refactoring

**Author:** Léo
**Date:** 2026-01-17

## Executive Summary

Milo est un système audio multiroom pour Raspberry Pi actuellement fonctionnel mais souffrant d'instabilités critiques dans la gestion des clients connectés/déconnectés, des zones, et de la synchronisation DSP. Le code actuel a été développé de manière incrémentale, créant des incohérences architecturales et une sur-complexification inutile.

Ce PRD définit un **refactoring complet du cœur audio** visant à :
- Établir le backend comme **source de vérité unique** (ClientRegistry, zones, DSP settings)
- Garantir une **synchronisation parfaite** des clients à la reconnexion
- Simplifier l'architecture pour un frontend **temps réel sans sur-complexification**
- Supprimer tout code legacy au profit d'une implémentation propre et optimisée

### What Makes This Special

Ce refactoring représente une **réécriture architecturale complète** du sous-système audio/multiroom, pas une correction de bugs isolés. L'objectif est d'atteindre une cohérence globale où :

- **Zone = Source de Vérité DSP** : `zone.dsp_settings` est mis à jour à chaque modification et toujours synchronisé
- **Reconnexion simple** : Le client lit directement sa source de vérité (zone ou standalone) - pas de queue complexe
- **Crossover dynamique** : Activation/désactivation automatique basée sur la présence d'un subwoofer ONLINE
- **Frontend passif** : Affichage pur de l'état backend via WebSocket, sans logique de propagation

**Logique de reconnexion :**

| Contexte | Source DSP | Source Volume |
|----------|------------|---------------|
| IN_ZONE + autres ONLINE | zone.dsp_settings | Moyenne zone |
| IN_ZONE + tous OFFLINE | zone.dsp_settings | startup_volume_db |
| STANDALONE + autres ONLINE | client.dsp_settings | volume_global |
| STANDALONE + seul | client.dsp_settings | startup_volume_db |

**Zones ciblées :**
- Volume (contrôle unifié, startup_volume_db, restore)
- Routing (direct + multiroom via Snapcast)
- Multiroom (ClientRegistry, zones, clients ONLINE/OFFLINE)
- DSP (filtres EQ, compressor, loudness, crossover)
- Cohérence plugins (Spotify, Mac, Bluetooth, Radio, Podcasts)

## Project Classification

**Technical Type:** IoT/Embedded + Web App (refactoring architectural)
**Domain:** Consumer Electronics / Audio
**Complexity:** Moyenne
**Project Context:** Brownfield - réécriture complète d'un sous-système existant

**Approche :** Suppression du code instable existant, implémentation nouvelle basée sur les 17 Functional Requirements (FR1-FR17) définis dans l'analyse de brainstorming. Architecture simplifiée sans pending queue - la source de vérité est toujours à jour et lue directement à la reconnexion.

## Success Criteria

### User Success

- **Synchronisation transparente** : Un client qui se reconnecte hérite immédiatement du volume et des filtres DSP de sa zone (ou de ses settings standalone)
- **Crossover automatique** : Activation/désactivation du crossover sans intervention manuelle, basée sur la présence d'un subwoofer ONLINE
- **Temps réel parfait** : Le frontend reflète toujours l'état exact du backend (volume, clients, zones, filtres DSP)
- **Cohérence garantie** : Tous les clients d'une zone partagent les mêmes settings DSP, même après déconnexion/reconnexion

### Technical Success

- **FR1-FR17 implémentés** : Tous les Functional Requirements définis dans le brainstorming sont fonctionnels et testés
- **Zéro code legacy** : Aucun code de compatibilité, migration ou fallback
- **Architecture cohérente** : Backend = source de vérité unique, frontend = affichage passif
- **Zéro erreur 503** : Plus d'appels à des clients indisponibles

### Measurable Outcomes

| Critère | Métrique | Cible |
|---------|----------|-------|
| Reconnexion IN_ZONE | DSP = zone.dsp_settings | 100% |
| Reconnexion STANDALONE | DSP = client.dsp_settings | 100% |
| Crossover auto | Activation < 1s après connexion sub | 100% |
| WebSocket sync | Latence frontend < 100ms | 95%+ |
| Tests FR | Couverture FR1-FR17 | 100% |

## Product Scope

### MVP - Minimum Viable Product

**Core :**
- ClientRegistry comme source de vérité (clients, zones, dsp_settings)
- 4 scénarios de reconnexion (FR7-FR10)
- Volume unifié (par client, delta zone, startup_volume_db)
- DSP settings par zone et par client standalone
- Presets DSP (modification et ajout pour clients et zones, avec preset "manual" )
- Crossover dynamique (FR13)
- WebSocket temps réel

**Exclus du MVP :**
- Nouvelles fonctionnalités audio
- UI redesign

### Growth Features (Post-MVP)

- Historique des configurations
- Backup/restore des settings

### Vision (Future)

- Multi-utilisateurs avec profils audio
- Automatisations (scènes audio)
- Intégration Home Assistant

## User Journeys

### Journey 1: Léo ajuste l'égaliseur de sa zone Salon (client offline)

Léo utilise Milo dans son salon avec trois enceintes en zone : le client local (touchscreen), milo-bureau, et milo-cuisine. Ce soir, milo-cuisine est éteint car personne n'est dans la cuisine.

Léo trouve que les basses manquent de punch pour son album de jazz. Il ouvre l'interface DSP sur le touchscreen et augmente la bande 80Hz de +3dB. Le changement s'applique immédiatement sur le local et milo-bureau - le son s'améliore instantanément.

Le lendemain matin, sa femme allume milo-cuisine pour écouter la radio pendant le petit-déjeuner. L'enceinte se connecte au réseau, et automatiquement, le système détecte qu'elle fait partie de la zone Salon. Les filtres DSP de la zone (incluant le boost de +3dB à 80Hz) sont appliqués. Quand Léo passe dans la cuisine, le son est parfaitement cohérent avec le salon - exactement comme il l'avait réglé.

**Capabilities révélées :** Mise à jour zone.dsp_settings à chaque modification, synchronisation DSP à la reconnexion, cohérence zone garantie.

---

### Journey 2: Client se reconnecte à sa zone (FR7)

Marie écoute de la musique dans son appartement avec deux enceintes en zone. Son wifi redémarre et milo-chambre perd la connexion pendant 2 minutes.

Quand le wifi revient, milo-chambre se reconnecte automatiquement. Le système détecte que l'autre client de la zone (milo-salon) est toujours ONLINE. Le volume de milo-chambre est ajusté à la moyenne de la zone, et les filtres DSP de la zone sont appliqués.

La musique reprend instantanément dans la chambre, parfaitement synchronisée avec le salon.

**Capabilities révélées :** Détection contexte reconnexion, calcul moyenne volume zone, application DSP depuis zone.dsp_settings.

---

### Journey 3: Premier client d'une zone démarre (FR8)

Le matin, tous les appareils Milo de Paul sont éteints. Il allume milo-salon qui fait partie de la zone "Living". Comme c'est le premier client de la zone à démarrer, le système n'a pas de référence de volume des autres clients.

Le volume est initialisé à `startup_volume_db` (-25dB, configuré dans les settings). Les filtres DSP de la zone sont chargés depuis `zone.dsp_settings` et appliqués.

Paul peut commencer à écouter immédiatement, à un volume sûr et prévisible.

**Capabilities révélées :** Détection "premier client", utilisation startup_volume_db, lecture zone.dsp_settings persisté.

---

### Journey 4: Subwoofer rejoint la zone (FR13 - Crossover)

Sophie a une zone "Home Cinema" avec deux enceintes bookshelf. Elle vient d'acheter un subwoofer Milo et le branche.

Le subwoofer se connecte et rejoint la zone. Le système détecte son `speaker_type: subwoofer` et active automatiquement le crossover :
- Subwoofer → filtre lowpass (basses uniquement)
- Bookshelf → filtre highpass (medium/aigus)

Le son s'améliore immédiatement - les basses sont désormais gérées par le sub, libérant les bookshelf pour les fréquences qu'elles reproduisent le mieux.

Plus tard, Sophie débranche le subwoofer pour le prêter à un ami. Le système détecte la déconnexion et désactive automatiquement le crossover - les bookshelf reprennent la reproduction full-range.

**Capabilities révélées :** Détection speaker_type, activation/désactivation crossover dynamique, recalcul automatique à chaque changement d'état.

---

### Journey 5: Utilisateur applique un preset DSP

Thomas a configuré un preset "Soirée" avec des basses boostées et un compresseur léger. Ce soir, il reçoit des amis et veut appliquer ce preset à sa zone Salon.

Il sélectionne le preset dans l'interface, confirme l'application. Le backend met à jour `zone.dsp_settings` avec les valeurs du preset, persiste le changement, et applique les nouveaux filtres à tous les clients ONLINE de la zone.

Un client (milo-terrasse) est éteint car il fait froid dehors. Quand Thomas l'allumera cet été, il recevra automatiquement les derniers settings de la zone.

**Capabilities révélées :** Gestion presets, application atomique à une zone, persistance, cohérence clients OFFLINE.

---

### Journey Requirements Summary

| Journey | Capabilities requises |
|---------|----------------------|
| #1 DSP zone + reconnexion | zone.dsp_settings toujours à jour, sync reconnexion |
| #2 Reconnexion IN_ZONE | Contexte détection, moyenne volume, DSP zone |
| #3 Premier client zone | startup_volume_db, lecture DSP persisté |
| #4 Crossover dynamique | Détection speaker_type, filtres crossover auto |
| #5 Preset DSP | Gestion presets, application zone atomique |

## Technical Requirements (IoT/Embedded + Web App)

### Architecture Overview

| Composant | Technologie | Rôle |
|-----------|-------------|------|
| Backend | FastAPI (Python 3.11+) | Source de vérité, API REST, WebSocket |
| Frontend | Vue 3 + Pinia | Interface utilisateur réactive |
| Audio | ALSA + CamillaDSP | Routing audio, volume, DSP filters |
| Multiroom | Snapcast | Synchronisation audio multi-clients |
| Communication | WebSocket | État temps réel |

### Constraints

- **Local network only** : Pas de cloud, pas d'accès internet requis
- **ALSA uniquement** : Pas de Pipewire/PulseAudio (compatibilité HiFiBerry)
- **Backend = SSOT** : Frontend ne fait qu'afficher/contrôler
- **Async everywhere** : Toutes les opérations I/O doivent être async

### Real-Time Communication

**WebSocket events :**
- `client_state_changed` : ONLINE/OFFLINE, volume, mute
- `zone_changed` : Création, modification, suppression
- `dsp_changed` : Filtres, compressor, loudness, presets
- `crossover_changed` : Activation/désactivation automatique

### Hardware Integration

- **Raspberry Pi 4/5** : Cible principale
- **HiFiBerry DAC** : Sortie audio
- **Touchscreen** : Interface utilisateur (optionnel)
- **WiFi** : Connectivité réseau

## Risk Mitigation Strategy

### Technical Risks

| Risque | Impact | Mitigation |
|--------|--------|------------|
| Régression plugins audio | Haut | Tests d'intégration par plugin (Spotify, Radio, etc.) |
| CamillaDSP latence | Moyen | Benchmarks avant/après refactoring |
| Snapcast sync | Moyen | Tests multi-clients avec reconnexion |

### Implementation Risks

| Risque | Impact | Mitigation |
|--------|--------|------------|
| Scope creep | Haut | FR1-FR17 figés, pas de nouvelles features |
| Code legacy oublié | Moyen | Revue systématique, pas de migration progressive |
| Breaking changes settings.json | Moyen | Script de migration one-shot si nécessaire |

### Validation Approach

1. **Tests unitaires** : Chaque FR avec cas de test
2. **Tests d'intégration** : Scénarios de reconnexion (FR7-FR10)
3. **Tests manuels** : Validation crossover, presets, WebSocket real-time

## Functional Requirements

### Client Registry

- **FR1**: System maintains a registry of clients identified by MAC address
- **FR2**: System tracks client state (online/offline, volume_db, mute, speaker_type)
- **FR18**: System detects client connection/disconnection events via Snapcast

### Zone Management

- **FR3**: User can create/delete zones with minimum 2 clients (online or offline)
- **FR4**: Zone stores and shares DSP settings among all member clients
- **FR14**: Client leaving a zone retains current DSP settings as standalone
- **FR15**: Client joining a zone adopts zone's DSP settings (overwrites current)

### Volume Control

- **FR5**: User can adjust volume independently for each client
- **FR6**: User can adjust zone volume (delta applied to all ONLINE clients, preserving relative offsets)
- **FR11**: System auto-updates startup_volume_db when restore_last_volume=true
- **FR12**: System applies startup_volume_db on backend restart

### Client Reconnection

- **FR7**: Client reconnecting IN_ZONE with others ONLINE receives zone average volume and zone DSP
- **FR8**: Client reconnecting IN_ZONE with all others OFFLINE receives startup_volume_db and zone DSP
- **FR9**: Client reconnecting STANDALONE with others ONLINE receives global volume and saved DSP
- **FR10**: Client reconnecting STANDALONE alone receives startup_volume_db and saved DSP

### DSP Management

- **FR16**: DSP changes to zone update zone.dsp_settings and apply to all ONLINE clients
- **FR19**: User can modify DSP filters (EQ bands: frequency, gain, Q) for a client or zone
- **FR20**: User can enable/disable compressor with configurable parameters for a client or zone
- **FR21**: User can enable/disable loudness compensation for a client or zone

### DSP Presets

- **FR22**: User can apply a pre-defined preset to a client or zone (presets are read-only)
- **FR23**: System auto-saves AND selects "Manual" preset on any filter modification (Manual is the only modifiable preset)

### Crossover

- **FR13**: System automatically activates crossover when subwoofer is ONLINE in zone
- **FR26**: System applies highpass filter to satellites/bookshelf/tower based on speaker_type
- **FR27**: System applies lowpass filter to subwoofer
- **FR28**: System deactivates crossover when subwoofer goes OFFLINE

### Real-Time Communication

- **FR17**: System broadcasts state changes via WebSocket in real-time
- **FR29**: Frontend displays current state of all clients, zones, and DSP settings
- **FR30**: Frontend updates immediately on WebSocket events without polling

## Non-Functional Requirements

### Performance

- **NFR1**: Volume changes are applied within 100ms of user action
- **NFR2**: WebSocket state updates reach frontend within 100ms
- **NFR3**: DSP filter changes are applied to CamillaDSP within 200ms
- **NFR4**: Client reconnection sync completes within 1 second
- **NFR5**: Crossover activation/deactivation completes within 500ms

### Reliability

- **NFR6**: Backend service recovers automatically after crash (systemd restart)
- **NFR7**: System state persists across backend restarts (settings.json)
- **NFR8**: No data loss on unexpected shutdown (atomic writes)
- **NFR9**: WebSocket reconnects automatically on connection loss

### Integration

- **NFR10**: Compatible with CamillaDSP v2.0+
- **NFR11**: Compatible with Snapcast server/client
- **NFR12**: Works with ALSA only (no Pipewire/PulseAudio dependency)
- **NFR13**: Supports HiFiBerry DAC cards

### Security

- **NFR14**: API accessible only from local network (CORS restricted)
- **NFR15**: No authentication required (trusted home network assumption)
- **NFR16**: No sensitive data stored (no encryption required)

### Maintainability

- **NFR17**: Code follows Python async/await patterns throughout
- **NFR18**: All state changes go through central state machine
- **NFR19**: No legacy/compatibility code retained
- **NFR20**: Tests cover all 28 FR scenarios

