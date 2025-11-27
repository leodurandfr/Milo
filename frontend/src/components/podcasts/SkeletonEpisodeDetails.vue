<template>
  <div class="skeleton-episode-details">
    <!-- Episode Card skeleton -->
    <SkeletonEpisodeCard />

    <!-- Description block skeleton -->
    <div class="skeleton-description-block">
      <div class="skeleton-text-line skeleton-section-title"></div>
      <div class="skeleton-text-line skeleton-description-line-1"></div>
      <div class="skeleton-text-line skeleton-description-line-2"></div>
      <div class="skeleton-text-line skeleton-description-line-3"></div>
      <div class="skeleton-text-line skeleton-description-line-4"></div>
    </div>
  </div>
</template>

<script setup>
import SkeletonEpisodeCard from './SkeletonEpisodeCard.vue'
</script>

<style scoped>
.skeleton-episode-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.skeleton-description-block {
  padding: var(--space-06);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.skeleton-text-line {
  height: 16px;
  border-radius: var(--radius-02);

  background: linear-gradient(
    90deg,
    var(--color-background-neutral) 0%,
    var(--color-background-strong) 50%,
    var(--color-background-neutral) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
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

@keyframes shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}
</style>
