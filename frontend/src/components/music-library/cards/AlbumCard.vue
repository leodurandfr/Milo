<template>
  <div v-press class="album-card" @click="$emit('click')">
    <LazyImage
      ref="lazyImg"
      :src="store.gridUrl(album.coverArt)"
      :fallback="musicPlaceholder"
      :alt="album.name"
      lazy
      class="album-cover"
    >
      <transition name="content-fade">
        <div v-if="!contentReady" class="cover-skeleton shimmer"></div>
      </transition>
    </LazyImage>
    <div class="album-info">
      <p class="album-name heading-4">{{ album.name }}</p>
      <p v-if="album.artist" class="album-artist text-mono-medium">{{ album.artist }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import LazyImage from '@/components/ui/LazyImage.vue';
import { useLazyImageSkeleton } from '@/composables/useLazyImageSkeleton';
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  album: {
    type: Object,
    required: true,
  },
});

defineEmits(['click']);

const store = useMusicLibraryStore();
const lazyImg = ref(null);
const { contentReady } = useLazyImageSkeleton(lazyImg, () => !!props.album.coverArt);
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
  border-radius: var(--radius-02);
  background: var(--color-background-neutral-50);
}

.cover-skeleton {
  position: absolute;
  inset: 0;
  --shimmer-base: var(--color-background-strong);
  --shimmer-highlight: var(--color-background-neutral);
}

/* Leave-only: the skeleton mounts at full opacity, then fades once the cover
   (real or fallback) is ready, so it always paints at least once. */
.content-fade-leave-active {
  transition: opacity var(--transition-normal-leave);
}

.content-fade-leave-to {
  opacity: 0;
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
