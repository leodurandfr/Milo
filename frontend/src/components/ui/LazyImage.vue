<template>
  <div class="lazy-image" :class="{ instant }">
    <!-- Baseline layer: SVG generated from name, or static image fallback.
         Mounted at opacity 1, then faded out once the real image loads — so a
         transparent favicon shows the card background, not this layer, through
         its transparent areas. -->
    <div
      v-if="fallbackName"
      class="lazy-image-placeholder"
      :class="{ hidden: placeholderHidden }"
      v-html="resolvedFallbackSvg"
    />
    <img
      v-else-if="fallback"
      :src="fallback"
      class="lazy-image-placeholder"
      :class="{ hidden: placeholderHidden }"
      alt=""
    />

    <!-- While the image loads, in place of the placeholder: the skeleton is
         an ink, the placeholder would show through it. -->
    <Transition :name="instant ? 'none' : 'reveal'">
      <div v-if="skeletonShown" class="lazy-image-skeleton shimmer" />
    </Transition>

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
import { ref, computed, onMounted, watch, nextTick } from 'vue'
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
  },
  // A skeleton while the image loads, which hands over to it — or to the
  // placeholder, when it fails — with the reveal.
  skeleton: {
    type: Boolean,
    default: false
  }
})

const imgRef = ref(null)
const imageLoaded = ref(false)
const imageError = ref(false)
// Ready before anything was painted: shown as it is, with nothing to reveal it
// from. Once a frame went out with the skeleton or the placeholder, the image
// fades in over it.
const instant = ref(false)
let painted = false

const skeletonShown = computed(() => props.skeleton && !!props.src && !imageLoaded.value && !imageError.value)
const placeholderHidden = computed(() => imageLoaded.value || skeletonShown.value)

const resolvedFallbackSvg = computed(() => {
  if (!props.fallbackName) return ''
  return generateStationAvatarSvg(props.fallbackName)
})

function handleImageLoad() {
  if (imageLoaded.value) return
  const img = imgRef.value
  if (img && (img.naturalWidth < MIN_IMAGE_SIZE || img.naturalHeight < MIN_IMAGE_SIZE)) {
    imageError.value = true
    return
  }
  instant.value = !painted
  imageLoaded.value = true
}

function handleImageError() {
  imageError.value = true
}

// An image the browser already holds is complete as soon as it is in the DOM,
// before the next frame: it is drawn at once, never revealed from a skeleton or
// a placeholder nobody saw. Otherwise the image is revealed once a frame went
// out without it.
function settle() {
  if (!props.src) return
  const img = imgRef.value
  if (img?.complete && img.naturalHeight >= MIN_IMAGE_SIZE && img.naturalWidth >= MIN_IMAGE_SIZE) {
    instant.value = true
    imageLoaded.value = true
    return
  }
  painted = false
  // Called back before the next frame: the second is called once one went out.
  requestAnimationFrame(() => requestAnimationFrame(() => { painted = true }))
}

// Another image in the same place (a station change in the source bar): the
// same rule as a mount, once the new src is in the DOM.
watch(() => props.src, async () => {
  imageLoaded.value = false
  imageError.value = false
  instant.value = false
  await nextTick()
  settle()
})

onMounted(settle)

defineExpose({ imageLoaded, imageError })
</script>

<style scoped>
.lazy-image {
  position: relative;
  overflow: hidden;
}

.lazy-image-main,
.lazy-image-placeholder,
.lazy-image-skeleton {
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
  transition: opacity var(--transition-reveal);
}

.lazy-image-placeholder.hidden {
  opacity: 0;
}

.lazy-image-main {
  opacity: 0;
  transition: opacity var(--transition-reveal);
  z-index: 1;
}

.lazy-image-main.loaded {
  opacity: 1;
}

/* Ready before the first frame: nothing was shown to reveal it from. */
.lazy-image.instant .lazy-image-placeholder,
.lazy-image.instant .lazy-image-main {
  transition: none;
}
</style>
