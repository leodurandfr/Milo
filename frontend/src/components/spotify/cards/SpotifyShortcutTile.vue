<template>
  <div v-press class="shortcut-tile" @click="$emit('click')">
    <div class="tile-cover" :class="{ 'liked-cover': liked }">
      <SvgIcon v-if="liked" name="heart" :size="20" aria-hidden="true" />
      <LazyImage v-else :src="image || ''" :fallback="musicPlaceholder" :alt="title" lazy class="tile-image" />
      <transition name="loading-fade">
        <div v-if="opening" class="card-loading-overlay">
          <LoadingSpinner :size="32" />
        </div>
      </transition>
    </div>
    <p class="tile-name heading-4">{{ title }}</p>
  </div>
</template>

<script setup>
import LazyImage from '@/components/ui/LazyImage.vue';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { useCardOpening } from '@/composables/useSpotifyOpening';
import { musicPlaceholder } from '@/constants/placeholders';

const props = defineProps({
  // The page it opens: the tile spins while that page loads.
  uri: {
    type: String,
    required: true,
  },
  title: {
    type: String,
    required: true,
  },
  image: {
    type: String,
    default: '',
  },
  // The Liked Songs tile: a heart instead of a cover.
  liked: {
    type: Boolean,
    default: false,
  },
});

defineEmits(['click']);

const opening = useCardOpening(() => props.uri);
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
  position: relative;
  width: 64px;
  height: 64px;
  flex-shrink: 0;
  overflow: hidden;
  background: var(--color-surface-glass);
}

.tile-image {
  width: 100%;
  height: 100%;
}

.liked-cover {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-on-brand);
  background: var(--color-brand);
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
