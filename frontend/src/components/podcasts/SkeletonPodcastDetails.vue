<template>
  <!-- PodcastDetails in its own geometry: the header, then the episodes'
       heading and list. -->
  <div class="skeleton-podcast-details">
    <SkeletonDetailHeader subtitle action="small" />

    <div class="skeleton-episodes-section">
      <span class="skeleton-line heading-2">
        <span class="skeleton-text-line shimmer skeleton-section-title"></span>&#8203;
      </span>
      <div class="skeleton-episodes-list">
        <SkeletonEpisodeCard v-for="i in 5" :key="`skeleton-episode-${i}`" />
      </div>
    </div>
  </div>
</template>

<script setup>
import SkeletonDetailHeader from '@/components/audio/SkeletonDetailHeader.vue'
import SkeletonEpisodeCard from './SkeletonEpisodeCard.vue'
</script>

<style scoped>
/* PodcastDetails' .details-content and episodes section: change one, change
   both. */
.skeleton-podcast-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-06);
}

.skeleton-line {
  display: flex;
  align-items: center;
  margin-bottom: var(--space-03);
}

.skeleton-section-title {
  width: 200px;
}

.skeleton-episodes-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}
</style>
