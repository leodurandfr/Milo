<template>
  <div class="skeleton-episode-details">
    <div class="skeleton-header">
      <div class="skeleton-header-image shimmer"></div>
      <div class="skeleton-header-meta">
        <div class="skeleton-text-line shimmer skeleton-header-title"></div>
        <div class="skeleton-text-line shimmer skeleton-header-podcast"></div>
        <div class="skeleton-text-line shimmer skeleton-header-status"></div>
      </div>
    </div>

    <div class="skeleton-description-block">
      <div class="skeleton-text-line shimmer skeleton-section-title"></div>
      <div class="skeleton-text-line shimmer skeleton-description-line-1"></div>
      <div class="skeleton-text-line shimmer skeleton-description-line-2"></div>
      <div class="skeleton-text-line shimmer skeleton-description-line-3"></div>
      <div class="skeleton-text-line shimmer skeleton-description-line-4"></div>
    </div>
  </div>
</template>

<style scoped>
.skeleton-episode-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.skeleton-header {
  display: flex;
  align-items: center;
  gap: var(--space-03);
  background: var(--color-background-contrast);
  border-radius: var(--radius-04);
  padding: var(--space-03) var(--space-04) var(--space-03) var(--space-03);
}

.skeleton-header-image {
  width: 150px;
  height: 150px;
  flex-shrink: 0;
  border-radius: var(--radius-02);
}

.skeleton-header-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.skeleton-header-title {
  width: 70%;
  height: 22px;
}

.skeleton-header-podcast {
  width: 45%;
}

.skeleton-header-status {
  width: 35%;
}

@media (max-aspect-ratio: 4/3) {
  .skeleton-header {
    flex-direction: column;
    align-items: stretch;
  }

  .skeleton-header-image {
    width: 100%;
    height: auto;
    aspect-ratio: 1 / 1;
  }
}

.skeleton-description-block {
  padding: var(--space-06);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.skeleton-section-title {
  width: 150px;
  height: 18px;
}

.skeleton-description-line-1 {
  width: 100%;
}

.skeleton-description-line-2 {
  width: 95%;
}

.skeleton-description-line-3 {
  width: 88%;
}

.skeleton-description-line-4 {
  width: 70%;
}
</style>
