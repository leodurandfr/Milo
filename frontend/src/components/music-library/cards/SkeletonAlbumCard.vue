<template>
  <div class="skeleton-album-card">
    <div class="skeleton-album-cover shimmer"></div>
    <div class="skeleton-album-info">
      <!-- Each bar in a line of AlbumCard's typography: name, then artist. -->
      <span class="skeleton-line heading-4">
        <span class="skeleton-text-line shimmer skeleton-title"></span>&#8203;
      </span>
      <span class="skeleton-line text-body-medium">
        <span class="skeleton-text-line shimmer skeleton-artist"></span>&#8203;
      </span>
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
  border-radius: var(--radius-02);
}

.skeleton-album-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.skeleton-line {
  display: flex;
  align-items: center;
}

.skeleton-title {
  width: 80%;
}

.skeleton-artist {
  width: 55%;
}
</style>
