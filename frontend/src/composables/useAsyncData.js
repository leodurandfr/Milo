// frontend/src/composables/useAsyncData.js
// Generic async loading helper: wraps any async callback with a loading state.
import { ref } from 'vue';
import { logger } from '@/services/logger';

/**
 * Wraps an async callback with a loading state.
 *
 * @param {() => Promise<void>} callback  - Async function that fetches data and
 *        writes results into its own refs (closed over by the caller). Errors
 *        thrown here are caught and logged.
 * @param {object}            [options]
 * @param {string}            [options.logTag='component'] - Logger category for error output.
 * @returns {{ loading: Ref<boolean>, execute: () => Promise<void> }}
 */
export function useAsyncData(callback, { logTag = 'component' } = {}) {
  const loading = ref(false);

  async function execute() {
    loading.value = true;

    try {
      await callback();
    } catch (err) {
      logger.error(logTag, 'Async data load failed', err);
    } finally {
      loading.value = false;
    }
  }

  return { loading, execute };
}
