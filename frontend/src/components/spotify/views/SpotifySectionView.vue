<!-- One of Spotify's sections whole, or a list of the account's library, its
     row's cards in a grid. A library list runs to hundreds of cards: they are
     mounted a chunk at a time, ahead of the scroll. -->
<template>
  <div>
    <SpotifyCardGrid :items="visible" @select="$emit('select', $event)" />
    <div v-if="hasMore" ref="sentinelRef" />
  </div>
</template>

<script setup>
import SpotifyCardGrid from '../SpotifyCardGrid.vue';
import { useCardGridColumns } from '@/composables/useCardGridColumns';
import { useRenderWindow } from '@/composables/useRenderWindow';

const props = defineProps({
  // The section's cards, as its row drew them.
  items: {
    type: Array,
    required: true,
  },
});

// A card, with the kind of page it opens.
defineEmits(['select']);

// Whole rows at a time: four rows of cards are about a screen and a half on
// the kiosk (a row is ~325 px of its 800, measured), as the listing's chunk is.
const CHUNK_ROWS = 4;
const { columns } = useCardGridColumns();
const { visible, hasMore, sentinelRef } = useRenderWindow(() => props.items, { chunk: () => columns.value * CHUNK_ROWS });
</script>
