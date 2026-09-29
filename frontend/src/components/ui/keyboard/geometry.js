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

/** Row of accent variants above the pressed key, clamped inside the keyboard. */
export function accentPopupPlacement(keyRect, kbRect, variantCount) {
  const width = accentPopupWidth(variantCount);
  const centred = (keyRect.left - kbRect.left) + (keyRect.width / 2) - (width / 2);
  return {
    left: Math.max(4, Math.min(centred, kbRect.width - width - 4)),
    bottom: (kbRect.bottom - keyRect.top) + 6
  };
}

/** Which accent the finger is over, from a viewport clientX. */
export function accentIndexAt(clientX, kbLeft, popupLeft, variantCount) {
  const x = clientX - kbLeft - popupLeft - ACCENT_PADDING;
  const index = Math.floor(x / (ACCENT_OPTION_WIDTH + ACCENT_GAP));
  return Math.max(0, Math.min(index, variantCount - 1));
}
