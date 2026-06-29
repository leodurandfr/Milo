<template>
  <div class="skeleton-podcast-details">
    <SkeletonPodcastCard variant="row" />

    <div class="skeleton-episodes-section">
      <div class="skeleton-text-line shimmer skeleton-section-title"></div>
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
  gap: var(--space-06);
}

.skeleton-episodes-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.skeleton-section-title {
  width: 200px;
  height: 26px;
}

.skeleton-episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

@media (max-aspect-ratio: 4/3) {
  .skeleton-section-title {
    height: 24px;
  }
}
</style>
