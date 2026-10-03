import { ref, computed, onUnmounted } from 'vue';

// How many Modals are open, across every instance (App's three, the Music
// Library's playlist modals, any added later).
const openCount = ref(0);

/** True while any Modal is open — the dock must never sit under one. */
export const anyModalOpen = computed(() => openCount.value > 0);

/**
 * Reports one Modal's open state into the shared count, and withdraws it if the
 * Modal unmounts while open.
 */
export function useModalPresence() {
  let counted = false;

  function setOpen(open) {
    if (open === counted) return;
    counted = open;
    openCount.value += open ? 1 : -1;
  }

  onUnmounted(() => setOpen(false));

  return { setOpen };
}
