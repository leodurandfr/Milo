<template>
  <div v-press class="playlist-card" @click="$emit('click')">
    <LazyImage ref="lazyImg" :src="playlist.image || ''" :fallback="musicPlaceholder"
      :alt="title" lazy class="playlist-cover">
      <transition name="content-fade">
        <div v-if="!contentReady" class="cover-skeleton shimmer"></div>
      </transition>
    </LazyImage>
    <div class="playlist-info">
      <p class="playlist-name heading-4">{{ title }}</p>
      <p v-if="byline" class="playlist-owner text-mono-medium">{{ byline }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue';
import { useI18n } from '@/services/i18n';
import LazyImage from '@/components/ui/LazyImage.vue';
import { useLazyImageSkeleton } from '@/composables/useLazyImageSkeleton';
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  // { uri, name, owner, image } as /api/spotify/home lists it. No track count:
  // the library's is wrong for every playlist Spotify regenerates.
  playlist: {
    type: Object,
    required: true,
  },
});

defineEmits(['click']);

const { t } = useI18n();
const lazyImg = ref(null);
const { contentReady } = useLazyImageSkeleton(lazyImg, () => !!props.playlist.image);

const title = computed(() => props.playlist.name || t('spotify.untitledPlaylist'));
const byline = computed(() =>
  props.playlist.owner === 'spotify' ? 'Spotify' : ''
);
</script>

<style scoped>
.playlist-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  cursor: pointer;
  min-width: 0;
}

.playlist-cover {
  aspect-ratio: 1;
  width: 100%;
  border-radius: var(--radius-02);
  background: var(--color-surface-glass);
}

.cover-skeleton {
  position: absolute;
  inset: 0;
}

.content-fade-leave-active {
  transition: opacity var(--transition-normal-leave);
}

.content-fade-leave-to {
  opacity: 0;
}

.playlist-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
  min-width: 0;
}

.playlist-name,
.playlist-owner {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.playlist-name {
  color: var(--color-text);
}

.playlist-owner {
  color: var(--color-text-secondary);
}
</style>
