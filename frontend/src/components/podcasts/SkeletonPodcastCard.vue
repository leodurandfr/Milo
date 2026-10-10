<template>
  <!-- PodcastCard in the home grid: the cover, then each bar in a line of the
       card's typography — its rank, its name, its publisher. -->
  <div class="skeleton-podcast-card">
    <div class="skeleton-card-image shimmer"></div>
    <div class="skeleton-card-info">
      <span class="skeleton-line text-mono-medium">
        <span class="skeleton-text-line shimmer skeleton-tag"></span>&#8203;
      </span>
      <span class="skeleton-line heading-4">
        <span class="skeleton-text-line shimmer skeleton-title"></span>&#8203;
      </span>
      <span class="skeleton-line text-mono-medium">
        <span class="skeleton-text-line shimmer skeleton-publisher"></span>&#8203;
      </span>
    </div>
  </div>
</template>

<style scoped>
/* PodcastCard's box: change one, change both. */
.skeleton-podcast-card {
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border-radius: var(--radius-04);
  padding: var(--space-03) var(--space-03) var(--space-04) var(--space-03);
  gap: var(--space-03);
}

.skeleton-card-image {
  width: 100%;
  aspect-ratio: 1;
  border-radius: var(--radius-02);
  flex-shrink: 0;
}

.skeleton-card-info {
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.skeleton-line {
  display: flex;
  align-items: center;
}

.skeleton-tag {
  width: 25%;
}

.skeleton-title {
  width: 90%;
}

.skeleton-publisher {
  width: 60%;
}
</style>
