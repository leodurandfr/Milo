// frontend/src/composables/useScreenActivity.js
/**
 * Composable to detect screen activity (touch, mouse, keyboard)
 * and notify the backend to reset the sleep timer.
 *
 * Works with all screen types (touch or not).
 */
import { onMounted, onUnmounted } from 'vue';
import axios from 'axios';

let lastActivityTime = 0;
const THROTTLE_DELAY = 2000; // Minimum 2 seconds between each notification

export function useScreenActivity() {
  const notifyActivity = async () => {
    const now = Date.now();

    // Throttle: ignore if less than 2s since last notification
    if (now - lastActivityTime < THROTTLE_DELAY) {
      return;
    }

    lastActivityTime = now;

    try {
      await axios.post('/api/settings/screen-activity');
    } catch (error) {
      // Silent fail - do not pollute the console with these errors
      // The backend already logs errors if necessary
      console.debug('Failed to notify screen activity:', error);
    }
  };

  const setupListeners = () => {
    // Touch events (mobile/tablet/touchscreen)
    document.addEventListener('touchstart', notifyActivity, { passive: true });

    // Mouse events (desktop/touchscreen with cursor)
    document.addEventListener('mousedown', notifyActivity, { passive: true });
    document.addEventListener('mousemove', notifyActivity, { passive: true });

    // Keyboard events (optional, for accessibility)
    document.addEventListener('keydown', notifyActivity, { passive: true });
  };

  const removeListeners = () => {
    document.removeEventListener('touchstart', notifyActivity, { passive: true });
    document.removeEventListener('mousedown', notifyActivity, { passive: true });
    document.removeEventListener('mousemove', notifyActivity, { passive: true });
    document.removeEventListener('keydown', notifyActivity, { passive: true });
  };

  onMounted(() => {
    setupListeners();
  });

  onUnmounted(() => {
    removeListeners();
  });

  return {
    notifyActivity
  };
}