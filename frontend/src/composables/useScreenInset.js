// frontend/src/composables/useScreenInset.js
import { ref, computed, watch } from 'vue';
import { useSettingsStore } from '@/stores/settingsStore';
import { useHardwareConfig } from '@/composables/useHardwareConfig';
import { isKiosk } from '@/utils/kiosk';
import { screenInsetPx } from '@/utils/screenInset';

/**
 * The kiosk's panel geometry, written on <html> so a layer teleported out of
 * the app (a dropdown, a modal) reads it as well:
 *
 * - `--kiosk-zoom`: the interface scale, Chromium's page zoom (milo-kiosk-zoom).
 *   It is read from the page, not the ui_scale setting: the setting changes
 *   first and the zoom only once the kiosk has restarted, which reloads the
 *   page, so the ratio is fixed for the page's life.
 * - `--screen-inset`: the strip of the panel the enclosure hides, from
 *   `screen.edge_inset_mm` and the panel's pitch (hardware-info). A panel of
 *   unknown pitch takes none.
 *
 * Neither is written on a remote browser, which keeps the declared defaults (1
 * and 0px).
 */

// Module state: one document. The CSS px the inset covers now, 0 when none.
const insetPx = ref(0);

export function useScreenInset() {
  return { insetPx };
}

/**
 * App.vue, once. `enabled` drops the inset on a route that opts out of the app's
 * furniture. Returns `isKnown`: whether both inputs have been read, so the boot
 * screen can stay up until the inset is drawn rather than move the first frame.
 */
export function mountScreenInset({ enabled }) {
  if (!isKiosk()) return { isKnown: computed(() => true) };
  const settingsStore = useSettingsStore();
  const { hardwareInfo } = useHardwareConfig();
  const root = document.documentElement;
  const ratio = window.devicePixelRatio;

  root.style.setProperty('--kiosk-zoom', String(ratio));

  const wanted = computed(() => (enabled.value
    ? screenInsetPx(
      settingsStore.screenEdgeInset.edge_inset_mm,
      hardwareInfo.value?.screen_pixels_per_mm ?? null,
      ratio,
    )
    : null));

  watch(wanted, (px) => {
    if (px) root.style.setProperty('--screen-inset', `${px}px`);
    else root.style.removeProperty('--screen-inset');
    insetPx.value = px ?? 0;
  }, { immediate: true });

  // A panel of unknown pitch takes no inset whatever the setting says.
  return {
    isKnown: computed(() => hardwareInfo.value !== null
      && (!hardwareInfo.value.screen_pixels_per_mm || settingsStore.isLoaded)),
  };
}
