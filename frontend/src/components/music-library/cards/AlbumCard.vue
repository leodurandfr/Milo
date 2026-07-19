<template>
  <div v-press class="album-card" @click="$emit('click')">
    <LazyImage
      :src="store.gridUrl(album.coverArt)"
      :fallback="albumPlaceholder"
      :alt="album.name"
      lazy
      class="album-cover"
    />
    <div class="album-info">
      <p class="album-name heading-4">{{ album.name }}</p>
      <p v-if="album.artist" class="album-artist text-mono">{{ album.artist }}</p>
    </div>
  </div>
</template>

<script setup>
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import LazyImage from '@/components/ui/LazyImage.vue';
import albumPlaceholder from '@/assets/images/album-placeholder.svg';

defineProps({
  album: {
    type: Object,
    required: true,
  },
});

defineEmits(['click']);

const store = useMusicLibraryStore();
</script>

<style scoped>
.album-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  cursor: pointer;
  min-width: 0;
}

.album-cover {
  aspect-ratio: 1;
  width: 100%;
  border-radius: var(--radius-04);
  background: var(--color-background-neutral-50);
}

.album-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}

.album-name {
  margin: 0;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.album-artist {
  margin: 0;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
