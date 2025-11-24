<template>
  <div class="skeleton-podcast-card">
    <div class="skeleton-card-image"></div>
    <div class="skeleton-text-line skeleton-tag"></div>
    <div class="skeleton-text-line skeleton-title"></div>
    <div class="skeleton-text-line skeleton-publisher"></div>
  </div>
</template>

<style scoped>
.skeleton-podcast-card {
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-04);
  padding: var(--space-03);
  gap: var(--space-03);
  background: var(--color-background-neutral);
}

@keyframes shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

.skeleton-card-image {
  width: 100%;
  aspect-ratio: 1;
  border-radius: var(--radius-03);
  flex-shrink: 0;

  /* Gradient shimmer animation */
  background: linear-gradient(
    90deg,
    var(--color-background-neutral) 0%,
    var(--color-background-strong) 50%,
    var(--color-background-neutral) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

.skeleton-text-line {
  height: 16px;
  border-radius: var(--radius-02);

  /* Gradient shimmer animation */
  background: linear-gradient(
    90deg,
    var(--color-background-neutral) 0%,
    var(--color-background-strong) 50%,
    var(--color-background-neutral) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

.skeleton-tag {
  width: 40%;
}

.skeleton-title {
  width: 90%;
}

.skeleton-publisher {
  width: 60%;
}
</style>
