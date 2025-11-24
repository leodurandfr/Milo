<template>
  <div class="skeleton-station-card">
    <div class="skeleton-station-image"></div>
  </div>
</template>

<style scoped>
.skeleton-station-card {
  aspect-ratio: 1;
  width: 100%;
  border-radius: var(--radius-05);
  overflow: hidden;
  background: var(--color-background-neutral);
}

.skeleton-station-image {
  width: 100%;
  height: 100%;
  border-radius: var(--radius-05);

  background: linear-gradient(
    90deg,
    var(--color-background-neutral) 0%,
    var(--color-background-strong) 50%,
    var(--color-background-neutral) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
}

@keyframes shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
</style>
