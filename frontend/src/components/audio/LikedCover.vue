<template>
  <div class="liked-cover">
    <img :src="likedCover" alt="" class="liked-cover-image" draggable="false" />
    <SvgIcon name="heart" class="liked-cover-heart" aria-hidden="true" />
  </div>
</template>

<script setup>
import SvgIcon from '@/components/ui/SvgIcon.vue';
import { likedCover } from '@/constants/placeholders';
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

/* A share of the tile, never a pixel size: the cover scales as one picture,
   from a 60 px row to the phone's full-width header. */
.liked-cover-heart {
  position: relative;
  width: 32%;
}

.liked-cover-heart :deep(svg) {
  width: 100%;
  height: auto;
}
</style>
