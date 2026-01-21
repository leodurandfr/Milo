# Plan de Simplification Multiroom

**Date:** 2026-01-21
**Objectif:** Simplifier la gestion des clients multiroom en unifiant les identifiants et en encapsulant le routage DSP

---

## Contexte et Problemes Actuels

### Probleme Principal
Le code actuel distingue les clients "local" vs "distant" de multiples facons redondantes :
- `normalize_client_id()` qui retourne `'local'` comme string special
- Helpers `get_local_client()`, `is_local_mac_id()`, `_get_local_mac_id()`, `_is_local_client()`
- Property `is_local` sur le modele Client
- `normalizeHostname()` dans le frontend
- 15+ blocs `if normalized == 'local' / else` dans dsp.py

### Etat Actuel Casse
- `normalize_client_id()` a ete supprime de `snapcast.py` mais `dsp.py` essaie toujours de l'importer
- L'application ne peut pas demarrer : `ImportError: cannot import name 'normalize_client_id'`

### Principe de Simplification
```
mac_id = adresse MAC reelle (format xx:xx:xx:xx:xx:xx)
is_local = deduit de ip === "127.0.0.1" (jamais stocke)
standalone = deduit de zone_id === null (jamais stocke)
tri UI = responsabilite frontend uniquement
```

---

## Architecture Cible

### Flux de Donnees Simplifie

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND                                 │
│  - Recoit clients avec mac_id, is_local, volume_db, zone_id     │
│  - Trie localement : is_local === true en premier               │
│  - Envoie mac_id au backend pour toutes les actions             │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                     API ENDPOINTS (dsp.py)                       │
│  - Recoit mac_id                                                 │
│  - Delegue a DspRouter                                          │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                        DspRouter                                 │
│  1. Lookup client dans ClientRegistry via mac_id                │
│  2. if client.is_local → dsp_service (local)                    │
│  3. else → proxy_service (distant)                              │
└───────────────────────────┬─────────────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
       ┌─────────────┐             ┌─────────────┐
       │ dsp_service │             │proxy_service│
       │   (local)   │             │  (HTTP)     │
       └─────────────┘             └─────────────┘
```

---

## Plan d'Implementation

### Phase 1 : Nettoyage Backend - Suppression des Helpers Obsoletes

#### 1.1 Supprimer l'import casse dans dsp.py

**Fichier:** `backend/api/dsp.py`
**Ligne:** 24

```python
# SUPPRIMER cette ligne
from backend.core.multiroom.snapcast import normalize_client_id
```

> **Note:** Cet import casse l'application. Le supprimer est la premiere etape, mais les 15 usages de `normalize_client_id` dans le fichier devront etre remplaces par les appels a DspRouter (Phase 3).

#### 1.2 Supprimer get_local_client() et is_local_mac_id()

**Fichier:** `backend/core/multiroom/registry.py`
**Lignes:** 392-402

```python
# SUPPRIMER ces deux methodes
def get_local_client(self) -> Optional[Client]:
    """Get the local client (ip == 127.0.0.1)."""
    for client in self._clients.values():
        if client.is_local:
            return client
    return None

def is_local_mac_id(self, mac_id: str) -> bool:
    """Check if mac_id belongs to the local client."""
    client = self._clients.get(mac_id)
    return client.is_local if client else False
```

#### 1.3 Supprimer _get_local_mac_id() et _is_local_client()

**Fichier:** `backend/core/volume/service.py`
**Lignes:** 83-97

```python
# SUPPRIMER ces deux methodes
def _get_local_mac_id(self) -> Optional[str]:
    """Get the local client's mac_id from the registry."""
    registry = getattr(self.state_machine, 'client_registry', None)
    if registry:
        local_client = registry.get_local_client()
        if local_client:
            return local_client.mac_id
    return None

def _is_local_client(self, mac_id: str) -> bool:
    """Check if a mac_id belongs to the local client."""
    registry = getattr(self.state_machine, 'client_registry', None)
    if registry:
        return registry.is_local_mac_id(mac_id)
    return False
```

**Remplacement:** Utiliser directement le pattern :
```python
client = registry.get_client(mac_id)
if client and client.ip == "127.0.0.1":
    # local
