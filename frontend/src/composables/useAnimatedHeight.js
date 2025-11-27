// frontend/src/composables/useAnimatedHeight.js
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue';

/**
 * Composable for animating container height based on content changes.
 * Uses ResizeObserver to detect content size changes and applies spring animations.
 *
 * @param {Ref} contentRef - Reference to the inner content element to observe
 * @param {Object} options - Configuration options
 * @param {string} options.initialHeight - Initial height value (default: 'auto')
 * @param {number} options.threshold - Minimum change in pixels to trigger animation (default: 2)
 * @param {boolean} options.skipFirstResize - Skip animation on first resize (default: true)
 * @param {Function} options.getExtraHeight - Function that returns extra height to add (e.g., padding)
 * @param {Function} options.getMaxHeight - Function that returns the max available height
 * @returns {Object} - { containerHeight, resetFirstResize, setupObserver, disconnectObserver }
 */
export function useAnimatedHeight(contentRef, options = {}) {
  const {
    initialHeight = 'auto',
    threshold = 2,
    skipFirstResize = true,
    getExtraHeight = null,
    getMaxHeight = null
  } = options;

  const containerHeight = ref(initialHeight);
  let resizeObserver = null;
  let isFirstResize = skipFirstResize;

  function setupObserver() {
    if (resizeObserver) {
      resizeObserver.disconnect();
    }

    resizeObserver = new ResizeObserver(entries => {
      if (!entries[0]) return;

      // Get content height
      let newHeight = entries[0].contentRect.height;

      // Add extra height (e.g., parent padding)
      if (getExtraHeight) {
        newHeight += getExtraHeight();
      }

      // Clamp to max available height if provided
      if (getMaxHeight) {
        const maxAvailable = getMaxHeight();
        if (maxAvailable && maxAvailable < Infinity) {
          newHeight = Math.min(newHeight, maxAvailable);
        }
      }

      // First resize: initialize without transition
      if (isFirstResize) {
        containerHeight.value = `${newHeight}px`;
        isFirstResize = false;
        return;
      }

      // Threshold to avoid micro-adjustments (jitter)
      const currentHeight = parseFloat(containerHeight.value) || 0;
      if (Math.abs(newHeight - currentHeight) > threshold) {
        containerHeight.value = `${newHeight}px`;
      }
    });

    if (contentRef.value) {
      resizeObserver.observe(contentRef.value);
    }
  }

  function disconnectObserver() {
    if (resizeObserver) {
      resizeObserver.disconnect();
      resizeObserver = null;
    }
  }

  function resetFirstResize() {
    isFirstResize = true;
  }

  // Watch for ref changes (e.g., when v-if toggles the element)
  watch(contentRef, (newRef) => {
    if (newRef) {
      setupObserver();
    } else {
      disconnectObserver();
    }
  });

  onMounted(async () => {
    await nextTick();
    if (contentRef.value) {
      setupObserver();
    }
  });

  onUnmounted(() => {
    disconnectObserver();
  });

  return {
    containerHeight,
    resetFirstResize,
    setupObserver,
    disconnectObserver
  };
}
