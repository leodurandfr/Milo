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
 * What a card writes under its cover: its name, then its byline. A cover that
 * already carries the name (a mix, a station) writes the byline alone.
 */
export function cardLines(item, t) {
  const byline = cardByline(item, t);
  return { heading: item.name_in_cover ? '' : item.name || t('spotify.untitledPlaylist'), byline };
}
