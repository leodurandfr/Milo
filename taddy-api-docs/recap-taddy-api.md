  📚 RÉCAPITULATIF EXHAUSTIF - Plugin Podcasts Milō via Taddy API

  TABLE DES MATIÈRES

  1. #1-architecture-des-vues
  2. #2-queries-graphql-disponibles
  3. #3-modèles-de-données-complets
  4. #4-fonctionnalités-par-vue
  5. #5-données-locales-à-persister
  6. #6-contraintes--limitations
  7. #7-spécifications-techniques

  ---
  1. ARCHITECTURE DES VUES

  Vues Principales (Navigation Tabs)

  1. Accueil - Découverte sans recherche
  2. Abonnements - Podcasts suivis
  3. Recherche - Recherche avancée
  4. File d'attente - Épisodes en cours/non terminés

  Sous-vues (Modales/Écrans Secondaires)

  - Détails Podcast - Informations complètes + liste épisodes
  - Détails Épisode - Description complète + chapitres

  Composant Global

  - Lecteur - Visible sur toutes les vues (mini ou expandable)

  ---
  2. QUERIES GRAPHQL DISPONIBLES

  2.1 getPopularContent - Podcasts Populaires Globaux

  getPopularContent(
    taddyType: PODCASTSERIES,           # Type (PODCASTSERIES uniquement pour l'instant)
    filterByLanguage: FRENCH,            # Optionnel: filtrer par langue
    filterByGenres: [PODCASTSERIES_TECHNOLOGY],  # Optionnel: filtrer par genres
    page: 1,                             # Max 20
    limitPerPage: 25                     # Max 25
  ) {
    popularityRankId                     # ID pour cache
    podcastSeries { ... }                # Liste PodcastSeries
  }

  Utilisation Accueil : "Podcasts populaires", "Populaires en français", "Populaires Tech"

  2.2 getTopChartsByCountry - Top Charts par Pays

  getTopChartsByCountry(
    taddyType: PODCASTSERIES,            # ou PODCASTEPISODE
    country: FRANCE,                     # Requis: pays
    source: APPLE_PODCASTS,              # Défaut: APPLE_PODCASTS
    page: 1,                             # Max 20
    limitPerPage: 25                     # Max 25
  ) {
    topChartsId                          # ID pour cache
    podcastSeries { ... }                # Liste si taddyType=PODCASTSERIES
    podcastEpisodes { ... }              # Liste si taddyType=PODCASTEPISODE
  }

  Utilisation Accueil : "Top France", "Top USA", "Épisodes populaires France"

  2.3 getTopChartsByGenres - Top Charts par Genre

  getTopChartsByGenres(
    taddyType: PODCASTSERIES,            # ou PODCASTEPISODE
    genres: [PODCASTSERIES_TRUE_CRIME],  # Requis: tableau genres
    source: APPLE_PODCASTS,
    filterByCountry: FRANCE,             # Optionnel (surtout pour épisodes)
    page: 1,
    limitPerPage: 25
  ) {
    topChartsId
    podcastSeries { ... }
    podcastEpisodes { ... }
  }

  Utilisation Accueil : "Top True Crime", "Top Technologie", "Populaires par genre"

  2.4 getPodcastSeries - Détails Podcast + Épisodes

  getPodcastSeries(
    uuid: ID,                            # Ou par name, rssUrl, itunesId
  ) {
    # Tous les champs PodcastSeries
    episodes(
      sortOrder: LATEST,                 # LATEST, OLDEST, ou SEARCH
      page: 1,                           # Max 1000
      limitPerPage: 25,                  # Max 25
      searchTerm: "mot",                 # Si sortOrder=SEARCH
      includeRemovedEpisodes: false
    ) { ... }
  }

  Utilisation : Vue Détails Podcast, rafraîchissement abonnements

  2.5 getPodcastEpisode - Détails Épisode Complet

  getPodcastEpisode(
    uuid: ID,                            # Ou par guid, name
    seriesUuidForLookup: ID              # Optionnel: restreindre à un podcast
  ) {
    # Tous les champs PodcastEpisode
    chapters { ... }                     # Chapitres si disponibles
    transcript { ... }                   # Transcription si disponible
  }

  Utilisation : Vue Détails Épisode, chapitres, transcription

  2.6 getMultiplePodcastEpisodes - Batch Épisodes

  getMultiplePodcastEpisodes(
    uuids: [ID]                          # Max 25 IDs
  ) {
    # Liste PodcastEpisode
  }

  Utilisation : Charger plusieurs épisodes favoris/en cours simultanément

  2.7 getMultiplePodcastSeries - Batch Podcasts

  getMultiplePodcastSeries(
    uuids: [ID]                          # Max 25 IDs
  ) {
    # Liste PodcastSeries
  }

  Utilisation : Charger abonnements en batch

  2.8 getLatestPodcastEpisodes - Derniers Épisodes Multi-Podcasts

  getLatestPodcastEpisodes(
    uuids: [ID],                         # Max 1000 UUIDs podcasts
    # OU
    rssUrls: [String],                   # Max 1000 URLs RSS
    page: 1,                             # Max 20
    limitPerPage: 50                     # Max 50 (plus généreux!)
  ) {
    # Liste PodcastEpisode triée par date
  }

  Utilisation Abonnements : "Nouveaux épisodes" de tous les podcasts suivis

  2.9 search - Recherche Avancée

  search(
    term: "mot clé",                     # Requis
    filterForTypes: [PODCASTSERIES],     # ou PODCASTEPISODE
    filterForGenres: [Genre],
    filterForLanguages: [Language],
    filterForCountries: [Country],
    filterForPodcastContentType: [AUDIO], # AUDIO ou VIDEO

    # Pour épisodes uniquement:
    filterForDurationLessThan: 1800,     # Secondes
    filterForDurationGreaterThan: 600,
    filterForHasTranscript: true,
    filterForPublishedAfter: 1700000000, # Epoch timestamp
    filterForPublishedBefore: 1750000000,

    # Pour podcasts uniquement:
    filterForLastUpdatedAfter: Int,
    filterForLastUpdatedBefore: Int,
    filterForTotalEpisodesLessThan: Int,
    filterForTotalEpisodesGreaterThan: Int,

    sortBy: POPULARITY,                  # ou EXACTNESS
    matchBy: MOST_TERMS,                 # ou ALL_TERMS, EXACT_PHRASE
    isSafeMode: false,                   # Exclure explicite
    page: 1,                             # Max 20
    limitPerPage: 25                     # Max 25
  ) {
    searchId
    podcastSeries { ... }
    podcastEpisodes { ... }
    rankingDetails {
      uuid
      rankingScore                       # 0-100
      type
    }
    responseDetails {
      totalCount
      pagesCount
      type
    }
  }

  Utilisation Vue Recherche : Tous les filtres, infinite scroll

  2.10 getApiRequestsRemaining - Quota API

  getApiRequestsRemaining

  Utilisation : Monitoring, affichage warning si proche limite

  ---
  3. MODÈLES DE DONNÉES COMPLETS

  3.1 PodcastSeries

  interface PodcastSeries {
    // Identifiants
    uuid: ID                              // Identifiant unique Taddy
    itunesId: number                      // ID Apple Podcasts

    // Informations principales
    name: string                          // Titre
    description: string                   // Description (HTML ou texte brut via shouldStripHtmlTags)
    descriptionLinks: string[]            // URLs extraites de la description
    authorName: string                    // Nom du créateur

    // Images
    imageUrl: string                      // Pochette principale
    itunesInfo: {
      baseArtworkUrlOf(size: number): string  // Image haute résolution (640, 1400...)
      publisherName: string               // Éditeur
      country: Country                    // Pays
      subtitle: string
      summary: string
    }

    // Métadonnées
    datePublished: number                 // Epoch timestamp (secondes)
    totalEpisodesCount: number            // Nombre d'épisodes
    genres: Genre[]                       // Max 5 genres
    language: Language                    // Langue parlée
    contentType: 'AUDIO' | 'VIDEO'        // Type de contenu
    seriesType: 'EPISODIC' | 'SERIAL'     // Épisodes indépendants vs ordre important

    // Flags
    isCompleted: boolean                  // Série terminée
    isExplicitContent: boolean            // Contenu explicite
    isBlocked: boolean                    // Bloqué par Taddy

    // Popularité
    popularityRank: PopularityRank | null // TOP_200 à TOP_200000

    // Liens externes
    websiteUrl: string                    // Site web
    rssUrl: string                        // Flux RSS
    rssOwnerName: string
    rssOwnerPublicEmail: string
    copyright: string

    // Personnes
    persons: Person[]                     // Hosts, guests, etc.

    // Hashes (détection de changements)
    hash: string                          // Hash des détails
    childrenHash: string                  // Hash des épisodes

    // Feed refresh
    feedRefreshDetails: {
      dateLastRefreshed: number
      priority: 'HIGH' | 'MEDIUM' | 'LOW' | 'INACTIVE' | 'NEVER'
      priorityReason: string
    }

    // Transcription
    taddyTranscribeStatus: 'TRANSCRIBING' | 'NOT_TRANSCRIBING' | 'CREATOR_ASKED_NOT_TO_TRANSCRIBE'

    // Épisodes (avec pagination)
    episodes: PodcastEpisode[]
  }

  3.2 PodcastEpisode

  interface PodcastEpisode {
    // Identifiants
    uuid: ID
    guid: string                          // GUID RSS

    // Informations principales
    name: string                          // Titre
    description: string                   // Description complète
    descriptionLinks: string[]            // URLs extraites
    subtitle: string                      // Max 255 caractères

    // Média
    audioUrl: string                      // URL fichier audio
    videoUrl: string                      // URL vidéo (si podcast vidéo)
    duration: number                      // Durée en SECONDES
    fileLength: number                    // Taille fichier
    fileType: string                      // MIME type

    // Images
    imageUrl: string                      // Pochette épisode (ou podcast si null)

    // Métadonnées
    datePublished: number                 // Epoch timestamp (secondes)
    episodeType: 'FULL' | 'TRAILER' | 'BONUS'
    seasonNumber: number | null
    episodeNumber: number | null

    // Flags
    isExplicitContent: boolean
    isRemoved: boolean                    // Retiré du RSS
    isBlocked: boolean

    // Liens
    websiteUrl: string                    // Page web de l'épisode

    // Parent
    podcastSeries: PodcastSeries

    // Personnes (spécifiques à l'épisode)
    persons: Person[]

    // Hash
    hash: string

    // Chapitres
    chapters: Chapter[]
    chaptersUrls: string[]

    // Transcription
    taddyTranscribeStatus: 'COMPLETED' | 'PROCESSING' | 'FAILED' | 'NOT_TRANSCRIBING'
    transcript: string[]                  // Paragraphes
    transcriptWithSpeakersAndTimecodes: TranscriptItem[]
    transcriptUrls: string[]
  }

  interface Chapter {
    id: ID
    title: string
    startTimecode: number                 // MILLISECONDES
  }

  interface TranscriptItem {
    id: ID
    text: string
    speaker: string | null
    startTimecode: number                 // MILLISECONDES
    endTimecode: number
  }

  3.3 Person (60+ rôles)

  interface Person {
    uuid: ID
    name: string
    role: ContentRole                     // HOST, CO_HOST, GUEST, PRODUCER, NARRATOR, EDITOR, etc.
    imageUrl: string
    url: string
  }

  3.4 PopularityRank

  TOP_200 | TOP_1000 | TOP_2000 | TOP_3000 | TOP_4000 | TOP_5000 |
  TOP_10000 | TOP_20000 | TOP_50000 | TOP_100000 | TOP_200000 | null

  3.5 Genres (124 disponibles)

  Catégories principales :
  - Arts (7 sous-genres)
  - Business (7)
  - Comédie (4)
  - Éducation (5)
  - Fiction (4)
  - Gouvernement (1)
  - Santé et forme (7)
  - Histoire (1)
  - Enfants et famille (5)
  - Loisirs (9)
  - Musique (4)
  - Actualités (8)
  - Religion et spiritualité (8)
  - Science (5)
  - Société et culture (7)
  - Sports (19)
  - Technologie (1)
  - True Crime (1)
  - TV et cinéma (4)

  ---
  4. FONCTIONNALITÉS PAR VUE

  4.1 VUE ACCUEIL

  Sections suggérées :
  1. Top France - getTopChartsByCountry(country: FRANCE, taddyType: PODCASTSERIES)
  2. Épisodes du moment - getTopChartsByCountry(country: FRANCE, taddyType: PODCASTEPISODE)
  3. Parcourir par genre - Grille de genres cliquables → getTopChartsByGenres
  4. Populaires globalement - getPopularContent(filterByLanguage: FRENCH)
  5. Continue Listening - Données locales + getMultiplePodcastEpisodes

  Données affichées (carte podcast) :
  - Pochette (imageUrl ou itunesInfo.baseArtworkUrlOf(300))
  - Titre (name)
  - Éditeur (itunesInfo.publisherName ou authorName)
  - Badge popularité (popularityRank)
  - Badge explicite si isExplicitContent
  - Nombre d'épisodes (totalEpisodesCount)

  Données affichées (carte épisode) :
  - Pochette épisode (imageUrl ou podcastSeries.imageUrl)
  - Titre épisode (name)
  - Nom podcast (podcastSeries.name)
  - Durée formatée (duration → "1h 23min")
  - Date publication (datePublished → "il y a 3 jours")
  - Type (episodeType badge si TRAILER ou BONUS)

  4.2 VUE ABONNEMENTS

  Sections :
  1. Nouveaux épisodes - getLatestPodcastEpisodes(uuids: subscriptions)
    - Triés par date publication
    - Badge "Nouveau" si < 7 jours
    - Indicateur si déjà écouté/en cours
  2. Mes podcasts - getMultiplePodcastSeries(uuids: subscriptions)
    - Grille ou liste
    - Tri : Alphabétique, Récent, Nouveaux épisodes
    - Action : Désabonner (swipe ou menu)

  Détection nouveautés :
  - Sauvegarder childrenHash à chaque fetch
  - Comparer avec hash précédent pour détecter changements
  - Afficher badge "X nouveaux" si différent

  4.3 VUE RECHERCHE

  Interface :
  1. Barre de recherche (term)
  2. Toggle : Podcasts / Épisodes (filterForTypes)
  3. Filtres dépliables :
    - Genre (filterForGenres) - Multi-select
    - Langue (filterForLanguages)
    - Pays (filterForCountries)
    - Durée (pour épisodes) :
        - "< 15 min" → filterForDurationLessThan: 900
      - "15-30 min" → both filters
      - "30-60 min"
      - "> 1h" → filterForDurationGreaterThan: 3600
    - Safe Mode (isSafeMode)
    - Tri (sortBy) : Pertinence / Popularité
  4. Résultats avec infinite scroll
    - Affiche totalCount et pagesCount
    - Score de pertinence (rankingDetails.rankingScore)

  Astuce recherche :
  - Exclusion avec - : "technologie -crypto"
  - Phrase exacte avec EXACT_PHRASE

  4.4 VUE FILE D'ATTENTE (Continue Listening)

  Données sources :
  - Épisodes avec playback_progress sauvegardé
  - Position > 0 ET position < (duration - 30)

  Affichage :
  - Pochette
  - Titre épisode + Podcast
  - Barre de progression mini (position/duration)
  - Temps restant formaté
  - Date dernière écoute
  - Actions : Play (reprend), Marquer terminé, Supprimer

  Tri :
  - Par date dernière écoute (défaut)
  - Par temps restant
  - Par podcast

  4.5 DÉTAILS PODCAST (Sous-vue)

  Header :
  - Pochette large (itunesInfo.baseArtworkUrlOf(640))
  - Titre
  - Éditeur/Auteur
  - Badges : Popularité, Explicite, Terminé (isCompleted)
  - Genres (tags cliquables)
  - Langue
  - Type (SERIAL → "Série" / EPISODIC → "Épisodique")
  - Bouton S'abonner / Se désabonner

  Section Infos :
  - Description complète (description avec shouldStripHtmlTags: true)
  - Liens extraits (descriptionLinks)
  - Site web (websiteUrl)
  - Hosts/Personnes (persons avec rôles)

  Liste Épisodes :
  - Tri : Récent (LATEST) / Ancien (OLDEST) / Recherche (SEARCH)
  - Recherche dans épisodes (searchTerm)
  - Infinite scroll (page, limitPerPage: 25, max 1000 pages)
  - Indicateurs : En cours (barre), Écouté (check), Favori (cœur)

  4.6 DÉTAILS ÉPISODE (Sous-vue)

  Informations :
  - Pochette
  - Titre + Sous-titre
  - Podcast parent (lien)
  - Date publication formatée
  - Durée formatée
  - Type (FULL/TRAILER/BONUS)
  - Saison X Épisode Y (si disponibles)
  - Badge explicite

  Description :
  - Description complète
  - Liens extraits

  Chapitres (si disponibles) :
  Introduction          00:00
  Interview             03:00  ▶️ (clic pour sauter)
  Conclusion            40:00

  Actions :
  - Play / Resume
  - Ajouter aux favoris
  - Partager (websiteUrl)

  Transcription (optionnel, si disponible) :
  - Affichage synchronisé avec lecture
  - Identification speakers
  - Clic pour naviguer

  4.7 LECTEUR (Composant Global)

  Mini Player (visible sur toutes vues) :
  - Pochette miniature (50x50)
  - Titre épisode (tronqué)
  - Nom podcast (tronqué)
  - Play/Pause
  - Barre de progression fine
  - Clic pour expandre

  Player Expandé :
  - Pochette large
  - Titre épisode complet
  - Nom podcast
  - Barre de progression interactive (seek)
  - Temps actuel / Durée totale
  - Contrôles :
    - -15 secondes
    - Play/Pause
    - +30 secondes
    - Vitesse de lecture (0.5x, 1x, 1.25x, 1.5x, 2x) → mpv supporte
  - Toggle Favori
  - Liste chapitres (si disponibles, cliquables)

  Comportement :
  - Sauvegarde position toutes les 10 secondes
  - Reprise automatique à dernière position
  - Détection fin épisode (marquer comme terminé)
  - Broadcast WebSocket état lecture

  ---
  5. DONNÉES LOCALES À PERSISTER

  5.1 Structure /var/lib/milo/podcast_data.json

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

    "favorites": [
      {
        "uuid": "episode-uuid-1",
        "podcastUuid": "podcast-uuid-1",
        "name": "Episode Name",
        "podcastName": "Podcast Name",
        "imageUrl": "https://...",
        "duration": 3600,
        "addedAt": 1700000000
      }
    ],

    "playback_progress": {
      "episode-uuid-1": {
        "position": 1234,
        "duration": 3600,
        "lastPlayed": 1700000000,
        "completed": false
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
      "playbackSpeed": 1.0,
      "skipSilence": false,
      "autoPlay": false
    }
  }

  5.2 Opérations CRUD

  Abonnements :
  - subscribe(podcastUuid) - Ajouter avec métadonnées
  - unsubscribe(podcastUuid) - Retirer
  - getSubscriptions() - Liste
  - isSubscribed(podcastUuid) - Vérifier

  Favoris :
  - addFavorite(episodeUuid, metadata) - Ajouter
  - removeFavorite(episodeUuid) - Retirer
  - getFavorites() - Liste
  - isFavorite(episodeUuid) - Vérifier

  Progression :
  - saveProgress(episodeUuid, position, duration) - MAJ position
  - getProgress(episodeUuid) - Récupérer position
  - markAsCompleted(episodeUuid) - Marquer terminé
  - clearProgress(episodeUuid) - Effacer
  - getInProgressEpisodes() - Liste pour File d'attente

  Cache :
  - TTL recommandé : 1 heure
  - Invalidation sur hash différent
  - Nettoyage périodique vieux cache

  ---
  6. CONTRAINTES & LIMITATIONS

  6.1 Taddy API

  - Rate Limit : 100 requêtes/heure (HTTP 429)
  - Pagination : Max 25 résultats/page, max 20 pages (500 résultats)
  - Épisodes podcast : Max 1000 pages (25000 épisodes)
  - Batch queries : Max 25 UUIDs
  - getLatestPodcastEpisodes : Max 1000 podcasts, 50 épisodes/page
  - Top Charts : Mis à jour quotidiennement (~6h PST)
  - Transcriptions auto : Business tier requis

  6.2 Pas Disponible via API

  - Recommandations personnalisées (pas d'endpoint)
  - Notes/Avis utilisateurs
  - Commentaires sociaux
  - Statistiques d'écoute globales
  - Téléchargement offline (doit être implémenté localement)

  6.3 À Implémenter Localement

  - File d'attente / Playlist
  - Téléchargement episodes
  - Historique d'écoute
  - Sleep timer
  - Notifications nouveaux épisodes
  - Sync multi-device (si nécessaire)

  ---
  7. SPÉCIFICATIONS TECHNIQUES

  7.1 Backend (FastAPI)

  Routes API à créer :
  GET  /api/podcast/discover/popular
  GET  /api/podcast/discover/top-charts/country/{country}
  GET  /api/podcast/discover/top-charts/genres
  GET  /api/podcast/search
  GET  /api/podcast/series/{uuid}
  GET  /api/podcast/series/{uuid}/episodes
  GET  /api/podcast/episode/{uuid}
  GET  /api/podcast/episode/{uuid}/chapters
  GET  /api/podcast/subscriptions
  POST /api/podcast/subscriptions
  DELETE /api/podcast/subscriptions/{uuid}
  GET  /api/podcast/subscriptions/latest-episodes
  GET  /api/podcast/favorites
  POST /api/podcast/favorites
  DELETE /api/podcast/favorites/{uuid}
  GET  /api/podcast/queue
  POST /api/podcast/play
  POST /api/podcast/pause
  POST /api/podcast/resume
  POST /api/podcast/seek
  POST /api/podcast/stop
  POST /api/podcast/speed
  GET  /api/podcast/status
  GET  /api/podcast/progress
  POST /api/podcast/progress

  Services nécessaires :
  - TaddyApiClient - Wrapper GraphQL avec cache et rate limiting
  - PodcastDataService - Persistence JSON (abonnements, favoris, progression)
  - PodcastPlugin - Plugin audio (étend UnifiedAudioPlugin)
  - MpvController - Contrôle mpv (existant pour radio)

  7.2 Frontend (Vue 3)

  Store Pinia :
  // podcastStore.js
  {
    // State
    currentEpisode: null,
    playbackState: 'stopped', // 'playing', 'paused'
    currentPosition: 0,
    playbackSpeed: 1.0,

    subscriptions: [],
    favorites: [],
    inProgressEpisodes: [],

    searchResults: [],
    searchFilters: {},

    // Cache local
    podcastCache: {},
    episodeCache: {},

    // Actions
    play(episodeUuid),
    pause(),
    resume(),
    seek(position),
    setSpeed(speed),

    subscribe(podcastUuid),
    unsubscribe(podcastUuid),

    addFavorite(episodeUuid),
    removeFavorite(episodeUuid),

    search(term, filters),
    loadMoreResults(),

    fetchPodcastDetails(uuid),
    fetchEpisodeDetails(uuid),

    // WebSocket handlers
    handleStateUpdate(data),
    handleProgressUpdate(data)
  }

  Composants :
  - PodcastSource.vue - Container principal avec navigation tabs
  - PodcastHome.vue - Vue Accueil
  - PodcastSubscriptions.vue - Vue Abonnements
  - PodcastSearch.vue - Vue Recherche
  - PodcastQueue.vue - Vue File d'attente
  - PodcastDetails.vue - Modal détails podcast
  - EpisodeDetails.vue - Modal détails épisode
  - PodcastPlayer.vue - Lecteur (mini + expandable)
  - PodcastCard.vue - Carte podcast
  - EpisodeCard.vue - Carte épisode
  - ChapterList.vue - Liste chapitres
  - GenreGrid.vue - Grille genres

  7.3 Formatage Utilitaire

  // Durée (secondes → format lisible)
  function formatDuration(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (h > 0) return `${h}h ${m}min`;
    return `${m} min`;
  }

  // Date (timestamp → relatif)
  function formatDate(epochSeconds) {
    const date = new Date(epochSeconds * 1000);
    const now = new Date();
    const diff = now - date;

    if (diff < 86400000) return "Aujourd'hui";
    if (diff < 172800000) return "Hier";
    if (diff < 604800000) return `il y a ${Math.floor(diff/86400000)} jours`;
    // etc.
  }

  // Popularity rank badge
  function getPopularityBadge(rank) {
    if (!rank) return null;
    const num = parseInt(rank.split('_')[1]);
    if (num <= 200) return { text: 'Top 300', class: 'badge-gold' };
    if (num <= 1000) return { text: 'TOP 1K', class: 'badge-silver' };
    // etc.
  }

  ---
  8. PRIORITÉS DE DÉVELOPPEMENT SUGGÉRÉES

  Phase 1 : Core (MVP)

  1. Backend : TaddyApiClient avec cache
  2. Backend : Routes basiques (search, series, episode)
  3. Backend : Plugin audio avec mpv
  4. Frontend : Recherche simple
  5. Frontend : Détails podcast + épisodes
  6. Frontend : Lecteur basique (play/pause/seek)
  7. Persistence : Progression lecture

  Phase 2 : Découverte

  1. Vue Accueil avec popular/top charts
  2. Navigation genres
  3. Abonnements
  4. Favoris

  Phase 3 : Avancé

  1. File d'attente / Continue Listening
  2. Filtres recherche complets
  3. Chapitres
  4. Vitesse lecture
  5. Nouveaux épisodes abonnements

  Phase 4 : Polish

  1. Cache intelligent
  2. Gestion rate limit
  3. Transcriptions (si disponibles)
  4. Optimisations UI/UX

