<template>
  <div class="skeleton-card" aria-hidden="true">
    <div class="skeleton-cover shimmer"></div>
    <div class="skeleton-text-line shimmer"></div>
  </div>
</template>

<style scoped>
.skeleton-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
  min-width: 0;
}

.skeleton-cover {
  aspect-ratio: 1;
  width: 100%;
  border-radius: var(--radius-02);
}

.skeleton-text-line {
  width: 70%;
}
</style>
