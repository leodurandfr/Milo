/**
 * The line a Spotify card shows under its name: Spotify's own subtitle, else
 * "Artist" for an artist, else nothing — a library playlist has none. Read by
 * the card, and by its shelf, whose height depends on whether any card has one.
 */
export function cardByline(item, t) {
  if (item.subtitle) return item.subtitle;
  return item.kind === 'artist' ? t('spotify.artist') : '';
}

/**
 * What a card writes under its cover: a heading-4 line, then the byline under
 * it. The line is the card's name — or its byline, when the cover already
 * carries the name (a station's "With …"); a card with neither writes nothing.
 */
export function cardLines(item, t) {
  const byline = cardByline(item, t);
  if (item.name_in_cover) return { heading: byline, byline: '' };
  return { heading: item.name || t('spotify.untitledPlaylist'), byline };
}
