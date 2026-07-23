<!-- LyricsLoadingState.vue — spinner + "searching lyrics for track · artist" copy,
     shared by LyricsView's initial fetch and LyricsContent's wait for a known
     playback position, so the two loading phases read as one continuous screen. -->
<template>
  <div class="lyrics-loading-state">
    <LoadingSpinner :size="size" />
    <div class="lyrics-loading-copy">
      <p class="text-body lyrics-loading-label">{{ t('lyrics.loading') }}</p>
      <p class="text-body lyrics-loading-track-line">
        <span>{{ trackTitle }}</span>
        <span class="lyrics-loading-track-sep">·</span>
        <span>{{ trackArtist }}</span>
      </p>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from '@/services/i18n';
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue';

defineProps({
  trackTitle: { type: String, default: '' },
  trackArtist: { type: String, default: '' },
  size: { type: Number, default: 56 }
});

const { t } = useI18n();
</script>

<style scoped>
.lyrics-loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-04);
}

.lyrics-loading-state > :deep(.loading-spinner) {
  color: var(--color-text-contrast);
}

.lyrics-loading-copy {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-01);
  text-align: center;
}

.lyrics-loading-label {
  color: var(--color-text-contrast-50);
}

.lyrics-loading-track-line {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 0 var(--space-02);
  padding-inline: var(--space-05);
  color: var(--color-text-contrast);
}

.lyrics-loading-track-sep {
  color: var(--color-text-contrast);
}
</style>
