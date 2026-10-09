import { ref, computed, toValue } from 'vue';
import { useTimer } from '@/composables/useTimer';

// How long a picture may keep a profile undescribed before its initial stands
// in: Spotify describes a new profile within its 10 s request, and a picture
// that comes later still fades in over the initial.
const PICTURE_WAIT_MS = 4000;

/**
 * Whether Spotify has not described a kept profile yet: a profile kept a
 * moment ago has no Spotify name, and may still get a picture. Shared by the
 * avatar (its skeleton) and the profile row (its text skeletons), so both
 * give up waiting at the same moment.
 *
 * `profile` is a getter or a ref of `{ spotify_name, avatar_url }`.
 */
export function useProfileDescribing(profile) {
  const waited = ref(false);
  useTimer().setTimeout(() => { waited.value = true; }, PICTURE_WAIT_MS);

  const describing = computed(() => {
    const p = toValue(profile);
    return !p.avatar_url && p.spotify_name == null && !waited.value;
  });

  return { describing, waited };
}
