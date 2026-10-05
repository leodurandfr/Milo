<template>
  <!-- One of Spotify's sections whole, its row's cards in a grid. -->
  <SpotifyCardGrid :items="items" @select="$emit('select', $event)" />
</template>

<script setup>
import SpotifyCardGrid from '../SpotifyCardGrid.vue';

defineProps({
  // The section's cards, as its row drew them.
  items: {
    type: Array,
    required: true,
  },
});

// A card, with the kind of page it opens.
defineEmits(['select']);
</script>
