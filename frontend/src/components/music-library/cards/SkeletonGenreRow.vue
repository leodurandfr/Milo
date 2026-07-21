<template>
  <div class="skeleton-genre-row">
    <div class="skeleton-text-line shimmer skeleton-name"></div>
    <div class="skeleton-text-line shimmer skeleton-count"></div>
  </div>
</template>

<style scoped>
.skeleton-genre-row {
  display: flex;
  flex-direction: row;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-03);
  padding: var(--space-03) var(--space-04);
  border-radius: var(--radius-04);
  background: var(--color-background-neutral-50);
  min-width: 0;
}

.skeleton-name {
  width: 40%;
}

.skeleton-count {
  flex-shrink: 0;
  width: 15%;
}
</style>
