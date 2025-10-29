// frontend/src/composables/useScreenActivity.js
/**
 * Composable pour détecter l'activité écran (touch, souris, clavier)
 * et notifier le backend pour reset le timer de mise en veille.
 *
 * Fonctionne avec tous les types d'écrans (tactiles ou non).
 */
import { onMounted, onUnmounted } from 'vue';
import axios from 'axios';

let lastActivityTime = 0;
const THROTTLE_DELAY = 2000; // 2 secondes minimum entre chaque notification

export function useScreenActivity() {
  const notifyActivity = async () => {
    const now = Date.now();

    // Throttle: ignorer si moins de 2s depuis la dernière notification
    if (now - lastActivityTime < THROTTLE_DELAY) {
      return;
    }

    lastActivityTime = now;

    try {
      await axios.post('/api/settings/screen-activity');
    } catch (error) {
      // Silent fail - ne pas polluer la console avec ces erreurs
      // Le backend log déjà les erreurs si nécessaire
      console.debug('Failed to notify screen activity:', error);
    }
  };

  const setupListeners = () => {
    // Événements tactiles (mobile/tablette/écran tactile)
    document.addEventListener('touchstart', notifyActivity, { passive: true });

    // Événements souris (desktop/écran tactile avec curseur)
    document.addEventListener('mousedown', notifyActivity, { passive: true });
    document.addEventListener('mousemove', notifyActivity, { passive: true });

    // Événements clavier (optionnel, pour accessibilité)
    document.addEventListener('keydown', notifyActivity, { passive: true });
  };

  const removeListeners = () => {
    document.removeEventListener('touchstart', notifyActivity);
    document.removeEventListener('mousedown', notifyActivity);
    document.removeEventListener('mousemove', notifyActivity);
    document.removeEventListener('keydown', notifyActivity);
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
