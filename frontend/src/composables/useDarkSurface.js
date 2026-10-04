// frontend/src/composables/useDarkSurface.js
// Which tone the surface currently filling the screen is painted in.
//
// App.vue mounts the fixed overlays (VolumeBar) once, above router-view, so they
// outlive every view and can learn nothing from a parent — and no element can
// read a colour back out of what happens to be painted behind it. So the answer
// is assembled here: the dark theme paints every surface dark, and in the light
// theme the one full-bleed dark surface, LyricsView (--color-contrast,
// dark in both themes), declares itself. Everything else the light theme shows
// is a light surface, including the modals, whose scrim only blurs whatever is
// beneath — which is why a modal opened over Lyrics needs no entry of its own:
// the view under it is still mounted and still counts.
//
// A counter rather than a flag, because two LyricsView instances can overlap:
// AudioSourceView swaps its slots without `out-in`, so lyrics closed and
// reopened within the leave transition can mount the new view before the old
// one is gone — and a flag would go light when the old one unmounts, under the
// lyrics now open. A counter is right whichever order the two land in.
import { computed, onScopeDispose, ref } from 'vue';
import { useTheme } from '@/composables/useTheme';

const darkSurfaces = ref(0);

/** Whether what fills the screen right now is a dark surface. */
export function useDarkSurface() {
  const { isDark } = useTheme();
  return { isDarkSurface: computed(() => isDark.value || darkSurfaces.value > 0) };
}

/**
 * Declare the calling component a full-bleed dark surface for its whole mounted
 * life (LyricsView, mounted only while open).
 */
export function markDarkSurface() {
  darkSurfaces.value += 1;
  onScopeDispose(() => { darkSurfaces.value -= 1; });
}
