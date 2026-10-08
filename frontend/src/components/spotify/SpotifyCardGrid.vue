<!-- Cards in the square-artwork grid's columns. Outside the template: a root
     comment makes it a fragment in development, and it is a page's root. -->
<template>
  <div class="cards-grid">
    <SpotifyCard v-for="item in items" :key="item.uri" :item="item" @click="$emit('select', item)" />
  </div>
</template>

<script setup>
import SpotifyCard from './cards/SpotifyCard.vue';

defineProps({
  // Cards as SpotifyCard draws them.
  items: {
    type: Array,
    required: true,
  },
});

// A card, with the kind of page it opens.
defineEmits(['select']);
</script>

<style scoped>
.cards-grid {
  display: grid;
  grid-template-columns: repeat(var(--card-grid-columns), minmax(0, 1fr));
  row-gap: var(--space-05);
  column-gap: var(--space-03);
}
</style>
