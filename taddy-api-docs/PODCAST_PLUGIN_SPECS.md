# Plugin Podcasts Milo - Specifications Completes via Taddy API

Document de reference pour le design Figma et le developpement du plugin Podcasts.

---

## TABLE DES MATIERES

1. [Architecture des Vues](#1-architecture-des-vues)
2. [Queries GraphQL Disponibles](#2-queries-graphql-disponibles)
3. [Modeles de Donnees Complets](#3-modeles-de-donnees-complets)
4. [Fonctionnalites par Vue](#4-fonctionnalites-par-vue)
5. [Donnees Locales a Persister](#5-donnees-locales-a-persister)
6. [Contraintes et Limitations](#6-contraintes-et-limitations)
7. [Specifications Techniques](#7-specifications-techniques)
8. [Priorites de Developpement](#8-priorites-de-developpement)

---

## 1. ARCHITECTURE DES VUES

### Vues Principales (Navigation Tabs)
1. **Accueil** (PodcastSource.vue) - Decouverte sans recherche
2. **Abonnements** (PodcastSubscriptions.vue) - Podcasts suivis
3. **Recherche** (PodcastSearch.vue) - Recherche avancee avec filtres
4. **File d'attente** (PodcastQueue.vue) - Episodes en cours/non termines

### Sous-vues (Modales/Ecrans Secondaires)
- **Details Podcast** (PodcastDetails.vue) - Informations completes + liste episodes
- **Details Episode** (EpisodeDetails.vue) - Description complete

### Composant Global
- **Lecteur** (PodcastPlayer.vue) - Panel lateral coulissant, visible sur toutes les vues pendant la lecture

### Composants Reutilisables
- **PodcastCard.vue** - Carte pour afficher un podcast (pochette, titre, editeur)
- **EpisodeCard.vue** - Carte pour afficher un episode (pochette, titre, duree, date)

---

## 2. QUERIES GRAPHQL DISPONIBLES

### 2.1 `getPopularContent` - Podcasts Populaires Globaux

**Utilisation :** Vue Accueil - "Podcasts populaires", "Populaires en francais"

```graphql
{
  getPopularContent(
    taddyType: PODCASTSERIES
    filterByLanguage: FRENCH
    filterByGenres: [PODCASTSERIES_TECHNOLOGY]
    page: 1
    limitPerPage: 25
  ) {
    popularityRankId
    podcastSeries {
      uuid
      name
      description(shouldStripHtmlTags: true)
      imageUrl
      totalEpisodesCount
      genres
      language
      popularityRank
      isExplicitContent
      itunesInfo {
        baseArtworkUrlOf(size: 300)
        publisherName
      }
    }
  }
}
```

**Parametres :**
- `taddyType` - PODCASTSERIES (seul type supporte pour l'instant)
- `filterByLanguage` - Optionnel : filtrer par langue
- `filterByGenres` - Optionnel : tableau de genres
- `page` - 1 a 20 max
- `limitPerPage` - 1 a 25 max

---

### 2.2 `getTopChartsByCountry` - Top Charts par Pays

**Utilisation :** Vue Accueil - "Top France", "Top USA"

```graphql
{
  getTopChartsByCountry(
    taddyType: PODCASTSERIES
    country: FRANCE
    source: APPLE_PODCASTS
    page: 1
    limitPerPage: 25
  ) {
    topChartsId
    podcastSeries {
      uuid
      name
      imageUrl
      popularityRank
      genres
      totalEpisodesCount
      itunesInfo {
        baseArtworkUrlOf(size: 300)
        publisherName
      }
    }
  }
}
```

**Parametres :**
- `taddyType` - PODCASTSERIES ou PODCASTEPISODE (requis)
- `country` - Pays (requis) : FRANCE, UNITED_STATES_OF_AMERICA, etc.
- `source` - APPLE_PODCASTS (defaut, seule option)
- `page` - 1 a 20 max
- `limitPerPage` - 1 a 25 max

**Note :** Mis a jour quotidiennement (~6h PST)

---

### 2.3 `getTopChartsByGenres` - Top Charts par Genre

**Utilisation :** Vue Accueil - "Top True Crime", "Populaires par genre"

```graphql
{
  getTopChartsByGenres(
    taddyType: PODCASTSERIES
    genres: [PODCASTSERIES_TRUE_CRIME, PODCASTSERIES_TECHNOLOGY]
    source: APPLE_PODCASTS
    filterByCountry: FRANCE
    page: 1
    limitPerPage: 25
  ) {
    topChartsId
    podcastSeries {
      uuid
      name
      imageUrl
      genres
      popularityRank
    }
  }
}
```

**Parametres :**
- `taddyType` - PODCASTSERIES ou PODCASTEPISODE (requis)
- `genres` - Tableau de genres (requis)
- `source` - APPLE_PODCASTS (defaut)
- `filterByCountry` - Optionnel, surtout pour episodes
- `page` - 1 a 20 max
- `limitPerPage` - 1 a 25 max

---

### 2.4 `getPodcastSeries` - Details Podcast + Episodes

**Utilisation :** Vue Details Podcast, rafraichissement abonnements

```graphql
{
  getPodcastSeries(uuid: "podcast-uuid-here") {
    uuid
    name
    description(shouldStripHtmlTags: true)
    descriptionLinks
    imageUrl
    authorName
    totalEpisodesCount
    genres
    language
    contentType
    seriesType
    isCompleted
    isExplicitContent
    popularityRank
    websiteUrl
    rssUrl
    hash
    childrenHash
    itunesInfo {
      baseArtworkUrlOf(size: 640)
      publisherName
      country
      subtitle
      summary
    }
    persons {
      name
      role
      imageUrl
      url
    }
    episodes(
      sortOrder: LATEST
      page: 1
      limitPerPage: 25
    ) {
      uuid
      name
      description(shouldStripHtmlTags: true)
      datePublished
      duration
      audioUrl
      imageUrl
      episodeType
      seasonNumber
      episodeNumber
      isExplicitContent
      websiteUrl
    }
  }
}
```

**Parametres episodes :**
- `sortOrder` - LATEST (recent d'abord), OLDEST (ancien d'abord), SEARCH
- `page` - 1 a 1000 max (!)
- `limitPerPage` - 1 a 25 max
- `searchTerm` - Si sortOrder=SEARCH, filtre titre/description
- `includeRemovedEpisodes` - false par defaut

---

### 2.5 `getPodcastEpisode` - Details Episode Complet

**Utilisation :** Vue Details Episode

```graphql
{
  getPodcastEpisode(uuid: "episode-uuid-here") {
    uuid
    guid
    name
    description(shouldStripHtmlTags: true)
    descriptionLinks
    subtitle
    audioUrl
    videoUrl
    duration
    fileLength
    fileType
    imageUrl
    datePublished
    episodeType
    seasonNumber
    episodeNumber
    isExplicitContent
    isRemoved
    websiteUrl
    hash
    podcastSeries {
      uuid
      name
      imageUrl
    }
    persons {
      name
      role
      imageUrl
    }
  }
}
```

---

### 2.6 `getMultiplePodcastSeries` - Batch Podcasts

**Utilisation :** Charger abonnements en batch

```graphql
{
  getMultiplePodcastSeries(uuids: ["uuid1", "uuid2", "uuid3"]) {
    uuid
    name
    imageUrl
    totalEpisodesCount
    childrenHash
    itunesInfo {
      publisherName
    }
  }
}
```

**Limite :** Max 25 UUIDs par requete

---

### 2.7 `getMultiplePodcastEpisodes` - Batch Episodes

**Utilisation :** Charger episodes file d'attente

```graphql
{
  getMultiplePodcastEpisodes(uuids: ["ep-uuid1", "ep-uuid2"]) {
    uuid
    name
    duration
    audioUrl
    imageUrl
    datePublished
    episodeType
    podcastSeries {
      uuid
      name
      imageUrl
    }
  }
}
```

**Limite :** Max 25 UUIDs par requete

---

### 2.8 `getLatestPodcastEpisodes` - Derniers Episodes Multi-Podcasts

**Utilisation :** Vue Abonnements - "Nouveaux episodes" de tous les podcasts suivis

```graphql
{
  getLatestPodcastEpisodes(
    uuids: ["podcast-uuid1", "podcast-uuid2", "podcast-uuid3"]
    page: 1
    limitPerPage: 50
  ) {
    uuid
    name
    description(shouldStripHtmlTags: true)
    datePublished
    duration
    audioUrl
    imageUrl
    episodeType
    podcastSeries {
      uuid
      name
      imageUrl
    }
  }
}
```

**Parametres :**
- `uuids` - Max 1000 UUIDs de podcasts (!)
- `rssUrls` - Alternative : Max 1000 URLs RSS
- `page` - 1 a 20 max
- `limitPerPage` - 1 a 50 max (plus genereux que autres queries!)

**Note :** Retourne les episodes tries par date de publication (plus recent en premier)

---

### 2.9 `search` - Recherche Avancee

**Utilisation :** Vue Recherche avec tous les filtres

```graphql
{
  search(
    term: "technologie"
    filterForTypes: [PODCASTSERIES]
    filterForGenres: [PODCASTSERIES_TECHNOLOGY]
    filterForLanguages: [FRENCH]
    filterForCountries: [FRANCE]
    filterForPodcastContentType: [AUDIO]
    sortBy: POPULARITY
    matchBy: MOST_TERMS
    isSafeMode: false
    page: 1
    limitPerPage: 25
  ) {
    searchId
    podcastSeries {
      uuid
      name
      description(shouldStripHtmlTags: true)
      imageUrl
      totalEpisodesCount
      genres
      language
      popularityRank
      isExplicitContent
      itunesInfo {
        baseArtworkUrlOf(size: 300)
        publisherName
      }
    }
    rankingDetails {
      uuid
      rankingScore
      type
    }
    responseDetails {
      totalCount
      pagesCount
      type
    }
  }
}
```

**Pour recherche d'episodes :**

```graphql
{
  search(
    term: "interview"
    filterForTypes: [PODCASTEPISODE]
    filterForDurationLessThan: 1800
    filterForDurationGreaterThan: 600
    filterForPublishedAfter: 1700000000
    filterForHasTranscript: false
    sortBy: POPULARITY
    page: 1
    limitPerPage: 25
  ) {
    searchId
    podcastEpisodes {
      uuid
      name
      description(shouldStripHtmlTags: true)
      duration
      audioUrl
      imageUrl
      datePublished
      episodeType
      podcastSeries {
        uuid
        name
        imageUrl
      }
    }
    responseDetails {
      totalCount
      pagesCount
      type
    }
  }
}
```

**Parametres complets :**
- `term` - Requis : terme de recherche
- `filterForTypes` - PODCASTSERIES ou PODCASTEPISODE (tableau)
- `filterForGenres` - Tableau de genres
- `filterForLanguages` - Tableau de langues
- `filterForCountries` - Tableau de pays
- `filterForPodcastContentType` - AUDIO ou VIDEO
- `sortBy` - EXACTNESS (defaut) ou POPULARITY
- `matchBy` - MOST_TERMS (defaut), ALL_TERMS, EXACT_PHRASE
- `isSafeMode` - true pour exclure contenu explicite

**Filtres episodes uniquement :**
- `filterForDurationLessThan` - En secondes
- `filterForDurationGreaterThan` - En secondes
- `filterForPublishedAfter` - Epoch timestamp (secondes)
- `filterForPublishedBefore` - Epoch timestamp (secondes)
- `filterForHasTranscript` - boolean

**Filtres podcasts uniquement :**
- `filterForLastUpdatedAfter` - Epoch timestamp
- `filterForLastUpdatedBefore` - Epoch timestamp
- `filterForTotalEpisodesLessThan` - Nombre episodes
- `filterForTotalEpisodesGreaterThan` - Nombre episodes

**Pagination :**
- `page` - 1 a 20 max
- `limitPerPage` - 1 a 25 max
- Total max : 500 resultats (20 pages x 25 resultats)

**Astuces recherche :**
- Exclusion avec `-` : "technologie -crypto"
- Phrase exacte avec matchBy: EXACT_PHRASE

---

### 2.10 `getApiRequestsRemaining` - Quota API

**Utilisation :** Monitoring, affichage warning si proche limite

```graphql
{
  getApiRequestsRemaining
}
```

Retourne un entier (nombre de requetes restantes sur l'heure)

---

## 3. MODELES DE DONNEES COMPLETS

### 3.1 PodcastSeries

```typescript
interface PodcastSeries {
  // Identifiants
  uuid: string                          // Identifiant unique Taddy
  itunesId: number                      // ID Apple Podcasts

  // Informations principales
  name: string                          // Titre
  description: string                   // Description (HTML ou texte brut)
  descriptionLinks: string[]            // URLs extraites de la description
  authorName: string                    // Nom du createur

  // Images
  imageUrl: string                      // Pochette principale
  itunesInfo: {
    baseArtworkUrlOf(size: number): string  // Image haute resolution (300, 640, 1400)
    publisherName: string               // Editeur
    country: Country                    // Pays
    subtitle: string
    summary: string
    hash: string
    uuid: string
  }

  // Metadonnees
  datePublished: number                 // Epoch timestamp (secondes)
  totalEpisodesCount: number            // Nombre d'episodes
  genres: Genre[]                       // Max 5 genres
  language: Language                    // Langue parlee
  contentType: 'AUDIO' | 'VIDEO'        // Type de contenu
  seriesType: 'EPISODIC' | 'SERIAL'     // Episodes independants vs ordre important

  // Flags
  isCompleted: boolean                  // Serie terminee
  isExplicitContent: boolean            // Contenu explicite
  isBlocked: boolean                    // Bloque par Taddy

  // Popularite
  popularityRank: PopularityRank | null // TOP_200 a TOP_200000

  // Liens externes
  websiteUrl: string                    // Site web
  rssUrl: string                        // Flux RSS
  rssOwnerName: string
  rssOwnerPublicEmail: string
  copyright: string

  // Personnes
  persons: Person[]                     // Hosts, guests, etc.

  // Hashes (detection de changements)
  hash: string                          // Hash des details podcast
  childrenHash: string                  // Hash des episodes (change si nouveaux episodes)

  // Feed refresh
  feedRefreshDetails: {
    dateLastRefreshed: number
    priority: 'HIGH' | 'MEDIUM' | 'LOW' | 'INACTIVE' | 'NEVER'
    priorityReason: string
  }

  // Episodes (avec pagination)
  episodes: PodcastEpisode[]
}
```

### 3.2 PodcastEpisode

```typescript
interface PodcastEpisode {
  // Identifiants
  uuid: string
  guid: string                          // GUID RSS

  // Informations principales
  name: string                          // Titre
  description: string                   // Description complete
  descriptionLinks: string[]            // URLs extraites
  subtitle: string                      // Max 255 caracteres

  // Media
  audioUrl: string                      // URL fichier audio
  videoUrl: string                      // URL video (si podcast video)
  duration: number                      // Duree en SECONDES
  fileLength: number                    // Taille fichier en bytes
  fileType: string                      // MIME type (audio/mpeg, etc.)

  // Images
  imageUrl: string                      // Pochette episode (ou podcast si null)

  // Metadonnees
  datePublished: number                 // Epoch timestamp (secondes)
  episodeType: 'FULL' | 'TRAILER' | 'BONUS'
  seasonNumber: number | null
  episodeNumber: number | null

  // Flags
  isExplicitContent: boolean
  isRemoved: boolean                    // Retire du RSS
  isBlocked: boolean

  // Liens
  websiteUrl: string                    // Page web de l'episode

  // Parent
  podcastSeries: PodcastSeries

  // Personnes (specifiques a l'episode)
  persons: Person[]

  // Hash
  hash: string
}
```

### 3.3 Person

```typescript
interface Person {
  uuid: string
  name: string
  role: ContentRole
  imageUrl: string
  url: string
}
```

**Roles disponibles (60+) :**
- PODCASTSERIES_HOST
- PODCASTSERIES_CO_HOST
- PODCASTSERIES_GUEST
- PODCASTSERIES_PRODUCER
- PODCASTSERIES_NARRATOR
- PODCASTSERIES_EDITOR
- PODCASTSERIES_AUTHOR
- PODCASTSERIES_SOUND_DESIGNER
- ... et beaucoup d'autres

### 3.4 PopularityRank

```typescript
type PopularityRank =
  | 'TOP_200'
  | 'TOP_1000'
  | 'TOP_2000'
  | 'TOP_3000'
  | 'TOP_4000'
  | 'TOP_5000'
  | 'TOP_10000'
  | 'TOP_20000'
  | 'TOP_50000'
  | 'TOP_100000'
  | 'TOP_200000'
  | null
```

### 3.5 Genres (124 disponibles)

**Categories principales :**
- **Arts** (7) : PODCASTSERIES_ARTS, PODCASTSERIES_ARTS_BOOKS, PODCASTSERIES_ARTS_DESIGN, ...
- **Business** (7) : PODCASTSERIES_BUSINESS, PODCASTSERIES_BUSINESS_CAREERS, ...
- **Comedie** (4) : PODCASTSERIES_COMEDY, PODCASTSERIES_COMEDY_STANDUP, ...
- **Education** (5) : PODCASTSERIES_EDUCATION, PODCASTSERIES_EDUCATION_COURSES, ...
- **Fiction** (4) : PODCASTSERIES_FICTION, PODCASTSERIES_FICTION_DRAMA, ...
- **Gouvernement** (1) : PODCASTSERIES_GOVERNMENT
- **Sante et forme** (7) : PODCASTSERIES_HEALTH_AND_FITNESS, ...
- **Histoire** (1) : PODCASTSERIES_HISTORY
- **Enfants et famille** (5) : PODCASTSERIES_KIDS_AND_FAMILY, ...
- **Loisirs** (9) : PODCASTSERIES_LEISURE, PODCASTSERIES_LEISURE_VIDEO_GAMES, ...
- **Musique** (4) : PODCASTSERIES_MUSIC, ...
- **Actualites** (8) : PODCASTSERIES_NEWS, PODCASTSERIES_NEWS_POLITICS, ...
- **Religion et spiritualite** (8) : PODCASTSERIES_RELIGION_AND_SPIRITUALITY, ...
- **Science** (5) : PODCASTSERIES_SCIENCE, ...
- **Societe et culture** (7) : PODCASTSERIES_SOCIETY_AND_CULTURE, ...
- **Sports** (19) : PODCASTSERIES_SPORTS, PODCASTSERIES_SPORTS_SOCCER, ...
- **Technologie** (1) : PODCASTSERIES_TECHNOLOGY
- **True Crime** (1) : PODCASTSERIES_TRUE_CRIME
- **TV et cinema** (4) : PODCASTSERIES_TV_AND_FILM, ...

Fichier complet disponible : `/frontend/src/constants/podcast_genres.js`

### 3.6 Countries (264 disponibles)

Exemples : FRANCE, UNITED_STATES_OF_AMERICA, CANADA, GERMANY, SPAIN, ITALY, etc.

### 3.7 Languages (197 disponibles)

Exemples : FRENCH, ENGLISH, SPANISH, GERMAN, ITALIAN, PORTUGUESE, etc.

---

## 4. FONCTIONNALITES PAR VUE

### 4.1 VUE ACCUEIL (PodcastSource.vue)

**Sections suggerees :**
1. **Top France** - `getTopChartsByCountry(country: FRANCE, taddyType: PODCASTSERIES)`
2. **Episodes populaires** - `getTopChartsByCountry(country: FRANCE, taddyType: PODCASTEPISODE)`
3. **Parcourir par genre** - Grille de genres cliquables -> `getPopularContent(filterByLanguage: <langue_settings>, filterByGenres: [GENRE])` (filtre automatiquement dans la langue de l'utilisateur)
4. **Populaires globalement** - `getPopularContent(filterByLanguage: <langue_settings>)`
5. **Continue Listening** - Donnees locales + `getMultiplePodcastEpisodes`

**Note importante :** La langue utilisee pour les filtres est celle definie dans `settings.json` (ex: FRENCH). Cela evite d'afficher des podcasts dans des langues non comprises par l'utilisateur.

**Donnees affichees (PodcastCard) :**
- Pochette (imageUrl ou itunesInfo.baseArtworkUrlOf(300))
- Titre (name)
- Editeur (itunesInfo.publisherName ou authorName)
- Nombre d'episodes (totalEpisodesCount)

**Donnees affichees (EpisodeCard) :**
- Pochette episode (imageUrl ou podcastSeries.imageUrl)
- Titre episode (name)
- Nom podcast (podcastSeries.name)
- Duree formatee (duration -> "1h 23min")
- Date publication (datePublished -> "il y a 3 jours")
- Barre de progression si en cours

### 4.2 VUE ABONNEMENTS (PodcastSubscriptions.vue)

**Sections :**

1. **Nouveaux episodes** - `getLatestPodcastEpisodes(uuids: subscriptions)`
   - Tries par date publication
   - Badge "Nouveau" si < 7 jours
   - Indicateur si deja ecoute/en cours

2. **Mes podcasts** - `getMultiplePodcastSeries(uuids: subscriptions)`
   - Grille ou liste de podcasts
   - Tri : Alphabetique, Recent, Nouveaux episodes
   - Action : Desabonner (swipe ou menu)
   - Badge "X nouveaux" si childrenHash different

**Detection nouveautes :**
- Sauvegarder `childrenHash` a chaque fetch
- Comparer avec hash precedent pour detecter changements
- Afficher badge "X nouveaux" si different

### 4.3 VUE RECHERCHE (PodcastSearch.vue)

**Interface :**

1. **Barre de recherche** (term)
2. **Resultats mixtes** : Une seule requete retourne Podcasts ET Episodes simultanement
3. **Filtres depliables :**
   - Genre (filterForGenres) - Multi-select
   - Langue (filterForLanguages) - Par defaut : langue de settings.json
   - Pays (filterForCountries)
   - Duree (pour episodes) :
     - "< 15 min" -> filterForDurationLessThan: 900
     - "15-30 min" -> both filters
     - "30-60 min"
     - "> 1h" -> filterForDurationGreaterThan: 3600
   - Safe Mode (isSafeMode)
   - Tri (sortBy) : Pertinence / Popularite

4. **Affichage des resultats** :
   - Section "Podcasts" (ex: top 5-10 resultats de podcastSeries)
   - Section "Episodes" (ex: top 10-15 resultats de podcastEpisodes)
   - Bouton "Voir plus de podcasts" si totalCount > affichage
   - Bouton "Voir plus d'episodes" si totalCount > affichage
   - Infinite scroll optionnel par section

5. **Requete unique avec resultats mixtes :**
   ```graphql
   search(
     term: "..."
     filterForTypes: [PODCASTSERIES, PODCASTEPISODE]  # Les deux types !
     filterForLanguages: [<langue_settings>]
     ...
   ) {
     podcastSeries { ... }      # Liste podcasts
     podcastEpisodes { ... }    # Liste episodes (separee)
     responseDetails {
       type         # Indique PODCASTSERIES ou PODCASTEPISODE
       totalCount   # Nombre total pour ce type
       pagesCount   # Pages disponibles pour ce type
     }
   }
   ```

**Astuce recherche :**
- Exclusion avec `-` : "technologie -crypto"
- Phrase exacte avec matchBy: EXACT_PHRASE

### 4.4 VUE FILE D'ATTENTE (PodcastQueue.vue)

**Donnees sources :**
- Episodes avec playback_progress sauvegarde
- Position > 0 ET position < (duration - 30)

**Affichage (EpisodeCard avec progression) :**
- Pochette
- Titre episode + Podcast
- Barre de progression mini (position/duration)
- Temps restant formate ("23 min restantes")
- Date derniere ecoute
- Actions : Play (reprend), Marquer termine, Supprimer

**Tri :**
- Par date derniere ecoute (defaut)
- Par temps restant
- Par podcast

### 4.5 DETAILS PODCAST (PodcastDetails.vue)

**Header :**
- Pochette large (itunesInfo.baseArtworkUrlOf(640))
- Titre
- Editeur/Auteur
- Badges : Termine (isCompleted)
- Genres (tags)
- Langue
- Type (SERIAL -> "Serie" / EPISODIC -> "Episodique")
- Bouton **S'abonner / Se desabonner**

**Section Infos :**
- Description complete (description avec shouldStripHtmlTags: true)
- Liens extraits (descriptionLinks)
- Site web (websiteUrl)
- Hosts/Personnes (persons avec roles)

**Liste Episodes :**
- Tri : Recent (LATEST) / Ancien (OLDEST) / Recherche (SEARCH)
- Recherche dans episodes (searchTerm)
- Infinite scroll (page, limitPerPage: 25, max 1000 pages)
- Indicateurs : En cours (barre), Ecoute (check)
- Clic sur episode -> EpisodeDetails ou lecture directe

### 4.6 DETAILS EPISODE (EpisodeDetails.vue)

**Informations :**
- Pochette
- Titre + Sous-titre
- Podcast parent (lien vers PodcastDetails)
- Date publication formatee
- Duree formatee
- Type (FULL/TRAILER/BONUS)
- Saison X Episode Y (si disponibles)

**Description :**
- Description complete
- Liens extraits

**Actions :**
- Play / Resume (si en cours)
- Ajouter a la file d'attente
- Partager (websiteUrl)

### 4.7 LECTEUR (PodcastPlayer.vue - Panel Lateral)

**Comportement :**
- Panel lateral coulissant (droite ou gauche)
- S'ouvre automatiquement quand lecture demarre
- Peut etre ferme manuellement
- Reste ouvert si lecture active

**Contenu :**
- Pochette large
- Titre episode complet
- Nom podcast (cliquable vers PodcastDetails)
- Barre de progression interactive (seek via clic/drag)
- Temps actuel / Duree totale

**Controles :**
- -15 secondes (rewind)
- Play/Pause
- +30 secondes (forward)
- Vitesse de lecture : 0.5x, 0.75x, 1x, 1.25x, 1.5x, 2x (mpv supporte)

**Actions additionnelles :**
- Fermer le panel
- Aller a la page episode
- Aller a la page podcast

**Comportement technique :**
- Sauvegarde position toutes les 10 secondes
- Reprise automatique a derniere position
- Detection fin episode (marquer comme termine)
- Broadcast WebSocket etat lecture vers frontend

---

## 5. DONNEES LOCALES A PERSISTER

### 5.1 Structure `/var/lib/milo/podcast_data.json`

```json
{
  "subscriptions": [
    {
      "uuid": "podcast-uuid-1",
      "name": "Podcast Name",
      "imageUrl": "https://...",
      "childrenHash": "abc123",
      "addedAt": 1700000000,
      "lastChecked": 1700100000
    }
  ],

  "playback_progress": {
    "episode-uuid-1": {
      "position": 1234,
      "duration": 3600,
      "lastPlayed": 1700000000,
      "completed": false,
      "podcastUuid": "podcast-uuid-1",
      "episodeName": "Episode Name",
      "podcastName": "Podcast Name",
      "imageUrl": "https://..."
    }
  },

  "cache": {
    "episodes": {
      "episode-uuid": {
        "data": { /* PodcastEpisode complet */ },
        "cachedAt": 1700000000
      }
    },
    "podcasts": {
      "podcast-uuid": {
        "data": { /* PodcastSeries complet */ },
        "cachedAt": 1700000000
      }
    }
  },

  "settings": {
    "defaultCountry": "FRANCE",
    "defaultLanguage": "FRENCH",
    "safeMode": false,
    "playbackSpeed": 1.0
  }
}
```

### 5.2 Operations CRUD

**Abonnements :**
```python
async def subscribe(podcast_uuid: str, metadata: dict) -> bool
async def unsubscribe(podcast_uuid: str) -> bool
async def get_subscriptions() -> List[dict]
async def is_subscribed(podcast_uuid: str) -> bool
async def update_subscription_hash(podcast_uuid: str, new_hash: str) -> bool
```

**Progression :**
```python
async def save_progress(episode_uuid: str, position: int, duration: int, metadata: dict) -> bool
async def get_progress(episode_uuid: str) -> Optional[dict]
async def mark_as_completed(episode_uuid: str) -> bool
async def clear_progress(episode_uuid: str) -> bool
async def get_in_progress_episodes() -> List[dict]
```

**Cache :**
```python
async def cache_episode(episode_uuid: str, data: dict) -> bool
async def get_cached_episode(episode_uuid: str) -> Optional[dict]
async def cache_podcast(podcast_uuid: str, data: dict) -> bool
async def get_cached_podcast(podcast_uuid: str) -> Optional[dict]
async def clean_old_cache(max_age_seconds: int = 3600) -> int
```

**Settings :**
```python
async def get_podcast_settings() -> dict
async def update_podcast_settings(settings: dict) -> bool
```

---

## 6. CONTRAINTES ET LIMITATIONS

### 6.1 Taddy API

- **Rate Limit** : 500 requetes/mois (HTTP 429 si depasse)
- **Pagination** : Max 25 resultats/page, max 20 pages (500 resultats)
- **Episodes podcast** : Max 1000 pages (25000 episodes)
- **Batch queries** : Max 25 UUIDs
- **getLatestPodcastEpisodes** : Max 1000 podcasts, 50 episodes/page
- **Top Charts** : Mis a jour quotidiennement (~6h PST), source Apple Podcasts uniquement

### 6.2 Non Disponible via API

- Recommandations personnalisees (pas d'endpoint)
- Notes/Avis utilisateurs
- Commentaires sociaux
- Statistiques d'ecoute globales

### 6.3 A Implementer Localement

- File d'attente / Continue Listening
- Historique d'ecoute
- Sleep timer
- Notifications nouveaux episodes (via childrenHash)

---

## 7. SPECIFICATIONS TECHNIQUES

### 7.1 Backend (FastAPI)

**Routes API a creer :**

```
# Decouverte
GET  /api/podcast/discover/popular
     params: language?, genres[]?, page?, limit?
GET  /api/podcast/discover/top-charts/country/{country}
     params: type (series|episodes), page?, limit?
GET  /api/podcast/discover/top-charts/genres
     params: genres[], country?, page?, limit?

# Recherche
GET  /api/podcast/search
     params: term, type (series|episodes), genres[]?, languages[]?,
             countries[]?, duration_min?, duration_max?, safe_mode?,
             sort_by?, page?, limit?

# Podcasts
GET  /api/podcast/series/{uuid}
GET  /api/podcast/series/{uuid}/episodes
     params: sort (latest|oldest|search), search_term?, page?, limit?
GET  /api/podcast/episode/{uuid}

# Abonnements
GET  /api/podcast/subscriptions
POST /api/podcast/subscriptions
     body: { uuid, name, imageUrl }
DELETE /api/podcast/subscriptions/{uuid}
GET  /api/podcast/subscriptions/latest-episodes
     params: page?, limit?

# File d'attente
GET  /api/podcast/queue
POST /api/podcast/queue/{episode_uuid}/complete
DELETE /api/podcast/queue/{episode_uuid}

# Lecture
POST /api/podcast/play
     body: { episode_uuid, position? }
POST /api/podcast/pause
POST /api/podcast/resume
POST /api/podcast/seek
     body: { position }
POST /api/podcast/stop
POST /api/podcast/speed
     body: { speed }
GET  /api/podcast/status

# Progression
GET  /api/podcast/progress/{episode_uuid}
POST /api/podcast/progress/{episode_uuid}
     body: { position, duration }

# Monitoring
GET  /api/podcast/api-quota
```

**Services necessaires :**

1. **TaddyApiClient** (`backend/infrastructure/plugins/podcast/taddy_api.py`)
   - Wrapper GraphQL avec cache memoire
   - Gestion rate limiting (429)
   - Retry logic
   - Cache TTL configurable

2. **PodcastDataService** (`backend/infrastructure/services/podcast_data_service.py`)
   - Persistence JSON (abonnements, progression)
   - Lecture/ecriture atomique
   - File locks

3. **PodcastPlugin** (`backend/infrastructure/plugins/podcast/plugin.py`)
   - Etend UnifiedAudioPlugin
   - Gestion etats (INACTIVE, READY, CONNECTED)
   - Integration mpv
   - Broadcasting WebSocket

4. **MpvController** (reutiliser celui de radio plugin)
   - Control mpv pour lecture audio
   - Seek, pause, resume, speed
   - Progress tracking

### 7.2 Frontend (Vue 3)

**Store Pinia (podcastStore.js) :**

```javascript
import { defineStore } from 'pinia'

export const usePodcastStore = defineStore('podcast', {
  state: () => ({
    // Lecture
    currentEpisode: null,
    playbackState: 'stopped', // 'playing', 'paused', 'stopped'
    currentPosition: 0,
    currentDuration: 0,
    playbackSpeed: 1.0,
    playerPanelOpen: false,

    // Donnees
    subscriptions: [],
    inProgressEpisodes: [],

    // Recherche
    searchResults: {
      podcasts: [],
      episodes: []
    },
    searchFilters: {
      term: '',
      // Pas de 'type' - recherche mixte (podcasts + episodes simultanement)
      genres: [],
      languages: [],  // Par defaut: settings.defaultLanguage
      countries: [],
      durationMin: null,
      durationMax: null,
      safeMode: false,
      sortBy: 'exactness'
    },
    searchPagination: {
      podcasts: { page: 1, totalPages: 0, totalCount: 0 },
      episodes: { page: 1, totalPages: 0, totalCount: 0 },
      loading: false
    },

    // Cache local
    podcastCache: new Map(),
    episodeCache: new Map(),

    // Settings
    settings: {
      defaultCountry: 'FRANCE',
      defaultLanguage: 'FRENCH',
      safeMode: false,
      playbackSpeed: 1.0
    }
  }),

  getters: {
    isPlaying: (state) => state.playbackState === 'playing',
    isPaused: (state) => state.playbackState === 'paused',
    hasCurrentEpisode: (state) => state.currentEpisode !== null,
    progressPercentage: (state) => {
      if (!state.currentDuration) return 0
      return (state.currentPosition / state.currentDuration) * 100
    }
  },

  actions: {
    // Lecture
    async play(episodeUuid, resumePosition = null) {},
    async pause() {},
    async resume() {},
    async seek(position) {},
    async stop() {},
    async setSpeed(speed) {},
    openPlayerPanel() { this.playerPanelOpen = true },
    closePlayerPanel() { this.playerPanelOpen = false },

    // Abonnements
    async loadSubscriptions() {},
    async subscribe(podcastUuid, metadata) {},
    async unsubscribe(podcastUuid) {},
    isSubscribed(podcastUuid) {},
    async loadLatestEpisodesForSubscriptions() {},

    // File d'attente
    async loadInProgressEpisodes() {},
    async markAsCompleted(episodeUuid) {},
    async removeFromQueue(episodeUuid) {},

    // Recherche
    async search() {},
    async loadMoreResults() {},
    clearSearch() {},
    updateSearchFilters(filters) {},

    // Details
    async fetchPodcastDetails(uuid) {},
    async fetchEpisodeDetails(uuid) {},
    async fetchPodcastEpisodes(uuid, page, sortOrder, searchTerm) {},

    // WebSocket handlers
    handleStateUpdate(data) {},
    handleProgressUpdate(data) {},

    // Settings
    async loadSettings() {},
    async updateSettings(settings) {}
  }
})
```

**Composants Vue :**

```
frontend/src/components/audio/
├── PodcastSource.vue          # Vue principale Accueil (tabs navigation)
├── PodcastSubscriptions.vue   # Vue Abonnements
├── PodcastSearch.vue          # Vue Recherche
├── PodcastQueue.vue           # Vue File d'attente
├── PodcastDetails.vue         # Modal details podcast
├── EpisodeDetails.vue         # Modal details episode
├── PodcastPlayer.vue          # Panel lateral lecteur
├── PodcastCard.vue            # Carte podcast reutilisable
└── EpisodeCard.vue            # Carte episode reutilisable
```

**Structure PodcastSource.vue (skeleton) :**

```vue
<template>
  <div class="podcast-source">
    <!-- Navigation tabs -->
    <div class="tabs">
      <button @click="activeTab = 'home'" :class="{ active: activeTab === 'home' }">
        Accueil
      </button>
      <button @click="activeTab = 'subscriptions'" :class="{ active: activeTab === 'subscriptions' }">
        Abonnements
      </button>
      <button @click="activeTab = 'search'" :class="{ active: activeTab === 'search' }">
        Recherche
      </button>
      <button @click="activeTab = 'queue'" :class="{ active: activeTab === 'queue' }">
        File d'attente
      </button>
    </div>

    <!-- Contenu selon tab -->
    <div class="tab-content">
      <!-- Accueil (inline dans PodcastSource) -->
      <div v-if="activeTab === 'home'" class="home-view">
        <!-- Top France -->
        <!-- Episodes populaires -->
        <!-- Parcourir par genre -->
        <!-- Continue Listening -->
      </div>

      <!-- Autres vues en composants -->
      <PodcastSubscriptions v-else-if="activeTab === 'subscriptions'" />
      <PodcastSearch v-else-if="activeTab === 'search'" />
      <PodcastQueue v-else-if="activeTab === 'queue'" />
    </div>

    <!-- Modales -->
    <PodcastDetails v-if="showPodcastDetails" :uuid="selectedPodcastUuid" @close="closePodcastDetails" />
    <EpisodeDetails v-if="showEpisodeDetails" :uuid="selectedEpisodeUuid" @close="closeEpisodeDetails" />

    <!-- Player panel lateral -->
    <PodcastPlayer v-if="podcastStore.hasCurrentEpisode" />
  </div>
</template>
```

### 7.3 Formatage Utilitaire

```javascript
// utils/podcast.js

/**
 * Formate une duree en secondes vers format lisible
 * @param {number} seconds - Duree en secondes
 * @returns {string} Format "Xh Ymin" ou "Y min"
 */
export function formatDuration(seconds) {
  if (!seconds || seconds <= 0) return '0 min'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}min`
  return `${m} min`
}

/**
 * Formate une duree en temps restant
 * @param {number} position - Position actuelle en secondes
 * @param {number} duration - Duree totale en secondes
 * @returns {string} Format "Xh Ymin restantes" ou "Y min restantes"
 */
export function formatTimeRemaining(position, duration) {
  const remaining = duration - position
  return formatDuration(remaining) + ' restantes'
}

/**
 * Formate un timestamp epoch vers date relative
 * @param {number} epochSeconds - Timestamp en secondes
 * @returns {string} "Aujourd'hui", "Hier", "il y a X jours", etc.
 */
export function formatRelativeDate(epochSeconds) {
  const date = new Date(epochSeconds * 1000)
  const now = new Date()
  const diff = now - date
  const days = Math.floor(diff / 86400000)

  if (days === 0) return "Aujourd'hui"
  if (days === 1) return "Hier"
  if (days < 7) return `il y a ${days} jours`
  if (days < 30) return `il y a ${Math.floor(days / 7)} semaines`
  if (days < 365) return `il y a ${Math.floor(days / 30)} mois`
  return `il y a ${Math.floor(days / 365)} ans`
}

/**
 * Formate un timestamp vers date complete
 * @param {number} epochSeconds - Timestamp en secondes
 * @returns {string} "15 novembre 2024"
 */
export function formatFullDate(epochSeconds) {
  const date = new Date(epochSeconds * 1000)
  return date.toLocaleDateString('fr-FR', {
    day: 'numeric',
    month: 'long',
    year: 'numeric'
  })
}



/**
 * Formate le type d'episode
 * @param {string} type - FULL, TRAILER, BONUS
 * @returns {string} Label francais
 */
export function formatEpisodeType(type) {
  const types = {
    'FULL': 'Complet',
    'TRAILER': 'Bande-annonce',
    'BONUS': 'Bonus'
  }
  return types[type] || type
}

/**
 * Formate le type de serie
 * @param {string} type - EPISODIC, SERIAL
 * @returns {string} Label francais
 */
export function formatSeriesType(type) {
  const types = {
    'EPISODIC': 'Episodique',
    'SERIAL': 'Serie'
  }
  return types[type] || type
}

/**
 * Formate la position en mm:ss ou hh:mm:ss
 * @param {number} seconds - Position en secondes
 * @returns {string} Format "mm:ss" ou "hh:mm:ss"
 */
export function formatPlayerTime(seconds) {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)

  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }
  return `${m}:${s.toString().padStart(2, '0')}`
}

/**
 * Obtient la meilleure image disponible
 * @param {object} podcast - PodcastSeries object
 * @param {number} size - Taille souhaitee (300, 640, 1400)
 * @returns {string} URL de l'image
 */
export function getPodcastImage(podcast, size = 300) {
  if (podcast.itunesInfo?.baseArtworkUrlOf) {
    return podcast.itunesInfo.baseArtworkUrlOf(size)
  }
  return podcast.imageUrl || '/default-podcast.png'
}

/**
 * Obtient l'image d'episode ou fallback podcast
 * @param {object} episode - PodcastEpisode object
 * @param {number} size - Taille souhaitee
 * @returns {string} URL de l'image
 */
export function getEpisodeImage(episode, size = 300) {
  if (episode.imageUrl) return episode.imageUrl
  if (episode.podcastSeries) {
    return getPodcastImage(episode.podcastSeries, size)
  }
  return '/default-episode.png'
}
```

---

## 8. PRIORITES DE DEVELOPPEMENT

### Phase 1 : Core MVP (Fonctionnel minimal)

1. **Backend**
   - TaddyApiClient avec cache memoire simple
   - Routes basiques : search, getPodcastSeries, getPodcastEpisode
   - PodcastPlugin avec mpv (play, pause, resume, seek, stop)
   - Persistence progression lecture

2. **Frontend**
   - PodcastSource.vue structure de base avec tabs
   - PodcastSearch.vue (recherche simple)
   - PodcastCard.vue / EpisodeCard.vue
   - PodcastPlayer.vue (panel lateral)
   - Store Pinia basique

3. **Integration**
   - WebSocket events pour etat lecture
   - Sauvegarde progression automatique

**Resultat :** Peut rechercher, afficher details, lire episodes

### Phase 2 : Decouverte

1. Vue Accueil complete
   - Top Charts France
   - Episodes populaires
   - Genres grid
   - Continue Listening

2. PodcastDetails.vue complet
3. EpisodeDetails.vue complet

**Resultat :** Experience decouverte complete

### Phase 3 : Gestion

1. Abonnements
   - PodcastSubscriptions.vue
   - CRUD subscriptions
   - Nouveaux episodes

2. File d'attente
   - PodcastQueue.vue
   - Gestion progression

**Resultat :** Utilisateur peut gerer sa bibliotheque

### Phase 4 : Avance

1. Filtres recherche complets
2. Vitesse de lecture
3. Cache intelligent avec TTL
4. Gestion rate limit (monitoring quota)
5. Optimisations UI/UX

**Resultat :** Application polie et robuste

---

## ANNEXES

### A. Authentification Taddy API

Headers requis pour chaque requete :
```
X-USER-ID: <votre-user-id>
X-API-KEY: <votre-api-key>
```

Endpoint : `https://api.taddy.org` (POST GraphQL)

### B. Gestion Erreurs HTTP

- **200** : Succes
- **400** : Requete invalide (syntaxe GraphQL)
- **401** : Non authentifie (headers manquants)
- **429** : Rate limit atteint (attendre reset horaire)
- **500** : Erreur serveur Taddy

### C. References

- Schema GraphQL complet : `/home/milo/milo/taddy-api-docs/schema.graphql`
- Documentation Taddy : `/home/milo/milo/taddy-api-docs/taddy-api-docs.md`
- Genres constants : `/home/milo/milo/frontend/src/constants/podcast_genres.js`
- Plugin Radio (reference) : `/home/milo/milo/backend/infrastructure/plugins/radio/`

---

*Document genere le 17 novembre 2024 - Version 1.0*
