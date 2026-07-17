# Migration Podcast : Taddy → Podcast Index

> **Statut : PHASE 1 (backend) FAITE — Phase 2 (frontend) et fin de Phase 3 restantes.**
> Document d'exécution auto-suffisant.
> Pensé pour être exécuté dans une ou plusieurs conversations Claude Code *futures*
> (chaque phase est indépendante). Coche les cases au fur et à mesure.

## Suivi

- [x] **Phase 0** — Prérequis humains (obtenir clé+secret Podcast Index) *(clé valide dans `constants.py`, validée live le 2026-07-17 — la 1re clé fournie était erronée, le secret était bon)*
- [x] **Phase 1** — Backend : nouveau client + normalisation + credentials app-level + routes + schema bump *(fait le 2026-07-17, vérifié end-to-end avec données réelles : search, série, épisode, lookup, charts)*
- [x] **Phase 2** — Frontend : suppression écran credentials/quota + onglet recherche épisodes *(fait — `npm run lint` + `npm run build` verts, `grep -rin taddy|podcast-credentials frontend/src` = 0)*
- [x] **Phase 3** — Tests, lint, nettoyage des références résiduelles *(backend fait en Phase 1 ; volet frontend fait avec la Phase 2 — lint vert ; branche compat `episodes: []` du `/search` retirée côté backend, `searchPrompt` i18n passé en "podcasts uniquement" ; `pytest` 1805 passed, contrat Milo-Mac vert)*

---

## 0. Contexte pour un exécuteur qui découvre (à lire en premier)

Milō est un système audio multiroom (Raspberry Pi, FastAPI + Vue). La source **podcast**
(`backend/sources/podcast/`, Famille C « active player ») utilise aujourd'hui l'**API Taddy**
(GraphQL, `api.taddy.org`) pour la **découverte/recherche/métadonnées uniquement**.

**Faits établis lors du repérage (ne pas re-dériver) :**

1. **La lecture audio ne dépend PAS de Taddy.** MPV joue l'`audio_url` = URL d'enclosure RSS
   renvoyée par l'API ([`source.py:208-241`](../backend/sources/podcast/source.py#L208-L241)).
   Podcast Index renvoie ce même `enclosureUrl` → **le player, la progression, la vitesse,
   l'auto-stop, le monitor et la complétion sont intacts.**
2. **Aucun couplage Milo-Mac.** `grep podcast backend/tests/contracts/milo_mac_contract.json`
   = vide. Les routes `/api/podcast/*` et les sous-champs de `metadata` (lus comme dict opaque)
   ne sont pas figés par le contrat → on renomme/retype librement. Seul consommateur : `frontend/`.
3. **Podcast Index exige une clé + un secret** (auth SHA-1 par requête), mais **illimité** et
   **gratuit**. Décision : une **clé applicative unique** partagée par tous les Milō, embarquée
   dans l'app — donc **plus aucune saisie de credentials par l'utilisateur** (c'était la contrainte
   pénible de Taddy) et **plus de quota** (Taddy plafonnait ~500 req/h).
4. **Seule perte fonctionnelle assumée : la recherche d'épisodes cross-podcasts.**
   Podcast Index (comme toute alternative non-Taddy) ne fait pas de recherche fulltext
   d'épisodes → **la recherche devient "podcasts uniquement"**.

**Principe directeur :** garder **le schéma normalisé interne de Milō** (`uuid`, `name`,
`image_url`, `audio_url`, `episodes`, `podcast:{uuid,name,image_url}`, `is_subscribed`,
`playback_progress`, `children_hash`…) comme interface stable. On **ne change que le
fournisseur en dessous**. Le champ `uuid` contiendra désormais un **ID Podcast Index**
(feedId / episodeId, stringifié) au lieu d'un UUID Taddy — traité comme chaîne opaque par les
routes et le frontend, donc `/series/{uuid}`, `/episode/{uuid}`, `/play {episode_uuid}`
continuent de fonctionner sans y toucher.

---

## 1. Décisions verrouillées

| Sujet | Décision |
|---|---|
| Backend de découverte | **Podcast Index** (REST, `https://api.podcastindex.org/api/1.0`) |
| Credentials | **Clé applicative unique embarquée** — plus de saisie par utilisateur |
| Charts (top + par genre) | **iTunes RSS conservé** (ordre Apple exact, keyless), inchangé |
| Recherche | **Podcasts uniquement** (feeds-only) — onglet épisodes retiré |
| Persistance | **Reset via schema bump** (anciens UUID Taddy invalides), pas de shim |

