// frontend/tests/pure/podcastArtwork.test.js
/**
 * Whether an episode's picture is its own or its show's — what decides if the
 * full player's source bar names the show (the cover is the episode's) or just
 * says Podcasts (the cover already is the show).
 *
 * The URLs are the shapes the catalog was measured to serve: an item without a
 * picture inherits the show's URL (rss_parser.py), Radio France stamps the
 * show's image with a cache-busting query its items repeat without, and audio
 * hosts give a per-episode picture its own path.
 *
 * Goes red if the same image under a different query reads as the episode's,
 * if an inherited or empty image reads as the episode's, or if a picture of its
 * own reads as the show's.
 */
import { describe, it, expect } from 'vitest';
import { episodeHasOwnImage, sameImage } from '@/utils/podcastArtwork';

const SHOW = 'https://www.radiofrance.fr/s3/cruiser-production/2022/06/a7fc766f/1400x1400_les-pieds-sur-terre.jpg?1791066081785';

function episode(image, showImage = SHOW) {
  return { image_url: image, podcast: { name: 'Les pieds sur terre', image_url: showImage } };
}

describe('episodeHasOwnImage', () => {
  it('reads the show’s own URL as the show’s', () => {
    expect(episodeHasOwnImage(episode(SHOW))).toBe(false);
  });

  it('reads the show’s image without its cache-busting query as the show’s', () => {
    expect(episodeHasOwnImage(episode(SHOW.split('?')[0]))).toBe(false);
  });

  it('reads an empty episode image as no picture of its own', () => {
    expect(episodeHasOwnImage(episode(''))).toBe(false);
    expect(episodeHasOwnImage(episode(null))).toBe(false);
    expect(episodeHasOwnImage({ podcast: { image_url: SHOW } })).toBe(false);
  });

  it('reads another path as the episode’s own picture', () => {
    const own = 'https://static.audiomeans.fr/img/episode/dce760bc-d454-494b-ac25-4cd053c85fb7.jpg';
    expect(episodeHasOwnImage(episode(own, 'https://static.audiomeans.fr/img/podcast/f6476049.jpg'))).toBe(true);
  });

  it('reads a picture with no show image to compare against as the episode’s', () => {
    expect(episodeHasOwnImage(episode('https://example.org/episode.jpg', ''))).toBe(true);
  });

  it('answers no episode at all as no picture of its own', () => {
    expect(episodeHasOwnImage(null)).toBe(false);
  });
});

describe('sameImage', () => {
  it('ignores the query and the fragment, never the path', () => {
    expect(sameImage('https://cdn.example/a.jpg?w=600', 'https://cdn.example/a.jpg#x')).toBe(true);
    expect(sameImage('https://cdn.example/a.jpg', 'https://cdn.example/b.jpg')).toBe(false);
    expect(sameImage('https://one.example/a.jpg', 'https://two.example/a.jpg')).toBe(false);
  });

  it('compares relative URLs too, and never calls two empties the same', () => {
    expect(sameImage('/img/show.jpg?w=300', '/img/show.jpg?w=600')).toBe(true);
    expect(sameImage('/img/show.jpg', '/img/episode.jpg')).toBe(false);
    expect(sameImage('', '')).toBe(false);
  });
});
