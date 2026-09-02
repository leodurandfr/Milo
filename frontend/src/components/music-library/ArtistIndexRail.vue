<template>
  <div ref="railRef" class="index-rail text-mono-small" :style="{ '--rail-top': `${railTopPx}px` }"
    @pointerdown="onPointerDown" @pointermove="onPointerMove" @pointerup="onPointerUp"
    @pointercancel="onPointerUp">
    <div ref="stripRef" class="rail-strip">
      <span v-for="(letter, i) in shownLetters" :key="`${letter}-${i}`"
        :class="['rail-entry', { active: letter === activeLetter }]">{{ letter }}</span>
      <span v-if="activeLetter" class="rail-bubble text-mono" :style="{ top: `${bubblePercent}%` }">
        {{ activeLetter }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { condenseLetters, letterAtRatio, offsetWithin, scrollParentOf } from './indexRail';

const props = defineProps({
  // Every bucket name in the index, mounted or not — the rail is how a letter is
  // reached before its rows exist.
  letters: {
    type: Array,
    default: () => [],
  },
});

const emit = defineEmits(['jump']);

// Never fewer than two letters, whatever the arithmetic below says: a window too
// short for the rail must still show one, not vanish.
const MIN_LETTERS = 2;
const FALLBACK_ROW_PX = 22;

const railRef = ref(null);
const stripRef = ref(null);
const railTopPx = ref(0);
const bandPx = ref(0);
const rowPx = ref(FALLBACK_ROW_PX);
const activeLetter = ref('');
const bubblePercent = ref(0);

// The STRIP's box, not the band's: the letters are centred in the band, so
// mapping the finger over the band would answer with a letter drawn elsewhere.
// Cached for the length of one drag — each jump scrolls the list under the
// finger, and re-reading the box on every pointermove would flush that layout
// back 120 times a second. It is refreshed after a jump instead; the rail is
// sticky, so it settles after the first one and then never moves.
let stripRect = null;
let rectFrame = 0;
let observer = null;

const shownLetters = computed(() =>
  condenseLetters(props.letters, Math.max(MIN_LETTERS, Math.floor(bandPx.value / rowPx.value)))
);

// Where the rail comes to rest — the top of the list — in the scrollport's own
// laid-out pixels. It is the one thing CSS cannot know: the band's height and
// its centring are plain calc() from there, so a resized window re-lays the rail
// on its own. A stale value costs where the strip is centred and nothing else —
// the band's bottom is a calc() from the window, so it lands right either way.

// Read back from the box CSS just laid out, never computed against the window:
// the height is a calc() with a floor, so this cannot come back empty, and one
// row is the design system's line-height plus the gap between two letters.
function measureBand() {
  const el = railRef.value;
  if (!el) return;
  // Re-anchored here too, and only on a real change — which also keeps the
  // observer from looping, since the second pass finds it unchanged. This alone
  // does NOT heal every drift: the band's height is a calc() from this offset
  // and nothing else, so a layout that moves the list without resizing it (a
  // storage-space row appearing above the tabs) resizes nothing and wakes no
  // observer. The gesture handler re-reads it for that, and until then the cost
  // is where the strip is centred, not where the band ends — the bottom is a
  // calc() from the window.
  // Measured on the ROW, never on the rail: Chrome folds the sticky shift into a
  // sticky element's own offsetTop, so reading it here would feed the offset
  // back into itself and hold whatever drift it had.
  const row = el.parentElement;
  const scroller = scrollParentOf(el);
  const top = scroller && row ? offsetWithin(row, scroller) : 0;
  if (Math.abs(top - railTopPx.value) > 1) railTopPx.value = top;
  bandPx.value = el.clientHeight;
  const lineHeight = parseFloat(getComputedStyle(el).lineHeight);
  const gap = parseFloat(getComputedStyle(stripRef.value).rowGap) || 0;
  if (lineHeight > 0) rowPx.value = lineHeight + gap;
}

function refreshRect() {
  stripRect = stripRef.value?.getBoundingClientRect() || null;
}

function track(clientY) {
  if (!stripRect) return;
  const ratio = (clientY - stripRect.top) / stripRect.height;
  // Placed as a percentage of the strip: pointer coordinates are screen pixels
  // and a CSS offset is laid-out ones, which differ by ui_scale on the kiosk —
  // a fraction of the strip's own height is the same number in both.
  bubblePercent.value = Math.min(100, Math.max(0, ratio * 100));
  // Over what is DRAWN, not over the whole index: on an abridged rail the two
  // differ by up to a rung, and a press has to answer with the letter printed
  // under the finger. The letters the abridgement dropped are reached by their
  // neighbour and a short scroll — landing on a letter nobody touched is worse.
  const letter = letterAtRatio(shownLetters.value, ratio);
  if (!letter || letter === activeLetter.value) return;
  activeLetter.value = letter;
  emit('jump', letter);
  cancelAnimationFrame(rectFrame);
  rectFrame = requestAnimationFrame(refreshRect);
}

function onPointerDown(e) {
  // Capture: past the first press the gesture belongs to the rail, so sliding
  // off its width mid-drag keeps scrubbing instead of ending it.
  railRef.value?.setPointerCapture?.(e.pointerId);
  // The one moment the anchor has to be right, and the only one that catches a
  // list moved by something that resized nothing (see measureBand).
  measureBand();
  refreshRect();
  track(e.clientY);
}

function onPointerMove(e) {
  if (stripRect) track(e.clientY);
}

function onPointerUp(e) {
  // hasPointerCapture first: releasing a pointer that is already gone (the
  // cancel path) throws rather than no-ops.
  const el = railRef.value;
  if (el?.hasPointerCapture?.(e.pointerId)) el.releasePointerCapture(e.pointerId);
  cancelAnimationFrame(rectFrame);
  stripRect = null;
  activeLetter.value = '';
}

onMounted(() => {
  measureBand();
  // The band follows the window through CSS alone; this only reads the result
  // back, to know how many letters that height holds.
  observer = new ResizeObserver(measureBand);
  observer.observe(railRef.value);
  // A window change moves what is laid out above the list as often as it resizes
  // the band, and the observer only sees the second.
  window.addEventListener('resize', measureBand);
});

onBeforeUnmount(() => {
  observer?.disconnect();
  window.removeEventListener('resize', measureBand);
  cancelAnimationFrame(rectFrame);
});
</script>

<style scoped>
/* The band: from where the rail comes to rest — the top of the list — down to
   --space-07 above the bottom of the window, with the letters centred in it.
   Its sticky offset is that same resting position, so it stays exactly where it
   was drawn as the list scrolls under it.
   The viewport unit counts screen pixels while the app is laid out in the fewer
   ones ui_scale magnifies (published by applyUiScale), hence the division; the
   floor leaves a short window with a small rail rather than none.
   svh, NOT dvh: the dynamic unit grows as a phone browser retracts its toolbar
   on the way down the list, and the band is pinned at the top — so it grew
   downwards and carried the centred strip with it, which is the rail visibly
   sliding down the page as you near the bottom. The small unit is the viewport
   with every toolbar OUT, so it never changes and the rail never moves. It also
   makes a bottom safe-area inset moot: the band already ends a retracted
   toolbar's height above the screen, which is more than the home indicator
   needs, and env() would have been the last thing left that moves. */
.index-rail {
  position: sticky;
  top: var(--rail-top, 0px);
  height: max(
    var(--space-09),
    calc(100svh / var(--ui-scale, 1) - var(--rail-top, 0px) - var(--space-07))
  );
  align-self: flex-start;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  width: var(--space-06);
  color: var(--color-text-light);
  cursor: pointer;
  user-select: none;
  /* The drag scrubs the index; it must never pan the list it is scrolling. */
  touch-action: none;
}

.rail-strip {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: calc(var(--space-01) / 2);
  width: 100%;
}

.rail-entry {
  display: block;
  text-align: center;
  pointer-events: none;
}

.rail-entry.active {
  color: var(--color-brand);
}

.rail-bubble {
  position: absolute;
  right: 100%;
  margin-right: var(--space-02);
  transform: translateY(-50%);
  display: flex;
  align-items: center;
  justify-content: center;
  min-width: var(--space-08);
  height: var(--space-08);
  border-radius: var(--radius-full);
  background: var(--color-background-contrast-80);
  color: var(--color-text-contrast);
  pointer-events: none;
}
</style>
