// frontend/src/milo-down-main.js
/**
 * Entry point of the rescue page (milo-down.html): "Milō is restarting…", then,
 * `STUCK_AFTER_MS` after this tab first found it gone — counted again from
 * every probe the unit did not answer at all — and while nginx still answers
 * for it, "Milō no longer starts", in the language the page that left
 * for it was in. It asks /api/health every `PROBE_INTERVAL_MS` and goes back to
 * the app the first time it answers.
 *
 * Deliberately not main.js: the app would open a WebSocket and read the
 * settings from the backend this page is here because it is gone. No Vue
 * either — two lines of text, and the less this page loads the likelier it is
 * to show.
 */
import { apiCall } from './services/apiCall';
import { i18n } from './services/i18n';
import {
  PROBE_INTERVAL_MS, rescueLanguage, rescuePhase, rescueSince, rescueSinceAfter,
} from './utils/backendDown';
import './assets/styles/reset.css';
import './assets/styles/design-system.css';
import './assets/styles/milo-down.css';

const KEYS = {
  restarting: { title: 'app.restartingTitle', description: 'app.restartingDescription' },
  stuck: { title: 'app.stuckTitle', description: 'app.stuckDescription' },
};

let since = rescueSince(Date.now());
// What the last probe answered: none yet says "restarting".
let lastStatus = null;

function render() {
  const keys = KEYS[rescuePhase(Date.now() - since, lastStatus)];
  for (const [part, key] of Object.entries(keys)) {
    document.querySelector(`[data-milo-down="${part}"]`).textContent = i18n.t(key);
  }
}

async function probe() {
  const { ok, error } = await apiCall.get('/api/health', {
    category: 'boot',
    message: 'Milō is not answering yet',
    logLevel: 'debug',
  });
  if (ok) {
    window.location.replace('/');
    return;
  }
  lastStatus = error?.status ?? null;
  since = rescueSinceAfter(since, Date.now(), lastStatus);
  render();
  setTimeout(probe, PROBE_INTERVAL_MS);
}

async function initRescuePage() {
  // The theme setting is the backend's too: the screen's own preference stands
  // in. Set here rather than through useTheme, which would pull Vue and the
  // settings store into the one page that must load when nothing else does.
  document.documentElement.dataset.theme = window.matchMedia('(prefers-color-scheme: dark)').matches
    ? 'dark'
    : 'light';
  await i18n.loadTranslations('english');
  const language = rescueLanguage(
    window.location.search,
    navigator.languages,
    i18n.getAvailableLanguages().map(({ code }) => code),
  );
  await i18n.handleLanguageChanged(language);
  i18n.applyDocumentLanguage();
  render();
  setTimeout(probe, PROBE_INTERVAL_MS);
}

initRescuePage();
