# PLAN_QOBUZ — Ajout de Qobuz Connect à Milō (Famille B)

## Pour l'agent qui exécute ce plan (lire en premier)

1. Lis ce fichier en entier (il est conçu pour être self-contained). Repère la **première case non cochée** `[ ]`.
2. Lis le **Journal des décisions** ET le bloc **« Surface qobuz-proxy (déjà cartographiée) »** ci-dessous : tout le reverse-engineering du proxy y est figé. **Ne re-fouille pas le repo qobuz-proxy**, ne re-débats pas les décisions D1/D2/D3.
3. Exécute **UNIQUEMENT cette étape**. Suis « Réf » → « Action » → « Vérif ».
4. Coche la case `[x]`, fais le commit indiqué, puis **ARRÊTE-TOI** (un commit par étape, on économise les tokens).
5. Solo dev → commit directement sur `main` (pas de branche). Comments/docstrings en **anglais**.
6. Respecte [CLAUDE.md](CLAUDE.md) : source Famille B (comme AirPlay), ALSA-only + CamillaDSP toujours dans le chemin, broadcast via `state_machine`, settings via `SettingsService`, pas de migration/shim, background tasks via `BackgroundTaskSet`, lint floor (`ruff check backend/`, `pytest backend/`, `npm run lint`, `npm run build`).
7. **Ajout purement additif** → ne touche PAS [backend/tests/contracts/milo_mac_contract.json](backend/tests/contracts/milo_mac_contract.json) (Milo-Mac ne consomme pas Qobuz).

### Étapes qui nécessitent TON intervention physique (préviens l'utilisateur au début de ces conversations)
- **0.1** et **4.1** : jouer un titre depuis l'app Qobuz iOS (sur ton téléphone) pour valider le son / les transitions.
- **1.1** : reboot du Pi (bump module snd-aloop).
- **1.4** : login Qobuz une seule fois (OAuth via navigateur LAN sur `http://milo.local:8689`, ou coller un token).

---

## Contexte (pourquoi)

Qobuz Connect (mai 2025) n'a **pas de voie officielle DIY**. On passe par **qobuz-proxy** ([github.com/leolobato/qobuz-proxy](https://github.com/leolobato/qobuz-proxy) — Python, MIT, maintenu, sur PyPI), un **device Qobuz Connect virtuel** par reverse-engineering, intégré **comme sidecar** sur le modèle exact de go-librespot/Spotify déjà en place. Risque = fragilité (RE récent, 1 mainteneur) ; pas de risque légal (même catégorie que go-librespot déjà embarqué). À traiter comme un essai.

**Famille B tranchée** (voir D2) : le contrôle de lecture appartient à l'app Qobuz, pas au proxy. Milō **affiche + joue**, comme AirPlay. Pas de contrôles on-device.

## Journal des décisions

