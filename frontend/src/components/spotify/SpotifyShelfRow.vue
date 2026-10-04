<template>
  <!-- One of Spotify's shelves, scrolled sideways as in its app. It runs past
       the column into the page's side space instead of being cut at its edge. -->
  <div ref="rowRef" class="shelf-row">
    <SpotifyCard v-for="item in items" :key="item.uri" :item="item"
      :class="{ outside: outside.has(item.uri) }" :data-uri="item.uri"
      @click="$emit('select', item)" />
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onBeforeUnmount } from 'vue';
import SpotifyCard from './cards/SpotifyCard.vue';

const props = defineProps({
  // Cards as /api/spotify/home lists a shelf's.
  items: {
    type: Array,
    required: true,
  },
});

defineEmits(['select']);

// The cards wholly outside the column. The row's side padding is the space it
// runs into, so the column is the row's box less that padding.
const rowRef = ref(null);
const outside = ref(new Set());

let intersections = null;
let resizes = null;
let margin = '';

function observe() {
  const el = rowRef.value;
  if (!el) return;
  const style = getComputedStyle(el);
  const next = `0px -${style.paddingRight} 0px -${style.paddingLeft}`;
  if (next === margin && intersections) return;
  margin = next;
  // The mask's stops in pixels: in a gradient, the layout's percentage would
  // resolve against the row's width rather than the column's.
  el.style.setProperty('--shelf-bleed-start', style.paddingLeft);
  el.style.setProperty('--shelf-bleed-end', style.paddingRight);
  intersections?.disconnect();
  intersections = new IntersectionObserver((entries) => {
    const now = new Set(outside.value);
    for (const entry of entries) {
      const uri = entry.target.dataset.uri;
      if (entry.isIntersecting) now.delete(uri);
      else now.add(uri);
    }
    outside.value = now;
  }, { root: el, rootMargin: margin, threshold: 0 });
  for (const card of el.children) intersections.observe(card);
}

onMounted(() => {
  observe();
  // The column narrows when the player appears, and the window can resize.
  resizes = new ResizeObserver(observe);
  resizes.observe(rowRef.value);
});

watch(() => props.items, async () => {
  await nextTick();
  margin = '';
  observe();
});

onBeforeUnmount(() => {
  intersections?.disconnect();
  resizes?.disconnect();
});
</script>

<style scoped>
/* The cards a quarter larger than a grid's: one column fewer, and three
   quarters of the next one showing there is more. The row reaches into the
   space the layout gives it on either side, and its padding brings the first
   card back to the column's edge. */
.shelf-row {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: calc(
    (100% - (var(--card-grid-columns) - 1) * var(--space-03)) / (var(--card-grid-columns) - 0.25)
  );
  column-gap: var(--space-03);
  margin-inline: calc(-1 * var(--content-bleed-start, 0px)) calc(-1 * var(--content-bleed-end, 0px));
  padding-inline: var(--content-bleed-start, 0px) var(--content-bleed-end, 0px);
  scroll-padding-inline: var(--content-bleed-start, 0px) var(--content-bleed-end, 0px);
  overflow-x: auto;
  overscroll-behavior-x: contain;
  scroll-snap-type: x mandatory;
  scrollbar-width: none;
}

.shelf-row::-webkit-scrollbar {
  display: none;
}

.shelf-row > * {
  scroll-snap-align: start;
  transition: opacity var(--transition-medium);
}

/* Past the column the row fades out towards the screen's edge, and a card
   wholly out there recedes further, so the side space shows where the row
   goes without competing with the column. The phone has no side space to
   speak of: the screen's edge cuts the row there. */
@media not (max-aspect-ratio: 4/3) {
  .shelf-row {
    -webkit-mask-image: linear-gradient(to right, transparent 0, black var(--shelf-bleed-start, 0px),
      black calc(100% - var(--shelf-bleed-end, 0px)), transparent 100%);
    mask-image: linear-gradient(to right, transparent 0, black var(--shelf-bleed-start, 0px),
      black calc(100% - var(--shelf-bleed-end, 0px)), transparent 100%);
  }

  .shelf-row > .outside {
    opacity: 0.4;
  }
}
</style>
