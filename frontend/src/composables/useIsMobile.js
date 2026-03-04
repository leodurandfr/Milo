// Shared mobile detection composable (module-level singleton)
import { ref, onUnmounted, readonly } from 'vue';

const MOBILE_QUERY = '(max-aspect-ratio: 4/3)';

// Module-level shared state (single listener for entire app)
// Initialize eagerly to avoid first-render flash on mobile
const mediaQuery =
  typeof window !== 'undefined' ? window.matchMedia(MOBILE_QUERY) : null;
const isMobile = ref(mediaQuery?.matches ?? false);
let listenerCount = 0;

function update() {
  isMobile.value = mediaQuery?.matches ?? false;
}

export function useIsMobile() {
  if (listenerCount === 0 && mediaQuery) {
    mediaQuery.addEventListener('change', update);
  }
  listenerCount++;

  onUnmounted(() => {
    listenerCount--;
    if (listenerCount === 0 && mediaQuery) {
      mediaQuery.removeEventListener('change', update);
    }
  });

  return { isMobile: readonly(isMobile) };
}
