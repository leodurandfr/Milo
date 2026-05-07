# Code Review Plan — 2026-05-07

Review approfondie des 23 commits du 2026-05-07 (~2900 lignes, 40 fichiers).
Sortie cible : code propre, optim, sans surcomplexification ni dette ; pas de compatibilité (app non publiée).

## Mode d'emploi

Chaque phase se déroule en **2 passes** : audit (read-only) puis fix (après validation).

### Passe 1 — Audit

Pour démarrer dans une nouvelle conversation :

> Faire la passe Audit de la phase non auditée du plan de code-review (`plan-code-review-2026-05-07.md`).

L'instance qui exécute :
1. Lit la phase la plus haute dont la case **Audit** est non cochée, et **uniquement celle-ci**.
2. Lit les fichiers + commits indiqués (intégralement, pas en survol).
3. Mène la review en suivant la **checklist de concerns** de la phase.
4. **N'écrit aucune modification de code.**
5. Remplit la section `### Findings` de la phase dans ce fichier, avec pour chaque finding :
   - ID (`A-01`, `A-02`, …)
   - Sévérité : `P0 bug` / `P1 design` / `P2 lisibilité-optim`
   - Localisation `file:line` (et `file2:line` si lien cross-fichier)
   - Description courte du problème (3-5 lignes)
   - Fix proposé concret (pas "améliorer X" mais "remplacer Y par Z")
   - Confiance : `haute` / `moyenne` / `basse`
   - Case `- [ ]` pour cocher au moment du fix
6. Coche la case **Audit** de la phase et présente le résumé.

### Validation utilisateur

