<template>
  <div class="profile-avatar" :style="{ '--avatar-size': `${size}px`, '--avatar-tint': tint }">
    <LazyImage v-if="profile.avatar_url" :src="profile.avatar_url" :alt="profile.name" class="avatar-image" />
    <span v-else class="avatar-initial heading-2" aria-hidden="true">{{ initial }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import LazyImage from '@/components/ui/LazyImage.vue';

const props = defineProps({
  // { name, avatar_url, color } — color is the profile's own, from Spotify.
  profile: {
    type: Object,
    required: true,
  },
  size: {
    type: Number,
    default: 96,
  },
});

const initial = computed(() => (props.profile.name || '?').trim().charAt(0).toUpperCase());
// Spotify gives each profile a color; without one the tile stays neutral.
const tint = computed(() => props.profile.color || 'var(--color-background-strong)');
</script>

<style scoped>
.profile-avatar {
  position: relative;
  width: var(--avatar-size);
  height: var(--avatar-size);
  flex-shrink: 0;
  border-radius: var(--radius-full);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--avatar-tint);
}

.avatar-image {
  width: 100%;
  height: 100%;
}

.avatar-initial {
  color: var(--color-text-contrast);
}
</style>
