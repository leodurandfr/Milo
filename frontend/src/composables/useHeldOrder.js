import { shallowRef, watch } from 'vue';

/**
 * An order shown ahead of the state that confirms it: a dropped row stays
 * where it was dropped instead of jumping back for a round trip.
 *
 * The held order is dropped when `shape` moves (the state it waited for, or
 * any other change to the list), and when its request answers false — unless
 * a newer drop has replaced it since.
 *
 * @param {() => string} shape - What identifies the list as the state draws it
 * @returns {{held: import('vue').ShallowRef<Array|null>, hold: (order: Array|null, request: Promise<boolean>) => void}}
 */
export function useHeldOrder(shape) {
  // Shallow so `held.value` stays the array handed in: a deep ref returns a
  // proxy of it, and the identity check below would never match.
  const held = shallowRef(null);
  watch(shape, () => {
    held.value = null;
  });

  function hold(order, request) {
    held.value = order;
    request.then((ok) => {
      if (!ok && held.value === order) held.value = null;
    });
  }

  return { held, hold };
}
