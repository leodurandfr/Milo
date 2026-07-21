import { ref, onMounted, watch, unref } from 'vue';

// `hasSource` may be a ref/computed or a plain getter function — resolve either.
function resolve(hasSource) {
  return typeof hasSource === 'function' ? hasSource() : unref(hasSource);
}

// Reveals content once a LazyImage settles (loaded or errored), or immediately
// if there's no source to wait for (no <img> ever mounts, so it would never
// settle on its own). The flip is deferred via requestAnimationFrame so the
// skeleton is always painted at least once before its leave-transition starts
// — without this, a cached image (synchronous imageLoaded=true) would skip the
// skeleton paint entirely.
export function useLazyImageSkeleton(lazyImgRef, hasSource) {
  const contentReady = ref(false);

  function markReady() {
    requestAnimationFrame(() => { contentReady.value = true; });
  }

  onMounted(() => {
    if (!resolve(hasSource)) markReady();
  });

  watch(
    () => lazyImgRef.value?.imageLoaded || lazyImgRef.value?.imageError,
    (settled) => { if (settled) markReady(); }
  );

  return { contentReady };
}
