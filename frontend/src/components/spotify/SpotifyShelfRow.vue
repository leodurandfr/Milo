<template>
  <!-- One of Spotify's shelves, scrolled sideways as in its app. It runs past
       the column into the page's side space instead of being cut at its edge. -->
  <div ref="rowRef" class="shelf-row">
    <SpotifyCard v-for="item in items" :key="item.uri" :item="item"
      @click="$emit('select', item)" />
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount } from 'vue';
import SpotifyCard from './cards/SpotifyCard.vue';

defineProps({
  // Cards as /api/spotify/home lists a shelf's.
  items: {
    type: Array,
    required: true,
  },
});

defineEmits(['select']);

// The mask's stops in pixels, read off the row's side padding (the space it
// runs into): the layout's bleed is partly a percentage, which in a mask would
// resolve against the row's width rather than the column's.
const rowRef = ref(null);
let resizes = null;

function measure() {
  const el = rowRef.value;
  if (!el) return;
  const style = getComputedStyle(el);
  el.style.setProperty('--shelf-bleed-start', style.paddingLeft);
  el.style.setProperty('--shelf-bleed-end', style.paddingRight);
}

onMounted(() => {
  measure();
  // The column narrows when the player appears, and the window can resize.
  // The border box, because what is measured is the padding, which can move
  // while the content box does not.
  resizes = new ResizeObserver(measure);
  resizes.observe(rowRef.value, { box: 'border-box' });
});

onBeforeUnmount(() => {
  resizes?.disconnect();
});
</script>

<style scoped>
/* The cards larger than a grid's: one column fewer, and part of the next
   one showing there is more — three quarters on the phone, half on a wider
   screen, where the cards can afford to grow. The row reaches into the space
   the layout gives it on either side, and its padding brings the first card
   back to the column's edge. */
.shelf-row {
  --shelf-peek: 0.25;
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: calc(
    (100% - (var(--card-grid-columns) - 1) * var(--space-03)) / (var(--card-grid-columns) - var(--shelf-peek))
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
}

/* Past the column the row recedes, so the side space shows where the row
   goes without competing with the column: a soft fade from each of the
   column's edges, --content-bleed-fade long, then the rest of the row at
   --shelf-receded, to the screen's edge — under the player too, which covers
   it. Four layers, composited: the receded level everywhere, the column solid
   (a pixel wider each side so no seam shows), and a fade from solid to nothing
   laid outward from each of its edges, which over the receded level reads as
   solid down to it. The phone has no side space to speak of: the screen's
   edge cuts the row there. */
@media not (max-aspect-ratio: 4/3) {
  .shelf-row {
    --shelf-peek: 0.5;
    --shelf-fade: var(--content-bleed-fade, 0px);
    --shelf-receded: color-mix(in srgb, black 16%, transparent);
    --shelf-fade-curve: black,
      color-mix(in srgb, black 97%, transparent) 10%,
      color-mix(in srgb, black 90%, transparent) 20%,
      color-mix(in srgb, black 78%, transparent) 30%,
      color-mix(in srgb, black 65%, transparent) 40%,
      color-mix(in srgb, black 50%, transparent) 50%,
      color-mix(in srgb, black 35%, transparent) 60%,
      color-mix(in srgb, black 22%, transparent) 70%,
      color-mix(in srgb, black 10%, transparent) 80%,
      color-mix(in srgb, black 3%, transparent) 90%,
      transparent;
    --shelf-mask: linear-gradient(var(--shelf-receded), var(--shelf-receded)),
      linear-gradient(black, black),
      linear-gradient(to left, var(--shelf-fade-curve)),
      linear-gradient(to right, var(--shelf-fade-curve));
    -webkit-mask-image: var(--shelf-mask);
    mask-image: var(--shelf-mask);
    mask-size:
      100% 100%,
      calc(100% - var(--shelf-bleed-start, 0px) - var(--shelf-bleed-end, 0px) + 2px) 100%,
      var(--shelf-fade) 100%,
      var(--shelf-fade) 100%;
    mask-position:
      0 0,
      calc(var(--shelf-bleed-start, 0px) - 1px) 0,
      calc(var(--shelf-bleed-start, 0px) - var(--shelf-fade)) 0,
      right calc(var(--shelf-bleed-end, 0px) - var(--shelf-fade)) top 0;
    mask-repeat: no-repeat;
  }
}
</style>
