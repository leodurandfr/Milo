// frontend/src/utils/screenInset.js
// The kiosk's bezel inset: the strip of the panel its enclosure hides, set in
// millimeters (screen.edge_inset_mm) and drawn as --screen-inset, in CSS px.

/**
 * A whole number of panel pixels, so every edge the inset moves stays sharp,
 * divided by the page's devicePixelRatio: one CSS px is that many panel pixels
 * whatever produced the ratio (Chromium's page zoom on the kiosk). CSS's own
 * `mm` is never the panel's — it is a 96-dpi convention.
 *
 * @param {number} mm  the setting
 * @param {number|null} pixelsPerMm  the panel's pitch (hardware-info), null when unknown
 * @param {number} devicePixelRatio  panel pixels per CSS px
 * @returns {number|null} CSS px, or null when there is no inset to draw
 */
export function screenInsetPx(mm, pixelsPerMm, devicePixelRatio) {
  if (!(mm > 0) || !(pixelsPerMm > 0) || !(devicePixelRatio > 0)) return null;
  const panelPixels = Math.round(mm * pixelsPerMm);
  return panelPixels > 0 ? panelPixels / devicePixelRatio : null;
}
