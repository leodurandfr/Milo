# Deep Dive: Architecture des Sources Audio

> Documentation exhaustive de l'architecture des sources audio de Milo
> Générée le 2026-01-09

## Table des matières

1. [Vue d'ensemble](#vue-densemble)
2. [Architecture en couches](#architecture-en-couches)
3. [Interface AudioSourceProtocol](#interface-audiosourceprotocol)
4. [Classe de base UnifiedAudioSource](#classe-de-base-unifiedaudiosource)
5. [Machine d'état UnifiedAudioStateMachine](#machine-détat-unifiedaudiostatemachine)
6. [Les 5 sources implémentées](#les-5-sources-implémentées)
   - [Spotify Source](#spotify-source)
   - [Bluetooth Source](#bluetooth-source)
   - [Mac Source](#mac-source)
   - [Radio Source](#radio-source)
   - [Podcast Source](#podcast-source)
7. [Flux de données et communications](#flux-de-données-et-communications)
8. [Injection de dépendances](#injection-de-dépendances)
9. [Routes API](#routes-api)
10. [Patterns et bonnes pratiques](#patterns-et-bonnes-pratiques)

---

## Vue d'ensemble

Le système de sources audio de Milo permet de gérer 5 sources audio différentes de manière unifiée :

| Source | Service externe | Protocole | État |
|--------|-----------------|-----------|------|
| **Spotify** | go-librespot | WebSocket | Production |
| **Bluetooth** | BlueALSA | D-Bus | Production |
| **Mac** | ROC Toolkit | UDP/RTP | Production |
| **Radio** | mpv | IPC Socket | Production |
| **Podcast** | mpv + Taddy API | IPC Socket + GraphQL | Production |

### Principes architecturaux

1. **Single Source of Truth** : `UnifiedAudioStateMachine` gère tout l'état audio
2. **Source Pattern** : Toutes les sources implémentent `AudioSourceProtocol`
3. **Async-first** : Toutes les opérations I/O sont asynchrones
4. **Dependency Injection** : via `dependency-injector`
5. **Event-driven** : Communication WebSocket temps réel

---

## Architecture en couches

```
┌─────────────────────────────────────────────────────────────────┐
│                    PRESENTATION LAYER                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ API Routes  │  │  WebSocket  │  │   Frontend (Vue 3)      │  │
│  │ (FastAPI)   │  │   Manager   │  │   Pinia Stores          │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    APPLICATION LAYER                             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              AudioSourceProtocol (Interface)               │   │
│  │  - initialize() - start() - stop() - restart()           │   │
│  │  - get_status() - handle_command()                        │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   INFRASTRUCTURE LAYER                           │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │              UnifiedAudioStateMachine                       │ │
│  │  - transition_to_source() - update_source_state()          │ │
│  │  - broadcast_event() - register_source()                   │ │
│  └────────────────────────────────────────────────────────────┘ │
│                              │                                   │
│    ┌─────────────────────────┼─────────────────────────┐        │
│    ▼                         ▼                         ▼        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐        │
│  │ Spotify  │  │Bluetooth │  │   Mac    │  │  Radio   │ ...    │
│  │ Source   │  │ Source   │  │ Source   │  │ Source   │        │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                      DOMAIN LAYER                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐  │
│  │ AudioSource │  │ SourceState │  │   SystemAudioState      │  │
│  │    (Enum)   │  │   (Enum)    │  │     (DataClass)         │  │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Interface AudioSourceProtocol

**Fichier** : `backend/core/audio_source.py`

L'interface définit le contrat que toutes les sources doivent respecter :

```python
class AudioSourceProtocol(Protocol):
    """Interface pour toutes les sources audio."""

    async def initialize(self) -> bool:
        """Initialise la source (appelé une seule fois au démarrage)."""

    async def start(self) -> bool:
        """Démarre la source (appelé lors de la transition vers cette source)."""

    async def stop(self) -> bool:
        """Arrête la source (appelé lors de la transition vers une autre source)."""

    async def restart(self) -> bool:
        """Redémarre la source."""

    async def get_status(self) -> Dict[str, Any]:
        """Retourne le statut complet de la source."""

    async def handle_command(self, command: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Traite une commande spécifique à la source."""

    def is_active_source(self) -> bool:
        """Indique si cette source est la source audio active."""
```

### États de la source (SourceState)

```python
class SourceState(Enum):
    STARTING = "starting"      # Source en cours de démarrage
    READY = "ready"            # Source démarrée, en attente de connexion
    CONNECTED = "connected"    # Source connectée et opérationnelle
    ERROR = "error"            # Source en erreur
```

### Sources audio (AudioSource)

```python
class AudioSource(Enum):
    NONE = "none"
    SPOTIFY = "spotify"
    BLUETOOTH = "bluetooth"
    MAC = "mac"
    RADIO = "radio"
    PODCAST = "podcast"
```

---

## Classe de base UnifiedAudioSource

**Fichier** : `backend/core/audio_source.py`

La classe de base fournit l'implémentation commune pour toutes les sources :

### Responsabilités

1. **Gestion du cycle de vie** : initialize, start, stop, restart
2. **Contrôle systemd** : démarrage/arrêt des services
3. **Gestion d'état** : transitions STARTING → READY → CONNECTED
4. **Métadonnées** : stockage et mise à jour des informations de lecture
5. **Logging** : journalisation standardisée

### Structure

```python
class UnifiedAudioSource(AudioSourceProtocol):
    def __init__(self, source: AudioSource, config: Dict, state_machine):
        self.source = source
        self.config = config
        self.state_machine = state_machine
        self.service_name = config.get("service_name", "")
        self._systemd = SystemdServiceManager()
        self._metadata: Dict[str, Any] = {}
        self.current_state = SourceState.READY
        self._initialized = False

    async def _update_state(self, new_state: SourceState, metadata: Dict = None):
        """Met à jour l'état et notifie la state machine."""
        self.current_state = new_state
        if metadata:
            self._metadata.update(metadata)
        await self.state_machine.update_source_state(
            self.source, new_state, self._metadata
        )

    async def _start_service(self) -> bool:
        """Démarre le service systemd associé."""

    async def _stop_service(self) -> bool:
        """Arrête le service systemd associé."""

    def is_active_source(self) -> bool:
        """Vérifie si cette source est la source active."""
        return self.state_machine.system_state.active_source == self.source
```

### Cycle de vie typique

```
           initialize()
               │
               ▼
         ┌───────────┐
         │  READY    │ ◄────────────────┐
         └───────────┘                  │
               │                        │
           start()                   stop()
               │                        │
               ▼                        │
         ┌───────────┐                  │
         │ STARTING  │                  │
         └───────────┘                  │
               │                        │
      Service ready / Connection        │
               │                        │
               ▼                        │
         ┌───────────┐                  │
         │CONNECTED  │──────────────────┘
         └───────────┘
```

---

## Machine d'état UnifiedAudioStateMachine

**Fichier** : `backend/infrastructure/state/state_machine.py`

### Rôle central

La state machine est le **coeur du système audio** :

1. **Enregistre les sources** via `register_source()`
2. **Gère les transitions** entre sources audio
3. **Buffer les mises à jour** pendant les transitions
4. **Broadcast les événements** via WebSocket
5. **Protège l'état** avec des locks asynchrones

### Transitions protégées

```python
async def transition_to_source(self, target_source: AudioSource) -> bool:
    async with self._transition_lock:
        # 1. Marquer comme en transition
        self.system_state.transitioning = True
        self.system_state.active_source = target_source
        self.system_state.source_state = SourceState.STARTING

        # 2. Broadcast transition_start
        await self._broadcast_event("system", "transition_start", {...})

        # 3. Arrêter l'ancienne source
        await self._stop_source(old_source)

        # 4. Démarrer la nouvelle source
        success = await self._start_new_source(target_source)

        # 5. Fin de transition
        self.system_state.transitioning = False

        # 6. Rejouer les mises à jour bufferisées
        await self._replay_buffered_updates()

        # 7. Broadcast transition_complete
        await self._broadcast_event("system", "transition_complete", {...})
```

### Buffering des mises à jour

Pendant une transition, les mises à jour d'état sont stockées dans une queue FIFO et rejouées après :

```python
# File d'attente avec protection mémoire (max 50 éléments)
_buffered_updates: deque[Tuple[AudioSource, SourceState, Dict]] = deque(maxlen=50)

async def update_source_state(self, source, new_state, metadata):
    if is_transitioning:
        # Bufferiser au lieu d'ignorer
        self._buffered_updates.append((source, new_state, metadata))
        return

    # Appliquer directement sinon
    await self._apply_source_state_update(source, new_state, metadata)
```

### Broadcast WebSocket

```python
async def _broadcast_event(self, category: str, event_type: str, data: Dict):
    event_data = {
        "category": category,      # "source", "system", "routing"
        "type": event_type,        # "state_changed", "transition_start", etc.
        "source": data.get("source", category),
        "data": {..., "full_state": current_state},
        "timestamp": time.time()
    }
    await self.websocket_handler.handle_event(event_data)
```

---

## Les 5 sources implémentées

### Spotify Source

**Fichiers** :
- `backend/sources/spotify/source.py`
- `backend/sources/spotify/routes.py`

**Service externe** : go-librespot (Spotify Connect)

**Caractéristiques** :

| Aspect | Détail |
|--------|--------|
| Communication | WebSocket vers go-librespot |
| Port WebSocket | ws://localhost:3678 |
| Service systemd | milo-spotify.service |
| Métadonnées | Track, Artist, Album, Position, Duration, Cover Art |
| Events | will_play, playing, paused, stopped, position, volume |

**Architecture interne** :

```
┌─────────────────────────────────────────────────────────────┐
│                    SpotifySource                             │
│  ┌───────────────────┐  ┌────────────────────────────────┐  │
│  │ LibrespotMonitor  │  │     Metadata Extraction        │  │
│  │  (WebSocket)      │  │  - Track info from events      │  │
│  │  - Event listener │  │  - Cover art base64            │  │
│  │  - Reconnection   │  │  - Position tracking           │  │
│  └───────────────────┘  └────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│                    go-librespot                              │
│  - Spotify Connect receiver                                  │
│  - Config: /var/lib/milo/go-librespot/config.yml            │
└─────────────────────────────────────────────────────────────┘
```

**Flux de données** :

```
Spotify App → go-librespot → WebSocket Event → SpotifySource
                                                    │
                                          ┌────────┴────────┐
                                          ▼                 ▼
                                    state_machine     Frontend
                                   (update_state)   (via WebSocket)
```

**Commandes supportées** :
- `restart` : Redémarre le service go-librespot
- `get_logs` : Récupère les logs du service

---

### Bluetooth Source

**Fichiers** :
- `backend/sources/bluetooth/source.py`
- `backend/sources/bluetooth/agent.py`
- `backend/sources/bluetooth/monitor.py`
- `backend/sources/bluetooth/routes.py`

**Service externe** : BlueALSA (Bluetooth A2DP sink)

**Caractéristiques** :

| Aspect | Détail |
|--------|--------|
| Communication | D-Bus |
| Services systemd | milo-bluealsa.service, milo-bluealsa-aplay.service |
| Protocole BT | A2DP Sink (récepteur audio) |
| Agent | Auto-accept pairing (PIN: 0000) |

**Architecture interne** :

```
┌─────────────────────────────────────────────────────────────────┐
│                    BluetoothSource                               │
│  ┌────────────────┐  ┌─────────────────┐  ┌──────────────────┐  │
│  │ BluetoothAgent │  │ BluealMonitor   │  │ BluealPlayback   │  │
│  │  (D-Bus Agent) │  │ (D-Bus Monitor) │  │ (Audio Routing)  │  │
│  │  - Pairing     │  │ - PCM tracking  │  │ - aplay control  │  │
│  │  - Auto-accept │  │ - Device detect │  │ - Volume         │  │
│  └────────────────┘  └─────────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
          │                    │                     │
          ▼                    ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                         D-Bus                                    │
│  - org.bluez (Bluetooth daemon)                                 │
│  - org.bluealsa (BlueALSA daemon)                               │
└─────────────────────────────────────────────────────────────────┘
```

**États Bluetooth** :

```python
class BluetoothState(Enum):
    INACTIVE = "inactive"      # Service arrêté
    READY = "ready"            # En attente de connexion
    CONNECTED = "connected"    # Appareil connecté (audio possible)
    PLAYING = "playing"        # Audio en cours de lecture
```

**Commandes supportées** :
- `disconnect` : Déconnecte l'appareil Bluetooth actuel
- `restart_audio` : Redémarre le flux audio
- `get_paired_devices` : Liste les appareils appairés

---

### Mac Source

**Fichiers** :
- `backend/sources/mac/source.py`
- `backend/sources/mac/routes.py`

**Service externe** : ROC Toolkit (streaming audio réseau)

**Caractéristiques** :

| Aspect | Détail |
|--------|--------|
| Communication | UDP/RTP |
| Service systemd | milo-mac.service |
| Ports | RTP: 10001, RS8M: 10002, RTCP: 10003 |
| Protocole | RTP avec FEC (Forward Error Correction) |

**Architecture interne** :

```
┌─────────────────────────────────────────────────────────────┐
│                      MacSource                               │
│  ┌───────────────────┐  ┌─────────────────────────────────┐ │
│  │ Service Control   │  │     Connection Tracking         │ │
│  │ - systemd start   │  │  - Connected client name       │ │
│  │ - systemd stop    │  │  - Connection status           │ │
│  └───────────────────┘  └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│                    roc-recv (ROC Toolkit)                    │
│  - Receives RTP audio stream from Mac                        │
│  - Applies FEC for packet loss recovery                      │
│  - Output: ALSA device                                       │
└─────────────────────────────────────────────────────────────┘
          ▲
          │
┌─────────────────────────────────────────────────────────────┐
│                    Mac avec roc-send                         │
│  - Captures system audio                                     │
│  - Streams via RTP to Milo                                   │
└─────────────────────────────────────────────────────────────┘
```

**Commandes supportées** :
- `restart` : Redémarre le service ROC
- `get_logs` : Récupère les logs du service
- `get_connections` : Liste les connexions actives

---

### Radio Source

**Fichiers** :
- `backend/sources/radio/source.py`
- `backend/shared/mpv_controller.py`
- `backend/sources/radio/data.py`
- `backend/sources/radio/browser_api.py`
- `backend/sources/radio/genres.py`
- `backend/sources/radio/routes.py`

**Service externe** : mpv (lecteur média)

**Caractéristiques** :

| Aspect | Détail |
|--------|--------|
| Communication | IPC Socket JSON |
| Service systemd | milo-radio.service |
| Socket IPC | /run/milo/radio-ipc.sock |
| API externe | Radio Browser API |
| Stockage | /var/lib/milo/radio_data.json |

**Architecture interne** :

```
┌─────────────────────────────────────────────────────────────────────┐
│                         RadioSource                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │  MpvController  │  │ StationManager  │  │ RadioBrowserAPI     │  │
│  │  - IPC Socket   │  │ - Favorites     │  │ - Search stations   │  │
│  │  - Play/Stop    │  │ - Custom        │  │ - Get countries     │  │
│  │  - Volume       │  │ - Broken list   │  │ - Station details   │  │
│  │  - Status poll  │  │ - Metadata mod  │  │ - Mirror selection  │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘  │
│           │                   │                                      │
│           │           ┌──────┴──────┐                               │
│           │           ▼             ▼                               │
│           │   ┌──────────────┐  ┌──────────────┐                    │
│           │   │ ImageManager │  │ radio_data   │                    │
│           │   │ - Upload     │  │   .json      │                    │
│           │   │ - WebP conv  │  └──────────────┘                    │
│           │   │ - Validation │                                      │
│           │   └──────────────┘                                      │
└───────────┼─────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────┐
│                            mpv                                       │
│  - Lit les flux HTTP/HTTPS des stations                             │
│  - Extrait les métadonnées (icy-title)                              │
│  - Output: ALSA device via routing.env                              │
└─────────────────────────────────────────────────────────────────────┘
```

**Communication IPC avec mpv** :

```python
class MpvController:
    async def _send_command(self, command: list) -> Optional[dict]:
        """Envoie une commande JSON via socket IPC."""
        message = {"command": command}
        writer.write((json.dumps(message) + "\n").encode())
        response = await asyncio.wait_for(reader.readline(), timeout=2.0)
        return json.loads(response)

    async def play(self, url: str) -> bool:
        """Charge et joue une URL."""
        return await self._send_command(["loadfile", url, "replace"])

    async def get_property(self, property_name: str):
        """Lit une propriété mpv (volume, time-pos, etc.)."""
        return await self._send_command(["get_property", property_name])
```

**Gestion des stations** :

```python
class StationManager:
    # Structure de données
    {
        "favorites": ["uuid1", "uuid2", ...],
        "broken_stations": ["uuid3", ...],
        "custom_stations": {
            "custom_abc123": {
                "name": "Ma Station",
                "url": "http://...",
                "image": "abc123.webp"
            }
        },
        "modified_metadata": {
            "uuid1": {
                "name": "Nom personnalisé",
                "custom_image": "def456.webp"
            }
        },
        "station_cache": {
            "uuid1": {...station data...}
        }
    }
```

**Commandes supportées** :
- `play_station` : Joue une station par UUID
- `stop_playback` : Arrête la lecture
- `add_favorite` / `remove_favorite` : Gestion des favoris
- `mark_broken` / `reset_broken` : Gestion des stations cassées

---

### Podcast Source

**Fichiers** :
- `backend/sources/podcast/source.py`
- `backend/sources/podcast/taddy_api.py`
- `backend/sources/podcast/routes.py`

**Services externes** : mpv + Taddy API (GraphQL)

**Caractéristiques** :

| Aspect | Détail |
|--------|--------|
| Communication | IPC Socket + GraphQL |
| Service systemd | milo-podcast.service |
| Socket IPC | /run/milo/podcast-ipc.sock |
| API externe | Taddy GraphQL (podcasts) |
| Stockage | /var/lib/milo/podcast_data.json |

**Architecture interne** :

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PodcastSource                                │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────┐  │
│  │  MpvController  │  │ PodcastDataSvc  │  │     TaddyAPI        │  │
│  │  - Playback     │  │ - Subscriptions │  │ - Search            │  │
│  │  - Seek         │  │ - Progress      │  │ - Top charts        │  │
│  │  - Speed        │  │ - Settings      │  │ - Episode details   │  │
│  │  - Position     │  │ - Queue         │  │ - iTunes RSS        │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
          │                     │                      │
          │                     │                      ▼
          │                     │          ┌─────────────────────────┐
          │                     │          │    Taddy GraphQL API    │
          │                     │          │  - Rate limited (hourly)│
          │                     │          │  - Cached (60 min)      │
          │                     │          └─────────────────────────┘
          │                     ▼
          │          ┌─────────────────────┐
          │          │   podcast_data.json │
          │          │  - subscriptions    │
          │          │  - progress         │
          │          │  - settings         │
          │          └─────────────────────┘
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                            mpv                                       │
│  - Instance séparée de Radio (socket différent)                     │
│  - Supporte la vitesse de lecture (0.5x - 2.0x)                     │
│  - Seek précis pour reprise                                         │
└─────────────────────────────────────────────────────────────────────┘
```

**Fonctionnalités avancées** :

1. **Suivi de progression** :
   - Sauvegarde automatique toutes les 10 secondes
   - Reprise automatique à la dernière position
   - Marquage "terminé" quand < 5 secondes de la fin

2. **Vitesse de lecture** :
   ```python
   ALLOWED_SPEEDS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

   async def set_speed(self, speed: float) -> bool:
       if speed in self.ALLOWED_SPEEDS:
           await self._mpv.set_property("speed", speed)
   ```

3. **API Taddy avec cache** :
   ```python
   class TaddyAPI:
       CACHE_DURATION = 60 * 60  # 1 heure

       async def _cached_request(self, cache_key: str, query: str):
           if cache_key in self._cache:
               cached, timestamp = self._cache[cache_key]
               if time.time() - timestamp < self.CACHE_DURATION:
                   return cached
           result = await self._graphql_request(query)
           self._cache[cache_key] = (result, time.time())
           return result
   ```

**Commandes supportées** :
- Playback : `play`, `pause`, `resume`, `stop`, `seek`, `speed`
- Abonnements : `subscribe`, `unsubscribe`
- Queue : `mark_complete`, `remove_from_queue`

---

## Flux de données et communications

### Changement de source audio

```
┌──────────┐     POST /audio/source      ┌──────────────┐
│ Frontend │ ─────────────────────────► │   API Route   │
└──────────┘                             └──────────────┘
                                                │
                                                ▼
                                    ┌───────────────────────┐
                                    │  state_machine        │
                                    │  .transition_to_      │
                                    │   source(SPOTIFY)     │
                                    └───────────────────────┘
                                                │
                     ┌──────────────────────────┼──────────────────────────┐
                     ▼                          ▼                          ▼
           ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
           │ _broadcast_event│      │  _stop_source   │      │ _start_new_     │
           │ ("transition_   │      │   (OLD_SOURCE)  │      │  source(SPOTIFY)│
           │  start")        │      └─────────────────┘      └─────────────────┘
           └─────────────────┘                                        │
                     │                                                ▼
                     ▼                                    ┌─────────────────────┐
           ┌─────────────────┐                            │  SpotifySource      │
           │  WebSocket      │                            │   .start()          │
           │  Manager        │                            └─────────────────────┘
           └─────────────────┘                                        │
                     │                                                ▼
                     ▼                                    ┌─────────────────────┐
           ┌─────────────────┐                            │  systemd start      │
           │  Frontend       │                            │  milo-spotify       │
           │  (Vue Store)    │                            └─────────────────────┘
           └─────────────────┘
```

### Mise à jour de métadonnées (Spotify)

```
┌─────────────────┐                  ┌─────────────────┐
│  go-librespot   │  WebSocket msg   │ LibrespotMonitor│
│  (will_play)    │ ────────────────►│ (event handler) │
└─────────────────┘                  └─────────────────┘
                                              │
                                    parse metadata
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │ SpotifySource   │
                                    │ ._update_state( │
                                    │   CONNECTED,    │
                                    │   {track:...})  │
                                    └─────────────────┘
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │  state_machine  │
                                    │ .update_source_ │
                                    │  state()        │
                                    └─────────────────┘
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │ _broadcast_event│
                                    │ ("source",      │
                                    │  "state_changed"│
                                    │  {metadata:...})│
                                    └─────────────────┘
                                              │
                                              ▼
                                    ┌─────────────────┐
                                    │  Frontend       │
                                    │ unifiedAudio    │
                                    │ Store.update()  │
                                    └─────────────────┘
```

---

## Injection de dépendances

**Fichier** : `backend/config/container.py`

### Ordre d'initialisation (CRITIQUE)

```python
def initialize_services():
    # ÉTAPE 1: Récupérer les instances
    state_machine = container.audio_state_machine()
    routing_service = container.audio_routing_service()
    ...

    # ÉTAPE 2: Résoudre les dépendances circulaires
    routing_service.set_source_callback(lambda src: state_machine.get_source(src))
    routing_service.set_state_machine(state_machine)
    state_machine.routing_service = routing_service
    ...

    # ÉTAPE 3: Enregistrer les sources (AVANT init async)
    state_machine.register_source(AudioSource.SPOTIFY, spotify_source)
    state_machine.register_source(AudioSource.BLUETOOTH, bluetooth_source)
    state_machine.register_source(AudioSource.MAC, mac_source)
    state_machine.register_source(AudioSource.RADIO, radio_source)
    state_machine.register_source(AudioSource.PODCAST, podcast_source)

    # ÉTAPE 4: Initialisation async parallèle
    async def init_async():
        await asyncio.gather(
            routing_service.initialize(),
            volume_service.initialize(),
            ...
        )
```

### Configuration des sources

```python
# Chaque source reçoit sa configuration via le constructeur
spotify_source = SpotifySource(
    config={
        "config_path": "/var/lib/milo/go-librespot/config.yml",
        "service_name": "milo-spotify.service"
    },
    state_machine=state_machine,
    settings_service=settings_service
)
```

---

## Routes API

### Routes communes (audio.py)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/api/audio/state` | État complet du système audio |
| POST | `/api/audio/source` | Changer de source audio |
| POST | `/api/audio/stop` | Arrêter la source active |

### Routes Spotify (spotify.py)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/api/spotify/status` | Statut de la source Spotify |
| POST | `/api/spotify/restart` | Redémarrer le service |
| GET | `/api/spotify/logs` | Logs du service |

### Routes Bluetooth (bluetooth.py)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/api/bluetooth/status` | Statut Bluetooth |
| POST | `/api/bluetooth/disconnect` | Déconnecter l'appareil |
| POST | `/api/bluetooth/restart-audio` | Redémarrer l'audio |
| GET | `/api/bluetooth/paired-devices` | Appareils appairés |

### Routes Mac (mac.py)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/api/mac/status` | Statut du récepteur |
| POST | `/api/mac/restart` | Redémarrer le service |
| GET | `/api/mac/logs` | Logs du service |
| GET | `/api/mac/connections` | Connexions actives |

### Routes Radio (radio.py)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/api/radio/stations` | Rechercher des stations |
| GET | `/api/radio/station/{id}` | Détails d'une station |
| POST | `/api/radio/play` | Jouer une station |
| POST | `/api/radio/stop` | Arrêter la lecture |
| GET/POST | `/api/radio/favorites` | Gérer les favoris |
| POST | `/api/radio/custom/add` | Ajouter station custom |
| GET | `/api/radio/countries` | Liste des pays |
| GET | `/api/radio/favicon` | Proxy pour favicons |

### Routes Podcast (podcast.py)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/api/podcast/discover/*` | Découverte (popular, charts, genres) |
| GET | `/api/podcast/search` | Recherche podcasts/épisodes |
| GET | `/api/podcast/series/{uuid}` | Détails podcast |
| GET | `/api/podcast/episode/{uuid}` | Détails épisode |
| POST | `/api/podcast/play` | Jouer un épisode |
| POST | `/api/podcast/pause/resume/seek/stop` | Contrôles lecture |
| POST | `/api/podcast/speed` | Vitesse de lecture |
| GET/POST/DELETE | `/api/podcast/subscriptions` | Abonnements |
| GET | `/api/podcast/queue` | Épisodes en cours |
| GET/POST | `/api/podcast/progress/{uuid}` | Progression |

---

## Patterns et bonnes pratiques

### 1. Pattern Observer (Event Broadcasting)

```python
# Source notifie la state machine
await self.state_machine.update_source_state(
    source=self.source,
    new_state=SourceState.CONNECTED,
    metadata={"track": "..."}
)

# State machine broadcast aux clients WebSocket
await self._broadcast_event("source", "state_changed", {...})
```

### 2. Pattern Template Method

La classe de base définit le squelette, les sous-classes implémentent les détails :

```python
class UnifiedAudioSource:
    async def start(self) -> bool:
        await self._update_state(SourceState.STARTING)
        success = await self._start_service()  # Template
        if success:
            await self._on_service_started()   # Hook pour sous-classe
        return success
```

### 3. Protection par locks asynchrones

```python
class UnifiedAudioStateMachine:
    def __init__(self):
        self._transition_lock = asyncio.Lock()  # Transitions atomiques
        self._state_lock = asyncio.Lock()        # Accès état protégé
        self._buffer_lock = asyncio.Lock()       # Buffer mises à jour

    async def transition_to_source(self, target):
        async with self._transition_lock:
            # Une seule transition à la fois
            ...
```

### 4. Gestion d'erreur avec fallback

```python
async def _stop_source(self, source: AudioSource):
    if source != AudioSource.NONE:
        source_instance = self.sources.get(source)
        if source_instance:
            try:
                await source_instance.stop()
            except Exception as e:
                self.logger.error(f"Error stopping {source.value}: {e}")
                # Continue quand même - ne pas bloquer
```

### 5. Timeout avec asyncio

```python
async def transition_to_source(self, target_source):
    try:
        async with asyncio.timeout(self.TRANSITION_TIMEOUT):
            # Opérations de transition...
    except asyncio.TimeoutError:
        await self._emergency_stop()
        return False
```

---

## Références des fichiers

| Composant | Chemin |
|-----------|--------|
| **Interface** | `backend/core/audio_source.py` |
| **Base class** | `backend/core/audio_source.py` |
| **State Machine** | `backend/core/state.py` |
| **Domain** | `backend/core/models/audio_state.py` |
| **Service Registry** | `backend/dependencies.py` |
| **Spotify Source** | `backend/sources/spotify/source.py` |
| **Bluetooth Source** | `backend/sources/bluetooth/source.py` |
| **Mac Source** | `backend/sources/mac/source.py` |
| **Radio Source** | `backend/sources/radio/source.py` |
| **Podcast Source** | `backend/sources/podcast/source.py` |
| **Routes Audio** | `backend/api/audio.py` |
| **Routes Spotify** | `backend/sources/spotify/routes.py` |
| **Routes Bluetooth** | `backend/sources/bluetooth/routes.py` |
| **Routes Mac** | `backend/sources/mac/routes.py` |
| **Routes Radio** | `backend/sources/radio/routes.py` |
| **Routes Podcast** | `backend/sources/podcast/routes.py` |

---

*Documentation générée automatiquement par BMAD workflow document-project*
