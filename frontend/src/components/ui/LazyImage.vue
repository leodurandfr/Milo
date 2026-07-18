<template>
  <div class="lazy-image" :class="{ instant }">
    <!-- Baseline layer: SVG generated from name, or static image fallback.
         Mounted at opacity 1, then faded out once the real image loads — so a
         transparent favicon shows the card background, not this layer, through
         its transparent areas. -->
    <div
      v-if="fallbackName"
      class="lazy-image-placeholder"
      :class="{ hidden: imageLoaded }"
      v-html="resolvedFallbackSvg"
    />
    <img
      v-else-if="fallback"
      :src="fallback"
      class="lazy-image-placeholder"
      :class="{ hidden: imageLoaded }"
      alt=""
    />

    <!-- Real image layer: fades in over the placeholder once loaded. -->
    <img
      v-if="src && !imageError"
      ref="imgRef"
      :src="src"
      :alt="alt"
      class="lazy-image-main"
      :class="{ loaded: imageLoaded }"
      :loading="lazy ? 'lazy' : 'eager'"
      :fetchpriority="priority"
      decoding="async"
      @load="handleImageLoad"
      @error="handleImageError"
    />

    <slot />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { generateStationAvatarSvg } from '@/utils/stationAvatar'
import { MIN_IMAGE_SIZE } from '@/constants/imageQuality'

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
  },
  // Browser fetch-priority hint. Use 'high' for above-the-fold critical images.
  priority: {
    type: String,
    default: 'auto'
  },
  // Defer the fetch until the image nears the viewport. Default is eager so
  // small or above-the-fold grids load immediately; long scrollable lists
  // should opt in explicitly.
  lazy: {
    type: Boolean,
    default: false
  }
})

// A real image that is already cached (or returns within a frame or two) must
// never cross-fade — the generated placeholder would flash over it on every
// page switch. Reveal those instantly; keep the fade only for genuine loads.
const INSTANT_REVEAL_MS = 80

const imgRef = ref(null)
const imageLoaded = ref(false)
const imageError = ref(false)
const instant = ref(false)
let loadStartedAt = 0

const resolvedFallbackSvg = computed(() => {
  if (!props.fallbackName) return ''
  return generateStationAvatarSvg(props.fallbackName)
})

function handleImageLoad() {
  const img = imgRef.value
  if (img && (img.naturalWidth < MIN_IMAGE_SIZE || img.naturalHeight < MIN_IMAGE_SIZE)) {
    imageError.value = true
    return
  }
  instant.value = performance.now() - loadStartedAt < INSTANT_REVEAL_MS
  imageLoaded.value = true
}

function handleImageError() {
  imageError.value = true
}

watch(() => props.src, () => {
  imageLoaded.value = false
  imageError.value = false
  instant.value = false
  loadStartedAt = performance.now()
})

// Browser-cached images may complete before Vue mounts. Flip imageLoaded so
// the parent's skeleton overlay can dismiss on first paint instead of waiting
// for a `load` event that won't fire — and reveal instantly (no fade), since a
// complete-at-mount image is already on screen the moment the placeholder would.
onMounted(() => {
  loadStartedAt = performance.now()
  const img = imgRef.value
  if (img?.complete && img.naturalHeight >= MIN_IMAGE_SIZE && img.naturalWidth >= MIN_IMAGE_SIZE) {
    instant.value = true
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

.lazy-image-placeholder {
  z-index: 0;
  transition: opacity 200ms ease-out;
}

.lazy-image-placeholder.hidden {
  opacity: 0;
}

.lazy-image-main {
  opacity: 0;
  transition: opacity 200ms ease-out;
  z-index: 1;
}

.lazy-image-main.loaded {
  opacity: 1;
}

/* Cached / instant reveal: swap placeholder → image with no cross-fade, so the
   generated avatar never flashes over an image that's already there. */
.lazy-image.instant .lazy-image-placeholder,
.lazy-image.instant .lazy-image-main {
  transition: none;
}
</style>
