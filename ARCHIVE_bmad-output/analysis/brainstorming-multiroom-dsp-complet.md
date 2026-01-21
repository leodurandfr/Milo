# Brainstorming Multiroom/DSP - Résultats Complets

> Document extrait de la session de brainstorming First Principles + Morphological Analysis + Mind Mapping
> Date originale: 2026-01-16
> Extraction: 2026-01-17

---

## 1. First Principles - Vérités Fondamentales

### 1.1 Les 8 Vérités Absolues

| # | Vérité | Description |
|---|--------|-------------|
| 1 | **Client = appareil physique avec CamillaDSP** | Chaque client est un appareil Milo avec CamillaDSP |
| 2 | **CamillaDSP gère TOUT l'audio** | Volume + filtres EQ + compressor + loudness |
| 3 | **Zone = DSP settings partagés, volume indépendant** | EQ/loudness/compressor partagés, mais chaque client garde son volume |
| 4 | **Client = IN_ZONE OU STANDALONE** | Jamais les deux simultanément |
| 5 | **Client = ONLINE OU OFFLINE** | États mutuellement exclusifs |
| 6 | **Backend Milo = source de vérité unique** | Pas de stockage frontend |
| 7 | **Frontend = affichage/contrôle uniquement** | Pas de persistance |
| 8 | **"local" = client comme les autres** | Mêmes règles, pas de traitement spécial |

### 1.2 Entités Minimales

