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

## Prompt 2 — Frontend reorganization (nouveau panneau + suppressions)

**Périmètre** : créer le panneau "Lecture audio", supprimer SpotifySettings, déplacer inactivity_timeout hors de DockSettings, nettoyer les timers hardcodés Radio/Podcast côté frontend.

**Pré-requis** : Prompt 1 terminé (l'API `/audio-disconnect` doit exister).

### Tâches

- [ ] **Nouveau composant `frontend/src/components/settings/categories/AudioPlaybackSettings.vue`**
  - [ ] Structure identique à l'actuel `SpotifySettings.vue` mais avec **deux** `ToggleSection` :
    - "Auto-stop sur pause" → presets `30s, 2min, 5min, 10min, 30min`, lié à `audio.auto_disconnect_delay`
    - "Fermeture automatique après inactivité" → presets `5min, 30min, 1h, 2h, 6h, 12h`, lié à `audio.inactivity_timeout`
  - [ ] Note discrète sous le 1er toggle : "S'applique aux sources qui supportent la pause" (i18n)

- [ ] **`frontend/src/stores/settingsStore.js`**
  - [ ] Supprimer `spotifyDisconnect` ref + getter + `updateSpotifyDisconnect`
  - [ ] Remplacer `inactivityTimeout` ref par un `audioPlayback` ref groupé :
    ```js
    const audioPlayback = ref({
      auto_disconnect_delay: 120,
      inactivity_timeout: 7200,
    })
    ```
  - [ ] Adapter `loadAllSettings()` pour mapper depuis le nouveau payload backend (`audio_disconnect` + `inactivity_timeout`)
  - [ ] Ajouter handlers WS pour `audio_disconnect_changed` et `inactivity_timeout_changed`

- [ ] **`frontend/src/components/settings/SettingsModal.vue`**
  - [ ] Ajouter entrée `Lecture audio` dans la grille (créer/réutiliser une icône SVG appropriée — cf existant pour le style)
  - [ ] Supprimer entrée `Spotify` (lignes ~90-95) et l'import associé
  - [ ] Mettre à jour `headerTitle` map : retirer `'spotify'`, ajouter `'audio-playback'`
  - [ ] Mettre à jour `shouldShowPlaceholder` (count change cohérent)
  - [ ] Importer et router `AudioPlaybackSettings` au lieu de `SpotifySettings`

- [ ] **`frontend/src/components/settings/categories/DockSettings.vue`**
  - [ ] Supprimer la section "Inactivity timeout" (template lignes ~95-109)
  - [ ] Supprimer toute la logique associée : `inactivityConfig`, `inactivityEnabled`, `setInactivityTimeout`, `handleInactivityToggle`, `lastNonZeroTimeout`, le `watch` correspondant, l'import/usage de `settingsStore.inactivityTimeout`
  - [ ] Vérifier que le composant ne contient plus que la gestion du dock/applications

- [ ] **Suppressions définitives**
  - [ ] Supprimer `frontend/src/components/settings/categories/SpotifySettings.vue`
  - [ ] Supprimer `RADIO_PLAYER_HIDE_DELAY_MS` et `PODCAST_PLAYER_HIDE_DELAY_MS` de `frontend/src/constants/audioPlayer.js`
  - [ ] Le fichier `audioPlayer.js` peut être supprimé entièrement s'il ne contenait que ces deux constantes

- [ ] **`frontend/src/components/radio/RadioSource.vue`**
  - [ ] Remplacer l'import `RADIO_PLAYER_HIDE_DELAY_MS` par une constante locale `const HIDE_FADE_MS = 3000`
  - [ ] Le timer reste purement esthétique (cache l'UI quand la radio s'arrête)

- [ ] **`frontend/src/components/podcasts/PodcastSource.vue`**
  - [ ] Remplacer `PODCAST_PLAYER_HIDE_DELAY_MS` par une constante locale `const HIDE_FADE_MS = 3000`
  - [ ] **Supprimer** le callback `onHideTimeout: async () => { await podcastStore.stop() }` (lignes ~149-153) — le backend gère le stop maintenant
  - [ ] Garder `onFadeOutStart` pour le `clearDisplayEpisode()`

- [ ] **`frontend/src/composables/useSourcePlaybackVisibility.js`**
  - [ ] Si `onHideTimeout` n'est plus utilisé nulle part après les modifs ci-dessus → retirer le param et la logique associée
  - [ ] Sinon, le garder tel quel (il peut servir à nettoyer de l'UI sans appel backend)

- [ ] **i18n** (`frontend/src/locales/english.json`, `french.json`, et autres si présents)
  - [ ] Ajouter clés `settings.audioPlayback`, `audioPlayback.title`, `audioPlayback.autoDisconnect`, `audioPlayback.autoDisconnectHint`, `audioPlayback.notApplicableNote`, `audioPlayback.inactivityTimeout`, `audioPlayback.inactivityHint`
  - [ ] Conserver les clés `time.*` (réutilisées pour les presets)
  - [ ] Supprimer `spotifySettings.*`, `applicationsSettings.inactivityTimeout`, `applicationsSettings.inactivityDelay`

### Critères de validation

- ✅ Le panneau Settings → Lecture audio s'ouvre, affiche les deux toggles, et les valeurs persistent au reload
- ✅ Plus de panneau Spotify dans Settings (l'auto-disconnect Spotify reste actif côté backend, piloté par le nouveau réglage global)
- ✅ Plus de section "Fermeture automatique" dans le panneau Dock
- ✅ Le podcast s'arrête bien quand la pause dépasse le délai (preuve que c'est bien le backend qui le fait, pas le frontend)
- ✅ Aucun import cassé, build frontend OK

---

## Prompt 3 — Radio / Podcast / CD pause hooks (backend mpv)

**Périmètre** : ajouter le câblage pause→`_start_pause_timer()` aux 3 sources mpv. Le frontend ne change pas (déjà nettoyé au Prompt 2).

**Pré-requis** : Prompts 1 et 2 terminés.

### Tâches

- [ ] **`backend/shared/mpv_audio_source.py`** (base partagée)
  - [ ] Dans `__init__` : set `self.auto_disconnect_enabled = True` (l'effective enable est piloté par le delay global = 0 → désactivé)
  - [ ] Dans le state listener mpv existant qui broadcast les events :
    - Quand `pause` devient `True` ou état mpv = "paused" → appeler `self._start_pause_timer()`
    - Quand `pause` devient `False` ou playback reprend → appeler `self._cancel_pause_timer()`
  - [ ] Override `_on_auto_disconnect()` : appeler `self._do_stop()`, puis demander au state_machine de retourner sur `AudioSource.NONE` via `transition_to_source(AudioSource.NONE)`

- [ ] **`backend/sources/radio/source.py`**
  - [ ] Dans `initialize()` ou `_do_start()` : appeler `await self._load_auto_disconnect_config()`
  - [ ] Vérifier que les events pause mpv sont bien remontés (le listener radio écoute déjà `pause` pour son état UI ?)
  - [ ] Note : pour la radio, "pause" mpv peut ne pas être un cas usuel (stream live = stop, pas pause). Vérifier si mpv émet `pause` ou seulement `idle-active`. Adapter le listener si besoin.

- [ ] **`backend/sources/podcast/source.py`**
  - [ ] Dans `initialize()` ou `_do_start()` : appeler `await self._load_auto_disconnect_config()`
  - [ ] Vérifier que la pause mpv déclenche bien `_start_pause_timer` via la base mpv
  - [ ] Le système de save de progression toutes les 10s doit continuer de fonctionner pendant la fenêtre du timer (les deux sont indépendants)

- [ ] **`backend/sources/cd/source.py`**
  - [ ] Dans `initialize()` ou `_do_start()` : appeler `await self._load_auto_disconnect_config()`
  - [ ] Vérifier comportement mpv pour la lecture CD (le reader thread tourne en parallèle de mpv ; à l'auto-disconnect, `_do_stop` doit aussi arrêter le reader thread proprement — c'est déjà fait dans le `_do_stop` existant)

- [ ] **Tests**
  - [ ] Test que sur Radio/Podcast/CD, un appel à `reload_auto_disconnect_config()` met bien à jour la valeur active
  - [ ] Test (si possible avec mock mpv) que pause + délai → `_on_auto_disconnect()` est appelé

### Critères de validation

- ✅ Lecture Radio + pause via UI → après le délai global (par ex. 30s mis pour le test), la radio s'arrête et l'UI revient à la home
- ✅ Idem Podcast et CD
- ✅ La reprise (play) avant expiration du délai annule le timer (pas d'arrêt prématuré)
- ✅ Changement du délai global via Settings → s'applique immédiatement aux sources actives

---

## Prompt 4 — Silence detector + Bluetooth + Mac (ROC)

**Périmètre** : créer le module silence_detector partagé, modifier asound.conf pour le tap audio, intégrer dans BluetoothSource et MacSource.

**Pré-requis** : Prompts 1, 2, 3 terminés. C'est la phase la plus risquée techniquement (ALSA + threading), à isoler.

### Décision technique à finaliser au début du prompt

**Approche tap ALSA** : choisir entre les options suivantes en début d'implémentation :

- **Option A — Subdevice loopback dédié + alsaloop forwarder** : BT/ROC écrivent dans un nouveau subdevice snd-aloop (par ex. 8 et 9, après bump de `pcm_substreams=16`) ; un service systemd `alsaloop` forward chaque flux vers la destination originale (camilladsp ou snapcast loopback) ; le silence detector lit le côté capture du subdevice. Robuste, mais ajoute un service par source taggée.

- **Option B — Plugin `multi` ALSA inline** : modifier asound.conf pour que `milo_bluetooth_direct`/`_multiroom` (et idem ROC) deviennent des `multi` qui split en mémoire vers (a) la destination existante et (b) un subdevice de capture pur. Plus léger, mais le plugin `multi` est principalement conçu pour le routing channel-par-channel, pas le clonage stream-to-multiple-slaves — à valider qu'ALSA expose bien ce que l'on veut.

- **Option C — Plugin `file` (record-side fork)** : utiliser le plugin `file` ALSA pour dupliquer la sortie vers un FIFO/loopback que le silence detector écoute. Léger, simple à configurer.

→ **Choisir au début du prompt en testant, puis documenter le choix.** Mettre à jour la suite des tâches en conséquence.

### Tâches

- [ ] **Décision tap ALSA** (cf ci-dessus) — documenter le choix dans le code et dans ce plan

- [ ] **`backend/core/silence_detector.py`** — nouveau module
  - [ ] Class `SilenceDetector` :
    - Constructor `(capture_device: str, threshold_dbfs: float = -60.0, idle_seconds: float = 2.0, sample_rate: int = 44100, channels: int = 2)`
    - Méthodes `async start()`, `async stop()`, `set_callbacks(on_silence_started, on_audio_resumed)`
    - Implémentation : thread bloquant lit chunks ~100ms via `pyalsaaudio` (ou `alsaaudio`), calcule peak/RMS via numpy, déclenche les callbacks via `asyncio.run_coroutine_threadsafe`
    - Hystérésis : ne pas toggler à chaque échantillon — un seul event "silence" après N secondes consécutives sous le seuil, un seul event "resumed" dès qu'un sample dépasse le seuil
  - [ ] Vérifier que `pyalsaaudio` ou équivalent est dans `requirements.txt` ; sinon l'ajouter

- [ ] **ALSA** — selon l'option retenue ci-dessus
  - [ ] Modifier `rootfs/etc/asound.conf` pour exposer un point de capture par source taggée (BT, ROC)
  - [ ] Si bump de `pcm_substreams` nécessaire : modifier `install/alsa.sh:24` (`pcm_substreams=8` → `16`)
  - [ ] Si Option A retenue : créer service systemd `milo-bt-silencetap.service` et `milo-mac-silencetap.service` avec `alsaloop`
  - [ ] Documenter le nouveau routing dans `docs/architecture.md`

- [ ] **`backend/sources/bluetooth/source.py`**
  - [ ] Set `auto_disconnect_enabled = True` dans `__init__`
  - [ ] Instancier `SilenceDetector(capture_device=<tap_bt>)` comme membre de la source
  - [ ] Dans `_on_device_connected()` : démarrer le silence detector (`await self._silence_detector.start()`) et câbler les callbacks vers `_start_pause_timer` / `_cancel_pause_timer`
  - [ ] Dans `_on_device_disconnected()` et `_cleanup()` : stopper le silence detector
  - [ ] Override `_on_auto_disconnect()` :
    ```python
    if self.connected_device:
        addr = self.connected_device["address"]
        await self._disconnect_device(addr)
    # ne PAS stopper bluealsa.service
    ```
  - [ ] Dans `initialize()` : appeler `await self._load_auto_disconnect_config()`

- [ ] **`backend/sources/mac/source.py`** (et nouveau `MacSource` si pas déjà comme les autres)
  - [ ] Set `auto_disconnect_enabled = True`
  - [ ] Instancier `SilenceDetector(capture_device=<tap_roc>)`
  - [ ] Au moment où la source devient "connected" (détection de packets ROC) : démarrer le silence detector
  - [ ] Override `_on_auto_disconnect()` : `await self._do_stop()` puis `transition_to_source(AudioSource.NONE)`
  - [ ] Dans `initialize()` : appeler `await self._load_auto_disconnect_config()`

- [ ] **Tuning**
  - [ ] Tester avec un téléphone iOS (Safari + YouTube) en BT — doit déconnecter après le délai
  - [ ] Tester avec macOS (ROC) en lecture pause — doit déconnecter
  - [ ] Tester un passage très silencieux (intro de podcast) — ne doit PAS déclencher si délai > durée du silence
  - [ ] Ajuster `threshold_dbfs` et `idle_seconds_before_event` si faux positifs/négatifs

- [ ] **Tests**
  - [ ] Test unitaire `SilenceDetector` avec mock ALSA capture (injecter chunks bruyants et silencieux)
  - [ ] Vérifier hystérésis (silence court ne trigger pas, resume rapide annule le pending event)

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
