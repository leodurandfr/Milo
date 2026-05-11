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
      @transitionend="handleTransitionEnd"
    />
    <!-- Fallback stays mounted until the favicon's opacity fade completes (transitionend) -->
    <div
      v-if="!fadeComplete && fallbackName"
      class="lazy-image-placeholder"
      v-html="resolvedFallbackSvg"
    />
    <img
      v-else-if="!fadeComplete && fallback"
      :src="fallback"
      class="lazy-image-placeholder"
      alt=""
    />
    <slot />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
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
const fadeComplete = ref(false)

const MIN_IMAGE_SIZE = 8

// Resolve the SVG markup lazily — generation runs only when the fallback is actually rendered
const resolvedFallbackSvg = computed(() => {
  if (fadeComplete.value || !props.fallbackName) return ''
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

function handleTransitionEnd(e) {
  if (e.propertyName === 'opacity' && imageLoaded.value) {
    fadeComplete.value = true
  }
}

// Reset state on src change so the new image gets its own fade-in over the fallback
watch(() => props.src, () => {
  imageLoaded.value = false
  imageError.value = false
  fadeComplete.value = false
})

// Handle browser-cached images that complete before Vue mounts.
// We can't rely on a transitionend event here (the transition may never run
// in the same tick as the initial paint), so skip the fade and unmount the
// fallback immediately by flipping fadeComplete alongside imageLoaded.
onMounted(() => {
  const img = imgRef.value
  if (img?.complete && img.naturalHeight >= MIN_IMAGE_SIZE && img.naturalWidth >= MIN_IMAGE_SIZE) {
    imageLoaded.value = true
    fadeComplete.value = true
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

/* Main image fades in over the fallback; the fallback stays mounted until
   transitionend, then unmounts. The fallback bleeds through during the ramp
   — accepted trade-off for a softer transition. */
.lazy-image-main {
  opacity: 0;
  transition: opacity 200ms ease-out;
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