```

#### 1.4 GARDER is_local property (essentielle pour routage + tri)

**Fichier:** `backend/core/multiroom/models.py`
**Lignes:** 404-407

```python
@property
def is_local(self) -> bool:
    """Check if this is the local client (running on this device)."""
    return self.ip == "127.0.0.1"
```

> **Decision:** GARDER cette property car elle est **essentielle** pour :
> 1. **Routage DSP** : Le DspRouter utilise `client.is_local` pour decider local vs proxy
> 2. **Tri frontend** : Le frontend utilise `client.is_local` pour afficher le client local en premier
>
> Ce qu'on supprime, ce sont les **helpers redondants** (`get_local_client()`, `is_local_mac_id()`, etc.) qui font des lookups juste pour acceder a cette property.

**to_dict() inclut deja is_local** (ligne 386) :
```python
# GARDER - essentiel pour le frontend
if include_runtime:
    result["online"] = self.online
    result["is_local"] = self.is_local  # Utilise pour le tri + affichage
```

---

### Phase 2 : Creation du DspRouter

**Nouveau fichier:** `backend/core/multiroom/dsp_router.py`

```python
# backend/core/multiroom/dsp_router.py
"""
DspRouter - Centralized DSP command routing.

Routes DSP commands to local dsp_service or remote proxy_service
based on client IP address. Eliminates if/else duplication in endpoints.

Architecture:
- Lookup client in ClientRegistry by mac_id
- If client.ip == "127.0.0.1" → local dsp_service
- Else → proxy_service to remote client
"""
import logging
from typing import Any, Dict, Optional, Callable, Awaitable

logger = logging.getLogger(__name__)


