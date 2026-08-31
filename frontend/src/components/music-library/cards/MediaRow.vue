<template>
  <div v-press class="media-row" @click="$emit('click')">
    <div v-if="icon" class="media-cover media-cover--icon">
      <SvgIcon :name="icon" :size="28" />
    </div>
    <LazyImage
      v-else
      ref="lazyImg"
      :src="store.thumbUrl(coverId)"
      :fallback="musicPlaceholder"
      :alt="title"
      lazy
      :class="['media-cover', { rounded: roundedCover }]"
    >
      <transition name="content-fade">
        <div v-if="!contentReady" class="cover-skeleton shimmer"></div>
      </transition>
    </LazyImage>
    <div class="media-details">
      <p class="media-title heading-3">{{ title }}</p>
      <p v-if="subtitle" class="media-subtitle text-mono">{{ subtitle }}</p>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue';
import { useMusicLibraryStore } from '@/stores/musicLibraryStore';
import LazyImage from '@/components/ui/LazyImage.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { useLazyImageSkeleton } from '@/composables/useLazyImageSkeleton';
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  // Navidrome coverArt id (may be empty → placeholder fallback). One drawing for
  // every row here, artist rows included: the disc sits inside the round crop as
  // readably as a square one, and a second file was a second thing to keep in
  // step with the player's.
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
  icon: {
    type: String,
    default: '',
  },
});

defineEmits(['click']);

const store = useMusicLibraryStore();
const lazyImg = ref(null);
const { contentReady } = useLazyImageSkeleton(lazyImg, () => !!props.coverId);
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

.media-cover--icon {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-brand);
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
