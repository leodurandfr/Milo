// frontend/src/composables/useAsyncData.js
// Generic async loading helper: wraps any async callback with loading/error state.
import { ref } from 'vue';
import { logger } from '@/services/logger';

/**
 * Wraps an async callback with loading/error state management.
 *
 * @param {() => Promise<void>} callback  - Async function that fetches data and
 *        writes results into its own refs (closed over by the caller). Errors
 *        thrown here are caught, logged, and exposed via the returned error ref.
 * @param {object}            [options]
 * @param {string}            [options.logTag='component'] - Logger category for error output.
 * @returns {{ loading: Ref<boolean>, error: Ref<Error|null>, execute: () => Promise<void> }}
 */
export function useAsyncData(callback, { logTag = 'component' } = {}) {
  const loading = ref(false);
  const error = ref(null);

  async function execute() {
    loading.value = true;
    error.value = null;

    try {
      await callback();
    } catch (err) {
      error.value = err;
      logger.error(logTag, 'Async data load failed', err);
    } finally {
      loading.value = false;
    }
  }

  return { loading, error, execute };
}