```
┌─────────────────────────────────────────────────────────────────┐
│ CLIENT                                                          │
│ • mac_id (PK) - identifiant stable via MAC address              │
│ • name - nom d'affichage éditable                               │
│ • ip - adresse IP                                               │
│ • online - état de connexion                                    │
│ • zone_id? - appartenance zone (null si standalone)             │
│ • volume_db - volume individuel                                 │
│ • mute - état mute                                              │
│ • speaker_type - satellite|bookshelf|tower|subwoofer            │
├─────────────────────────────────────────────────────────────────┤
│ ZONE                                                            │
│ • id (PK) - identifiant unique                                  │
│ • name - nom d'affichage                                        │
│ • client_ids[] - liste des mac_id membres                       │
│ • dsp_settings - settings DSP partagés                          │
├─────────────────────────────────────────────────────────────────┤
│ DSP_SETTINGS                                                    │
│ • filters[] - EQ bands (id, freq, gain, q, filter_type)         │
│ • compressor - {enabled, threshold, ratio, attack, release}     │
│ • loudness - {enabled, reference_level}                         │
├─────────────────────────────────────────────────────────────────┤
│ STANDALONE_DSP                                                  │
│ • Même structure que DSP_SETTINGS                               │
│ • Stocké par mac_id pour clients hors-zone                      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. Morphological Analysis - Matrices Complètes

### 2.1 Matrice des États Client

| # | Online | Zone | DSP Source | Volume Source |
|---|--------|------|------------|---------------|
| 1 | ✅ ONLINE | IN_ZONE | Zone settings | Propre volume_db |
| 2 | ✅ ONLINE | STANDALONE | Ses settings sauvegardés | Propre volume_db |
| 3 | ❌ OFFLINE | IN_ZONE | *(attend reconnexion)* | *(attend reconnexion)* |
| 4 | ❌ OFFLINE | STANDALONE | *(attend reconnexion)* | *(attend reconnexion)* |

### 2.2 Matrice de Reconnexion - LES 4 SCÉNARIOS

| # | Contexte | Volume | DSP | FR |
|---|----------|--------|-----|-----|
| **1** | IN_ZONE + autres clients ONLINE | **Moyenne zone** | Settings zone | FR7 |
| **2** | IN_ZONE + tous autres OFFLINE | **startup_volume_db** | Settings zone (persistés) | FR8 |
| **3** | STANDALONE + autres clients ONLINE | **volume_global** | Ses settings sauvegardés | FR9 |
| **4** | STANDALONE + aucun autre client | **startup_volume_db** | Ses settings sauvegardés | FR10 |

### 2.3 Matrice des Actions Volume

| Action | Cible | Effet |
|--------|-------|-------|
| Changer volume | Client (dans zone) | Change CE client uniquement |
| Changer volume | Zone (slider zone) | **Delta** appliqué à tous clients ONLINE (écarts préservés) |
| Changer volume | Client standalone | Change CE client uniquement |

### 2.4 Matrice des Actions DSP

| Action | Clients ONLINE | Clients OFFLINE | Persistance |
|--------|----------------|-----------------|-------------|
| Modifier DSP zone | Appliqué immédiatement | **zone.dsp_settings mis à jour → appliqué à reconnexion** | Zone settings → settings.json |
| Modifier DSP standalone | Appliqué immédiatement | N/A | Client settings → settings.json |

### 2.5 Matrice des Transitions Zone

| Transition | Comportement DSP | Comportement Volume |
|------------|------------------|---------------------|
| Client **rejoint** zone | DSP écrasé par settings zone | Volume = moyenne zone |
| Client **quitte** zone | Garde DSP actuel comme standalone | Garde son volume actuel |
| Zone supprimée | Tous clients gardent DSP actuels | Volumes inchangés |
| Zone < 2 clients total | Zone supprimée automatiquement | - |

### 2.6 Matrice Crossover Dynamique

| Situation | Crossover |
|-----------|-----------|
| Zone avec subwoofer ONLINE | ✅ Activé automatiquement |
| Zone sans subwoofer ONLINE | ❌ Désactivé |
| Subwoofer se déconnecte | Crossover désactivé pour la zone |
| Subwoofer se reconnecte | Crossover réactivé automatiquement |

**Comportement par speaker_type :**

| Type | Filtre crossover |
|------|------------------|
| `satellite` | Highpass (fréquence haute) |
| `bookshelf` | Highpass (fréquence moyenne) |
| `tower` | Highpass (fréquence basse) |
| `subwoofer` | Lowpass |

---

## 3. Problème Central Identifié

### 3.1 Scénario Actuel (PROBLÉMATIQUE)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Zone "Salon" = [local, milo-client-01, milo-client-02]                      │
│                                                                             │
│ 1. User change EQ band 1 = +3dB                                             │
│    └─ milo-client-02 est OFFLINE                                            │
│                                                                             │
│ 2. Frontend propage:                                                        │
│    ✓ local         → OK                                                     │
│    ✓ milo-client-01 → OK                                                    │
│    ✗ milo-client-02 → SKIP (offline)                                        │
│                                                                             │
│ 3. milo-client-02 se reconnecte                                             │
│    └─ Restaure ses ANCIENS settings (avant le changement!)                  │
│    └─ EQ band 1 = 0dB (pas +3dB)                                            │
│                                                                             │
│ RÉSULTAT: Zone désynchronisée!                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Problèmes Identifiés (par criticité)

| # | Problème | Impact | État |
|---|----------|--------|------|
| **1** | **Pas de file d'attente de settings en attente** | Client offline ne reçoit jamais les changements DSP | ❌ Non implémenté |
| **2** | **dsp_ready devient stale** | Frontend envoie requêtes à clients indisponibles → 503 | ⚠️ Partiel |
| **3** | **Pas d'opérations atomiques** | 10 filtres × N clients = 10N requêtes HTTP | ⚠️ Flicker |
| **4** | **Trois sources pour dsp_id** | Incohérences possibles | ⚠️ Fragmentation |
| **5** | **Volume non inclus dans la sync** | Restauration incomplète | ⚠️ Manque |

---

## 4. Architecture Cible Proposée

### 4.1 Nouvelle Structure ClientRegistryService

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     ClientRegistryService (SSOT)                            │
│                                                                             │
│  Clients: Map<mac_id, RegisteredClient>                                     │
│    - available (Snapcast)                                                   │
│    - dsp_ready (CamillaDSP)                                                 │
│    - speaker_type (satellite/bookshelf/tower/subwoofer)                     │
│    - dsp_settings (pour clients STANDALONE uniquement)                      │
│                                                                             │
│  Zones: Map<zone_id, Zone>                                                  │
│    - client_ids[]                                                           │
│    - dsp_settings (SOURCE DE VÉRITÉ pour tous les clients de la zone)       │
│    - crossover_frequency                                                    │
│                                                                             │
│  Méthodes clés:                                                             │
│  + getClientDspSource(mac_id) → zone.dsp_settings OU client.dsp_settings    │
│  + updateZoneDspSettings(zone_id, settings) → persiste + applique ONLINE    │
│  + syncClientOnReconnect(mac_id) → lit source de vérité et applique         │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Principe clé : Pas de pending queue**
- `zone.dsp_settings` est **toujours à jour** (mis à jour à chaque modification)
- À la reconnexion, le client lit simplement sa source de vérité (zone ou standalone)
- Architecture simplifiée et robuste

### 4.2 Nouveau Flux DSP (Architecture Simplifiée)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. User change EQ band 1 = +3dB sur Zone "Salon"                            │
│                                                                             │
│ 2. Backend reçoit: POST /api/dsp/zone/{zone_id}/filter/{filter_id}          │
│    └─ Endpoint atomique pour zone                                           │
│                                                                             │
│ 3. Backend:                                                                 │
│    a) Met à jour zone.dsp_settings (SOURCE DE VÉRITÉ)                       │
│    b) Persiste dans settings.json                                           │
│    c) Pour chaque client ONLINE de la zone:                                 │
│       → Applique immédiatement via CamillaDSP                               │
│    d) Clients OFFLINE: rien à faire (zone.dsp_settings est à jour)          │
│                                                                             │
│ 4. milo-client-02 se reconnecte                                             │
│    └─ Event AVAILABILITY_CHANGED                                            │
│    └─ syncClientOnReconnect('milo-client-02')                               │
│    └─ Lit zone.dsp_settings (déjà à jour avec +3dB!)                        │
│    └─ Applique à CamillaDSP                                                 │
│                                                                             │
│ RÉSULTAT: Zone toujours synchronisée! (sans pending queue)                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 5. Mind Mapping - Flows Complets

### 5.1 Flow Volume Change

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           VOLUME CHANGE FLOW                                 │
└──────────────────────────────────────────────────────────────────────────────┘

  ┌─────────┐         ┌──────────────────┐         ┌─────────────────┐
  │ Frontend│────────►│ ClientRegistry   │────────►│ CamillaDSP      │
  │ Slider  │  API    │ Service          │  Apply  │ (local/remote)  │
  └─────────┘         └────────┬─────────┘         └─────────────────┘
                               │
                               │ Si restore=true
                               ▼
                      ┌──────────────────┐
                      │ Update           │
                      │ startup_volume_db│
                      │ → settings.json  │
                      └──────────────────┘

  ┌─────────────────────────────────────────────────────────────────┐
  │ VOLUME CLIENT (dans zone ou standalone)                         │
  │ → Applique à CE client uniquement                               │
  ├─────────────────────────────────────────────────────────────────┤
  │ VOLUME ZONE (slider zone)                                       │
  │ → Calcule delta                                                 │
  │ → Applique delta à TOUS clients ONLINE de la zone               │
  └─────────────────────────────────────────────────────────────────┘
```

