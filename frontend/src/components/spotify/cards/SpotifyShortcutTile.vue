<template>
  <div v-press class="shortcut-tile" @click="$emit('click')">
    <div class="tile-cover">
      <LikedCover v-if="liked" />
      <LazyImage v-else :src="image || ''" :fallback="musicPlaceholder" :alt="title" lazy class="tile-image" />
    </div>
    <p class="tile-name heading-4">{{ title }}</p>
  </div>
</template>

<script setup>
import LikedCover from '@/components/audio/LikedCover.vue';
import LazyImage from '@/components/ui/LazyImage.vue';
import { musicPlaceholder } from '@/constants/placeholders';

defineProps({
  title: {
    type: String,
    required: true,
  },
  image: {
    type: String,
    default: '',
  },
  // The Liked Songs tile: its cover in place of the image.
  liked: {
    type: Boolean,
    default: false,
  },
});

defineEmits(['click']);
</script>

<style scoped>
.shortcut-tile {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  min-width: 0;
  padding-right: var(--space-03);
  border-radius: var(--radius-02);
  background: var(--color-surface);
  overflow: hidden;
  cursor: pointer;
}

.tile-cover {
  width: 64px;
  height: 64px;
  flex-shrink: 0;
  background: var(--color-surface-glass);
}

.tile-image {
  width: 100%;
  height: 100%;
}

.tile-name {
  margin: 0;
  min-width: 0;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
