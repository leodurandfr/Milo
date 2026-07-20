<template>
  <div class="player-info-text">
    <div v-if="kicker" class="player-info-kicker">
      <LazyImage v-if="kickerIcon || kickerFallbackName" class="player-info-kicker-icon"
        :src="kickerIcon" :fallback-name="kickerFallbackName" alt="" />
      <span class="player-info-kicker-label text-mono-small">{{ kicker }}</span>
    </div>
    <p class="player-info-title heading-2">{{ title }}</p>
    <p v-if="secondary" class="player-info-secondary text-body">{{ secondary }}</p>
  </div>
</template>

<script setup>
import LazyImage from '@/components/ui/LazyImage.vue'

defineProps({
  /**
   * Small label above the title (station name, podcast name). Optionally
   * paired with an icon via kickerIcon/kickerFallbackName.
   */
  kicker: {
    type: String,
    default: null
  },
  kickerIcon: {
    type: String,
    default: null
  },
  kickerFallbackName: {
    type: String,
    default: null
  },
  /**
   * Main line (track title, episode name, station name).
   */
  title: {
    type: String,
    required: true
  },
  /**
   * Secondary line below the title (artist).
   */
  secondary: {
    type: String,
    default: null
  }
})
</script>

<style scoped>
.player-info-text {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.player-info-kicker {
  display: flex;
  align-items: center;
  gap: var(--space-02);
  padding-bottom: var(--space-02);
  min-width: 0;
}

.player-info-kicker-icon {
  width: 24px;
  height: 24px;
  flex-shrink: 0;
  border-radius: var(--radius-01);
  overflow: hidden;
}

.player-info-kicker-label {
  color: var(--color-text-contrast-50);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.player-info-title {
  color: var(--color-text-contrast);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

.player-info-secondary {
  color: var(--color-text-contrast-50);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
}
</style>
