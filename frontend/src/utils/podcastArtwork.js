// frontend/src/utils/podcastArtwork.js
// Whether an episode carries a picture of its own, or the show's.
//
// The catalog fills an episode's `image_url` from its <itunes:image>, and with
// the show's image when the item has none (rss_parser.py), so an episode
// without a picture of its own arrives with the show's URL, not an empty one.
// And the same picture does not always arrive under the same string: Radio
// France stamps the show's image with a cache-busting query
// (`…/1400x1400_les-pieds-sur-terre.jpg?1791066081785`) that its items repeat
// without, and imgix-style CDNs put the rendition in the query. So two URLs
// name the same image when they differ only by query and fragment.

/** A URL's identity without its query and fragment; relative URLs kept relative. */
function withoutQuery(url) {
  try {
    const parsed = new URL(url, 'http://relative.invalid');
    return `${parsed.origin}${parsed.pathname}`;
  } catch {
    return url.split(/[?#]/)[0];
  }
}

/** Whether two image URLs name the same image. */
export function sameImage(a, b) {
  return !!a && !!b && withoutQuery(a) === withoutQuery(b);
}

/**
 * Whether the picture an episode is shown with is its own rather than its
 * show's: an empty episode image is no picture of its own, and one naming the
 * show's image is the show's. With no show image to compare against, a
 * picture the episode has is its own.
 *
 * @param {{image_url?: string, podcast?: {image_url?: string}}|null} episode
 */
export function episodeHasOwnImage(episode) {
  const own = episode?.image_url;
  if (!own) return false;
  const show = episode?.podcast?.image_url;
  if (!show) return true;
  return !sameImage(own, show);
}
