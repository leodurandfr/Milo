<template>
  <div class="skeleton-album-card">
    <div class="skeleton-album-cover shimmer"></div>
    <div class="skeleton-album-info">
      <div class="skeleton-text-line shimmer skeleton-title"></div>
      <div class="skeleton-text-line shimmer skeleton-artist"></div>
    </div>
  </div>
</template>

<style scoped>
.skeleton-album-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  min-width: 0;
}

.skeleton-album-cover {
  aspect-ratio: 1;
  width: 100%;
  border-radius: var(--radius-04);
}

.skeleton-album-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.skeleton-title {
  width: 80%;
}

.skeleton-artist {
  width: 55%;
}
</style>
