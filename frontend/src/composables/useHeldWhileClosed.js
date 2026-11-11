import { computed, shallowRef, toValue, watchEffect } from 'vue';

/**
 * What a collapsing section draws: the live value while it is open, and the
 * last value it showed open once it closes. A Collapse keeps its content on
 * screen while it folds, and the change that closes it (a toggle sending 0,
 * 'none', an emptied list; the work that ended) usually rewrites that content
 * in the same tick, so the content would jump just before it disappears.
 *
 * Order-free within a tick: the hold is taken by a pre-flush effect, which
 * sees the section already closed when both move together.
 *
 * @param {import('vue').MaybeRefOrGetter<boolean>} open
 * @param {import('vue').MaybeRefOrGetter<any>} live
 * @returns {import('vue').ComputedRef<any>}
 */
export function useHeldWhileClosed(open, live) {
  const held = shallowRef(toValue(live));
  watchEffect(() => {
    if (toValue(open)) held.value = toValue(live);
  });
  return computed(() => (toValue(open) ? toValue(live) : held.value));
}
