import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { releaseCard } from '@/utils/spotifyReleases';

/**
 * The list of an artist's discography on screen, the choice of the others,
 * and its releases as cards: the artist page's section and the discography
 * page show the same.
 *
 * @param {() => Array<{id: string, title: string, items: Array}>} groups
 *   the discography's lists, as /api/spotify/artists files them
 * @param {() => string|undefined} groupId the list picked; the first one when
 *   it is not (or no longer) there
 */
export function useSpotifyDiscography(groups, groupId) {
  const { t } = useI18n();
  const current = computed(() => groups().find((g) => g.id === groupId()) ?? groups()[0] ?? null);
  const options = computed(() => groups().map((g) => ({ value: g.id, label: g.title })));
  const cards = computed(() => (current.value?.items ?? []).map((release) => releaseCard(release, t)));
  return { current, options, cards };
}
