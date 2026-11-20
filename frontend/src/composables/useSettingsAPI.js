// frontend/src/composables/useSettingsAPI.js
import { ref, onBeforeUnmount } from 'vue';
import { logger } from '@/services/logger';
import { apiCall } from '@/services/apiCall';
import { useTimer } from '@/composables/useTimer';
import { useSettingsStore } from '@/stores/settingsStore';

/**
 * Composable to manage settings API calls with debouncing.
 * Debounced updates still pending on unmount are flushed (sent immediately),
 * never dropped — navigating away must not lose a write.
 *
 * A settings screen moves before its write lands, so a write the server
 * refuses (a 422, a failed save, a backend gone) is undone here, once for every
 * screen: the stored settings are read back into the store, then `resync` puts
 * the screen's own copy back on the store, and `refusal` holds the server's
 * detail for SettingsRefusal to show. The store, not the value before the tap,
 * because another device may have written since. A write whose settings read
 * fails too (the backend is down) leaves the store to the resync the socket's
 * return runs, which is why SettingsRefusal says only that the change was not
 * saved, never that the old value is back.
 *
 * A refusal answered after a newer write to the same endpoint was sent or
 * queued is not undone: the newer write speaks for the screen now, and reading
 * the settings back before it lands would move the control under the user's
 * finger.
 *
 * @param {object} [options]
 * @param {() => void} [options.resync] - for a screen that holds a local copy
 *   of what it shows (a slider's value): put it back on the store. A screen
 *   that draws the store itself needs none.
 */
export function useSettingsAPI({ resync } = {}) {
  const timer = useTimer();
  const settingsStore = useSettingsStore();
  const pending = new Map(); // key → { timerId, endpoint, payload }
  // endpoint → the sequence number of the last write sent to it.
  const lastSent = new Map();
  let sequence = 0;

  // The server's detail for the last write it refused, until the next write.
  const refusal = ref(null);

  /**
   * One PUT, reporting rather than throwing.
   * @returns {Promise<{applied: boolean, refused: string|null}>} `refused` is
   *   the server's detail when the write failed outright and no newer write to
   *   the endpoint was sent or queued since, null otherwise (a cancelled
   *   request is neither applied nor refused).
   */
  async function put(endpoint, payload) {
    const seq = ++sequence;
    lastSent.set(endpoint, seq);
    const result = await apiCall.put(`/api/settings/${endpoint}`, payload, {
      category: 'api',
      message: `Error updating ${endpoint}`
    });
    if (!result.ok) {
      if (!result.error) return { applied: false, refused: null };
      const superseded = lastSent.get(endpoint) !== seq
        || [...pending.values()].some(entry => entry.endpoint === endpoint);
      return { applied: false, refused: superseded ? null : refusalText(result.error.detail) };
    }
    const reloadFailed = result.data?.reload_success === false;
    if (reloadFailed) {
      logger.warn('api', `Setting ${endpoint} saved but runtime reload failed`);
    }
    return { applied: !reloadFailed, refused: null };
  }

  // A route that answers a structured detail (dock-apps: `{message,
  // operations_log}`) says it in its `message`.
  function refusalText(detail) {
    if (typeof detail === 'string') return detail;
    if (typeof detail?.message === 'string') return detail.message;
    return JSON.stringify(detail);
  }

  async function undo(detail) {
    refusal.value = detail;
    await settingsStore.loadAllSettings();
    resync?.();
  }

  /**
   * Send a settings update to the API. Never rejects: a refusal is undone and
   * reported (see above).
   * @param {string} endpoint - API endpoint (e.g., 'volume-limits')
   * @param {object} payload - Data to send
   * @returns {Promise<{applied: boolean}>} `applied` is false when the server
   *   saved the setting but could not apply it at runtime (`reload_success`),
   *   or the request was cancelled or refused.
   */
  async function updateSetting(endpoint, payload) {
    refusal.value = null;
    const { applied, refused } = await put(endpoint, payload);
    if (refused !== null) await undo(refused);
    return { applied };
  }

  function flush(key) {
    const entry = pending.get(key);
    if (!entry) return;
    pending.delete(key);
    timer.clear(entry.timerId);
    put(entry.endpoint, entry.payload).then(({ refused }) => {
      if (refused !== null) undo(refused);
    });
  }

  /**
   * Update with debouncing. A refusal is undone and reported as by
   * updateSetting, including for a write flushed on unmount.
   * @param {string} key - Unique key to identify the timer
   * @param {string} endpoint - API endpoint
   * @param {object} payload - Data to send
   * @param {number} delay - Delay in ms (default: 800ms)
   */
  function debouncedUpdate(key, endpoint, payload, delay = 800) {
    refusal.value = null;
    const existing = pending.get(key);
    if (existing) {
      timer.clear(existing.timerId);
    }

    const timerId = timer.setTimeout(() => flush(key), delay);
    pending.set(key, { timerId, endpoint, payload });
  }

  onBeforeUnmount(() => {
    for (const key of [...pending.keys()]) flush(key);
  });

  return {
    updateSetting,
    debouncedUpdate,
    refusal
  };
}
