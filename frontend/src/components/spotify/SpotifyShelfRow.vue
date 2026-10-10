<template>
  <!-- One of Spotify's shelves, scrolled sideways as in its app. It runs past
       the column into the page's side space instead of being cut at its edge. -->
  <div class="shelf">
    <div class="shelf-row" :class="{ 'with-name': withName, 'with-byline': withByline }">
      <SpotifyCard v-for="item in items" :key="item.uri" :item="item"
        @click="$emit('select', item)" />
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useI18n } from '@/services/i18n';
import { cardLines } from '@/utils/spotifyCard';
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
// The row is as tall as its tallest card: with a name line and a byline line,
// if any has one.
const lines = computed(() => props.items.map((item) => cardLines(item, t)));
const withName = computed(() => lines.value.some((line) => line.heading));
const withByline = computed(() => lines.value.some((line) => line.byline));
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
     as a column, then what it writes under it), worked out from the width it
     is laid out in, so the page below it sits where it will be. Once drawn, it
     keeps the height it was drawn at while off screen (content-visibility:
     auto always remembers it), and takes its new one when it comes back near. */
  --shelf-cover-height: calc(
    (100cqi - (var(--card-grid-columns) - 1) * var(--space-03)) / (var(--card-grid-columns) - var(--shelf-peek))
  );
  content-visibility: auto;
  contain-intrinsic-block-size: var(--shelf-cover-height);
}

/* The name under the cover, when a card writes one. */
.shelf-row.with-name {
  contain-intrinsic-block-size: calc(var(--shelf-cover-height) + var(--space-03) + var(--line-height-h4));
}

/* The byline alone, when the cover carries the name. */
.shelf-row.with-byline {
  contain-intrinsic-block-size: calc(var(--shelf-cover-height) + var(--space-02) + var(--line-height-body-medium));
}

/* And the byline under it. */
.shelf-row.with-name.with-byline {
  contain-intrinsic-block-size: calc(
    var(--shelf-cover-height) + var(--space-03) + var(--line-height-h4) + var(--space-01) + var(--line-height-body-medium)
  );
}

.shelf-row::-webkit-scrollbar {
  display: none;
}

.shelf-row > * {
  scroll-snap-align: start;
}

/* Under the player the row recedes, so a card showing there does not compete
   with it: solid out to the layout's --content-solid-end (the column's edge),
   a soft fade from there, --content-fade-end long, then the rest of the row
   at --shelf-receded, to the screen's edge. Three layers, composited: the
   receded level everywhere, the solid span (a pixel wider so no seam shows),
   and a fade from solid to nothing laid outward from its edge, which over the
   receded level reads as solid down to it. With no player the layout sets no
   stop and the solid span is the whole row. The phone has no side space to
   speak of: the player is a bar under the rows there. */
@media not (max-aspect-ratio: 4/3) {
  .shelf-row {
    --shelf-peek: 0.5;
    --shelf-receded: color-mix(in srgb, black 16%, transparent);
    --shelf-mask: linear-gradient(var(--shelf-receded), var(--shelf-receded)),
      linear-gradient(black, black),
      linear-gradient(to right, black, transparent);
    -webkit-mask-image: var(--shelf-mask);
    mask-image: var(--shelf-mask);
    mask-size:
      100% 100%,
      calc(100% - var(--content-solid-end, 0px) + 1px) 100%,
      var(--content-fade-end, 0px) 100%;
    mask-position:
      0 0,
      0 0,
      right calc(var(--content-solid-end, 0px) - var(--content-fade-end, 0px)) top 0;
    mask-repeat: no-repeat;
  }
}
</style>
