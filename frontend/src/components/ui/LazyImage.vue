<template>
  <div class="lazy-image">
    <img
      v-if="src"
      ref="imgRef"
      :src="src"
      :alt="alt"
      class="lazy-image-main"
      :class="{ loaded: imageLoaded }"
      loading="lazy"
      decoding="async"
      @load="handleImageLoad"
      @error="handleImageError"
    />
    <img
      :src="fallback"
      class="lazy-image-placeholder"
      alt=""
    />
    <slot />
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useImageFallback } from '@/composables/useImageFallback'

defineProps({
  src: {
    type: String,
    default: ''
  },
  fallback: {
    type: String,
    required: true
  },
  alt: {
    type: String,
    default: ''
  }
})

const imgRef = ref(null)
const { imageLoaded, imageError, handleImageLoad, handleImageError } = useImageFallback(imgRef)

defineExpose({ imageLoaded, imageError })
</script>

<style scoped>
.lazy-image {
  position: relative;
  overflow: hidden;
}

.lazy-image img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.lazy-image-main {
  opacity: 0;
  transition: opacity var(--transition-normal);
  z-index: 1;
}

.lazy-image-main.loaded {
  opacity: 1;
}

.lazy-image-placeholder {
  opacity: 1;
  z-index: 0;
}
</style>