Entre les deux passes, l'utilisateur :
- Lit la section Findings remplie
- Coche les findings à appliquer (ou les laisse cochés s'il accepte tout)
- Décoche/supprime ceux qu'il rejette
- Peut commenter (ajouter une ligne `note:` sous un finding)

### Passe 2 — Fix

> Faire la passe Fix de la phase non corrigée du plan de code-review (`plan-code-review-2026-05-07.md`).

L'instance qui exécute :
1. Lit la phase dont la case **Audit** est cochée mais **Fix** ne l'est pas.
2. Applique **uniquement les findings cochés** dans la section Findings.
3. Pour chaque finding appliqué : coche sa case et ajoute le hash du commit (ou "uncommitted" si tu commits toi-même).
4. Présente `git diff` final + résumé findings appliqués / écartés / reportés.
5. Coche la case **Fix** de la phase.

### Décisions structurelles verrouillées

**Ordre imposé** : A → B → C. Bloc A est fondationnel (sémantique `setup_completed`, hostname, mDNS) et tout le bloc B s'appuie dessus ; corriger A en premier évite de revenir sur B.

**Bloc B non scindé backend/frontend** : la plupart des bugs probables sont des mismatchs de contrat API → les voir ensemble. 1200 LOC restent gérables en une phase.

**Out of scope** explicite pour ces reviews :
- Tests automatisés (pas de coverage requis)
- Refonte i18n globale (revue locale par phase)
- Performance pure (sauf si symptôme évident)
- Documentation utilisateur

### Exception "fix direct sans validation"

Pendant la passe Audit, l'instance peut corriger **uniquement** ces cas en passant, et les noter dans le résumé d'audit :
- Faute de frappe dans une chaîne i18n ou un message utilisateur
- Import manifestement mort (vérifié avec grep, aucun usage)

Tout le reste passe par la validation.

---

## Phase A — Refactor first-boot & sémantique `setup_completed`

- [x] **Audit** — fait le 2026-05-07, 6 findings (P0: 0 / P1: 3 / P2: 3)
- [x] **Fix** — fait le 2026-05-07, 6 appliqués / 0 écartés / 0 reportés

### Commits couverts
- `884ceeab` refactor(first-boot): drop milo-setup intermediate hostname
- `5ea9b438` refactor(first-boot): make role detection idempotent across boots
- `83e37339` feat(first-boot): add configure_server to revert milo-client to server
- `88f09782` feat(milo-client): mark setup_completed before apply-hardware reboot

### Fichiers (lire intégralement)

**Système / shell**
- `rootfs/usr/local/bin/milo-first-boot` (+254 / -46) — refonte majeure, role detection idempotente, ajout `configure_server`, lecture marker wifi-adoption (anticipé Phase B mais cohabite ici)
- `rootfs/usr/local/bin/milo-mdns-probe` (+19 / -7)
- `rootfs/etc/NetworkManager/dispatcher.d/90-milo-network` (+10 / -3)
- `system/milo-first-boot.service` (+2 / -2)
- `pi-gen/config` (+1 / -1) — vérifier hostname/user cohérence
- `pi-gen/README.md` (+1 / -1)

**Backend client**
- `milo-client/app/routes/hardware.py` (+25 / -0) — écrit `setup_completed=true` avant reboot apply-hardware

### Checklist de concerns (review approfondie)

**Idempotence & robustesse** (priorité)
- `milo-first-boot` : si interrompu (power loss) en plein milieu d'un `configure_client`, est-ce que le boot suivant repart proprement ?
- Si `setup_completed=true` mais hostname incohérent (ex: `milo` mais `/var/lib/milo` chowné `milo-client`) → quel est le comportement ? Y a-t-il un état intermédiaire impossible à atteindre ?
- Probe mDNS via ethernet uniquement : que se passe-t-il si l'ethernet n'est pas branché au premier boot, puis l'est au deuxième ? L'auto-revert client→server documenté dans plan.md fonctionne-t-il ?
- Dispatcher NetworkManager `90-milo-network` : déclenche quoi, dans quel ordre, est-ce ré-entrant ?

**Sémantique `setup_completed`**
- Vérifier que `setup_completed` n'est plus écrit prématurément côté serveur (avant fin du wizard).
- Le timing dans `milo-client/app/routes/hardware.py` : `setup_completed=true` AVANT `apply-hardware` est-il exact ? Si reboot foire, on a un client qui se croit configuré sans carte audio appliquée ?
- Le revert client→server supprime bien `/var/lib/milo/settings.json` et chowne correctement ?

**Sécurité / safety shell**
- `set -euo pipefail` partout ?
- Échappement des variables shell (notamment hostnames + paths `/var/lib/milo`) ?
- Aucune injection possible via valeurs lues depuis settings.json ou hostnames mDNS ?
- Logs : ne fuit pas de credentials wifi (anticipé Phase B mais le plumbing est ici) ?

**Conventions projet (CLAUDE.md)**
- Comments en anglais
- Pas de migration code / fallback legacy
- Pas de sudo dans le code (rappel: ces scripts shell sont une exception légitime puisqu'ils tournent root via systemd ; vérifier toutefois les permissions service)

**Cohérence pi-gen**
- L'image flashée part avec hostname `milo`, sans hostname intermédiaire `milo-setup` (cf. commit `884ceeab`) → vérifier que `pi-gen/config` reflète bien et qu'aucun stage de pi-gen ne réintroduit `milo-setup`.

### Files-to-read auxiliaires (contexte)

- `backend/api/setup.py` (lecture seule pour Phase A : voir s'il y a usage de `setup_completed` qui a évolué — ne pas modifier ici)
- `backend/core/settings.py` (lecture seule : flux d'écriture `setup_completed`)

### Findings (à remplir lors de l'audit)

#### A-01 — Fenêtre de revert pour milo-client en mid-config (chemin ethernet)
- **Sévérité** : P1 design
- **Localisation** : `rootfs/usr/local/bin/milo-first-boot:393-396` + `rootfs/usr/local/bin/milo-first-boot:189` (configure_client n'écrit pas `setup_completed`)
- **Problème** : Pour un milo-client adopté via ethernet (`configure_client`, sans `_with_hardware`), `_apply_client_filesystem` ne pose volontairement pas `setup_completed=true`. Tant que l'utilisateur n'a pas atteint `POST /api/hardware/reboot` côté milo-client (qui peut se passer des heures plus tard), `settings.json` ne contient pas le flag — donc à chaque boot la boucle principale tourne. Si pendant cette fenêtre la LAN coupe, ou que le serveur est redémarré, ou qu'un switch perd l'IGMP plus longtemps que les 3×10 s du retry, le client se voit à `hostname=milo-client + milo.local introuvable` et **lance `configure_server`** : il se transforme en serveur tout neuf. Le revert détruit `/var/lib/milo/settings.json` et chowne en `milo:milo`, avec rollback complet des services. C'est exactement le scénario que `setup_completed=true` est censé empêcher pour le chemin wifi-adoption (cf. commit `88f09782`), mais la version ethernet n'a pas ce verrou.
- **Fix proposé** : Dans `_apply_client_filesystem` (avant le `chown -R "$MILO_CLIENT_USER:audio" "$MILO_DATA_DIR"`), écrire `setup_completed=true` dans `$MILO_DATA_DIR/settings.json` via une heredoc python similaire à celle de `configure_client_with_hardware`. Le client portera ainsi le verrou dès le premier boot post-adoption ethernet, comme le fait déjà le chemin wifi. Mettre à jour le commentaire de l'étape 10 en conséquence ("written here so milo-first-boot skips re-detection on subsequent boots, even if the user hasn't yet picked an audio card").
- **Confiance** : haute
- [x] À appliquer — 5ec83167

#### A-02 — `configure_server` change le hostname en premier : revert partiel possible sur power loss
- **Sévérité** : P1 design
- **Localisation** : `rootfs/usr/local/bin/milo-first-boot:282-336`
- **Problème** : `configure_server` fait l'ordre `hostname → ALSA → modprobe → disable client services → enable server services → wipe settings.json`. Si le power coupe entre l'étape 1 (hostname=milo) et l'étape 5 (services serveur réactivés), le device boote avec `hostname=milo` mais avec les services client toujours enabled et les services serveur toujours disabled. Au boot suivant, milo-first-boot rentre dans la branche `hostname=milo + milo.local introuvable → no-op` (ligne 398) et **ne rejoue pas `configure_server`** car la condition de déclenchement (`hostname=milo-client`) n'est plus vraie. L'utilisateur se retrouve avec un device "milo" inerte (aucun backend serveur ne tourne, services client tournent en l'air sans `/var/lib/milo-client/hardware.json` correctement initialisé). Même problème théorique côté `_apply_client_filesystem`, mais là la condition de re-trigger (`hostname=milo + milo.local trouvé`) reste valide et l'idempotence joue.
- **Fix proposé** : Réordonner `configure_server` pour que le hostname change passe en **dernier**, juste avant le `reboot`. Ordre proposé : 1. ALSA → 2. modprobe → 3. disable client → 4. enable server → 5. reset data → 6. hostname (+`/etc/hostname`+`/etc/hosts`) → reboot. Tant que l'étape 6 n'a pas eu lieu, le re-run de milo-first-boot conserve la condition `hostname=milo-client + no milo.local` et redéclenche `configure_server` pour finir le travail.
- **Confiance** : haute
- [x] À appliquer — 5ec83167

#### A-03 — `setup_completed=true` posé avant le succès du subprocess apply-hardware
- **Sévérité** : P1 design
- **Localisation** : `milo-client/app/routes/hardware.py:135-160`
- **Problème** : Dans `POST /api/hardware/reboot`, `_set_setup_completed_in_milo_settings()` est appelé **avant** `asyncio.create_subprocess_exec("sudo", APPLY_HARDWARE_SCRIPT, ...)`. Si le subprocess échoue immédiatement (script absent, sudoers cassé, returncode > 0 dans les 2s), la route lève `HTTPException 500`, mais `setup_completed=true` est déjà persisté. Au boot suivant, milo-first-boot voit `setup_completed=true` → exit early → milo-client.service démarre **sans** que `config.txt` ait été modifié (carte audio non chargée). Le device est verrouillé en mode client mais sans audio, et la seule sortie est que l'utilisateur retape `/api/hardware/reboot` (qui réussira probablement la 2e fois). Le commentaire ligne 135-136 documente l'intention ("lock the role"), mais l'erreur de séquencement rend l'état "verrouillé sans hardware appliqué" possible.
- **Fix proposé** : Inverser l'ordre : spawn le subprocess en premier, attendre 2s, **uniquement si le process est encore vivant** (`asyncio.TimeoutError` = il continue donc va probablement reboot) appeler `_set_setup_completed_in_milo_settings()`. Si le process est sorti avec `returncode > 0` dans les 2s, raise `HTTPException` sans toucher à settings.json. Code minimal :
  ```python
  proc = await asyncio.create_subprocess_exec(...)
  try:
      await asyncio.wait_for(proc.wait(), timeout=2)
      if proc.returncode is not None and proc.returncode > 0:
          stderr = await proc.stderr.read()
          raise HTTPException(500, f"Reboot script failed: {stderr.decode().strip()}")
  except asyncio.TimeoutError:
      # Still running → reboot in progress, safe to lock the role now
      try:
          _set_setup_completed_in_milo_settings()
      except Exception as e:
          logger.error(f"Failed to mark setup_completed: {e}")
  ```
- **Confiance** : haute
- [x] À appliquer — 5ec83167

#### A-04 — `configure_server` ne nettoie pas `pending_client_role.json`
- **Sévérité** : P2 lisibilité-optim
- **Localisation** : `rootfs/usr/local/bin/milo-first-boot:279-341`
- **Problème** : Si `configure_client_with_hardware` est interrompu après le `os.remove(marker_path)` mais avant le `/sbin/reboot` (extrêmement court mais possible), ou plus simplement si une mauvaise séquence de timing fait que le marker existe encore quand `configure_server` est appelé, la révocation laisse le fichier orphelin sur disque. Il restera collé jusqu'à ce que quelqu'un finisse le wizard côté serveur et que `setup_completed=true` repasse, à ce moment-là milo-first-boot ouvrira la branche "marker found → applying wifi adoption" et **rappliquera la config client adoptée** alors que le device a été reverti volontairement en serveur. Bug latent silencieux.
- **Fix proposé** : Ajouter dans `configure_server`, après le reset des données serveur (entre lignes 336 et 339), `rm -f "$PENDING_CLIENT_ROLE_FILE"` avec un log "stale wifi-adoption marker removed" si le fichier existait.
- **Confiance** : moyenne
- [x] À appliquer — 5ec83167

#### A-05 — `configure_server` ne supprime pas les autres fichiers d'état (radio, podcast, hardware client)
- **Sévérité** : P2 lisibilité-optim
- **Localisation** : `rootfs/usr/local/bin/milo-first-boot:332-336`
- **Problème** : Le revert ne touche que `settings.json`. Les fichiers `radio_data.json`, `podcast_data.json`, `last_volume.json` et `hardware.json` côté serveur ne sont pas effacés. En pratique, sur un revert depuis milo-client, ces fichiers n'existent généralement pas (ils sont créés par milo-backend qui était disabled), donc l'effet est nul ; mais si un revert survient après que l'utilisateur ait lancé manuellement milo-backend ne serait-ce qu'une fois (dev/debug), la nouvelle vie en serveur démarre avec d'anciennes radios/podcasts. Le commentaire ligne 332-334 ("backend will recreate settings.json with defaults") induit en erreur en suggérant un wipe complet alors que c'est juste settings.json. Question d'hygiène et d'attentes.
- **Fix proposé** : Soit (a) reformuler le commentaire en "Reset only settings.json — other data files (radio, podcast, hardware) are inert on a fresh server and will be overwritten when needed" pour clarifier l'intention, soit (b) wipe complet du contenu de `$MILO_DATA_DIR` sauf `backups/` puis chown. (a) est plus safe ; (b) est plus propre. Préférer (a) sauf si tu veux explicitement repartir from scratch.
- **Confiance** : moyenne
- [x] À appliquer — 5ec83167

#### A-06 — `become-client` laisse fuiter le profil wifi sauvé si `setup_completed` échoue
- **Sévérité** : P2 lisibilité-optim
- **Localisation** : `backend/api/setup.py:215-235`
- **Problème** : Dans `POST /api/setup/become-client`, l'ordre est : 1. write marker → 2. `wifi_service.save_network()` → 3. `set_setting("setup_completed", True)`. Si l'étape 3 échoue, le code ligne 230-234 supprime bien le marker mais **pas** le profil wifi. Le device reste donc serveur, mais avec une connexion NM persistante vers le SSID de l'utilisateur. Pas catastrophique (ne s'autoconnecte pas en l'absence de marker), mais c'est un leak silencieux qui pourrait surprendre lors d'un retry sur un autre SSID. Idem si l'étape 2 réussit puis que le marker write échoue (ordre étape 1 vs 2 actuel rend ce cas impossible, mais la robustesse symétrique mérite un cleanup).
- **Fix proposé** : Au moment du rollback (ligne 230-234), ajouter une suppression du profil wifi via `wifi_service.delete_network(payload.wifi_ssid)` (ou équivalent disponible) dans un `try/except` silencieux. Si la méthode n'existe pas dans `wifi_service`, l'ajouter en se basant sur `nmcli connection delete <ssid>`.
- **Confiance** : moyenne
- [x] À appliquer — 5ec83167

---

## Phase B — Feature adoption wifi des speakers

- [x] **Audit** — fait le 2026-05-07, 9 findings (P0: 0 / P1: 2 / P2: 7)
- [x] **Fix** — fait le 2026-05-07, 9 appliqués / 0 écartés / 0 reportés

### Commits couverts
- `3eb01ef5` feat(wifi): make setup hotspot SSID unique per device
- `aca7110c` feat(discovery): add wifi-speakers endpoint
- `db8371d6` feat(discovery): expose server wifi credentials
- `5967fb65` feat(setup): add become-client endpoint
- `b32f3121` feat(discovery): orchestrate wifi adoption
- `f87f7811` feat(first-boot): apply wifi-adoption marker
- `2e4e392c` feat(registration): forward identity to server
- `206f32f4` refactor(network): extract reusable NetworkSelector
- `ed432f47` feat(discovery): add Pinia store
- `37e06217` feat(discovery): unify pending ethernet + wifi hotspots
- `e7ab4a25` feat(discovery): extend ConfigureSystem with wifi adoption mode
- `6c6e982c` feat(i18n): add discovery + adopt strings
- `7bbfc7d1` refactor(wifi): revert hotspot SSID to plain `Milō` (revert partiel de `3eb01ef5`)

### Fichiers (lire intégralement)

**Backend orchestration**
- `backend/core/multiroom/wifi_adoption.py` (+281 / -0) — **fichier neuf, cœur de la feature**
- `backend/api/discovery.py` (+140 / -0) — **fichier neuf**, endpoints scan + adopt
- `backend/api/setup.py` (+146 / -0) — endpoint `become-client`
- `backend/core/wifi/service.py` (+85 / -5)
- `backend/api/multiroom.py` (+22 / -13)
- `backend/api/models.py` (+19 / -0)
- `backend/dependencies.py` (+10 / -5) — câblage du nouveau service
- `backend/main.py` (+4 / -0) — registration routes

**Backend client**
- `milo-client/app/services/registration.py` (+23 / -0) — forward identity au serveur
- `rootfs/usr/local/bin/milo-first-boot` — section marker wifi-adoption (cohabite avec Phase A)

**Frontend**
- `frontend/src/components/network/NetworkSelector.vue` (+286 / -0) — **fichier neuf, extrait de NetworkStep**
- `frontend/src/components/setup/NetworkStep.vue` (+30 / -217) — refactor consumer du NetworkSelector
- `frontend/src/components/settings/categories/multiroom/ConfigureSystem.vue` (+148 / -31)
- `frontend/src/components/settings/categories/multiroom/MultiroomSettings.vue` (+76 / -56)
- `frontend/src/components/settings/categories/multiroom/SystemListItem.vue` (+71 / -5)
- `frontend/src/stores/discoveryStore.js` (+120 / -0) — **fichier neuf**

**i18n** (vérifier 8 locales)
- `frontend/src/locales/{english,french,chinese,german,hindi,italian,portuguese,spanish}.json` (+24 chaque)

### Checklist de concerns (review approfondie)

**Orchestration & sécurité (criticité haute)**
- `wifi_adoption.py` : flux complet — switch wifi temporaire, push config, restore wifi original. Que se passe-t-il si :
  - L'utilisateur ferme le browser pendant le switch ? (le serveur reste-t-il bloqué sur le hotspot ?)
  - Le speaker ne répond pas après push ? Timeout configuré ? Cleanup ?
  - Le restore wifi échoue ? Le serveur perd-il sa connexion réseau ?
  - Plusieurs adoptions simultanées ? (lock global ? FIFO ?)
- Hotspot ouvert assumé MVP (cf plan.md) — vérifier qu'aucune autre porte n'est ouverte :
  - `become-client` n'accepte la config qu'une seule fois ? (anti-replay)
  - Validation des champs reçus côté speaker (pas d'injection dans wpa_supplicant/nmcli) ?
  - Logs ne fuient pas le password wifi ?
- `discovery.py` `expose-credentials` : qui peut appeler ? Auth requise ? Le sudoers `nmcli --show-secrets` est-il scopé strictement ?

**Contrat API (mismatch frontend/backend)**
- Pour chaque endpoint nouveau (`/api/discovery/wifi-speakers`, `/api/discovery/credentials`, `/api/discovery/adopt`, `/api/setup/become-client`) :
  - Pydantic model côté backend ↔ shape attendue côté `discoveryStore.js`
  - `snake_case` partout ? (CLAUDE.md exige snake_case sur Pydantic)
  - Codes HTTP cohérents avec convention projet (`status: success/error`, raise HTTPException pour vrais erreurs)
  - REST verbs respectés (`PUT` idempotent, `POST` action, `DELETE` removal, `PATCH` partial)

**Backend qualité**
- `wifi_adoption.py` : 281 lignes, vérifier découpage logique, éviter "god service". Faut-il extraire un sous-module ?
- `discovery.py` : routes vs business logic — la logique scan/adopt doit vivre dans `core/multiroom/wifi_adoption.py`, les routes restent fines.
- `dependencies.py` : ordre d'initialisation respecte la doc (CRITICAL section). Pas de cycle nouveau ?
- Async/await partout ? Pas de `subprocess.run` bloquant ?
- WebSocket events : si la feature broadcast, utilise-t-elle `state_machine.broadcast_event()` (pas `ws_manager.broadcast_dict()`) avec category cohérente (`multiroom` probable) ?

**Frontend qualité**
- `NetworkSelector.vue` (286L extraites) : props/events propres ? Réutilisable indépendamment ? Pas de dépendance cachée vers `NetworkStep` ?
- `discoveryStore.js` : state shape claire, error handling via `apiCall`, polling/refresh maîtrisé (pas de fuite d'intervals au unmount) ?
- `ConfigureSystem.vue` (+148L) : la section conditionnelle "Connexion réseau" pour wifi adoption ne complexifie-t-elle pas le composant au point qu'un sous-composant serait justifié ?
- `MultiroomSettings.vue` : unification ethernet pending + hotspot wifi — la dedup/agrégation est-elle dans le store (correct) ou dans le composant (à refactor) ?
- `SystemListItem.vue` : badge 🔌/📶 propre, design tokens utilisés (pas de couleurs hardcodées) ?
- WS events handled in store, **pas dans les composants** (CLAUDE.md pitfall #9)
- `apiCall()` utilisé pour toutes les actions API ?
- `useI18n()` dans `<script setup>`, pas `$t()` global ?

**i18n**
- Les 8 locales ont les mêmes clés (pas de drift) ?
- Les chaînes existent dans `english.json` (commit `db2bc65f` mentionne "missing English locale") ?
- Pas de hardcoded text dans les composants neufs ?

**Cohérence avec plan.md (vérifier que l'implémentation suit les décisions verrouillées)**
- Marker file : path, format, suppression après lecture par `milo-first-boot`
- Speaker name fallback `Speaker` côté backend, placeholder vide côté frontend
- Auto-fill creds via sudoers `nmcli --show-secrets` si serveur sur wifi, sinon NetworkSelector

**Out-of-scope explicit (à noter sans fixer)**
- Challenge token sur hotspot (futur)
- Reset/factory-reset (futur)

### Files-to-read auxiliaires (contexte)

- `frontend/src/components/setup/NetworkStep.vue` post-refactor (cohérence avec NetworkSelector)
- `backend/core/wifi/service.py` extensions (anciennes méthodes encore appelées correctement ?)
- `milo-client/app/routes/hardware.py` (interaction avec marker wifi-adoption ?)

### Findings (à remplir lors de l'audit)

#### B-01 — `discoveryStore.adoptSpeaker` n'a aucun timeout axios → UI peut se figer indéfiniment
- **Sévérité** : P1 design
- **Localisation** : `frontend/src/stores/discoveryStore.js:70-78` + `frontend/src/components/settings/categories/multiroom/ConfigureSystem.vue:286-296`
- **Problème** : L'appel `axios.post('/api/discovery/adopt-speaker', payload)` n'a pas de timeout (et il n'y a pas non plus de `axios.defaults.timeout` global dans le projet — vérifié par grep). Côté backend, `WifiAdoptionService._adopt_impl` peut très bien bloquer 30 s sur `_connect_to_hotspot`, 20 s sur `_push_become_client`, puis 30 s sur `_restore_connection` ; et si `_restore_connection` échoue (cf. B-02), le serveur reste sur le hotspot du speaker, donc la réponse HTTP ne reviendra jamais. La promesse `await discoveryStore.adoptSpeaker(...)` reste pending pour toujours, `isRebooting=true` reste affiché à vie, et il n'existe aucun fallback côté UI (pas de timeout local, pas de `setTimeout` watchdog comme en mode ethernet à `ConfigureSystem.vue:303-305`).
- **Fix proposé** : Passer un timeout explicite à l'appel adopt-speaker. Côté backend les délais cumulés worst-case sont ~80 s, ajouter une marge → 120 s :
  ```js
  const response = await axios.post('/api/discovery/adopt-speaker', payload, { timeout: 120000 });
  ```
  Et côté `ConfigureSystem.vue`, ajouter un watchdog timer du même genre que le mode ethernet (`rebootTimeoutId = setTimeout(() => { rebootTimedOut.value = true; }, 120000)` dans la branche wifi de `applyConfiguration`) pour que le `MessageContent` bascule sur `multiroom.pending.rebootTimeout` au lieu de tourner indéfiniment.
- **Confiance** : haute
- [x] À appliquer — 61566bb3

#### B-02 — `_restore_connection` échoue silencieusement → serveur bloqué sur hotspot
- **Sévérité** : P1 design
- **Localisation** : `backend/core/multiroom/wifi_adoption.py:217-226`
- **Problème** : Si `nmcli connection up <original_wifi>` échoue ou timeout (ex : la home wifi reste invisible parce que `wlan0` est encore associée au hotspot du speaker, ou le profil original a été corrompu par un `_delete_ssid_profiles` antérieur), `_restore_connection` se contente de logger l'erreur. Le serveur reste alors associé au hotspot du speaker — qui s'éteint quelques secondes plus tard quand le speaker reboot — et perd toute connectivité LAN. Le seul espoir de récupération est que NM décide de lui-même de se reconnecter à un profil connu, ce qui n'arrive pas si le profil reste actif côté NM mais sans AP. Sur un serveur wifi-only c'est un crash silencieux qui force une intervention physique.
- **Fix proposé** : Sur échec de `connection up <name>`, faire un `nmcli device disconnect wlan0` (qui force NM à libérer l'interface et autoconnecter sur le meilleur profil dispo, c.-à-d. la home wifi quand le speaker s'éteint) avant de logger. Code :
  ```python
  async def _restore_connection(self, name: Optional[str]) -> None:
      if not name:
          return
      rc, _, stderr = await self._run_nmcli(
          "connection", "up", name, timeout=RESTORE_CONNECT_TIMEOUT
      )
      if rc == 0:
          return
      self.logger.error(
          "Failed to restore wifi connection '%s': %s — falling back to nmcli device disconnect",
          name, stderr,
      )
      # Force NM to drop the temp hotspot and autoconnect to a known profile
      await self._run_nmcli("device", "disconnect", WLAN_INTERFACE)
  ```
  Le `device disconnect` rend la main à NM qui rebascule via autoconnect sur le profil home dès que l'AP du speaker disparaît.
- **Confiance** : moyenne
- [x] À appliquer — 61566bb3

#### B-03 — 5 clés i18n mortes × 8 locales (40 lignes) jamais référencées
- **Sévérité** : P2 lisibilité-optim
- **Localisation** : `frontend/src/locales/{english,french,chinese,german,hindi,italian,portuguese,spanish}.json` → clés `multiroom.adopt.errorWifi`, `multiroom.adopt.success`, `multiroom.discovery.title`, `multiroom.discovery.viaEthernet`, `multiroom.discovery.viaWifi`
- **Problème** : Le commit `6c6e982c` a ajouté ces 5 clés × 8 locales = 40 lignes mortes. Vérifié par `grep -rn` sur tout `frontend/src/` : 0 référence pour les 5 clés. Probablement résidu d'un design plus ancien où la section discovery avait son propre titre + sous-libellés `viaEthernet` / `viaWifi` (avant la fusion ethernet+wifi en un seul bandeau "Pending"), et où l'erreur push était split en `errorPush` / `errorWifi` (seul `errorPush` est utilisé maintenant à `ConfigureSystem.vue:310`). CLAUDE.md exige de ne PAS laisser de clés/branches mortes.
- **Fix proposé** : Supprimer les 5 clés des 8 fichiers de locale. Garder uniquement les clés effectivement utilisées (`multiroom.adopt.adopting`, `changeNetwork`, `enterCredentials`, `errorPush`, `networkSection`, `useServerWifi`).
- **Confiance** : haute
- [x] À appliquer — 61566bb3

#### B-04 — `SystemListItem` hardcode `:signal="100"` pour les hotspots wifi au lieu d'utiliser le signal du scan
- **Sévérité** : P2 lisibilité-optim
- **Localisation** : `frontend/src/components/settings/categories/multiroom/SystemListItem.vue:18-23` + `MultiroomSettings.vue:233-244`
- **Problème** : Quand `discoverySource === 'wifi'`, le composant rend `<WifiSignal :signal="100" :size="16" />` — toujours toutes les barres pleines. Pourtant `MultiroomSettings.discoveryItems` capture `signal: hotspot.signal` depuis le scan (`discoveryStore.hotspots`), mais ne le passe jamais à `SystemListItem`. Résultat : un speaker à -80 dBm en bout de portée s'affiche visuellement identique à un speaker à -40 dBm collé au serveur, et l'utilisateur ne peut pas évaluer si l'adoption a une chance d'aboutir ni si le speaker a besoin d'être déplacé.
- **Fix proposé** : Ajouter une prop `signal` à `SystemListItem` (Number, default null) et la consommer dans `<WifiSignal :signal="signal ?? 100" :size="16" />`. Côté `MultiroomSettings.vue` ligne 17-25, passer `:signal="item.signal"` quand `item.source === 'wifi'`. La valeur est déjà dans `discoveryItems[i].signal`.
- **Confiance** : haute
- [x] À appliquer — 61566bb3

#### B-05 — `NetworkSelector` expose `connectError` mais le slot d'adoption ne déclenche jamais connect/save → UI dead code
- **Sévérité** : P2 lisibilité-optim
- **Localisation** : `frontend/src/components/network/NetworkSelector.vue:54-61` + `frontend/src/components/settings/categories/multiroom/ConfigureSystem.vue:47-53`
- **Problème** : Dans `ConfigureSystem.vue` mode wifi, `<NetworkSelector :submit-action="null" @update:wifi="onManualWifiUpdate">` est utilisé sans slot `#action` — donc les closures `connect`/`save` exposées par le slot ne sont jamais invoquées. Le `connectError` ref de `useWifi()` ne peut donc jamais se peupler dans ce contexte, mais `<span v-if="connectError" class="wifi-error">` reste rendu conditionnellement. C'est du DOM mort qui n'a aucune chance d'apparaître — pollution mineure, mais le pattern devient confusant pour le prochain dev qui se demandera quand l'erreur s'affiche dans le flow d'adoption.
- **Fix proposé** : Ajouter une prop `showConnectError: Boolean = true` à `NetworkSelector`, et la mettre à `false` dans le call site d'adoption. Alternative plus simple : laisser `connectError` toujours rendu (faux positifs impossibles puisque les actions ne sont pas appelées) mais documenter le contrat d'adoption-mode dans le commentaire de tête. Préférer l'option 1 (prop) — plus explicite, ~3 lignes.
- **Confiance** : moyenne
- [x] À appliquer — 61566bb3

#### B-06 — `speaker_name` accepte une string sans `max_length` (BecomeClientRequest + AdoptSpeakerRequest)
- **Sévérité** : P2 lisibilité-optim
- **Localisation** : `backend/api/setup.py:43` (`BecomeClientRequest.speaker_name`) + `backend/api/discovery.py:36` (`AdoptSpeakerRequest.speaker_name`)
- **Problème** : Les deux modèles définissent `speaker_name: str = Field(..., min_length=1, ...)` sans borne supérieure. À comparer à `RegisterClientRequest.name` (`backend/api/models.py:478`) qui fixe `max_length=64`, à `ClientUpdateRequest.name` qui n'en a pas non plus mais qui passe par le registry (donc moins exposé), et au front (`ConfigureSystem.vue:62` met `:maxlength="16"` côté input). Donc côté UI on autorise 16 chars max, mais le backend acceptera n'importe quelle string (potentiellement un payload malicieux de 1 Mo via curl direct sur le hotspot). Pas catastrophique grâce au front, mais asymétrie + cohérence avec `RegisterClientRequest`.
- **Fix proposé** : Ajouter `max_length=64` aux deux champs `speaker_name`, comme `RegisterClientRequest.name`. Le front continue d'imposer 16 — pas de breaking.
- **Confiance** : haute
- [x] À appliquer — 61566bb3

#### B-07 — Mapping AdoptionError code → HTTP imprécis (500 au lieu de 502 pour erreurs upstream)
- **Sévérité** : P2 lisibilité-optim
- **Localisation** : `backend/api/discovery.py:51-56`
- **Problème** : `_ADOPTION_CLIENT_ERROR_CODES` ne mappe explicitement que 4 codes ; tout le reste tombe sur 500 par défaut. Or `no_gateway`, `hotspot_connect_failed`, `push_failed` sont tous des échecs upstream (le serveur lui-même fonctionne, c'est le speaker / le réseau temporaire qui défaille). Sémantiquement c'est du 502 Bad Gateway (ou 504 Gateway Timeout pour les timeouts). Conséquence pratique : le front ne peut pas distinguer "le serveur lui-même est cassé" d'un "le speaker n'a pas répondu" et affiche le même message générique `multiroom.adopt.errorPush` dans les deux cas.
- **Fix proposé** : Étendre la table :
  ```python
  _ADOPTION_CLIENT_ERROR_CODES = {
      "invalid_ssid": 400,
      "invalid_target_wifi": 400,
      "already_configured": 409,
      "push_rejected": 502,
      "push_failed": 502,
      "no_gateway": 502,
      "hotspot_connect_failed": 502,
  }
  ```
  Garder 500 par défaut pour les erreurs réellement internes (non-AdoptionError, cf. ligne 133-138).
- **Confiance** : haute
- [x] À appliquer — 61566bb3

#### B-08 — Code `invalid_ssid` réutilisé pour deux scénarios distincts (mauvais SSID vs serveur en mode hotspot)
- **Sévérité** : P2 lisibilité-optim
- **Localisation** : `backend/core/multiroom/wifi_adoption.py:87-90`
- **Problème** : La même `AdoptionError(code="invalid_ssid")` est levée pour (a) `ssid != HOTSPOT_NAME` (vrai cas de SSID invalide) et (b) `wifi_service.hotspot_active=True` (le serveur lui-même diffuse son hotspot — c'est un état serveur, pas un SSID invalide). Le détail du message diffère mais le code est partagé, donc une UI qui voudrait mapper finement (ex : "Désactivez d'abord votre point d'accès Milō" vs "Speaker pas trouvé") ne peut pas distinguer.
- **Fix proposé** : Introduire un code dédié pour le cas serveur-en-mode-hotspot :
  ```python
  if self.wifi_service.hotspot_active:
      raise AdoptionError("server_in_hotspot_mode", "Cannot adopt while broadcasting the setup hotspot")
  ```
  Ajouter `"server_in_hotspot_mode": 409` dans `_ADOPTION_CLIENT_ERROR_CODES` (le serveur est dans un état incompatible avec l'action — sémantique 409 Conflict).
- **Confiance** : haute
- [x] À appliquer — 61566bb3

#### B-09 — `become-client` : pas de lock → écritures parallèles sur `pending_client_role.json` peuvent se clobber
- **Sévérité** : P2 lisibilité-optim
- **Localisation** : `backend/api/setup.py:158-259` + `_atomic_write_json` ligne 54-61
- **Problème** : Si deux POST `/api/setup/become-client` arrivent en parallèle (scénario rare mais possible — deux serveurs Milō sur le même hotspot, ou un retry FE qui double-fire), la check `setup_completed` ligne 184 peut être satisfaite par les deux requêtes avant que la première n'ait écrit. Les deux écrivent `pending_client_role.json` via `_atomic_write_json`, qui utilise un nom de tempfile fixe `{path}.tmp` — donc deux `open(tmp, "w")` truncate concurrent + deux `os.replace` : la victoire revient au dernier qui rename, mais le marker peut contenir un mélange via fsync interleavé (très improbable mais non garanti). Le `wifi_service.save_network` interne a un lock (`_connect_lock`), mais le `setup_completed` write via SettingsService est sérialisé ; le marker write ne l'est pas.
- **Fix proposé** : Soit (a) ajouter une `asyncio.Lock` module-level dans `setup.py` et l'acquérir au début de `become_client`, soit (b) utiliser `tempfile.NamedTemporaryFile(dir=path.parent, delete=False)` dans `_atomic_write_json` pour avoir un nom de tempfile unique par process. (a) est plus défensif (sérialise toute la séquence marker+wifi+setup_completed), (b) corrige juste la race sur le marker. Préférer (a) : `_become_client_lock = asyncio.Lock()` au top du module, `async with _become_client_lock:` autour de tout le corps de la route.
- **Confiance** : moyenne
- [x] À appliquer — 61566bb3

---

## Phase C — Détection conflit hostname mDNS + auto-reclaim

- [ ] **Audit**
- [ ] **Fix**

### Commits couverts
- `375473f7` feat(system): mDNS hostname conflict detection + auto-reclaim
- `db2bc65f` fix(system): detect parasite milo-N.local servers + add missing English locale
- `0f4272a0` feat(message-content): expose loading state on CTAs + wire into recheck button
- `6fdc2922` feat(system): show device name + IP on conflict alert for clarity
- `584d0e6b` style: remove 'details' style in MessageContent

### Fichiers (lire intégralement)

**Backend**
- `backend/core/system/hostname_conflict.py` (+324 / -0) — **fichier neuf, cœur**
- `backend/core/system/__init__.py` (+4 / -0)
- `backend/api/system.py` (+18 / -1)
- `backend/main.py` (+7 / -0)
- `backend/dependencies.py` (+5 / -0)

**Frontend**
- `frontend/src/components/system/HostnameConflictView.vue` (+78 / -0) — **neuf**
- `frontend/src/components/ui/MessageContent.vue` (+38 / -11) — loading state CTA + retrait style 'details'
- `frontend/src/stores/systemStore.js` (+80 / -0) — **neuf**
- `frontend/src/App.vue` (+13 / -1) — wire-in HostnameConflictView
- `frontend/src/components/settings/SettingsModal.vue` (+15 / -7) — interaction probable

**i18n**
- `frontend/src/locales/*.json` (+24 chaque, partagé avec Phase B mais clés distinctes)

### Checklist de concerns (review approfondie)

**Détection (criticité haute)**
- `hostname_conflict.py` : algo de détection — quels signaux déclenchent un conflit ?
  - Y a-t-il des **faux positifs** ? (ex: notre propre annonce vue par nous-même ?)
  - Détection `milo-N.local` parasites (cf `db2bc65f`) — sur quoi se base-t-elle ? Regex ? Priorité hostname `milo.local` ?
  - mDNS timing : combien de temps on écoute avant de conclure ?
  - Cache TTL avahi : on flush ou on attend ?

**Auto-reclaim (criticité haute)**
- Conditions d'auto-reclaim : restrictives ? (ex: ne pas reclaim si l'autre est notre client légitime)
- Risque de boucle : reclaim → autre device reclaim → re-reclaim... ? Backoff/limit ?
- Reclaim modifie-t-il l'hostname système ? Si oui : `hostnamectl` async, pas de race avec NetworkManager ?

**API & WebSocket**
- Routes dans `api/system.py` : `recheck` endpoint ? Use-cases ?
- WS broadcast : category `system`, event type clair, `state_machine.broadcast_event()` (pas direct ws_manager)
- `dependencies.py` ordre d'init : `HostnameConflictService` initialisé avant ou après `state_machine` ?

**Frontend UX**
- `HostnameConflictView.vue` : modal bloquant, banner dismissible, ou overlay ? Quel comportement actuel ?
- `MessageContent.vue` (loading state CTA) : la prop est-elle assez générique ? Pattern réutilisable ailleurs ?
- Le retrait du style `details` (commit `584d0e6b`) : visuel cohérent avec le design system ? Pas de breakage ailleurs où `details` était utilisé ?
- `systemStore.js` : poll vs WS ? Si poll : interval raisonnable, cleanup au unmount ? Si WS : event handling correct ?
- L'affichage device name + IP (`6fdc2922`) : i18n correctement, pas de leak d'IP dans logs partagés ?

**Conventions projet**
- Pas de WS event handling dans `App.vue` ou `HostnameConflictView.vue` (doit être dans store)
- `useI18n()` dans `<script setup>`
- Design tokens (`var(--color-*)`)
- Pas de dépendance vers stores audio (système isolé)

**Cohérence inter-features**
- Phase A introduit la détection mDNS via `milo-mdns-probe` — y a-t-il duplication entre `milo-mdns-probe` (shell, boot) et `hostname_conflict.py` (python, runtime) ?
- Si duplication : c'est OK (contextes différents) mais documenter, sinon factoriser.

**i18n**
- Clés `system.*` ou `hostnameConflict.*` cohérentes
- 8 locales en parité

### Files-to-read auxiliaires (contexte)

- `backend/core/state.py` (broadcast_event signature)
- `frontend/src/locales/english.json` (vérifier qu'il a bien rattrapé son retard sur les autres)

### Findings (à remplir lors de l'audit)

> Format par finding (préfixe `C-NN`) — voir Phase A pour le template.

_(à remplir)_

---

## Out-of-scope global (toutes phases)

Reportés à un autre cycle si découverts :
- Coverage de tests
- Refonte i18n / extraction d'un système de pluralisation
- Migration vers TypeScript frontend
- Documentation utilisateur des nouvelles features
- Hardening sécurité hotspot adoption (challenge token) — déjà documenté dans plan d'origine
- Reset/factory-reset milo-client

## Convention de coche

État d'une phase :

```markdown
- [x] **Audit** — fait le 2026-MM-DD, N findings (P0: x / P1: y / P2: z)
- [x] **Fix** — fait le 2026-MM-DD, A appliqués / B écartés / C reportés
```

Findings reportés (volontairement non traités) : laisser dans la section `### Findings` avec une note `> reporté: <raison>` sous le finding, et la case décochée. Ils servent de trace écrite pour un cycle ultérieur.
