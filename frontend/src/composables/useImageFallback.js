import { ref, onMounted } from 'vue'

/**
 * Composable for image loading with fallback support.
 * Tracks load/error state and handles browser-cached images.
 *
 * @param {import('vue').Ref<HTMLImageElement|null>} imgRef - Template ref to the <img> element
 * @returns {{ imageLoaded: import('vue').Ref<boolean>, imageError: import('vue').Ref<boolean>, handleImageLoad: Function, handleImageError: Function }}
 */
export function useImageFallback(imgRef) {
  const imageLoaded = ref(false)
  const imageError = ref(false)

  function handleImageLoad() {
    imageLoaded.value = true
  }

  function handleImageError() {
    imageError.value = true
  }

  // Handle browser-cached images that complete before Vue mounts
  onMounted(() => {
    if (imgRef.value?.complete && imgRef.value?.naturalHeight !== 0) {
      imageLoaded.value = true
    }
  })

  return { imageLoaded, imageError, handleImageLoad, handleImageError }
}
