// frontend/src/composables/useLoadingIndicator.js
// The spinner of a player's main button, from its session's phase. A track
// change passes through `loading` too briefly to show one, so it waits a
// second (useDelayedFlag); a session opening is a real wait (a CD spinning
// up), so it shows at once — otherwise a player mounting with its session
// showed pause, then the spinner a second later, then pause again.
import { computed, toValue } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useDelayedFlag } from '@/composables/useDelayedFlag';

/**
 * @param {import('vue').MaybeRefOrGetter<string|null>} phase - the player's session phase
 * @returns {import('vue').ComputedRef<boolean>}
 */
export function useLoadingIndicator(phase) {
  const unifiedStore = useUnifiedAudioStore();
  const loading = () => toValue(phase) === 'loading';
  const longLoad = useDelayedFlag(loading);
  return computed(() => loading() && (unifiedStore.sessionOpening || longLoad.value));
}
