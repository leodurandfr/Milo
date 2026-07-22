<!-- frontend/src/components/settings/categories/music-library/ScanProgress.vue -->
<!--
  Navidrome scan progress bar — shared by the maintenance "Library refresh"
  section and the add-share wizard's post-connect indexing screen. Navidrome
  never reports a scan's target count, so the bar always sweeps; the label
  carries the live track count.
-->
<template>
  <div class="scan-progress" :class="{ 'is-open': open }">
    <div class="scan-progress__inner">
      <div v-if="hasBar" class="scan-progress__track">
        <div class="scan-progress__fill" />
      </div>
      <span class="scan-progress__label text-mono-small">{{ label }}</span>
    </div>
  </div>
</template>

<script setup>
defineProps({
  open: { type: Boolean, default: false },
  hasBar: { type: Boolean, default: true },
  label: { type: String, required: true },
});
</script>

<style scoped>
/* Reveal — grid-rows + opacity + margin; the negative margin cancels the card's
   row gap while collapsed. */
.scan-progress {
  display: grid;
  grid-template-rows: 0fr;
  opacity: 0;
  margin-top: calc(-1 * var(--space-04));
  transition:
    grid-template-rows var(--transition-fast),
    opacity var(--transition-fast),
    margin-top var(--transition-fast);
}

.scan-progress.is-open {
  grid-template-rows: 1fr;
  opacity: 1;
  margin-top: 0;
}

.scan-progress__inner {
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: var(--space-01);
}

.scan-progress__track {
  height: 8px;
  background: var(--color-background-strong);
  border-radius: var(--radius-01);
  overflow: hidden;
}

/* A segment sweeps across the track (no known total to fill toward). */
.scan-progress__fill {
  width: 40%;
  height: 100%;
  background: var(--color-background-contrast);
  border-radius: var(--radius-01);
  animation: scan-progress-indeterminate 1.1s ease-in-out infinite;
}

@keyframes scan-progress-indeterminate {
  0% {
    transform: translateX(-120%);
  }

  100% {
    transform: translateX(280%);
  }
}

.scan-progress__label {
  color: var(--color-text-secondary);
}
</style>
