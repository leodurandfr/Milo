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
    'hiphop',
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
    'rnb',
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
    'tropical',
    'urban',
    'world music'
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


def is_valid_genre(genre: str) -> bool:
    """
    Check if a genre is valid

    Args:
        genre: Genre string to validate

    Returns:
        True if genre is in VALID_GENRES (case-insensitive)
    """
    normalized = normalize_genre(genre)
    if not normalized:
        return False

    # Direct match
    if normalized in VALID_GENRES:
        return True

    # Handle space/hyphen variations (e.g., "hip hop" vs "hip-hop")
    normalized_with_hyphen = normalized.replace(' ', '-')
    normalized_with_space = normalized.replace('-', ' ')

    return (normalized_with_hyphen in VALID_GENRES or
            normalized_with_space in VALID_GENRES)


def extract_valid_genre(tags: str) -> str:
    """
    Extract first valid genre from comma-separated tags

    Args:
        tags: Comma-separated tags from RadioBrowser API (e.g., "aac,groove,public radio")

    Returns:
        First valid genre found, or empty string if none found

    Example:
        >>> extract_valid_genre("aac,groove,public radio,radio france")
        "groove"
        >>> extract_valid_genre("mp3,128kbps")
        ""
    """
    if not tags:
        return ''

    # Split by comma and check each tag
    tag_list = [tag.strip() for tag in tags.split(',') if tag.strip()]

    for tag in tag_list:
        if is_valid_genre(tag):
            # Return the normalized version that exists in VALID_GENRES
            normalized = normalize_genre(tag)

            # Try direct match first
            if normalized in VALID_GENRES:
                return normalized

            # Try hyphen variation
            with_hyphen = normalized.replace(' ', '-')
            if with_hyphen in VALID_GENRES:
                return with_hyphen

            # Try space variation
            with_space = normalized.replace('-', ' ')
            if with_space in VALID_GENRES:
                return with_space

    return ''
