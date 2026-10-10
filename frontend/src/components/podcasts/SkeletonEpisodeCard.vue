<template>
  <div class="skeleton-episode-card">
    <div class="skeleton-card-image shimmer"></div>
    <!-- Each bar in a line of EpisodeCard's typography: the episode, its
         show, its meta. -->
    <div class="skeleton-content">
      <span class="skeleton-line heading-3">
        <span class="skeleton-text-line shimmer skeleton-title"></span>&#8203;
      </span>
      <span class="skeleton-line text-mono-medium">
        <span class="skeleton-text-line shimmer skeleton-podcast"></span>&#8203;
      </span>
      <span class="skeleton-line text-mono-medium">
        <span class="skeleton-text-line shimmer skeleton-meta"></span>&#8203;
      </span>
    </div>
  </div>
</template>

<style scoped>
/* EpisodeCard's box: change one, change both. */
.skeleton-episode-card {
  display: flex;
  gap: var(--space-03);
  border-radius: var(--radius-04);
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
  background: var(--color-surface);
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
  gap: var(--space-01);
  justify-content: center;
}

.skeleton-line {
  display: flex;
  align-items: center;
}

.skeleton-title {
  width: 90%;
}

.skeleton-podcast {
  width: 50%;
}

.skeleton-meta {
  width: 35%;
}

@media (max-aspect-ratio: 4/3) {
  .skeleton-card-image {
    width: 96px;
    height: 96px;
  }

  .skeleton-content {
    gap: 0;
  }
}
</style>
