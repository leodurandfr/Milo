"""
Valid music genres for RadioBrowser API

This list is used to validate genre tags from RadioBrowser API.
Only tags that match these genres (case-insensitive) will be used as the station genre.

Note: When comparing tags, normalization is applied:
- Convert to lowercase
- Strip whitespace
- Handle hyphens and spaces (e.g., "hip hop" matches "hip-hop")
"""

VALID_GENRES = {
    '60s',
    '70s',
    '80s',
    '90s',
    '1990s',
    '2010s',
    'acoustic',
    'afrobeats',
    'alternative',
    'alternative rock',
    'ambient',
    'americana',
    'art rock',
    'avant-garde',
    'bachata',
    'big band',
    'blues',
    'bluegrass',
    'bossa nova',
    'britpop',
    'celtic',
    'chill',
    'chillout',
    'classic jazz',
    'classic rock',
    'classical',
    'country',
    'dance',
    'dancehall',
    'darkwave',
    'death metal',
    'deep house',
    'disco',
    'downtempo',
    'drum and bass',
    'dub',
    'dubstep',
    'edm',
    'electro',
    'electronic',
    'eurodance',
    'flamenco',
    'folk',
    'folk rock',
    'funk',
    'garage',
    'gospel',
    'groove',
    'grunge',
    'hard rock',
    'hardcore',
    'hip-hop',
    'house',
    'indie',
    'italo disco',
    'jazz',
    'jazz fusion',
    'k-pop',
    'latin',
    'latin music',
    'latin pop',
    'lo-fi',
    'lounge',
    'merengue',
    'metal',
    'minimal',
    'minimal techno',
    'new age',
    'new wave',
    'news',
    'nu disco',
    'oldies',
    'opera',
    'pop',
    'pop dance',
    'pop rock',
    'power metal',
    'progressive house',
    'progressive rock',
    'psychedelic',
    'psychedelic rock',
    'punk',
    'r&b',
    'rap',
    'rare groove',
    'reggae',
    'reggaeton',
    'rock',
    'roots',
    'salsa',
    'schlager',
    'singer-songwriter',
    'ska',
    'smooth jazz',
    'smooth lounge',
    'soul',
    'stoner rock',
    'swing',
    'synthwave',
    'talk',
    'tech house',
    'techno',
    'thrash metal',
    'trance',
    'trap',
    'trip-hop',
    'tropical'
}


def normalize_genre(genre: str) -> str:
    """
    Normalize genre string for comparison

    Args:
        genre: Raw genre string

    Returns:
        Normalized genre string (lowercase, stripped)
    """
    if not genre:
        return ''
    return genre.lower().strip()


def canonical_genre(genre: str) -> str:
    """
    Return the VALID_GENRES spelling a genre string matches, or '' if none

    Matching is case-insensitive and treats spaces and hyphens alike
    (e.g., "Hip Hop" matches "hip-hop").
    """
    normalized = normalize_genre(genre)
    if not normalized:
        return ''

    for candidate in (normalized, normalized.replace(' ', '-'), normalized.replace('-', ' ')):
        if candidate in VALID_GENRES:
            return candidate
    return ''


def extract_valid_genre(tags: str) -> str:
    """
    Extract first valid genre from comma-separated tags

    Args:
        tags: Comma-separated tags from RadioBrowser API (e.g., "aac,groove,public radio")

    Returns:
        First valid genre found, in its VALID_GENRES spelling, or empty string if none found

    Example:
        >>> extract_valid_genre("aac,groove,public radio,radio france")
        "groove"
        >>> extract_valid_genre("mp3,128kbps")
        ""
    """
    if not tags:
        return ''

    for tag in tags.split(','):
        genre = canonical_genre(tag)
        if genre:
            return genre

    return ''
