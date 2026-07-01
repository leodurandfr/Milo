# Plan d'implémentation — Source DLNA (UPnP Media Renderer)

> Objectif : faire apparaître Milō comme une **enceinte DLNA** (rôle DMR — Digital Media Renderer)
> que n'importe quelle app de contrôle (BubbleUPnP, Hi-Fi Cast, NAS Synology/QNAP, Plex, JRiver,
> foobar2000, Audirvana…) peut piloter en lui poussant un flux audio, métadonnées riches incluses.

---

## 🧭 Curseur d'avancement — lire EN PREMIER

> **Protocole d'exécution.** Quand on me dit « passe à la prochaine phase du plan DLNA », je :
> 1. lis ce curseur, prends la **première phase non cochée**,
> 2. exécute **toutes** ses tâches (elles sont autonomes et exactes ci-dessous),
> 3. valide son **gate**,
> 4. coche la phase ici + coche ses tâches, puis m'arrête et rends la main.
> Aucune phase n'anticipe la suivante. Chaque phase est vérifiable seule.

- [x] **Phase 0** — POC de faisabilité (hors repo) — ✅ **PASSÉ (2026-07-01)** : gmrender visible (SSDP), audio OK (MP3/FLAC/FLAC 24-192/WAV/AAC/ALAC), bridge GENA fiable (title/artist/album/art/état/position). Détails + findings §2.
- [~] **Phase 1** — Image/build : daemon + ALSA + Snapcast + systemd — **IMPLÉMENTÉE (2026-07-01)** : tous les fichiers écrits + **validation statique verte** (bash -n, `systemd-analyze verify`, `asound.conf` parse → `milo_dlna_direct` listé). ⚠️ **Gate runtime NON encore validé** : test d'écoute on-device requis (reboot pour `pcm_substreams=9` + arrêt du POC P0 qui squatte `:49494`/UUID) — cf. §3 fin. Binaire réel = **`/usr/bin/gmediarender`** (apt, pas `/usr/local/bin`).
- [~] **Phase 2** — Backend : source Famille B + bridge + wiring — **IMPLÉMENTÉE (2026-07-01)** : 4 fichiers `sources/dlna/` + wiring (enum, `dependencies.py` creator+register, `DEFAULT_DOCK_APPS`, `main.py` route, `requirements.txt`). **`pytest` 1682 vert (contrat Milo-Mac inclus), `ruff` clean, import/instanciation OK.** ⚠️ Reste le **smoke-test runtime** (push DLNA → WAITING→ACTIVE + métadonnées au journal) — bundlé avec le gate Phase 1 au reboot final.
- [ ] **Phase 3** — Frontend : composant + routing UI + i18n + icône — *gate : lecteur plein écran s'affiche au push BubbleUPnP*
- [ ] **Phase 4** — Multiroom, edge cases, tests, docs — *gate : `pytest` + lint verts, bascules de sources OK*

**Prochaine phase à exécuter : Phase 3 (frontend). Phases 1 & 2 IMPLÉMENTÉES + validées (statique/pytest) — leurs deux gates runtime (écoute direct+multiroom, push DLNA→WAITING→ACTIVE) sont bundlés dans UN reboot final on-device.**

---

## 1. Décision d'architecture

### Famille de source
DLNA = **Famille B (Passive player)** — exactement comme AirPlay : contrôle externe (l'émetteur
pilote la lecture), métadonnées riches (titre/artiste/album/pochette), **pas de contrôle depuis l'UI
Milō** (`<AudioPlayerFull :showControls="false" />`). On suit donc la layout AirPlay à l'identique.

> **Pourquoi « afficher seulement », pas d'exploration.** DLNA répartit 3 rôles : serveur (DMS, ex.
> NAS/Plex qui stocke la bibliothèque), contrôleur (DMC, ex. BubbleUPnP/mconnect/Audirvana qui
> parcourt, cherche, choisit, gère la file d'attente et le next/prev) et renderer (DMR, qui reçoit
> « joue ça » et le joue). **Milō est uniquement DMR** : toute la navigation vit dans l'app de
> contrôle, Milō ne fait qu'afficher le now-playing. D'où l'absence de tracklist/queue/recherche dans
> l'UI Milō — ce n'est pas un manque à combler, c'est le partage de rôles du standard.

