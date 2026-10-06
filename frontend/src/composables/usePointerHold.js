/**
 * Held while the pointer that pressed is down.
 *
 * The press-and-hold state of a control whose feedback lasts as long as the
 * finger does — a Toggle's knob widening, a slider's thumb held — released on
 * pointerup or pointercancel anywhere (the pointer leaves the control while it
 * is held), and on unmount.
 *
 * Usage:
 *   const { held, press } = usePointerHold({ onRelease });
 *   <el @pointerdown="(e) => !disabled && press(e)">
 *
 * press() ignores anything but the primary button and returns whether it took
 * the press, so a caller can start its own work only then.
 */
import { ref, onUnmounted } from 'vue';

export function usePointerHold({ onRelease } = {}) {
  const held = ref(false);

  function release() {
    window.removeEventListener('pointerup', release);
    window.removeEventListener('pointercancel', release);
    if (!held.value) return;
    held.value = false;
    onRelease?.();
  }

  function press(event) {
    if (event.button !== 0 || held.value) return false;
    held.value = true;
    window.addEventListener('pointerup', release);
    window.addEventListener('pointercancel', release);
    return true;
  }

  onUnmounted(release);

  return { held, press, release };
}
