// frontend/src/composables/useDarkSurface.js
// Which tone the surface currently filling the screen is painted in.
//
// App.vue mounts the fixed overlays (VolumeBar) once, above router-view, so they
// outlive every view and can learn nothing from a parent — and no element can
// read a colour back out of what happens to be painted behind it. So the two
// full-bleed dark surfaces declare themselves here instead: AudioScreensaver
// (black under dimmed artwork) and LyricsView (--color-background-contrast).
// Everything else the app shows is a light surface, including the modals, whose
// scrim only blurs whatever is beneath — which is why a modal opened over Lyrics
// needs no entry of its own: the view under it is still mounted and still counts.
//
// A counter rather than a flag, because the two can overlap — the screensaver
// rises over an open Lyrics view — and a flag would go light again on the first
// of the two to close.
import { computed, onScopeDispose, ref, toValue, watch } from 'vue';

const darkSurfaces = ref(0);

/** Whether what fills the screen right now is a dark surface. */
export function useDarkSurface() {
  return { isDarkSurface: computed(() => darkSurfaces.value > 0) };
}

/**
 * Declare the calling component a full-bleed dark surface while `active` holds,
 * releasing on unmount whatever it holds then.
 *
 * @param {boolean|import('vue').Ref<boolean>|(() => boolean)} [active=true]
 *   Defaults to the component's whole mounted life (LyricsView, mounted only
 *   while open); pass the visibility flag for a component that stays mounted
 *   while hidden (AudioScreensaver).
 */
export function markDarkSurface(active = true) {
  let counted = false;

  const count = (on) => {
    if (on === counted) return;
    counted = on;
    darkSurfaces.value += on ? 1 : -1;
  };

  watch(() => Boolean(toValue(active)), count, { immediate: true });
  onScopeDispose(() => count(false));
}