### 5.2 Flow DSP Settings Change

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         DSP SETTINGS CHANGE FLOW                             │
└──────────────────────────────────────────────────────────────────────────────┘

                    ┌─────────────────────────────────────┐
                    │           Frontend UI               │
                    │   (EQ / Compressor / Loudness)      │
                    └─────────────────┬───────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │  POST /api/dsp/zone/{id}/filter/X   │
                    │  ou /api/dsp/zone/{id}/compressor   │
                    │  ou /api/dsp/zone/{id}/loudness     │
                    └─────────────────┬───────────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────────┐
                    │       ClientRegistryService         │
                    └─────────────────┬───────────────────┘
                                      │
                     ┌────────────────┴────────────────┐
                     │                                 │
              Client IN_ZONE?                  Client STANDALONE?
                     │                                 │
                     ▼                                 ▼
        ┌────────────────────────┐       ┌────────────────────────┐
        │ 1. Update zone.dsp     │       │ 1. Update client.dsp   │
        │    (SOURCE DE VÉRITÉ)  │       │ 2. Apply to CamillaDSP │
        │ 2. Persist zone to JSON│       │ 3. Persist to JSON     │
        │ 3. Pour chaque client  │       └────────────────────────┘
        │    ONLINE:             │
        │    → Apply CamillaDSP  │
        │ (OFFLINE: rien à faire │
        │  zone.dsp est à jour)  │
        └────────────────────────┘
