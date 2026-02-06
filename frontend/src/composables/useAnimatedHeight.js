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
 * @returns {Object} - { containerHeight, resetFirstResize, requestHeightDelta, setupObserver, disconnectObserver }
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

  // Height lock: prevents ResizeObserver updates during child animations
  let isHeightLocked = false;
  let unlockTimer = null;

  function setupObserver() {
    if (resizeObserver) {
      resizeObserver.disconnect();
    }

    resizeObserver = new ResizeObserver(entries => {
      if (!entries[0]) return;

      // Skip updates while height is locked (child animation in progress)
      if (isHeightLocked) return;

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

  /**
   * Request a height change before a child animation starts.
   * Locks the ResizeObserver and sets the target height immediately,
   * allowing the Modal to animate smoothly while child animates visually.
   *
   * @param {number} delta - Height change in pixels (positive for expand, negative for collapse)
   * @param {number} duration - Animation duration in ms (default: 400)
   */
  function requestHeightDelta(delta, duration = 400) {
    // Clear any pending unlock
    if (unlockTimer) clearTimeout(unlockTimer);

    // Calculate target height
    const currentHeight = parseFloat(containerHeight.value) || 0;
    let targetHeight = currentHeight + delta;

    // Get max height constraint
    let maxAvailable = Infinity;
    if (getMaxHeight) {
      const max = getMaxHeight();
      if (max && max < Infinity) {
        maxAvailable = max;
        targetHeight = Math.min(targetHeight, maxAvailable);
      }
    }

    // Ensure non-negative height
    targetHeight = Math.max(0, targetHeight);

    // Smart detection: if modal is already at max height, don't lock ResizeObserver
    // Let it handle the height change naturally (avoids wrong predictions when content overflows)
    const isAtMaxHeight = Math.abs(currentHeight - maxAvailable) < 2; // 2px threshold
    const shouldLock = !isAtMaxHeight;

    if (shouldLock) {
      // Lock ResizeObserver and use delta prediction
      isHeightLocked = true;
      containerHeight.value = `${targetHeight}px`;

      // Unlock after animation completes and force height recalculation
      unlockTimer = setTimeout(() => {
        isHeightLocked = false;
        // Force recalculation to correct any delta discrepancies
        if (contentRef.value) {
          let actualHeight = contentRef.value.getBoundingClientRect().height;
          if (getExtraHeight) actualHeight += getExtraHeight();
          if (maxAvailable < Infinity) {
            actualHeight = Math.min(actualHeight, maxAvailable);
          }
          containerHeight.value = `${actualHeight}px`;
        }
      }, duration);
    }
    // else: Modal at max height - don't lock, let ResizeObserver handle it naturally
    // This avoids wrong predictions when content overflows/shifts
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
    requestHeightDelta,
    setupObserver,
    disconnectObserver
  };
}
