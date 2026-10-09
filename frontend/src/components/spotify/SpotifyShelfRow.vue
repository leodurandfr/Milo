<template>
  <!-- One of Spotify's shelves, scrolled sideways as in its app. It runs past
       the column into the page's side space instead of being cut at its edge. -->
  <div class="shelf">
    <div ref="rowRef" class="shelf-row" :class="{ 'with-byline': withByline }">
      <SpotifyCard v-for="item in items" :key="item.uri" :item="item"
        @click="$emit('select', item)" />
    </div>
  </div>
</template>

<script>
// The mask's stops in pixels, read off the row's side padding (the space it
// runs into): the layout's bleed is partly a percentage, which in a mask would
// resolve against the row's width rather than the column's.
//
// One observer for every row: a frame that resizes them all (a page mounting or
// coming back, the player opening) reads every padding before writing a stop —
// one style pass, where each row reading after the last one's write laid the
// page out once per row. Its first report lands before the first paint. The
// border box, because what is measured is the padding, which can move while the
// content box does not.
let rows = null;

function measureRows(entries) {
  // Out of the document (a page its KeepAlive kept): no style to read, and the
  // stops it had are the ones it gets back.
  const stops = entries
    .map(({ target }) => target)
    .filter((el) => el.isConnected)
    .map((el) => {
      const style = getComputedStyle(el);
      return [el, style.paddingLeft, style.paddingRight];
    });
  for (const [el, start, end] of stops) {
    setStop(el, '--shelf-bleed-start', start);
    setStop(el, '--shelf-bleed-end', end);
  }
}

// Written only when they moved: an unchanged row costs no style pass.
function setStop(el, name, value) {
  if (el.style.getPropertyValue(name) !== value) el.style.setProperty(name, value);
}
</script>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { useI18n } from '@/services/i18n';
import { cardByline } from '@/utils/spotifyCard';
import SpotifyCard from './cards/SpotifyCard.vue';

const props = defineProps({
  // Cards as /api/spotify/home lists a shelf's.
  items: {
    type: Array,
    required: true,
  },
});

defineEmits(['select']);

const { t } = useI18n();
// The row is as tall as its tallest card: with a byline line, if any has one.
const withByline = computed(() => props.items.some((item) => cardByline(item, t)));

const rowRef = ref(null);
let observed = null;

onMounted(() => {
  rows ??= new ResizeObserver(measureRows);
  observed = rowRef.value;
  rows.observe(observed, { box: 'border-box' });
});

onBeforeUnmount(() => {
  rows?.unobserve(observed);
});
</script>

<style scoped>
/* The width the row's cards are laid out in, for the height a row not drawn
   yet is given (below): the row's own content box, which its negative margins
   and its padding bring back to this one's width. A container also contains
   layout and style: an overlay a card opens is positioned against the shelf
   and stacked inside it, so it has to be teleported out. */
.shelf {
  container-type: inline-size;
}

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
  /* A row off screen is neither styled, laid out nor painted until it comes
     near — on the Spotify home, most of eighteen. Until it is first drawn it
     holds the height its cards will have (SpotifyCard: a square cover as wide
     as a column, then its name), worked out from the width it is laid out in,
     so the page below it sits where it will be. Once drawn, it keeps the
     height it was drawn at while off screen (content-visibility: auto always
     remembers it), and takes its new one when it comes back near. */
  --shelf-card-height: calc(
    (100cqi - (var(--card-grid-columns) - 1) * var(--space-03)) / (var(--card-grid-columns) - var(--shelf-peek))
    + var(--space-02) + var(--line-height-h4)
  );
  content-visibility: auto;
  contain-intrinsic-block-size: var(--shelf-card-height);
}

/* And the byline under the name, when a card has one. */
.shelf-row.with-byline {
  contain-intrinsic-block-size: calc(var(--shelf-card-height) + var(--space-01) + var(--line-height-mono-medium));
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
    /* (1 - t)², an ease-out: it starts falling at the column's edge itself
       and lands flat on the receded level. An S-curve held the first fifth
       near solid, so the fade seemed to begin past the edge. */
    --shelf-fade-curve: black,
      color-mix(in srgb, black 81%, transparent) 10%,
      color-mix(in srgb, black 64%, transparent) 20%,
      color-mix(in srgb, black 49%, transparent) 30%,
      color-mix(in srgb, black 36%, transparent) 40%,
      color-mix(in srgb, black 25%, transparent) 50%,
      color-mix(in srgb, black 16%, transparent) 60%,
      color-mix(in srgb, black 9%, transparent) 70%,
      color-mix(in srgb, black 4%, transparent) 80%,
      color-mix(in srgb, black 1%, transparent) 90%,
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
