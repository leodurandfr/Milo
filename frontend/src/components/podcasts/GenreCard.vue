<template>
  <div v-press class="genre-card" @click="$emit('click')">
    <div class="genre-image">
      <img :src="genreImage" :alt="label" class="genre-img" />
    </div>
    <p class="genre-label heading-4">{{ label }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'

import { genreArtwork } from '@/constants/podcastGenres'

const props = defineProps({
  label: {
    type: String,
    required: true
  },
  value: {
    type: String,
    required: true
  }
})

defineEmits(['click'])

const genreImage = computed(() => genreArtwork(props.value))
</script>

<style scoped>
.genre-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-03) var(--space-04) var(--space-03);
  background: var(--color-background-neutral);
  border-radius: var(--radius-04);
  cursor: pointer;
}

.genre-image {
  aspect-ratio: 8 / 3;
  width: 100%;
  border-radius: var(--radius-03);
  overflow: hidden;
}

.genre-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.genre-label {
  margin: 0;
  color: var(--color-text);
}
</style>