```

### 5.3 Flow Reconnexion Client

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         CLIENT RECONNECTION FLOW                             │
└──────────────────────────────────────────────────────────────────────────────┘

  ┌──────────────┐
  │ Snapcast     │
  │ Client.OnConnect
  └──────┬───────┘
         │
         ▼
  ┌──────────────────────────────────────────────────────────────┐
  │                  ClientRegistryService                        │
  │                                                               │
  │  1. Mark client ONLINE                                        │
  │  2. Vérifier si client est dans ClientRegistry                │
  │  3. Déterminer contexte (IN_ZONE ou STANDALONE)               │
  └──────────────────────────┬────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
   │  IN_ZONE    │    │  IN_ZONE    │    │ STANDALONE  │
   │ + others ON │    │ all OFF     │    │             │
   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
          │                  │                  │
          ▼                  ▼                  ▼
   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
   │Vol: moyenne │    │Vol: startup │    │Vol: global  │
   │     zone    │    │  _volume_db │    │ ou startup  │
   │DSP: zone    │    │DSP: zone    │    │DSP: client  │
   │.dsp_settings│    │.dsp_settings│    │.dsp_settings│
   └──────┬──────┘    └──────┬──────┘    └──────┬──────┘
          │                  │                  │
          └──────────────────┼──────────────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │ Apply to CamillaDSP  │
                  │ (lecture directe de  │
                  │  la source de vérité)│
                  └──────────────────────┘
                             │
                             ▼
                  ┌──────────────────────┐
                  │ Recalculate Crossover│
                  │ (si zone + speaker_  │
                  │  type = subwoofer)   │
                  └──────────────────────┘
```

### 5.4 Flow Crossover Dynamique

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         CROSSOVER CALCULATION                                │
└──────────────────────────────────────────────────────────────────────────────┘

  Trigger: Client ONLINE status change OR speaker_type change

  ┌────────────────────────────────────────────────────────────┐
  │                  Pour chaque ZONE                          │
  │                                                            │
  │  1. Lister clients ONLINE                                  │
  │  2. Vérifier si subwoofer ONLINE présent                   │
  │                                                            │
  │     ┌─────────────────┐      ┌─────────────────┐           │
  │     │ Sub ONLINE?     │      │ Sub ONLINE?     │           │
  │     │      OUI        │      │      NON        │           │
  │     └────────┬────────┘      └────────┬────────┘           │
  │              │                        │                    │
  │              ▼                        ▼                    │
  │     ┌─────────────────┐      ┌─────────────────┐           │
  │     │ CROSSOVER ON    │      │ CROSSOVER OFF   │           │
  │     │                 │      │ (bypass filters)│           │
  │     │ Satellites:     │      └─────────────────┘           │
  │     │  → Highpass     │                                    │
  │     │ Subwoofer:      │                                    │
  │     │  → Lowpass      │                                    │
  │     └─────────────────┘                                    │
  └────────────────────────────────────────────────────────────┘
