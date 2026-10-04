// frontend/src/composables/useAutoPlayer.js
// Opens a browser source's full player after a stretch of inactivity on the
// unit's own screen. Mounted once, by MainView.
//
// The countdown runs only while every condition below holds, and stops the
// moment one falls; it starts over from zero when they all hold again. Its one
// effect is expand() — nothing here ever collapses the player: a pause leaves
// it on screen, and only the back button (or an ending with nothing to resume,
// BrowserSourceViews) returns to the navigation.
import { computed, watch, onUnmounted } from 'vue';
import { useTimer } from '@/composables/useTimer';
import { usePlayerExpansion } from '@/composables/usePlayerExpansion';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSettingsStore } from '@/stores/settingsStore';
import { useLyricsStore } from '@/stores/lyricsStore';
import { isKiosk } from '@/utils/kiosk';
import { BROWSER_SOURCES } from '@/constants/audioSources';

/** Minimum ms between two activity events that restart the countdown. */
const ACTIVITY_THROTTLE_MS = 500;

const ACTIVITY_EVENTS = ['pointerdown', 'wheel', 'touchstart'];

const LISTENING_PHASES = ['playing', 'loading'];

export function useAutoPlayer() {
  const unifiedStore = useUnifiedAudioStore();
  const settingsStore = useSettingsStore();
  const lyricsStore = useLyricsStore();
  const { expanded, expand } = usePlayerExpansion();
  const timer = useTimer();

  let countdown = null;
  let lastActivityTime = 0;

  const delayMs = computed(() => settingsStore.screenAutoPlayer.auto_player_delay_seconds * 1000);

  const armed = computed(() => {
    // The Pi's own screen only: a phone or a Mac viewing the UI is held in a
    // hand, and a view changing under it on its own would be a surprise.
    if (!isKiosk()) return false;
    if (!settingsStore.screenAutoPlayer.auto_player_enabled) return false;
    // Only a source with a navigation has a full player to open; every other
    // one already shows its player, or its status card.
    if (!BROWSER_SOURCES.includes(unifiedStore.systemState.source)) return false;
    // Listening, not a pause. A load counts as listening: it is what a track
    // change and a stalled stream look like, and falling through here would
    // restart the countdown at every track, so a delay longer than one track
    // would never be reached.
    if (!LISTENING_PHASES.includes(unifiedStore.systemState.session?.phase)) return false;
    // Lyrics are read without touching the screen; opening the player behind
    // them would change the view they close onto.
    if (lyricsStore.isOpen) return false;
    return !expanded.value;
  });

  function stopCountdown() {
    if (countdown) {
      timer.clear(countdown);
      countdown = null;
    }
  }

  function startCountdown() {
    stopCountdown();
    countdown = timer.setTimeout(() => {
      countdown = null;
      expand();
    }, delayMs.value);
  }

  function handleActivity() {
    const now = Date.now();
    if (now - lastActivityTime < ACTIVITY_THROTTLE_MS) return;
    lastActivityTime = now;
    startCountdown();
  }

  function addActivityListeners() {
    for (const type of ACTIVITY_EVENTS) {
      document.addEventListener(type, handleActivity, { passive: true });
    }
  }

  function removeActivityListeners() {
    for (const type of ACTIVITY_EVENTS) {
      document.removeEventListener(type, handleActivity);
    }
  }

  watch(armed, (isArmed) => {
    if (isArmed) {
      addActivityListeners();
      startCountdown();
    } else {
      removeActivityListeners();
      stopCountdown();
    }
  }, { immediate: true });

  // A new delay counts from now, not from the last touch.
  watch(delayMs, () => {
    if (armed.value) startCountdown();
  });

  onUnmounted(removeActivityListeners);
}
