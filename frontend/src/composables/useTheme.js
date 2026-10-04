// frontend/src/composables/useTheme.js
import { ref, computed, watch, onUnmounted } from 'vue';
import { useSettingsStore } from '@/stores/settingsStore';
import { useTimer } from '@/composables/useTimer';
import { isKiosk } from '@/utils/kiosk';
import { isDaytime } from '@/utils/daylight';

/**
 * Light or dark: which `--color-*` block of design-system.css is in force.
 *
 * The kiosk follows `screen.theme` — `auto` meaning dark from sunset to sunrise
 * at the timezone's coordinates. Every other browser follows its own system
 * theme and never reads the setting: a phone in the room at night has already
 * said what it wants.
 *
 * The answer is written as `data-theme` on <html>, which is the only thing CSS
 * reads; `isDark` is for the few places a script has to choose (a component
 * never tests the theme in CSS, it consumes the tokens).
 */

const AUTO_REFRESH_MS = 60 * 1000;

// Module state: one document, one theme, read by every useTheme() caller.
const theme = ref(null);
const isDark = computed(() => theme.value === 'dark');

export function useTheme() {
  return { isDark };
}

/**
 * Put a theme on the document, with no fade. The gallery canvas calls this
 * directly to show a component in either theme.
 */
export function applyTheme(next) {
  theme.value = next;
  document.documentElement.dataset.theme = next;
}

/**
 * Decide the theme and keep it current. Called once, from App.vue's setup.
 *
 * `animate` holds the cross-fade back until the app is on screen: the first
 * theme lands under the boot screen, and a setting arriving with the first
 * settings load is not a change anybody watched happen.
 */
export function mountTheme({ animate }) {
  const settingsStore = useSettingsStore();
  const timer = useTimer();
  const kiosk = isKiosk();

  const systemDark = ref(false);
  let media = null;
  const onSystemChange = (event) => { systemDark.value = event.matches; };
  if (!kiosk && window.matchMedia) {
    media = window.matchMedia('(prefers-color-scheme: dark)');
    systemDark.value = media.matches;
    media.addEventListener('change', onSystemChange);
  }

  // Read afresh on every tick rather than offset from a start time: the Pi has
  // no RTC, and the clock steps forward mid-boot when NTP answers.
  const now = ref(new Date());
  if (kiosk) timer.setInterval(() => { now.value = new Date(); }, AUTO_REFRESH_MS);

  const wanted = computed(() => {
    if (!kiosk) return systemDark.value ? 'dark' : 'light';
    const chosen = settingsStore.screenTheme.theme;
    if (chosen === 'light' || chosen === 'dark') return chosen;
    const { latitude, longitude } = settingsStore.daylightLocation;
    return isDaytime(now.value, latitude, longitude) ? 'light' : 'dark';
  });

  // A view transition fades the screen as one image, so an animation in flight
  // at sunset keeps its own transition; without the API the swap is instant.
  watch(wanted, (next) => {
    if (next === theme.value) return;
    if (theme.value !== null && animate.value && document.startViewTransition) {
      document.startViewTransition(() => applyTheme(next));
    } else {
      applyTheme(next);
    }
  }, { immediate: true });

  onUnmounted(() => media?.removeEventListener('change', onSystemChange));

  return { isDark };
}
