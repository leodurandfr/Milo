<template>
  <div v-press class="media-row" @click="$emit('click')">
    <LazyImage
      :src="store.thumbUrl(coverId)"
      :fallback="roundedCover ? artistPlaceholder : albumPlaceholder"
      :alt="title"
      lazy
      :class="['media-cover', { rounded: roundedCover }]"
    />
    <div class="media-details">
      <p class="media-title heading-3">{{ title }}</p>
      <p v-if="subtitle" class="media-subtitle text-mono">{{ subtitle }}</p>
    </div>
  </div>
</template>

<script setup>
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import LazyImage from '@/components/ui/LazyImage.vue';
import albumPlaceholder from '@/assets/images/album-placeholder.svg';
import artistPlaceholder from '@/assets/images/artist-placeholder.svg';

defineProps({
  // Navidrome coverArt id (may be empty → placeholder fallback). Album/playlist/
  // song rows fall back to the CD placeholder; artist rows (roundedCover) fall
  // back to the static artist placeholder.
  coverId: {
    type: String,
    default: '',
  },
  title: {
    type: String,
    required: true,
  },
  subtitle: {
    type: String,
    default: '',
  },
  // Circular cover for artist rows.
  roundedCover: {
    type: Boolean,
    default: false,
  },
});

defineEmits(['click']);

const store = useMusicLibraryStore();
</script>

<style scoped>
.media-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: var(--space-03);
  padding: var(--space-02);
  border-radius: var(--radius-04);
  cursor: pointer;
  background: var(--color-background-neutral-50);
  min-width: 0;
  transition: background var(--transition-fast);
}

.media-cover {
  flex-shrink: 0;
  width: 60px;
  height: 60px;
  border-radius: var(--radius-02);
  background: var(--color-background-neutral-12);
}

.media-cover.rounded {
  border-radius: 50%;
}

.media-details {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--space-01);
  overflow: hidden;
}

.media-title {
  margin: 0;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.media-subtitle {
  margin: 0;
  color: var(--color-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
