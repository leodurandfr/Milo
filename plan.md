# Plan — Auto-stop unifié pour toutes les sources audio

> **But** : remplacer les multiples mécanismes de timeout/auto-disconnect actuels (Spotify settings, AirPlay backend-only, hardcoded frontend timers Radio/Podcast, rien pour CD/BT/Mac) par **un seul réglage global** appliqué à toutes les sources éligibles.
>
> **Découpage** : 4 prompts indépendants, chacun livrable et testable séparément.
> Pour exécuter : ouvrir une nouvelle conversation et dire « implémenter la prochaine phase ». L'agent lira ce fichier et exécutera le prochain prompt non terminé.

---

## Contexte technique partagé (à garder en tête pour tous les prompts)

### Architecture cible

- **Setting unique** : `audio.auto_disconnect_delay` (float, secondes, 0 = disabled)
- **Setting compagnon existant** : `audio.inactivity_timeout` (déjà en place, juste déplacé d'UI)
- **Sources éligibles** : Spotify, AirPlay, Radio, Podcast, CD, Bluetooth, Mac (ROC)
- **Mécanisme par source** :
  - Spotify, AirPlay : events pause natifs (déjà câblés)
  - Radio, Podcast, CD : events pause mpv
  - Bluetooth, Mac (ROC) : silence detection ALSA (module partagé)

### Infra réutilisée

- `BaseAudioSource._start_pause_timer()` / `_cancel_pause_timer()` / `_on_auto_disconnect()` (`backend/core/audio_source.py:350-464`)
- `BaseAudioSource._load_auto_disconnect_config()` à modifier pour lire la clé globale
- `state_machine.broadcast_event()` pour les WS events de changement de config

### Conventions projet

- Commentaires en anglais (cf CLAUDE.md)
- Pas de migration code transitoire — migration une fois au load, helpers supprimés ensuite
- Pydantic models en snake_case
- Frontend : `apiCall()` pour les API actions, gestion WS dans les stores Pinia

---

## Prompt 1 — Backend foundation (global setting + migration + API) ✅

**Périmètre** : créer le réglage global, migrer les anciennes clés, simplifier Spotify/AirPlay. Aucune nouvelle source couverte. Frontend inchangé pour l'instant (l'ancien endpoint `spotify-disconnect` est remplacé, mais le panneau Spotify côté UI sera nettoyé au Prompt 2).

### Tâches

- [x] **`backend/core/settings.py`** — defaults
  - [x] Supprimer entrées `"spotify": {"auto_disconnect_delay": ...}` et `"airplay": {"auto_disconnect_delay": ...}` du dict `defaults`
  - [x] Ajouter `auto_disconnect_delay: 120.0` dans le dict `audio` existant (qui contient déjà `inactivity_timeout`)

- [x] **`backend/core/settings.py`** — `_validate_and_merge()`
  - [x] Migration au load : si `settings.spotify.auto_disconnect_delay` ou `settings.airplay.auto_disconnect_delay` présent → écrire la valeur (max des deux si les deux existent) dans `validated.audio.auto_disconnect_delay`, ne pas réécrire `validated.spotify.*` / `validated.airplay.*`
  - [x] Validation `auto_disconnect_delay` : 0 (disabled) ou clamp [1.0, 9999.0]
  - [x] Mettre à jour la section `audio` validation pour inclure le nouveau champ aux côtés de `inactivity_timeout`

