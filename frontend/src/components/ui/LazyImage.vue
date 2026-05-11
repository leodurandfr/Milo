<template>
  <div class="lazy-image">
    <img
      v-if="src && !imageError"
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
    <!-- Fallback visible until the main image is loaded (bridges the network/decode gap) -->
    <div
      v-if="!imageLoaded && fallbackName"
      class="lazy-image-placeholder"
      v-html="resolvedFallbackSvg"
    />
    <img
      v-else-if="!imageLoaded && fallback"
      :src="fallback"
      class="lazy-image-placeholder"
      alt=""
    />
    <slot />
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { generateStationAvatarSvg } from '@/utils/stationAvatar'

const props = defineProps({
  src: {
    type: String,
    default: ''
  },
  // Static fallback URL (e.g. local placeholder asset)
  fallback: {
    type: String,
    default: ''
  },
  // Name used to lazily generate a deterministic inline SVG avatar.
  // Takes precedence over `fallback` when provided.
  fallbackName: {
    type: String,
    default: ''
  },
  alt: {
    type: String,
    default: ''
  }
})

const imgRef = ref(null)
const imageLoaded = ref(false)
const imageError = ref(false)

const MIN_IMAGE_SIZE = 8

// Resolve the SVG markup lazily — generation runs only when the fallback is actually rendered
const resolvedFallbackSvg = computed(() => {
  if (imageLoaded.value || !props.fallbackName) return ''
  return generateStationAvatarSvg(props.fallbackName)
})

function handleImageLoad() {
  const img = imgRef.value
  if (img && (img.naturalWidth < MIN_IMAGE_SIZE || img.naturalHeight < MIN_IMAGE_SIZE)) {
    imageError.value = true
    return
  }
  imageLoaded.value = true
}

function handleImageError() {
  imageError.value = true
}

// Handle browser-cached images that complete before Vue mounts
onMounted(() => {
  const img = imgRef.value
  if (img?.complete && img.naturalHeight >= MIN_IMAGE_SIZE && img.naturalWidth >= MIN_IMAGE_SIZE) {
    imageLoaded.value = true
  }
})

defineExpose({ imageLoaded, imageError })
</script>

<style scoped>
.lazy-image {
  position: relative;
  overflow: hidden;
}

.lazy-image-main,
.lazy-image-placeholder {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

img.lazy-image-main,
img.lazy-image-placeholder {
  object-fit: cover;
}

/* Main image: invisible until loaded, then instant swap to opaque on top of the fallback.
   No opacity transition — a fade would let the (unmounting) fallback bleed through. */
.lazy-image-main {
  opacity: 0;
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