- **D1 — Sortie audio qobuz-proxy → ALSA `milo_qobuz`** : **(a) backend `local` (PortAudio) ciblant le PCM nommé `milo_qobuz`.** Condition : le PCM doit porter un `hint { show on }` ALSA, sinon PortAudio ne l'énumère pas (`resolve_device()` ne matche que les devices énumérés). **Validé/ajusté en 0.1.** Fallbacks si le hint ne suffit pas : (b) patch mince du backend local pour ouvrir un PCM par nom (pyalsaaudio) ; (c) pipe → mpv/aplay. Volume = CamillaDSP, le volume interne du proxy est ignoré.
- **D2 — Famille de source : B (récepteur passif, métadonnées seules, `showControls=false`, comme AirPlay).** Tranché depuis le code : qobuz-proxy n'expose **aucun** endpoint de contrôle (play/pause/next/seek), et position/durée ne sortent pas en HTTP. Métadonnées lues par **polling HTTP** de `GET /api/status`.
- **D3 — Emplacement qobuz-proxy : `/var/lib/milo/qobuz/`** (venv + `config.yaml` + cache token OAuth), parallèle à `go-librespot`.
- **D4 — Login Qobuz = écran « Compte Qobuz » intégré aux réglages Milō (Option B).** Contexte : contrairement à Spotify Connect (zeroconf sans login), qobuz-proxy **exige un login de compte one-time** — sans lui, le device ne s'annonce pas dans l'app Qobuz (cf. « Surface », `app.py` ne démarre les speakers qu'une fois authentifié). Il n'existe **pas** de voie Qobuz Connect certifiée/DIY, donc ce login est incontournable. UX retenue : un écran de réglages Milō (bouton *Connecter* → OAuth de qobuz-proxy, statut lu depuis `/api/status → auth`, *Déconnecter* → `POST /api/auth/logout`), via un relais backend Milō (pas d'appel cross-origin direct au `:8689`). Token-collé = fallback dev uniquement.

---

## Surface qobuz-proxy (déjà cartographiée — NE PAS re-explorer)

**Architecture.** qobuz-proxy est un **renderer esclave** de Qobuz Connect. L'app/cloud Qobuz est le contrôleur : les commandes (`SET_STATE`=play/pause/stop/seek, loop, shuffle) arrivent **du cloud vers le proxy** en protobuf. Le proxy ne fait que *recevoir* ces commandes et *remonter* son état (position/durée/volume) au cloud. **Il n'y a aucun canal de contrôle local.**

**API HTTP locale (aiohttp, port 8689) — liste exhaustive des routes** (`qobuz_proxy/webui/routes.py` + `speaker_routes.py`) :
```
GET  /                         GET/POST      /api/speakers
GET  /api/status               PUT/DELETE    /api/speakers/{id}
POST /api/auth/logout          POST          /api/discover/dlna
GET  /auth/login | /auth/callback   GET      /api/discover/audio-devices
```
→ **Aucune route de lecture.** La web UI (`static/app.js`) fait juste `setInterval(fetchStatus, 3000)` sur `/api/status`. Pas de WebSocket.

**`GET /api/status`** → JSON `{ auth, speakers[], version, uptime }`. Chaque speaker (voir `Speaker.get_status()`) :
```json
{
  "id": "milo",              // slugify(name)
  "name": "Milō",
  "backend": "local",
  "status": "playing",       // playing | paused | idle | disconnected
  "config": { ... },
  "now_playing": {           // présent SEULEMENT si status ∈ {playing, paused}
    "title": "...", "artist": "...", "album": "...",
    "album_art_url": "https://.../images.qobuz.com/...",  // URL CDN → charge direct dans le kiosk, PAS de route binaire
    "quality": "...", "volume": 42
  }
}
```
**PAS de `position`/`duration` en HTTP** (elles ne partent qu'au cloud). **PAS de nom d'appareil contrôleur** (le proxy ne connaît que le nom de l'enceinte = « Milō »).

**Config** (`config.yaml`, un seul speaker → forme `device`/`backend`/`server`) :
```yaml
qobuz: { max_quality: auto }        # auto | 5=MP3 | 6=CD | 7=HiRes96 | 27=HiRes192
device: { name: "Milō" }            # nom affiché dans l'app Qobuz
backend:
  type: "local"
  local: { device: "milo_qobuz", buffer_size: 2048 }
server: { http_port: 8689, bind_address: "0.0.0.0" }   # 0.0.0.0 requis : le téléphone doit joindre :8689 via mDNS
logging: { level: "info" }
```
**Auth** : one-time (cf. D4). Flux OAuth exposé par qobuz-proxy :
- `GET /auth/login?origin=<url>` → redirige vers la page de connexion Qobuz (l'`origin` détermine où Qobuz renvoie ; **doit pointer vers qobuz-proxy** car c'est son `/auth/callback` qui échange le code).
- `GET /auth/callback` → échange le code, cache le token, démarre les speakers.
- `POST /api/auth/logout` → efface le token, arrête les speakers.
- État de connexion lisible dans `GET /api/status → auth` : `{ authenticated, user_id, email, name, avatar }`.
Le token est caché automatiquement (persiste sous D3 — confirmer le chemin dans `auth/tokens.py` et le forcer sous D3). Alternative dev : coller `qobuz.user_id` + `qobuz.auth_token` dans `config.yaml`.
**Les speakers (et donc la découverte mDNS + l'endpoint `/connect-to-qconnect`) ne démarrent qu'APRÈS authentification** → tant que non connecté, « Milō » n'apparaît pas dans l'app Qobuz.

**Backend `local`** : nécessite `pip install 'qobuz-proxy[local]'` (tire `sounddevice`) + `libportaudio2` (apt). `resolve_device()` (`backends/local/device.py`) matche `audio_device` contre `sounddevice.query_devices()` par index / nom exact / substring → **d'où la nécessité du `hint { show on }` sur le PCM `milo_qobuz`** (D1).

**Entrée CLI** : `python -m qobuz_proxy` (ou script console `qobuz-proxy`) ; option `--config` (confirmer via `qobuz_proxy/cli.py`).

---

## Phase 0 — Validation matériel (gating, sur le Pi)

- [ ] **0.1 — PoC chemin audio + résolution PortAudio du PCM nommé.** Réf : bloc « Surface » (D1), [rootfs/etc/asound.conf](rootfs/etc/asound.conf) (`pcm.milo_spotify*`, `camilladsp`). Action : (1) `pip install 'qobuz-proxy[local]'` dans un venv jetable + `libportaudio2` ; (2) ajouter provisoirement un PCM `milo_qobuz` → `camilladsp` **avec `hint { show on; description "milo_qobuz" }`** ; (3) confirmer que `python -c "import sounddevice; print(sounddevice.query_devices())"` **liste `milo_qobuz`** (sinon appliquer un fallback D1 b/c et le noter) ; (4) lancer qobuz-proxy (`backend.local.device: milo_qobuz`), sélectionner « Milō » dans l'app Qobuz iOS, jouer un titre. Vérif : PCM énuméré par PortAudio + **son audible via CamillaDSP + volume Milō effectif**. **Si D1 doit changer, corrige le Journal.** Commit : *(aucun — PoC jetable)*.
- [x] **0.2 — Cartographier l'API qobuz-proxy + trancher la famille.** → **FAIT.** Résultat figé dans le bloc « Surface » ci-dessus. **D2 = Famille B.** (Pas de commit — consigné directement dans ce plan.)

## Phase 1 — Infra système & audio

- [ ] **1.1 — Bump snd-aloop `pcm_substreams` → slot 8.** Réf : commentaire de layout en tête de [rootfs/etc/asound.conf](rootfs/etc/asound.conf) (« pcm_substreams bump -> slot 8 »). Action : localiser la config du module `snd-aloop` (`/etc/modprobe.d/`) et porter `pcm_substreams` à ≥ 9. Vérif : après reboot, le substream d'index 8 existe (`ls /proc/asound/Loopback/pcm0p/` montre `sub8`). Commit : `chore(audio): bump snd-aloop substreams for qobuz slot 8`.
- [ ] **1.2 — Devices ALSA Qobuz.** Réf : blocs `milo_spotify` de [rootfs/etc/asound.conf](rootfs/etc/asound.conf). Action : ajouter `pcm.milo_qobuz` (alias qui commute sur `MILO_MODE`, **+ `hint { show on; description "milo_qobuz" }`** — indispensable pour PortAudio, cf. D1), `pcm.milo_qobuz_direct` → `camilladsp`, `pcm.milo_qobuz_multiroom` → `hw:Loopback,0,8`. Vérif : `aplay -D milo_qobuz` sans erreur de device inconnu **ET** `milo_qobuz` visible dans `sounddevice.query_devices()`. Commit : `feat(audio): add milo_qobuz ALSA device (direct + multiroom slot 8)`.
- [ ] **1.3 — Unité systemd.** Réf : [system/milo-spotify.service](system/milo-spotify.service). Action : créer `system/milo-qobuz.service` en copie stricte (`BindsTo=milo-backend.service` ; `After=… sound.target milo-backend.service milo-camilladsp.service` ; `EnvironmentFile=/var/lib/milo/routing.env` ; `User=milo Group=audio` ; `Restart=always` ; `MemoryMax=…` ; **pas de `WantedBy` snapcast**). `ExecStart` = `/var/lib/milo/qobuz/venv/bin/qobuz-proxy --config /var/lib/milo/qobuz/config.yaml` (confirmer la CLI en 1.4). Ajouter `Environment=MILO_MODE` piloté par `routing.env` (le PCM `milo_qobuz` en dépend). Reproduire **à l'identique** le wiring de démarrage/enable de `milo-spotify` (le sidecar doit tourner en permanence pour être découvrable). Vérif : `systemd-analyze verify system/milo-qobuz.service`. Commit : `feat(system): add milo-qobuz.service sidecar unit`.
- [ ] **1.4 — Déploiement qobuz-proxy @ D3.** Réf : bloc « Surface » (Config, Auth). Action : (1) `apt: libportaudio2` ; (2) venv `/var/lib/milo/qobuz/venv` + `pip install 'qobuz-proxy[local]'` ; (3) écrire `/var/lib/milo/qobuz/config.yaml` (speaker unique : `device.name: "Milō"`, `backend.type: local`, `backend.local.device: "milo_qobuz"`, `server.http_port: 8689`, `bind_address: "0.0.0.0"`, `max_quality: auto`) ; (4) **auth one-time** — pour ce déploiement initial, connecter le compte via le navigateur (`http://milo.local:8689`, « Log in to Qobuz ») OU token collé ; **confirmer où le token est caché** (`qobuz_proxy/auth/tokens.py`) et **le forcer sous D3** (chemin persistant, writable par l'user `milo`). NB : l'écran de login utilisateur final vit dans les réglages Milō (D4, étapes 2.4 + 3.4) ; ici on valide juste que l'auth fonctionne ; (5) `systemctl daemon-reload` + démarrer via le wiring de 1.3. **Confirmer l'entrée CLI réelle** (`qobuz-proxy --help` / `cli.py`) et corriger l'`ExecStart` de 1.3 si besoin. Vérif : `systemctl start milo-qobuz` OK ; « Milō » visible dans l'app Qobuz ; `curl -s localhost:8689/api/status` renvoie le speaker. Commit : `feat(qobuz): deploy qobuz-proxy sidecar + document install`.

## Phase 2 — Backend

- [ ] **2.1 — Enum.** Réf : [backend/core/models/audio_state.py](backend/core/models/audio_state.py). Action : ajouter `QOBUZ = "qobuz"` dans `AudioSource`. Vérif : `cd backend && python -c "from core.models.audio_state import AudioSource; AudioSource.QOBUZ"`. Commit : `feat(state): add QOBUZ audio source enum`.
- [ ] **2.2 — Source module (Famille B).** Réf : **[backend/sources/airplay/](backend/sources/airplay/)** (récepteur passif : détection d'un sender externe + `metadata_reader`) et [backend/sources/bluetooth/](backend/sources/bluetooth/) (`monitor.py`), [backend/shared/mpv.py](backend/shared/mpv.py) pour le style. Action : créer `backend/sources/qobuz/source.py` → `QobuzSource(BaseAudioSource)` (`source_id="qobuz"`, `service_name="milo-qobuz.service"`, `__all__=["QobuzSource"]`), + `backend/sources/qobuz/monitor.py`. Implémenter :
  - `_do_start/_do_stop/_get_status` (pas de `_handle_command` de lecture — **Famille B**).
  - **Monitor** : boucle async (via `BackgroundTaskSet` ou `self._monitor_task = asyncio.create_task(...)`) qui `GET http://127.0.0.1:8689/api/status` **~1 s** (client HTTP async des sources existantes), filtre le speaker `id == "milo"`, et :
    - `status ∈ {playing, paused}` + `now_playing` → **demande l'activation de la source** (modèle AirPlay) + `await state_machine.update_source_state(AudioSource.QOBUZ, SourceState.ACTIVE, metadata)`.
    - `status ∈ {idle, disconnected}` → libère / inactive.
  - **Mapping metadata** (clés lues par [AudioPlayerFull.vue](frontend/src/components/audio/AudioPlayerFull.vue)) : `title`, `artist`, `album_art_url` (← `now_playing.album_art_url`), `is_playing` (`status=="playing"`), `is_buffering=False`, et **`client_name`** = libellé statique `"Qobuz"` (pour que la source-bar s'affiche ; sinon la barre reste masquée et on n'a que pochette+titre+artiste — au choix).
  - Loggers : routes `getLogger(__name__)` (n/a ici), sous-modules `getLogger("source.qobuz.monitor")`. Doctrine boucle de fond : `try/except Exception` autour du **corps** de boucle + log + `continue` ; **fail open** si le proxy est injoignable (warning, on continue). Pas de `GET /qobuz/status` ni `POST /qobuz/restart`. **Pas de `routes.py`** (rien de Qobuz-spécifique à exposer, pas d'artwork binaire).

  Vérif : `ruff check backend/` + `cd backend && python -c "import sources.qobuz.source"`. Commit : `feat(qobuz): add QobuzSource (family B, /api/status monitor)`.
- [ ] **2.3 — Enregistrement DI.** Réf : [backend/dependencies.py](backend/dependencies.py) (creator `spotify_source` ; `register_source`). Action : ajouter le creator `"qobuz_source"` dans `_create_service()` (constructeur `(config, state_machine, settings_service, systemd_manager)`) + `state_machine.register_source(AudioSource.QOBUZ, get_service("qobuz_source"))` dans `initialize_services()`. Enregistrer l'`initialize()` async si `QobuzSource` en a un. Vérif : `cd backend && python -m pytest` (**dont `test_milo_mac_contract.py` — doit rester vert, aucun changement de manifeste**). Commit : `feat(qobuz): register QobuzSource in DI + state machine`.
- [ ] **2.4 — Relais backend « Compte Qobuz » (pour D4).** Réf : bloc « Surface » (Auth) ; conventions [backend/api/route_helpers.py](backend/api/route_helpers.py) + une route existante simple. Action : ajouter une petite surface REST Milō (ex. `backend/api/qobuz_account.py` sous `/api/qobuz/account`) qui relaie qobuz-proxy (`http://127.0.0.1:8689`) sans exposer `:8689` au frontend (cross-origin/CORS) :
  - `GET /api/qobuz/account` → lit `GET /api/status → auth`, renvoie `{ authenticated, name, email, avatar }`.
  - `GET /api/qobuz/account/login-url` → renvoie l'URL OAuth à ouvrir (`http://<host-lan>:8689/auth/login?origin=http://<host-lan>:8689`) — le callback atterrit sur qobuz-proxy (stocke le token + démarre le speaker), le frontend rafraîchit ensuite le statut.
  - `POST /api/qobuz/account/logout` → relaie `POST /api/auth/logout`.
  Doctrine erreurs HTTP (`api_error_handler`), **fail-open** si `:8689` injoignable. **Aucun** couplage Milo-Mac. Vérif : `ruff check backend/` + `cd backend && python -m pytest`. Commit : `feat(qobuz): backend relay for Qobuz account login/status`.

## Phase 3 — Frontend

- [ ] **3.1 — Store + i18n + icône.** Réf : entrée **AirPlay** dans [frontend/src/stores/unifiedAudioStore.js](frontend/src/stores/unifiedAudioStore.js), `frontend/src/locales/english.json` (canonique), [frontend/src/components/ui/AppIcon.vue](frontend/src/components/ui/AppIcon.vue). Action : (1) enregistrer la source `qobuz` dans le store (mirror AirPlay) ; (2) clés i18n (label « Qobuz », statuts) — **anglais d'abord** ; (3) ajouter l'**icône `qobuz`** dans `AppIcon` (asset SVG) — requise pour la source-bar et le dock. Vérif : `cd frontend && npm run lint`. Commit : `feat(frontend): register qobuz source in store + i18n + icon`.
- [ ] **3.2 — Composant.** Réf : composant **AirPlay** (Famille B) + [AudioPlayerFull.vue](frontend/src/components/audio/AudioPlayerFull.vue). Action : créer `frontend/src/components/qobuz/QobuzPlayer.vue` wrappant `<AudioPlayerFull source="qobuz" :showControls="false" />`. Vérif : `cd frontend && npm run lint && npm run build`. Commit : `feat(frontend): add qobuz player component`.
- [ ] **3.3 — Dock / liste des sources.** Réf : là où AirPlay est déclaré comme source sélectionnable (dock). Action : ajouter Qobuz (mirror AirPlay). Vérif : Qobuz apparaît dans le dock en `npm run dev`. Commit : `feat(frontend): add qobuz to source dock`.
- [ ] **3.4 — Écran « Compte Qobuz » dans les réglages (D4).** Réf : une section de réglages existante (structure + `apiCall`), étape 2.4. Action : ajouter dans les réglages Milō une section « Compte Qobuz » : état lu via `apiCall.get('/api/qobuz/account')` (affiche connecté + nom/email, ou déconnecté) ; bouton **Connecter** → `GET /api/qobuz/account/login-url` puis ouvre l'URL OAuth (nouvel onglet/popup — le flux se termine sur la page qobuz-proxy `:8689`, l'utilisateur revient dans Milō, le statut se rafraîchit par polling/refetch) ; bouton **Déconnecter** → `POST /api/qobuz/account/logout`. i18n anglais d'abord. Vérif : `cd frontend && npm run lint && npm run build`. Commit : `feat(frontend): add Qobuz account settings screen`.

## Phase 4 — Intégration & robustesse

- [ ] **4.1 — Vérif end-to-end (sur le Pi + téléphone).** Action : « Milō » visible dans l'app Qobuz ; lecture en **`direct`** ET **`multiroom`** (slot 8) ; pochette+titre+artiste corrects dans l'UI Milō ; volume Milō effectif ; pause/stop depuis le téléphone reflétés (paused / libération de source) ; switch Qobuz ↔ Spotify ↔ Radio (Loopback libéré proprement, pas d'état fantôme) ; **connexion/déconnexion du compte depuis l'écran réglages Milō (D4)** — après *Déconnecter*, « Milō » disparaît de l'app Qobuz ; après *Connecter*, il réapparaît ; `sudo journalctl -u milo-qobuz -f` pour les transitions. Ajuster `backend.local.buffer_size` si xruns. Vérif : tous les scénarios OK. Commit : `test(qobuz): validate end-to-end direct + multiroom`.
- [ ] **4.2 — Doc finale & nettoyage.** Action : [docs/architecture.md](docs/architecture.md) (inventaire persistance `/var/lib/milo/qobuz/` + systemd `milo-qobuz.service` + devices ALSA `milo_qobuz`) et [docs/development.md](docs/development.md) (source Qobuz = Famille B, install qobuz-proxy + auth one-time). **Supprimer ce fichier `PLAN_QOBUZ.md`** une fois tout coché. Vérif : `ruff check backend/ && cd frontend && npm run lint && npm run build`. Commit : `docs(qobuz): document qobuz source + remove completed plan`.

## Notes

- **Contrat Milo-Mac** : ajout additif → ne pas toucher le manifeste tant que Milo-Mac ne consomme pas Qobuz.
- Si une étape révèle qu'une décision D1/D2/D3 est fausse, **corriger le Journal** puis reprendre — pas de shim de compat (un seul chemin de code).
- **Modèle = AirPlay** partout (récepteur passif). En cas de doute d'implémentation, lire `sources/airplay/` (backend) et son composant (frontend) comme référence.
