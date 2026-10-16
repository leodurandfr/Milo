// frontend/src/utils/screenColor.js
// The kiosk's one color transform, a color matrix on the root element: the
// panel's measured white correction (screen registry) times the warm filter.
// Both are per-channel gains on gamma-encoded values, which is what the matrix
// applies in sRGB, so the two compose by product and the filter does on a
// corrected panel exactly what it did on a raw one.

// The filter's ink and its ceiling: at 100 % warmth, rgba(255, 119, 0, 0.40).
// Measured on a D65 reference it walks the blackbody locus (|Duv| <= 0.003)
// from 6500 K down to about 3700 K.
const WARM_INK = [1, 119 / 255, 0];
const WARM_MAX_ALPHA = 0.40;

/**
 * @param {number[]|null} correction  [r, g, b] gains, or null when none applies
 * @param {{enabled: boolean, warmth: number}} filter
 * @returns {number[]|null} [r, g, b] gains in 0..1, or null when the layer would change nothing
 */
export function screenColorGains(correction, filter) {
  const alpha = filter?.enabled && filter.warmth > 0 ? (filter.warmth / 100) * WARM_MAX_ALPHA : 0;
  if (!correction && alpha === 0) return null;
  // The filter was measured as a multiply layer at opacity a over C, which
  // gives C * (1 - a + a * ink).
  return WARM_INK.map((ink, i) => (correction?.[i] ?? 1) * (1 - alpha + alpha * ink));
}
