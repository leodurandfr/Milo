<template>
  <div class="loading-spinner" :class="spinnerClass" :style="spinnerStyle">
    <div class="loading-spinner-content">
      <div v-html="svgContent" class="loading-spinner-svg" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';

const props = defineProps({
  size: {
    type: [String, Number],
    default: 32
  }
});

const spinnerClass = computed(() => {
  // Use CSS classes for named sizes (enables responsive behavior)
  // 'inherit' means parent controls size via --spinner-size variable
  if (typeof props.size === 'string' && props.size !== 'inherit') {
    return `loading-spinner--${props.size}`;
  }
  return '';
});

const spinnerStyle = computed(() => {
  // Only apply inline styles for numeric sizes
  if (typeof props.size === 'number') {
    return {
      width: `${props.size}px`,
      height: `${props.size}px`,
      '--spinner-size': `${props.size}px`
    };
  }
  // For 'inherit', let parent CSS control the size via --spinner-size
  // For other string sizes (small, medium, large), styles are handled by CSS classes
  return {};
});

/* The geometry, in the units of the 24x24 viewBox. Two numbers, and every
   consumer reads the result — there is no second place that resizes a spinner.

   `OUTER_RADIUS` is the radius the original artwork carried, kept: the ring
   draws 18.7 units of the 24, against `play`'s 17.57 of ink. Drawing it a few
   percent over the glyph it replaces is deliberate — a ring reads lighter than
   a solid mark at an equal height, and the swap is one in time, never a
   neighbour in space, so the eye compares before and after at the same spot.
   Sizing it *under* the play is what made the wait read as a shrink.

   `BLADE_WIDTH` is the icon set's line weight, measured on search.svg — its
   ring is 1.51 units thick and its handle 1.5 wide. A spinner is the only
   stroked mark among solid glyphs, so it borrows that weight rather than
   inventing one.

   `INNER_RADIUS` keeps the hole at the proportion the original artwork had. */
const OUTER_RADIUS = 9.35;
const INNER_RADIUS = 5.04;
const BLADE_WIDTH = 1.5;
const BLADE_COUNT = 8;

/* One blade lit, two fading behind it, the rest at the floor — rotated by one
   step per blade, which is what makes the ring appear to turn. Nothing rotates:
   only opacity animates, so the mark never leaves its bounding box.

   Derived from BLADE_COUNT rather than written out: the rotation indexes this
   modulo BLADE_COUNT, so a hand-written list one short of it would hand every
   blade `undefined`, and a `values="undefined;…"` makes the browser discard the
   whole <animate> and hold the spinner static at full opacity — silently. */
const OPACITY_HEAD = [1, 0.64, 0.6];
const OPACITY_FLOOR = 0.16;
const OPACITY_CYCLE = Array.from(
  { length: BLADE_COUNT },
  (_, i) => OPACITY_HEAD[i] ?? OPACITY_FLOOR
);

const svgContent = computed(() => {
  const cap = BLADE_WIDTH / 2;
  const blades = Array.from({ length: BLADE_COUNT }, (_, i) => {
    const angle = (i * 2 * Math.PI) / BLADE_COUNT - Math.PI / 2;
    const cos = Math.cos(angle);
    const sin = Math.sin(angle);
    const at = (r) => [12 + r * cos, 12 + r * sin].map((v) => v.toFixed(3));
    const [x1, y1] = at(INNER_RADIUS + cap);
    const [x2, y2] = at(OUTER_RADIUS - cap);

    // Rotated right by `i`, then closed on its own first value so the cycle loops.
    const values = Array.from(
      { length: BLADE_COUNT },
      (_, j) => OPACITY_CYCLE[(j - i + BLADE_COUNT) % BLADE_COUNT]
    );
    values.push(values[0]);

    return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" opacity="${values[0]}">
  <animate attributeName="opacity" values="${values.join(';')}" dur="1.4s" repeatCount="indefinite"/>
</line>`;
  });

  return `<svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" stroke="currentColor" stroke-width="${BLADE_WIDTH}" stroke-linecap="round">
${blades.join('\n')}
</svg>`;
});
</script>

<style scoped>
.loading-spinner {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: var(--radius-02);
  overflow: hidden;
  width: var(--spinner-size, 32px);
  height: var(--spinner-size, 32px);
}

/* Each rung states its native value as the fallback of `--spinner-size` rather
   than setting the property, for the same reason SvgIcon states `--svg-size`
   that way: a custom property set on the element outranks one inherited from an
   ancestor, so a named size would lock out the caller that wants to drive it.
   Kept in step with SvgIcon's rungs — a spinner standing in for an icon has to
   land on the same footprint. */

/* Size variants matching SvgIcon dimensions - Desktop */
.loading-spinner--small {
  width: var(--spinner-size, 24px);
  height: var(--spinner-size, 24px);
}

.loading-spinner--medium {
  width: var(--spinner-size, 28px);
  height: var(--spinner-size, 28px);
}

.loading-spinner--large {
  width: var(--spinner-size, 32px);
  height: var(--spinner-size, 32px);
}

/* Size variants matching SvgIcon dimensions - Mobile */
@media (max-aspect-ratio: 4/3) {
  .loading-spinner--small {
    width: var(--spinner-size, 20px);
    height: var(--spinner-size, 20px);
  }

  .loading-spinner--medium {
    width: var(--spinner-size, 24px);
    height: var(--spinner-size, 24px);
  }

  .loading-spinner--large {
    width: var(--spinner-size, 28px);
    height: var(--spinner-size, 28px);
  }
}

.loading-spinner-content {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
}

.loading-spinner-svg {
  display: block;
  width: 100%;
  height: 100%;
}

.loading-spinner-svg :deep(svg) {
  width: 100% !important;
  height: 100% !important;
  display: block;
}
</style>
