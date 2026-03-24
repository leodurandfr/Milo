<template>
  <div class="skeleton-station-card">
    <div class="skeleton-station-image shimmer"></div>
  </div>
</template>

<style scoped>
.skeleton-station-card {
  aspect-ratio: 1;
  width: 100%;
  border-radius: var(--radius-05);
  overflow: hidden;
}

.skeleton-station-image {
  width: 100%;
  height: 100%;
  border-radius: var(--radius-05);
}
</style>
