<template>
  <div v-press class="shortcut-tile" @click="$emit('click')">
    <div v-if="liked" class="tile-cover liked-cover" aria-hidden="true">
      <SvgIcon name="heart" :size="20" />
    </div>
    <LazyImage v-else :src="image || ''" :fallback="musicPlaceholder" :alt="title" lazy class="tile-cover" />
    <p class="tile-name text-body">{{ title }}</p>
  </div>
</template>

<script setup>
import LazyImage from '@/components/ui/LazyImage.vue';
import SvgIcon from '@/components/ui/SvgIcon.vue';
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
  // The Liked Songs tile: a heart instead of a cover.
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
  background: var(--color-background-neutral);
  overflow: hidden;
  cursor: pointer;
}

.tile-cover {
  width: 56px;
  height: 56px;
  flex-shrink: 0;
  background: var(--color-background-neutral-50);
}

.liked-cover {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-contrast);
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