class DspRouter:
    """
    Routes DSP commands to appropriate service based on client location.

    Usage:
        router = DspRouter(registry, dsp_service, proxy_service)
        await router.set_volume(mac_id, volume_db)
        await router.set_mute(mac_id, muted)
    """

    def __init__(
        self,
        client_registry,
        dsp_service,
        proxy_service,
        volume_service=None
    ):
        self._registry = client_registry
        self._dsp_service = dsp_service
        self._proxy_service = proxy_service
        self._volume_service = volume_service

    def _get_client(self, mac_id: str):
        """Get client from registry."""
        return self._registry.get_client(mac_id) if self._registry else None

    def _is_local(self, client) -> bool:
        """Check if client is local (running on this device)."""
        return client and client.is_local

    async def _route(
        self,
        mac_id: str,
        local_action: Callable[[], Awaitable[Any]],
        remote_action: Callable[[str], Awaitable[Any]],
        action_name: str = "action"
    ) -> Dict[str, Any]:
        """
        Route action to local or remote based on client IP.

        Args:
            mac_id: Client MAC address
            local_action: Async function to call for local client
            remote_action: Async function to call for remote (receives IP)
            action_name: Name for logging

        Returns:
            Action result dict with status
        """
        client = self._get_client(mac_id)

        if not client:
            logger.warning(f"Client {mac_id} not found for {action_name}")
            return {"status": "error", "message": f"Client {mac_id} not found"}

        if self._is_local(client):
            logger.debug(f"Routing {action_name} to local DSP for {mac_id}")
            return await local_action()
        else:
            if not self._proxy_service:
                return {"status": "error", "message": "Proxy service not available"}

            if not client.online:
                logger.debug(f"Skipping offline client {mac_id} for {action_name}")
                return {"status": "skipped", "reason": "client_offline"}

            logger.debug(f"Routing {action_name} to proxy for {mac_id} ({client.ip})")
            return await remote_action(client.ip)

    # === VOLUME ===

    async def set_volume(self, mac_id: str, volume_db: float) -> Dict[str, Any]:
        """Set volume for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.set_volume(volume_db)
                return {"status": "success" if success else "error", "volume": volume_db}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/dsp/volume", {"volume": volume_db})
            return result

        return await self._route(mac_id, local, remote, "set_volume")

    async def set_mute(self, mac_id: str, muted: bool) -> Dict[str, Any]:
        """Set mute for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.set_mute(muted)
                return {"status": "success" if success else "error", "mute": muted}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/dsp/mute", {"muted": muted})
            return result

        return await self._route(mac_id, local, remote, "set_mute")

    # === PRESETS ===

    async def load_preset(self, mac_id: str, preset_id: str) -> Dict[str, Any]:
        """Load a preset for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.load_preset(preset_id)
                return {"status": "success" if success else "error", "preset_id": preset_id}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", f"/dsp/preset/{preset_id}")
            return result

        return await self._route(mac_id, local, remote, "load_preset")

    # === FILTERS ===

    async def update_filter(
        self,
        mac_id: str,
        filter_id: str,
        filter_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Update a filter for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.set_filter(
                    filter_id=filter_id,
                    freq=filter_data.get("freq"),
                    gain=filter_data.get("gain"),
                    q=filter_data.get("q"),
                    filter_type=filter_data.get("filter_type"),
                    enabled=filter_data.get("enabled", True)
                )
                return {"status": "success" if success else "error", "filter_id": filter_id}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(
                ip, "PUT", f"/dsp/filter/{filter_id}", filter_data
            )
            return result

        return await self._route(mac_id, local, remote, "update_filter")

    # === COMPRESSOR ===

    async def set_compressor(self, mac_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Set compressor settings for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.set_compressor(**settings)
                return {"status": "success" if success else "error"}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/dsp/compressor", settings)
            return result

        return await self._route(mac_id, local, remote, "set_compressor")

    # === LOUDNESS ===

    async def set_loudness(self, mac_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Set loudness settings for a client."""
        async def local():
            if self._dsp_service:
                success = await self._dsp_service.set_loudness(**settings)
                return {"status": "success" if success else "error"}
            return {"status": "error", "message": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/dsp/loudness", settings)
            return result

        return await self._route(mac_id, local, remote, "set_loudness")

    # === DSP ENABLED ===

    async def set_dsp_enabled(self, mac_id: str, enabled: bool, routing_service=None) -> Dict[str, Any]:
        """Set DSP effects enabled state for a client."""
        async def local():
            if routing_service:
                success = await routing_service.set_dsp_effects_enabled(enabled)
                return {"status": "success" if success else "error", "enabled": enabled}
            return {"status": "error", "message": "Routing service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "PUT", "/dsp/enabled", {"enabled": enabled})
            return result

        return await self._route(mac_id, local, remote, "set_dsp_enabled")

    # === STATUS ===

    async def get_status(self, mac_id: str) -> Dict[str, Any]:
        """Get DSP status for a client."""
        async def local():
            if self._dsp_service:
                return await self._dsp_service.get_status()
            return {"available": False, "error": "DSP service not available"}

        async def remote(ip: str):
            result = await self._proxy_service.request(ip, "GET", "/dsp/status")
            return result

        return await self._route(mac_id, local, remote, "get_status")

    async def get_levels(self, mac_id: str) -> Dict[str, Any]:
        """Get audio levels for a client."""
        async def local():
            if self._dsp_service:
                return await self._dsp_service.get_levels()
            return {"available": False}

        async def remote(ip: str):
            return await self._proxy_service.get_dsp_levels(ip)

        return await self._route(mac_id, local, remote, "get_levels")
```

**Enregistrement dans dependencies.py:**
```python
# Ajouter dans _create_service()
elif service_name == "dsp_router":
    from backend.core.multiroom.dsp_router import DspRouter
    return DspRouter(
        client_registry=get_service("client_registry_service"),
        dsp_service=get_service("camilladsp_service"),
        proxy_service=get_service("proxy_service")
    )
```

---

### Phase 3 : Refactoring de dsp.py

#### Principe de Refactoring

**AVANT (pattern repete 15+ fois):**
```python
normalized = normalize_client_id(client_id)
if normalized == 'local':
    # Action locale directe
    result = await dsp_service.do_something()
else:
    # Action via proxy
    result = await proxy_service.request(hostname, ...)
```

**APRES (appel simple):**
```python
result = await dsp_router.do_something(mac_id, ...)
```

#### Exemple de Transformation

**Endpoint: get_zone_levels (ligne 152-188)**

AVANT:
```python
@router.get("/levels/zone/{client_ids}")
async def get_zone_levels(client_ids: str):
    ids = client_ids.split(",")

    async def get_client_levels(client_id: str):
        normalized = normalize_client_id(client_id)
        if normalized == 'local':
            try:
                return await dsp_service.get_levels()
            except Exception as e:
                logger.debug(f"Failed to get local DSP levels: {e}")
                return None
        else:
            if proxy_service:
                return await proxy_service.get_dsp_levels(client_id)
            return None
    # ... rest
```

APRES:
```python
@router.get("/levels/zone/{client_ids}")
async def get_zone_levels(client_ids: str):
    ids = client_ids.split(",")

    async def get_client_levels(client_id: str):
        return await dsp_router.get_levels(client_id)
    # ... rest
```

#### Liste des Transformations dans dsp.py

| Ligne | Fonction/Endpoint | Transformation |
|-------|-------------------|----------------|
| 24 | Import | Supprimer `from ... import normalize_client_id` |
| 159 | get_zone_levels | `dsp_router.get_levels(client_id)` |
| 390 | load_preset_for_zone | `dsp_router.load_preset(client_id, preset_id)` |
| 469 | update_zone_filter | `dsp_router.update_filter(client_id, filter_id, data)` |
| 569 | update_zone_compressor | `dsp_router.set_compressor(client_id, data)` |
| 653 | update_zone_loudness | `dsp_router.set_loudness(client_id, data)` |
| 742 | update_zone_dsp_enabled | `dsp_router.set_dsp_enabled(client_id, enabled)` |
| 813 | load_preset_for_client | `dsp_router.load_preset(mac_id, preset_id)` |
| 861 | proxy_load_preset | `dsp_router.load_preset(hostname, preset_id)` |
| 1311 | get_client_dsp_status | `dsp_router.get_status(hostname)` |
| 1416 | get_client_dsp_enabled | `dsp_router.get_dsp_enabled(hostname)` |
| 1437 | update_client_dsp_enabled | `dsp_router.set_dsp_enabled(hostname, enabled)` |
| 1462 | get_client_volume | `dsp_router.get_volume(hostname)` |
| 1481 | update_client_volume | `dsp_router.set_volume(hostname, volume)` |
| 1516 | update_client_mute | `dsp_router.set_mute(hostname, muted)` |

---

### Phase 4 : Simplification Frontend

#### 4.1 Supprimer normalizeHostname()

**Fichier:** `frontend/src/stores/dspStore.js`
**Lignes:** 364-367

```javascript
// SUPPRIMER cette fonction
function normalizeHostname(hostname) {
  return hostname === 'milo' ? 'local' : hostname;
}
```

**Remplacer tous les usages** par utilisation directe de `hostname` ou `mac_id`.

#### 4.2 Modifier sortClientIdsLocalFirst()

**Fichier:** `frontend/src/stores/dspStore.js`
**Lignes:** 309-313

AVANT:
```javascript
function sortClientIdsLocalFirst(clientIds) {
  if (!clientIds || !Array.isArray(clientIds)) return [];
  return [...clientIds].sort((a, b) => (a === 'local' ? -1 : b === 'local' ? 1 : 0));
}
```

APRES:
```javascript
// Cette fonction n'a plus de sens car on ne connait pas les IPs ici
// Le tri doit se faire la ou on a acces aux objets clients complets
// SUPPRIMER ou adapter selon le contexte d'utilisation
```

#### 4.3 Modifier le tri dans MultiroomControl.vue

**Fichier:** `frontend/src/components/multiroom/MultiroomControl.vue`
**Lignes:** 265-274

AVANT:
```javascript
.sort((a, b) => {
  // Local first
  if (a.mac_id === 'local') return -1;
  if (b.mac_id === 'local') return 1;
  // ...
});
```

APRES:
```javascript
.sort((a, b) => {
  // Local first (using is_local property from backend)
  if (a.is_local) return -1;
  if (b.is_local) return 1;
  // Online clients first
  if (a.online && !b.online) return -1;
  if (!a.online && b.online) return 1;
  // Then alphabetically
  return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
});
```

#### 4.4 Modifier updateClientDspVolume dans dspStore.js

**Fichier:** `frontend/src/stores/dspStore.js`
**Lignes:** 398-424

AVANT:
```javascript
async function updateClientDspVolume(clientId, volumeDb) {
  const normalized = normalizeHostname(clientId);

  // Skip remote clients when multiroom is disabled
  if (normalized !== 'local') {
    // ...
  }

  if (isMacAddress(normalized)) {
    await axios.patch(`/api/volume/client/mac/${macToUrlFormat(normalized)}`, { volume_db: volumeDb });
  } else {
    await axios.patch(`/api/volume/client/${normalized}`, { volume_db: volumeDb });
  }
}
```

APRES:
```javascript
async function updateClientDspVolume(macId, volumeDb) {
  try {
    // All clients identified by MAC address now
    // Skip multiroom check - backend handles routing
    await axios.patch(`/api/volume/client/mac/${macToUrlFormat(macId)}`, { volume_db: volumeDb });
    return true;
  } catch (error) {
    console.error(`Error updating DSP volume for ${macId}:`, error);
    return false;
  }
}
```

#### 4.5 Principe pour MultiroomItem.vue

Le composant ne devrait pas faire de logique - il affiche ce qu'il recoit :
- `client.name` - nom
- `client.volume_db` ou `client.dspVolume` - volume actuel
- `client.mute` ou `client.dspMuted` - etat mute
- `client.online` - statut connexion
- `client.is_local` - pour style "local" si necessaire (property envoyee par le backend)

---

### Phase 5 : Tests de Validation

#### Tests Manuels

1. **Volume client local:**
   - Activer multiroom
   - Modifier le volume du client local via slider
   - Verifier que le volume change immediatement
   - Verifier que le WebSocket broadcast met a jour l'UI

2. **Volume client distant:**
   - Avoir un milo-client connecte
   - Modifier le volume du client distant via slider
   - Verifier que le volume change sur le client distant
   - Verifier que le WebSocket broadcast met a jour l'UI

3. **Volume zone:**
   - Creer une zone avec 2+ clients
   - Modifier le volume de la zone
   - Verifier que tous les clients de la zone changent de volume
   - Verifier que le delta est applique correctement

4. **Tri des clients:**
   - Le client local (127.0.0.1) doit apparaitre en premier
   - Les clients online avant les offline
   - Tri alphabetique pour le reste

5. **Reconnexion client:**
   - Deconnecter un client distant
   - Verifier qu'il apparait "offline" dans l'UI
   - Reconnecter le client
   - Verifier que son volume est restaure

---

## Resume des Fichiers a Modifier

### Backend

| Fichier | Action |
|---------|--------|
| `backend/api/dsp.py` | Supprimer import, utiliser DspRouter |
| `backend/core/multiroom/dsp_router.py` | **CREER** - nouveau fichier |
| `backend/core/multiroom/registry.py` | Supprimer `get_local_client()`, `is_local_mac_id()` |
| `backend/core/volume/service.py` | Supprimer `_get_local_mac_id()`, `_is_local_client()` |
| `backend/dependencies.py` | Enregistrer DspRouter |

### Frontend

| Fichier | Action |
|---------|--------|
| `frontend/src/stores/dspStore.js` | Supprimer `normalizeHostname()`, simplifier fonctions |
| `frontend/src/components/multiroom/MultiroomControl.vue` | Tri par `ip === '127.0.0.1'` |
| `frontend/src/components/multiroom/MultiroomItem.vue` | Verifier qu'il utilise les donnees directement |

---

## Ordre d'Execution Recommande

1. **Phase 2 d'abord** : Creer DspRouter (permet de faire fonctionner le reste)
2. **Phase 3** : Refactorer dsp.py pour utiliser DspRouter (corrige l'import casse)
3. **Phase 1** : Supprimer les helpers obsoletes (maintenant safe car plus utilises)
4. **Phase 4** : Simplifier frontend
5. **Phase 5** : Tester

Cet ordre permet d'avoir une application fonctionnelle le plus tot possible.

---

## Notes Importantes

- **`is_local` est une property derivee** : calculee comme `self.ip == "127.0.0.1"`, pas stockee
- **Le backend ne trie pas** : c'est le frontend qui trie avec `client.is_local` pour l'affichage
- **mac_id est l'unique identifiant** : plus de "local" comme string special
- **Le routage est encapsule** : DspRouter utilise `client.is_local` pour router local vs proxy
- **Supprimer les helpers redondants** : `get_local_client()`, `is_local_mac_id()`, etc. - utiliser directement `client.is_local`
