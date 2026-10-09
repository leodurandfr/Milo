/**
 * The line a Spotify card shows under its name: Spotify's own subtitle, else
 * what the card is when that says something (an artist, a playlist Spotify
 * made), else nothing. Read by the card, and by its shelf, whose height depends
 * on whether any card has one.
 */
export function cardByline(item, t) {
  if (item.subtitle) return item.subtitle;
  if (item.kind === 'artist') return t('spotify.artist');
  return item.owner === 'spotify' ? 'Spotify' : '';
}
