<!-- LyricsLoadingState.vue — spinner + "{label} track · artist" copy, shared by
     LyricsView's initial fetch ("searching") and LyricsContent's wait for a
     known playback position ("loading"), so the two read as one continuous
     screen while still naming what's actually happening. -->
<template>
  <div class="lyrics-loading-state">
    <LoadingSpinner :size="size" />
    <div class="lyrics-loading-copy">
      <p class="text-body lyrics-loading-label">{{ t(labelKey) }}</p>
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
  labelKey: { type: String, default: 'lyrics.loading' },
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