- [x] **`backend/core/audio_source.py`** — `BaseAudioSource`
  - [x] `_load_auto_disconnect_config()` : retirer le param `settings_key`, lire toujours `audio.auto_disconnect_delay`
  - [x] `set_auto_disconnect_config()` : retirer le param `settings_key`, écrire toujours dans `audio.auto_disconnect_delay`
  - [x] Nouvelle méthode publique `async reload_auto_disconnect_config()` qui rappelle `_load_auto_disconnect_config()` (sera invoquée depuis l'API)

- [x] **`backend/core/state.py`** — `AudioStateMachine`
  - [x] Nouvelle méthode `async reload_auto_disconnect_for_all_sources()` qui itère sur les sources enregistrées et appelle `source.reload_auto_disconnect_config()` sur chacune

- [x] **`backend/api/models.py`**
  - [x] Supprimer `SpotifyDisconnectRequest`
  - [x] Ajouter `AudioDisconnectRequest { auto_disconnect_delay: float }`

- [x] **`backend/api/settings.py`**
  - [x] Supprimer routes `GET/PUT /spotify-disconnect` (lignes ~520-547)
  - [x] Ajouter routes `GET/PUT /audio-disconnect` :
    - GET retourne `{ "config": { "auto_disconnect_delay": <value> } }`
    - PUT persiste, broadcast event WS `audio_disconnect_changed`, appelle `state_machine.reload_auto_disconnect_for_all_sources()`
  - [x] Adapter le endpoint d'agrégation des settings (ligne ~138-139) : remplacer `spotify_disconnect`/`airplay_disconnect` par `audio_disconnect`

- [x] **`backend/sources/spotify/source.py`**
  - [x] Ligne ~216 : `_load_auto_disconnect_config()` sans argument
  - [x] Ligne ~521-531 : supprimer override custom de `set_auto_disconnect_config` (devient inutile)

- [x] **`backend/sources/airplay/source.py`**
  - [x] Ligne ~83 : `_load_auto_disconnect_config()` sans argument

- [x] **Tests** (`backend/tests/`)
  - [x] Test migration : settings.json avec `spotify.auto_disconnect_delay=300` → après load, `audio.auto_disconnect_delay==300` et plus de clé `spotify.*`/`airplay.*`
  - [x] Test migration : les deux anciennes clés présentes (300 et 600) → résultat 600 (max)
  - [x] Test API : `GET /api/settings/audio-disconnect`, `PUT /api/settings/audio-disconnect` avec valeurs valides et invalides
  - [x] Supprimer ou adapter les anciens tests `test_set_spotify_disconnect_*`

### Critères de validation

- ✅ `settings.json` après migration ne contient plus `spotify.auto_disconnect_delay` ni `airplay.auto_disconnect_delay`
- ✅ Spotify et AirPlay continuent de fonctionner avec auto-disconnect (vérifier par test ou en runtime)
- ✅ `PUT /api/settings/audio-disconnect` fait reloader la config sur **toutes** les sources actives, pas juste Spotify
- ✅ Tests existants passent

---

## Prompt 2 — Frontend reorganization (nouveau panneau + suppressions) ✅

**Périmètre** : créer le panneau "Lecture audio", supprimer SpotifySettings, déplacer inactivity_timeout hors de DockSettings, nettoyer les timers hardcodés Radio/Podcast côté frontend.

**Pré-requis** : Prompt 1 terminé (l'API `/audio-disconnect` doit exister).

### Tâches

- [x] **Nouveau composant `frontend/src/components/settings/categories/AudioPlaybackSettings.vue`**
  - [x] Structure identique à l'actuel `SpotifySettings.vue` mais avec **deux** `ToggleSection` :
    - "Auto-stop sur pause" → presets `30s, 2min, 5min, 10min, 30min`, lié à `audio.auto_disconnect_delay`
    - "Fermeture automatique après inactivité" → presets `5min, 30min, 1h, 2h, 6h, 12h`, lié à `audio.inactivity_timeout`
  - [x] Note discrète sous le 1er toggle : "S'applique aux sources qui supportent la pause" (i18n)

- [x] **`frontend/src/stores/settingsStore.js`**
  - [x] Supprimer `spotifyDisconnect` ref + getter + `updateSpotifyDisconnect`
  - [x] Remplacer `inactivityTimeout` ref par un `audioPlayback` ref groupé
  - [x] Adapter `loadAllSettings()` pour mapper depuis le nouveau payload backend (`audio_disconnect` + `inactivity_timeout`)
  - [x] Handlers WS `audio_disconnect_changed` et `inactivity_timeout_changed` mis à jour dans `App.vue`

- [x] **`frontend/src/components/settings/SettingsModal.vue`**
  - [x] Entrée `Lecture audio` ajoutée (icône `audio-playback.svg` créée)
  - [x] Entrée `Spotify` + import supprimés
  - [x] `headerTitle` map mise à jour
  - [x] `shouldShowPlaceholder` recompté (Audio playback inconditionnel, Spotify supprimé, Screen géré séparément)
  - [x] `AudioPlaybackSettings` importé et routé

- [x] **`frontend/src/components/settings/categories/DockSettings.vue`**
  - [x] Section "Inactivity timeout" supprimée
  - [x] Toute la logique associée supprimée

- [x] **Suppressions définitives**
  - [x] `SpotifySettings.vue` supprimé
  - [x] `frontend/src/constants/audioPlayer.js` supprimé (les deux constantes étaient les seules)

- [x] **`frontend/src/components/radio/RadioSource.vue`**
  - [x] Constante locale `HIDE_FADE_MS = 3000`

- [x] **`frontend/src/components/podcasts/PodcastSource.vue`**
  - [x] Constante locale `HIDE_FADE_MS = 3000`
  - [x] Callback `onHideTimeout: stop()` supprimé
  - [x] `onFadeOutStart` conservé

- [x] **`frontend/src/composables/useSourcePlaybackVisibility.js`**
  - [x] `onHideTimeout` n'avait plus aucun caller → param et logique retirés

- [x] **i18n** (8 locales)
  - [x] Ajout `settings.audioPlayback`, `audioPlayback.{autoDisconnect,autoDisconnectHint,notApplicableNote,inactivityTimeout,inactivityHint}`, `time.6h`
  - [x] Suppression `spotifySettings.*`, `applicationsSettings.inactivityTimeout`, `applicationsSettings.inactivityDelay`

### Critères de validation

- ✅ Le panneau Settings → Lecture audio s'ouvre, affiche les deux toggles, et les valeurs persistent au reload
- ✅ Plus de panneau Spotify dans Settings (l'auto-disconnect Spotify reste actif côté backend, piloté par le nouveau réglage global)
- ✅ Plus de section "Fermeture automatique" dans le panneau Dock
- ✅ Le podcast s'arrête bien quand la pause dépasse le délai (preuve que c'est bien le backend qui le fait, pas le frontend)
- ✅ Aucun import cassé, build frontend OK

---

## Prompt 3 — Radio / Podcast / CD pause hooks (backend mpv) ✅

**Périmètre** : ajouter le câblage pause→`_start_pause_timer()` aux 3 sources mpv. Le frontend ne change pas (déjà nettoyé au Prompt 2).

**Pré-requis** : Prompts 1 et 2 terminés.

### Tâches

- [x] **`backend/shared/mpv_audio_source.py`** (base partagée)
  - [x] `__init__` : `auto_disconnect_enabled = True` + `_was_paused: bool = False`
  - [x] `_handle_pause_change(is_paused)` : helper edge-trigger (pas d'IPC), appelé par les sous-classes avec leur propre signal de pause
  - [x] Override `_on_auto_disconnect()` : `state_machine.transition_to_source(NONE, expected_source=self.source)` (CAS guard)
  - [x] Fix sous-jacent dans `BaseAudioSource._start_pause_timer` : détacher la task ref avant d'appeler le callback pour éviter qu'un `_cancel_pause_timer()` ré-entrant (via `stop()` interne) cancel la task en cours

- [x] **`backend/sources/radio/source.py`**
  - [x] `_do_start` appelle `await self._load_auto_disconnect_config()`
  - [x] `_on_monitor_tick` lit `pause` une fois si une station joue, appelle `_handle_pause_change`
  - [x] `_handle_stop_playback` annule explicitement (pause mpv peut rester sticky après `stop`)

- [x] **`backend/sources/podcast/source.py`**
  - [x] `_do_start` appelle `await self._load_auto_disconnect_config()`
  - [x] `_on_monitor_tick` réutilise le `pause_state` déjà lu pour appeler `_handle_pause_change` (zéro IPC supplémentaire)
  - [x] `_handle_stop_playback` annule explicitement le timer

- [x] **`backend/sources/cd/source.py`**
  - [x] `_do_start` appelle `await self._load_auto_disconnect_config()`
  - [x] `_handle_pause` / `_handle_resume` / `_handle_play_track` / `_handle_stop_playback` appellent explicitement `_handle_pause_change` (pas de polling mpv pour CD — évite la confusion `playback-time` vs `time-pos` du démuxer FIFO/raw_audio)

- [x] **Tests** (`backend/tests/test_mpv_audio_source.py`, nouveau fichier)
  - [x] Test edge pause→arme / unpause→annule / pas de média→clear / désactivé→no-op
  - [x] Test `_on_auto_disconnect` appelle `transition_to_source(NONE, expected_source=...)`
  - [x] Test `reload_auto_disconnect_config` (delay=0 et delay>0)
  - [x] Test régression : timer auto-détaché avant exécution du callback (évite self-cancel)

### Critères de validation

- ✅ Lecture Radio + pause via UI → après le délai global (par ex. 30s mis pour le test), la radio s'arrête et l'UI revient à la home
- ✅ Idem Podcast et CD
- ✅ La reprise (play) avant expiration du délai annule le timer (pas d'arrêt prématuré)
- ✅ Changement du délai global via Settings → s'applique immédiatement aux sources actives

---

## Prompt 4 — Silence detector + Bluetooth + Mac (ROC) ✅

**Périmètre** : créer le module silence_detector partagé, intégrer dans BluetoothSource et MacSource.

**Pré-requis** : Prompts 1, 2, 3 terminés.

### Décision technique retenue : Option D — CamillaDSP capture-level polling

Aucune des options A/B/C n'était sans coût significatif (alsaloop+dsnoop pour A, plugin `multi` non adapté au stream-cloning pour B, back-pressure FIFO pour C). À la place, le silence detector lit `levels.capture_peak` directement depuis CamillaDSP qui est **déjà** dans le chemin audio :

- Direct mode : BT/ROC → `pcm.camilladsp` (loopback subdev 5) → CamillaDSP → DAC. Le peak meter de CamillaDSP voit le signal.
- Multiroom mode : BT/ROC → loopback 0/1 → snapserver → snapclient local → `pcm.snapclient_dsp` (loopback 5) → CamillaDSP → DAC. Toujours dans le chemin local.

**Avantages** :
- Zéro modif ALSA (`asound.conf` inchangé, `pcm_substreams=8` inchangé)
- Aucun nouveau service systemd (`alsaloop`, etc.)
- Aucune nouvelle dépendance Python (pas de `pyalsaaudio`)
- Réutilise `CamillaDSPService.get_levels()` déjà exposé

**Limite assumée** : si l'utilisateur écoute uniquement sur des zones distantes sans zone locale active, le peak local sera nul. Acceptable puisque BT et ROC nécessitent du hardware local — l'utilisateur est forcément sur la zone locale.

### Tâches

- [x] **`backend/core/silence_detector.py`** — nouveau module
  - [x] Class `SilenceDetector(camilladsp_service, threshold_dbfs=-60.0, idle_seconds=2.0, poll_interval=0.5)`
  - [x] Méthodes `async start()`, `async stop()`, `set_callbacks(on_silence_started, on_audio_resumed)`
  - [x] Polling asyncio (pas de thread) : `await camilladsp_service.get_levels()` toutes les `poll_interval` secondes
  - [x] Hystérésis : un seul event "silence" après `idle_seconds` continus sous le seuil, un seul event "resumed" dès qu'un sample dépasse le seuil

- [x] **`backend/dependencies.py`**
  - [x] Injecter `camilladsp_service` dans `bluetooth_source` et `mac_source`

- [x] **`backend/sources/bluetooth/source.py`**
  - [x] Constructeur accepte `camilladsp_service`, instancie `SilenceDetector`
  - [x] `auto_disconnect_enabled = True` dès l'init (pris en compte par le delay global du settings)
  - [x] `_on_device_connected` : `await self._load_auto_disconnect_config()`, démarre le silence detector, câble silence→`_start_pause_timer` / resume→`_cancel_pause_timer`
  - [x] `_on_device_disconnected`, `_cleanup` : stoppe le silence detector
  - [x] Override `_on_auto_disconnect` : déconnecte le device courant, ne stoppe **pas** bluealsa

- [x] **`backend/sources/mac/source.py`**
  - [x] Constructeur accepte `camilladsp_service`, instancie `SilenceDetector`
  - [x] `auto_disconnect_enabled = True`
  - [x] `_add_client` : `await self._load_auto_disconnect_config()`, démarre le silence detector au premier client
  - [x] Disconnect du dernier client → arrête le silence detector
  - [x] `_do_stop` : arrête le silence detector
  - [x] Override `_on_auto_disconnect` : `transition_to_source(NONE, expected_source=MAC)` (CAS guard)

- [x] **Tests** (`backend/tests/test_silence_detector.py`)
  - [x] Mock `camilladsp_service.get_levels()` : silence stable → callback `on_silence_started`
  - [x] Resume avant `idle_seconds` → pas d'event ; resume après silence_started → `on_audio_resumed`
  - [x] `available=False` → pas d'event (CamillaDSP déconnecté)
  - [x] `start()` idempotent ; `stop()` cancel propre

### Critères de validation

- ✅ Téléphone connecté en BT, lecture pause → après le délai global, device déconnecté
- ✅ macOS (ROC) idle → après le délai, source MAC quittée vers NONE
- ✅ `bluealsa.service` reste running après auto-disconnect (un nouveau device peut s'apparier)
- ✅ Reprise audio avant expiration du délai annule le timer

### Critères de validation

- ✅ Téléphone connecté en BT, lecture mise en pause sur le téléphone → après le délai global, le device est déconnecté côté Milō
- ✅ Idem si pas d'event A2DP suspend (test avec un sender qui stream du silence)
- ✅ Mac (ROC) en lecture, pause de la lecture côté Mac → après le délai, source désactivée et retour sur NONE
- ✅ `bluealsa.service` reste running après auto-disconnect (un nouveau device peut s'apparier)
- ✅ Reprise audio avant expiration du délai annule correctement le timer

---

## Tableau récapitulatif final attendu

À l'issue des 4 prompts :

| Source | Mécanisme | Réglage |
|---|---|---|
| Spotify | Events pause natifs | `audio.auto_disconnect_delay` |
| AirPlay | Events metadata pipe | `audio.auto_disconnect_delay` |
| Radio | mpv pause events | `audio.auto_disconnect_delay` |
| Podcast | mpv pause events | `audio.auto_disconnect_delay` |
| CD | mpv pause events | `audio.auto_disconnect_delay` |
| Bluetooth | Silence detector ALSA | `audio.auto_disconnect_delay` |
| Mac (ROC) | Silence detector ALSA | `audio.auto_disconnect_delay` |

UI : un seul panneau **Settings → Lecture audio** avec deux toggles (Auto-stop sur pause / Fermeture automatique).

Code supprimé : `SpotifySettings.vue`, section inactivity dans `DockSettings.vue`, constantes `*_PLAYER_HIDE_DELAY_MS`, callback `onHideTimeout: stop()` dans Podcast, override custom de `set_auto_disconnect_config` dans Spotify.