### Moteur retenu : `gmrender-resurrect` (daemon externe)
On enveloppe un daemon DLNA éprouvé, comme Milō enveloppe déjà shairport-sync (AirPlay) et
go-librespot (Spotify). Choix justifié :

- **gmrender-resurrect** : renderer UPnP/DLNA léger, pensé Raspberry Pi, sortie **ALSA via GStreamer**,
  maintenu (maj jan. 2025). Gère toute la partie « device » UPnP (SSDP, GENA, AVTransport,
  RenderingControl) que les contrôleurs (BubbleUPnP…) attendent au carré — c'est le point le plus
  risqué à réimplémenter soi-même.
- **upmpdcli écarté** : impose MPD comme moteur → second player en doublon de mpv. Contraire au
  principe « single optimized path ».

> GStreamer devient un moteur audio de plus, mais Milō a **déjà** un moteur par source (mpv pour
> radio/podcast/cd, shairport, go-librespot, ROC, bluez-alsa). La règle « single path » interdit les
> *chemins de repli redondants*, pas un moteur par source. GStreamer est donc cohérent.

### Le point délicat : récupérer les métadonnées de gmrender
gmrender n'émet pas de métadonnées sur stdout/pipe (contrairement à shairport). Approche retenue :

**Bridge control-point local.** Le backend agit comme un point de contrôle UPnP **vers le gmrender
local** (`localhost`), s'abonne en **GENA** aux events `LastChange` de ses services AVTransport /
RenderingControl, et reçoit ainsi en push (event-driven, conforme à la doctrine Milō) :
titre/artiste/album/URI pochette + état de lecture. La **position** est lue par poll périodique
`GetPositionInfo` (comme chaque source poll mpv/RTP pour la progression).

- Lib : **`async-upnp-client`** (maintenue, utilisée par Home Assistant) côté point de contrôle.
- Ce module joue exactement le rôle du `metadata_reader.py` d'AirPlay : il expose les **mêmes
  callbacks** (`on_metadata`, `on_play_state`, `on_artwork`, `on_progress`, `on_connection`), donc
  `source.py` reste quasi identique en forme à `AirPlaySource`.
- Pour rendre le bridge déterministe : gmrender lancé avec **port fixe** (`-p 49494`) et **UUID fixe**
  (`-u <uuid>`) → le backend connaît l'URL de description (`http://localhost:49494/...`) sans dépendre
  d'un M-SEARCH.

### Alternative documentée (non retenue en v1)
**Renderer maison en Python → mpv** : zéro daemon/GStreamer, un seul moteur (mpv), mais impose
d'implémenter tout le device UPnP **+ l'eventing GENA** (la partie que les contrôleurs jugent
sévèrement). Risque de bugs de compat élevé. À ne reconsidérer que si éviter GStreamer devient
prioritaire. Le plan est structuré pour que seule la **Phase 1** (daemon + ALSA) et le module bridge
changent dans ce cas — le reste (source Famille B, frontend) est identique.

