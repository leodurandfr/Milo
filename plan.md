# Plan — Sémantique `setup_completed` + adoption wifi des milo-clients

## Contexte

Aujourd'hui :
- Un raspberry fraîchement flashé démarre avec hostname `milo`. Au premier boot, `milo-first-boot` détecte si un `milo.local` est déjà sur le LAN (probe mDNS via ethernet) → si oui, bascule en `milo-client`, sinon reste serveur (wizard accessible).
- Le seul flow de déploiement client viable est **ethernet au premier boot** (un milo-client wifi-only n'est pas supporté).
- `setup_completed=true` est écrit par `configure_client` au moment du basculement, **uniquement pour empêcher milo-first-boot de re-trigger**. C'est sémantiquement faux : à ce stade le client n'a pas de carte audio et apparaît "pending" côté serveur.

Goal :
1. **Phase 1** — Rendre `setup_completed=true` cohérent : true uniquement quand l'utilisateur a fini la config (wizard pour serveur, ConfigureSystem.vue pour client). Permettre le revert client→server si `setup_completed=false` et milo.local disparu.
2. **Phase 2** — Ajouter le support wifi-only client : un nouveau Raspberry sans ethernet (donc en hotspot) est détecté par le serveur existant qui l'adopte via wifi (push wifi creds + config audio).

Au final : symétrie complète — un nouveau device peut être adopté comme client soit via ethernet (existing), soit via wifi (nouveau), avec la même UI côté serveur.

---

## Décisions d'architecture (verrouillées)

### Phase 1
- **Skip-check de milo-first-boot reste sur `setup_completed`** (pas sur `mode`)
- **Hostname est la source de vérité du rôle actuel** (pas de champ `mode` dans settings.json)
- **Probe mDNS uniquement via ethernet** (pas de revert si pas d'ethernet — cohérent avec le comportement actuel)
- **`/var/lib/milo` chowné à `milo-client:audio`** sur un client (le user `milo` est inutile sur un client)
- **Au revert client→server : on supprime `/var/lib/milo/settings.json`** (le backend serveur le recréera avec defaults)
- **`setup_completed=true` est écrit côté client** dans le handler `POST /api/hardware/reboot` du milo-client backend, AVANT de lancer apply-hardware

### Phase 2
- **SSID hotspot devient unique par device** : `Milō-XXXX` où XXXX = 4 derniers chars du MAC (uppercase hex, sans `:`)
- **Le serveur orchestre l'adoption** (switch wifi temporaire si nécessaire, push config, restore wifi)
- **Endpoint sur le device en mode "fresh server"** : `POST /api/setup/become-client` reçoit la config, écrit un fichier marker, applique wifi creds, marque `setup_completed=true`, reboot
- **`milo-first-boot` lit le fichier marker** au boot suivant : exécute `configure_client` avec la config audio fournie + écrit `hardware.json` du milo-client + supprime le marker → reboot
- **UI unifiée côté serveur** : la liste "Nouveaux haut-parleurs détectés" agrège pending ethernet + hotspots wifi, avec badge 🔌/📶
- **`ConfigureSystem.vue` étendu** avec section conditionnelle "Connexion réseau" en haut pour les adoptions wifi
- **Auto-fill wifi creds** si serveur sur wifi (via sudoers `nmcli ... --show-secrets`), sinon NetworkStep-like selector
- **Speaker name par défaut** : `Speaker-XXXX` (4 derniers chars MAC) si vide
- **Hotspot ouvert sans auth pour MVP** (proximité physique requise comme protection ; ajout d'un challenge token noté comme amélioration future)

### Hors scope de ce plan
- Reset/factory-reset d'un milo-client déjà setup (réflashage manuel pour l'instant)
- Flow alternatif "ce device est destiné à être un serveur même si milo.local existe" (force server mode)
- Support adoption wifi sans wifi sur le serveur (limitation acceptée)

---

## Pi de test

`192.168.1.55`, login `milo` / password `milo` (sshpass-able).

---

# PHASE 1 — Sémantique `setup_completed`

**Estimation** : ~150 lignes, 2 fichiers principaux modifiés.

## Étape 1.1 — milo-first-boot : refonte du flow principal

**Fichier** : `rootfs/usr/local/bin/milo-first-boot`

**Changements** :
- [x] Garder le skip-check sur `setup_completed` (lignes 30-39, inchangé).
- [x] Supprimer le bloc d'écriture de `setup_completed=true` dans `configure_client` (lignes 201-208).
- [x] Remplacer `chown milo:milo "$MILO_DATA_DIR/settings.json"` par `chown -R milo-client:audio "$MILO_DATA_DIR"` (sur un client le user `milo` est inutile).
- [x] `configure_client` ne crée pas `settings.json` du tout (le milo-client backend le créera vide au prochain démarrage si besoin).
- [x] Refactorer le `Main` (lignes 217-242) en logique idempotente :
  ```bash
  current_hostname=$(hostname)
  
  if has_ethernet_carrier 15 && wait_for_ethernet_ip; then
      sleep 5
      milo_local_found=false
      for attempt in 1 2 3; do
          if /usr/bin/python3 /usr/local/bin/milo-mdns-probe milo 10 "$ETH_IP"; then
              milo_local_found=true
              break
          fi
          (( attempt < 3 )) && sleep 3
      done
      
      if $milo_local_found; then
          if [[ "$current_hostname" != "milo-client" ]]; then
              configure_client  # reboots
          fi
      else
          if [[ "$current_hostname" == "milo-client" ]]; then
              configure_server  # reboots
          fi
      fi
  fi
  
  exit 0
  ```

**Acceptance** :
- Sans ethernet : exit 0 (aucune action), comportement identique à aujourd'hui.
- Ethernet + milo.local + hostname=milo : configure_client + reboot.
- Ethernet + milo.local + hostname=milo-client : exit 0 (déjà client).
- Ethernet + pas milo.local + hostname=milo : exit 0 (déjà serveur).
- Ethernet + pas milo.local + hostname=milo-client : configure_server + reboot.

## Étape 1.2 — milo-first-boot : ajouter `configure_server` (revert)

**Fichier** : `rootfs/usr/local/bin/milo-first-boot`

**Changements** :
- [x] Ajouter une fonction `configure_server()` symétrique à `configure_client()`. Doit :
  - Set hostname à `milo` + update `/etc/hosts`
  - Restaurer `/etc/asound.conf` depuis `$MILO_APP_DIR/rootfs/etc/asound.conf`
  - Supprimer `/etc/modprobe.d/milo-client-loopback.conf` si présent
  - Désactiver les services client (`milo-client`, `milo-client-snapclient`, `milo-client-camilladsp`)
  - Réactiver les services serveur (`milo-backend`, `milo-kiosk`, `milo-readiness`, `milo-bluealsa`, `milo-bluealsa-aplay`, `milo-camilladsp`, `nginx`)
  - Supprimer `/var/lib/milo/settings.json` (le backend serveur le recréera avec defaults)
  - `chown -R milo:milo /var/lib/milo`
  - Garder `/var/lib/milo-client`, user `milo-client`, symlinks (innocents sur serveur)
  - Logger explicitement chaque étape pour debug
  - `/sbin/reboot`

**Acceptance** :
- Après revert : hostname=milo, services serveur up, asound serveur, settings.json reset.
- Boot suivant : milo-first-boot exit early (already server, no milo.local).

## Étape 1.3 — milo-client : `setup_completed=true` avant reboot

**Fichier** : `milo-client/app/routes/hardware.py`

**Changements** :
- [x] Ajouter une fonction utilitaire `_set_setup_completed_in_milo_settings()` :
  ```python
  MILO_SETTINGS_FILE = "/var/lib/milo/settings.json"
  
  def _set_setup_completed_in_milo_settings() -> None:
      """Mark setup as completed in /var/lib/milo/settings.json (read by milo-first-boot)."""
      try:
          with open(MILO_SETTINGS_FILE) as f:
              data = json.load(f)
      except (FileNotFoundError, json.JSONDecodeError):
          data = {}
      data["setup_completed"] = True
      tmp = MILO_SETTINGS_FILE + ".tmp"
      with open(tmp, "w") as f:
          json.dump(data, f, indent=2)
          f.flush()
          os.fsync(f.fileno())
      os.replace(tmp, MILO_SETTINGS_FILE)
  ```
- [x] Dans le handler `reboot()`, appeler cette fonction AVANT de lancer le subprocess `milo-client-apply-hardware` (et logger).
- [x] Si l'écriture échoue : logger error, mais continuer le reboot (pas bloquer).

**Acceptance** :
- Après ConfigureSystem.vue → apply : `/var/lib/milo/settings.json` contient `{"setup_completed": true}`.
- Au boot suivant : milo-first-boot skip (rôle verrouillé).

## Étape 1.4 — Validation Phase 1 sur Pi de test

**Acceptance** :
- [ ] Test 1 : Fresh flash + ethernet + milo.local sur LAN → boot 1 configure_client + reboot. Boot 2 : skip (already client). Apparaît "pending" sur serveur.
- [ ] Test 2 : Configurer depuis ConfigureSystem.vue → apply → reboot. Au reboot : `setup_completed=true`. Boot suivants : skip. Apparaît "configured" côté serveur.
- [ ] Test 3 : Fresh flash + ethernet + pas de milo.local → exit (déjà serveur). Wizard accessible.
- [ ] Test 4 : Wizard non terminé → débrancher ethernet → reboot → pas d'ethernet → exit. Hotspot. Wizard toujours accessible.
- [ ] **Test clé** : configure_client passé, `setup_completed=false`, milo.local disparaît, reboot du client → revert serveur + reboot. Boot suivant : reste milo, wizard accessible.
- [ ] Test 6 : Wizard serveur fini → setup_completed=true → reboot → skip (verrouillé).

---

# PHASE 2 — Adoption wifi des milo-clients

**Estimation** : ~530 lignes, ~6 fichiers modifiés/créés.

## Étape 2.1 — Hotspot SSID unique par device

**Fichier** : `backend/core/wifi/service.py`

**Changements** :
- [x] Calculer `HOTSPOT_CON_NAME` dynamiquement au moment de l'activation : lire `/sys/class/net/wlan0/address`, prendre les 4 derniers chars hex (uppercase, sans `:`), former `Milō-XXXX`.
- [x] Adapter `_activate_hotspot()` et `_delete_hotspot_profile()` pour utiliser ce nom dynamique.
- [x] Adapter le dispatcher `rootfs/etc/NetworkManager/dispatcher.d/90-milo-network` (et son équivalent client) : matcher tout SSID `Milō-*` au lieu de juste `Milō`. *(Note : le dispatcher milo-client ne référence pas le hotspot, aucun changement requis.)*
- [x] Adapter `wifi/service.py::_has_active_connection()` pour détecter aussi `Milō-*`.
- [x] Mettre à jour les i18n strings du wizard `setup.wifi.*` qui mentionnent "Milō" → utiliser le nouveau format dans l'affichage si pertinent. *(Note : aucun string `setup.wifi.*` ne mentionne le SSID hotspot ; `setup.summary.accessHint` utilise `{ssid}` paramétré, pas de changement requis.)*

**Acceptance** :
- Hotspot s'appelle `Milō-AB12` (format MAC-based, unique par device).
- Backwards compatible : aucun device existant n'utilisait l'ancien format de manière persistante.

## Étape 2.2 — Endpoint serveur : scan hotspots

**Fichier** : `backend/api/setup.py` (étendre) OU nouveau `backend/api/discovery.py`

**Décision** : créer `backend/api/discovery.py` (le scan hotspot n'est pas du setup wizard).

**Changements** :
- [x] Nouveau router `GET /api/discovery/wifi-speakers` qui :
  - Réutilise `wifi_service.scan_networks()` (déjà existant, scan via nmcli avec `--rescan yes`, timeout 15s)
  - Filtre les SSID matchant `HOTSPOT_NAME_RE` (`^Milō-[0-9A-F]{4}$`), exclut le hotspot propre du device
  - Retourne `{"status": "success", "data": {"hotspots": [{"ssid": "Milō-AB12", "mac_suffix": "AB12", "signal": 75}, ...]}}` (convention `status/data` du codebase)
- [x] Enregistrer le router dans `main.py`.
- [x] ~~Sudoers : `milo ALL=(root) NOPASSWD: /usr/bin/nmcli device wifi list*`~~ — non nécessaire : la polkit rule `50-milo-networkmanager.rules` autorise déjà toutes les actions NetworkManager pour le user `milo` (utilisé par `wifi_service` existant sans sudo).

**Acceptance** :
- Appel `/api/discovery/wifi-speakers` retourne les hotspots Milō-XXXX visibles.

## Étape 2.3 — Endpoint serveur : récupérer wifi creds actifs

**Fichier** : `backend/api/discovery.py`

**Changements** :
- [x] Nouveau `GET /api/discovery/server-wifi-creds` qui :
  - Run `nmcli -t -f NAME,DEVICE connection show --active` pour trouver la connexion active sur wlan0
  - Si trouvée : `nmcli -s -t -f 802-11-wireless.ssid,802-11-wireless-security.psk connection show <NAME>` (avec `--show-secrets`)
  - Retourne `{"available": true, "ssid": "MyHomeWifi", "password": "secret"}` ou `{"available": false}` (cas serveur ethernet only)
- [x] ~~Sudoers : `milo ALL=(root) NOPASSWD: /usr/bin/nmcli -s * connection show *` (granulaire).~~ — non nécessaire : la polkit rule `50-milo-networkmanager.rules` autorise déjà toutes les actions NetworkManager pour le user `milo`, y compris `GetSecrets` D-Bus (gated by `org.freedesktop.NetworkManager.settings.modify.system`). Implémenté via `WifiService.get_active_wifi_credentials()` qui réutilise `_run_nmcli` (sans sudo, comme le reste du service).

**Acceptance** :
- Si serveur sur wifi : retourne SSID + password.
- Si serveur ethernet only : retourne `{"available": false}`.

## Étape 2.4 — Endpoint device : `become-client`

**Fichier** : `backend/api/setup.py` (le device en mode "fresh server" expose ces routes)

**Changements** :
- [x] Nouveau `POST /api/setup/become-client` payload :
  ```python
  class BecomeClientRequest(BaseModel):
      wifi_ssid: str
      wifi_password: str
      audio_id: str
      speaker_name: str
      speaker_type: str  # "satellite" | "bookshelf" | "tower" | "subwoofer"
  ```
- [x] Validation : audio_id ∈ AUDIO_CARDS (excl. `none`), speaker_type ∈ SPEAKER_TYPES, ssid non vide. Réponse 409 si `setup_completed` est déjà true (idempotency guard).
- [x] Étapes :
  1. Écrire `/var/lib/milo/pending_client_role.json` avec `{audio_id, overlay, volume_control, speaker_name, speaker_type}` (atomic write via tempfile + fsync + os.replace ; `volume_control` auto-détecté via `is_dac_card()` comme `configure_pending_client`).
  2. Sauvegarder le profil wifi cible via `wifi_service.save_network(ssid, password)` — pas de switch live, le hotspot ouvert reste actif pour que la réponse HTTP atteigne le serveur ; NetworkManager se connectera au reboot.
  3. Marquer `setup_completed=true` via SettingsService.
  4. Réponse HTTP 200 `{"status": "rebooting"}`, puis fire-and-forget `sudo /usr/sbin/reboot` (1s delay pour laisser la réponse sortir).
  5. Rollback : si une étape échoue après l'écriture du marker, suppression du marker pour permettre un retry propre.
- [x] ~~Sudoers nécessaire pour les commandes nmcli wifi connection add (ajout granulaire).~~ — non nécessaire : la polkit rule `50-milo-networkmanager.rules` autorise déjà toutes les actions NetworkManager pour le user `milo` (`wifi_service.save_network()` utilise `nmcli` sans sudo). `sudo /usr/sbin/reboot` est déjà autorisé dans `/etc/sudoers.d/milo-backend`.

**Acceptance** :
- POST reussi → fichier marker créé, wifi configuré, `setup_completed=true`, reboot.

## Étape 2.5 — Backend serveur : orchestration adoption

**Fichier** : `backend/core/multiroom/wifi_adoption.py` (nouveau)

**Changements** :
- [x] Nouveau service `WifiAdoptionService` avec méthode `adopt_speaker(ssid, audio_id, speaker_name, speaker_type, wifi_ssid, wifi_password)` :
  1. Capture connexion réseau active actuelle (pour restore).
  2. `nmcli connection add type wifi ifname wlan0 con-name "<hotspot_ssid>" ssid "<hotspot_ssid>" wifi-sec.key-mgmt none` (open hotspot).
  3. `nmcli connection up "<hotspot_ssid>"` (timeout 30s).
  4. Récupère gateway IP (typiquement `10.42.0.1`) via `ip route show default`.
  5. POST `http://<gateway>:8000/api/setup/become-client` avec le payload.
  6. Cleanup : `nmcli connection delete "<hotspot_ssid>"` + reconnecte connexion d'origine.
  7. Retourne success/error + détails pour UI.
- [x] Endpoint `POST /api/discovery/adopt-speaker` qui appelle ce service.
- [x] Gestion d'erreurs robuste : si étape 5 échoue, restaurer la connexion d'origine puis retourner erreur explicite. Cleanup garanti via `try/finally` (le profil temporaire et la connexion d'origine sont restaurés même en cas d'exception après l'association). `AdoptionError` codé (`invalid_ssid`, `invalid_target_wifi`, `hotspot_connect_failed`, `no_gateway`, `push_failed`, `push_rejected`, `already_configured`) → HTTP 400/409/502/500 selon le cas.

**Acceptance** :
- Adopter un hotspot vu sur le LAN → device reboot → device joint le wifi maison → s'enregistre comme client configuré.

## Étape 2.6 — milo-first-boot : lecture du fichier pending_client_role

**Fichier** : `rootfs/usr/local/bin/milo-first-boot`

**Changements** :
- [x] Modifier le skip-check : avant d'exit sur `setup_completed=true`, vérifier si `/var/lib/milo/pending_client_role.json` existe :
  - Si oui : lire le fichier, exécuter `configure_client_with_hardware()` (nouvelle fonction qui appelle configure_client + écrit `hardware.json` du milo-client avec les valeurs lues), supprimer le fichier marker, reboot.
  - Si non : exit 0 (rôle verrouillé, normal).
- [x] Nouvelle fonction `configure_client_with_hardware()` qui :
  - Appelle un helper `_apply_client_filesystem` partagé (extrait de `configure_client`) pour la logique commune (hostname, user, services, ALSA), sans toucher à settings.json (setup_completed déjà true).
  - Lit le marker JSON et écrit `/var/lib/milo-client/hardware.json` (audio_id/overlay/volume_control) + `/var/lib/milo-client/identity.json` (name/speaker_type) en atomic write, puis supprime le marker.
  - Si l'application du marker échoue (lecture, clé manquante, IO), exit 1 sans reboot pour permettre un retry au prochain boot.

**Acceptance** :
- Au boot après `become-client` : le device passe en milo-client avec hardware déjà appliqué.

## Étape 2.7 — Registration : envoyer name + type au serveur

**Fichier** : `milo-client/app/services/registration.py`

**Changements** :
- [x] Lire `/var/lib/milo-client/identity.json` si présent, ajouter `name` + `speaker_type` au payload de registration.
- [x] Côté serveur (`backend/api/multiroom.py::register_client`) : si `name` et `speaker_type` fournis dans le payload, les utiliser pour pré-remplir le client dans le registry (au lieu d'attendre une étape "configure" séparée). *(Implémentation : staged via `pending_clients_service` so the existing snapclient-connect transfer logic in `websocket.py` picks them up at registry insertion time.)*

**Acceptance** :
- Le client wifi-adopté s'enregistre directement avec son nom et type → apparaît dans la liste "configured" sans passer par "pending".

## Étape 2.8 — Frontend : extraction NetworkSelector réutilisable

**Fichier** : `frontend/src/components/setup/NetworkStep.vue` → extraire la partie scan/select/password en `frontend/src/components/network/NetworkSelector.vue`

**Changements** :
- [x] Extraire le bloc "wifi networks" + "country selector" + "password input" en composant réutilisable `NetworkSelector.vue` qui émet `update:wifi {ssid, password}`. *(Implémentation : NetworkSelector encapsule le state via useWifi() per-instance, expose les closures `connect`/`save` via le slot `action`, et accepte une prop `submitAction` pour gérer Enter dans le password input — évite de fragmenter le password ref entre composants.)*
- [x] Refactor `NetworkStep.vue` pour utiliser ce nouveau composant.
- [x] Vérifier qu'aucun comportement existant ne casse (test wizard serveur).

**Acceptance** :
- `NetworkStep.vue` fonctionne identiquement à avant.
- `NetworkSelector.vue` peut être utilisé seul pour entrer un SSID + password.

## Étape 2.9 — Frontend : nouveau store discovery

**Fichier** : `frontend/src/stores/discoveryStore.js` (nouveau)

**Changements** :
- [x] Store Pinia avec :
  - `state` : `{ hotspots: [], scanning: false, serverWifiCreds: null }`
  - `actions` : `scanHotspots()`, `loadServerWifiCreds()`, `adoptSpeaker(payload)`
- [x] Polling automatique des hotspots toutes les 10s pendant que la modal est ouverte. *(Implémentation : `startPolling()` / `stopPolling()` reference-counted, kicke un scan immédiat puis intervalle 10s. `adoptSpeaker` retire optimistiquement le hotspot adopté de la liste car le device va reboot et cesser d'émettre.)*

**Acceptance** :
- Le store reflète l'état des hotspots détectés et des wifi creds du serveur.

## Étape 2.10 — Frontend : badge dans SystemListItem.vue + agrégation MultiroomSettings.vue

**Fichiers** : `frontend/src/components/settings/categories/multiroom/SystemListItem.vue`, `MultiroomSettings.vue`

**Changements** :
- [x] Ajouter prop `discoverySource: 'ethernet' | 'wifi'` à `SystemListItem.vue` + petit badge icône (SvgIcon `network` pour ethernet, `WifiSignal` plein pour wifi — réutilisation du composant déjà utilisé dans NetworkStep, évite l'ajout d'une nouvelle asset).
- [x] Dans `MultiroomSettings.vue`, fusionner `pendingClients` (multiroomStore) + `hotspots` (discoveryStore) en une seule liste "Nouveaux haut-parleurs détectés" (computed `discoveryItems`). Polling discoveryStore lifecycle géré via onMounted/onBeforeUnmount.
- [x] Identifier la source par origine (clé `mac_id` pour pending vs `ssid` pour hotspot) → dériver le badge via `source: 'ethernet' | 'wifi'` injecté dans chaque item.
- [x] Click sur item ethernet → `ConfigureSystem.vue` mode ethernet (existing behavior).
- [x] Click sur item wifi → push de `multiroom-configure-system` avec `hotspotToAdopt` stashé dans SettingsModal (consommé par ConfigureSystem en étape 2.11). `macId` relâché de `required` à `default: null` dans ConfigureSystem pour accepter le mode wifi sans crash entre 2.10 et 2.11.

**Acceptance** :
- La liste affiche pending ethernet + hotspots wifi avec badges visuels.

## Étape 2.11 — Frontend : ConfigureSystem.vue extension wifi

**Fichier** : `frontend/src/components/settings/categories/multiroom/ConfigureSystem.vue`

**Changements** :
- [ ] Ajouter prop `mode: 'ethernet' | 'wifi'` (default `'ethernet'`).
- [ ] Ajouter prop `hotspotSsid: String` (uniquement utile en mode wifi).
- [ ] Si `mode === 'wifi'`, afficher en haut une section "Connexion réseau" :
  - Si `discoveryStore.serverWifiCreds.available === true` : "Ce haut-parleur sera connecté à `<ssid>` (réseau du serveur)" + bouton "Changer" qui révèle le `NetworkSelector.vue`.
  - Si `serverWifiCreds.available === false` : afficher directement `NetworkSelector.vue` pour saisie manuelle.
- [ ] Au moment de l'apply :
  - Mode ethernet : appel existant `multiroomStore.configurePendingClient(macId, payload)`.
  - Mode wifi : appel `discoveryStore.adoptSpeaker({ssid: hotspotSsid, audio_id, speaker_name, speaker_type, wifi_ssid, wifi_password})`.
- [ ] Speaker name : valeur par défaut `Speaker-XXXX` (4 derniers chars de mac_suffix dérivés du SSID `Milō-XXXX`) si vide.

**Acceptance** :
- Mode ethernet : comportement identique à aujourd'hui.
- Mode wifi : section réseau apparaît, auto-fill marche, adoption end-to-end fonctionnelle.

## Étape 2.12 — i18n

**Fichier** : `frontend/src/locales/en.json`, `frontend/src/locales/fr.json`

**Changements** :
- [ ] Nouveaux strings :
  - `multiroom.discovery.title` : "Nouveaux haut-parleurs détectés" / "Newly discovered speakers"
  - `multiroom.discovery.viaEthernet` : "Connecté en ethernet"
  - `multiroom.discovery.viaWifi` : "Hotspot WiFi détecté"
  - `multiroom.adopt.networkSection` : "Connexion réseau"
  - `multiroom.adopt.useServerWifi` : "Utiliser le réseau du serveur (`<ssid>`)"
  - `multiroom.adopt.changeNetwork` : "Changer"
  - `multiroom.adopt.enterCredentials` : "Le serveur n'est pas connecté en wifi. Entrez les identifiants du réseau pour ce haut-parleur."
  - `multiroom.adopt.adopting` : "Adoption en cours..."
  - `multiroom.adopt.success` : "Haut-parleur ajouté avec succès"
  - `multiroom.adopt.errorWifi` : "Échec de la connexion au hotspot du haut-parleur"
  - `multiroom.adopt.errorPush` : "Le haut-parleur n'a pas accepté la configuration"
  - Tous en EN aussi.

**Acceptance** :
- Aucune key manquante dans la console au runtime.

## Étape 2.13 — Validation Phase 2 sur 2 Pi de test

**Setup** : Un Pi `192.168.1.55` configuré comme serveur (setup_completed=true). Un second Pi (raspberry de test à flasher) avec l'image fresh.

**Acceptance** :
- [ ] Test A — adoption wifi end-to-end :
  - Boot du second Pi sans ethernet → hotspot `Milō-XXXX` actif.
  - Sur le serveur, ouvrir Settings → Multiroom → la liste affiche le hotspot avec badge wifi.
  - Click → ConfigureSystem.vue mode wifi → wifi creds pré-remplies (cas 1, serveur sur wifi) → choisir audio + nom → Apply.
  - Le serveur perd brièvement la connexion (~30s), le second Pi reboot.
  - Le second Pi joint le wifi maison, s'enregistre comme milo-client configuré (apparaît directement dans la liste configured, pas pending).
  - Le serveur reprend sa connexion wifi maison.
- [ ] Test B — adoption wifi avec serveur ethernet only :
  - Idem mais serveur en ethernet → ConfigureSystem.vue mode wifi affiche NetworkSelector → user entre creds manuellement → adoption OK.
- [ ] Test C — flow ethernet inchangé :
  - Brancher le second Pi en ethernet → apparaît en pending → ConfigureSystem.vue mode ethernet (sans section wifi) → Apply → reboot → configured.
- [ ] Test D — adoption échoue (mauvais wifi password) :
  - Adopter avec un mauvais password wifi maison → device reboot, ne joint pas le wifi → après timeout, hotspot revient → user retente.
- [ ] Test E — vérification SSID unique :
  - Booter 2 Pis sans ethernet en même temps → 2 hotspots distincts visibles avec MAC suffixes différents.

---

## Notes pour la conversation d'implémentation

1. **Lire CLAUDE.md** et respecter les conventions (commentaires en anglais, pas de migration code, pas de fallback).
2. **Avant chaque étape** : vérifier l'état actuel du fichier (la structure peut avoir évolué).
3. **Atomic writes** : tous les writes sur settings.json / hardware.json / pending_client_role.json doivent passer par `os.replace` (tempfile + rename).
4. **Logging** : préfixer les logs de `milo-first-boot` avec `[milo-first-boot]`. Côté backend, utiliser `logger = logging.getLogger(__name__)`.
5. **Reboot** : toujours après que les changements soient sur disque (sync implicite via fsync + os.replace). Préférer `/sbin/reboot` direct dans les scripts shell, `subprocess.Popen(["sudo", "/sbin/reboot"])` côté Python (vérifier sudoers).
6. **Tester sur Pi de test** après chaque phase, pas après chaque étape (sauf si une étape touche le boot path).
7. **Si une étape révèle un problème non anticipé** : noter dans une section "Découvertes en cours d'implémentation" en bas du plan, et proposer un fix avant de continuer.
