// frontend/src/constants/collapse.js
/**
 * A section folding inside the settings modal: the curve its collapse rides on
 * both the section and the modal clip, and how long the clip follows it before
 * its own height observer takes over again.
 *
 * A height that lands on 0 cannot draw a spring's overshoot, while the clip,
 * whose height stays positive, would: so a collapse rides a monotone curve,
 * the same on both sides. It must stay the curve the CSS closes on
 * (`--transition-collapse`, design-system.css). The follow window is the 300 ms
 * collapse plus a margin; nothing wobbles afterwards.
 */
export const COLLAPSE_TRANSITION = 'height var(--transition-collapse)';
export const COLLAPSE_FOLLOW_MS = 450;
