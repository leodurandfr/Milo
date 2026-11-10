// frontend/src/components/ui/keyboard/geometry.js

/**
 * Popup placement for the virtual keyboard, relative to the keyboard's own box.
 */

// Mirrors .accent-popup / .accent-option in VirtualKeyboard.vue's scoped CSS.
// Changing one side alone misplaces the popup and breaks slide-to-select.
export const ACCENT_OPTION_WIDTH = 44;
export const ACCENT_GAP = 2;
export const ACCENT_PADDING = 4;

export function accentPopupWidth(variantCount) {
  return variantCount * (ACCENT_OPTION_WIDTH + ACCENT_GAP) - ACCENT_GAP + ACCENT_PADDING * 2;
}

/** Enlarged single character above the pressed key. */
export function pressPopupPlacement(keyRect, kbRect) {
  const width = Math.max(keyRect.width + 12, 48);
  return {
    left: (keyRect.left - kbRect.left) + (keyRect.width / 2) - (width / 2),
    bottom: (kbRect.bottom - keyRect.top) + 6,
    width
  };
}

/**
 * Row of accent variants above the pressed key, clamped inside the keyboard and
 * clear of the strip `hidden` px wide that the kiosk's bezel covers on each side.
 */
export function accentPopupPlacement(keyRect, kbRect, variantCount, hidden = 0) {
  const width = accentPopupWidth(variantCount);
  const centred = (keyRect.left - kbRect.left) + (keyRect.width / 2) - (width / 2);
  const margin = 4 + hidden;
  return {
    left: Math.max(margin, Math.min(centred, kbRect.width - width - margin)),
    bottom: (kbRect.bottom - keyRect.top) + 6
  };
}

/** Which accent the finger is over, from a viewport clientX. */
export function accentIndexAt(clientX, kbLeft, popupLeft, variantCount) {
  const x = clientX - kbLeft - popupLeft - ACCENT_PADDING;
  const index = Math.floor(x / (ACCENT_OPTION_WIDTH + ACCENT_GAP));
  return Math.max(0, Math.min(index, variantCount - 1));
}
