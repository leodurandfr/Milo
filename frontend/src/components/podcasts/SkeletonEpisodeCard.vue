<template>
  <div class="skeleton-episode-card">
    <div class="skeleton-card-image shimmer"></div>
    <div class="skeleton-content">
      <div class="skeleton-text-line shimmer skeleton-title"></div>
      <div class="skeleton-text-line shimmer skeleton-podcast"></div>
      <div class="skeleton-text-line shimmer skeleton-meta"></div>
    </div>
  </div>
</template>

<style scoped>
.skeleton-episode-card {
  display: flex;
  gap: var(--space-03);
  border-radius: var(--radius-04);
  padding: var(--space-03);
  background: var(--color-background-neutral-50);
}

.skeleton-card-image {
  width: 128px;
  height: 128px;
  border-radius: var(--radius-02);
  flex-shrink: 0;
}

.skeleton-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  justify-content: center;
}

.skeleton-title {
  width: 90%;
  height: 20px;
}

.skeleton-podcast {
  width: 70%;
}

.skeleton-meta {
  width: 50%;
}

/* Mobile: Match EpisodeCard responsive dimensions */
@media (max-aspect-ratio: 4/3) {
  .skeleton-card-image {
    width: 64px;
    height: 64px;
  }

  .skeleton-text-line {
    height: 14px;
  }

  .skeleton-title {
    height: 18px;
  }
}
</style>