```

---

## 6. Règles de Volume Complètes

### 6.1 Propriétés Volume

| Propriété | Portée | Description |
|-----------|--------|-------------|
| `volume_db` | Par client | Volume individuel de chaque client |
| `volume_zone_avg` | Calculé | Moyenne des volumes des clients ONLINE de la zone |
| `volume_global` | Calculé | Moyenne de tous les clients ONLINE |
| `startup_volume_db` | Global | Volume appliqué au démarrage/reconnexion sans référence |

### 6.2 Comportement Slider Zone

- **Mode delta** : +3dB sur zone → +3dB sur chaque client ONLINE
- Les écarts relatifs entre clients sont **préservés**

### 6.3 Mise à jour startup_volume_db

| `restore_last_volume` | Comportement |
|-----------------------|--------------|
| `false` | Valeur fixe (définie manuellement) |
| `true` | **Mis à jour automatiquement** = `volume_global` en continu |

---

## 7. Règles de Zones

### 7.1 Contraintes

- **Minimum 2 clients** (online OU offline) pour qu'une zone existe
- Si < 2 clients au total → zone **supprimée automatiquement**
- Zone avec 1 seul ONLINE → **Conservée** (autres sont offline)

### 7.2 Transitions

| Transition | DSP | Volume |
|------------|-----|--------|
| Client rejoint zone | Écrasé par zone settings | = moyenne zone |
| Client quitte zone | Garde DSP actuel comme standalone | Inchangé |

---

## 8. API Endpoints Proposés

### 8.1 Endpoints Zone DSP

```
POST /api/dsp/zone/{zone_id}/filter/{filter_id}
  → Update filter pour toute la zone
  → Applique aux clients ONLINE, zone.dsp_settings à jour pour OFFLINE

PUT /api/dsp/zone/{zone_id}/compressor
  → Update compressor pour toute la zone

PUT /api/dsp/zone/{zone_id}/loudness
  → Update loudness pour toute la zone

POST /api/dsp/zone/{zone_id}/preset/{preset_id}
  → Apply preset à toute la zone
