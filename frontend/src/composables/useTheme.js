// frontend/src/composables/useTheme.js
import { ref, computed, watch } from 'vue';
import { useSettingsStore } from '@/stores/settingsStore';
import { useTimer } from '@/composables/useTimer';
import { isDaytime } from '@/utils/daylight';

/**
 * Light or dark: which `--color-*` block of design-system.css is in force.
 *
 * Every browser showing Milō follows `screen.theme`, the kiosk and a phone or a
 * Mac alike — `auto` meaning dark from sunset to sunrise at the timezone's
 * coordinates. The system's own theme is never read: the setting is Milō's.
 *
 * The answer is written as `data-theme` on <html>, which is the only thing CSS
 * reads; `isDark` is for the few places a script has to choose (a component
 * never tests the theme in CSS, it consumes the tokens).
 */

const AUTO_REFRESH_MS = 60 * 1000;
// Past the fade's 400 ms (design-system.css, .theme-ghost): the copy is
// removed on its animationend, this only bounds a copy whose animation never ran.
const GHOST_TIMEOUT_MS = 1000;

let ghost = null;

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

function removeGhost() {
  ghost?.remove();
  ghost = null;
}

// A copy starts unscrolled and with its canvases blank. A detached copy has no
// box to scroll, so this runs once the copy is in the document.
function copyLiveState(live, copy) {
  const from = live.querySelectorAll('*');
  copy.querySelectorAll('*').forEach((el, i) => {
    const src = from[i];
    if (src.scrollTop || src.scrollLeft) el.scrollTo(src.scrollLeft, src.scrollTop);
    if (src instanceof HTMLCanvasElement && src.width && src.height) {
      el.getContext('2d')?.drawImage(src, 0, 0);
    }
  });
}

/**
 * Whether this engine draws backdrop-filter only in its compositor, so a view
 * transition's captures lose it: WebKit, the one engine that still parses the
 * prefixed property (Chromium answers false, measured on the kiosk's Chromium 149).
 */
function backdropIsComposited() {
  return globalThis.CSS?.supports?.('-webkit-backdrop-filter', 'none') ?? false;
}

/**
 * Fade the screen out of `previous`: a copy of the page, held in that theme by
 * its own color-scheme (which every role follows), laid over the live page
 * and faded out while the live page is already in the new theme.
 *
 * WebKit only. A view transition fades still captures of the page, and WebKit
 * leaves every backdrop-filter out of them — the glass went flat for the whole
 * fade in Safari. The copy is live DOM, so its glass still blurs (what is under
 * it in the copy, since the fading layer is the glass's backdrop root), and
 * nothing of the live page is ever captured. It is not used everywhere because
 * it costs the kiosk: copying and restyling ~2200 nodes left 6-14 frames in
 * 700 ms against 67-133 for the view transition, measured on the Pi's GPU.
 *
 * Every child of <body> is copied, not #app alone: a Dropdown's menu is
 * teleported beside it.
 */
function fadeOutTheme(previous, timer) {
  removeGhost();
  const layer = document.createElement('div');
  layer.className = 'theme-ghost';
  layer.style.colorScheme = previous;
  layer.setAttribute('aria-hidden', 'true');
  layer.inert = true;
  const pairs = [];
  for (const live of document.body.children) {
    if (live.tagName === 'SCRIPT' || live.id === 'boot-screen') continue;
    const copy = live.cloneNode(true);
    // A copied iframe loads its document again (the gallery's whole canvas
    // app): an empty box in its place lets the live one show through, and
    // keeps the copy's elements in step with the live ones for copyLiveState.
    copy.querySelectorAll('iframe').forEach((frame) => {
      const box = document.createElement('div');
      for (const { name, value } of frame.attributes) {
        if (name !== 'src' && name !== 'srcdoc') box.setAttribute(name, value);
      }
      frame.replaceWith(box);
    });
    layer.append(copy);
    pairs.push([live, copy]);
  }
  // A descendant's animationend bubbles up to the layer too.
  layer.addEventListener('animationend', (event) => {
    if (event.target === layer && ghost === layer) removeGhost();
  });
  timer.setTimeout(() => {
    if (ghost === layer) removeGhost();
  }, GHOST_TIMEOUT_MS);
  document.body.append(layer);
  ghost = layer;
  pairs.forEach(([live, copy]) => copyLiveState(live, copy));
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

  // Read afresh on every tick rather than offset from a start time: the Pi has
  // no RTC, and the clock steps forward mid-boot when NTP answers.
  const now = ref(new Date());
  timer.setInterval(() => { now.value = new Date(); }, AUTO_REFRESH_MS);

  const wanted = computed(() => {
    const chosen = settingsStore.screenTheme.theme;
    if (chosen === 'light' || chosen === 'dark') return chosen;
    const { latitude, longitude } = settingsStore.daylightLocation;
    return isDaytime(now.value, latitude, longitude) ? 'light' : 'dark';
  });

  // A view transition fades the screen as one image, so an animation in flight
  // at sunset keeps its own transition; without the API the swap is instant.
  // WebKit fades a copy instead (fadeOutTheme). A hidden tab (a phone in a
  // pocket at sunset) has nothing to fade, and the browser refuses the
  // transition by rejecting `ready`; one hidden mid-fade is aborted the same
  // way. The theme lands either way — only the fade is lost. Reduced motion
  // asks for no fade at all.
  watch(wanted, (next) => {
    if (next === theme.value) return;
    const previous = theme.value;
    const visible = document.visibilityState === 'visible';
    const still = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    if (previous === null || !animate.value || !visible || still) {
      applyTheme(next);
    } else if (backdropIsComposited()) {
      fadeOutTheme(previous, timer);
      applyTheme(next);
    } else if (document.startViewTransition) {
      const transition = document.startViewTransition(() => applyTheme(next));
      transition.ready.catch(() => {});
    } else {
      applyTheme(next);
    }
  }, { immediate: true });

  return { isDark };
}
