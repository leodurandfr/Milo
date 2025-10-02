<!-- frontend/src/components/snapcast/SnapclientItemSkeleton.vue -->
<template>
  <div class="snapclient-skeleton">
    <div class="skeleton-name shimmer"></div>
    <div class="skeleton-volume shimmer"></div>
    <div class="skeleton-controls">
      <div class="skeleton-button shimmer"></div>
      <div class="skeleton-toggle shimmer"></div>
    </div>
  </div>
</template>

<script setup>
// Pas de logique, juste un composant visuel
</script>

<style scoped>
.snapclient-skeleton {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-radius: 16px;
  gap: var(--space-04);
  padding: var(--space-04) var(--space-04) var(--space-04) var(--space-05);
  background: var(--color-background-neutral);
}

.skeleton-name {
  width: 112px;
  height: 24px;
  border-radius: var(--radius-03);
  background: var(--color-background-strong);
}

.skeleton-volume {
  flex: 1;
  height: 40px;
  border-radius: 20px;
  background: var(--color-background-strong);
}

.skeleton-controls {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.skeleton-button {
  width: 48px;
  height: 48px;
  border-radius: var(--radius-full);
  background: var(--color-background-strong);
}

.skeleton-toggle {
  width: 48px;
  height: 28px;
  border-radius: 14px;
  background: var(--color-background-strong);
}

/* Animation shimmer */
.shimmer {
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-glass) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}

@keyframes shimmer {
  0% {
    background-position: -200% 0;
  }
  100% {
    background-position: 200% 0;
  }
}

/* Responsive */
@media (max-aspect-ratio: 4/3) {
  .snapclient-skeleton {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-03);
  }

  .skeleton-name {
    flex: 1;
    order: 1;
  }

  .skeleton-controls {
    order: 2;
    margin-left: auto;
  }

  .skeleton-volume {
    order: 3;
    width: 100%;
  }
}
</style>