### ⚠️ Deux chemins d'install à garder synchro
Milō s'installe de **deux** façons, et plusieurs réglages sont **dupliqués** entre elles — toute modif
système doit être répercutée aux deux, sinon l'image et une install manuelle divergent :
1. **Image pi-gen** — `pi-gen/stage-milo/` (build de l'image SD livrée).
2. **Installeur modulaire** — `install.sh` + `install/*.sh` (install/réinstall sur un Pi existant).

Certaines configs ont **une seule source de vérité** partagée (bien), d'autres sont **copiées-collées**
(à toucher deux fois) :

| Config | Source de vérité | Dupliqué ? |
|---|---|---|
| `asound.conf` | `rootfs/etc/asound.conf` (les deux chemins le copient) | ✅ unique |
| Snapserver | `install/snapcast.sh` | ✅ unique |
| `snd-aloop pcm_substreams` | — | ❌ **DEUX endroits** : `install/alsa.sh:24` **et** `pi-gen/stage-milo/02-install-milo/01-run.sh:46` |
| units systemd | glob `system/*.service` | ✅ auto (pi-gen + `milo-deploy-update`) — rien à énumérer |
| deps apt | `pi-gen/stage-milo/00-install-deps/00-packages` (+ éventuel module `install/`) | à vérifier selon paquet |

---

## 2. Phase 0 — POC de faisabilité (hors repo)

**But :** lever les 3 incertitudes techniques avant tout code Milō.

- [x] **Daemon** : installer gmrender-resurrect sur le Pi (paquet apt `gmediarender` + `gstreamer1.0-*`,
  ou compilation). Le lancer à la main :
  `gmediarender -f "Milō" -p 49494 -u <uuid> --gstout-audiosink alsasink --gstout-audiodevice default`.
  → **RÉSULTAT** : `gmediarender` 0.3-1 dispo en apt (pas de compil pour le binaire). ⚠️ **`-f "Milō"`
  crashe** (`Invalid byte sequence in conversion input` — le `ō`) : la v0.3 ne fait pas de `setlocale()`,
  échec même avec `LANG/LC_ALL=*.UTF-8`. → nom ASCII `"Milo"` **ou** compiler + patch `setlocale` (§3.4).
  ⚠️ `--gstout-audiodevice default` = `hw:0` **occupé par CamillaDSP** → viser `milo_dlna` (slot dédié §3.2).
- [x] **Découverte + audio** : depuis BubbleUPnP (Android) / un NAS, vérifier que « Milō » apparaît
  comme renderer, lui pousser un morceau (MP3, **FLAC**, **FLAC 24/192**) → audio en sortie. **Noter
  la liste exacte des plugins GStreamer** nécessaires pour la couverture codec (FLAC/ALAC/AAC/WAV/MP3,
  hi-res 24/192) — servira en Phase 1.
  → **RÉSULTAT** : SSDP OK, **son confirmé aux enceintes** (testé via un point de contrôle SOAP maison,
  pas besoin de BubbleUPnP). **Plugins requis** (empirique, chaque codec poussé/décodé) : `-plugins-base`
  (typefind/audioconvert/resample/playbin) + `-plugins-good` (flac, mp3=mpg123, wav, audioparsers,
  qtdemux, souphttpsrc) + `-plugins-bad` (faad→AAC) + `gstreamer1.0-alsa` (alsasink) +
  **`gstreamer1.0-libav` (avdec_alac→ALAC)**. **⚠️ libav manque au §3.1 initial** — sans lui l'ALAC
  échoue (`Missing decoder: ALAC`) ; tout le reste (FLAC/AAC/WAV/MP3 hi-res) marche sans.
- [x] **Bridge métadonnées** : prototype `async-upnp-client` qui se connecte à
  `http://localhost:49494/...`, construit un `DmrDevice`, s'abonne en GENA à AVTransport, et logge
  titre/artiste/album/pochette/état + position (`GetPositionInfo`) au fil de la lecture.
  → **RÉSULTAT** : **GENA fiable** (`async-upnp-client` 0.47). Le proto capte en push `état` +
  `title/artist/album/albumArtURI` (DIDL-Lite) et la position en poll. ⚠️ **gmrender écoute sur l'IP
  LAN, PAS `localhost`** (rien sur 127.0.0.1) → le bridge Phase 2 doit viser l'IP LAN (§4.2).
- [x] **Trancher** : disponibilité `gmediarender` en apt vs compilation ; empreinte mémoire/CPU
  GStreamer en hi-res (fixer un `MemoryMax`) ; volume UPnP **ignoré en v1** (CamillaDSP autoritaire,
  lancer volume fixe 0 dB, équivalent `ignore_volume_control` de shairport).
  → **RÉSULTAT** : apt ✅ (binaire). Hi-res FLAC 24/192 : **RSS ~70 MiB** (pic 71), **CPU ~2-3 % d'un
  cœur** → `MemoryMax=256M` conseillé (marge pochette+buffering). Volume ignoré (`--gstout-initial-volume-db 0`).

**Gate Phase 0 : ✅ PASSÉ (2026-07-01)** — daemon + découverte/audio + bridge GENA tous OK, bridge
fiable (plans B poll-only/allégé/renderer-maison **non nécessaires**).

---

## 3. Phase 1 — Image/build : daemon, ALSA, Snapcast, systemd (zéro code applicatif)

### 3.1 Packages + binaire (les DEUX chemins d'install)
- [x] Ajouter les plugins GStreamer retenus en Phase 0 à
  `pi-gen/stage-milo/00-install-deps/00-packages` : `gstreamer1.0-plugins-base`,
  `gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-bad`, `gstreamer1.0-alsa`,
  **`gstreamer1.0-libav`** (requis pour l'ALAC — `avdec_alac` ; confirmé en P0). Idem `install/dlna.sh`.
- [x] **Binaire `gmediarender`** → dispo en **apt** (P0), donc **ajouté à `00-packages`** ; le binaire
  atterrit en **`/usr/bin/gmediarender`** (pas `/usr/local/bin` — pas de build, pas de branche source).
- [x] **Cohérence install manuel** : module `install/dlna.sh` créé (`install_gmediarender()` = apt des
  plugins + gmediarender + disable du service packagé), `source`-r dans `install.sh` et appelé dans
  `main()` après `install_snapcast` — même install que l'image.

### 3.2 ALSA — 3 entrées + bump Loopback (slot 8)
Fichier : `rootfs/etc/asound.conf` (source unique, copiée par les deux chemins). Slots **1..7** pris,
slot 0 = DSP → DLNA prend le **slot 8**. Suivre le motif `milo_cd` (bloc alias l.85, direct l.126,
multiroom l.195) :
- [x] **Alias dynamique** `pcm.milo_dlna` (concat sur `MILO_MODE`) — après le bloc `milo_cd`.
- [x] **Variante direct** `pcm.milo_dlna_direct` → `slave.pcm "camilladsp"`.
- [x] **Variante multiroom** `pcm.milo_dlna_multiroom` → Loopback `device 0 subdevice 8`.
- [x] **Bump `snd-aloop pcm_substreams` 8 → 9 aux DEUX endroits** (sinon le slot 8 n'existe pas) :
  `install/alsa.sh:24` **et** `pi-gen/stage-milo/02-install-milo/01-run.sh:46` (+ en-tête `asound.conf`
  slots 1..8). ⚠️ Effectif seulement après reboot (module chargé actuellement à 8).

### 3.3 Snapcast / Snapserver — 🔴 sinon multiroom muet
Fichier : `install/snapcast.sh` (source unique). Le subdevice 8 doit être **lu** par Snapserver :
- [x] Ajouter `/DLNA` à l'agrégateur meta (l.33) :
  `source = meta:///Bluetooth/ROC/Spotify/Radio/Podcast/AirPlay/CD/DLNA?name=Multiroom`.
- [x] Ajouter la source ALSA après la ligne CD (l.41) :
  `source = alsa:///?name=DLNA&device=hw:1,1,8&idle_threshold=5000`.

### 3.4 systemd — `system/milo-dlna.service` (nouveau)
Calqué sur `milo-airplay.service` (déployé auto via glob `system/*.service`, rien à énumérer) :
- [x] `BindsTo=milo-backend.service` ; `After=network-online.target sound.target milo-backend.service
  milo-camilladsp.service` ; `Wants=network-online.target` ; `Requires=sound.target` ;
  `EnvironmentFile=/var/lib/milo/routing.env` (charge `MILO_MODE`) ; `Environment=HOME=/home/milo`
  (registre plugins GStreamer) ; `User=milo`, `Group=audio`, `Restart=always`, `RestartSec=5`,
  **`MemoryMax=256M`** (mesuré P0 : ~70 MiB hi-res).
- [x] `ExecStart=/usr/bin/gmediarender -f "Milo" -p 49494 -u b48ac0ce-d18a-4ace-ad6e-250398667052
  --gstout-audiosink alsasink --gstout-audiodevice milo_dlna --gstout-initial-volume-db 0`.
  ⚠️ **Nom ASCII `"Milo"`** (le paquet apt v0.3 crashe sur `"Milō"` — P0) ; **chemin `/usr/bin`** (apt,
  corrigé vs `/usr/local/bin` du plan). Flags `-f/-p/-u/--gstout-*` re-vérifiés dans `--help` sur le Pi.
- [x] `[Install] WantedBy=multi-user.target` (cohérent avec les autres units source ; le cycle de vie
  reste piloté par le backend via le state machine + `BindsTo`).

**Gate Phase 1 :** `systemctl start milo-dlna` → push BubbleUPnP → son en sortie **en mode direct ET
en mode multiroom** (vérifier la bascule `MILO_MODE` + routage Snapcast slot 8). → cocher la phase.

**Procédure on-device pour fermer le gate (à exécuter sur l'unité) :**
1. **Arrêter le POC P0** qui squatte `:49494`/UUID : `systemctl --user stop gmediarender-poc miloweb-poc`
   (sinon collision de port + UUID SSDP avec `milo-dlna.service`).
2. **Déployer** les configs sur le Pi vivant : `asound.conf`, `snapserver.conf`, `snd-aloop.conf`
   (`pcm_substreams=9`) et le unit `milo-dlna.service` (via `milo-deploy-update write-config` +
   `systemctl daemon-reload`).
3. **Reboot** (indispensable : `pcm_substreams` passe de 8 → 9, module rechargé — impossible à chaud car
   CamillaDSP tient snd-aloop). Après reboot : `cat /sys/module/snd_aloop/parameters/pcm_substreams` = 9.
4. `sudo systemctl start milo-dlna` → depuis un contrôleur DLNA (BubbleUPnP / point de contrôle SOAP),
   **pousser un morceau** → son aux enceintes **en mode direct**.
5. Basculer en **multiroom** (`MILO_MODE=multiroom` via l'UI/routing) → re-pousser → son via Snapcast
   (slot 8). → alors cocher `[~]` → `[x]` sur la ligne Phase 1 du curseur.

---

## 4. Phase 2 — Backend : source Famille B + bridge + wiring

### 4.1 Dépendance
- [x] Ajouter `async-upnp-client` à `requirements.txt` (`>=0.47.0`, validé P0) — déjà dans le venv de dev ;
  l'image le prendra au build.

### 4.2 Bridge UPnP — `backend/sources/dlna/metadata_reader.py` (`DlnaBridge`) ✅
- [x] Se connecte à la description gmrender (`http://<LAN_IP>:49494/description.xml`, IP via
  `get_local_ip()`), construit un `DmrDevice`. `AiohttpNotifyServer` bindé sur l'IP LAN (gmrender
  n'écoute pas loopback). `async-upnp-client` 0.47.
- [x] **S'abonne (GENA)** (`async_subscribe_services(auto_resubscribe=True)`) → `on_event` →
  `_dispatch_state` lit `transport_state/media_title/artist/album/image_url` → `on_metadata/on_play_state/on_artwork`.
  Émission uniquement sur changement (GENA renvoie l'état complet).
- [x] **Poll position** : `_poll_once` (`async_update` → `GetPositionInfo`) → `on_progress(position_ms,
  duration_ms)`, via `BackgroundTaskSet` (pas de `create_task` brut) ; broadcasts rate-limités 30 s côté source.
- [x] Robustesse : boucle superviseur (reconnexion si gmrender disparaît), corps en
  `try/except Exception` + log + retry (doctrine « background loop »). Forme calquée sur AirPlay.

### 4.3 Source + routes — `backend/sources/dlna/` ✅
- [x] **`source.py`** → `class DlnaSource(BaseAudioSource)`, `source_id="dlna"`,
  `service_name="milo-dlna.service"`, calqué sur `AirPlaySource` :
  - `__init__` : config `{port}` (host optionnel), `auto_stop_enabled=True`, `auto_stop_delay=10s`,
    champs `_device_connected/_is_playing/_client_name`, artwork (`_artwork_data/_mime/_hash`).
  - `_do_start` : `_start_service_and_wait(1.5s)` → instancie `DlnaBridge` avec les callbacks →
    `_update_connection_state()`. **Divergence assumée vs plan** : pas de `_do_restart` custom ;
    `_on_auto_stop` override → retour **WAITING sans bouncer gmrender** (renderer sans session, reste
    découvrable — contrairement à AirPlay qui restart shairport). Reroute direct↔multiroom = `stop()/start()` de base (OK).
  - `_handle_command` : **non supporté** (`COMMANDS={}`, passif comme AirPlay).
  - Callbacks alimentant `self._metadata` (title/artist/album/album_art_url/album_art_width/
    is_playing/client_name) et `broadcast_position_update()`.
  - Artwork : URL DIDL-Lite (`media_image_url`), **fetch async** (aiohttp), dimensions via Pillow,
    `album_art_url = /api/dlna/artwork?v=<hash>` (motif AirPlay exact).
- [x] **`routes.py`** : `APIRouter(prefix="/dlna")`, `GET /artwork`, via `make_source_dependency("DLNA")`
  + `setup_dlna_routes`. **Pas** de `GET /dlna/status`, **pas** de `POST /dlna/restart`.
- [x] **`__init__.py`** : `__all__ = ["DlnaSource", "router", "setup_dlna_routes"]`.

### 4.4 Wiring (points exacts) ✅
- [x] `backend/core/models/audio_state.py` : `DLNA = "dlna"` ajouté à l'enum `AudioSource`.
- [x] `backend/dependencies.py` : creator `"dlna_source"` (config `{port:49494}`) + STEP 3
  `state_machine.register_source(AudioSource.DLNA, get_service("dlna_source"))`.
- [x] `backend/config/constants.py` : `"dlna"` ajouté à `DEFAULT_DOCK_APPS` (après `cd`) ;
  `AUDIO_SOURCE_APPS` auto-dérivé confirme `dlna`.
- [x] `backend/main.py` : `setup_dlna_routes` importé + câblé (`dlna_router` include, prefix `/api`).

### 4.5 Contrat Milo-Mac — aucune modif ✅
- [x] Manifeste/snapshot **non touchés** : broadcast générique `("source","state_changed")` couvre DLNA ;
  `/api/dlna/*` internes ; `active_source` opaque côté Milo-Mac. **`pytest` contrat vert.** Forme du
  payload metadata documentée en docstring sur `DlnaSource._update_connection_state`.

**Gate Phase 2 :** push BubbleUPnP → `journalctl -u milo-backend` montre WAITING→ACTIVE avec
métadonnées correctes ; `pytest` (contrat inclus) vert. → cocher la phase.

---

## 5. Phase 3 — Frontend

- [ ] `frontend/src/constants/audioSources.js` : ajouter `'dlna'` à `ALL_AUDIO_SOURCES` (après `'airplay'`).
- [ ] `frontend/src/components/dlna/DLNASource.vue` (nouveau) :
  `<AudioPlayerFull source="dlna" :showControls="false" />` (motif AirPlaySource.vue).
- [ ] `frontend/src/components/audio/AudioSourceView.vue` : import async `DLNASource` + branche
  `v-else-if="shouldShowDLNA"` + `shouldShowDLNA = computed(() => richSource.value === 'dlna')`.
  **+ `currentDeviceName`** : `case 'dlna': return meta.client_name || '';` (motif `'airplay'`), sinon
  la carte de repli `AudioSourceStatus` affiche un nom vide.
- [ ] `frontend/src/composables/useRichDisplay.js` : case `'dlna'` →
  `state==='active' && !!m.is_playing && !!m.title && !!m.artist && (m.album_art_width||0) > AIRPLAY_MIN_ARTWORK_PX`
  (mêmes critères qu'AirPlay).
- [ ] `frontend/src/components/ui/AppIcon.vue` : ajouter `'dlna'` au validator + `iconMapping`.
- [ ] `frontend/src/assets/app-icons/dlna.svg` (nouveau).
- [ ] `frontend/src/stores/settingsStore.js` : `dlna: true` dans `dockApps`.
- [ ] `frontend/src/schemas/ws.js` : ajouter `'dlna'` à l'enum de `source.position_update`.
- [ ] i18n : `frontend/src/locales/english.json` (canonique) `audioSources.dlna = "DLNA"`, puis les **7
  autres langues** (allemand, espagnol, français, portugais, italien, hindi, chinois).
- [ ] `unifiedAudioStore.js` + `App.vue::resyncStores()` : **aucun changement** (state générique,
  full_state au reconnect).

**Gate Phase 3 :** `npm run dev`, push BubbleUPnP → lecteur plein écran DLNA (pochette/titre/artiste,
nom du device dans la barre source), icône dans le dock, libellé i18n OK. → cocher la phase.

---

## 6. Phase 4 — Multiroom, edge cases, tests, docs

- [ ] **Multiroom** : vérifier routage Snapcast subdevice 8, bascule direct↔multiroom pendant lecture
  (`release_for_reroute`/`acquire_after_reroute` héritées de Base — vérifier qu'elles suffisent ou
  surcharger).
- [ ] **Bascule de sources** : DLNA→Spotify→DLNA, DLNA pendant qu'AirPlay joue, etc. (priorité state machine).
- [ ] **Auto-stop** : pause prolongée → retour WAITING propre (timer hérité, motif AirPlay).
- [ ] **Reconnexion bridge** : tuer/redémarrer gmrender → le backend re-souscrit, pas de fuite de tâche.
- [ ] **Tests** : `pytest backend/` (contrat Milo-Mac vert), `ruff check backend/`, `npm run lint`.
  (Vitest reste skip en CI — connu.)
- [ ] **Docs** : `docs/architecture.md` (sources, persistance, asound, systemd), `docs/development.md`
  (checklist « ajout d'une source » version DLNA), `README.md` (feature + **limite** : DLNA « Play To »
  = jouer une **bibliothèque musicale** ; pas d'audio de vidéo/film/TV, pas de lip-sync, pas de sortie
  audio déportée à la Bluetooth/AirPlay), manuel bilingue si pertinent (docs en anglais).
- [ ] **Nettoyage du POC Phase 0** (scaffolding hors repo, sur le Pi de dev) : arrêter/supprimer les
  services transitoires `--user` `gmediarender-poc` / `miloweb-poc` / `bridgepoc`
  (`systemctl --user stop <u> && systemctl --user reset-failed <u>`), supprimer les fichiers de test du
  scratchpad, et vérifier qu'aucun `gmediarender` lancé à la main ne tourne (le seul restant doit être
  `milo-dlna.service`). NB : les paquets apt (`gmediarender`, `gstreamer1.0-*`) + `async-upnp-client`
  installés manuellement en P0 sont désormais officialisés par les Phases 1/2 — **les garder**.

**Gate Phase 4 :** tout vert → plan terminé. → cocher la phase.

---

## 7. Récap des fichiers touchés

**Nouveaux**
- `backend/sources/dlna/{__init__,source,metadata_reader,routes}.py`
- `system/milo-dlna.service`
- `install/dlna.sh`
- `frontend/src/components/dlna/DLNASource.vue`
- `frontend/src/assets/app-icons/dlna.svg`

**Modifiés — backend**
- `backend/core/models/audio_state.py` (enum) · `backend/dependencies.py` (creator + register_source)
- `backend/config/constants.py` (DEFAULT_DOCK_APPS) · app init (câblage `setup_dlna_routes`)
- `requirements.txt` (`async-upnp-client`)

**Modifiés — image/système**
- `rootfs/etc/asound.conf` (3 entrées milo_dlna, slot 8)
- `install/alsa.sh:24` **et** `pi-gen/stage-milo/02-install-milo/01-run.sh:46` (`pcm_substreams` → 9)
- `install/snapcast.sh` (meta agrégateur + source ALSA DLNA)
- `pi-gen/stage-milo/00-install-deps/00-packages` (plugins GStreamer + gmediarender si apt)
- `pi-gen/stage-milo/01-install-audio/01-run.sh` (build gmediarender si compilé)
- `install.sh` (source + appel `install/dlna.sh`)

**Modifiés — frontend**
- `constants/audioSources.js`, `components/audio/AudioSourceView.vue`, `composables/useRichDisplay.js`,
  `components/ui/AppIcon.vue`, `stores/settingsStore.js`, `schemas/ws.js`, `locales/*.json` (×8)

**Inchangés (auto)** : `unifiedAudioStore.js`, `App.vue::resyncStores()`, contrat Milo-Mac
(manifeste + snapshot), déploiement units (glob `system/*.service`).

---

## 8. Risques & questions ouvertes (levées en Phase 0/1)

1. **Fiabilité du bridge GENA** vers un gmrender local — risque #1. Plan B : poll-only.
2. **`snd-aloop pcm_substreams`** — bumper aux **deux** endroits ; sinon slot 8 inexistant.
3. **Snapserver** — sans la source ALSA + agrégateur meta, le multiroom est **muet** (risque #2).
4. **Couverture codec GStreamer** (FLAC/ALAC/AAC hi-res) — choisir les plugins en P0.
5. **Volume UPnP** — v1 : ignoré, CamillaDSP autoritaire.
6. **Empreinte mémoire/CPU** GStreamer sur le Pi en hi-res — mesurer, fixer `MemoryMax`.
7. **Recouvrement UX avec AirPlay** — DLNA cible Android/NAS/hi-res ; bien le positionner dans le dock.
8. **Cas d'usage NON couverts** — DLNA « Play To » pousse un **média entier** à un renderer **sans
   écran** ; ce n'est **pas** une sortie audio déportée. Pas d'audio d'une vidéo (VLC), pas d'audio
   TV/Freebox (Jellyfin), pas de lip-sync. À documenter pour éviter la confusion.
