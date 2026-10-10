<template>
  <div class="skeleton-card" aria-hidden="true">
    <div class="skeleton-cover shimmer"></div>
    <!-- The name, in a line of SpotifyCard's heading-4. -->
    <span class="skeleton-line heading-4">
      <span class="skeleton-text-line shimmer"></span>&#8203;
    </span>
  </div>
</template>

<style scoped>
.skeleton-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
  min-width: 0;
}

.skeleton-cover {
  aspect-ratio: 1;
  width: 100%;
  border-radius: var(--radius-02);
}

.skeleton-line {
  display: flex;
  align-items: center;
}

.skeleton-text-line {
  width: 70%;
}
</style>
