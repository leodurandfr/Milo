import { computed, ref, toValue, watch } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

/**
 * The play button of a detail page's header (DetailHeader): pause while what
 * the page lists is what plays, resume it where it was once paused, start it
 * otherwise.
 *
 * The spinner is this button's own: it turns from a press until the page's
 * context plays, and never for a load nobody asked for here — a track change
 * pressed on the player passes through `loading` with this page still current.
 *
 * @param {string} source the audio source the page browses
 * @param {import('vue').MaybeRefOrGetter<boolean>} isCurrent whether the
 *   selected source's queue (live, or its resume point) is this page's context
 * @param {(shuffle: boolean) => Promise<boolean>} start plays the page's
 *   context from the top, or shuffled; resolves false when nothing was started
 */
export function useDetailPlayback(source, isCurrent, start) {
  const unifiedStore = useUnifiedAudioStore();

  const current = computed(() => unifiedStore.systemState.source === source && toValue(isCurrent));
  const phase = computed(() => (current.value ? unifiedStore.systemState.session?.phase ?? null : null));
  const playing = computed(() => phase.value === 'playing');

  const pressed = ref(false);
  // A load seen since the press ends the wait however it ends: playing, or a
  // failure that never gets there (the session ends, or settles paused).
  let loadSeen = false;
  watch(phase, (now) => {
    if (!pressed.value) return;
    if (now === 'loading') loadSeen = true;
    else if (now === 'playing' || loadSeen) pressed.value = false;
  });

  async function settle(command) {
    pressed.value = true;
    loadSeen = false;
    if (!(await command())) pressed.value = false;
  }

  function play() {
    if (pressed.value) return;
    const resumable = current.value && unifiedStore.systemState.controls.includes('resume');
    settle(resumable ? () => unifiedStore.sendCommand(source, 'resume') : () => start(false));
  }

  // Always a fresh shuffled queue, even over the page's own: asking again is
  // how a new order is drawn.
  function shuffle() {
    if (pressed.value) return;
    settle(() => start(true));
  }

  function pause() {
    unifiedStore.sendCommand(source, 'pause');
  }

  return { playing, loading: pressed, play, pause, shuffle };
}
