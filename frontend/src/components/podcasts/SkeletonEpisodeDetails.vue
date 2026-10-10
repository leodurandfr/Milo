<template>
  <!-- EpisodeDetails in its own geometry: the header, then the description's
       card — its title, then a paragraph in body-medium lines. -->
  <div class="skeleton-episode-details">
    <SkeletonDetailHeader subtitle action="icon" />

    <div class="skeleton-description-block">
      <span class="skeleton-line text-mono-medium">
        <span class="skeleton-text-line shimmer skeleton-section-title"></span>&#8203;
      </span>
      <div class="skeleton-paragraph">
        <span v-for="width in LINES" :key="width" class="skeleton-line text-body-medium">
          <span class="skeleton-text-line shimmer" :style="{ width }"></span>&#8203;
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import SkeletonDetailHeader from '@/components/audio/SkeletonDetailHeader.vue'

const LINES = ['100%', '95%', '88%', '70%']
</script>

<style scoped>
/* EpisodeDetails' .details-content and .description-block: change one,
   change both. */
.skeleton-episode-details {
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.skeleton-description-block {
  background: var(--color-surface);
  border-radius: var(--radius-04);
  padding: var(--space-04);
  display: flex;
  flex-direction: column;
  gap: var(--space-03);
}

.skeleton-line {
  display: flex;
  align-items: center;
}

.skeleton-section-title {
  width: 120px;
}
</style>
