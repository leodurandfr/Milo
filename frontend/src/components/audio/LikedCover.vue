<template>
  <div class="liked-cover" :class="{ blurred }">
    <img :src="likedCover" alt="" class="liked-cover-image" draggable="false" />
    <SvgIcon name="heart" :size="iconSize" class="liked-cover-heart" aria-hidden="true" />
  </div>
</template>

<script setup>
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { likedCover } from '@/constants/placeholders';

defineProps({
  // The heart is sized by the tile, not by the bitmap: 28 on a 60–64 px tile,
  // 48 on a page header.
  iconSize: {
    type: Number,
    required: true,
  },
  // Blurred in place under a card's loading overlay, as LazyImage blurs a cover.
  blurred: {
    type: Boolean,
    default: false,
  },
});
</script>

<style scoped>
.liked-cover {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  border-radius: inherit;
  overflow: hidden;
  color: var(--color-text-on-brand);
}

.liked-cover-image {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.liked-cover-heart {
  position: relative;
}

.liked-cover-image,
.liked-cover-heart {
  transition: filter var(--transition-fast);
}

.liked-cover.blurred .liked-cover-image,
.liked-cover.blurred .liked-cover-heart {
  filter: blur(var(--blur-02));
}
</style>
