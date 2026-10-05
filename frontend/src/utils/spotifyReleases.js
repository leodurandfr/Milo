// frontend/src/utils/spotifyReleases.js
// How an artist's release reads on its card in the Spotify browser.

// The locale key naming each kind of release, as /api/spotify/artists files it.
const RELEASE_TYPE_KEYS = {
  album: 'spotify.album',
  single: 'spotify.single',
  compilation: 'spotify.compilation',
};

/**
 * The line under a release's title, as Spotify's desktop app writes it: its
 * year then what it is ("2025 · Album"), and for the latest release that fact
 * first ("Latest release · Single"). Whatever is not known is left out.
 *
 * @param {{ year?: string|null, release_type?: string|null, latest?: boolean }} release
 * @param {(key: string) => string} t
 */
export function releaseSubtitle(release, t) {
  const kind = RELEASE_TYPE_KEYS[release.release_type];
  const label = kind ? t(kind) : null;
  const parts = release.latest
    ? [t('spotify.latestRelease'), label ?? release.year]
    : [release.year, label];
  return parts.filter(Boolean).join(' · ');
}

/** A release as a card draws it: SpotifyCard reads its byline from `subtitle`. */
export function releaseCard(release, t) {
  return { ...release, subtitle: releaseSubtitle(release, t) };
}
