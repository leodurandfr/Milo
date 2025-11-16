/**
 * Taddy API Genre enums for podcasts
 * 124 hierarchical genres with French labels
 */

export const PODCAST_GENRES = [
  // ARTS (7 genres)
  { label: 'Arts', value: 'PODCASTSERIES_ARTS' },
  { label: 'Arts - Livres', value: 'PODCASTSERIES_ARTS_BOOKS' },
  { label: 'Arts - Design', value: 'PODCASTSERIES_ARTS_DESIGN' },
  { label: 'Arts - Mode et beauté', value: 'PODCASTSERIES_ARTS_FASHION_AND_BEAUTY' },
  { label: 'Arts - Cuisine', value: 'PODCASTSERIES_ARTS_FOOD' },
  { label: 'Arts - Arts du spectacle', value: 'PODCASTSERIES_ARTS_PERFORMING_ARTS' },
  { label: 'Arts - Arts visuels', value: 'PODCASTSERIES_ARTS_VISUAL_ARTS' },

  // BUSINESS (7 genres)
  { label: 'Business', value: 'PODCASTSERIES_BUSINESS' },
  { label: 'Business - Carrières', value: 'PODCASTSERIES_BUSINESS_CAREERS' },
  { label: 'Business - Entrepreneuriat', value: 'PODCASTSERIES_BUSINESS_ENTREPRENEURSHIP' },
  { label: 'Business - Investissement', value: 'PODCASTSERIES_BUSINESS_INVESTING' },
  { label: 'Business - Management', value: 'PODCASTSERIES_BUSINESS_MANAGEMENT' },
  { label: 'Business - Marketing', value: 'PODCASTSERIES_BUSINESS_MARKETING' },
  { label: 'Business - Associations', value: 'PODCASTSERIES_BUSINESS_NON_PROFIT' },

  // COMEDY (4 genres)
  { label: 'Comédie', value: 'PODCASTSERIES_COMEDY' },
  { label: 'Comédie - Interviews', value: 'PODCASTSERIES_COMEDY_INTERVIEWS' },
  { label: 'Comédie - Improvisation', value: 'PODCASTSERIES_COMEDY_IMPROV' },
  { label: 'Comédie - Stand-up', value: 'PODCASTSERIES_COMEDY_STANDUP' },

  // EDUCATION (5 genres)
  { label: 'Éducation', value: 'PODCASTSERIES_EDUCATION' },
  { label: 'Éducation - Cours', value: 'PODCASTSERIES_EDUCATION_COURSES' },
  { label: 'Éducation - Tutoriels', value: 'PODCASTSERIES_EDUCATION_HOW_TO' },
  { label: 'Éducation - Langues', value: 'PODCASTSERIES_EDUCATION_LANGUAGE_LEARNING' },
  { label: 'Éducation - Développement personnel', value: 'PODCASTSERIES_EDUCATION_SELF_IMPROVEMENT' },

  // FICTION (4 genres)
  { label: 'Fiction', value: 'PODCASTSERIES_FICTION' },
  { label: 'Fiction - Comédie', value: 'PODCASTSERIES_FICTION_COMEDY_FICTION' },
  { label: 'Fiction - Drame', value: 'PODCASTSERIES_FICTION_DRAMA' },
  { label: 'Fiction - Science-fiction', value: 'PODCASTSERIES_FICTION_SCIENCE_FICTION' },

  // GOVERNMENT (1 genre)
  { label: 'Gouvernement', value: 'PODCASTSERIES_GOVERNMENT' },

  // HEALTH & FITNESS (7 genres)
  { label: 'Santé et forme', value: 'PODCASTSERIES_HEALTH_AND_FITNESS' },
  { label: 'Santé - Médecines alternatives', value: 'PODCASTSERIES_HEALTH_AND_FITNESS_ALTERNATIVE_HEALTH' },
  { label: 'Santé - Fitness', value: 'PODCASTSERIES_HEALTH_AND_FITNESS_FITNESS' },
  { label: 'Santé - Médecine', value: 'PODCASTSERIES_HEALTH_AND_FITNESS_MEDICINE' },
  { label: 'Santé - Santé mentale', value: 'PODCASTSERIES_HEALTH_AND_FITNESS_MENTAL_HEALTH' },
  { label: 'Santé - Nutrition', value: 'PODCASTSERIES_HEALTH_AND_FITNESS_NUTRITION' },
  { label: 'Santé - Sexualité', value: 'PODCASTSERIES_HEALTH_AND_FITNESS_SEXUALITY' },

  // HISTORY (1 genre)
  { label: 'Histoire', value: 'PODCASTSERIES_HISTORY' },

  // KIDS & FAMILY (5 genres)
  { label: 'Enfants et famille', value: 'PODCASTSERIES_KIDS_AND_FAMILY' },
  { label: 'Enfants - Éducation', value: 'PODCASTSERIES_KIDS_AND_FAMILY_EDUCATION_FOR_KIDS' },
  { label: 'Enfants - Parentalité', value: 'PODCASTSERIES_KIDS_AND_FAMILY_PARENTING' },
  { label: 'Enfants - Animaux', value: 'PODCASTSERIES_KIDS_AND_FAMILY_PETS_AND_ANIMALS' },
  { label: 'Enfants - Histoires', value: 'PODCASTSERIES_KIDS_AND_FAMILY_STORIES_FOR_KIDS' },

  // LEISURE (9 genres)
  { label: 'Loisirs', value: 'PODCASTSERIES_LEISURE' },
  { label: 'Loisirs - Animation et manga', value: 'PODCASTSERIES_LEISURE_ANIMATION_AND_MANGA' },
  { label: 'Loisirs - Automobile', value: 'PODCASTSERIES_LEISURE_AUTOMOTIVE' },
  { label: 'Loisirs - Aviation', value: 'PODCASTSERIES_LEISURE_AVIATION' },
  { label: 'Loisirs - Artisanat', value: 'PODCASTSERIES_LEISURE_CRAFTS' },
  { label: 'Loisirs - Jeux', value: 'PODCASTSERIES_LEISURE_GAMES' },
  { label: 'Loisirs - Hobbies', value: 'PODCASTSERIES_LEISURE_HOBBIES' },
  { label: 'Loisirs - Maison et jardin', value: 'PODCASTSERIES_LEISURE_HOME_AND_GARDEN' },
  { label: 'Loisirs - Jeux vidéo', value: 'PODCASTSERIES_LEISURE_VIDEO_GAMES' },

  // MUSIC (4 genres)
  { label: 'Musique', value: 'PODCASTSERIES_MUSIC' },
  { label: 'Musique - Commentaires', value: 'PODCASTSERIES_MUSIC_COMMENTARY' },
  { label: 'Musique - Histoire', value: 'PODCASTSERIES_MUSIC_HISTORY' },
  { label: 'Musique - Interviews', value: 'PODCASTSERIES_MUSIC_INTERVIEWS' },

  // NEWS (8 genres)
  { label: 'Actualités', value: 'PODCASTSERIES_NEWS' },
  { label: 'Actualités - Business', value: 'PODCASTSERIES_NEWS_BUSINESS' },
  { label: 'Actualités - Quotidien', value: 'PODCASTSERIES_NEWS_DAILY_NEWS' },
  { label: 'Actualités - Divertissement', value: 'PODCASTSERIES_NEWS_ENTERTAINMENT' },
  { label: 'Actualités - Commentaires', value: 'PODCASTSERIES_NEWS_COMMENTARY' },
  { label: 'Actualités - Politique', value: 'PODCASTSERIES_NEWS_POLITICS' },
  { label: 'Actualités - Sport', value: 'PODCASTSERIES_NEWS_SPORTS' },
  { label: 'Actualités - Tech', value: 'PODCASTSERIES_NEWS_TECH' },

  // RELIGION & SPIRITUALITY (8 genres)
  { label: 'Religion et spiritualité', value: 'PODCASTSERIES_RELIGION_AND_SPIRITUALITY' },
  { label: 'Religion - Bouddhisme', value: 'PODCASTSERIES_RELIGION_AND_SPIRITUALITY_BUDDHISM' },
  { label: 'Religion - Christianisme', value: 'PODCASTSERIES_RELIGION_AND_SPIRITUALITY_CHRISTIANITY' },
  { label: 'Religion - Hindouisme', value: 'PODCASTSERIES_RELIGION_AND_SPIRITUALITY_HINDUISM' },
  { label: 'Religion - Islam', value: 'PODCASTSERIES_RELIGION_AND_SPIRITUALITY_ISLAM' },
  { label: 'Religion - Judaïsme', value: 'PODCASTSERIES_RELIGION_AND_SPIRITUALITY_JUDAISM' },
  { label: 'Religion - Générale', value: 'PODCASTSERIES_RELIGION_AND_SPIRITUALITY_RELIGION' },
  { label: 'Spiritualité', value: 'PODCASTSERIES_RELIGION_AND_SPIRITUALITY_SPIRITUALITY' },

  // SCIENCE (10 genres)
  { label: 'Science', value: 'PODCASTSERIES_SCIENCE' },
  { label: 'Science - Astronomie', value: 'PODCASTSERIES_SCIENCE_ASTRONOMY' },
  { label: 'Science - Chimie', value: 'PODCASTSERIES_SCIENCE_CHEMISTRY' },
  { label: 'Science - Sciences de la Terre', value: 'PODCASTSERIES_SCIENCE_EARTH_SCIENCES' },
  { label: 'Science - Sciences de la vie', value: 'PODCASTSERIES_SCIENCE_LIFE_SCIENCES' },
  { label: 'Science - Mathématiques', value: 'PODCASTSERIES_SCIENCE_MATHEMATICS' },
  { label: 'Science - Sciences naturelles', value: 'PODCASTSERIES_SCIENCE_NATURAL_SCIENCES' },
  { label: 'Science - Nature', value: 'PODCASTSERIES_SCIENCE_NATURE' },
  { label: 'Science - Physique', value: 'PODCASTSERIES_SCIENCE_PHYSICS' },
  { label: 'Science - Sciences sociales', value: 'PODCASTSERIES_SCIENCE_SOCIAL_SCIENCES' },

  // SOCIETY & CULTURE (6 genres)
  { label: 'Société et culture', value: 'PODCASTSERIES_SOCIETY_AND_CULTURE' },
  { label: 'Société - Documentaires', value: 'PODCASTSERIES_SOCIETY_AND_CULTURE_DOCUMENTARY' },
  { label: 'Société - Journaux personnels', value: 'PODCASTSERIES_SOCIETY_AND_CULTURE_PERSONAL_JOURNALS' },
  { label: 'Société - Philosophie', value: 'PODCASTSERIES_SOCIETY_AND_CULTURE_PHILOSOPHY' },
  { label: 'Société - Voyages', value: 'PODCASTSERIES_SOCIETY_AND_CULTURE_PLACES_AND_TRAVEL' },
  { label: 'Société - Relations', value: 'PODCASTSERIES_SOCIETY_AND_CULTURE_RELATIONSHIPS' },

  // SPORTS (15 genres)
  { label: 'Sports', value: 'PODCASTSERIES_SPORTS' },
  { label: 'Sports - Baseball', value: 'PODCASTSERIES_SPORTS_BASEBALL' },
  { label: 'Sports - Basketball', value: 'PODCASTSERIES_SPORTS_BASKETBALL' },
  { label: 'Sports - Cricket', value: 'PODCASTSERIES_SPORTS_CRICKET' },
  { label: 'Sports - Fantasy sports', value: 'PODCASTSERIES_SPORTS_FANTASY_SPORTS' },
  { label: 'Sports - Football américain', value: 'PODCASTSERIES_SPORTS_FOOTBALL' },
  { label: 'Sports - Golf', value: 'PODCASTSERIES_SPORTS_GOLF' },
  { label: 'Sports - Hockey', value: 'PODCASTSERIES_SPORTS_HOCKEY' },
  { label: 'Sports - Rugby', value: 'PODCASTSERIES_SPORTS_RUGBY' },
  { label: 'Sports - Course à pied', value: 'PODCASTSERIES_SPORTS_RUNNING' },
  { label: 'Sports - Football', value: 'PODCASTSERIES_SPORTS_SOCCER' },
  { label: 'Sports - Natation', value: 'PODCASTSERIES_SPORTS_SWIMMING' },
  { label: 'Sports - Tennis', value: 'PODCASTSERIES_SPORTS_TENNIS' },
  { label: 'Sports - Volleyball', value: 'PODCASTSERIES_SPORTS_VOLLEYBALL' },
  { label: 'Sports - Nature', value: 'PODCASTSERIES_SPORTS_WILDERNESS' },
  { label: 'Sports - Lutte', value: 'PODCASTSERIES_SPORTS_WRESTLING' },

  // TECHNOLOGY (1 genre)
  { label: 'Technologie', value: 'PODCASTSERIES_TECHNOLOGY' },

  // TRUE CRIME (1 genre)
  { label: 'True crime', value: 'PODCASTSERIES_TRUE_CRIME' },

  // TV & FILM (6 genres)
  { label: 'TV et cinéma', value: 'PODCASTSERIES_TV_AND_FILM' },
  { label: 'TV - After shows', value: 'PODCASTSERIES_TV_AND_FILM_AFTER_SHOWS' },
  { label: 'TV - Histoire', value: 'PODCASTSERIES_TV_AND_FILM_HISTORY' },
  { label: 'TV - Interviews', value: 'PODCASTSERIES_TV_AND_FILM_INTERVIEWS' },
  { label: 'TV - Critiques de films', value: 'PODCASTSERIES_TV_AND_FILM_FILM_REVIEWS' },
  { label: 'TV - Critiques de séries', value: 'PODCASTSERIES_TV_AND_FILM_TV_REVIEWS' },
];

/**
 * Creates dropdown options for podcast genres
 * @param {string} allLabel - Label for "All Genres" option
 * @returns {Array} Array of {label, value} objects for dropdown
 */
export function createPodcastGenreOptions(allLabel = 'Tous les genres') {
  return [
    { label: allLabel, value: '' },
    ...PODCAST_GENRES
  ];
}
