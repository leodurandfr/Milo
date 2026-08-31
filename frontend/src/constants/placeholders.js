/**
 * The two images Milō draws in place of a cover it does not have: a disc for
 * anything musical (album, track, playlist, artist, CD) and a microphone for a
 * podcast episode.
 *
 * Imported from here and nowhere else. Reaching for the asset directly is a
 * second chance to pick a different drawing for the same silence, which is how
 * one CD ended up drawn twice — as a 20 KB JPEG with a baked white background
 * for the player, and as a transparent SVG for the cards.
 */
import musicPlaceholder from '@/assets/images/music-placeholder.svg';
import podcastPlaceholder from '@/assets/images/podcast-placeholder.svg';

export { musicPlaceholder, podcastPlaceholder };