**Stockage de la clé — VERROUILLÉ : `backend/config/constants.py`** (clé + secret en dur,
app-level). Simplicité "single code path", conforme au besoin "une clé embarquée pour tous les
utilisateurs". Caveat assumé : une clé PI extractible est à faible enjeu (gratuite, illimitée,
révocable — rotation si besoin). *(Alternative non retenue : variable d'env.)*

---

## Phase 0 — Prérequis humains

- [x] Créer un compte sur **https://api.podcastindex.org/** et obtenir une **API Key** + **API Secret**.
- [x] Décider du stockage (constants vs env — voir ci-dessus).

---

## Référence Podcast Index (pour ne pas re-fetch la doc)

**Base URL :** `https://api.podcastindex.org/api/1.0`

**Auth — recalculée À CHAQUE requête (fenêtre 3 min) :**
```python
import hashlib, time
now = str(int(time.time()))
auth = hashlib.sha1(f"{API_KEY}{API_SECRET}{now}".encode("utf-8")).hexdigest()
headers = {
    "User-Agent": "Milo/1.0",
    "X-Auth-Key": API_KEY,
    "X-Auth-Date": now,
    "Authorization": auth,
}
```

**Endpoints utilisés** (enveloppe JSON = `{status, feeds|items|feed|episode, count, description}`) :

| Besoin Milō | Endpoint PI | Clé réponse |
|---|---|---|
| Recherche podcasts | `GET /search/byterm?q=&max=&clean=` | `feeds[]` |
| Détail podcast | `GET /podcasts/byfeedid?id=<feedId>` | `feed` |
| Podcast par iTunes ID (lookup charts) | `GET /podcasts/byitunesid?id=<itunesId>` | `feed` |
| Épisodes d'un podcast | `GET /episodes/byfeedid?id=<feedId>&max=&since=` | `items[]` |
| Détail épisode (avant play) | `GET /episodes/byid?id=<episodeId>` | `episode` |
| (option) Trending | `GET /podcasts/trending?max=&lang=&cat=` | `feeds[]` |

> Pas d'endpoint batch multi-feeds : `latest-episodes` des abonnements = N × `/episodes/byfeedid`
> en parallèle (PI illimité, donc OK).
> Pagination : PI utilise `max` (+ curseur `since` epoch), pas `page/offset`. Charger un `max`
> large (ex. 100) et paginer côté client, ou cursoriser par date.

**Mapping champs — SÉRIE (feed PI → clé normalisée Milō) :**
`id`→`uuid` (str) · `title`→`name` · `description` · `artwork`(sinon `image`)→`image_url` ·
`author`→`publisher`/`author` · `episodeCount`→`total_episodes` · `categories`(dict)→`genres`(liste) ·
`language` · `explicit`→`is_explicit` · `url`→`rss_url` · `link`→`website_url` · `itunesId`→`itunes_id` ·
`lastUpdateTime`(ou `newestItemPubdate`)→`children_hash` (token "nouveaux épisodes").
*Abandonnés :* `popularity_rank` (PI n'a pas de rang).

**Mapping champs — ÉPISODE (episode PI → clé normalisée Milō) :**
`id`→`uuid` (str) · `guid` · `title`→`name` · `description` · `datePublished`→`date_published` ·
`duration`(déjà en s)→`duration` · `enclosureUrl`→`audio_url` · `image`(sinon `feedImage`)→`image_url` ·
`enclosureType`→`file_type` · `enclosureLength`→`file_length` · `season`/`episode` · `episodeType`→`episode_type` ·
`explicit`→`is_explicit` · `link`→`website_url` · `feedId`→`podcast.uuid`, `feedTitle`→`podcast.name`,
`feedImage`→`podcast.image_url`. *Abandonnés :* `subtitle`, `video_url`, `is_removed` (absents de PI).
Conserver la garde `_coerce_duration_seconds` (ms>24h) par défense.

---

## Phase 1 — Backend

### WI-1 — Nouveau client
- [x] Créer `backend/sources/podcast/podcastindex_api.py` : classe `PodcastIndexAPI` (async, aiohttp).
  Méthodes : `search_podcasts(term, ...)`, `get_podcast_series(feed_id, ...)` (→ `/podcasts/byfeedid`
  + `/episodes/byfeedid`, 2 appels //), `get_episode(episode_id)` (→ `/episodes/byid`),
  `lookup_by_itunes_id(itunes_id)` (→ `/podcasts/byitunesid`),
  `get_latest_episodes(feed_ids[])` (N × `/episodes/byfeedid` en parallèle),
  et **conserver** `get_itunes_top_podcasts_by_genre` (iTunes RSS, inchangé — déplacer ici tel quel).
  Auth SHA-1 par requête (snippet ci-dessus). Garder les caches TTL et le sentinel `_network_error`.
  **Retirer** `_rate_limited` et tout le quota (`get_api_requests_remaining`).
- [x] **Supprimer** `backend/sources/podcast/taddy_api.py`. Y **conserver** (déplacer) uniquement
  `MILO_LANGUAGE_TO_ITUNES_COUNTRY` et `GENRE_TO_ITUNES_ID` (charts iTunes). Supprimer tous les
  enums/maps Taddy (`MILO_LANGUAGE_TO_TADDY`, `ITUNES_COUNTRY_TO_TADDY_COUNTRY`,
  `map_milo_language_to_taddy*`).

### WI-2 — Normalisation
- [x] `_normalize_podcast_series` / `_normalize_episode` sur les champs PI (tables de mapping
  ci-dessus), **en gardant les clés de sortie Milō identiques**. ID canonique : `feedId` (podcast),
  episode `id` (épisode), **stringifiés**.

### WI-3 — Credentials app-level (cœur du changement)
- [x] Ajouter la clé+secret dans la config app (constants.py **ou** env — voir décision).
- [x] `PodcastSource.__init__` ([`source.py:62-68`](../backend/sources/podcast/source.py#L62-L68)) :
  lire la config app au lieu de `settings_service.get_setting_sync("podcast.taddy_*")` ;
  instancier `PodcastIndexAPI`. Renommer l'attribut `_taddy_api`→`_podcast_api` et la property
  `taddy_api`→`podcast_api` ([`source.py:648-650`](../backend/sources/podcast/source.py#L648-L650)).
  `get_episode` reste appelé en [`source.py:204`](../backend/sources/podcast/source.py#L204).
- [x] **Supprimer `reload_credentials`** ([`source.py:631-640`](../backend/sources/podcast/source.py#L631-L640))
  — plus de hot-reload per-user.
- [x] Retirer `podcast.taddy_user_id`/`taddy_api_key` de
  [`settings.py:67-69`](../backend/core/settings.py#L67-L69) + coercition
  [`settings.py:209-216`](../backend/core/settings.py#L209-L216). *(Note : d'anciens fichiers
  `settings.json` garderont ces clés inertes — pas de reset nécessaire pour settings.json.)*
- [x] Supprimer les 3 routes credentials dans `backend/api/settings.py` :
  `PUT /podcast-credentials` (452), `POST /podcast-credentials/validate` (488),
  `GET /podcast-credentials/status` (537) + l'import `TaddyAPI` (10) + le bloc
  `requests_remaining` (508-527).
- [x] Supprimer `PodcastCredentialsRequest` (`backend/api/models.py:204-208`) et les champs
  `taddy_*` de `backend/api/responses.py:149-150`.
- [x] Supprimer l'event WS `podcast_credentials_changed`
  (`backend/core/models/ws_events.py:325-326`) — **vérifié absent du manifest Milo-Mac** → sûr.

### WI-4 — Routes
- [x] `backend/sources/podcast/routes.py` : retirer les imports de maps Taddy (27-31).
  `/discover/top-charts` (51) et `/discover/by-genre` (91) → s'appuient sur **iTunes RSS**
  (via `get_itunes_top_podcasts_by_genre` / un `get_itunes_top_podcasts` sans genre).
  ⚠️ **top-charts devient podcasts-only** : abandonner `content_type=PODCASTEPISODE`
  (charts d'épisodes indisponibles keyless — vérifier l'usage frontend, probablement à retirer).
  `/lookup/itunes/{itunes_id}` (125) → `source.podcast_api.lookup_by_itunes_id` (renvoie un
  feedId PI au lieu d'un UUID Taddy — même flux). `/subscriptions/latest-episodes` (363) → N appels //.
  Remplacer tous les `source.taddy_api.*` par `source.podcast_api.*`.

### WI-5 — Persistance (fail-loud, obligatoire avec WI-1..4)
- [x] **Bumper `PodcastDataService.SCHEMA_VERSION` 1 → 2**
  ([`data.py:41`](../backend/sources/podcast/data.py#L41)). Au boot, `SchemaVersionMismatch` →
  bannière + `SystemExit(1)` → l'opérateur `rm /var/lib/milo/podcast_data.json`, l'utilisateur
  re-souscrit. **Aucun shim de traduction d'ID.** (Les abonnements/progression étaient clés par
  UUID Taddy, invalides dès qu'on utilise des IDs PI.)

**Critère d'acceptation Phase 1 :** `python -m pytest backend/` passe (après WI-7) ; le backend
démarre ; `GET /api/podcast/search?term=...` renvoie des podcasts ; ouvrir une série renvoie ses
épisodes ; `POST /api/podcast/play {episode_uuid}` lit l'audio.

---

## Phase 2 — Frontend (après Phase 1)

### WI-6 — Nettoyage UI + recherche podcasts-only
- [x] **Supprimer l'écran credentials** `frontend/src/components/settings/categories/PodcastSettings.vue`
  (tout le composant : formulaire user_id/api_key + bloc quota `requestsUsed/500`) et le retirer de
  `SettingsModal.vue` (mount + `refreshPodcastCredentialsStatus` ligne 651).
- [x] **Supprimer le gate** `frontend/src/components/podcasts/CredentialsRequired.vue` et son point
  de montage (grep `CredentialsRequired` dans `frontend/src`) — le podcast est toujours disponible.
- [x] **Retirer l'onglet "épisodes" de la recherche** dans
  `frontend/src/components/podcasts/SearchView.vue` : section épisodes (46-61), `loadMoreEpisodes`
  (263-275), et adapter `setSearchResults(data.podcasts, data.episodes, ...)` (242).
- [x] `frontend/src/stores/podcastStore.js` : trim des branches `episodes` de l'état recherche
  (`searchResults.episodes` 50, `searchPagination.episodes` 54, `searchCurrentPage.episodes` 58),
  `setSearchResults` (404), `appendSearchResults('episodes')` (422-428). **Ne pas toucher** au reste
  (clés `uuid`, `episode_uuid`, `image_url`, `is_subscribed`, `playback_progress`, `children_hash`
  préservées côté backend → aucun changement nécessaire).
- [x] `frontend/src/stores/settingsStore.js` : retirer `podcastCredentials` (57-59), les refs statut
  (63-65), l'hydratation `d.podcast_credentials` (201-203), `updatePodcastCredentials` (303),
  `refreshPodcastCredentialsStatus` (308-317).
- [x] `frontend/src/App.vue` : retirer l'appel `refreshPodcastCredentialsStatus` (355) et le handler
  WS `podcast_credentials_changed` (677-681).
- [x] **i18n** : retirer le bloc `podcastSettings` (298-311) et les clés `podcasts.credentialsError`,
  `.credentialsErrorHint`, `.configureButton`, `.rateLimitError`, `.rateLimitErrorHint` (536-540),
  ainsi que `podcasts.recentEpisodesTitle` / `podcasts.loadMoreEpisodes` si l'onglet épisodes part.
  **Dans les 8 locales** : `english.json` (canonique) + `french/german/spanish/italian/portuguese/chinese/hindi.json`.

**Critère d'acceptation Phase 2 :** `npm run lint` passe ; l'écran Réglages n'affiche plus de
section podcast credentials ; la recherche affiche uniquement des podcasts ; aucun appel réseau
vers `/podcast-credentials`.

---

## Phase 3 — Tests, lint, nettoyage

### WI-7
- [x] *(backend fait — `grep -rin taddy backend/` → 0 ; `frontend/` reste avec la Phase 2)*
  `grep -rin taddy backend/ frontend/` → 0 résultat résiduel (imports, docstrings, symboles).
  Points connus à nettoyer : `backend/sources/podcast/__init__.py:3` (docstring),
  docstrings dans `source.py` (6,13,61,477,520) et `data.py`.
- [x] Adapter/retirer les tests : `backend/tests/test_podcast_source.py` (patche
  `...source.TaddyAPI`, assert `_taddy_api` — lignes 29-30, 81-99, 115, 138, 159-160, 186-187),
  `backend/tests/test_routes_settings.py` (370-371, 379),
  `backend/tests/contracts/test_response_models.py:248` (fixture `podcast_credentials`).
- [x] Ajouter des tests `_normalize_podcast_series` / `_normalize_episode` sur des payloads PI réels.
  *(→ `backend/tests/test_podcastindex_api.py`, payloads calqués sur les schémas OpenAPI officiels —
  l'appel live était bloqué par le secret invalide, voir la note en tête.)*
- [x] *(backend fait — `python -m pytest backend/` : 1804 passed, contrat Milo-Mac vert ;
  `npm run lint` à repasser avec la Phase 2)*
  Vérifier `python -m pytest backend/` (le contrat Milo-Mac doit rester vert — podcast absent
  du manifest, donc aucune action, mais pytest le confirme) + `npm run lint`.

---

## Deltas de comportement (à assumer, documentés)

- **Recherche = podcasts uniquement** (perte assumée).
- **top-charts = podcasts uniquement** (plus de charts d'épisodes).
- **Pagination** back-catalogue : `page` → `max`/curseur `since`.
- **Tri épisodes** : `OLDEST` = reverse client ; `SEARCH` = supprimé.
- **Nouveaux épisodes** : détectés via `lastUpdateTime`/`newestItemPubdate` (stocké dans `children_hash`).

## Ce qui NE change PAS

Player MPV, progression, vitesse, auto-stop, monitor, complétion ; charts Apple (iTunes RSS) ;
contrat Milo-Mac ; le schéma normalisé consommé par le frontend.
