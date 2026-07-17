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

- **D1 — Sortie audio qobuz-proxy → ALSA `milo_qobuz`** : **(a) backend `local` (PortAudio) ciblant le PCM nommé `milo_qobuz`. ✅ VALIDÉ EN 0.1 (audible + volume).** Corrections issues du PoC 0.1 sur le Pi :
  - **Le `hint { show on }` n'est PAS nécessaire à l'énumération.** Tous les PCM `milo_*` (type plug) apparaissent déjà dans `sounddevice.query_devices()` **et** dans `qobuz-proxy --list-audio-devices` **sans aucun bloc `hint`** (l'`asound.conf` n'en contient aucun). → retirer l'exigence du hint de 0.1/1.2.
  - **La capacité *output* d'un PCM PortAudio dépend de si la substream Loopback sous-jacente est LIBRE** : une substream occupée remonte `max_output_channels=0` et devient non-résoluble (« No output device matching »). `milo_qobuz → camilladsp` (Loopback subdevice 0, single-writer) n'est donc ouvrable par le backend `local` **que quand subdevice 0 est libre** — c.-à-d. en mode `direct` avec Qobuz comme source active. Confirmé : subdevice 0 libre → `milo_qobuz` = `max_out 128`, listé par le resolver natif du proxy `[29] milo_qobuz`. (Le state machine libère subdevice 0 quand Qobuz devient actif — c'est le comportement normal des sources ; à re-vérifier en intégration 4.1.)
  - **Test audible OK** : tone 440 Hz via `milo_qobuz → camilladsp → HifiBerry` audible ; A/B volume Milō (-30 → -50 dB) nettement entendu. Le volume CamillaDSP est le même chemin que Spotify — garanti.
  - Fallbacks conservés si un cas dégénère : (b) patch mince du backend local (pyalsaaudio) ; (c) pipe → mpv/aplay. Volume = CamillaDSP, volume interne du proxy ignoré.
- **D2 — Famille de source : B (récepteur passif, métadonnées seules, `showControls=false`, comme AirPlay).** Tranché depuis le code : qobuz-proxy n'expose **aucun** endpoint de contrôle (play/pause/next/seek), et position/durée ne sortent pas en HTTP. Métadonnées lues par **polling HTTP** de `GET /api/status`.
- **D3 — Emplacement qobuz-proxy : `/var/lib/milo/qobuz/`** (venv + `config.yaml` + cache token OAuth), parallèle à `go-librespot`. **Précisions PoC 0.1** : qobuz-proxy **n'est PAS sur PyPI** — paquet `qobuz-proxy` v1.5.0 uniquement sur GitHub ([github.com/leolobato/qobuz-proxy](https://github.com/leolobato/qobuz-proxy)) → installer depuis git (`pip install '.[local]'` après clone, ou `pip install 'git+https://github.com/leolobato/qobuz-proxy@<pin>#egg=qobuz-proxy[local]'`). Le paquet lit `$QOBUZPROXY_DATA_DIR` (défaut config `./config.yaml` ou `$QOBUZPROXY_DATA_DIR/config.yaml`) → pointer `QOBUZPROXY_DATA_DIR=/var/lib/milo/qobuz` dans l'unité systemd pour forcer config + cache token sous D3.
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
  "id": "mil",               // slugify(name): "Milō" → "mil" (non-ASCII ō dropped). id is hard-coupled to name in qobuz-proxy; display name "Milō" kept on purpose → match the speaker by audio_device, NOT this id. Confirmed live in 1.4.
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

**Entrée CLI** (✅ confirmée en 0.1, v1.5.0) : script console `qobuz-proxy` (entry point `qobuz_proxy.cli:main`). Flags utiles : `--config PATH`, `--backend-type local`, `--audio-device milo_qobuz`, `--http-port 8689`, `--bind 0.0.0.0`, `--list-audio-devices` (= ce que `resolve_device()` utilise), `--log-level`. → `ExecStart` de 1.3 = `.../qobuz-proxy --config /var/lib/milo/qobuz/config.yaml`.

---

## Phase 0 — Validation matériel (gating, sur le Pi)

- [x] **0.1 — PoC chemin audio + résolution PortAudio du PCM nommé.** → **FAIT (2026-07-17, sur le Pi).** Validé : (1) `qobuz-proxy[local]` v1.5.0 installé depuis git (pas PyPI) dans un venv jetable + `libportaudio2` (apt) ; (2) PCM provisoire `milo_qobuz` → `camilladsp` ; (3) **`milo_qobuz` énuméré par PortAudio ET par `qobuz-proxy --list-audio-devices` (`[29] milo_qobuz`, `max_out 128`) une fois subdevice 0 libre (mode `direct`)** ; (4) **son audible** : tone 440 Hz via `milo_qobuz → camilladsp → HifiBerry` entendu + **A/B volume Milō (-30/-50 dB) nettement audible** → **volume CamillaDSP effectif**. Journal D1/D3 + étapes 1.1/1.2 corrigés (voir ci-dessus : pas de hint requis, output-capability = substream libre, pas de PyPI, LoopbackDLNA au lieu de « slot 8 »). Le lancement du **vrai proxy avec compte Qobuz + lecture depuis le téléphone** est reporté à 1.4/4.1 (login one-time D4) ; le PoC a validé le risque technique D1 avec la lib PortAudio exacte utilisée par le backend `local`. Système restauré (multiroom + spotify), artefacts PoC supprimés. Commit : *(aucun — PoC jetable)*.
- [x] **0.2 — Cartographier l'API qobuz-proxy + trancher la famille.** → **FAIT.** Résultat figé dans le bloc « Surface » ci-dessus. **D2 = Famille B.** (Pas de commit — consigné directement dans ce plan.)

## Phase 1 — Infra système & audio

- [x] **1.1 — ⚠️ RÉVISÉ (PoC 0.1) : PAS de bump snd-aloop — utiliser une substream libre de `LoopbackDLNA`.** → **FAIT (2026-07-17, sur le Pi).** Constat sur le Pi : `snd-aloop` **hard-cap à 8 substreams/carte** ; `Loopback` (carte 1) est **pleine** (0=DSP, 1..7=sources). Une 2ᵉ carte **`LoopbackDLNA` existe déjà** (`/etc/modprobe.d/snd-aloop.conf` : `options snd-aloop index=1,2 enable=1,1 id=Loopback,LoopbackDLNA pcm_substreams=8,8`) — 8 substreams, DLNA n'occupe que `sub0` **quand il joue** (sinon tout libre). → Le bump « slot 8 » est **impossible et inutile**. **Action : rien à modifier côté module** (sauf si un jour on veut isoler Qobuz sur une 3ᵉ carte) ; Qobuz prend une substream libre de `LoopbackDLNA` en 1.2. Cette étape se réduit à **confirmer** que `LoopbackDLNA` a une substream libre pour Qobuz (`hw:LoopbackDLNA,0,1`). ~~Commit bump~~ → **aucun commit** (constat consigné ici). *(Si Phase 1.4 exige `libportaudio2` : déjà installé sur ce Pi en 0.1 — à ajouter dans `install.sh`/pi-gen.)* **Vérif live confirmée** : `/proc/asound/cards` liste bien `1 [Loopback]` + `2 [LoopbackDLNA]` (8 substreams chacune) ; les 8 substreams playback ET capture de `card2/pcm0p|pcm0c` sont tous `closed` (libres) ; `hw:LoopbackDLNA,0,1` présent (`aplay -l`) et **ouvrable en écriture** (`speaker-test -D hw:LoopbackDLNA,0,1` a ouvert le device et bloqué faute de lecteur capture = comportement loopback normal). → target Qobuz 1.2 = `hw:LoopbackDLNA,0,1` (capture snapserver `hw:2,1,1`) validé.
- [x] **1.2 — Devices ALSA Qobuz.** → **FAIT (2026-07-17, sur le Pi).** Ajouté à [rootfs/etc/asound.conf](rootfs/etc/asound.conf) : alias `pcm.milo_qobuz` (commute sur `MILO_MODE`), `pcm.milo_qobuz_direct` → `camilladsp`, `pcm.milo_qobuz_multiroom` → **`hw:LoopbackDLNA,0,1`** (subdevice 1 libre de la 2ᵉ carte ; capture snapserver = `hw:2,1,1`). Pas de `hint` (facultatif, cf. PoC 0.1). En-tête de layout mis à jour (DLNA=sub0, Qobuz=sub1). **Vérif live** : fichier déployé sur `/etc/asound.conf` (base identique au repo, ajout purement additif) ; `aplay -D milo_qobuz -f S16_LE -c2 -r48000 /dev/zero` **ouvre le device sans erreur de résolution dans les DEUX modes** — `MILO_MODE=direct` (→ `camilladsp`) et `MILO_MODE=multiroom` (→ `hw:LoopbackDLNA,0,1`). L'énumération `qobuz-proxy --list-audio-devices` (déjà validée en 0.1, `[29] milo_qobuz max_out 128` quand la substream cible est libre) sera re-confirmée au redéploiement du proxy en 1.4. Commit : `feat(audio): add milo_qobuz ALSA device (direct + multiroom on LoopbackDLNA slot 1)`.
- [x] **1.3 — Unité systemd.** → **FAIT (2026-07-17).** Créé [system/milo-qobuz.service](system/milo-qobuz.service) en copie stricte de milo-spotify : `BindsTo=milo-backend.service` ; `After=network-online.target sound.target milo-backend.service milo-camilladsp.service` + `Wants=network-online.target` ; `EnvironmentFile=/var/lib/milo/routing.env` (fournit `MILO_MODE` au PCM `milo_qobuz` — même wiring que spotify, pas de `Environment=MILO_MODE` séparé) ; `User=milo Group=audio` ; `Restart=always`/`RestartSec=5`/`TimeoutStopSec=5` ; `MemoryMax=256M` ; `[Install] WantedBy=multi-user.target` mais **backend-managed, pas enable au boot** (pas de `WantedBy` snapcast). Ajouté `Environment=QOBUZPROXY_DATA_DIR=/var/lib/milo/qobuz` pour forcer config + cache token sous D3. `ExecStart=/var/lib/milo/qobuz/venv/bin/qobuz-proxy --config /var/lib/milo/qobuz/config.yaml` (CLI confirmée en 0.1 ; venv déployé en 1.4). **Répercuté dans install.sh + pi-gen** : les deux copient déjà `system/*.service` via glob (auto-pris) → seul ajout = `milo-qobuz` dans les listes de commentaires « services managed dynamically by the backend » ([install/system.sh](install/system.sh), [pi-gen/stage-milo/03-configure/01-run.sh](pi-gen/stage-milo/03-configure/01-run.sh)). **Vérif** : `systemd-analyze verify` ne remonte que « qobuz-proxy is not executable » (venv absent → normal, installé en 1.4) ; syntaxe de l'unité valide. Commit : `feat(system): add milo-qobuz.service sidecar unit`.
- [x] **1.4 — Déploiement qobuz-proxy @ D3.** → **FAIT (2026-07-17, sur le Pi, login utilisateur présent).** (1) `libportaudio2` déjà présent ; (2) venv `/var/lib/milo/qobuz/venv` + `qobuz-proxy[local]` **v1.5.0** installé depuis git **tag épinglé** (`pip install "qobuz-proxy[local] @ git+https://github.com/leolobato/qobuz-proxy@v1.5.0"` — la forme `#egg=…[extra]` est rejetée par pip moderne → **PEP 508 direct-URL**) ; (3) `config.yaml` écrit en **forme flat single-speaker** (validée contre le parser v1.5.0 : `qobuz.max_quality: auto`, `device.name: "Milō"` (affichage ; id = slugify → `"mil"`, couplé, cf. 2.2 : matcher par audio_device), `backend.type: local` + `backend.local.device: "milo_qobuz"` + `buffer_size: 2048`, `server.http_port: 8689` + `bind_address: "0.0.0.0"`, `logging.level: info`) — parse OK, `milo_qobuz` énuméré output-capable `[12] 128ch` **même en `multiroom`** (sub1 LoopbackDLNA libre) ; (4) **token caché = `$QOBUZPROXY_DATA_DIR/credentials.json` = `/var/lib/milo/qobuz/credentials.json`** (confirmé dans `auth/credentials.py::CACHE_DIR`, **pas** `tokens.py` qui n'est que des dataclasses) — l'unité 1.3 fixe déjà `QOBUZPROXY_DATA_DIR=/var/lib/milo/qobuz` → token sous D3, `milo:audio`, writable ; **auth one-time faite via navigateur LAN `http://milo.local:8689`** → `authenticated: true` (user « Léo »), `credentials.json` écrit (`user_id`+`user_auth_token`+`email`) ; (5) unité déployée (`cp` → `/etc/systemd/system/`, `daemon-reload`), `systemctl start milo-qobuz` **actif**, `/api/status` renvoie le speaker (`backend: local`, `audio_device: milo_qobuz`, `status: idle`, `effective_quality: 27` HiRes192). **CLI confirmée** (`--config PATH` OK) → `ExecStart` de 1.3 inchangé. **Install reproductible ajouté** : [install/qobuz-proxy.sh](install/qobuz-proxy.sh) (apt + venv + pip tag épinglé + config.yaml, `QOBUZ_PROXY_VERSION=1.5.0`), câblé dans [install.sh](install.sh) (source + `install_qobuz_proxy`) et [pi-gen stage 02](pi-gen/stage-milo/02-install-milo/01-run.sh) (réutilise la fn, single source of truth) ; uninstall déjà couvert (`milo-*.service` glob + `rm -rf /var/lib/milo`). **Constats propagés au plan** : `device.name` = « Milō » (affichage conservé) → speaker `id == "mil"` (id couplé à slugify(name), non séparable ; matcher par audio_device, cf. 2.2) ; `credentials.json` (pas config.yaml) porte le token. Commit : `feat(qobuz): deploy qobuz-proxy sidecar + document install`.

## Phase 2 — Backend

- [x] **2.1 — Enum.** → **FAIT.** `QOBUZ = "qobuz"` ajouté à `AudioSource`. Commit `feat(state): add QOBUZ audio source enum`.
- [x] **2.2 — Source module (Famille B).** → **FAIT.** Créés `backend/sources/qobuz/{__init__,source,monitor}.py`. `QobuzSource(BaseAudioSource)` (`source_id="qobuz"`, `service_name="milo-qobuz.service"`, `__all__=["QobuzSource"]`, `COMMANDS={}`) : `_do_start` démarre le service + `QobuzMonitor`, `_cleanup` arrête le monitor (le `_do_stop` par défaut de la base enchaîne `_cleanup()` + `_stop_service()`, pas d'override). `QobuzMonitor` = boucle async (`asyncio.create_task`, session aiohttp, timeout 3 s) qui `GET /api/status` ~1 s, matche le speaker par `config.audio_device == "milo_qobuz"` (fallback `speakers[0]`) — **pas par `id`** (`slugify("Milō")="mil"`, id couplé, confirmé par le revert `9fbc54b1` qui a remis `device.name="Milō"`), et passe le speaker (ou `None`) au callback `_on_status`. Mapping : `status ∈ {playing,paused}` + `now_playing` → `emit_connection_state(connected=True, PlaybackMetadata(title/artist/album/album_art_url/is_playing/is_buffering), extras={client_name:"Qobuz"})` = ACTIVE (modèle AirPlay/DLNA, `emit_connection_state`→`update_source_state`) ; `idle/disconnected/absent` → WAITING. **Constat archi** : le boot arrête les services sidecar lingering (`main.py` l.113-121) → milo-qobuz.service + monitor ne tournent qu'une fois Qobuz sélectionné (transition), comme Spotify ; pas de `transition_to_source` explicite requis. Pas de position/durée (HTTP ne les expose pas), pas d'auto-stop (le proxy signale `idle` directement), pas de `routes.py`, pas d'artwork binaire (URL CDN Qobuz). Loggers `source.qobuz.*`, boucle fail-open (warning + continue). `ruff` clean, import OK. Commit `feat(qobuz): add QobuzSource (family B, /api/status monitor)`.
- [x] **2.3 — Enregistrement DI.** → **FAIT.** Creator `"qobuz_source"` ajouté à `_create_service()` (constructeur `(state_machine, settings_service, systemd_manager)`, pas de `config` — défauts internes) + `state_machine.register_source(AudioSource.QOBUZ, get_service("qobuz_source"))` dans `initialize_services()` (STEP 3). **Pas** d'entrée `init_async` : `QobuzSource` n'override pas `initialize()` (défaut base) et n'a rien à charger au boot — comme AirPlay/DLNA/BT. `pytest backend/` = **1808 passed** (dont `test_milo_mac_contract.py` vert, manifeste inchangé). Commit `feat(qobuz): register QobuzSource in DI + state machine`.
- [x] **2.4 — Relais backend « Compte Qobuz » (pour D4).** → **FAIT.** Créé [backend/api/qobuz_account.py](backend/api/qobuz_account.py) → `create_qobuz_account_router()` (prefix `/api/qobuz/account`, sans deps), câblé dans `main.py` (import + `app.include_router(create_qobuz_account_router())` après discovery). Routes : `GET /api/qobuz/account` (lit `/api/status → auth`, renvoie `{authenticated,name,email,avatar}`, **fail-open** → `authenticated:false` en HTTP 200 si proxy injoignable) ; `GET /api/qobuz/account/login-url` (construit l'URL OAuth depuis le host de la requête : `http://<host>:8689/auth/login?origin=…` pour que le callback reste sur le proxy) ; `POST /api/qobuz/account/logout` (relaie `POST /api/auth/logout`, fail-open si injoignable, 502 si le proxy répond non-2xx). Format `{status:"success", data:{…}}` (conv. discovery). Aucun couplage Milo-Mac. `ruff` clean, `pytest backend/` = **1808 passed**. Commit `feat(qobuz): backend relay for Qobuz account login/status`.

### Constats archi (Phase 2 — utiles pour Phases 3 & 4)

Relevés en implémentant le backend ; à garder en tête pour le frontend + l'intégration :

- **Cycle de vie du sidecar = activation à la demande (comme Spotify), PAS toujours-on.** Au boot, `main.py` (~l.113-121) arrête tout service source « lingering » (le state machine démarre à `source=NONE`). Donc **`milo-qobuz.service` + le monitor ne tournent qu'une fois Qobuz sélectionné** dans le dock Milō (`api/audio.py` → `transition_to_source(AudioSource.QOBUZ)` → `QobuzSource.start()` → `_do_start` lance le proxy + le poll). **Conséquence UX (à valider en 4.1)** : « Milō » n'apparaît dans l'app Qobuz **qu'après** avoir choisi Qobuz dans le dock (le proxy ne s'annonce en mDNS qu'une fois lancé) — exactement comme Spotify Connect ici. Le compte reste connecté (token caché sous D3) même service arrêté ; c'est l'annonce mDNS qui va/vient avec le service.
- **Pas de `transition_to_source` dans la source.** Le passage ACTIVE se fait *dans* la source déjà active : `_on_status` → `emit_connection_state()` → `set_state()` → `state_machine.update_source_state()`, qui **est un no-op si Qobuz n'est pas la source active** (`update_source_state` filtre sur `active_source`). Le dock/API pilote la sélection ; le monitor ne fait que remonter playing/paused/idle une fois sélectionné.
- **Forme metadata émise sur le WS** (event `source/state_changed` → `system_state.metadata`, lu par `AudioPlayerFull.vue` en 3.2) : `title, artist, album, album_art_url, is_playing, is_buffering` (canonique `PlaybackMetadata`, champs `None` filtrés) **+ `client_name:"Qobuz"`** (extra). **Ni `position` ni `duration`** (le proxy ne les expose pas en HTTP) → la barre de progression reste inerte : normal pour Famille B, ne pas la câbler. `album_art_url` = URL CDN Qobuz chargée directement par le kiosk (pas de route binaire type `/api/airplay/artwork`).
- **Réglages compte (3.4) — contrat des 3 routes 2.4**, format `{status:"success", data:{…}}` (conv. discovery, `apiCall.get` renvoie `data`) :
  - `GET /api/qobuz/account` → `data:{authenticated, name, email, avatar}` (fail-open : `authenticated:false` en 200 si proxy down).
  - `GET /api/qobuz/account/login-url` → `data:{login_url}` : ouvrir tel quel (nouvel onglet/popup) ; le flux se termine sur `:8689/auth/callback`, puis refetch le statut au retour.
  - `POST /api/qobuz/account/logout` → `{status:"success"}` (fail-open ; 502 si le proxy répond non-2xx).
- **Enum côté frontend** : la valeur de source sur le wire est la string `"qobuz"` (`AudioSource.QOBUZ.value`) — c'est la clé à mirrorer sur AirPlay dans `unifiedAudioStore.js` (3.1) et à passer en `source="qobuz"` au composant (3.2).

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