```

### 8.2 Réponse Type

```json
{
  "status": "partial",  // "success" | "partial" | "error"
  "applied_to": ["local", "milo-client-01"],
  "offline": ["milo-client-02"],  // seront synchronisés à la reconnexion
  "errors": []
}
```

---

## 9. Structure JSON Persistée

```json
{
  "multiroom": {
    "clients": {
      "aa:bb:cc:dd:ee:f1": {
        "mac_id": "aa:bb:cc:dd:ee:f1",
        "name": "Salon",
        "ip": "192.168.1.10",
        "speaker_type": "bookshelf",
        "dsp_settings": {
          "filters": [...],
          "compressor": {...},
          "loudness": {...}
        }
      }
    },
    "zones": {
      "zone_001": {
        "id": "zone_001",
        "name": "Salon + Sub",
        "client_ids": ["aa:bb:cc:dd:ee:f1", "aa:bb:cc:dd:ee:f2"],
        "dsp_settings": {
          "filters": [...],
          "compressor": {...},
          "loudness": {...}
        }
      }
    }
  },
  "volume": {
    "startup_volume_db": -25.0,
    "restore_last_volume": true
  }
}
```

**Note :** `client.dsp_settings` est utilisé uniquement pour les clients STANDALONE. Les clients IN_ZONE utilisent `zone.dsp_settings` comme source de vérité.

---

## 10. Functional Requirements Complets

| FR | Description | Epic |
|----|-------------|------|
| FR1 | Registre clients par MAC address | Epic 1 |
| FR2 | État client (online, volume, mute, speaker_type) | Epic 1 |
| FR3 | Créer/supprimer zones (min 2 clients) | Epic 2 |
| FR4 | Zone partage DSP settings | Epic 2 |
| FR5 | Volume indépendant par client | Epic 3 |
| FR6 | Slider zone = delta | Epic 3 |
| **FR7** | **Reconnexion IN_ZONE + autres ONLINE → vol=moyenne, dsp=zone** | Epic 5 |
| **FR8** | **Reconnexion IN_ZONE + tous OFFLINE → vol=startup, dsp=zone** | Epic 5 |
| **FR9** | **Reconnexion STANDALONE + autres ONLINE → vol=global, dsp=saved** | Epic 5 |
| **FR10** | **Reconnexion STANDALONE + aucun → vol=startup, dsp=saved** | Epic 5 |
| FR11 | Auto-update startup_volume_db si restore=true | Epic 3 |
| FR12 | Restart backend → startup_volume_db | Epic 3 |
| **FR13** | **Crossover auto si subwoofer ONLINE** | Epic 5 |
| FR14 | Quitte zone → garde DSP comme standalone | Epic 2 |
| FR15 | Rejoint zone → DSP écrasé | Epic 2 |
| **FR16** | **DSP zone → ONLINE appliqué, zone.dsp_settings toujours à jour** | Epic 4 |
| FR17 | WebSocket temps réel | Epic 4 |

---

## 11. Ce qui MANQUE dans l'implémentation actuelle

### 11.1 Non implémenté

| Élément | Description | Priorité |
|---------|-------------|----------|
| Endpoints zone DSP | `/api/dsp/zone/{id}/...` | **HAUTE** |
| Mise à jour zone.dsp_settings | À chaque modification DSP | **CRITIQUE** |
| Propagation backend | Actuellement frontend fait la propagation | **HAUTE** |
| syncClientOnReconnect() | Lecture source de vérité à la reconnexion | **CRITIQUE** |

### 11.2 Partiellement implémenté

| Élément | État actuel | Manque |
|---------|-------------|--------|
| Zone.dsp_settings | Existe | N'est jamais mis à jour après création |
| Reconnexion sync | Volume OK | DSP non synchronisé depuis zone.dsp_settings |
| Frontend propagation | Fonctionne | Devrait être backend (source de vérité unique)

---

## 12. Plan d'Implémentation Recommandé

### Phase 1: Backend - Source de Vérité DSP
- Modifier `updateZoneDspSettings()` pour toujours mettre à jour `zone.dsp_settings`
- Persister immédiatement dans settings.json
- Appliquer aux clients ONLINE via CamillaDSP

### Phase 2: Backend - Zone DSP Endpoints
- Nouveaux endpoints `/api/dsp/zone/{zone_id}/filter/{filter_id}`
- Nouveaux endpoints `/api/dsp/zone/{zone_id}/compressor`
- Nouveaux endpoints `/api/dsp/zone/{zone_id}/loudness`
- Logique: update zone.dsp_settings + apply to ONLINE

### Phase 3: Backend - Reconnexion Simplifiée
- Modifier `syncClientOnReconnect()` pour lire directement la source de vérité
- IN_ZONE → `zone.dsp_settings`
- STANDALONE → `client.dsp_settings`
- Appliquer volume selon contexte (moyenne zone, global, ou startup)

### Phase 4: Frontend - Simplification
- Utiliser endpoints zone au lieu de propagation manuelle
- Supprimer `propagateToLinkedClients()` de dspStore.js
- Laisser backend gérer toute la propagation

### Phase 5: Tests E2E
- Test reconnexion IN_ZONE (FR7, FR8)
- Test reconnexion STANDALONE (FR9, FR10)
- Test crossover dynamique (FR13)
- Test propagation zone DSP (FR16)

---

*Document généré à partir de la session de brainstorming du 2026-01-16*
*Mis à jour le 2026-01-17 : Architecture simplifiée sans pending queue*
