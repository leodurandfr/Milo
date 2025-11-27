<template>
  <div class="skeleton-podcast-details">
    <!-- Podcast Card skeleton (row variant) -->
    <SkeletonPodcastCard variant="row" />

    <!-- Episodes section -->
    <div class="skeleton-episodes-section">
      <div class="skeleton-text-line skeleton-section-title"></div>
      <div class="skeleton-episodes-list">
        <SkeletonEpisodeCard v-for="i in 5" :key="`skeleton-episode-${i}`" />
      </div>
    </div>
  </div>
</template>

<script setup>
import SkeletonPodcastCard from './SkeletonPodcastCard.vue'
import SkeletonEpisodeCard from './SkeletonEpisodeCard.vue'
</script>

<style scoped>
.skeleton-podcast-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
}

.skeleton-episodes-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
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
  width: 200px;
  height: 24px;
  margin-bottom: var(--space-03);
}

.skeleton-episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
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
